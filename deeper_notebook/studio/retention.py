"""v0.8.124 — Studio artifact retention: revision pruning + stale export cleanup.

Ships OFF by default (`DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS=0`).

Why disabled by default: unlike the LangGraph checkpoint pruner (pure
history nobody reads again), Studio artifacts and their exports are
user-facing content — a revision or an export file a user still wants
can look "stale" to a heuristic without actually being unwanted. Rather
than guess at a safe default cadence, this job requires an operator to
explicitly opt in (set the interval > 0) after understanding what it
deletes. `DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN=true` lets an operator
see exactly what a run *would* touch (counts only, zero mutation) before
flipping dry-run off.

Two independent prunes, each independently disable-able:

  * Revision pruning: for every top-level artifact (revision_of_id is
    NONE), keep the newest `revision_keep_per_artifact` revisions and
    delete the rest via `StudioArtifact.delete()` (which already
    removes the revision's own export files under containment guards).
    `revision_keep_per_artifact <= 0` disables this half entirely.
  * Stale-export cleanup: for every artifact (top-level and revisions),
    unlink on-disk export files that `get_stale_export_formats()` says
    are stale AND whose file is older than `stale_export_max_age_days`,
    then drop the format's key from `export_paths` and
    `output_payload["export_hashes"]` and save the artifact. A fresh
    stale export (younger than the threshold) is left alone — staleness
    alone isn't reason enough to delete a file the user might still
    want; age is the second gate. `stale_export_max_age_days <= 0`
    disables this half entirely.

Mirrors `deeper_notebook/utils/checkpoint_prune.py`'s loop shape
(`run_prune_loop` -> `run_retention_loop`) so the two background jobs
read the same way in `api/main.py`'s lifespan.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger

from deeper_notebook.database.repository import repo_query
from deeper_notebook.domain.notebook import StudioArtifact
from deeper_notebook.environment import resolve_env
from deeper_notebook.exceptions import DatabaseOperationError, InvalidInputError

# Defaults. Interval 0 = disabled — see module docstring for why this job
# doesn't default to "on" the way the checkpoint pruner does.
_DEFAULT_RETENTION_INTERVAL_HOURS = 0
_DEFAULT_REVISION_KEEP_PER_ARTIFACT = 10
_DEFAULT_STALE_EXPORT_MAX_AGE_DAYS = 30
_DEFAULT_DRY_RUN = False

_DRY_RUN_TRUTHY = {"1", "true", "yes"}


@dataclass
class RetentionReport:
    """Counters for one `prune_studio_retention` run."""

    revisions_examined: int = 0
    revisions_deleted: int = 0
    exports_examined: int = 0
    exports_removed: int = 0
    bytes_reclaimed: int = 0
    dry_run: bool = False


def _revision_keep_per_artifact() -> int:
    raw = resolve_env(
        "DEEPER_NOTEBOOK_STUDIO_REVISION_KEEP_PER_ARTIFACT", ""
    ).strip()
    if not raw:
        return _DEFAULT_REVISION_KEEP_PER_ARTIFACT
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "DEEPER_NOTEBOOK_STUDIO_REVISION_KEEP_PER_ARTIFACT={!r} is not an "
            "integer; using default {}",
            raw,
            _DEFAULT_REVISION_KEEP_PER_ARTIFACT,
        )
        return _DEFAULT_REVISION_KEEP_PER_ARTIFACT


def _stale_export_max_age_days() -> int:
    raw = resolve_env("DEEPER_NOTEBOOK_STUDIO_EXPORT_STALE_DAYS", "").strip()
    if not raw:
        return _DEFAULT_STALE_EXPORT_MAX_AGE_DAYS
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "DEEPER_NOTEBOOK_STUDIO_EXPORT_STALE_DAYS={!r} is not an integer; "
            "using default {}",
            raw,
            _DEFAULT_STALE_EXPORT_MAX_AGE_DAYS,
        )
        return _DEFAULT_STALE_EXPORT_MAX_AGE_DAYS


def _retention_interval_hours() -> float:
    raw = resolve_env(
        "DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS", ""
    ).strip()
    if not raw:
        return float(_DEFAULT_RETENTION_INTERVAL_HOURS)
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS={!r} is not a "
            "number; using default {}",
            raw,
            _DEFAULT_RETENTION_INTERVAL_HOURS,
        )
        return float(_DEFAULT_RETENTION_INTERVAL_HOURS)


def _retention_dry_run() -> bool:
    raw = resolve_env("DEEPER_NOTEBOOK_STUDIO_RETENTION_DRY_RUN", "").strip()
    if not raw:
        return _DEFAULT_DRY_RUN
    return raw.lower() in _DRY_RUN_TRUTHY


async def _top_level_artifacts() -> list[StudioArtifact]:
    """All top-level Studio artifacts (revision_of_id is NONE), across every
    notebook. Copies `StudioArtifact.get_for_notebook`'s query style but
    without the notebook_id filter, since retention runs instance-wide."""
    try:
        rows = await repo_query(
            """
            SELECT * FROM studio_artifact
            WHERE revision_of_id = NONE
            """,
        )
        return [StudioArtifact(**row) for row in rows] if rows else []
    except Exception as e:
        logger.error(f"Error fetching top-level Studio artifacts for retention: {e}")
        logger.exception(e)
        raise DatabaseOperationError(e)


def _resolve_export_root() -> Optional[Path]:
    from deeper_notebook.studio.generation.persistence import _artifact_export_dir

    try:
        return _artifact_export_dir().resolve()
    except Exception as exc:
        logger.warning(f"Failed to resolve Studio export directory for retention: {exc}")
        return None


async def _prune_revisions(
    report: RetentionReport, *, keep: int, dry_run: bool
) -> None:
    if keep <= 0:
        logger.debug(
            "Studio retention: revision pruning disabled "
            "(revision_keep_per_artifact={})",
            keep,
        )
        return

    top_level = await _top_level_artifacts()
    for artifact in top_level:
        try:
            revisions = await StudioArtifact.get_revisions(str(artifact.id))
        except (InvalidInputError, DatabaseOperationError) as exc:
            logger.warning(
                f"Studio retention: failed to list revisions for artifact "
                f"{artifact.id}: {exc}"
            )
            continue

        report.revisions_examined += len(revisions)
        # get_revisions() orders by `updated DESC` — newest first already.
        overflow = revisions[keep:]
        for revision in overflow:
            report.revisions_deleted += 1
            if dry_run:
                continue
            try:
                await revision.delete()
            except Exception as exc:
                logger.warning(
                    f"Studio retention: failed to delete revision "
                    f"{revision.id} of artifact {artifact.id}: {exc}"
                )


async def _prune_stale_exports(
    report: RetentionReport,
    *,
    max_age_days: int,
    dry_run: bool,
    export_root: Optional[Path],
) -> None:
    from deeper_notebook.studio.generation.persistence import get_stale_export_formats

    if max_age_days <= 0:
        logger.debug(
            "Studio retention: stale-export pruning disabled "
            "(stale_export_max_age_days={})",
            max_age_days,
        )
        return

    max_age_seconds = max_age_days * 86400
    now = time.time()

    top_level = await _top_level_artifacts()
    artifacts: list[StudioArtifact] = list(top_level)
    for artifact in top_level:
        try:
            artifacts.extend(await StudioArtifact.get_revisions(str(artifact.id)))
        except (InvalidInputError, DatabaseOperationError) as exc:
            logger.warning(
                f"Studio retention: failed to list revisions for stale-export "
                f"scan of artifact {artifact.id}: {exc}"
            )

    for artifact in artifacts:
        try:
            stale_formats = get_stale_export_formats(artifact)
        except Exception as exc:
            logger.warning(
                f"Studio retention: failed to compute stale export formats "
                f"for artifact {artifact.id}: {exc}"
            )
            continue

        export_paths = (
            artifact.export_paths if isinstance(artifact.export_paths, dict) else {}
        )
        payload = (
            artifact.output_payload
            if isinstance(artifact.output_payload, dict)
            else {}
        )
        hashes = payload.get("export_hashes")
        hashes = hashes if isinstance(hashes, dict) else {}

        removed_any = False
        for fmt in stale_formats:
            path_str = export_paths.get(fmt)
            if not path_str:
                continue
            report.exports_examined += 1

            try:
                candidate = Path(path_str)
                if not candidate.is_file():
                    continue
                resolved = candidate.resolve()
            except Exception:
                continue

            if export_root is None or export_root not in resolved.parents:
                # Outside the containment root — never touch it.
                continue

            try:
                mtime = resolved.stat().st_mtime
            except OSError:
                continue
            if (now - mtime) < max_age_seconds:
                # Stale by hash, but not old enough by the file's age gate.
                continue

            file_size = 0
            try:
                file_size = resolved.stat().st_size
            except OSError:
                pass

            report.exports_removed += 1
            report.bytes_reclaimed += file_size

            if dry_run:
                continue

            try:
                resolved.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    f"Studio retention: failed to unlink stale export "
                    f"{resolved} for artifact {artifact.id}: {exc}"
                )
                continue

            export_paths.pop(fmt, None)
            hashes.pop(fmt, None)
            removed_any = True

        if removed_any and not dry_run:
            artifact.export_paths = export_paths
            payload["export_hashes"] = hashes
            artifact.output_payload = payload
            try:
                await artifact.save()
            except Exception as exc:
                logger.warning(
                    f"Studio retention: failed to save artifact {artifact.id} "
                    f"after stale-export cleanup: {exc}"
                )


async def prune_studio_retention(
    *,
    revision_keep_per_artifact: int,
    stale_export_max_age_days: int,
    dry_run: bool,
) -> RetentionReport:
    """Run one Studio retention pass: prune old revisions, then unlink
    stale-and-old exports. Both halves are independently disable-able via
    their threshold args (`<= 0`). `dry_run=True` counts everything and
    mutates nothing (no delete, no unlink, no save)."""
    report = RetentionReport(dry_run=dry_run)

    await _prune_revisions(report, keep=revision_keep_per_artifact, dry_run=dry_run)

    export_root = _resolve_export_root()
    await _prune_stale_exports(
        report,
        max_age_days=stale_export_max_age_days,
        dry_run=dry_run,
        export_root=export_root,
    )

    if report.revisions_deleted or report.exports_removed:
        logger.info(
            "Studio retention: deleted {} revision(s), removed {} export(s) "
            "reclaiming {} bytes (dry_run={})",
            report.revisions_deleted,
            report.exports_removed,
            report.bytes_reclaimed,
            dry_run,
        )
    else:
        logger.debug(
            "Studio retention: nothing to remove ({} revisions, {} exports "
            "examined; dry_run={})",
            report.revisions_examined,
            report.exports_examined,
            dry_run,
        )

    try:
        from api.metrics import studio_retention_runs_total

        studio_retention_runs_total.inc()
    except Exception:
        pass

    return report


async def run_retention_loop(stop_event, *, interval_hours: Optional[float] = None) -> None:
    """Background-task loop. Runs one retention pass on entry, then sleeps
    for the configured interval, repeating until `stop_event` is set.

    Mirrors `deeper_notebook.utils.checkpoint_prune.run_prune_loop`.
    Interval 0 (the default) means the job is disabled: log once and
    return immediately without running a pass.
    """
    import asyncio

    if interval_hours is None:
        interval_hours = _retention_interval_hours()

    if interval_hours <= 0:
        logger.info(
            "Studio retention: disabled "
            "(DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS={})",
            interval_hours,
        )
        return

    keep = _revision_keep_per_artifact()
    max_age_days = _stale_export_max_age_days()
    dry_run = _retention_dry_run()
    interval_seconds = interval_hours * 3600

    while not stop_event.is_set():
        try:
            await prune_studio_retention(
                revision_keep_per_artifact=keep,
                stale_export_max_age_days=max_age_days,
                dry_run=dry_run,
            )
        except Exception as exc:
            logger.warning("Studio retention loop iteration failed: {}", exc)

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
