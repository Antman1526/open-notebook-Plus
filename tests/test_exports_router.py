"""v0.7.90 — tests for api/routers/exports.py.

Exercises the notebook-to-folder, notebook-to-zip, and single-note export
endpoints with stubbed domain objects (no real SurrealDB needed). Tests
real on-disk writes via tmp_path so any filesystem-layer regressions
(permissions, path normalization, zip layout) are caught.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import exports as exports_mod
from api.routers import filesystem as fs_mod


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.include_router(exports_mod.router, prefix="/api")
    app.include_router(fs_mod.router, prefix="/api")  # /fs/mkdir for parent dirs
    return TestClient(app)


# ----------------------------------------------------------------------------
# Stubs for the domain layer
# ----------------------------------------------------------------------------


class _FakeNote:
    def __init__(
        self,
        id: str,
        title: str,
        content: str,
        note_type: str = "ai",
        created: str = "2026-05-01T00:00:00Z",
        updated: str = "2026-05-01T00:00:00Z",
    ) -> None:
        self.id, self.title, self.content = id, title, content
        self.note_type = note_type
        self.created, self.updated = created, updated


class _FakeSource:
    def __init__(
        self,
        id: str,
        title: str,
        full_text: str,
        file_path: Optional[str] = None,
        url: Optional[str] = None,
    ) -> None:
        self.id, self.title, self.full_text = id, title, full_text
        self.asset = SimpleNamespace(file_path=file_path, url=url)


class _FakeNotebook:
    def __init__(
        self,
        id: str,
        name: str,
        notes: list[_FakeNote],
        sources: Optional[list[_FakeSource]] = None,
        description: str = "test notebook",
    ) -> None:
        self.id, self.name, self.description = id, name, description
        self.created = "2026-05-01T00:00:00Z"
        self.updated = "2026-05-01T00:00:00Z"
        self._notes, self._sources = notes, sources or []

    async def get_notes(self) -> list[_FakeNote]:
        return self._notes

    async def get_sources(self) -> list[_FakeSource]:
        return self._sources


@pytest.fixture()
def patched_domain(monkeypatch: pytest.MonkeyPatch):
    """Stub the Notebook/Note domain getters so tests don't need a database."""
    notebooks_by_id: dict[str, _FakeNotebook] = {}
    notes_by_id: dict[str, _FakeNote] = {}

    async def _get_notebook(notebook_id: str) -> Optional[_FakeNotebook]:
        return notebooks_by_id.get(notebook_id)

    async def _get_note(note_id: str) -> Optional[_FakeNote]:
        return notes_by_id.get(note_id)

    monkeypatch.setattr(exports_mod.Notebook, "get", staticmethod(_get_notebook))
    monkeypatch.setattr(exports_mod.Note, "get", staticmethod(_get_note))
    return {"notebooks": notebooks_by_id, "notes": notes_by_id}


# ----------------------------------------------------------------------------
# Slugify + helper unit tests
# ----------------------------------------------------------------------------


def test_slugify_strips_emoji_and_special_chars() -> None:
    # v0.7.90 — slugifier strips leading numeric prefixes so v0.7.89 page
    # titles ("📄 01 · Architecture") don't get double-prefixed downstream.
    assert exports_mod._slugify("📋 00 · Overview!") == "overview"
    assert exports_mod._slugify("📄 01 · Architecture") == "architecture"
    assert exports_mod._slugify("Hello / World — v2") == "hello-world-v2"
    assert exports_mod._slugify("") == "untitled"
    # Caps slug to 80 chars
    long_title = "a" * 200
    assert len(exports_mod._slugify(long_title)) == 80
    # Multi-segment numeric titles strip prefix-by-prefix; the final
    # bare-number segment survives so the filename is at least
    # distinguishable rather than collapsing to "untitled".
    assert exports_mod._slugify("01 02 03") == "03"
    # Pure-symbol titles (no surviving characters) fall back to "untitled"
    assert exports_mod._slugify("---") == "untitled"
    assert exports_mod._slugify("📋📋📋") == "untitled"


def test_build_overview_path_detects_v0_7_89_notes() -> None:
    n = _FakeNote("note:1", "📋 00 · My Project — Overview", "body")
    assert exports_mod._build_overview_path(n) == "00-overview.md"  # type: ignore[arg-type]
    n2 = _FakeNote("note:2", "📄 01 · Backend Internals", "body")
    assert exports_mod._build_overview_path(n2) is None  # type: ignore[arg-type]
    # Any note with "Overview" in the title is also caught
    n3 = _FakeNote("note:3", "Project Overview", "body")
    assert exports_mod._build_overview_path(n3) == "00-overview.md"  # type: ignore[arg-type]


def test_plan_filenames_overview_wins_zero_then_pages_indexed() -> None:
    notes: list = [
        _FakeNote("note:p1", "📄 01 · Architecture", "body"),
        _FakeNote("note:ov", "📋 00 · Title — Overview", "body"),
        _FakeNote("note:p2", "📄 02 · Backend", "body"),
    ]
    plan = exports_mod._plan_filenames(notes)
    filenames = [p[0] for p in plan]
    # Overview first; then pages indexed 01, 02 in their original order
    assert filenames[0] == "00-overview.md"
    assert filenames[1].startswith("01-")
    assert filenames[2].startswith("02-")


# ----------------------------------------------------------------------------
# /notebooks/{id}/export — folder format
# ----------------------------------------------------------------------------


def test_export_notebook_folder_writes_overview_and_pages(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [
        _FakeNote("note:ov", "📋 00 · Demo — Overview", "Overview body."),
        _FakeNote("note:p1", "📄 01 · Architecture", "Arch body."),
        _FakeNote("note:p2", "📄 02 · Backend", "Backend body."),
    ]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "demo-export"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "folder"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "folder"
    # 3 notes + 1 manifest = 4 files
    assert body["file_count"] == 4
    assert target.exists() and target.is_dir()
    assert (target / "00-overview.md").exists()
    # The 01-architecture / 02-backend files exist with slugified names
    assert (target / "01-architecture.md").exists()
    assert (target / "02-backend.md").exists()
    # Manifest is valid JSON and references all notes
    manifest = json.loads((target / "manifest.json").read_text())
    assert manifest["notebook"]["name"] == "Demo"
    assert len(manifest["notes"]) == 3
    # Each note file starts with the frontmatter we wrote
    content = (target / "00-overview.md").read_text()
    assert content.startswith("---\n")
    assert "title: 📋 00 · Demo — Overview" in content
    assert "Overview body." in content


def test_export_notebook_folder_404_when_notebook_missing(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    r = client.post(
        "/api/notebooks/notebook:nope/export",
        json={"destination": str(tmp_path / "x"), "format": "folder"},
    )
    assert r.status_code == 404


def test_export_notebook_folder_400_when_empty_and_no_sources(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    nb = _FakeNotebook("notebook:empty", "Empty", notes=[])
    patched_domain["notebooks"]["notebook:empty"] = nb
    r = client.post(
        "/api/notebooks/notebook:empty/export",
        json={"destination": str(tmp_path / "x"), "format": "folder"},
    )
    assert r.status_code == 400
    assert "no notes to export" in r.json()["detail"]


def test_export_notebook_folder_409_on_existing_file_without_overwrite(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    """v0.7.90 — the pre-flight overwrite check must catch existing files
    BEFORE writing anything else, so a re-run doesn't half-clobber an
    earlier export."""
    notes = [_FakeNote("note:1", "Some Note", "body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "demo"
    target.mkdir()
    (target / "01-some-note.md").write_text("stale content")
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "folder", "overwrite": False},
    )
    assert r.status_code == 409
    # The stale file MUST be untouched
    assert (target / "01-some-note.md").read_text() == "stale content"


def test_export_notebook_folder_overwrite_replaces_files(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [_FakeNote("note:1", "Some Note", "fresh body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "demo"
    target.mkdir()
    (target / "01-some-note.md").write_text("stale")
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "folder", "overwrite": True},
    )
    assert r.status_code == 200, r.text
    content = (target / "01-some-note.md").read_text()
    assert "fresh body" in content
    assert "stale" not in content


def test_export_notebook_folder_with_sources(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [_FakeNote("note:1", "📋 00 · Overview", "body")]
    sources = [
        _FakeSource("source:1", "doc.pdf", "Extracted text.", file_path="/tmp/doc.pdf"),
        _FakeSource("source:2", "page.html", "Web text.", url="https://example.com"),
    ]
    nb = _FakeNotebook("notebook:1", "Demo", notes, sources=sources)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "demo-with-sources"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={
            "destination": str(target),
            "format": "folder",
            "include_sources": True,
        },
    )
    assert r.status_code == 200, r.text
    # sources/ subfolder created with one file per source
    sources_dir = target / "sources"
    assert sources_dir.exists() and sources_dir.is_dir()
    source_files = sorted(p.name for p in sources_dir.iterdir())
    assert len(source_files) == 2
    # Each source file embeds its full_text
    s1_content = (sources_dir / "1.md").read_text()
    assert "Extracted text." in s1_content
    assert "original_file: /tmp/doc.pdf" in s1_content


# ----------------------------------------------------------------------------
# /notebooks/{id}/export — zip format
# ----------------------------------------------------------------------------


def test_export_notebook_zip_creates_archive(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [
        _FakeNote("note:ov", "📋 00 · Demo — Overview", "Overview body."),
        _FakeNote("note:p1", "📄 01 · Architecture", "Arch body."),
    ]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "demo.zip"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "zip"},
    )
    assert r.status_code == 200, r.text
    assert target.exists()
    with zipfile.ZipFile(target) as zf:
        names = sorted(zf.namelist())
        assert "00-overview.md" in names
        assert "01-architecture.md" in names
        assert "manifest.json" in names


def test_export_notebook_zip_missing_parent_dir_400(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [_FakeNote("note:1", "T", "body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "missing-parent" / "demo.zip"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "zip"},
    )
    assert r.status_code == 400
    assert "Parent directory does not exist" in r.json()["detail"]


# ----------------------------------------------------------------------------
# /notes/{id}/export
# ----------------------------------------------------------------------------


def test_export_note_writes_markdown_file(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    note = _FakeNote("note:1", "Single Note", "Body content.")
    patched_domain["notes"]["note:1"] = note
    target = tmp_path / "single.md"
    r = client.post(
        "/api/notes/note:1/export",
        json={"destination": str(target)},
    )
    assert r.status_code == 200, r.text
    assert target.exists()
    content = target.read_text()
    assert "title: Single Note" in content
    assert "Body content." in content


def test_export_note_404_when_missing(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    r = client.post(
        "/api/notes/note:nope/export",
        json={"destination": str(tmp_path / "x.md")},
    )
    assert r.status_code == 404


def test_export_note_forces_md_extension(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    note = _FakeNote("note:1", "Note", "body")
    patched_domain["notes"]["note:1"] = note
    target_without_ext = tmp_path / "untyped"
    r = client.post(
        "/api/notes/note:1/export",
        json={"destination": str(target_without_ext)},
    )
    assert r.status_code == 200, r.text
    # Should end up with .md appended
    assert (tmp_path / "untyped.md").exists()
    assert not target_without_ext.exists()


def test_export_note_refuses_directory_destination(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    note = _FakeNote("note:1", "Note", "body")
    patched_domain["notes"]["note:1"] = note
    r = client.post(
        "/api/notes/note:1/export",
        json={"destination": str(tmp_path)},
    )
    assert r.status_code == 400
    assert "directory" in r.json()["detail"].lower()


def test_export_note_409_when_target_exists_without_overwrite(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    note = _FakeNote("note:1", "Note", "fresh")
    patched_domain["notes"]["note:1"] = note
    target = tmp_path / "note.md"
    target.write_text("stale")
    r = client.post(
        "/api/notes/note:1/export",
        json={"destination": str(target), "overwrite": False},
    )
    assert r.status_code == 409
    assert target.read_text() == "stale"


def test_export_note_overwrite_replaces_file(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    note = _FakeNote("note:1", "Note", "fresh body content")
    patched_domain["notes"]["note:1"] = note
    target = tmp_path / "note.md"
    target.write_text("stale")
    r = client.post(
        "/api/notes/note:1/export",
        json={"destination": str(target), "overwrite": True},
    )
    assert r.status_code == 200, r.text
    assert "fresh body content" in target.read_text()


def test_export_refuses_system_destination(
    client: TestClient,
    patched_domain,
) -> None:
    note = _FakeNote("note:1", "Note", "body")
    patched_domain["notes"]["note:1"] = note
    r = client.post(
        "/api/notes/note:1/export",
        json={"destination": "/etc/onp-note.md"},
    )
    assert r.status_code == 403


# ============================================================================
# v0.7.94 — Notebook import (reverse of v0.7.90 export)
# ============================================================================


def test_import_creates_new_notebook_from_folder(
    client: TestClient,
    patched_domain,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.94 happy path: a folder containing .md files (no manifest) is
    imported into a fresh notebook. Notes save in alphabetical order so
    '00-overview' sorts first."""
    # Set up a tiny export-shaped folder
    folder = tmp_path / "to-import"
    folder.mkdir()
    (folder / "00-overview.md").write_text(
        "---\ntitle: Overview\ntype: ai\n---\nOverview body."
    )
    (folder / "01-arch.md").write_text(
        "---\ntitle: Architecture\ntype: ai\n---\nArch body."
    )

    # Stub the domain layer — track save() calls
    saved_notebooks: list = []
    saved_notes: list = []
    notebook_id = "notebook:imported-1"

    class _NotebookStub:
        def __init__(self, *, name, description=None):
            self.name, self.description = name, description
            self.id = notebook_id

        async def save(self):
            saved_notebooks.append(self)

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.title, self.content, self.note_type = title, content, note_type
            self.id = f"note:imp-{len(saved_notes)}"

        async def save(self):
            saved_notes.append(self)

        async def add_to_notebook(self, _id):
            pass

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _NotebookStub)
    monkeypatch.setattr(exports_mod, "Note", _NoteStub)

    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(folder), "mode": "new"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "new"
    assert body["notebook_id"] == notebook_id
    assert len(body["note_ids"]) == 2
    assert body["file_count"] == 2
    # First note is the Overview — frontmatter title respected
    assert saved_notes[0].title == "Overview"
    assert "Overview body." in saved_notes[0].content


def test_import_zip_archive(
    client: TestClient,
    patched_domain,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.94 — .zip imports work the same as folder imports."""
    zip_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "00-overview.md",
            "---\ntitle: Z Overview\n---\nzip overview body",
        )
        zf.writestr(
            "01-page.md",
            "---\ntitle: Z Page\n---\nzip page body",
        )
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "notebook": {"name": "From Manifest", "description": "from-zip"},
                }
            ),
        )

    saved_notebooks: list = []
    saved_notes: list = []

    class _NotebookStub:
        def __init__(self, *, name, description=None):
            self.name, self.description = name, description
            self.id = "notebook:zip-1"

        async def save(self):
            saved_notebooks.append(self)

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.title, self.content, self.note_type = title, content, note_type
            self.id = f"note:zimp-{len(saved_notes)}"

        async def save(self):
            saved_notes.append(self)

        async def add_to_notebook(self, _id):
            pass

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _NotebookStub)
    monkeypatch.setattr(exports_mod, "Note", _NoteStub)

    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(zip_path), "mode": "new"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Manifest's notebook.name wins over src.stem
    assert saved_notebooks[0].name == "From Manifest"
    assert body["file_count"] == 2  # manifest doesn't count


def test_import_into_existing_notebook(
    client: TestClient,
    patched_domain,
    monkeypatch,
    tmp_path: Path,
):
    """mode='into_existing' appends notes to an existing notebook —
    Notebook.get returns the target rather than constructing a new one."""
    folder = tmp_path / "to-add"
    folder.mkdir()
    (folder / "new-note.md").write_text("---\ntitle: Appended\n---\nbody")

    existing = SimpleNamespace(
        id="notebook:existing-1",
        name="Existing NB",
    )

    saved_notes: list = []

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.title, self.content = title, content
            self.note_type = note_type
            self.id = f"note:add-{len(saved_notes)}"

        async def save(self):
            saved_notes.append(self)

        async def add_to_notebook(self, _id):
            pass

    async def _get_notebook(_id):
        return existing

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Note", _NoteStub)
    monkeypatch.setattr(exports_mod.Notebook, "get", staticmethod(_get_notebook))

    r = client.post(
        "/api/notebooks/import",
        json={
            "source_path": str(folder),
            "mode": "into_existing",
            "target_notebook_id": "notebook:existing-1",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "into_existing"
    assert body["notebook_id"] == "notebook:existing-1"
    assert body["notebook_name"] == "Existing NB"
    assert len(body["note_ids"]) == 1
    assert saved_notes[0].title == "Appended"


def test_import_into_existing_404_when_target_missing(
    client: TestClient,
    patched_domain,
    monkeypatch,
    tmp_path: Path,
):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "n.md").write_text("---\ntitle: x\n---\nbody")

    async def _get_none(_id):
        return None

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod.Notebook, "get", staticmethod(_get_none))

    r = client.post(
        "/api/notebooks/import",
        json={
            "source_path": str(folder),
            "mode": "into_existing",
            "target_notebook_id": "notebook:nope",
        },
    )
    assert r.status_code == 404


def test_import_400_when_into_existing_missing_target_id(
    client: TestClient,
    tmp_path: Path,
):
    folder = tmp_path / "x"
    folder.mkdir()
    (folder / "n.md").write_text("---\ntitle: x\n---\nbody")
    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(folder), "mode": "into_existing"},
    )
    assert r.status_code == 400
    assert "target_notebook_id" in r.json()["detail"]


def test_import_400_when_no_md_files_found(
    client: TestClient,
    tmp_path: Path,
):
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "not-markdown.txt").write_text("nope")
    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(folder), "mode": "new"},
    )
    assert r.status_code == 400
    assert "No .md files" in r.json()["detail"]


def test_import_single_md_file_becomes_one_note_notebook(
    client: TestClient,
    patched_domain,
    monkeypatch,
    tmp_path: Path,
):
    """A single .md file (not a folder, not a zip) becomes a one-note
    notebook — useful for casual import of any markdown file."""
    md = tmp_path / "lonely.md"
    md.write_text("---\ntitle: Lone Note\n---\nbody")

    notebook_id = "notebook:single-1"
    saved_notes: list = []

    class _NotebookStub:
        def __init__(self, *, name, description=None):
            self.name, self.description = name, description
            self.id = notebook_id

        async def save(self):
            pass

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.title, self.content = title, content
            self.note_type = note_type
            self.id = f"note:lonely-{len(saved_notes)}"

        async def save(self):
            saved_notes.append(self)

        async def add_to_notebook(self, _id):
            pass

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _NotebookStub)
    monkeypatch.setattr(exports_mod, "Note", _NoteStub)

    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(md), "mode": "new"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["file_count"] == 1
    assert saved_notes[0].title == "Lone Note"


def test_import_zip_rejects_traversal_member(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.94 security: a zip with a '../escape.md' member must be
    rejected, not silently extracted to the parent directory."""
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../escape.md", "evil content")

    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(zip_path), "mode": "new"},
    )
    assert r.status_code == 400
    assert "Unsafe zip entry" in r.json()["detail"]


def test_import_rejects_oversized_file(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.94 — per-file cap (5 MB by default) prevents a single bloated
    .md file from consuming all the API's memory during import."""
    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "_MAX_IMPORT_FILE_BYTES", 100)
    md = tmp_path / "big.md"
    md.write_text("---\ntitle: x\n---\n" + ("a" * 500))
    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(md), "mode": "new"},
    )
    assert r.status_code == 413
    assert "per-file cap" in r.json()["detail"]


def test_import_round_trip_export_then_import_preserves_titles(
    client: TestClient,
    patched_domain,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.94 — export → import round-trip preserves note titles.
    Validates that _render_note_content's frontmatter matches what
    _parse_frontmatter reads back."""
    # Stage 1: export a notebook
    notes = [
        _FakeNote("note:1", "📋 00 · Demo — Overview", "Overview body."),
        _FakeNote("note:2", "📄 01 · Section One", "Body one."),
    ]
    nb = _FakeNotebook("notebook:export-1", "Demo", notes)
    patched_domain["notebooks"]["notebook:export-1"] = nb

    export_dir = tmp_path / "roundtrip"
    r = client.post(
        "/api/notebooks/notebook:export-1/export",
        json={"destination": str(export_dir), "format": "folder"},
    )
    assert r.status_code == 200, r.text

    # Stage 2: re-import the exported folder into a new notebook
    saved_notes: list = []
    new_notebook_id = "notebook:roundtrip-1"

    class _NotebookStub:
        def __init__(self, *, name, description=None):
            self.name, self.description = name, description
            self.id = new_notebook_id

        async def save(self):
            pass

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.title, self.content = title, content
            self.note_type = note_type
            self.id = f"note:rt-{len(saved_notes)}"

        async def save(self):
            saved_notes.append(self)

        async def add_to_notebook(self, _id):
            pass

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _NotebookStub)
    monkeypatch.setattr(exports_mod, "Note", _NoteStub)

    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(export_dir), "mode": "new"},
    )
    assert r.status_code == 200, r.text
    # Titles survive the round-trip
    titles = [n.title for n in saved_notes]
    assert "📋 00 · Demo — Overview" in titles
    assert "📄 01 · Section One" in titles


# ============================================================================
# v0.7.96 — Import preview (dry-run)
# ============================================================================


def test_import_preview_folder_returns_plan_without_touching_db(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    """Preview must NOT call Notebook.save / Note.save. We assert that
    by setting them to a raising-stub — if the endpoint accidentally
    invokes them, the test fails."""
    folder = tmp_path / "preview-folder"
    folder.mkdir()
    (folder / "00-overview.md").write_text("---\ntitle: My Overview\n---\nbody")
    (folder / "01-arch.md").write_text("---\ntitle: Architecture\n---\nbody")
    (folder / "manifest.json").write_text(
        json.dumps({"notebook": {"name": "Hint Name", "description": "Hint Desc"}})
    )

    # If the preview accidentally calls these, the test fails loudly.
    class _RaisingDomain:
        def __init__(self, *_a, **_kw):
            raise AssertionError("preview must NOT instantiate domain models")

        async def save(self):
            raise AssertionError("preview must NOT save")

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _RaisingDomain)
    monkeypatch.setattr(exports_mod, "Note", _RaisingDomain)
    monkeypatch.setattr(exports_mod, "Source", _RaisingDomain)

    r = client.post(
        "/api/notebooks/import/preview",
        json={"source_path": str(folder)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected_kind"] == "folder"
    assert body["notebook_name_hint"] == "Hint Name"
    assert body["description_hint"] == "Hint Desc"
    assert body["has_manifest"] is True
    titles = [n["title"] for n in body["notes"]]
    assert "My Overview" in titles
    assert "Architecture" in titles
    # Overview note flagged so the UI can render it specially
    overview_items = [n for n in body["notes"] if n["is_overview"]]
    assert len(overview_items) == 1
    assert overview_items[0]["title"] == "My Overview"


def test_import_preview_zip_detects_kind(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    zip_path = tmp_path / "p.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.md", "---\ntitle: A\n---\nbody")

    r = client.post(
        "/api/notebooks/import/preview",
        json={"source_path": str(zip_path)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected_kind"] == "zip"
    assert body["has_manifest"] is False
    assert len(body["notes"]) == 1


def test_import_preview_single_md(client: TestClient, tmp_path: Path):
    md = tmp_path / "single.md"
    md.write_text("---\ntitle: Single\n---\nbody")
    r = client.post(
        "/api/notebooks/import/preview",
        json={"source_path": str(md)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["detected_kind"] == "single_md"
    assert body["notes"][0]["title"] == "Single"


def test_import_preview_warns_on_empty_bundle(
    client: TestClient,
    tmp_path: Path,
):
    folder = tmp_path / "empty"
    folder.mkdir()
    (folder / "ignored.txt").write_text("not markdown")
    # No .md files → preview still returns 200 but warns
    r = client.post(
        "/api/notebooks/import/preview",
        json={"source_path": str(folder)},
    )
    # An empty folder has no md/json → _read_import_entries returns [] →
    # preview reports no notes/sources, with a helpful warning.
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notes"] == []
    assert body["sources"] == []
    assert any("No notes" in w for w in body["warnings"])


def test_import_preview_404_when_path_missing(
    client: TestClient,
    tmp_path: Path,
):
    r = client.post(
        "/api/notebooks/import/preview",
        json={"source_path": str(tmp_path / "does-not-exist")},
    )
    assert r.status_code == 404


# ============================================================================
# v0.7.97 — HTML export
# ============================================================================


def test_export_notebook_html_folder_writes_html_files(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    notes = [
        _FakeNote("note:1", "📋 00 · Demo — Overview", "# Heading\n\n**bold** body"),
        _FakeNote("note:2", "📄 01 · Section", "| a | b |\n|---|---|\n| 1 | 2 |"),
    ]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "html-export"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "html_folder"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "html_folder"
    # .html extensions on each note file
    assert (target / "00-overview.html").exists()
    assert (target / "01-section.html").exists()
    # The HTML wrapper includes our minimal stylesheet
    overview_html = (target / "00-overview.html").read_text()
    assert "<!doctype html>" in overview_html.lower()
    assert "<style>" in overview_html
    assert "<h1>Heading</h1>" in overview_html
    assert "<strong>bold</strong>" in overview_html
    # The frontmatter block survives as a metadata div
    assert "onp-frontmatter" in overview_html
    assert "📋 00 · Demo — Overview" in overview_html  # title escaped


def test_export_notebook_html_zip(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    notes = [_FakeNote("note:1", "📄 01 · Section", "**body**")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "out.zip"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "html_zip"},
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(target) as zf:
        names = sorted(zf.namelist())
        # HTML extensions only — no .md leftover
        assert any(n.endswith(".html") for n in names)
        assert not any(n.endswith(".md") for n in names)
        # manifest.json always preserves its name
        assert "manifest.json" in names
        # Body of one html file confirms rendering
        html_member = next(n for n in names if n.endswith(".html"))
        content = zf.read(html_member).decode()
        assert "<strong>body</strong>" in content


def test_html_export_escapes_attribute_positions(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    """v0.7.97 — Note titles can contain <, >, ", which would break the
    <title> tag if not escaped. Verify _html_escape is applied."""
    notes = [_FakeNote("note:1", 'Has <script>"x"</script> title', "body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "esc"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "html_folder"},
    )
    assert r.status_code == 200, r.text
    html = next(target.glob("*.html")).read_text()
    # The literal <script> must NOT appear inside <title>
    assert "<title>Has &lt;script&gt;" in html
    assert "<script>" not in html.split("</head>")[0]  # no raw script in head


# ============================================================================
# v0.7.98 — Zip compression options
# ============================================================================


def test_export_zip_default_compression_is_deflated(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    notes = [_FakeNote("note:1", "T", "body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "default.zip"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "zip"},
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(target) as zf:
        info = zf.infolist()[0]
        assert info.compress_type == zipfile.ZIP_DEFLATED


def test_export_zip_with_stored_compression(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    """v0.7.98 — compression='stored' must produce an uncompressed zip
    (compress_size == file_size for stored entries)."""
    notes = [_FakeNote("note:1", "T", "a" * 1000)]  # compressible
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "stored.zip"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={
            "destination": str(target),
            "format": "zip",
            "compression": "stored",
        },
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(target) as zf:
        infos = zf.infolist()
        # All entries are stored, not deflated
        for info in infos:
            assert info.compress_type == zipfile.ZIP_STORED
            # Stored entries don't shrink the data
            assert info.compress_size == info.file_size


def test_export_zip_with_bzip2_compression(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    notes = [_FakeNote("note:1", "T", "x" * 5000)]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    target = tmp_path / "bz2.zip"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={
            "destination": str(target),
            "format": "zip",
            "compression": "bzip2",
        },
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(target) as zf:
        info = zf.infolist()[0]
        assert info.compress_type == zipfile.ZIP_BZIP2


def test_export_zip_rejects_invalid_compression_via_pydantic(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    """Pydantic Literal validation catches typos before the request
    reaches our handler — verifies the request schema actually constrains
    the field."""
    r = client.post(
        "/api/notebooks/notebook:nope/export",
        json={
            "destination": str(tmp_path / "x.zip"),
            "format": "zip",
            "compression": "nonsense",
        },
    )
    assert r.status_code == 422  # Pydantic validation failure


# ============================================================================
# v0.7.104 — Import calls source.vectorize() (regression for the v0.7.94 bug)
# ============================================================================


def test_import_vectorizes_imported_sources(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.104 regression: imported Source records must have vectorize()
    called after save() so they get embeddings and become searchable
    via vector_search. Before this fix, sources from imports were
    text-only — visible in the UI but invisible to vector search,
    breaking 'import then chat-with-sources'."""
    folder = tmp_path / "with-sources"
    folder.mkdir()
    (folder / "00-overview.md").write_text("---\ntitle: Overview\n---\nbody")
    sources_dir = folder / "sources"
    sources_dir.mkdir()
    (sources_dir / "src-1.md").write_text(
        "---\ntitle: First Source\n---\nimported source text"
    )
    (sources_dir / "src-2.md").write_text("---\ntitle: Second Source\n---\nmore text")

    saved_sources: list = []
    vectorize_calls: list = []

    class _SourceStub:
        def __init__(self, *, title=None):
            self.title = title
            self.full_text = None
            self.id = f"source:imp-{len(saved_sources)}"

        async def save(self):
            saved_sources.append(self)

        async def add_to_notebook(self, _id):
            pass

        async def vectorize(self):
            vectorize_calls.append(self.id)

    class _NotebookStub:
        def __init__(self, *, name, description=None):
            self.name, self.description = name, description
            self.id = "notebook:vec-1"

        async def save(self):
            pass

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.title, self.content = title, content
            self.note_type = note_type
            self.id = "note:vec-1"

        async def save(self):
            pass

        async def add_to_notebook(self, _id):
            pass

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _NotebookStub)
    monkeypatch.setattr(exports_mod, "Note", _NoteStub)
    monkeypatch.setattr(exports_mod, "Source", _SourceStub)

    r = client.post(
        "/api/notebooks/import",
        json={
            "source_path": str(folder),
            "mode": "new",
            "import_sources": True,
        },
    )
    assert r.status_code == 200, r.text
    # Both sources got vectorize() called — they're now searchable.
    assert len(vectorize_calls) == 2
    assert set(vectorize_calls) == {s.id for s in saved_sources}


def test_import_vectorize_failure_is_non_fatal(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.104 — If vectorize fails (e.g. embedding backend down),
    the import must still succeed: source is saved + text-searchable,
    and a warning surfaces explaining how to backfill embeddings later."""
    folder = tmp_path / "vec-fail"
    folder.mkdir()
    (folder / "n.md").write_text("---\ntitle: x\n---\nbody")
    (folder / "sources").mkdir()
    (folder / "sources" / "src.md").write_text("---\ntitle: S\n---\ntext")

    class _SourceStub:
        def __init__(self, *, title=None):
            self.title = title
            self.full_text = None
            self.id = "source:vec-fail-1"

        async def save(self):
            pass

        async def add_to_notebook(self, _id):
            pass

        async def vectorize(self):
            raise RuntimeError("embedding backend unavailable")

    class _NotebookStub:
        def __init__(self, *, name, description=None):
            self.id = "notebook:vec-fail-1"
            self.name = name

        async def save(self):
            pass

    class _NoteStub:
        def __init__(self, *, title=None, content=None, note_type=None):
            self.id = "note:vec-fail-1"
            self.title = title
            self.content = content
            self.note_type = note_type

        async def save(self):
            pass

        async def add_to_notebook(self, _id):
            pass

    import api.routers.exports as exports_mod

    monkeypatch.setattr(exports_mod, "Notebook", _NotebookStub)
    monkeypatch.setattr(exports_mod, "Note", _NoteStub)
    monkeypatch.setattr(exports_mod, "Source", _SourceStub)

    r = client.post(
        "/api/notebooks/import",
        json={
            "source_path": str(folder),
            "mode": "new",
            "import_sources": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # Source still got imported (1 source in source_ids)
    assert len(body["source_ids"]) == 1
    # Warning surfaced with the actionable hint
    assert any(
        "embedding queue failed" in w and "rebuild" in w.lower()
        for w in body["warnings"]
    ), body["warnings"]


# ============================================================================
# v0.7.111 — Combined single-file export
# ============================================================================


def test_export_combined_md_concatenates_pages_into_single_file(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    """v0.7.111 — combined_md produces ONE .md file with all notes
    concatenated, separated by horizontal rules so renderers paginate
    cleanly on print-to-PDF."""
    notes = [
        _FakeNote("note:ov", "📋 00 · Demo — Overview", "Overview body."),
        _FakeNote("note:p1", "📄 01 · Architecture", "Arch body."),
        _FakeNote("note:p2", "📄 02 · Backend", "Backend body."),
    ]
    nb = _FakeNotebook("notebook:1", "Demo", notes, description="A test notebook")
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "combined.md"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "combined_md"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["format"] == "combined_md"
    assert body["file_count"] == 1
    assert target.exists()
    content = target.read_text()
    # Cover page with notebook title + description
    assert "# 📚 Demo" in content
    assert "A test notebook" in content
    # Table of contents lists every note
    assert "## Contents" in content
    assert "Overview" in content
    # Each note's body is included
    assert "Overview body." in content
    assert "Arch body." in content
    assert "Backend body." in content
    # Horizontal rules separate sections (page breaks for print)
    assert content.count("\n---\n") >= 3


def test_export_combined_html_includes_print_friendly_page_breaks(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    """v0.7.111 — combined_html embeds print CSS so each note paginates
    when the user prints-to-PDF from the browser."""
    notes = [_FakeNote("note:1", "First", "**bold** body")]
    nb = _FakeNotebook("notebook:1", "Demo HTML", notes)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "combined.html"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "combined_html"},
    )
    assert r.status_code == 200, r.text
    assert target.exists()
    html = target.read_text()
    # HTML5 wrapper
    assert "<!doctype html>" in html.lower()
    # Print CSS: page-break-after on .onp-page-break
    assert "@media print" in html
    assert "page-break-after" in html
    # Each note rendered as actual HTML
    assert "<strong>bold</strong>" in html
    # Cover page block
    assert "onp-cover" in html


def test_export_combined_auto_corrects_file_extension(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    """v0.7.111 — caller passes 'combined' without an extension; we
    must append .md or .html based on format. Otherwise the file is
    indistinguishable from binary on macOS / Windows."""
    notes = [_FakeNote("note:1", "T", "body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "extensionless"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(target), "format": "combined_md"},
    )
    assert r.status_code == 200, r.text
    # .md auto-appended
    assert (tmp_path / "extensionless.md").exists()
    assert not (tmp_path / "extensionless").exists()


def test_export_combined_md_with_sources(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    notes = [_FakeNote("note:1", "Page", "body")]
    sources = [_FakeSource("source:1", "Doc", "source text content")]
    nb = _FakeNotebook("notebook:1", "Demo", notes, sources=sources)
    patched_domain["notebooks"]["notebook:1"] = nb

    target = tmp_path / "with-sources.md"
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={
            "destination": str(target),
            "format": "combined_md",
            "include_sources": True,
        },
    )
    assert r.status_code == 200, r.text
    content = target.read_text()
    # Sources section appears after pages
    assert "# 📁 Sources" in content
    assert "source text content" in content


def test_export_combined_refuses_directory_target(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
):
    notes = [_FakeNote("note:1", "T", "body")]
    nb = _FakeNotebook("notebook:1", "Demo", notes)
    patched_domain["notebooks"]["notebook:1"] = nb
    r = client.post(
        "/api/notebooks/notebook:1/export",
        json={"destination": str(tmp_path), "format": "combined_md"},
    )
    assert r.status_code == 400
    assert "directory" in r.json()["detail"].lower()


# ============================================================================
# v0.7.117 — XSS hardening in markdown→HTML rendering
# ============================================================================


def test_html_export_escapes_raw_script_tag_in_note_content() -> None:
    """v0.7.117 — Combined HTML / per-page HTML exports must escape raw
    <script> tags rendered from markdown note content. Without this,
    sharing a notebook export by email/Drive could execute the author's
    script in the recipient's browser."""
    from api.routers.exports import _markdown_to_html

    payload = "Hello <script>alert(document.cookie)</script> world"
    html = _markdown_to_html(payload)
    # Literal <script> tag is escaped — does NOT appear as raw HTML
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    # The visible body still contains "Hello" and "world"
    assert "Hello" in html
    assert "world" in html


def test_html_export_escapes_inline_xss_vectors() -> None:
    """v0.7.117 — Common XSS vectors (img onerror, iframe, event handlers)
    must all be rendered as escaped literal text, not active HTML."""
    from api.routers.exports import _markdown_to_html

    vectors = [
        "<img src=x onerror=alert(1)>",
        "<iframe src='javascript:alert(1)'></iframe>",
        "<svg onload=alert(1)>",
        "<a href='javascript:alert(1)'>click</a>",
        "<object data='x' onerror=alert(1)></object>",
    ]
    for v in vectors:
        html = _markdown_to_html(v)
        # The dangerous tag must be escaped — no raw <iframe>, etc.
        # We check for the lowercase tag name surrounded by angle brackets;
        # if escaped, it'll look like &lt;iframe&gt; instead.
        tag = v.split()[0].lstrip("<").rstrip(">/").lower()
        assert f"<{tag}" not in html.lower(), (
            f"Raw <{tag}> survived rendering — XSS vector! Output: {html!r}"
        )


def test_html_export_preserves_safe_markdown_after_xss_lockdown() -> None:
    """v0.7.117 — XSS lockdown must NOT break legitimate markdown
    rendering. Bold, code blocks, tables, lists, links, blockquotes
    all still work."""
    from api.routers.exports import _markdown_to_html

    safe_md = """
# Heading

Some **bold** and *italic* and `inline code` and ~~strike~~.

| a | b |
|---|---|
| 1 | 2 |

- list item 1
- list item 2

> blockquote

```python
def foo():
    pass
```

[Real link](https://example.com)
"""
    html = _markdown_to_html(safe_md)
    # All the safe markdown features rendered
    assert "<h1>Heading</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<code>inline code</code>" in html
    assert "<s>strike</s>" in html
    assert "<table>" in html
    assert "<ul>" in html
    assert "<blockquote>" in html
    assert "<pre>" in html
    # Real (non-javascript) links still rendered
    assert 'href="https://example.com"' in html


def test_import_zip_rejects_symlink_entries(
    client: TestClient,
    monkeypatch,
    tmp_path: Path,
):
    """v0.7.117 — Zip imports must reject entries with the Unix symlink
    mode bit (0o120000) set. A malicious zip with 'passwords.md' →
    '/etc/passwd' would otherwise have its 'content' read as the
    target path string."""
    zip_path = tmp_path / "symlink-attack.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # Create a regular file first
        zf.writestr("real.md", "---\ntitle: real\n---\nbody")
        # Create a symlink entry by hand — set external_attr to 0o120777 << 16
        info = zipfile.ZipInfo("evil-link.md")
        info.external_attr = 0o120777 << 16  # symlink mode bits
        zf.writestr(info, "/etc/passwd")  # "content" is the link target

    r = client.post(
        "/api/notebooks/import",
        json={"source_path": str(zip_path), "mode": "new"},
    )
    assert r.status_code == 400, r.text
    assert "not a regular file" in r.json()["detail"]


def test_is_regular_file_entry_helper() -> None:
    """v0.7.117 — Unit test on the file-type discriminator."""
    from api.routers.exports import _is_regular_file_entry

    # Regular file (S_IFREG = 0o100000)
    reg = zipfile.ZipInfo("foo.md")
    reg.external_attr = 0o100644 << 16
    assert _is_regular_file_entry(reg) is True

    # Directory (S_IFDIR = 0o040000)
    d = zipfile.ZipInfo("subdir/")
    d.external_attr = 0o040755 << 16
    assert _is_regular_file_entry(d) is True

    # Symlink (S_IFLNK = 0o120000)
    link = zipfile.ZipInfo("link.md")
    link.external_attr = 0o120777 << 16
    assert _is_regular_file_entry(link) is False

    # FIFO (S_IFIFO = 0o010000)
    fifo = zipfile.ZipInfo("fifo")
    fifo.external_attr = 0o010644 << 16
    assert _is_regular_file_entry(fifo) is False

    # No Unix mode (DOS attrs only) — accepted as regular
    dos = zipfile.ZipInfo("dos.md")
    dos.external_attr = 0  # all zero
    assert _is_regular_file_entry(dos) is True

    # Python zipfile.writestr default: permissions only, no S_IF* bits.
    # This is the most common case and MUST be accepted.
    py_default = zipfile.ZipInfo("py.md")
    py_default.external_attr = 0o600 << 16
    assert _is_regular_file_entry(py_default) is True


def test_html_export_adds_rel_noopener_to_external_links() -> None:
    """v0.7.118 — External links (http://, https://, mailto:) in
    exported HTML must include rel='noopener noreferrer' to prevent
    tabnabbing and Referer leak when the recipient clicks them.
    Internal anchors (#section) and relative paths (./other.md) are
    left untouched."""
    from api.routers.exports import _markdown_to_html

    # External http(s) and mailto get the rel attribute
    for href in (
        "https://example.com",
        "http://example.com/path",
        "mailto:foo@bar.com",
    ):
        html = _markdown_to_html(f"[click]({href})")
        assert 'rel="noopener noreferrer"' in html, (
            f"External link {href!r} missing rel attribute: {html!r}"
        )

    # Internal anchor + relative path do NOT get rel
    for href in ("#section", "./other.md", "../sibling.md"):
        html = _markdown_to_html(f"[ref]({href})")
        assert "rel=" not in html, (
            f"Internal/relative link {href!r} should not have rel: {html!r}"
        )


def test_export_notebook_obsidian_folder(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [
        _FakeNote("note:ov", "Overview", "Overview referencing [source:src1]."),
        _FakeNote("note:p1", "Architecture", "Arch details."),
    ]
    sources = [_FakeSource("src1", "Primary Source", "Full raw text content.")]
    nb = _FakeNotebook("notebook:obsidian1", "Research Vault", notes, sources)
    patched_domain["notebooks"]["notebook:obsidian1"] = nb

    target = tmp_path / "obsidian-vault"
    r = client.post(
        "/api/notebooks/notebook:obsidian1/export",
        json={
            "destination": str(target),
            "format": "obsidian_folder",
            "include_sources": True,
        },
    )
    assert r.status_code == 200, r.text
    assert (target / ".obsidian" / "app.json").exists()
    assert (target / "Index.md").exists()
    assert (target / "00-overview.md").exists()
    assert (target / "sources" / "src1.md").exists()

    # Verify Index.md Map of Content
    index_content = (target / "Index.md").read_text()
    assert "[[00-overview|Overview]]" in index_content
    assert "[[sources/src1|Primary Source]]" in index_content

    # Verify note frontmatter and wikilinks
    note_content = (target / "00-overview.md").read_text()
    assert "tags:" in note_content
    assert "- deeper-notebook" in note_content
    assert "[[sources/src1|src1]]" in note_content

    # Verify source frontmatter
    source_content = (target / "sources" / "src1.md").read_text()
    assert "source_id: \"src1\"" in source_content


def test_export_notebook_obsidian_zip(
    client: TestClient,
    patched_domain,
    tmp_path: Path,
) -> None:
    notes = [_FakeNote("note:1", "Quick Note", "Content.")]
    nb = _FakeNotebook("notebook:obsidian2", "Zip Vault", notes)
    patched_domain["notebooks"]["notebook:obsidian2"] = nb

    target_zip = tmp_path / "vault.zip"
    r = client.post(
        "/api/notebooks/notebook:obsidian2/export",
        json={
            "destination": str(target_zip),
            "format": "obsidian_zip",
        },
    )
    assert r.status_code == 200, r.text
    assert target_zip.exists()

    with zipfile.ZipFile(target_zip, "r") as zf:
        namelist = zf.namelist()
        assert ".obsidian/app.json" in namelist
        assert "Index.md" in namelist
        assert "manifest.json" in namelist
        index_data = zf.read("Index.md").decode("utf-8")
        assert "# 📚 Zip Vault" in index_data

