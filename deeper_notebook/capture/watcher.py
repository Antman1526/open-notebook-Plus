"""Approved-root, content-addressed local Capture Inbox watcher."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from loguru import logger
from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from deeper_notebook.database.repository import (
    ensure_record_id,
    repo_create,
    repo_query,
)

from .contracts import CaptureInboxItem
from .fingerprints import CaptureFingerprintError, fingerprint_file

DEFAULT_CAPTURE_ROOT = Path.home() / "BrainPulseKnowledge" / "inbox"
SUPPORTED_CAPTURE_SUFFIXES = frozenset(
    {
        ".txt",
        ".md",
        ".markdown",
        ".pdf",
        ".doc",
        ".docx",
        ".ppt",
        ".pptx",
        ".xls",
        ".xlsx",
        ".csv",
        ".tsv",
        ".json",
        ".html",
        ".htm",
        ".mp3",
        ".m4a",
        ".wav",
        ".aac",
        ".flac",
        ".mp4",
        ".mov",
        ".mkv",
        ".webm",
    }
)
_TEMPORARY_SUFFIXES = ("~", ".tmp", ".part", ".crdownload", ".download")


class CaptureRepository(Protocol):
    async def has_fingerprint(self, sha256: str, byte_size: int) -> bool: ...

    async def record_fingerprint(self, sha256: str, byte_size: int) -> None: ...

    async def save_item(self, item: CaptureInboxItem) -> CaptureInboxItem: ...


@dataclass(frozen=True)
class _Observation:
    byte_size: int
    modified_ns: int
    first_seen_at: float


def _resolved_root(root: Path | str) -> Path:
    candidate = Path(os.path.expanduser(str(root))).resolve()
    if not candidate.is_absolute() or not candidate.is_dir():
        raise ValueError("capture root must be an existing absolute directory")
    return candidate


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class SurrealCaptureRepository:
    """Small persistence adapter; its SHA/size table survives watcher restarts."""

    async def has_fingerprint(self, sha256: str, byte_size: int) -> bool:
        rows = await repo_query(
            "SELECT id FROM capture_fingerprint WHERE sha256 = $sha256 "
            "AND byte_size = $byte_size LIMIT 1",
            {"sha256": sha256, "byte_size": byte_size},
        )
        return bool(rows)

    async def record_fingerprint(self, sha256: str, byte_size: int) -> None:
        if await self.has_fingerprint(sha256, byte_size):
            return
        await repo_create(
            "capture_fingerprint", {"sha256": sha256, "byte_size": byte_size}
        )

    async def save_item(self, item: CaptureInboxItem) -> CaptureInboxItem:
        data = item.model_dump(exclude={"id"}, mode="json")
        existing = await repo_query(
            "SELECT id FROM capture_inbox_item WHERE root_path = $root_path "
            "AND relative_path = $relative_path LIMIT 1",
            {"root_path": item.root_path, "relative_path": item.relative_path},
        )
        if existing:
            rows = await repo_query(
                "UPDATE $item MERGE $data RETURN AFTER;",
                {"item": ensure_record_id(str(existing[0]["id"])), "data": data},
            )
            if rows:
                return self._as_item(rows[0])
        created = await repo_create("capture_inbox_item", data)
        return self._as_item(created)

    async def approve_root(self, root: Path) -> str:
        root_path = str(root)
        existing = await repo_query(
            "SELECT id FROM capture_inbox_root WHERE path = $path LIMIT 1",
            {"path": root_path},
        )
        if existing:
            return str(existing[0]["id"])
        created = await repo_create("capture_inbox_root", {"path": root_path})
        return str(created["id"])

    async def list_roots(self) -> list[str]:
        rows = await repo_query(
            # v0.8.126 — found live: SurrealDB 2.x rejects ORDER BY on a field
            # that is not in the projection ("Missing order idiom `created`"),
            # which 500'd GET /capture/roots on every visit to the Capture page.
            "SELECT path, created FROM capture_inbox_root ORDER BY created ASC"
        )
        return [str(row["path"]) for row in rows if row.get("path")]

    async def list_items(self, *, limit: int = 200) -> list[CaptureInboxItem]:
        rows = await repo_query(
            "SELECT * FROM capture_inbox_item ORDER BY updated DESC LIMIT $limit",
            {"limit": min(max(limit, 1), 500)},
        )
        return [self._as_item(row) for row in rows]

    @staticmethod
    def _as_item(record: object) -> CaptureInboxItem:
        if not isinstance(record, dict):
            raise ValueError("Capture persistence returned an invalid item")
        fields = CaptureInboxItem.model_fields
        return CaptureInboxItem.model_validate(
            {field: record[field] for field in fields if field in record}
        )


class CaptureInboxWatcher:
    """Scan only explicit roots and accept files after two stable observations."""

    def __init__(
        self,
        *,
        approved_roots: Iterable[Path | str],
        repository: CaptureRepository,
        stable_after_seconds: float = 2.0,
    ) -> None:
        if stable_after_seconds < 2.0:
            raise ValueError("capture stability window must be at least two seconds")
        self._roots = {_resolved_root(root) for root in approved_roots}
        self._repository = repository
        self._stable_after_seconds = stable_after_seconds
        self._observations: dict[tuple[str, str], _Observation] = {}

    @property
    def approved_roots(self) -> tuple[Path, ...]:
        return tuple(sorted(self._roots))

    async def scan_root(
        self, root: Path | str, *, now_monotonic: float | None = None
    ) -> list[CaptureInboxItem]:
        root_path = _resolved_root(root)
        if root_path not in self._roots:
            raise ValueError("path is not an approved capture root")
        now = time.monotonic() if now_monotonic is None else now_monotonic
        results: list[CaptureInboxItem] = []
        try:
            candidates = sorted(root_path.rglob("*"), key=lambda path: str(path))
        except OSError as exc:
            raise ValueError("capture root could not be listed") from exc
        for candidate in candidates:
            if candidate.is_dir() and not candidate.is_symlink():
                continue
            result = await self._inspect(root_path, candidate, now)
            if result is not None:
                results.append(result)
        return results

    async def _inspect(
        self, root: Path, candidate: Path, now: float
    ) -> CaptureInboxItem | None:
        try:
            relative = candidate.relative_to(root).as_posix()
        except ValueError:
            return None
        extension = candidate.suffix.lower() or ".unknown"
        base = self._item_base(root, relative, candidate.name, extension)

        if any(
            part.startswith(".") for part in Path(relative).parts
        ) or candidate.name.lower().endswith(_TEMPORARY_SUFFIXES):
            return await self._save(base, "ignored", reason="hidden_or_temporary")
        if candidate.is_symlink():
            try:
                resolved = candidate.resolve()
            except OSError:
                resolved = candidate
            reason = (
                "symlink_escape"
                if not _is_within(resolved, root)
                else "symlink_not_supported"
            )
            return await self._save(base, "ignored", reason=reason)
        if extension not in SUPPORTED_CAPTURE_SUFFIXES:
            return await self._save(base, "ignored", reason="unsupported_type")
        try:
            stat = candidate.stat()
        except OSError:
            return await self._save(base, "failed", reason="unreadable")
        if not candidate.is_file():
            return await self._save(base, "ignored", reason="not_regular_file")

        key = (str(root), relative)
        current = _Observation(stat.st_size, stat.st_mtime_ns, now)
        observed = self._observations.get(key)
        if observed is None or (observed.byte_size, observed.modified_ns) != (
            current.byte_size,
            current.modified_ns,
        ):
            self._observations[key] = current
            return await self._save(
                base, "pending", byte_size=stat.st_size, modified_ns=stat.st_mtime_ns
            )
        if now - observed.first_seen_at < self._stable_after_seconds:
            return await self._save(
                base, "pending", byte_size=stat.st_size, modified_ns=stat.st_mtime_ns
            )
        try:
            fingerprint = fingerprint_file(candidate)
        except CaptureFingerprintError:
            self._observations.pop(key, None)
            return await self._save(base, "failed", reason="changed_or_unreadable")
        duplicate = await self._repository.has_fingerprint(
            fingerprint.sha256, fingerprint.byte_size
        )
        if not duplicate:
            await self._repository.record_fingerprint(
                fingerprint.sha256, fingerprint.byte_size
            )
        return await self._save(
            base,
            "duplicate" if duplicate else "ready",
            sha256=fingerprint.sha256,
            byte_size=fingerprint.byte_size,
            modified_ns=stat.st_mtime_ns,
        )

    def _item_base(
        self, root: Path, relative: str, filename: str, extension: str
    ) -> dict[str, object]:
        return {
            "root_path": str(root),
            "relative_path": relative,
            "filename": filename,
            "extension": extension,
        }

    async def _save(
        self,
        base: dict[str, object],
        state: str,
        *,
        sha256: str | None = None,
        byte_size: int | None = None,
        modified_ns: int | None = None,
        reason: str | None = None,
    ) -> CaptureInboxItem:
        item = CaptureInboxItem(
            **base,
            state=state,  # type: ignore[arg-type]
            sha256=sha256,
            byte_size=byte_size,
            modified_ns=modified_ns,
            reason=reason,
        )
        return await self._repository.save_item(item)


class _RootEventHandler(FileSystemEventHandler):
    def __init__(self, root: Path, dirty_roots: set[Path]) -> None:
        self._root = root
        self._dirty_roots = dirty_roots

    def on_any_event(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._dirty_roots.add(self._root)


class WatchdogCaptureService:
    """Event trigger only; stability and safety checks stay in the scanner."""

    def __init__(self, watcher: CaptureInboxWatcher) -> None:
        self._watcher = watcher
        self._dirty_roots: set[Path] = set(watcher.approved_roots)
        self._observer: Observer | None = None

    def start(self) -> None:
        if self._observer is not None:
            return
        observer = Observer()
        for root in self._watcher.approved_roots:
            observer.schedule(
                _RootEventHandler(root, self._dirty_roots), str(root), recursive=True
            )
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        if self._observer is None:
            return
        self._observer.stop()
        self._observer.join(timeout=2)
        self._observer = None

    async def scan_dirty_roots(self) -> list[CaptureInboxItem]:
        roots = tuple(self._dirty_roots)
        self._dirty_roots.clear()
        items: list[CaptureInboxItem] = []
        for root in roots:
            try:
                items.extend(await self._watcher.scan_root(root))
            except ValueError:
                logger.warning("Capture root became unavailable: {}", root)
        return items

    async def poll_forever(self, interval_seconds: float = 2.0) -> None:
        while True:
            await self.scan_dirty_roots()
            await asyncio.sleep(max(interval_seconds, 2.0))
