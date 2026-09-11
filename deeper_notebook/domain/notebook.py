import asyncio
import os
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Literal, Optional, Tuple, Union
from weakref import WeakKeyDictionary

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, field_validator
from surreal_commands import submit_command

from deeper_notebook.environment import resolve_env


def _is_command_registered(command_id: str) -> bool:
    """v0.7.133 — Check whether a surreal-commands command is in the
    process registry. Replaces the previous string-match against the
    `ValueError: Command not found` message that submit_command()
    raises when the registry is empty.

    Returns True if the command is registered (submit will succeed at
    the registry-check stage), False otherwise. Lazy-import the
    registry so an absent surreal_commands install doesn't break
    module load. Wrapped in a try/except because the registry API
    surface could change between minor versions and we want this
    pre-check to fail-closed (treat as "not registered, log + skip")
    rather than break Note.save.

    Why a pre-check beats catching the ValueError after the fact:
      - No string-content brittleness — surreal_commands could rename
        the exception message and this still works.
      - We never have to distinguish "registry miss" ValueError from
        a real argument-validation ValueError inside the command
        handler. The pre-check answers exactly the question we care
        about.
      - Faster: skips the cross-process DB roundtrip submit_command
        does before discovering the registry is empty.
    """
    try:
        from surreal_commands import registry

        return registry.get_command_by_id(command_id) is not None
    except Exception:
        # Registry API change, attribute missing, or surreal_commands
        # absent. Fail-closed.
        return False


from surrealdb import RecordID

from deeper_notebook.database.repository import ensure_record_id, repo_query
from deeper_notebook.domain.base import ObjectModel
from deeper_notebook.exceptions import (
    DatabaseOperationError,
    DeeperNotebookError,
    InvalidInputError,
)
from deeper_notebook.vault._projection_context import _projection_refresh_is_active

# Source deletion can change BM25 collection statistics.  Keep this as a
# fixed internal whitelist: callers and persisted source metadata must never
# select a table or index for REBUILD.
_SOURCE_SEARCH_INDEXES: tuple[tuple[str, str], ...] = (
    ("source", "idx_source_title"),
    ("source", "idx_source_full_text"),
    ("source_embedding", "idx_source_embed_chunk"),
    ("source_insight", "idx_source_insight"),
)
_SOURCE_SEARCH_REBUILD_TIMEOUT_S = 10.0
# This persisted marker remains stable until a dedicated record migration owns
# its transition; every query below derives from this one exact value.
_SOURCE_SEARCH_REBUILD_MARKER = "open_" "notebook:source_search_rebuild_pending"  # fmt: skip
# Desktop shutdown defaults to an eight-second process grace. Keep enough room
# for pool closure while waiting only briefly for an already-running pass; an
# unfinished marker is reconciled before the next API starts serving requests.
SOURCE_SEARCH_INDEX_SHUTDOWN_DRAIN_TIMEOUT_S = 5.0


class _SourceSearchIndexMaintenanceState:
    """One strong maintenance-task reference and generation counter per loop."""

    def __init__(self) -> None:
        self.task: asyncio.Task[None] | None = None
        # Source deletion owns a singleton durable marker. Keep the complete
        # marker-to-promotion sequence exclusive within its event loop so a
        # no-op/false delete cannot replace another delete's live token.
        self.deletion_lock = asyncio.Lock()
        self.generation = 0
        self.completed_generation = 0


_source_search_index_maintenance_states: WeakKeyDictionary[
    asyncio.AbstractEventLoop, _SourceSearchIndexMaintenanceState
] = WeakKeyDictionary()


def _source_search_index_maintenance_state() -> _SourceSearchIndexMaintenanceState:
    """Return state bound to the active event loop, never another loop's task."""
    loop = asyncio.get_running_loop()
    state = _source_search_index_maintenance_states.get(loop)
    if state is None:
        state = _SourceSearchIndexMaintenanceState()
        _source_search_index_maintenance_states[loop] = state
    return state


async def _mark_source_search_rebuild_pending() -> str:
    """Persist a fresh reconciliation receipt before source deletion begins."""
    rebuild_token = uuid.uuid4().hex
    try:
        rows = await repo_query(
            f"UPSERT {_SOURCE_SEARCH_REBUILD_MARKER} SET "
            "source_search_rebuild_pending = true, "
            "source_search_rebuild_state = 'intent', "
            "source_search_rebuild_token = $rebuild_token RETURN AFTER;",
            {"rebuild_token": rebuild_token},
        )
    except Exception as exc:
        raise DatabaseOperationError(
            "Could not write the source-search rebuild marker before deletion"
        ) from exc

    if (
        not rows
        or str(rows[0].get("source_search_rebuild_token")) != rebuild_token
        or rows[0].get("source_search_rebuild_state") != "intent"
    ):
        raise DatabaseOperationError(
            "Could not confirm the source-search rebuild marker before deletion"
        )
    return rebuild_token


async def _pending_source_search_rebuild_marker() -> tuple[str, str] | None:
    """Read the one fixed durable marker without deriving any query from input."""
    rows = await repo_query(
        "SELECT source_search_rebuild_token, source_search_rebuild_state "
        f"FROM {_SOURCE_SEARCH_REBUILD_MARKER};"
    )
    if not rows:
        return None
    token = rows[0].get("source_search_rebuild_token")
    state = rows[0].get("source_search_rebuild_state")
    return (str(token), str(state)) if token and state else None


async def _promote_source_search_rebuild_marker(rebuild_token: str) -> bool:
    """Promote only this delete's persisted intent after its post-sweep."""
    rows = await repo_query(
        f"UPDATE {_SOURCE_SEARCH_REBUILD_MARKER} "
        "SET source_search_rebuild_state = 'ready' "
        "WHERE source_search_rebuild_token = $rebuild_token "
        "AND source_search_rebuild_state = 'intent' RETURN AFTER;",
        {"rebuild_token": rebuild_token},
    )
    return bool(rows)


async def _clear_source_search_rebuild_marker(rebuild_token: str) -> bool:
    """Clear only the exact marker token observed before this rebuild pass."""
    rows = await repo_query(
        f"UPDATE {_SOURCE_SEARCH_REBUILD_MARKER} "
        "SET source_search_rebuild_pending = false, "
        "source_search_rebuild_state = NONE, "
        "source_search_rebuild_token = NONE "
        "WHERE source_search_rebuild_token = $rebuild_token "
        "AND source_search_rebuild_state = 'ready' RETURN AFTER;",
        {"rebuild_token": rebuild_token},
    )
    return bool(rows)


async def _refresh_source_search_indexes(rebuild_token: str) -> bool:
    """Perform one bounded, best-effort maintenance pass.

    The primary deletion is irreversible, so a rebuild failure cannot turn it
    into a false failed-delete response.  The warning remains explicit that
    search relevance can be degraded until a later successful rebuild.
    """
    succeeded = True
    for table, index in _SOURCE_SEARCH_INDEXES:
        try:
            marker = await _pending_source_search_rebuild_marker()
        except Exception as exc:
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: could not re-check source-search rebuild marker {} "
                "before index {} on table {}: {}",
                rebuild_token,
                index,
                table,
                exc,
            )
            return False
        if marker != (rebuild_token, "ready"):
            # A newer delete has installed intent or a new ready generation.
            # Stop before touching another index: its own promoted worker will
            # rebuild the full whitelist against the converged source rows.
            return False
        try:
            await asyncio.wait_for(
                repo_query(f"REBUILD INDEX {index} ON TABLE {table}"),
                timeout=_SOURCE_SEARCH_REBUILD_TIMEOUT_S,
            )
        except TimeoutError:
            succeeded = False
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: timed out rebuilding index {} on table {} after {}s "
                "following source deletion",
                index,
                table,
                _SOURCE_SEARCH_REBUILD_TIMEOUT_S,
            )
        except Exception as exc:
            succeeded = False
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: failed to rebuild index {} on table {} after source "
                "deletion: {}",
                index,
                table,
                exc,
            )
    return succeeded


async def _run_source_search_index_maintenance(
    state: _SourceSearchIndexMaintenanceState,
) -> None:
    """Converge marker generations, clearing only after a successful exact CAS."""
    while True:
        try:
            marker = await _pending_source_search_rebuild_marker()
        except Exception as exc:
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: could not read source-search rebuild marker: {}",
                exc,
            )
            return

        if marker is None:
            state.completed_generation = state.generation
            return

        rebuild_token, marker_state = marker
        if marker_state != "ready":
            # An in-flight delete has written intent but not finished its
            # post-sweep. Never rebuild or clear it from an older pass.
            return

        generation = state.generation
        if not await _refresh_source_search_indexes(rebuild_token):
            # Preserve the durable marker for a later delete, shutdown drain,
            # or next application startup; retrying in a tight detached loop
            # would amplify an outage.
            #
            # The one exception is a newer *ready* generation scheduled while
            # this pass was active. Its delete has completed post-sweep, so
            # converge exactly that trailing generation in this same worker.
            # An intent never qualifies here: it belongs to its live delete.
            try:
                newer_marker = await _pending_source_search_rebuild_marker()
            except Exception:
                return
            if (
                newer_marker is not None
                and newer_marker[1] == "ready"
                and state.generation > generation
            ):
                continue
            return

        try:
            cleared = await _clear_source_search_rebuild_marker(rebuild_token)
        except Exception as exc:
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: could not clear source-search rebuild marker {}: {}",
                rebuild_token,
                exc,
            )
            return

        if cleared:
            state.completed_generation = generation
        # A false CAS means another delete wrote a newer marker while this pass
        # ran. Re-read it and perform exactly the required trailing pass.


def _observe_source_search_index_maintenance(
    task: asyncio.Task[None],
) -> None:
    """Consume unexpected task failures so detached work never leaks warnings."""
    try:
        task.result()
    except asyncio.CancelledError:
        # Event-loop shutdown can cancel detached maintenance. A request/task
        # cancellation cannot, because callers never await this task directly.
        return
    except Exception:
        logger.exception("Unexpected source-search index maintenance failure")
    finally:
        # Retain the task strongly only while it is pending. Keeping a finished
        # task would also retain its event loop through the per-loop state map.
        state = _source_search_index_maintenance_states.get(task.get_loop())
        if state is not None and state.task is task:
            state.task = None


def _schedule_source_search_index_maintenance() -> None:
    """Record a successful delete and synchronously schedule coalesced work."""
    state = _source_search_index_maintenance_state()
    state.generation += 1
    if state.task is None or state.task.done():
        task = asyncio.create_task(
            _run_source_search_index_maintenance(state),
            name="source-search-index-maintenance",
        )
        state.task = task
        task.add_done_callback(_observe_source_search_index_maintenance)


async def drain_source_search_index_maintenance(
    *, timeout_s: float | None = SOURCE_SEARCH_INDEX_SHUTDOWN_DRAIN_TIMEOUT_S
) -> bool:
    """Boundedly drain the current loop's durable source-search maintenance.

    A false return preserves the marker for the next startup.  Cancellation is
    deliberately propagated after shielding the detached task, so the caller
    can log the degraded-search condition without silently erasing the receipt.
    """

    async def _drain() -> bool:
        try:
            marker = await _pending_source_search_rebuild_marker()
        except Exception as exc:
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: could not read source-search rebuild marker: {}",
                exc,
            )
            return False
        if marker is None:
            return True

        _rebuild_token, marker_state = marker
        if marker_state != "ready":
            # A live delete can be between its pre-delete receipt and
            # post-sweep promotion. It owns the intent; never turn a shutdown
            # drain into an early rebuild of its incomplete mutation.
            return False

        state = _source_search_index_maintenance_state()
        if state.task is None or state.task.done():
            _schedule_source_search_index_maintenance()
        task = state.task
        assert task is not None
        await asyncio.shield(task)

        try:
            return await _pending_source_search_rebuild_marker() is None
        except Exception as exc:
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: could not verify source-search rebuild marker: {}",
                exc,
            )
            return False

    if timeout_s is None:
        return await _drain()
    return await asyncio.wait_for(_drain(), timeout=timeout_s)


async def reconcile_source_search_index_maintenance() -> bool:
    """Finish any crash-surviving marker before the API begins serving traffic.

    Startup runs before requests are accepted, so an ``intent`` can only be a
    crash-left receipt, not an in-flight delete. Promote it to ``ready`` before
    invoking the normal worker. Request-time workers never make that inference.
    """
    try:
        marker = await _pending_source_search_rebuild_marker()
    except Exception as exc:
        logger.warning(
            "Search relevance may be degraded until the next successful "
            "rebuild: could not read source-search rebuild marker at startup: {}",
            exc,
        )
        return False

    if marker is not None:
        rebuild_token, marker_state = marker
        if marker_state == "intent":
            try:
                if not await _promote_source_search_rebuild_marker(rebuild_token):
                    logger.warning(
                        "Search relevance may be degraded until the next successful "
                        "rebuild: source-search startup could not promote intent {}",
                        rebuild_token,
                    )
                    return False
            except Exception as exc:
                logger.warning(
                    "Search relevance may be degraded until the next successful "
                    "rebuild: source-search startup could not promote intent {}: {}",
                    rebuild_token,
                    exc,
                )
                return False
        elif marker_state != "ready":
            logger.warning(
                "Search relevance may be degraded until the next successful "
                "rebuild: source-search marker has unknown state {}",
                marker_state,
            )
            return False
    return await drain_source_search_index_maintenance(timeout_s=None)


async def cancel_source_search_index_maintenance() -> None:
    """Quiesce this loop's exact maintenance worker without clearing its marker.

    Shutdown must not close the database pool while a timed-out worker can still
    issue rebuild queries. Cancellation deliberately leaves the durable receipt
    intact; startup reconciliation owns the next attempt.
    """
    state = _source_search_index_maintenance_state()
    task = state.task
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # The worker cancellation is expected. Do not clear the marker here.
        pass
    except Exception:
        # The done callback logs unexpected worker failures; shutdown still
        # needs the task quiesced before the database pool is closed.
        pass


async def _wait_for_source_search_index_maintenance() -> None:
    """Compatibility test helper; production uses the public drain above."""
    await drain_source_search_index_maintenance(timeout_s=None)


class _UnsafeUploadCleanupError(OSError):
    """The stored upload path cannot be unlinked without following names."""


def _secure_upload_unlink_is_supported() -> bool:
    """Return whether this platform exposes the required unlinkat primitives."""
    supports_dir_fd = getattr(os, "supports_dir_fd", frozenset())
    supports_follow_symlinks = getattr(
        os,
        "supports_follow_symlinks",
        frozenset(),
    )
    return (
        hasattr(os, "O_DIRECTORY")
        and hasattr(os, "O_NOFOLLOW")
        and hasattr(os, "O_NONBLOCK")
        and os.open in supports_dir_fd
        and os.stat in supports_dir_fd
        and os.unlink in supports_dir_fd
        and os.stat in supports_follow_symlinks
    )


def _verify_directory_identities(
    identities: list[tuple[int, str, int, int]],
) -> None:
    """Ensure every visible child still names its pinned directory."""
    for parent_fd, name, device, inode in identities:
        current = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISDIR(current.st_mode)
            or current.st_dev != device
            or current.st_ino != inode
        ):
            raise _UnsafeUploadCleanupError("upload-directory-identity-changed")


def _secure_unlink_uploaded_file(file_path: Path, uploads_root: Path) -> bool:
    """Unlink one regular upload through pinned, no-follow descriptors.

    Returns False when the stored file is already absent. Platforms without
    descriptor-relative no-follow operations fail closed instead of falling
    back to a race-prone pathname unlink.
    """
    root = Path(os.path.abspath(os.fspath(uploads_root)))
    candidate = Path(os.path.abspath(os.fspath(file_path)))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise _UnsafeUploadCleanupError("upload-path-outside-root") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise _UnsafeUploadCleanupError("upload-path-is-not-a-file")

    if os.name == "nt":
        from deeper_notebook.domain.windows_upload_cleanup import (
            secure_unlink_uploaded_file_windows,
        )

        return secure_unlink_uploaded_file_windows(root, relative)
    if not _secure_upload_unlink_is_supported():
        raise _UnsafeUploadCleanupError("secure-dir-fd-unlink-unavailable")

    directory_flags = (
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    )
    file_flags = (
        os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    )
    descriptors: list[int] = []
    identities: list[tuple[int, str, int, int]] = []
    file_fd: int | None = None
    try:
        anchor = root.anchor
        if not anchor:
            raise _UnsafeUploadCleanupError("upload-root-has-no-anchor")
        current_fd = os.open(anchor, directory_flags)
        descriptors.append(current_fd)
        if not stat.S_ISDIR(os.fstat(current_fd).st_mode):
            raise _UnsafeUploadCleanupError("upload-anchor-is-not-directory")

        root_components = root.parts[1:]
        parent_components = relative.parts[:-1]
        for component in (*root_components, *parent_components):
            parent_fd = current_fd
            current_fd = os.open(
                component,
                directory_flags,
                dir_fd=parent_fd,
            )
            descriptors.append(current_fd)
            result = os.fstat(current_fd)
            if not stat.S_ISDIR(result.st_mode):
                raise _UnsafeUploadCleanupError("upload-parent-is-not-directory")
            identities.append((parent_fd, component, result.st_dev, result.st_ino))

        _verify_directory_identities(identities)
        name = relative.parts[-1]
        try:
            file_fd = os.open(name, file_flags, dir_fd=current_fd)
        except FileNotFoundError:
            return False
        result = os.fstat(file_fd)
        if not stat.S_ISREG(result.st_mode):
            raise _UnsafeUploadCleanupError("upload-target-is-not-regular-file")

        visible = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(visible.st_mode)
            or visible.st_dev != result.st_dev
            or visible.st_ino != result.st_ino
        ):
            raise _UnsafeUploadCleanupError("upload-file-identity-changed")
        _verify_directory_identities(identities)
        os.unlink(name, dir_fd=current_fd)
        return True
    finally:
        if file_fd is not None:
            os.close(file_fd)
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class ExternalNoteReadOnlyError(DeeperNotebookError):
    """A canonical external note can only be refreshed by the vault projector."""

    code: ClassVar[str] = "external_note_read_only"

    def __init__(self) -> None:
        super().__init__(self.code)


class Notebook(ObjectModel):
    table_name: ClassVar[str] = "notebook"
    name: str
    description: str
    archived: Optional[bool] = False

    @field_validator("name")
    @classmethod
    def name_must_not_be_empty(cls, v):
        if not v.strip():
            raise InvalidInputError("Notebook name cannot be empty")
        return v

    async def get_sources(self, include_full_text: bool = False) -> list["Source"]:
        try:
            omit_clause = "" if include_full_text else "omit source.full_text"
            srcs = await repo_query(
                f"""
                select * {omit_clause} from (
                select in as source from reference where out=$id
                fetch source
            ) order by source.updated desc
            """,
                {"id": ensure_record_id(self.id)},
            )
            return [Source(**src["source"]) for src in srcs] if srcs else []
        except Exception as e:
            logger.error(f"Error fetching sources for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def get_notes(self, include_content: bool = False) -> list["Note"]:
        try:
            omit_clause = (
                "omit note.embedding"
                if include_content
                else "omit note.content, note.embedding"
            )
            srcs = await repo_query(
                f"""
            select * {omit_clause} from (
                select in as note from artifact where out=$id
                fetch note
            ) order by note.updated desc
            """,
                {"id": ensure_record_id(self.id)},
            )
            return [Note(**src["note"]) for src in srcs] if srcs else []
        except Exception as e:
            logger.error(f"Error fetching notes for notebook {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def get_graph(self) -> dict[str, Any]:
        """v0.8.83 — mind-map graph (improvement roadmap, Batch 3).
        v0.8.122 — Studio artifacts in mind-map graph with grounded_in source edges.

        Returns the notebook as a hub node with its sources, notes, and
        studio artifacts as connected nodes, grounded in the existing
        ``reference`` (source→notebook), ``artifact`` (note→notebook),
        ``studio_artifact`` (artifact→notebook), and ``grounded_in``
        (artifact→source) edges — no schema change. Node ids are the record
        ids so the frontend can deep-link to each item; labels are trimmed
        to keep the payload small.
        """
        sources = await self.get_sources()
        notes = await self.get_notes()
        try:
            artifacts = await StudioArtifact.get_for_notebook(str(self.id))
        except Exception as exc:
            logger.debug(
                f"Could not load studio artifacts for notebook {self.id} graph: {exc}"
            )
            artifacts = []

        def _label(text: Optional[str], fallback: str) -> str:
            cleaned = (text or "").strip() or fallback
            return cleaned if len(cleaned) <= 80 else cleaned[:79] + "…"

        nodes: list[dict[str, Any]] = [
            {
                "id": str(self.id),
                "type": "notebook",
                "label": _label(self.name, "Notebook"),
            }
        ]
        edges: list[dict[str, Any]] = []
        source_id_set = {str(s.id) for s in sources}
        for s in sources:
            nodes.append(
                {
                    "id": str(s.id),
                    "type": "source",
                    "label": _label(s.title, "Untitled source"),
                }
            )
            edges.append(
                {"source": str(self.id), "target": str(s.id), "kind": "reference"}
            )
        for n in notes:
            nodes.append(
                {
                    "id": str(n.id),
                    "type": "note",
                    "label": _label(n.title, "Untitled note"),
                }
            )
            edges.append(
                {"source": str(self.id), "target": str(n.id), "kind": "artifact"}
            )
        for a in artifacts:
            nodes.append(
                {
                    "id": str(a.id),
                    "type": "studio_artifact",
                    "label": _label(a.title, "Untitled artifact"),
                    "artifact_type": getattr(a, "artifact_type", None),
                }
            )
            edges.append(
                {"source": str(self.id), "target": str(a.id), "kind": "studio_artifact"}
            )
            for sid in (getattr(a, "source_ids", None) or []):
                sid_str = str(sid)
                if sid_str in source_id_set:
                    edges.append(
                        {"source": str(a.id), "target": sid_str, "kind": "grounded_in"}
                    )
        return {"nodes": nodes, "edges": edges}

    async def get_chat_sessions(
        self,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list["ChatSession"]:
        """Fetch all chat sessions for this notebook, newest-first.

        v0.7.169 — Optional `limit` / `offset` pagination. Previously
        this returned EVERY chat session attached to the notebook with
        no bound — a power user with hundreds of long-running chats
        paid for the full table fan-out PLUS the per-session LangGraph
        checkpoint read (v0.7.161 made those concurrent, but the
        underlying session list itself was still unbounded). Now
        the router can pass `limit=` to cap the response and keep the
        right-rail Chat list snappy.

        Defaults stay None for backward compatibility — callers that
        don't ask for pagination keep the pre-v0.7.169 unbounded
        behavior.
        """
        try:
            # v0.7.169 — Inject `LIMIT $limit START $offset` only when
            # the caller asked. Validate the inputs aggressively
            # (positive int / non-negative int respectively) BEFORE
            # interpolating into the query — SurrealQL accepts integer
            # literals via direct interpolation, but it doesn't sanitize
            # them the way a SQL driver would. Mirror the v0.7.159
            # `ObjectModel.get_all` validation pattern.
            if limit is not None:
                if not isinstance(limit, int) or limit <= 0 or isinstance(limit, bool):
                    raise InvalidInputError(
                        f"limit must be a positive int, got {limit!r}"
                    )
            if offset is not None:
                if (
                    not isinstance(offset, int)
                    or offset < 0
                    or isinstance(offset, bool)
                ):
                    raise InvalidInputError(
                        f"offset must be a non-negative int, got {offset!r}"
                    )
            tail = ""
            if limit is not None:
                tail = f" LIMIT {limit}"
            if offset is not None:
                tail += f" START {offset}"
            srcs = await repo_query(
                f"""
                select * from (
                    select
                    <- chat_session as chat_session
                    from refers_to
                    where out=$id
                    fetch chat_session
                )
                order by chat_session.updated desc{tail}
            """,  # nosec B608
                {"id": ensure_record_id(self.id)},
            )
            return (
                [ChatSession(**src["chat_session"][0]) for src in srcs] if srcs else []
            )
        except InvalidInputError:
            # v0.7.169 — Let typed input errors propagate to the
            # global exception handler (mapped to HTTP 400) rather
            # than getting clobbered to 500 by the generic except below.
            raise
        except Exception as e:
            logger.error(
                f"Error fetching chat sessions for notebook {self.id}: {str(e)}"
            )
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def get_delete_preview(self) -> dict[str, Any]:
        """
        Get counts of items that would be affected by deleting this notebook.

        Returns a dict with:
        - note_count: Number of notes that will be deleted
        - exclusive_source_count: Sources only in this notebook (can be deleted)
        - shared_source_count: Sources in other notebooks (will be unlinked only)
        """
        try:
            notebook_id = ensure_record_id(self.id)

            # Count notes
            note_result = await repo_query(
                "SELECT count() as count FROM artifact WHERE out = $notebook_id GROUP ALL",
                {"notebook_id": notebook_id},
            )
            note_count = note_result[0]["count"] if note_result else 0

            # Get sources with count of references to OTHER notebooks
            # If assigned_others = 0, source is exclusive to this notebook
            # If assigned_others > 0, source is shared with other notebooks
            source_counts = await repo_query(
                """
                SELECT
                    id,
                    count(->reference[WHERE out != $notebook_id].out) as assigned_others
                FROM (SELECT VALUE <-reference.in AS sources FROM $notebook_id)[0]
                """,
                {"notebook_id": notebook_id},
            )

            exclusive_count = 0
            shared_count = 0
            for src in source_counts:
                if src.get("assigned_others", 0) == 0:
                    exclusive_count += 1
                else:
                    shared_count += 1

            return {
                "note_count": note_count,
                "exclusive_source_count": exclusive_count,
                "shared_source_count": shared_count,
            }
        except Exception as e:
            logger.error(f"Error getting delete preview for notebook {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def delete(self, delete_exclusive_sources: bool = False) -> dict[str, Any]:
        """Atomically delete this notebook and all database-owned descendants.

        The hydrated note list is used only as an optimistic snapshot. The
        transaction re-reads the persisted rows, verifies the exact note set,
        and rejects file-canonical projections before its first destructive
        statement. A mismatch or external note raises via ``THROW`` and
        SurrealDB rolls the complete cascade back.

        Worker cancellation and uploaded-file cleanup are deliberately outside
        the database transaction. They run best-effort only after commit, and
        the source cleanup boundary refuses to unlink anything outside the
        configured uploads directory.
        """
        if self.id is None:
            raise InvalidInputError("Cannot delete notebook without an ID")

        try:
            notebook_id = ensure_record_id(self.id)
            notes = await self.get_notes()
            sources = await self.get_sources()
            expected_note_ids = [
                ensure_record_id(note.id) for note in notes if note.id is not None
            ]

            rows = await repo_query(
                """
                BEGIN TRANSACTION;

                IF (SELECT
                        id,
                        canonical_external,
                        external_state
                    FROM note
                    WHERE id IN (SELECT VALUE in FROM artifact
                        WHERE out = $notebook_id)
                ).any(|$note|
                    $note.canonical_external = true
                    OR ($note.external_state != NONE
                        AND $note.external_state != NULL)
                ) {
                    THROW "external_note_read_only";
                };
                IF (SELECT VALUE in FROM artifact
                        WHERE out = $notebook_id).len() != $expected_note_count
                    OR NOT ((SELECT VALUE in FROM artifact
                        WHERE out = $notebook_id)
                        CONTAINSALL $expected_note_ids)
                    OR NOT ($expected_note_ids CONTAINSALL
                        (SELECT VALUE in FROM artifact
                            WHERE out = $notebook_id))
                {
                    THROW "notebook_note_set_changed";
                };
                IF (SELECT
                        id,
                        canonical_external,
                        external_state
                    FROM note
                    WHERE id IN (SELECT VALUE in FROM artifact
                        WHERE out = $notebook_id)
                ).len() != $expected_note_count {
                    THROW "notebook_note_set_changed";
                };

                LET $current_note_ids = SELECT VALUE in
                    FROM artifact
                    WHERE out = $notebook_id;
                LET $source_rows = SELECT
                        id,
                        count(->reference[WHERE out != $notebook_id].out)
                            AS assigned_others
                    FROM (
                        SELECT VALUE <-reference.in AS sources
                        FROM $notebook_id
                    )[0];
                LET $exclusive_source_ids = IF $delete_exclusive_sources {
                    $source_rows
                        .filter(|$source| $source.assigned_others = 0)
                        .map(|$source| $source.id)
                } ELSE {
                    []
                };
                LET $source_count = $source_rows.len();
                LET $notebook_reference_ids = SELECT VALUE id
                    FROM reference
                    WHERE out = $notebook_id;
                LET $chat_session_ids = SELECT VALUE in
                    FROM refers_to
                    WHERE out = $notebook_id;

                DELETE note WHERE id IN $current_note_ids;
                DELETE artifact WHERE in IN $current_note_ids;
                DELETE artifact WHERE out = $notebook_id;
                DELETE source_embedding WHERE source IN $exclusive_source_ids;
                DELETE source_insight WHERE source IN $exclusive_source_ids;
                DELETE reference WHERE in IN $exclusive_source_ids;
                DELETE source WHERE id IN $exclusive_source_ids;
                DELETE $notebook_reference_ids;
                DELETE refers_to WHERE out = $notebook_id;
                DELETE chat_session WHERE id IN $chat_session_ids;
                DELETE $notebook_id;

                LET $result = {
                    deleted_notes: $current_note_ids.len(),
                    deleted_sources: $exclusive_source_ids.len(),
                    unlinked_sources: IF $delete_exclusive_sources {
                        $source_count - $exclusive_source_ids.len()
                    } ELSE {
                        $source_count
                    },
                    deleted_chat_session_ids: $chat_session_ids,
                    exclusive_source_ids: $exclusive_source_ids
                };
                RETURN $result;
                COMMIT TRANSACTION;
                """,
                {
                    "notebook_id": notebook_id,
                    "expected_note_ids": expected_note_ids,
                    "expected_note_count": len(notes),
                    "delete_exclusive_sources": delete_exclusive_sources,
                },
            )
            result = self._extract_delete_result(rows)
            # Preserve the exact domain-to-router checkpoint-cleanup contract.
            deleted_chat_session_ids = (
                [str(value) for value in result["deleted_chat_session_ids"]]
                if result["deleted_chat_session_ids"]
                else []
            )
            exclusive_source_ids = {
                str(value) for value in result["exclusive_source_ids"]
            }

            if exclusive_source_ids:
                for source in sources:
                    if source.id is None or str(source.id) not in exclusive_source_ids:
                        continue
                    try:
                        source._cleanup_uploaded_file()
                    except Exception as exc:
                        logger.warning(
                            "Post-commit cleanup failed for source {}: {}",
                            source.id,
                            exc,
                        )

            logger.info(
                "Deleted notebook {} atomically: {} notes, {} exclusive "
                "sources, {} unlinked sources, {} chat sessions",
                self.id,
                result["deleted_notes"],
                result["deleted_sources"],
                result["unlinked_sources"],
                len(deleted_chat_session_ids),
            )
            return {
                "deleted_notes": result["deleted_notes"],
                "deleted_sources": result["deleted_sources"],
                "unlinked_sources": result["unlinked_sources"],
                "deleted_chat_session_ids": deleted_chat_session_ids,
            }

        except ExternalNoteReadOnlyError:
            raise
        except Exception as e:
            if "external_note_read_only" in str(e):
                raise ExternalNoteReadOnlyError() from e
            logger.error(f"Error deleting notebook {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(f"Failed to delete notebook: {e}")

    @staticmethod
    def _extract_delete_result(rows: Any) -> dict[str, Any]:
        """Find and validate the transaction's final structured result."""

        required = {
            "deleted_notes",
            "deleted_sources",
            "unlinked_sources",
            "deleted_chat_session_ids",
            "exclusive_source_ids",
        }

        def candidates(value: Any):
            if isinstance(value, dict):
                yield value
                for nested in value.values():
                    yield from candidates(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from candidates(nested)

        result = next(
            (
                candidate
                for candidate in candidates(rows)
                if required <= candidate.keys()
            ),
            None,
        )
        if result is None:
            raise DatabaseOperationError(
                "Notebook delete transaction returned no result"
            )
        for key in ("deleted_notes", "deleted_sources", "unlinked_sources"):
            if not isinstance(result[key], int) or isinstance(result[key], bool):
                raise DatabaseOperationError(
                    f"Notebook delete transaction returned invalid {key}"
                )
        for key in ("deleted_chat_session_ids", "exclusive_source_ids"):
            if not isinstance(result[key], list):
                raise DatabaseOperationError(
                    f"Notebook delete transaction returned invalid {key}"
                )
        return result


class Asset(BaseModel):
    file_path: Optional[str] = None
    url: Optional[str] = None


class SourceEmbedding(ObjectModel):
    table_name: ClassVar[str] = "source_embedding"
    content: str

    async def get_source(self) -> "Source":
        try:
            src = await repo_query(
                """
            select source.* from $id fetch source
            """,
                {"id": ensure_record_id(self.id)},
            )
            return Source(**src[0]["source"])
        except Exception as e:
            logger.error(f"Error fetching source for embedding {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)


class SourceInsight(ObjectModel):
    table_name: ClassVar[str] = "source_insight"
    insight_type: str
    content: str

    async def get_source(self) -> "Source":
        try:
            src = await repo_query(
                """
            select source.* from $id fetch source
            """,
                {"id": ensure_record_id(self.id)},
            )
            return Source(**src[0]["source"])
        except Exception as e:
            logger.error(f"Error fetching source for insight {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def save_as_note(self, notebook_id: Optional[str] = None) -> Any:
        source = await self.get_source()
        note = Note(
            title=f"{self.insight_type} from source {source.title}",
            content=self.content,
        )
        await note.save()
        if notebook_id:
            await note.add_to_notebook(notebook_id)
        return note


class Source(ObjectModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    table_name: ClassVar[str] = "source"
    asset: Optional[Asset] = None
    title: Optional[str] = None
    topics: Optional[list[str]] = Field(default_factory=list)
    provenance: Optional[dict[str, Any]] = Field(default_factory=dict)
    source_type: Optional[
        Literal["link", "upload", "text", "web_import", "deep_research_report"]
    ] = None
    full_text: Optional[str] = None
    command: Optional[str | RecordID] = Field(
        default=None, description="Link to surreal-commands processing job"
    )

    @field_validator("command", mode="before")
    @classmethod
    def parse_command(cls, value):
        """Parse command field to ensure RecordID format"""
        if isinstance(value, str) and value:
            return ensure_record_id(value)
        return value

    # v0.8.66 (audit D-6) — the per-Source `parse_id` validator was hoisted to
    # ObjectModel base (`_coerce_id_to_str`) so all models coerce id uniformly.

    async def get_status(self) -> Optional[str]:
        """Get the processing status of the associated command"""
        if not self.command:
            return None

        try:
            from surreal_commands import get_command_status

            status = await get_command_status(str(self.command))
            return status.status if status else "unknown"
        except Exception as e:
            logger.warning(f"Failed to get command status for {self.command}: {e}")
            return "unknown"

    async def get_processing_progress(self) -> Optional[dict[str, Any]]:
        """Get detailed processing information for the associated command"""
        if not self.command:
            return None

        try:
            from surreal_commands import get_command_status

            status_result = await get_command_status(str(self.command))
            if not status_result:
                return None

            # Extract execution metadata if available
            result = getattr(status_result, "result", None)
            execution_metadata = (
                result.get("execution_metadata", {}) if isinstance(result, dict) else {}
            )

            return {
                "status": status_result.status,
                "started_at": execution_metadata.get("started_at"),
                "completed_at": execution_metadata.get("completed_at"),
                "error": getattr(status_result, "error_message", None),
                "progress": getattr(status_result, "progress", None),
                "result": result,
            }
        except Exception as e:
            logger.warning(f"Failed to get command progress for {self.command}: {e}")
            return None

    async def get_context(
        self, context_size: Literal["short", "long"] = "short"
    ) -> dict[str, Any]:
        insights_list = await self.get_insights()
        insights = [insight.model_dump() for insight in insights_list]
        if context_size == "long":
            return dict(
                id=self.id,
                title=self.title,
                insights=insights,
                full_text=self.full_text,
            )
        else:
            return dict(id=self.id, title=self.title, insights=insights)

    async def get_embedded_chunks(self) -> int:
        try:
            result = await repo_query(
                """
                select count() as chunks from source_embedding where source=$id GROUP ALL
                """,
                {"id": ensure_record_id(self.id)},
            )
            if len(result) == 0:
                return 0
            return result[0]["chunks"]
        except Exception as e:
            logger.error(f"Error fetching chunks count for source {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError(f"Failed to count chunks for source: {str(e)}")

    async def get_insights(self) -> list[SourceInsight]:
        try:
            result = await repo_query(
                """
                SELECT * FROM source_insight WHERE source=$id
                """,
                {"id": ensure_record_id(self.id)},
            )
            return [SourceInsight(**insight) for insight in result]
        except Exception as e:
            logger.error(f"Error fetching insights for source {self.id}: {str(e)}")
            logger.exception(e)
            raise DatabaseOperationError("Failed to fetch insights for source")

    async def add_to_notebook(self, notebook_id: str) -> Any:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        # v0.7.73 — defensive dedup at the domain layer. The HTTP endpoint
        # POST /notebooks/{notebook_id}/sources/{source_id} already has
        # an idempotency check (fixed in v0.7.60), but direct domain
        # calls (sources.py:493/572 upload flow, studio.py:375 batch
        # upload, the Source.add_to_notebook path from the upcoming
        # bulk-link feature) didn't. A duplicate call would create a
        # second `reference` edge, inflating source_count and
        # requiring multiple deletes-from-notebook clicks to remove
        # the source.
        existing = await repo_query(
            "SELECT * FROM reference WHERE out = $notebook_id AND in = $source_id",
            {
                "notebook_id": ensure_record_id(notebook_id),
                "source_id": ensure_record_id(self.id),
            },
        )
        if existing:
            return existing[0]
        return await self.relate("reference", notebook_id)

    async def vectorize(self) -> str:
        """
        Submit vectorization as a background job using the embed_source command.

        This method leverages the job-based architecture to prevent HTTP connection
        pool exhaustion when processing large documents. The embed_source command:
        1. Detects content type from file path
        2. Chunks text using content-type aware splitter
        3. Generates all embeddings in batches
        4. Bulk inserts source_embedding records

        Returns:
            str: The command/job ID that can be used to track progress via the commands API

        Raises:
            ValueError: If source has no text to vectorize
            DatabaseOperationError: If job submission fails
        """
        logger.info(f"Submitting embed_source job for source {self.id}")
        # v0.7.76 — same to_thread treatment as v0.7.55/57/62/68/70.
        # surreal_commands.submit_command opens a synchronous SurrealDB
        # WebSocket; running it directly inside this `async def` blocks
        # the FastAPI event loop for the handshake duration. Move it
        # to a worker thread.

        try:
            if not self.full_text or not self.full_text.strip():
                raise ValueError(f"Source {self.id} has no text to vectorize")

            # Submit the embed_source command
            command_id = await asyncio.to_thread(
                submit_command,
                "open_notebook",
                "embed_source",
                {"source_id": str(self.id)},
            )

            command_id_str = str(command_id)
            logger.info(
                f"Embed source job submitted for source {self.id}: "
                f"command_id={command_id_str}"
            )

            return command_id_str

        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to submit embed_source job for source {self.id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    async def add_insight(self, insight_type: str, content: str) -> Optional[str]:
        """
        Submit insight creation as an async command (fire-and-forget).

        Submits a create_insight command that handles database operations with
        automatic retry logic for transaction conflicts. The command also submits
        an embed_insight command for async embedding.

        This method returns immediately after submitting the command - it does NOT
        wait for the insight to be created. Use this for batch operations where
        throughput is more important than immediate confirmation.

        Args:
            insight_type: Type/category of the insight
            content: The insight content text

        Returns:
            command_id for optional tracking, or None if submission failed

        Raises:
            InvalidInputError: If insight_type or content is empty
        """
        if not insight_type or not content:
            raise InvalidInputError("Insight type and content must be provided")

        try:
            # Submit create_insight command (fire-and-forget)
            # Command handles retries internally for transaction conflicts
            # v0.7.76 — to_thread the sync submit_command, see vectorize().
            command_id = await asyncio.to_thread(
                submit_command,
                "open_notebook",
                "create_insight",
                {
                    "source_id": str(self.id),
                    "insight_type": insight_type,
                    "content": content,
                },
            )
            logger.info(
                f"Submitted create_insight command {command_id} for source {self.id} "
                f"(type={insight_type})"
            )
            return str(command_id)

        except Exception as e:
            logger.error(f"Error submitting create_insight for source {self.id}: {e}")
            return None

    def _prepare_save_data(self) -> dict:
        """Override to ensure command field is always RecordID format for database"""
        data = super()._prepare_save_data()

        # Ensure command field is RecordID format if not None
        if data.get("command") is not None:
            data["command"] = ensure_record_id(data["command"])

        return data

    async def _cancel_processing_command(self) -> None:
        """Best-effort cancellation for an ordinary source deletion."""
        if self.command:
            try:
                from surreal_commands import get_command_status
                from surreal_commands.core.service import get_command_service

                status = await get_command_status(str(self.command))
                # `status` is a CommandResult enum value or string. Only
                # nudge active jobs; completed/failed jobs need no action.
                status_str = getattr(status, "value", str(status)).lower()
                if status_str in {"new", "running", "queued"}:
                    svc = get_command_service()
                    await svc.update_command_result(
                        str(self.command),
                        status="canceled",
                        result={},
                        error_message=(
                            f"Source {self.id} was deleted by the user "
                            f"before processing completed."
                        ),
                    )
                    logger.info(
                        f"Cancelled in-flight command {self.command} for "
                        f"source {self.id} (was {status_str})"
                    )
            except Exception as e:
                logger.warning(
                    f"Could not cancel command {self.command} for source "
                    f"{self.id}: {e}. Continuing with deletion."
                )

    def _cleanup_uploaded_file(self) -> None:
        """Remove an owned upload without consulting queue or database state."""

        if self.asset and self.asset.file_path:
            file_path = Path(self.asset.file_path)
            from deeper_notebook.config import UPLOADS_FOLDER as _uploads

            try:
                deleted = _secure_unlink_uploaded_file(
                    file_path,
                    Path(_uploads),
                )
            except (OSError, ValueError) as exc:
                logger.warning(
                    f"Refusing to unlink unsafe file_path for source {self.id}: "
                    f"{file_path} (uploads root: {_uploads}; reason: {exc}). "
                    "Continuing with database deletion."
                )
            else:
                if deleted:
                    logger.info(f"Deleted file for source {self.id}: {file_path}")
                    return
                logger.debug(
                    f"File {file_path} not found for source {self.id}, skipping cleanup"
                )

    async def delete(self) -> bool:
        """Delete source and clean up associated file, embeddings, and insights.

        v0.6.34 — verifies the file_path is INSIDE UPLOADS_FOLDER before
        unlinking.

        v0.7.32 — also cancels any in-flight processing command. Without
        this, deleting a source mid-embed left the worker running
        against a now-dead source, writing fresh source_embedding rows
        pointing at the deleted source. Orphan data + wasted GPU.
        """
        state = _source_search_index_maintenance_state()
        async with state.deletion_lock:
            # This durable receipt is deliberately first: a forced kill after
            # any file/database mutation must still trigger reconciliation. The
            # mutex protects this singleton receipt through promotion, so one
            # false/no-op delete cannot eclipse another live delete's token.
            rebuild_token = await _mark_source_search_rebuild_pending()
            source_id = ensure_record_id(self.id)
            result = False

            try:
                await self._cancel_processing_command()
                self._cleanup_uploaded_file()

                # Delete associated embeddings and insights to prevent orphaned
                # records. Reference edges are likewise removed before parent
                # deletion so notebooks cannot retain null fetched sources.
                try:
                    await repo_query(
                        "DELETE source_embedding WHERE source = $source_id",
                        {"source_id": source_id},
                    )
                    await repo_query(
                        "DELETE source_insight WHERE source = $source_id",
                        {"source_id": source_id},
                    )
                    await repo_query(
                        "DELETE reference WHERE in = $source_id",
                        {"source_id": source_id},
                    )
                    logger.debug(
                        "Deleted embeddings, insights, and reference edges for "
                        "source {}",
                        self.id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to delete embeddings/insights for source {}: {}. "
                        "Continuing with source deletion.",
                        self.id,
                        exc,
                    )

                # Preserve the parent's bool/exception semantics. The durable
                # finalizer below still runs after this mutation attempt even
                # when it returns False.
                result = await super().delete()
            finally:
                # v0.7.133 race-window post-sweep: workers can write after the
                # pre-sweep but before/while parent deletion settles. This is
                # deliberately in the mutex-protected finalizer so cancellation
                # cannot release the singleton marker before promotion.
                try:
                    try:
                        await repo_query(
                            "DELETE source_embedding WHERE source = $source_id",
                            {"source_id": source_id},
                        )
                        await repo_query(
                            "DELETE source_insight WHERE source = $source_id",
                            {"source_id": source_id},
                        )
                        logger.debug(
                            "Race-window post-sweep cleared any stragglers for "
                            "source {}",
                            self.id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Race-window post-sweep failed for source {}: {}. "
                            "Orphan embeddings/insights may exist but are "
                            "unreachable via the API.",
                            self.id,
                            exc,
                        )
                finally:
                    # Any mutation attempt, including a false/no-op parent
                    # result or cancellation, must advance its own intent and
                    # schedule convergence before the deletion mutex releases.
                    try:
                        if await _promote_source_search_rebuild_marker(rebuild_token):
                            _schedule_source_search_index_maintenance()
                        else:
                            logger.warning(
                                "Search relevance may be degraded until the next "
                                "successful rebuild: source-search marker {} was "
                                "not promoted after source {} post-sweep",
                                rebuild_token,
                                self.id,
                            )
                    except asyncio.CancelledError:
                        logger.warning(
                            "Search relevance may be degraded until the next "
                            "successful rebuild: source-search marker {} remains "
                            "intent after cancellation following source {} "
                            "post-sweep",
                            rebuild_token,
                            self.id,
                        )
                        raise
                    except Exception as exc:
                        logger.warning(
                            "Search relevance may be degraded until the next "
                            "successful rebuild: could not promote source-search "
                            "marker {} after source {} post-sweep: {}",
                            rebuild_token,
                            self.id,
                            exc,
                        )

            return result


class Note(ObjectModel):
    table_name: ClassVar[str] = "note"
    title: Optional[str] = None
    note_type: Optional[Literal["human", "ai"]] = None
    content: Optional[str] = None
    vault_id: str | None = None
    vault_file_id: str | None = None
    source_format: str | None = None
    canonical_external: bool | None = None
    properties: dict[str, Any] | None = None
    tags: list[str] | None = None
    source_hash: str | None = None
    external_state: str | None = None

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, v):
        if v is not None and not v.strip():
            raise InvalidInputError("Note content cannot be empty")
        return v

    async def save(self) -> Optional[str]:
        """
        Save the note and submit embedding command.

        Overrides ObjectModel.save() to submit an async embed_note command
        after saving, instead of inline embedding.

        Returns:
            Optional[str]: The command_id if embedding was submitted, None otherwise
        """
        if not _projection_refresh_is_active() and await self._is_canonical_external():
            raise ExternalNoteReadOnlyError()

        # Call parent save (without embedding)
        await super().save()

        # Submit embedding command (fire-and-forget) if note has content
        if self.id and self.content and self.content.strip():
            # v0.7.76 — to_thread the sync submit_command, see vectorize().
            #
            # v0.7.129 — wrap submission in a try/except so a missing
            # registry entry (worker not running yet, worker not
            # importing the `commands` module, integration tests with
            # no worker process at all) doesn't fail an otherwise
            # successful save. Per the "fire-and-forget" contract
            # documented in domain/CLAUDE.md, embedding is
            # eventual-consistency — the note row itself is what
            # matters.
            #
            # v0.7.133 — replaced fragile string-match against the
            # exception message ("Command not found") with a direct
            # registry-introspection pre-check. The previous code did
            # `except ValueError: if "Command not found" in str(e)`,
            # which would silently break if surreal_commands ever
            # changed its error message (the library just raises plain
            # ValueError, no typed exception class). Now we ask the
            # registry directly via `registry.get_command_by_id()` —
            # returns None if the command isn't registered, no
            # exception. Cleaner intent + no string brittleness.
            if not _is_command_registered("open_notebook.embed_note"):
                logger.warning(
                    f"embed_note not in surreal-commands registry — "
                    f"note {self.id} saved without embedding. Embedding "
                    f"will run when the worker starts and the note is "
                    f"re-saved, or via a manual re-embed."
                )
                return None

            try:
                command_id = await asyncio.to_thread(
                    submit_command,
                    "open_notebook",
                    "embed_note",
                    {"note_id": str(self.id)},
                )
                logger.debug(f"Submitted embed_note command {command_id} for {self.id}")
                return command_id
            except ValueError:
                # v0.7.133 — Narrow propagation for ValueError. The
                # registry-miss case was already filtered by the
                # `_is_command_registered` pre-check above, so a
                # ValueError reaching here represents a real bug
                # (bad argument from caller, validation in
                # surreal_commands itself) that should surface, not
                # silently turn into "note saved without embedding".
                # Pinned by test_save_propagates_unrelated_value_errors.
                raise
            except Exception as e:
                # Connection failure, worker DB unreachable, transient
                # surreal_commands DB write error — the save is durable;
                # embedding is best-effort.
                logger.warning(
                    f"Failed to submit embed_note for {self.id}: {e}. "
                    f"Note saved; embedding will retry on next save."
                )
                return None

        return None

    async def add_to_notebook(self, notebook_id: str) -> Any:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        if await self._is_canonical_external():
            raise ExternalNoteReadOnlyError()
        # v0.7.73 — defensive dedup at the domain layer. Same rationale
        # as Source.add_to_notebook: the artifact-edge create was not
        # idempotent, and `notes.py:90` (create_note flow) could
        # double-link if the request was retried. Inflated note_count
        # required multiple unlinks per note.
        existing = await repo_query(
            "SELECT * FROM artifact WHERE out = $notebook_id AND in = $note_id",
            {
                "notebook_id": ensure_record_id(notebook_id),
                "note_id": ensure_record_id(self.id),
            },
        )
        if existing:
            return existing[0]
        return await self.relate("artifact", notebook_id)

    async def delete(self) -> bool:
        """Delete the note and cascade its artifact edges (the embedding is a
        column on the row, removed with it — there is no note_embedding table).

        v0.7.76 — base ObjectModel.delete only deletes the note record.
        Without explicit cascade, the `artifact` edges pointing at the
        note survive: get_notes on the parent notebook does
        `select in as note from artifact where out=$id fetch note`,
        which would then `fetch note` → null and crash on `Note(**None)`.
        Symmetric to Source.delete (v0.6.34 / v0.7.32) and the
        Notebook.delete chat_session cascade (v0.7.61).
        """
        if self.id is None:
            from deeper_notebook.exceptions import InvalidInputError as _IIE

            raise _IIE("Cannot delete note without an ID")
        if await self._is_canonical_external():
            raise ExternalNoteReadOnlyError()
        try:
            note_id = ensure_record_id(self.id)
            # Delete artifact edges first so the FETCH path in
            # get_notes can't race a half-deleted note.
            await repo_query(
                "DELETE artifact WHERE in = $note_id",
                {"note_id": note_id},
            )
            # v0.8.66 (audit D-1) — removed the dead `DELETE note_embedding`
            # cleanup. `note_embedding` is a phantom table (no migration defines
            # it); the note's embedding is a column on the row and is removed by
            # super().delete() below. (Edges are still deleted FIRST, above, to
            # keep the get_notes FETCH path from racing a half-deleted note —
            # v0.7.76.)
        except Exception as exc:
            logger.warning(
                f"Failed to cascade-clean artifact edges for note "
                f"{self.id}: {exc}. Continuing with note deletion."
            )
        return await super().delete()

    async def _is_canonical_external(self) -> bool:
        """Fail closed when the current or persisted row is file-canonical."""

        if self.canonical_external is True:
            return True
        if self.id is None:
            return False
        rows = await repo_query(
            "SELECT canonical_external FROM $id",
            {"id": ensure_record_id(self.id)},
        )
        return bool(rows and rows[0].get("canonical_external") is True)

    # v0.8.67 (audit A3) — "short" note context budget. Was a flat 100-CHAR
    # slice (`self.content[:100]`) that truncated mid-word with no marker, so
    # the LLM treated a fragment as the whole note. Now a token-based budget
    # with an explicit ellipsis so the model knows the note was elided. ~160
    # tokens ≈ a few sentences — enough to convey the gist in "short" mode
    # without flooding the budget.
    _SHORT_CONTEXT_MAX_TOKENS: ClassVar[int] = 160

    def get_context(
        self, context_size: Literal["short", "long"] = "short"
    ) -> dict[str, Any]:
        if context_size == "long":
            return dict(id=self.id, title=self.title, content=self.content)
        else:
            return dict(
                id=self.id,
                title=self.title,
                content=self._short_content(),
            )

    def _short_content(self) -> Optional[str]:
        """Trim note content to a token budget on a word boundary, appending
        ' […]' when truncated. Returns None for empty content (v0.8.67 A3)."""
        if not self.content:
            return None
        from deeper_notebook.utils.token_utils import token_count

        if token_count(self.content) <= self._SHORT_CONTEXT_MAX_TOKENS:
            return self.content
        # Coarse char budget (~4 chars/token), then shrink to the token cap on
        # whitespace boundaries so we don't cut mid-word.
        approx = self._SHORT_CONTEXT_MAX_TOKENS * 4
        trimmed = self.content[:approx]
        while trimmed and token_count(trimmed) > self._SHORT_CONTEXT_MAX_TOKENS:
            cut = (
                trimmed.rsplit(None, 1)[0] if " " in trimmed.strip() else trimmed[:-50]
            )
            if cut == trimmed:
                trimmed = trimmed[:-50]
            else:
                trimmed = cut
        return trimmed.rstrip() + " […]"


StudioArtifactType = Literal[
    "report",
    "study_guide",
    "course_pack",
    "training_guide",
    "briefing",
    "faq",
    "flashcards",
    "quiz",
    "data_table",
    "mind_map",
    "timeline",
    "infographic",
    "slide_deck",
    "podcast_outline",
    "podcast_audio",
    "research_run",
]

StudioArtifactStatus = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "cancelled",
]

StudioWorkflowRunStatus = Literal[
    "queued",
    "awaiting_approval",
    "running",
    "completed",
    "failed",
    "cancelled",
]


class StudioArtifact(ObjectModel):
    table_name: ClassVar[str] = "studio_artifact"
    nullable_fields: ClassVar[set[str]] = {
        "revision_of_id",
        "generation_claim_owner",
        "generation_claim_started_at",
        "generation_claim_lease_until",
    }

    notebook_id: str
    artifact_type: StudioArtifactType
    title: str
    status: StudioArtifactStatus = "pending"
    source_ids: list[str] = Field(default_factory=list)
    prompt: Optional[str] = None
    model_id: Optional[str] = None
    provider: Optional[str] = None
    output_format: Optional[str] = None
    output_payload: dict[str, Any] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    export_paths: dict[str, str] = Field(default_factory=dict)
    revision_of_id: Optional[str] = None
    generation_claim_owner: Optional[str] = Field(default=None, max_length=128)
    generation_claim_started_at: Optional[datetime] = None
    generation_claim_lease_until: Optional[datetime] = None

    def _prepare_save_data(self) -> dict[str, Any]:
        data = super()._prepare_save_data()
        data["notebook_id"] = ensure_record_id(self.notebook_id)
        data["source_ids"] = [
            ensure_record_id(source_id) for source_id in self.source_ids
        ]
        if self.revision_of_id is not None:
            data["revision_of_id"] = ensure_record_id(self.revision_of_id)
        return data

    @classmethod
    async def get_for_notebook(cls, notebook_id: str) -> list["StudioArtifact"]:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        try:
            rows = await repo_query(
                """
                SELECT * FROM studio_artifact
                WHERE notebook_id = $notebook_id
                AND revision_of_id = NONE
                ORDER BY updated DESC
                """,
                {"notebook_id": ensure_record_id(notebook_id)},
            )
            return [cls(**row) for row in rows] if rows else []
        except Exception as e:
            logger.error(f"Error fetching Studio artifacts for {notebook_id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)

    @classmethod
    async def get_revisions(cls, artifact_id: str) -> list["StudioArtifact"]:
        if not artifact_id:
            raise InvalidInputError("Artifact ID must be provided")
        try:
            rows = await repo_query(
                """
                SELECT * FROM studio_artifact
                WHERE revision_of_id = $artifact_id
                ORDER BY updated DESC
                """,
                {"artifact_id": ensure_record_id(artifact_id)},
            )
            return [cls(**row) for row in rows] if rows else []
        except Exception as e:
            logger.error(
                f"Error fetching Studio artifact revisions for {artifact_id}: {e}"
            )
            logger.exception(e)
            raise DatabaseOperationError(e)

    def _cleanup_export_files(self) -> None:
        """Safely unlink on-disk export files when an artifact is deleted.

        Guards against symlink traversal and directory escapes by requiring
        resolved paths to stay inside the Studio export root or video root.
        Also removes artifact-specific video directories if empty.
        """
        from deeper_notebook.config import DATA_FOLDER
        from deeper_notebook.studio.generation.persistence import _artifact_export_dir

        allowed_roots: list[Path] = []
        export_root: Optional[Path] = None
        try:
            export_root = _artifact_export_dir().resolve()
            allowed_roots.append(export_root)
        except Exception as exc:
            logger.warning(
                f"Failed to resolve export directory for artifact {self.id} cleanup: {exc}"
            )

        video_root: Optional[Path] = None
        try:
            video_root = (Path(DATA_FOLDER) / "video-overviews").resolve()
            allowed_roots.append(video_root)
        except Exception as exc:
            logger.warning(
                f"Failed to resolve video directory for artifact {self.id} cleanup: {exc}"
            )

        for path_str in (self.export_paths or {}).values():
            if not path_str or not isinstance(path_str, str):
                continue
            try:
                candidate = Path(path_str).resolve()
                if candidate.is_file() and any(
                    root in candidate.parents for root in allowed_roots
                ):
                    candidate.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning(
                    f"Failed to clean up export file {path_str} for artifact {self.id}: {exc}"
                )

        if export_root is not None and getattr(self, "id", None):
            try:
                slug = str(self.id).replace(":", "-")
                for item in list(export_root.iterdir()):
                    if item.is_dir() and (
                        item.name == slug or item.name.startswith(f"{slug}-")
                    ):
                        if export_root in item.parents:
                            for child in list(item.iterdir()):
                                try:
                                    if child.is_file():
                                        child.unlink(missing_ok=True)
                                except Exception as c_exc:
                                    logger.warning(
                                        f"Failed to unlink bundle file {child}: {c_exc}"
                                    )
                            try:
                                item.rmdir()
                            except Exception:
                                pass
            except Exception as exc:
                logger.warning(
                    f"Failed to clean up bundle directories for artifact {self.id}: {exc}"
                )

        if video_root is not None and getattr(self, "id", None):
            try:
                slug = str(self.id).replace(":", "-")
                artifact_video_dir = (video_root / slug).resolve()
                if artifact_video_dir.is_dir() and video_root in artifact_video_dir.parents:
                    for child in list(artifact_video_dir.iterdir()):
                        try:
                            if child.is_file():
                                child.unlink(missing_ok=True)
                        except Exception as c_exc:
                            logger.warning(
                                f"Failed to unlink video artifact child file {child}: {c_exc}"
                            )
                    try:
                        artifact_video_dir.rmdir()
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning(
                    f"Failed to clean up video overview directory for artifact {self.id}: {exc}"
                )

    async def delete(self) -> bool:
        """Unlink persisted export files on disk, then remove the database record.

        v0.8.125 — a top-level artifact also deletes its revisions first
        (each revision removes its own export files). Found live: deleting a
        parent left its revision row and export file orphaned.
        """
        if getattr(self, "revision_of_id", None) is None and self.id is not None:
            try:
                revisions = await StudioArtifact.get_revisions(str(self.id))
            except Exception as exc:  # noqa: BLE001 — never block the parent delete
                logger.warning(f"Could not list revisions for {self.id}: {exc}")
                revisions = []
            for revision in revisions:
                try:
                    await revision.delete()
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Failed to delete revision {revision.id} of {self.id}: {exc}")
        self._cleanup_export_files()
        return await super().delete()



class StudioWorkflowRun(ObjectModel):
    table_name: ClassVar[str] = "studio_workflow_run"
    nullable_fields: ClassVar[set[str]] = {"command_id"}

    artifact_id: str
    notebook_id: str
    title: str
    status: StudioWorkflowRunStatus = "queued"
    source_ids: list[str] = Field(default_factory=list)
    approval_required: bool = False
    steps: list[dict[str, Any]] = Field(default_factory=list)
    command_id: Optional[str] = None

    def _prepare_save_data(self) -> dict[str, Any]:
        data = super()._prepare_save_data()
        data["artifact_id"] = ensure_record_id(self.artifact_id)
        data["notebook_id"] = ensure_record_id(self.notebook_id)
        data["source_ids"] = [
            ensure_record_id(source_id) for source_id in self.source_ids
        ]
        if self.command_id is not None:
            data["command_id"] = ensure_record_id(self.command_id)
        return data

    @classmethod
    async def get_for_artifact(cls, artifact_id: str) -> list["StudioWorkflowRun"]:
        if not artifact_id:
            raise InvalidInputError("Artifact ID must be provided")
        try:
            rows = await repo_query(
                """
                SELECT * FROM studio_workflow_run
                WHERE artifact_id = $artifact_id
                ORDER BY updated DESC
                """,
                {"artifact_id": ensure_record_id(artifact_id)},
            )
            return [cls(**row) for row in rows] if rows else []
        except Exception as e:
            logger.error(f"Error fetching Studio workflow runs for {artifact_id}: {e}")
            logger.exception(e)
            raise DatabaseOperationError(e)


class ChatSession(ObjectModel):
    table_name: ClassVar[str] = "chat_session"
    # v0.8.43 — disabled_mcp_servers joins model_override as nullable.
    # None / unset = "all MCP servers visible for this session"
    # (the v0.8.42 default, no regression for pre-migration rows).
    nullable_fields: ClassVar[set[str]] = {"model_override", "disabled_mcp_servers"}
    title: Optional[str] = None
    model_override: Optional[str] = None
    # v0.8.43 — Persistent per-conversation MCP server disable picks.
    # The user's "load only what I need" choices stick across page
    # navigations + browser reloads. Names match `mcp_server.name`
    # case-insensitively in `_resolve_chat_tools`; we DON'T normalize
    # at write-time so the registry's exact casing round-trips.
    disabled_mcp_servers: Optional[list[str]] = None

    async def relate_to_notebook(self, notebook_id: str) -> Any:
        if not notebook_id:
            raise InvalidInputError("Notebook ID must be provided")
        # v0.8.66 (audit D-3) — idempotent relate. A retried session-create
        # (chat.py / source_chat.py) previously RELATE'd a SECOND `refers_to`
        # edge each time (SurrealDB RELATE is not upsert), and `dedup_edges`
        # doesn't sweep refers_to. Mirror the hardened reference/artifact path:
        # return the existing edge if present, else create one.
        if self.id:
            existing = await repo_query(
                "SELECT * FROM refers_to WHERE in = $sid AND out = $nid LIMIT 1",
                {
                    "sid": ensure_record_id(self.id),
                    "nid": ensure_record_id(notebook_id),
                },
            )
            if existing:
                return existing[0]
        return await self.relate("refers_to", notebook_id)

    async def relate_to_source(self, source_id: str) -> Any:
        if not source_id:
            raise InvalidInputError("Source ID must be provided")
        # v0.8.66 (audit D-3) — idempotent (see relate_to_notebook).
        if self.id:
            existing = await repo_query(
                "SELECT * FROM refers_to WHERE in = $sid AND out = $tid LIMIT 1",
                {
                    "sid": ensure_record_id(self.id),
                    "tid": ensure_record_id(source_id),
                },
            )
            if existing:
                return existing[0]
        return await self.relate("refers_to", source_id)

    async def delete(self) -> bool:
        """v0.8.68 — sweep this session's `refers_to` edges before the row
        delete. The base ObjectModel.delete only removes the record, so every
        deleted chat session previously left a dangling graph edge
        (session→notebook or session→source) that accumulated forever — only
        deleting the whole NOTEBOOK swept them (notebook.py Notebook.delete),
        and standalone session deletes (the common path: the session-list
        trash button in both chat UIs) never did. Mirrors the hardened
        cascade pattern on Source (v0.7.86) and Note (v0.7.76): best-effort,
        never blocks the primary delete.
        """
        if self.id is None:
            return False
        try:
            await repo_query(
                "DELETE refers_to WHERE in = $sid",
                {"sid": ensure_record_id(self.id)},
            )
        except Exception as exc:
            logger.warning(
                f"ChatSession.delete: could not sweep refers_to edges for "
                f"{self.id} (non-fatal, edge rows orphaned): {exc}"
            )
        return await super().delete()


# v0.8.67 (audit A1) — default semantic-search relevance floor. Raised from the
# old 0.2 to 0.3 to match the memory layer's own _MIN_SCORE (memory_recall.py),
# whose comment calls 0.0-0.3 "unrelated". 0.2 surfaced near-random sources into
# the LLM context. Env-tunable so operators can dial it without a rebuild and so
# the change is trivially reversible if a corpus needs a looser floor.
_DEFAULT_VECTOR_MIN_SCORE = 0.3


async def _enrich_vault_provenance(
    search_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach portable mounted-note provenance with one bounded lookup."""
    note_ids = [
        result.get("id")
        for result in search_results
        if isinstance(result, dict)
        and isinstance(result.get("id"), str)
        and str(result["id"]).startswith("note:")
    ][:1000]
    if not note_ids:
        return search_results
    rows = await repo_query(
        """
        SELECT id, canonical_external, vault_id, source_hash,
               vault_file_id.embedding_state AS embedding_state,
               vault_file_id.relative_path AS relative_path
        FROM note WHERE id IN $note_ids;
        """,
        {"note_ids": [ensure_record_id(note_id) for note_id in note_ids]},
    )
    provenance = {
        str(row["id"]): row
        for row in rows
        if row.get("canonical_external") is True
        and row.get("vault_id")
        and row.get("relative_path")
    }
    enriched: list[dict[str, Any]] = []
    for result in search_results:
        row = (
            provenance.get(str(result.get("id"))) if isinstance(result, dict) else None
        )
        if row is None:
            enriched.append(result)
            continue
        source_hash = row.get("source_hash")
        enriched.append(
            {
                **result,
                "vault_provenance": {
                    "canonical_external": True,
                    "vault_id": str(row["vault_id"]),
                    "relative_path": row["relative_path"],
                    "source_hash": source_hash
                    if str(source_hash).startswith("sha256:")
                    else f"sha256:{source_hash}",
                },
            }
        )
    return enriched


def _vector_min_score() -> float:
    raw = (resolve_env("DEEPER_NOTEBOOK_VECTOR_MIN_SCORE") or "").strip()
    if not raw:
        return _DEFAULT_VECTOR_MIN_SCORE
    try:
        val = float(raw)
    except ValueError:
        return _DEFAULT_VECTOR_MIN_SCORE
    # Clamp to a sane cosine range; outside [0,1] is always a misconfig.
    return val if 0.0 <= val <= 1.0 else _DEFAULT_VECTOR_MIN_SCORE


async def text_search(
    keyword: str, results: int, source: bool = True, note: bool = True
):
    if not keyword:
        raise InvalidInputError("Search keyword cannot be empty")
    try:
        search_results = await repo_query(
            """
            select *
            from fn::text_search($keyword, $results, $source, $note)
            """,
            {"keyword": keyword, "results": results, "source": source, "note": note},
        )
        return await _enrich_vault_provenance(search_results)
    except Exception as e:
        error_text = str(e).lower()
        if "position overflow" in error_text:
            logger.warning(
                "Text-search highlight overflow for {!r}; falling back to vector search",
                keyword,
            )
            try:
                return await vector_search(keyword, results, source, note)
            except Exception as vector_error:
                logger.error(
                    "Vector-search fallback also failed for {!r}: {}",
                    keyword,
                    vector_error,
                )
                raise DatabaseOperationError(vector_error)
        logger.error(f"Error performing text search: {str(e)}")
        logger.exception(e)
        raise DatabaseOperationError(e)


async def vector_search(
    keyword: str,
    results: int,
    source: bool = True,
    note: bool = True,
    minimum_score: float | None = None,
):
    if not keyword:
        raise InvalidInputError("Search keyword cannot be empty")
    # v0.8.67 (audit A1) — None → env-tunable default (0.3); an explicit caller
    # value (e.g. the /search/ask request) is still honored as-is.
    if minimum_score is None:
        minimum_score = _vector_min_score()
    try:
        from deeper_notebook.utils.embedding import generate_embedding

        # Use unified embedding function (handles chunking if query is very long)
        embed = await generate_embedding(keyword)
        search_results = await repo_query(
            """
            SELECT * FROM fn::vector_search($embed, $results, $source, $note, $minimum_score);
            """,
            {
                "embed": embed,
                "results": results,
                "source": source,
                "note": note,
                "minimum_score": minimum_score,
            },
        )
        return await _enrich_vault_provenance(search_results)
    except Exception as e:
        logger.error(f"Error performing vector search: {str(e)}")
        logger.exception(e)
        raise DatabaseOperationError(e)
