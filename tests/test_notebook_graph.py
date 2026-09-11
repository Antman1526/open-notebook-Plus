"""v0.8.83 — tests for Notebook.get_graph (mind-map graph, roadmap Batch 3)."""

from types import SimpleNamespace

from deeper_notebook.domain.notebook import Notebook


def _notebook() -> Notebook:
    nb = Notebook(name="My Notebook", description="d")
    nb.id = "notebook:abc"
    return nb


async def test_get_graph_builds_hub_and_spokes(monkeypatch):
    async def fake_sources(self):
        return [
            SimpleNamespace(id="source:1", title="Quantum Computing"),
            SimpleNamespace(id="source:2", title=None),  # exercise the fallback
        ]

    async def fake_notes(self):
        return [SimpleNamespace(id="note:1", title="My note")]

    monkeypatch.setattr(Notebook, "get_sources", fake_sources)
    monkeypatch.setattr(Notebook, "get_notes", fake_notes)

    graph = await _notebook().get_graph()

    # Notebook hub node comes first.
    assert graph["nodes"][0] == {
        "id": "notebook:abc",
        "type": "notebook",
        "label": "My Notebook",
    }
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert by_id["source:1"]["type"] == "source"
    assert by_id["source:1"]["label"] == "Quantum Computing"
    assert by_id["source:2"]["label"] == "Untitled source"  # None title → fallback
    assert by_id["note:1"]["type"] == "note"

    edges = {(e["source"], e["target"]): e["kind"] for e in graph["edges"]}
    assert edges[("notebook:abc", "source:1")] == "reference"
    assert edges[("notebook:abc", "source:2")] == "reference"
    assert edges[("notebook:abc", "note:1")] == "artifact"
    assert len(graph["nodes"]) == 4 and len(graph["edges"]) == 3


async def test_get_graph_truncates_long_labels(monkeypatch):
    long_title = "x" * 200

    async def fake_sources(self):
        return [SimpleNamespace(id="source:1", title=long_title)]

    async def fake_notes(self):
        return []

    monkeypatch.setattr(Notebook, "get_sources", fake_sources)
    monkeypatch.setattr(Notebook, "get_notes", fake_notes)

    graph = await _notebook().get_graph()
    label = next(n["label"] for n in graph["nodes"] if n["id"] == "source:1")
    assert len(label) == 80 and label.endswith("…")


async def test_get_graph_empty_notebook(monkeypatch):
    async def fake_empty(self):
        return []

    monkeypatch.setattr(Notebook, "get_sources", fake_empty)
    monkeypatch.setattr(Notebook, "get_notes", fake_empty)

    graph = await _notebook().get_graph()
    assert len(graph["nodes"]) == 1 and graph["nodes"][0]["type"] == "notebook"
    assert graph["edges"] == []


async def test_get_graph_includes_studio_artifacts_with_grounding(monkeypatch):
    from deeper_notebook.domain.notebook import StudioArtifact

    async def fake_sources(self):
        return [
            SimpleNamespace(id="source:1", title="Quantum Computing"),
            SimpleNamespace(id="source:2", title="Neural Nets"),
        ]

    async def fake_notes(self):
        return [SimpleNamespace(id="note:1", title="Notes")]

    async def fake_artifacts(notebook_id):
        return [
            SimpleNamespace(
                id="studio_artifact:report_1",
                title="Executive Brief",
                artifact_type="report",
                source_ids=["source:1", "source:non_existent"],
            )
        ]

    monkeypatch.setattr(Notebook, "get_sources", fake_sources)
    monkeypatch.setattr(Notebook, "get_notes", fake_notes)
    monkeypatch.setattr(StudioArtifact, "get_for_notebook", fake_artifacts)

    graph = await _notebook().get_graph()
    by_id = {n["id"]: n for n in graph["nodes"]}
    assert "studio_artifact:report_1" in by_id
    assert by_id["studio_artifact:report_1"]["type"] == "studio_artifact"
    assert by_id["studio_artifact:report_1"]["label"] == "Executive Brief"
    assert by_id["studio_artifact:report_1"]["artifact_type"] == "report"

    edges = {(e["source"], e["target"]): e["kind"] for e in graph["edges"]}
    assert edges[("notebook:abc", "studio_artifact:report_1")] == "studio_artifact"
    assert edges[("studio_artifact:report_1", "source:1")] == "grounded_in"
    assert ("studio_artifact:report_1", "source:non_existent") not in edges

