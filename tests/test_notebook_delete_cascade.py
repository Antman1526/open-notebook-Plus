"""Atomic read-only protection for the complete notebook database cascade."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from surrealdb import RecordID, Surreal

from deeper_notebook.domain.notebook import Asset, Source
from deeper_notebook.exceptions import DatabaseOperationError


class _FakeNote:
    def __init__(
        self,
        note_id: str,
        *,
        canonical_external: bool | None = None,
        external_state: str | None = None,
    ) -> None:
        self.id = note_id
        self.canonical_external = canonical_external
        self.external_state = external_state
        self.delete_called = False

    async def delete(self) -> bool:
        self.delete_called = True
        raise AssertionError("Notebook cascade must not call Note.delete()")


class _FakeSource:
    def __init__(self, source_id: str) -> None:
        self.id = source_id
        self.cleanup_calls = 0

    def _cleanup_uploaded_file(self) -> None:
        self.cleanup_calls += 1


class _FakeStudioArtifact:
    """Delete spy for `StudioArtifact.get_for_notebook()` results.

    v0.8.126 — used by `make_notebook(..., studio_artifacts=[...])` to
    verify `Notebook.delete()` cascades Studio artifacts before the atomic
    SurrealQL block, without touching the real database layer.
    """

    def __init__(self, artifact_id: str, *, fail: bool = False) -> None:
        self.id = artifact_id
        self.fail = fail
        self.delete_calls = 0

    async def delete(self) -> bool:
        self.delete_calls += 1
        if self.fail:
            raise RuntimeError("simulated studio artifact delete failure")
        return True


@pytest.fixture()
def make_notebook(monkeypatch):
    from deeper_notebook.domain import notebook as notebook_module

    def factory(
        notes: list[_FakeNote],
        *,
        transaction_result: Any = None,
        transaction_error: Exception | None = None,
        sources: list[Any] | None = None,
        studio_artifacts: list[Any] | None = None,
    ):
        calls: list[tuple[str, dict[str, Any]]] = []
        sources = sources or []
        studio_artifacts = studio_artifacts or []

        async def fake_repo_query(
            query: str,
            params: dict[str, Any] | None = None,
        ) -> Any:
            calls.append((query, params or {}))
            if transaction_error is not None:
                raise transaction_error
            return transaction_result or [
                {
                    "deleted_notes": len(notes),
                    "deleted_sources": 0,
                    "unlinked_sources": 0,
                    "deleted_chat_session_ids": [],
                    "exclusive_source_ids": [],
                }
            ]

        async def get_notes(_self):
            return notes

        async def get_sources(_self):
            return sources

        # v0.8.126 — default empty so pre-existing tests (which assert
        # exactly one `repo_query` call: the atomic transaction) are
        # unaffected; pass `studio_artifacts=[...]` to exercise the cascade.
        async def fake_get_for_notebook(_notebook_id):
            return studio_artifacts

        monkeypatch.setattr(notebook_module, "repo_query", fake_repo_query)
        monkeypatch.setattr(notebook_module.Notebook, "get_notes", get_notes)
        monkeypatch.setattr(notebook_module.Notebook, "get_sources", get_sources)
        monkeypatch.setattr(notebook_module, "ensure_record_id", lambda value: value)
        monkeypatch.setattr(
            notebook_module.StudioArtifact,
            "get_for_notebook",
            fake_get_for_notebook,
        )

        notebook = notebook_module.Notebook(
            id="notebook:test-cascade",
            name="Cascade",
            description="Atomic cascade",
        )
        return notebook, calls

    return factory


def _transaction(calls: list[tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any]]:
    assert len(calls) == 1, f"expected one atomic database call, got {len(calls)}"
    query, params = calls[0]
    assert "BEGIN TRANSACTION" in query
    assert "COMMIT TRANSACTION" in query
    assert query.index("RETURN $result") < query.index("COMMIT TRANSACTION")
    return query, params


def _assert_guards_precede_destructive_sql(query: str) -> None:
    first_delete = query.index("DELETE ")
    assert query.index("notebook_note_set_changed") < first_delete
    assert query.index("external_note_read_only") < first_delete
    assert query.index("THROW") < first_delete
    assert first_delete < query.index("COMMIT TRANSACTION")


def test_success_uses_one_transaction_and_never_calls_per_note_delete(make_notebook):
    notes = [_FakeNote(f"note:{index}") for index in range(5)]
    notebook, calls = make_notebook(
        notes,
        transaction_result=[
            {
                "deleted_notes": 5,
                "deleted_sources": 1,
                "unlinked_sources": 2,
                "deleted_chat_session_ids": ["chat_session:a"],
                "exclusive_source_ids": ["source:exclusive"],
            }
        ],
    )

    result = asyncio.run(notebook.delete(delete_exclusive_sources=True))

    query, params = _transaction(calls)
    _assert_guards_precede_destructive_sql(query)
    for fragment in (
        "DELETE note WHERE id IN $current_note_ids",
        "DELETE artifact WHERE in IN $current_note_ids",
        "LET $notebook_reference_ids = SELECT VALUE id",
        "DELETE $notebook_reference_ids",
        "DELETE chat_session WHERE id IN $chat_session_ids",
        "DELETE $notebook_id",
    ):
        assert fragment in query
    assert params["expected_note_count"] == 5
    assert params["expected_note_ids"] == [note.id for note in notes]
    assert result["deleted_notes"] == 5
    assert result["deleted_sources"] == 1
    assert result["unlinked_sources"] == 2
    assert result["deleted_chat_session_ids"] == ["chat_session:a"]
    assert all(note.delete_called is False for note in notes)


@pytest.mark.parametrize(
    "notes",
    [
        [
            _FakeNote(
                f"note:external-{index}",
                canonical_external=False,
                external_state=None,
            )
            for index in range(30)
        ],
        [
            _FakeNote("note:normal"),
            _FakeNote(
                "note:external",
                canonical_external=False,
                external_state=None,
            ),
        ],
    ],
)
def test_persisted_external_note_rejection_ignores_hydrated_flags(
    make_notebook,
    notes,
):
    notebook, calls = make_notebook(
        notes,
        transaction_error=RuntimeError("external_note_read_only"),
    )

    from deeper_notebook.domain.notebook import ExternalNoteReadOnlyError

    with pytest.raises(ExternalNoteReadOnlyError, match="external_note_read_only"):
        asyncio.run(notebook.delete())

    query, params = _transaction(calls)
    _assert_guards_precede_destructive_sql(query)
    assert "canonical_external" in query
    assert "external_state" in query
    assert params["expected_note_count"] == len(notes)
    assert all(note.delete_called is False for note in notes)


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("notebook_note_set_changed"),
        RuntimeError("simulated transaction failure"),
    ],
)
def test_count_mismatch_or_transaction_failure_has_no_followup_destructive_call(
    make_notebook,
    error,
):
    notes = [_FakeNote("note:a"), _FakeNote("note:b")]
    notebook, calls = make_notebook(notes, transaction_error=error)

    with pytest.raises(DatabaseOperationError):
        asyncio.run(notebook.delete())

    query, _params = _transaction(calls)
    _assert_guards_precede_destructive_sql(query)
    assert "CANCEL TRANSACTION" not in query
    assert all(note.delete_called is False for note in notes)


def test_empty_notebook_still_commits_single_transaction(make_notebook):
    notebook, calls = make_notebook([])

    result = asyncio.run(notebook.delete())

    query, params = _transaction(calls)
    _assert_guards_precede_destructive_sql(query)
    assert params["expected_note_count"] == 0
    assert params["expected_note_ids"] == []
    assert result["deleted_notes"] == 0


def test_source_cleanup_runs_only_after_successful_commit(make_notebook):
    source = _FakeSource("source:exclusive")
    notebook, calls = make_notebook(
        [],
        sources=[source],
        transaction_result=[
            {
                "deleted_notes": 0,
                "deleted_sources": 1,
                "unlinked_sources": 0,
                "deleted_chat_session_ids": [],
                "exclusive_source_ids": ["source:exclusive"],
            }
        ],
    )

    asyncio.run(notebook.delete(delete_exclusive_sources=True))

    _transaction(calls)
    assert source.cleanup_calls == 1


def test_transaction_failure_never_runs_external_source_cleanup(make_notebook):
    source = _FakeSource("source:exclusive")
    notebook, calls = make_notebook(
        [],
        sources=[source],
        transaction_error=RuntimeError("simulated transaction failure"),
    )

    with pytest.raises(DatabaseOperationError):
        asyncio.run(notebook.delete(delete_exclusive_sources=True))

    _transaction(calls)
    assert source.cleanup_calls == 0


def test_post_commit_upload_cleanup_is_filesystem_only(
    make_notebook,
    monkeypatch,
    tmp_path: Path,
):
    from deeper_notebook.database import repository as repository_module
    from deeper_notebook.domain import base as base_module

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    uploaded_file = uploads / "owned.txt"
    uploaded_file.write_text("delete after commit", encoding="utf-8")
    outside_file = tmp_path / "outside.txt"
    outside_file.write_text("must survive", encoding="utf-8")
    escaping_link = uploads / "escaping-link.txt"
    escaping_link.symlink_to(outside_file)
    fifo = uploads / "post-commit-fifo"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)

    queue_calls: list[str] = []

    async def observed_get_command_status(_command_id):
        queue_calls.append("get_command_status")

        class _Status:
            value = "running"

        return _Status()

    class _ObservedService:
        async def update_command_result(self, *_args, **_kwargs):
            queue_calls.append("update_command_result")

    def observed_get_command_service():
        queue_calls.append("get_command_service")
        return _ObservedService()

    monkeypatch.setattr(
        "deeper_notebook.config.UPLOADS_FOLDER",
        str(uploads),
    )
    monkeypatch.setattr(
        "surreal_commands.get_command_status",
        observed_get_command_status,
    )
    monkeypatch.setattr(
        "surreal_commands.core.service.get_command_service",
        observed_get_command_service,
    )
    repo_delete = AsyncMock(
        side_effect=AssertionError("post-commit cleanup touched repo_delete")
    )
    monkeypatch.setattr(base_module, "repo_delete", repo_delete)
    repository_api_mocks: dict[str, AsyncMock] = {}
    for name in (
        "repo_create",
        "repo_delete",
        "repo_query",
        "repo_relate",
        "repo_update",
        "repo_upsert",
    ):
        mock = AsyncMock(
            side_effect=AssertionError(f"post-commit cleanup touched repository.{name}")
        )
        monkeypatch.setattr(repository_module, name, mock)
        repository_api_mocks[name] = mock

    sources = [
        Source(
            id="source:owned",
            title="Owned upload",
            asset=Asset(file_path=str(uploaded_file)),
            command=RecordID("command", "owned"),
        ),
        Source(
            id="source:escaping-link",
            title="Escaping symlink",
            asset=Asset(file_path=str(escaping_link)),
            command=RecordID("command", "escaping-link"),
        ),
    ]
    if hasattr(os, "mkfifo"):
        sources.append(
            Source(
                id="source:fifo",
                title="FIFO upload",
                asset=Asset(file_path=str(fifo)),
                command=RecordID("command", "fifo"),
            )
        )
    exclusive_source_ids = [
        "source:owned",
        "source:escaping-link",
    ]
    if hasattr(os, "mkfifo"):
        exclusive_source_ids.append("source:fifo")
    notebook, calls = make_notebook(
        [],
        sources=sources,
        transaction_result=[
            {
                "deleted_notes": 0,
                "deleted_sources": len(sources),
                "unlinked_sources": 0,
                "deleted_chat_session_ids": [],
                "exclusive_source_ids": exclusive_source_ids,
            }
        ],
    )

    asyncio.run(notebook.delete(delete_exclusive_sources=True))

    _transaction(calls)
    assert queue_calls == []
    repo_delete.assert_not_awaited()
    for mock in repository_api_mocks.values():
        mock.assert_not_awaited()
    assert not uploaded_file.exists()
    assert escaping_link.is_symlink()
    assert outside_file.read_text(encoding="utf-8") == "must survive"
    if hasattr(os, "mkfifo"):
        assert fifo.is_fifo()


def test_transaction_failure_skips_filesystem_and_queue_cleanup(
    make_notebook,
    monkeypatch,
    tmp_path: Path,
):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    uploaded_file = uploads / "preserved.txt"
    uploaded_file.write_text("preserve on rollback", encoding="utf-8")
    queue_calls: list[str] = []

    async def observed_get_command_status(_command_id):
        queue_calls.append("get_command_status")
        raise AssertionError("queue cleanup ran after transaction failure")

    monkeypatch.setattr(
        "deeper_notebook.config.UPLOADS_FOLDER",
        str(uploads),
    )
    monkeypatch.setattr(
        "surreal_commands.get_command_status",
        observed_get_command_status,
    )
    source = Source(
        id="source:preserved",
        title="Preserved upload",
        asset=Asset(file_path=str(uploaded_file)),
        command=RecordID("command", "preserved"),
    )
    notebook, calls = make_notebook(
        [],
        sources=[source],
        transaction_error=RuntimeError("simulated transaction failure"),
    )

    with pytest.raises(DatabaseOperationError):
        asyncio.run(notebook.delete(delete_exclusive_sources=True))

    _transaction(calls)
    assert queue_calls == []
    assert uploaded_file.read_text(encoding="utf-8") == "preserve on rollback"


def _embedded_params(params: dict[str, Any]) -> dict[str, Any]:
    converted = dict(params)
    converted["notebook_id"] = RecordID("notebook", "test-cascade")
    converted["expected_note_ids"] = [
        RecordID("note", str(value).split(":", 1)[1])
        for value in params["expected_note_ids"]
    ]
    return converted


def test_embedded_surreal_success_returns_result_and_commits(make_notebook):
    notebook, calls = make_notebook(
        [_FakeNote("note:a")],
        transaction_result=[
            {
                "deleted_notes": 1,
                "deleted_sources": 1,
                "unlinked_sources": 0,
                "deleted_chat_session_ids": ["chat_session:a"],
                "exclusive_source_ids": ["source:exclusive"],
            }
        ],
    )
    asyncio.run(notebook.delete(delete_exclusive_sources=True))
    query, params = _transaction(calls)

    for iteration in range(50):
        with Surreal("mem://") as db:
            db.use("cascade", f"success-{iteration}")
            db.query(
                """
                CREATE notebook:`test-cascade`;
                CREATE notebook:other;
                CREATE note:a SET canonical_external = false;
                RELATE note:a->artifact->notebook:`test-cascade`;
                CREATE source:exclusive;
                RELATE source:exclusive->reference->notebook:`test-cascade`;
                CREATE source:shared;
                RELATE source:shared->reference->notebook:`test-cascade`;
                RELATE source:shared->reference->notebook:other;
                CREATE source_embedding:a SET source = source:exclusive;
                CREATE source_insight:a SET source = source:exclusive;
                CREATE chat_session:a;
                RELATE chat_session:a->refers_to->notebook:`test-cascade`;
                """
            )

            result = db.query(query, _embedded_params(params))

            assert isinstance(result, dict), result
            assert result["deleted_notes"] == 1
            assert result["deleted_sources"] == 1
            assert result["unlinked_sources"] == 1
            assert result["deleted_chat_session_ids"] == [RecordID("chat_session", "a")]
            assert db.query("SELECT VALUE id FROM notebook") == [
                RecordID("notebook", "other")
            ]
            assert db.query("SELECT VALUE id FROM note") == []
            assert db.query("SELECT VALUE id FROM artifact") == []
            assert db.query("SELECT VALUE id FROM source") == [
                RecordID("source", "shared")
            ]
            assert db.query("SELECT VALUE id FROM source_embedding") == []
            assert db.query("SELECT VALUE id FROM source_insight") == []
            assert db.query("SELECT in, out FROM reference") == [
                {
                    "in": RecordID("source", "shared"),
                    "out": RecordID("notebook", "other"),
                }
            ]
            assert db.query("SELECT VALUE id FROM chat_session") == []
            assert db.query("SELECT VALUE id FROM refers_to") == []


@pytest.mark.parametrize(
    ("external", "extra_note", "expected_error"),
    [
        (True, False, "external_note_read_only"),
        (False, True, "failed transaction"),
    ],
)
def test_embedded_surreal_guard_throw_rolls_back_every_delete(
    make_notebook,
    external,
    extra_note,
    expected_error,
):
    notebook, calls = make_notebook([_FakeNote("note:a")])
    asyncio.run(notebook.delete())
    query, params = _transaction(calls)

    with Surreal("mem://") as db:
        db.use("cascade", f"rollback-{external}-{extra_note}")
        db.query(
            """
            CREATE notebook:`test-cascade`;
            CREATE note:a SET canonical_external = $external;
            RELATE note:a->artifact->notebook:`test-cascade`;
            """,
            {"external": external},
        )
        if extra_note:
            db.query(
                """
                CREATE note:b SET canonical_external = false;
                RELATE note:b->artifact->notebook:`test-cascade`;
                """
            )

        try:
            result = db.query(query, _embedded_params(params))
        except Exception as exc:
            # surrealdb 1.x returned the failed transaction text, while 2.x
            # raises its typed query error. Accept both driver contracts but
            # keep unexpected application/runtime exceptions visible.
            assert type(exc).__module__ == "surrealdb.errors"
            assert type(exc).__name__ in {
                "InternalError",
                "SurrealDBMethodError",
            }
            result = str(exc)

        assert expected_error in result
        assert len(db.query("SELECT VALUE id FROM notebook")) == 1
        assert len(db.query("SELECT VALUE id FROM note")) == (2 if extra_note else 1)
        assert len(db.query("SELECT VALUE id FROM artifact")) == (
            2 if extra_note else 1
        )


# v0.8.126 — Studio artifacts (and their export files / revisions) used to
# survive a notebook delete: `Notebook.delete()` never looked them up.
def test_delete_cascades_studio_artifacts_before_transaction(make_notebook):
    artifact_a = _FakeStudioArtifact("studio_artifact:a")
    artifact_b = _FakeStudioArtifact("studio_artifact:b")
    notebook, calls = make_notebook(
        [],
        studio_artifacts=[artifact_a, artifact_b],
    )

    result = asyncio.run(notebook.delete())

    # Still exactly one repo_query call: the atomic transaction. Studio
    # artifact deletion goes through `StudioArtifact.delete()` (mocked in
    # this test), not a raw repo_query, and must happen before it.
    _transaction(calls)
    assert artifact_a.delete_calls == 1
    assert artifact_b.delete_calls == 1
    assert result["studio_artifacts_deleted"] == 2


def test_failing_studio_artifact_delete_does_not_abort_notebook_delete(make_notebook):
    ok_artifact = _FakeStudioArtifact("studio_artifact:ok")
    failing_artifact = _FakeStudioArtifact("studio_artifact:broken", fail=True)
    notebook, calls = make_notebook(
        [],
        studio_artifacts=[failing_artifact, ok_artifact],
    )

    result = asyncio.run(notebook.delete())

    _transaction(calls)
    assert failing_artifact.delete_calls == 1
    assert ok_artifact.delete_calls == 1
    # The failing artifact is logged and skipped, not counted, while the
    # notebook delete itself still completes successfully.
    assert result["studio_artifacts_deleted"] == 1
    assert result["deleted_notes"] == 0


def test_delete_with_no_studio_artifacts_reports_zero(make_notebook):
    notebook, calls = make_notebook([])

    result = asyncio.run(notebook.delete())

    _transaction(calls)
    assert result["studio_artifacts_deleted"] == 0
