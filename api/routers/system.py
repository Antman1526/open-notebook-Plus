"""v0.8.40d — System control endpoints for launcher → API IPC.

The v0.8.40 control plane gave the API a way to call into the launcher
(`POST /restart_sidecar`, `/hot_swap_chat`). This module gives the
launcher the reverse channel: a way to push state updates INTO the
running API process. Currently one use case:

  - After `hot_swap_chat` succeeds, the launcher needs to update
    `DEEPER_NOTEBOOK_LOCAL_N_CTX` in the API's environment so the smart
    router (provision.py) sees the new GGUF's native context length on
    the very next chat turn. Without this push, the n_ctx stays at the
    OLD GGUF's value until the next app launch — documented as a
    known limitation in v0.8.40b CHANGELOG, closed here.

Design choices:
  - **Path bypasses the password middleware** (added to
    main.py:excluded_paths) so the launcher doesn't need to know the
    user-facing password.
  - **Auth via the same bearer token the launcher control plane uses**
    (`DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN`). Both the launcher and the
    API have this token via session_env; reusing it for the reverse
    direction is symmetric. A future v0.8.40e could split the token
    if scope separation matters, but the trust boundary is the same
    (launcher and its child API process).
  - **Strict whitelist** of allowed env var names. Without this, a
    compromised process anywhere in the box could overwrite arbitrary
    env vars (PATH, PYTHONPATH…) and execute code on the next subprocess
    spawn. The whitelist is explicit; new use cases require adding the
    var name + a CHANGELOG note explaining why it's safe.
"""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from deeper_notebook.ai.offline_gate import find_local_language_model
from deeper_notebook.environment import normalize_product_environment, resolve_env
from deeper_notebook.health.network import get_network_state_with_settings
from desktop.data_root import active_data_root

router = APIRouter()


# Whitelist of env var names the launcher is permitted to push into
# the running API. Keep this list narrow + audited — each entry should
# correspond to a documented launcher-side mutation flow.
#
# DEEPER_NOTEBOOK_LOCAL_N_CTX — pushed by v0.8.40d after a successful
#   hot_swap_chat so provision.py's router sees the new GGUF's native
#   context length without app restart.
_ALLOWED_ENV_VARS: frozenset[str] = frozenset(
    {
        "DEEPER_NOTEBOOK_LOCAL_N_CTX",
        "DEEPER_NOTEBOOK_ACTIVE_GGUF_MODEL",
        "DEEPER_NOTEBOOK_ACTIVE_MLX_MODEL",
        "OPENAI_COMPATIBLE_BASE_URL",
        "OPENAI_COMPATIBLE_API_KEY",
    }
)


class EnvRefreshRequest(BaseModel):
    """Body for POST /api/system/env-refresh.

    Map of {env_var_name: new_value}. Only keys in `_ALLOWED_ENV_VARS`
    are honored; everything else is rejected with a 400 listing the
    offending names so the launcher knows to update its allowlist.
    """

    vars: dict[str, str]


@router.post("/api/system/env-refresh")
async def env_refresh(
    body: EnvRefreshRequest,
    authorization: str | None = Header(default=None),
):
    """v0.8.40d — Mutate selected env vars in the running API process.

    Called by the launcher's `hot_swap_chat` to push the new
    `DEEPER_NOTEBOOK_LOCAL_N_CTX` value so subsequent chat turns route
    against the new GGUF's actual native context window (not the stale
    pre-swap value).

    Auth: bearer token matching `DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN`.
    The launcher and API both receive this token via session_env at
    boot; nothing else on the box should know it. Constant-time compare
    via `secrets.compare_digest` so a timing attack on the token isn't
    feasible from a chatty localhost neighbor.

    Returns `{updated: [keys], rejected: [keys]}` so the caller can
    distinguish "applied" from "ignored" without parsing log lines.
    """
    expected_token = (
        resolve_env("DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN", "") or ""
    ).strip()
    if not expected_token:
        # No token configured → endpoint is disabled. Return 503 so
        # the caller (launcher) doesn't retry indefinitely.
        raise HTTPException(
            status_code=503,
            detail=(
                "env-refresh is disabled — DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN "
                "is not set in the API environment. The API is likely "
                "running outside the desktop launcher."
            ),
        )

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Missing or malformed Authorization header",
        )
    presented = authorization[len("Bearer ") :].strip()
    if not secrets.compare_digest(presented, expected_token):
        raise HTTPException(status_code=401, detail="Invalid token")

    updated: list[str] = []
    rejected: list[str] = []
    accepted: dict[str, str] = {}
    for k, v in (body.vars or {}).items():
        if k not in _ALLOWED_ENV_VARS:
            rejected.append(k)
            continue
        # `os.environ` mutation is process-wide and thread-safe per
        # CPython's GIL. The router reads via `os.getenv()` lazily at
        # chat-turn time, so the next turn picks up the new value.
        accepted[k] = str(v)
        updated.append(k)

    # Normalize the accepted patch independently so a freshly supplied legacy
    # alias overrides stale higher-precedence mirrors from an earlier refresh.
    # Multiple aliases in one request still use the standard precedence rule.
    os.environ.update(normalize_product_environment(accepted))
    return {"updated": updated, "rejected": rejected}


@router.get("/api/system/db-repair-needed")
async def db_repair_needed() -> dict:
    """v0.8.67q — Report whether the launcher flagged the SurrealDB live-query
    state as corrupt.

    The launcher's worker watcher (v0.8.67l) writes ~/.deeper-notebook/
    .needs_db_repair when it sees the "key being inserted already exists"
    crash that bricks source processing. On the NEXT launch the launcher runs
    a backup-first auto-repair and clears the flag. Between detection and that
    relaunch the worker is down with no UI signal, so the user doesn't know to
    restart — this endpoint lets the frontend show a banner telling them to.

    Read-only, no secrets. Unlike the launcher→API push routes above, this is a
    normal authenticated GET (the frontend calls it through the API client);
    it is intentionally NOT in main.py's excluded_paths."""
    flag = active_data_root() / ".needs_db_repair"
    try:
        needs = flag.exists()
    except OSError:
        needs = False
    return {"needs_repair": needs}


@router.get("/api/system/network-status")
async def network_status() -> dict:
    """v0.8.68 — current network state for the frontend offline badge.

    Drives use-network-status / NetworkStatusBadge (same polling-banner
    pattern as db_repair_needed above). Never 500s: any internal error
    degrades to {"status": "unknown"} so a probe bug can't paint the UI
    red or break the shell render."""
    import time as _time

    try:
        state = await get_network_state_with_settings()
        fallback_name = None
        if state.status == "offline":
            try:
                rec = await find_local_language_model()
                fallback_name = getattr(rec, "name", None) if rec else None
            except Exception:
                fallback_name = None
        return {
            "status": state.status,
            "forced_offline": state.forced_offline,
            "local_fallback_model": fallback_name,
            "checked_epoch_ms": int(_time.time() * 1000),
        }
    except Exception:
        return {
            "status": "unknown",
            "forced_offline": False,
            "local_fallback_model": None,
            "checked_epoch_ms": int(_time.time() * 1000),
        }
