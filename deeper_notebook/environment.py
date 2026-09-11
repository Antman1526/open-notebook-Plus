"""Canonical Deeper Notebook environment-variable resolution.

Product-owned settings have one precedence rule:

``DEEPER_NOTEBOOK_*`` > ``DN_*`` > ``OPEN_NOTEBOOK_*`` > ``ONP_*``.

Long-form legacy settings that never had an ``ONP_*`` spelling intentionally
expose only the canonical and legacy long names.  Values are never included in
resolution receipts or deprecation warnings.
"""

from __future__ import annotations

import os
import threading
import warnings
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass


@dataclass(frozen=True)
class SettingAliases:
    canonical: str
    canonical_short: str | None = None
    legacy: str | None = None
    legacy_short: str | None = None

    @property
    def precedence(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                self.canonical,
                self.canonical_short,
                self.legacy,
                self.legacy_short,
            )
            if name is not None
        )

    @property
    def legacy_names(self) -> frozenset[str]:
        return frozenset(
            name for name in (self.legacy, self.legacy_short) if name is not None
        )


@dataclass(frozen=True)
class EnvResolution:
    canonical: str
    winner: str | None
    used_legacy: bool


class LegacyEnvironmentWarning(UserWarning):
    """A deprecated product-owned environment alias supplied the value."""


_LONG_SUFFIXES = (
    "ACTIVE_GGUF_MODEL",
    "ACTIVE_MLX_MODEL",
    "ARTIFACT_EXPORT_DIR",
    "AUTO_ROUTE_CHAT",
    "CHAT_PROVIDER",
    "CHUNK_OVERLAP",
    "CHUNK_SIZE",
    "CLOUD_CHAT_MODEL_ID",
    "EMBEDDING_BATCH_SIZE",
    "ENCRYPTION_KEY",
    "ENCRYPTION_KEYS",
    "LAUNCHER_CONTROL_TOKEN",
    "LAUNCHER_CONTROL_URL",
    "LAUNCHER_LOG_DIR", "LMSTUDIO_PORT",
    "LOCAL_CHAT_BASE_URL",
    "LOCAL_CHAT_MODEL_ID",
    "LOCAL_DRAFT_MODEL_PATH",
    "LOCAL_DRAFT_N_PREDICT",
    "LOCAL_N_CTX",
    "MIN_CHUNK_SIZE",
    "MODEL_DIR",
    "MODEL_DIR_DEFAULT",
    "OSAURUS_PORT",
    "PASSWORD",
)

CONNECTION_TIMEOUT_PROVIDERS = (
    "ANTHROPIC",
    "AZURE",
    "DASHSCOPE",
    "DEEPSEEK",
    "ELEVENLABS",
    "GOOGLE",
    "GROQ",
    "MINIMAX",
    "MISTRAL",
    "OLLAMA",
    "OPENAI",
    "OPENAI_COMPATIBLE",
    "OPENROUTER",
    "VERTEX",
    "VOYAGE",
    "XAI",
)

_SHORT_SUFFIXES = (
    "AGENT_FSM",
    "AGENT_MAX_ITERATIONS",
    "API_READY_TIMEOUT",
    "ASK_MAX_RESULTS",
    "ASK_NODE_TIMEOUT_SEC",
    "ASK_PER_RESULT_CHAR_CAP",
    "AUTO_EXPORT_FIRST_DELAY_SECS",
    "AUTO_EXPORT_HOURS",
    "AUTO_EXPORT_KEEP",
    "BULK_VECTORIZE_MAX_SOURCES",
    "CHAT_HISTORY_CHAR_CAP",
    "CHAT_LLM_CTX",
    "CHAT_LLM_CTX_MAX",
    "CHAT_LLM_GGUF",
    "CHAT_LLM_N_GPU_LAYERS",
    "CHAT_MESSAGE_CHAR_CAP",
    "CHAT_MODEL_NAME",
    "CHAT_MODEL_TIMEOUT_SEC",
    "CHAT_RAM_GB_CEILING",
    "CHAT_TIMEOUT_S",
    "CHAT_TIMEOUT_SEC",
    "CHECKPOINT_KEEP_PER_THREAD",
    "CHECKPOINT_PRUNE_INTERVAL_HOURS",
    "CONNECTION_TEST_TIMEOUT_SEC",
    "DB_POOL_DISABLED",
    "DB_POOL_SIZE",
    "DISABLE_DB_AUTOREPAIR",
    "DISCOVER_MODELS_TIMEOUT_SEC",
    "EMBED_N_GPU_LAYERS",
    "ENCRYPTION_KDF",
    "EVIDENCE_STUDIO",
    "FRONTEND_READY_TIMEOUT",
    "LOCAL_REPLY_HEADROOM_TOKENS",
    "LOG_DIR",
    "LOG_JSON",
    "LOG_LEVEL",
    "MCP_AUTH_HEADER",
    "MCP_RPC_TIMEOUT_SEC",
    "MCP_TOOL_TIMEOUT_SEC",
    "KNOWLEDGE_ENGINE_BACKFILL_ENABLED",
    "KNOWLEDGE_ENGINE_SHADOW_ENABLED",
    "MEMORY_BATCH_TURNS",
    "MEMORY_CONFIDENCE_FLOOR",
    "MEMORY_INJECTED",
    "MEMORY_KEEP_PER_TABLE",
    "MEMORY_RECALL_BUDGET_SEC",
    "MEMORY_RECALL_EMBED_TIMEOUT_SEC",
    "MEMORY_RECALL_EPISODES",
    "MEMORY_RECALL_MODE",
    "MEMORY_RECALL_QUERY_TIMEOUT_SEC",
    "MEMORY_URL",
    "METRICS_AUTH_TOKEN",
    "MODEL_FLEET",
    "MODEL_SCAN_TIMEOUT",
    "NETWORK_STATE_TTL_SEC",
    "NET_PROBE_HOSTS",
    "NOTEBOOK_DELETE_BULK_THRESHOLD",
    "NOTE_TITLE_FALLBACK_LEN",
    "NOTE_TITLE_TIMEOUT_SEC",
    "PODCAST_GENERATION_TIMEOUT_SEC",
    "PODCAST_MAX_CONTENT_TOKENS",
    "PRIVACY_CLASSIFIER_MODEL",
    "PRIVACY_CLASSIFIER_TIMEOUT_SEC",
    "PRIVACY_CLASSIFIER_URL",
    "PRIVACY_GATE",
    "PROMPT_OPT_TIMEOUT_SEC",
    "RATE_LIMIT_PER_MIN",
    "REMIND_OPENCHRONICLE",
    "REPAIR_PORT",
    "RESEARCH_RUNS",
    "SCHOLARLY_MAILTO",
    "SCHOLARLY_SEARCH",
    "SEARCH_TIMEOUT_SEC",
    "SHUTDOWN_GRACE_SECS",
    "SIDECAR_TCP_TIMEOUT",
    "SLOW_QUERY_LOG_MS",
    "SOURCE_CHAT_HISTORY_CHAR_CAP",
    "SOURCE_CHAT_INSIGHT_CHAR_CAP",
    "SOURCE_CHAT_MAX_INSIGHTS",
    "SOURCE_CHAT_SOURCE_CHAR_CAP",
    "SOURCE_SYNC_TIMEOUT_SEC",
    "SOURCE_UPLOAD_MAX_BYTES",
    "SOURCE_VISUALS_ENABLED",
    "STREAM_HEARTBEAT_SEC",
    "STREAM_IDLE_TIMEOUT_SEC",
    "STT_URL",
    "STUDIO_EXPORT_STALE_DAYS",
    "STUDIO_EXTRACT_TIMEOUT_SEC",
    "STUDIO_MAX_COMBINED_CHARS",
    "STUDIO_MAX_FILE_CHARS",
    "STUDIO_NOTEBOOK_MULTIPAGE",
    "STUDIO_NOTEBOOK_PAGES_MAX",
    "STUDIO_NOTEBOOK_PARALLEL_PAGES",
    "STUDIO_OUTLINE_TIMEOUT_SEC",
    "STUDIO_PAGE_TIMEOUT_SEC",
    "STUDIO_RETENTION_DRY_RUN",
    "STUDIO_RETENTION_INTERVAL_HOURS",
    "STUDIO_REVISION_KEEP_PER_ARTIFACT",
    "STUDIO_STRICT_EVIDENCE",
    "STUDY_WORKBENCH",
    "SUBMIT_COMMAND_TIMEOUT_SEC",
    "SURREAL_TCP_TIMEOUT",
    "THEMES",
    "TRANSFORMATION_INPUT_CAP",
    "TRANSFORMATION_TIMEOUT_SEC",
    "TRANSFORM_NODE_TIMEOUT_SEC",
    "TTS_URL",
    "VECTOR_MIN_SCORE",
    "VERSION",
    "VOICE_INJECTED",
    "VISUAL_REFRESH",
    "WEB_SEARCH_CACHE_TTL_SEC",
    "WEB_SEARCH_KEYLESS",
    "WEB_SEARCH_MAX_RESULTS",
    "WEB_SEARCH_WIKI_LANG",
    "WEB_SEARCH_PROVIDER",
    "WEB_SEARCH_TIMEOUT_SEC",
    "WEB_SEARCH_TOTAL_BUDGET_SEC",
    "WORKER_MAX_TASKS",
    *(
        f"CONNECTION_TEST_TIMEOUT_SEC_{provider}"
        for provider in CONNECTION_TIMEOUT_PROVIDERS
    ),
)


def _long_aliases(suffix: str) -> SettingAliases:
    return SettingAliases(
        canonical=f"DEEPER_NOTEBOOK_{suffix}",
        legacy=f"OPEN_NOTEBOOK_{suffix}",
    )


def _short_aliases(suffix: str) -> SettingAliases:
    return SettingAliases(
        canonical=f"DEEPER_NOTEBOOK_{suffix}",
        canonical_short=f"DN_{suffix}",
        legacy=f"OPEN_NOTEBOOK_{suffix}",
        legacy_short=f"ONP_{suffix}",
    )


SETTINGS: dict[str, SettingAliases] = {
    **{
        aliases.canonical: aliases
        for aliases in (_long_aliases(suffix) for suffix in _LONG_SUFFIXES)
    },
    **{
        aliases.canonical: aliases
        for aliases in (_short_aliases(suffix) for suffix in _SHORT_SUFFIXES)
    },
}

_ALIASES: dict[str, SettingAliases] = {
    name: aliases for aliases in SETTINGS.values() for name in aliases.precedence
}

# Provider-specific connection timeouts are an intentional setting family.
_DYNAMIC_PREFIXES = (
    "DEEPER_NOTEBOOK_CONNECTION_TEST_TIMEOUT_SEC_",
    "DN_CONNECTION_TEST_TIMEOUT_SEC_",
    "OPEN_NOTEBOOK_CONNECTION_TEST_TIMEOUT_SEC_",
    "ONP_CONNECTION_TEST_TIMEOUT_SEC_",
)

_warned_legacy_keys: set[str] = set()
_warning_lock = threading.Lock()


def _setting_for(name: str) -> SettingAliases:
    aliases = _ALIASES.get(name)
    if aliases is not None:
        return aliases
    for prefix in _DYNAMIC_PREFIXES:
        if name.startswith(prefix) and len(name) > len(prefix):
            suffix = name.removeprefix(prefix)
            suffix = f"CONNECTION_TEST_TIMEOUT_SEC_{suffix}"
            return _short_aliases(suffix)
    raise KeyError(f"Unknown Deeper Notebook environment setting: {name}")


def _warn_legacy_once(legacy: str, canonical: str) -> None:
    with _warning_lock:
        if legacy in _warned_legacy_keys:
            return
        _warned_legacy_keys.add(legacy)
    warnings.warn(
        f"{legacy} is deprecated; use {canonical}.",
        LegacyEnvironmentWarning,
        stacklevel=3,
    )


def resolve_env(
    canonical: str,
    default: str | None = None,
    *,
    getter: Callable[[str], str | None] | None = None,
    with_receipt: bool = False,
) -> str | None | tuple[str | None, EnvResolution]:
    """Resolve one registered setting without ever exposing its value."""
    aliases = _setting_for(canonical)
    canonical = aliases.canonical
    read = getter or os.environ.get
    values = {name: read(name) for name in aliases.precedence}
    winner = next(
        (name for name in aliases.precedence if values[name] is not None),
        None,
    )
    value = values[winner] if winner is not None else default
    used_legacy = winner in aliases.legacy_names
    if winner is not None and used_legacy:
        _warn_legacy_once(winner, canonical)
    receipt = EnvResolution(
        canonical=canonical,
        winner=winner,
        used_legacy=used_legacy,
    )
    return (value, receipt) if with_receipt else value


def normalize_product_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Return a child-process-safe copy with precedence-safe alias mirrors.

    ``*_FILE`` variables are normalized as file paths.  This function never
    opens those paths, so secret file contents cannot be copied into the
    returned environment or into warnings.  Distinct assigned file/direct
    pairs retain their alias positions so file-aware getters can fall through
    unusable paths without normalization destroying the fallback chain.
    """
    normalized = dict(environment)
    for aliases in SETTINGS.values():
        candidates = tuple(
            candidate
            for name in aliases.precedence
            for candidate in (f"{name}_FILE", name)
        )
        winner = next(
            (name for name in candidates if environment.get(name) is not None),
            None,
        )
        if winner is None:
            continue

        for name in candidates:
            normalized.pop(name, None)

        is_file = winner.endswith("_FILE")
        if not is_file:
            value = environment[winner]
            for name in aliases.precedence:
                normalized[name] = value
        else:
            assigned_pairs = tuple(
                (
                    environment.get(f"{name}_FILE"),
                    environment.get(name),
                )
                for name in aliases.precedence
            )
            first_pair = next(
                pair
                for pair in assigned_pairs
                if pair[0] is not None or pair[1] is not None
            )
            active_pair = first_pair
            for name, assigned_pair in zip(
                aliases.precedence,
                assigned_pairs,
                strict=True,
            ):
                if assigned_pair[0] is not None or assigned_pair[1] is not None:
                    active_pair = assigned_pair
                file_value, direct_value = active_pair
                if file_value is not None:
                    normalized[f"{name}_FILE"] = file_value
                if direct_value is not None:
                    normalized[name] = direct_value

        base_winner = winner.removesuffix("_FILE")
        if base_winner in aliases.legacy_names:
            canonical = f"{aliases.canonical}_FILE" if is_file else aliases.canonical
            _warn_legacy_once(winner, canonical)

    return normalized


def apply_product_environment(
    environment: MutableMapping[str, str],
) -> dict[str, str]:
    """Apply exact normalized product state to a process environment."""
    normalized = normalize_product_environment(environment)
    product_names = {
        candidate
        for aliases in SETTINGS.values()
        for name in aliases.precedence
        for candidate in (name, f"{name}_FILE")
    }
    for name in product_names:
        if name not in normalized:
            environment.pop(name, None)
    environment.update(normalized)
    return normalized


def _reset_warning_state_for_tests() -> None:
    """Reset process-global warning deduplication for isolated unit tests."""
    with _warning_lock:
        _warned_legacy_keys.clear()


__all__ = [
    "CONNECTION_TIMEOUT_PROVIDERS",
    "EnvResolution",
    "LegacyEnvironmentWarning",
    "SETTINGS",
    "SettingAliases",
    "apply_product_environment",
    "normalize_product_environment",
    "resolve_env",
]
