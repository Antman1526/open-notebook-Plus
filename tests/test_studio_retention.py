"""v0.8.124 — tests for Studio artifact retention (revision pruning +
stale-export cleanup).

The job is OFF by default (DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS=0);
these tests exercise the pure pruning logic directly (bypassing SurrealDB via
monkeypatched repo_query / StudioArtifact classmethods) plus the env-parsing
helpers and the run_retention_loop shutdown shape.

Covers:
  * Revision pruning keeps the newest N, deletes the rest
  * revision_keep_per_artifact <= 0 disables revision pruning entirely
  * A stale export older than the threshold is unlinked; its key/hash
    is dropped; a fresh (within-threshold) export is left alone
  * A stale export path outside the containment root is never unlinked
  * dry_run=True counts everything but mutates nothing
  * Env-knob parsing: defaults, invalid -> default, dry-run truthy strings
  * run_retention_loop: interval 0 returns without running a pass
  * run_retention_loop: runs once then exits promptly once stop_event is set
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.studio import retention


def _mk_file(path: Path, *, age_days: float, content: bytes = b"x" * 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    mtime = time.time() - age_days * 86400
    os.utime(path, (mtime, mtime))
    return path


def _artifact_row(**overrides) -> dict:
    row = {
        "id": "studio_artifact:top1",
        "notebook_id": "notebook:1",
        "artifact_type": "report",
        "title": "Top artifact",
        "export_paths": {},
        "output_payload": {},
    }
    row.update(overrides)
    return row


def _revision(index: int, parent_id: str = "studio_artifact:top1") -> StudioArtifact:
    return StudioArtifact(
        id=f"studio_artifact:rev{index}",
        notebook_id="notebook:1",
        artifact_type="report",
        title=f"Revision {index}",
        revision_of_id=parent_id,
    )


# --------------------------------------------------------------------- #
# Revision pruning
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_revision_pruning_keeps_newest_n_deletes_rest(monkeypatch):
    revisions = [_revision(i) for i in range(5)]  # rev0 = newest (list order)
    deleted: list[str] = []

    async def fake_repo_query(*_args, **_kwargs):
        return [_artifact_row()]

    async def fake_get_revisions(_artifact_id):
        return revisions

    async def fake_delete(self):
        deleted.append(str(self.id))
        return True

    def fake_get_stale_export_formats(_artifact):
        return []

    monkeypatch.setattr(retention, "repo_query", fake_repo_query)
    monkeypatch.setattr(StudioArtifact, "get_revisions", fake_get_revisions)
    monkeypatch.setattr(StudioArtifact, "delete", fake_delete)
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence.get_stale_export_formats",
        fake_get_stale_export_formats,
    )

    report = await retention.prune_studio_retention(
        revision_keep_per_artifact=2,
        stale_export_max_age_days=0,  # disable export half for this test
        dry_run=False,
    )

    assert report.revisions_examined == 5
    assert report.revisions_deleted == 3
    assert deleted == ["studio_artifact:rev2", "studio_artifact:rev3", "studio_artifact:rev4"]


@pytest.mark.asyncio
async def test_revision_keep_zero_disables_pruning_entirely(monkeypatch):
    async def fake_repo_query(*_args, **_kwargs):
        raise AssertionError("repo_query must not be called when keep <= 0")

    monkeypatch.setattr(retention, "repo_query", fake_repo_query)

    report = await retention.prune_studio_retention(
        revision_keep_per_artifact=0,
        stale_export_max_age_days=0,
        dry_run=False,
    )

    assert report.revisions_examined == 0
    assert report.revisions_deleted == 0


# --------------------------------------------------------------------- #
# Stale export cleanup
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_stale_export_older_than_threshold_unlinked_fresh_kept(
    monkeypatch, tmp_path
):
    export_root = tmp_path / "exports"
    export_root.mkdir()
    stale_file = _mk_file(export_root / "stale.pdf", age_days=40)
    fresh_file = _mk_file(export_root / "fresh.md", age_days=1)
    stale_file_size = stale_file.stat().st_size

    artifact = StudioArtifact(
        id="studio_artifact:top1",
        notebook_id="notebook:1",
        artifact_type="report",
        title="Top artifact",
        export_paths={"pdf": str(stale_file), "markdown": str(fresh_file)},
        output_payload={"export_hashes": {"pdf": "h1", "markdown": "h2"}},
    )

    async def fake_repo_query(*_args, **_kwargs):
        return [_artifact_row(
            export_paths=artifact.export_paths,
            output_payload=artifact.output_payload,
        )]

    async def fake_get_revisions(_artifact_id):
        return []

    def fake_get_stale_export_formats(art):
        return list(art.export_paths.keys())

    saved: list[StudioArtifact] = []

    async def fake_save(self):
        saved.append(self)

    monkeypatch.setattr(retention, "repo_query", fake_repo_query)
    monkeypatch.setattr(StudioArtifact, "get_revisions", fake_get_revisions)
    monkeypatch.setattr(StudioArtifact, "save", fake_save)
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence.get_stale_export_formats",
        fake_get_stale_export_formats,
    )
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence._artifact_export_dir",
        lambda: export_root,
    )

    report = await retention.prune_studio_retention(
        revision_keep_per_artifact=0,  # disable revision half for this test
        stale_export_max_age_days=30,
        dry_run=False,
    )

    assert not stale_file.exists()
    assert fresh_file.exists()
    assert report.exports_examined == 2
    assert report.exports_removed == 1
    assert report.bytes_reclaimed == stale_file_size

    assert len(saved) == 1
    saved_artifact = saved[0]
    assert "pdf" not in saved_artifact.export_paths
    assert "markdown" in saved_artifact.export_paths
    assert "pdf" not in saved_artifact.output_payload["export_hashes"]
    assert "markdown" in saved_artifact.output_payload["export_hashes"]


@pytest.mark.asyncio
async def test_stale_export_outside_root_never_unlinked(monkeypatch, tmp_path):
    export_root = tmp_path / "root"
    export_root.mkdir()
    outside_dir = tmp_path / "outside"
    outside_file = _mk_file(outside_dir / "escaped.pdf", age_days=40)

    artifact_paths = {"pdf": str(outside_file)}

    async def fake_repo_query(*_args, **_kwargs):
        return [_artifact_row(export_paths=artifact_paths, output_payload={})]

    async def fake_get_revisions(_artifact_id):
        return []

    def fake_get_stale_export_formats(art):
        return list(art.export_paths.keys())

    async def fake_save(self):
        raise AssertionError("save() must not be called — nothing was removed")

    monkeypatch.setattr(retention, "repo_query", fake_repo_query)
    monkeypatch.setattr(StudioArtifact, "get_revisions", fake_get_revisions)
    monkeypatch.setattr(StudioArtifact, "save", fake_save)
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence.get_stale_export_formats",
        fake_get_stale_export_formats,
    )
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence._artifact_export_dir",
        lambda: export_root,
    )

    report = await retention.prune_studio_retention(
        revision_keep_per_artifact=0,
        stale_export_max_age_days=30,
        dry_run=False,
    )

    assert outside_file.exists()
    assert report.exports_removed == 0


# --------------------------------------------------------------------- #
# dry_run
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_dry_run_counts_but_changes_nothing(monkeypatch, tmp_path):
    export_root = tmp_path / "exports"
    export_root.mkdir()
    stale_file = _mk_file(export_root / "stale.pdf", age_days=40)

    revisions = [_revision(i) for i in range(3)]
    deleted: list[str] = []
    saved: list[StudioArtifact] = []

    async def fake_repo_query(*_args, **_kwargs):
        return [_artifact_row(
            export_paths={"pdf": str(stale_file)},
            output_payload={"export_hashes": {"pdf": "h1"}},
        )]

    async def fake_get_revisions(_artifact_id):
        return revisions

    async def fake_delete(self):
        deleted.append(str(self.id))
        return True

    async def fake_save(self):
        saved.append(self)

    def fake_get_stale_export_formats(art):
        return list(art.export_paths.keys())

    monkeypatch.setattr(retention, "repo_query", fake_repo_query)
    monkeypatch.setattr(StudioArtifact, "get_revisions", fake_get_revisions)
    monkeypatch.setattr(StudioArtifact, "delete", fake_delete)
    monkeypatch.setattr(StudioArtifact, "save", fake_save)
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence.get_stale_export_formats",
        fake_get_stale_export_formats,
    )
    monkeypatch.setattr(
        "deeper_notebook.studio.generation.persistence._artifact_export_dir",
        lambda: export_root,
    )

    report = await retention.prune_studio_retention(
        revision_keep_per_artifact=1,
        stale_export_max_age_days=30,
        dry_run=True,
    )

    assert report.dry_run is True
    assert report.revisions_examined == 3
    assert report.revisions_deleted == 2
    assert report.exports_examined == 1
    assert report.exports_removed == 1

    # Nothing actually happened.
    assert deleted == []
    assert saved == []
    assert stale_file.exists()


# --------------------------------------------------------------------- #
# Env-knob parsing
# --------------------------------------------------------------------- #


def test_env_defaults(monkeypatch):
    for var in (
        "DEEPER_NOTEBOOK_STUDIO_REVISION_KEEP_PER_ARTIFACT",
        "DEEPER_NOTEBOOK_STUDIO_EXPORT_STALE_DAYS",
        "DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS",
        "DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN",
    ):
        monkeypatch.delenv(var, raising=False)

    assert retention._revision_keep_per_artifact() == 10
    assert retention._stale_export_max_age_days() == 30
    assert retention._retention_interval_hours() == 0
    assert retention._retention_dry_run() is False


def test_env_invalid_values_fall_back_to_default(monkeypatch):
    monkeypatch.setenv(
        "DEEPER_NOTEBOOK_STUDIO_REVISION_KEEP_PER_ARTIFACT", "not-a-number"
    )
    monkeypatch.setenv("DEEPER_NOTEBOOK_STUDIO_EXPORT_STALE_DAYS", "not-a-number")
    monkeypatch.setenv(
        "DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS", "not-a-number"
    )

    assert retention._revision_keep_per_artifact() == 10
    assert retention._stale_export_max_age_days() == 30
    assert retention._retention_interval_hours() == 0


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("YES", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("", False),
    ],
)
def test_env_dry_run_truthy_strings(monkeypatch, raw, expected):
    monkeypatch.setenv("DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN", raw)
    assert retention._retention_dry_run() is expected


# --------------------------------------------------------------------- #
# run_retention_loop
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_retention_loop_interval_zero_returns_without_running(monkeypatch):
    called = False

    async def fake_prune(**_kwargs):
        nonlocal called
        called = True
        return retention.RetentionReport()

    monkeypatch.setattr(retention, "prune_studio_retention", fake_prune)

    stop_event = asyncio.Event()
    await retention.run_retention_loop(stop_event, interval_hours=0)

    assert called is False


@pytest.mark.asyncio
async def test_run_retention_loop_runs_once_then_exits_on_stop_event(monkeypatch):
    call_count = 0
    stop_event = asyncio.Event()

    async def fake_prune(**_kwargs):
        nonlocal call_count
        call_count += 1
        stop_event.set()
        return retention.RetentionReport()

    monkeypatch.setattr(retention, "prune_studio_retention", fake_prune)

    await asyncio.wait_for(
        retention.run_retention_loop(stop_event, interval_hours=5),
        timeout=5,
    )

    assert call_count == 1
    assert stop_event.is_set()
