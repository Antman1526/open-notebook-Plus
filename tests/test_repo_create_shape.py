"""v0.8.126 — repo_create must return the created record as a dict even
when the driver returns a one-element list (SurrealDB 2.x insert()).
Found live: capture root approval did `created["id"]` on a list."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from deeper_notebook.database import repository


class _FakeConnection:
    def __init__(self, result):
        self._result = result

    async def insert(self, table, data):
        return self._result


def _patch_connection(monkeypatch, result):
    @asynccontextmanager
    async def _conn():
        yield _FakeConnection(result)

    monkeypatch.setattr(repository, "db_connection", _conn)
    monkeypatch.setattr(repository, "parse_record_ids", lambda x: x)


async def test_repo_create_unwraps_single_element_list(monkeypatch):
    _patch_connection(monkeypatch, [{"id": "capture_inbox_root:abc", "path": "/x"}])
    created = await repository.repo_create("capture_inbox_root", {"path": "/x"})
    assert isinstance(created, dict)
    assert created["id"] == "capture_inbox_root:abc"


async def test_repo_create_passes_dict_through(monkeypatch):
    _patch_connection(monkeypatch, {"id": "t:1"})
    assert (await repository.repo_create("t", {}))["id"] == "t:1"


async def test_repo_create_empty_list_is_an_error(monkeypatch):
    _patch_connection(monkeypatch, [])
    with pytest.raises(RuntimeError):
        await repository.repo_create("t", {})
