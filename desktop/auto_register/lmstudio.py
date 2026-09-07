"""LM Studio (https://lmstudio.ai) auto-register.

LM Studio exposes a local OpenAI-compatible API on port 1234 by default.
This module probes the server and registers its discovered models with
Deeper Notebook's local credential registry.
"""

from __future__ import annotations

import logging
import os

import httpx

from deeper_notebook.environment import resolve_env
from desktop.auto_register._http import (
    _ensure_credential,
    _ensure_model,
    _is_embedding_gguf,
)

log = logging.getLogger(__name__)

DEFAULT_LMSTUDIO_PORT = 1234

_PROBE_TIMEOUT = httpx.Timeout(connect=2.0, read=5.0, write=2.0, pool=2.0)


def _lmstudio_port() -> int:
    """Read the configured LM Studio port from env, fall back to 1234."""
    raw = (
        resolve_env("DEEPER_NOTEBOOK_LMSTUDIO_PORT", "")
        or os.environ.get("LMSTUDIO_PORT", "")
    ).strip()
    if not raw:
        return DEFAULT_LMSTUDIO_PORT
    try:
        return int(raw)
    except ValueError:
        log.warning(
            "DEEPER_NOTEBOOK_LMSTUDIO_PORT=%r is not an integer; falling back to %d",
            raw,
            DEFAULT_LMSTUDIO_PORT,
        )
        return DEFAULT_LMSTUDIO_PORT


def _lmstudio_running(port: int) -> tuple[bool, list[str]]:
    """Probe http://127.0.0.1:{port}/v1/models.

    Returns (running, discovered_model_ids). `running` is True iff the
    endpoint returns 200 with parseable JSON data.
    """
    url = f"http://127.0.0.1:{port}/v1/models"
    try:
        with httpx.Client(timeout=_PROBE_TIMEOUT) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                log.debug(
                    "LM Studio probe at %s returned HTTP %d — skipping",
                    url,
                    resp.status_code,
                )
                return False, []
            data = resp.json()
            models = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            return True, models
    except httpx.ConnectError:
        log.debug("LM Studio not running on :%d (connect refused)", port)
        return False, []
    except Exception as exc:
        log.info(
            "LM Studio probe at %s failed unexpectedly (%s) — skipping",
            url,
            exc,
        )
        return False, []


def register_lmstudio_models(
    *,
    client: httpx.Client,
    existing_cred_names: set[str],
    existing_model_keys: set[tuple[str, str]],
    port: int | None = None,
) -> bool:
    """Discover and register a running LM Studio instance.

    1. Probe the configured port (default 1234). Bail silently if nothing listens.
    2. Create/refresh the 'LM Studio (local)' credential pointing at
       base_url=http://127.0.0.1:{port}/v1.
    3. For each model id returned by /v1/models, register a model row
       (type 'embedding' if the name suggests an embedding model, else 'language')
       linked to that credential.

    Returns True if at least one model was registered.
    """
    target_port = port if port is not None else _lmstudio_port()
    running, models = _lmstudio_running(target_port)
    if not running:
        return False

    base_url = f"http://127.0.0.1:{target_port}/v1"
    cred_name = "LM Studio (local)"
    cred_id = _ensure_credential(
        client=client,
        existing_names=existing_cred_names,
        name=cred_name,
        provider="openai_compatible",
        modalities=["language", "embedding"],
        base_url=base_url,
    )
    if cred_id is None:
        return False

    existing_cred_names.add(cred_name.lower())
    registered = False
    for model_name in models:
        lower_name = model_name.lower()
        is_embedding = _is_embedding_gguf(model_name) or "embed" in lower_name
        model_type = "embedding" if is_embedding else "language"

        ok = _ensure_model(
            client=client,
            existing_keys=existing_model_keys,
            name=model_name,
            provider="openai_compatible",
            model_type=model_type,
            credential_id=cred_id,
        )
        if ok:
            existing_model_keys.add((model_name.lower(), model_type))
            registered = True
            log.info(
                "Registered LM Studio model %r (%s) against credential %r (port %d)",
                model_name,
                model_type,
                cred_name,
                target_port,
            )

    return registered
