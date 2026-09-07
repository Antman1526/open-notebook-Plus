"""Tests for desktop.auto_register.lmstudio — LM Studio discovery and registration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from desktop.auto_register.lmstudio import (
    DEFAULT_LMSTUDIO_PORT,
    _lmstudio_port,
    _lmstudio_running,
    register_lmstudio_models,
)


def test_lmstudio_port_defaults_to_1234(monkeypatch):
    monkeypatch.delenv("DEEPER_NOTEBOOK_LMSTUDIO_PORT", raising=False)
    monkeypatch.delenv("LMSTUDIO_PORT", raising=False)
    assert _lmstudio_port() == DEFAULT_LMSTUDIO_PORT


def test_lmstudio_port_honors_env_override(monkeypatch):
    monkeypatch.setenv("DEEPER_NOTEBOOK_LMSTUDIO_PORT", "8888")
    assert _lmstudio_port() == 8888


def test_lmstudio_running_success():
    payload = {
        "data": [
            {"id": "qwen3.8-27b-mlx"},
            {"id": "text-embedding-nomic-embed-text-v1.5"},
        ]
    }
    resp = httpx.Response(200, content=json.dumps(payload).encode())
    with patch.object(httpx.Client, "get", return_value=resp):
        running, models = _lmstudio_running(1234)
    assert running is True
    assert models == ["qwen3.8-27b-mlx", "text-embedding-nomic-embed-text-v1.5"]


def test_lmstudio_running_connect_error():
    with patch.object(
        httpx.Client, "get", side_effect=httpx.ConnectError("Connection refused")
    ):
        running, models = _lmstudio_running(1234)
    assert running is False
    assert models == []


def test_lmstudio_running_non_200():
    resp = httpx.Response(502, content=b"Bad Gateway")
    with patch.object(httpx.Client, "get", return_value=resp):
        running, models = _lmstudio_running(1234)
    assert running is False
    assert models == []


def test_register_lmstudio_models_success():
    client = MagicMock(spec=httpx.Client)
    existing_cred_names = set()
    existing_model_keys = set()

    models_list = [
        "ornith-1.5-35b-a3b-mlx",
        "text-embedding-nomic-embed-text-v1.5",
    ]
    with (
        patch(
            "desktop.auto_register.lmstudio._lmstudio_running",
            return_value=(True, models_list),
        ),
        patch(
            "desktop.auto_register.lmstudio._ensure_credential",
            return_value="cred-lmstudio-123",
        ) as mock_ensure_cred,
        patch(
            "desktop.auto_register.lmstudio._ensure_model", return_value=True
        ) as mock_ensure_model,
    ):
        registered = register_lmstudio_models(
            client=client,
            existing_cred_names=existing_cred_names,
            existing_model_keys=existing_model_keys,
            port=1234,
        )

    assert registered is True
    assert "lm studio (local)" in existing_cred_names
    mock_ensure_cred.assert_called_once()
    assert mock_ensure_model.call_count == 2
    # Verify model type assignment: language vs embedding
    first_call = mock_ensure_model.call_args_list[0].kwargs
    assert first_call["name"] == "ornith-1.5-35b-a3b-mlx"
    assert first_call["model_type"] == "language"

    second_call = mock_ensure_model.call_args_list[1].kwargs
    assert second_call["name"] == "text-embedding-nomic-embed-text-v1.5"
    assert second_call["model_type"] == "embedding"


def test_register_lmstudio_models_not_running():
    client = MagicMock(spec=httpx.Client)
    existing_cred_names = set()
    existing_model_keys = set()

    with patch(
        "desktop.auto_register.lmstudio._lmstudio_running",
        return_value=(False, []),
    ):
        registered = register_lmstudio_models(
            client=client,
            existing_cred_names=existing_cred_names,
            existing_model_keys=existing_model_keys,
            port=1234,
        )

    assert registered is False
