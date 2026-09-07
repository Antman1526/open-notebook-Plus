"""v0.8.40b — Hot-swap chat GGUF tests.

Two layers (same shape as v0.8.40):
  1. ControlServer `/hot_swap_chat` route — auth, validation, callback
     dispatch (real socket, real handler).
  2. `POST /api/local-models/set-active` endpoint — body validation,
     path-traversal guard, control-plane proxy.

We do NOT test the full Supervisor.hot_swap_chat method against a real
launcher — that's an integration test against the desktop bundle and
needs a real GGUF + llama.cpp on disk. The control-plane round-trip
suite proves the wiring; Supervisor.hot_swap_chat itself is reviewed
manually + the path-validation branches are exercised here via mock
callbacks.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from http import HTTPStatus

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routers import local_models as local_models_router
from desktop.launcher_control import ControlServer


def _http_request(method, url, body=None, token=None):
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            body = {}
        return e.code, body


# ---------------------------------------------------------------------------
# ControlServer /hot_swap_chat layer
# ---------------------------------------------------------------------------


@pytest.fixture
def server():
    srv = ControlServer()
    srv.start()
    yield srv
    srv.stop()


def test_hot_swap_route_requires_token(server):
    code, body = _http_request(
        "POST",
        f"{server.url}/hot_swap_chat",
        body={"path": "/tmp/foo.gguf"},
    )
    assert code == HTTPStatus.UNAUTHORIZED


def test_hot_swap_route_missing_path_field_returns_400(server):
    server.register_callback("hot_swap_chat", lambda _p: (True, ""))
    code, body = _http_request(
        "POST",
        f"{server.url}/hot_swap_chat",
        body={},
        token=server.token,
    )
    assert code == HTTPStatus.BAD_REQUEST
    assert "path" in body["error"]


def test_hot_swap_route_dispatches_to_callback(server):
    calls: list[str] = []

    def _cb(path: str) -> tuple[bool, str]:
        calls.append(path)
        return True, f"swapped to {path}"

    server.register_callback("hot_swap_chat", _cb)
    code, body = _http_request(
        "POST",
        f"{server.url}/hot_swap_chat",
        body={"path": "/abs/path/to/new.gguf"},
        token=server.token,
    )
    assert code == HTTPStatus.OK
    assert body["ok"] is True
    # Response echoes the path field for caller correlation.
    assert body["path"] == "/abs/path/to/new.gguf"
    assert "swapped to" in body["detail"]
    assert calls == ["/abs/path/to/new.gguf"]


def test_hot_swap_route_failure_returns_400_with_detail(server):
    def _cb(_p: str) -> tuple[bool, str]:
        return False, "File not found"

    server.register_callback("hot_swap_chat", _cb)
    code, body = _http_request(
        "POST",
        f"{server.url}/hot_swap_chat",
        body={"path": "/nonexistent.gguf"},
        token=server.token,
    )
    assert code == HTTPStatus.BAD_REQUEST
    assert body["ok"] is False
    assert "File not found" in body["detail"]


def test_hot_swap_route_no_callback_returns_503(server):
    """No registered callback → 503. Same shape as /restart_sidecar."""
    code, body = _http_request(
        "POST",
        f"{server.url}/hot_swap_chat",
        body={"path": "/foo.gguf"},
        token=server.token,
    )
    assert code == HTTPStatus.SERVICE_UNAVAILABLE


def test_restart_sidecar_route_still_works(server):
    """v0.8.40b refactored the do_POST dispatcher; regression check
    that the original /restart_sidecar route still functions."""
    server.register_callback("restart_sidecar", lambda k: (True, f"ok {k}"))
    code, body = _http_request(
        "POST",
        f"{server.url}/restart_sidecar",
        body={"kind": "chat"},
        token=server.token,
    )
    assert code == HTTPStatus.OK
    assert body["kind"] == "chat"
    assert body["ok"] is True


# ---------------------------------------------------------------------------
# /api/local-models/set-active endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(local_models_router.router)
    return a


def test_set_active_rejects_missing_path(app):
    with TestClient(app) as client:
        resp = client.post("/api/local-models/set-active", json={})
    assert resp.status_code == 400


def test_set_active_rejects_nonexistent_file(app, tmp_path):
    bogus = tmp_path / "no-such.gguf"
    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/set-active",
            json={"path": str(bogus)},
        )
    assert resp.status_code == 400
    assert "not found" in resp.json()["detail"].lower()


def test_set_active_rejects_non_gguf(app, tmp_path):
    bad = tmp_path / "not-a-gguf.txt"
    bad.write_text("x")
    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/set-active",
            json={"path": str(bad)},
        )
    assert resp.status_code == 400
    assert "gguf" in resp.json()["detail"].lower()


def test_set_active_rejects_outside_model_dir(app, tmp_path, monkeypatch):
    """A GGUF that exists but lives outside the configured model dir
    must be rejected. Defense-in-depth even though the file is real."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    rogue = tmp_path / "rogue"
    rogue.mkdir()
    rogue_gguf = rogue / "evil.gguf"
    rogue_gguf.write_bytes(b"x" * 100)

    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/set-active",
            json={"path": str(rogue_gguf)},
        )
    assert resp.status_code == 400
    assert "must be inside" in resp.json()["detail"].lower()


def test_set_active_503_when_no_control_url(app, tmp_path, monkeypatch):
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    gguf = model_dir / "x.gguf"
    gguf.write_bytes(b"y" * 100)
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.delenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL", raising=False)
    monkeypatch.delenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", raising=False)

    with TestClient(app) as client:
        resp = client.post(
            "/api/local-models/set-active",
            json={"path": str(gguf)},
        )
    assert resp.status_code == 503
    assert "control plane" in resp.json()["detail"].lower()


def test_set_active_happy_path_roundtrip(app, tmp_path, monkeypatch):
    """End-to-end: stand up a real ControlServer with a happy-path
    callback, point env vars at it, POST → assert callback received
    the resolved absolute path and the API returned ok=True."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    gguf = model_dir / "new-chat-q4.gguf"
    gguf.write_bytes(b"z" * 256)
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    active_aliases = (
        "DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL",
        "DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL",
    )
    for name in active_aliases:
        monkeypatch.delenv(name, raising=False)

    received_paths: list[str] = []

    def _cb(path: str) -> tuple[bool, str]:
        received_paths.append(path)
        return True, f"Chat swapped to {path}"

    srv = ControlServer()
    srv.start()
    try:
        srv.register_callback("hot_swap_chat", _cb)
        monkeypatch.setenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL", srv.url)
        monkeypatch.setenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", srv.token)

        with TestClient(app) as client:
            resp = client.post(
                "/api/local-models/set-active",
                json={"path": str(gguf)},
            )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["ok"] is True
        # The launcher should have received the RESOLVED absolute path,
        # not the user-provided one (matters when path was relative).
        assert len(received_paths) == 1
        assert received_paths[0] == str(gguf.resolve())
        for name in active_aliases:
            assert os.environ[name] == str(gguf.resolve())
    finally:
        srv.stop()


def test_set_active_launcher_rejection_maps_to_400(app, tmp_path, monkeypatch):
    """Launcher callback returns (False, detail) → API surfaces 400 +
    detail. Critical for "you can't swap to a corrupted file"-style
    errors from the launcher to reach the user clearly."""
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    gguf = model_dir / "bad.gguf"
    gguf.write_bytes(b"q" * 32)
    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))

    def _cb(_p: str) -> tuple[bool, str]:
        return False, "GGUF metadata read failed — file likely corrupted"

    srv = ControlServer()
    srv.start()
    try:
        srv.register_callback("hot_swap_chat", _cb)
        monkeypatch.setenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL", srv.url)
        monkeypatch.setenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", srv.token)

        with TestClient(app) as client:
            resp = client.post(
                "/api/local-models/set-active",
                json={"path": str(gguf)},
            )
        assert resp.status_code == 400
        assert "corrupted" in resp.json()["detail"]
    finally:
        srv.stop()


def test_set_active_mlx_directory_happy_path(app, tmp_path, monkeypatch):
    """POST /api/local-models/set-active supports MLX model directories."""
    model_dir = tmp_path / "models"
    mlx_dir = model_dir / "MLX" / "mlx-community__test-7b"
    mlx_dir.mkdir(parents=True)
    (mlx_dir / "config.json").write_text('{"model_type": "qwen"}')
    (mlx_dir / "model.safetensors").write_bytes(b"mlx-weights")

    monkeypatch.setenv("DEEPER_NOTEBOOK_MODEL_DIR", str(model_dir))
    monkeypatch.delenv("DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL", raising=False)

    received_paths: list[str] = []

    def _cb(path: str) -> tuple[bool, str]:
        received_paths.append(path)
        return True, f"MLX swapped to {path}"

    srv = ControlServer()
    srv.start()
    try:
        srv.register_callback("hot_swap_chat", _cb)
        monkeypatch.setenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_URL", srv.url)
        monkeypatch.setenv("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", srv.token)

        with TestClient(app) as client:
            resp = client.post(
                "/api/local-models/set-active",
                json={"path": str(mlx_dir)},
            )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["ok"] is True
        assert len(received_paths) == 1
        assert received_paths[0] == str(mlx_dir.resolve())
        assert os.environ["DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL"] == str(mlx_dir.resolve())
    finally:
        srv.stop()

