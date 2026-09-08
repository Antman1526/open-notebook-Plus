"""v0.8.6 Item D — File-backed launcher preference layer.

Reads and writes ``~/.deeper-notebook/launcher.env`` as a KEY=VALUE file
so non-CLI users can configure the same knobs that are otherwise set via
shell env or ``.env`` files.

Format
------
- One ``KEY=VALUE`` per line.
- Lines starting with ``#`` are comments and are preserved through round-trips.
- Blank lines are preserved.
- No quoting support (values are taken verbatim after the ``=``).

Env-var precedence
------------------
``merge_with_env(env)`` applies file values ONLY for keys not already present
in ``env``. This means a shell-level ``export DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH=/x``
always wins over anything in the file — consistent with ops/CI override workflows.

Whitelist
---------
Only whitelisted keys may be written to the file. A PUT request with an
unknown key returns 400. This prevents the file from becoming an accidental
secrets store if the caller sends an arbitrary env var.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from deeper_notebook.environment import normalize_product_environment
from desktop.data_root import active_data_root

# v0.8.8 — log handle so merge_with_env can surface a malformed
# launcher.env (was silently swallowing ValueError pre-v0.8.8).
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Whitelist — only these keys may land in launcher.env.
# Do NOT add arbitrary env vars here; the goal is to expose a small, well-
# understood set of knobs — not a general-purpose secrets store.
# ---------------------------------------------------------------------------
ALLOWED_KEYS: frozenset[str] = frozenset(
    {
        "DEEPER_NOTEBOOK_LOCAL_DRAFT_MODEL_PATH",
        "DEEPER_NOTEBOOK_LOCAL_DRAFT_N_PREDICT",
        "DEEPER_NOTEBOOK_LOCAL_N_CTX",
        "DEEPER_NOTEBOOK_CHAT_LLM_CTX",
        "DEEPER_NOTEBOOK_CHAT_LLM_CTX_MAX",
        # v0.8.112 — the source-visual kill switch. Added deliberately and it
        # fits the rule above rather than bending it: a documented on/off knob,
        # not a secret and not arbitrary. Without it the flag is unreachable in
        # a packaged build — a Dock-launched .app inherits no shell environment,
        # and the launcher seeds its children from its own os.environ, so the
        # only alternative was `launchctl setenv`, which does not survive a
        # reboot.
        "DEEPER_NOTEBOOK_SOURCE_VISUALS_ENABLED",
        "DEEPER_NOTEBOOK_LLAMACPP_FLASH_ATTN",
        "DEEPER_NOTEBOOK_LLAMACPP_KV_QUANT",
    }
)


def _canonicalize_prefs(prefs: dict[str, str]) -> dict[str, str]:
    """Canonicalize compatibility keys through the central registry."""
    normalized = normalize_product_environment(prefs)
    return {key: normalized[key] for key in ALLOWED_KEYS if key in normalized}


def _prefs_path() -> Path:
    """Return the canonical path to the launcher.env file."""
    return active_data_root() / "launcher.env"


def _parse_file(text: str) -> dict[str, str]:
    """Parse KEY=VALUE text into a dict.

    Comments (#) and blank lines are ignored for the return value but are
    preserved by ``_render_file`` when round-tripping.

    Raises ``ValueError`` for non-blank, non-comment lines with no ``=``.
    """
    result: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(
                f"launcher.env line {lineno}: expected KEY=VALUE, got {raw!r}"
            )
        key, _, value = line.partition("=")
        result[key.strip()] = value  # value may be empty
    return result


def _render_file(existing_text: str, merged: dict[str, str]) -> str:
    """Rebuild the file content, preserving comments and blank lines.

    Strategy:
    - Walk the existing lines; update values for keys that appear.
    - Append new keys (those in ``merged`` but not in the old content).
    - Lines for keys whose value is absent from ``merged`` are dropped
      (i.e. the key was removed via ``update_prefs(key=None)``).
    """
    lines = existing_text.splitlines(keepends=True)
    updated_keys: set[str] = set()
    out: list[str] = []

    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(raw if raw.endswith("\n") else raw + "\n")
            continue
        if "=" not in stripped:
            # Malformed — preserve as-is (will be flagged on next parse).
            out.append(raw if raw.endswith("\n") else raw + "\n")
            continue
        key = stripped.partition("=")[0].strip()
        if key in merged:
            out.append(f"{key}={merged[key]}\n")
            updated_keys.add(key)
        # If key not in merged: omit the line (key was removed).

    # Append keys that weren't in the existing file.
    for key, value in merged.items():
        if key not in updated_keys:
            out.append(f"{key}={value}\n")

    return "".join(out)


def get_prefs() -> dict[str, str]:
    """Return current launcher preferences from the file.

    Returns an empty dict if the file does not exist or is empty.
    Raises ``ValueError`` for malformed lines.

    v0.8.8 — filters to ``ALLOWED_KEYS``. Pre-v0.8.8 the whitelist was
    only enforced on writes, so a file with non-whitelist keys (from a
    pre-whitelist history, manual edits, or a removed-from-whitelist
    release) leaked them through this function and into the API
    response of ``GET /api/launcher-prefs``. Filtering here aligns the
    function with its docstring promise and the v0.8.6 spec's "strict
    whitelist" wording.
    """
    path = _prefs_path()
    if not path.exists():
        return {}
    parsed = _parse_file(path.read_text(encoding="utf-8"))
    # Defense in depth — only surface whitelisted keys.
    return _canonicalize_prefs(parsed)


def update_prefs(updates: dict[str, Any]) -> dict[str, str]:
    """Merge ``updates`` into the current preferences and write the file.

    Parameters
    ----------
    updates:
        ``{KEY: VALUE}`` — sets the key.  ``{KEY: None}`` — removes the key.

    Returns the new merged dict (reflecting what is now in the file).

    Raises ``ValueError`` if any key is not in ``ALLOWED_KEYS`` or if the
    existing file has a malformed line.
    """
    unknown = set(updates) - ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"Key(s) not in whitelist: {sorted(unknown)}. "
            f"Allowed: {sorted(ALLOWED_KEYS)}"
        )

    path = _prefs_path()
    existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
    current = _parse_file(existing_text)

    for key, value in updates.items():
        if value is None:
            current.pop(key, None)
        else:
            current[key] = str(value)

    current = _canonicalize_prefs(current)

    # Write atomically (write to a temp file, rename).
    path.parent.mkdir(parents=True, exist_ok=True)
    new_text = _render_file(existing_text, current)
    tmp = path.with_suffix(".env.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)

    return current


def merge_with_env(env: dict[str, str]) -> dict[str, str]:
    """Return a copy of ``env`` with file values filled in for missing keys.

    Keys already present in ``env`` are NOT overwritten — shell env wins.
    This should be called early in the launcher startup sequence so all
    downstream readers (session_env builder, _spawn_llamacpp_chat, etc.)
    see the file values without special-casing.

    Returns the merged dict (the same dict that was passed in, mutated in
    place AND returned so callers can use it fluently).
    """
    try:
        prefs = get_prefs()
    except Exception as exc:
        # v0.8.8 — Surface the failure instead of silently swallowing
        # it. Pre-v0.8.8 a single malformed line reverted ALL prefs
        # with no indication to the user. Still non-fatal so a
        # misconfigured launcher.env doesn't block startup; the
        # launcher's logging is initialised before Supervisor.start_all,
        # so this log line lands in launcher.log where operators can
        # find it.
        log.warning(
            "launcher.env could not be parsed (%s); ignoring "
            "file-based prefs this launch. Fix the file and restart, "
            "or use Settings → Launch Preferences to overwrite it.",
            exc,
        )
        return env

    # v0.8.8 — second-line defense matching the get_prefs filter:
    # even if a non-whitelist key slipped through (e.g. a file edited
    # by hand outside the Settings UI), don't inject it into the
    # launcher's os.environ. The whitelist is the security boundary.
    for key, value in prefs.items():
        if key not in ALLOWED_KEYS:
            continue
        if key not in env:
            env[key] = value

    return env
