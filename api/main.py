import os
import re
import subprocess
from pathlib import Path

from dotenv import load_dotenv

from deeper_notebook.environment import (
    apply_product_environment,
    resolve_env,
)

# Load and normalize product-owned settings before importing authentication,
# logging, credentials, model routing, or database modules.
load_dotenv()
_NORMALIZED_PRODUCT_ENVIRONMENT = apply_product_environment(os.environ)

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Receive, Scope, Send

from api.auth import PasswordAuthMiddleware

# v0.7.120 — cross-cutting middlewares split into api/middleware/.
from api.middleware.metrics import PrometheusMetricsMiddleware
from api.middleware.request_id import RequestIDMiddleware
from api.middleware.security_headers import SecurityHeadersMiddleware
from api.rate_limit import RateLimitMiddleware
from api.routers import (
    auth,
    capture,
    chat,
    config,
    context,
    credentials,
    deeper_notebook,
    embedding,
    embedding_rebuild,
    episode_profiles,
    evaluations,
    exports,  # v0.7.90 — notebook/note export to host filesystem
    filesystem,  # v0.7.90 — host filesystem listing/mkdir for picker UI
    insights,
    knowledge_engine,
    knowledge_navigation,
    knowledge_workspace,
    languages,
    models,
    notebooks,
    notes,
    overlay,
    podcasts,
    research,
    search,
    settings,
    source_chat,
    source_visuals,
    sources,
    speaker_profiles,
    studio,
    study,
    study_anki,
    study_assistants,
    study_exams,
    study_plans,
    study_voice,
    transformations,
    vault,
    video_overviews,
)
from api.routers import commands as commands_router
from api.routers import (
    gmail as gmail_router,
)
from api.routers import launcher_prefs as _launcher_prefs_router  # v0.8.6 Item D
from api.routers import local_models as _local_models_router
from api.routers import mcp as _mcp_router
from api.routers import runtime as _runtime_router
from api.routers import system as _system_router  # v0.8.40d — launcher → API env push
from api.routers import updates as _updates_router  # v0.8.70 — in-app update notifier
from deeper_notebook.database.async_migrate import AsyncMigrationManager
from deeper_notebook.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DeeperNotebookError,
    ExternalServiceError,
    InvalidInputError,
    NetworkError,
    NotFoundError,
    RateLimitError,
)
from deeper_notebook.identity import DESCRIPTION, PRODUCT_NAME
from deeper_notebook.knowledge_engine.service import (
    KnowledgeEngineService,
    enabled_setting,
)
from deeper_notebook.logging import configure_logging
from deeper_notebook.utils.encryption import get_secret_from_env


def _parse_cors_origins(raw: str) -> list[str]:
    """Parse CORS_ORIGINS env value into a list of origins."""
    value = raw.strip()
    if value == "*":
        return ["*"]
    return [origin.strip() for origin in value.split(",") if origin.strip()]


# Parsed once at module load; CORS_ORIGINS changes require a restart.
_cors_origins_raw = os.getenv("CORS_ORIGINS")
CORS_ALLOWED_ORIGINS = _parse_cors_origins(_cors_origins_raw or "*")
CORS_IS_DEFAULT_WILDCARD = _cors_origins_raw is None


def _cors_headers(request: Request) -> dict[str, str]:
    """
    Build CORS headers for error responses.

    Mirrors Starlette CORSMiddleware behavior: reflects the request Origin
    when the origin is allowed (or when wildcard is configured, since
    browsers reject `Access-Control-Allow-Origin: *` combined with
    credentials). Omits `Access-Control-Allow-Origin` for disallowed
    origins so the browser blocks the error body from leaking cross-origin.
    """
    origin = request.headers.get("origin")
    headers: dict[str, str] = {
        "Access-Control-Allow-Credentials": "true",
        "Access-Control-Allow-Methods": "*",
        "Access-Control-Allow-Headers": "*",
    }

    if origin and ("*" in CORS_ALLOWED_ORIGINS or origin in CORS_ALLOWED_ORIGINS):
        headers["Access-Control-Allow-Origin"] = origin
        headers["Vary"] = "Origin"

    return headers


# Import commands to register them in the API process
try:
    logger.info("Commands imported in API process")
except Exception as e:
    logger.error(f"Failed to import commands in API process: {e}")


# v0.7.134 — Pool warmup retry helper (Area for Review #6).
#
# Background: the v0.7.44 warmup attempt grabs `warmup_n` connections at
# startup so the first chat doesn't pay the cold-handshake cost. The
# v0.7.52 bound added asyncio.wait_for(timeout=10) so a hung SurrealDB
# can't block boot indefinitely. But: a single transient failure (network
# blip during startup, SurrealDB still settling) used to break the entire
# warmup loop. The first chat would then pay the cold-handshake cost
# anyway — exactly what warmup was supposed to prevent.
#
# Fix: retry each individual acquire with exponential backoff (0.5s,
# 1.0s, 2.0s). After 3 attempts we give up on that connection and the
# outer loop decides whether to bail entirely or continue with fewer
# warm connections. Total worst-case warmup wait: 3 × 10s + 0.5 + 1.0
# = ~31.5s per slot, vs. ~5min cumulative chat-hot-path penalty if
# warmup silently skipped.
_WARMUP_RETRY_DELAYS_S: tuple[float, ...] = (0.5, 1.0, 2.0)


async def _warmup_pool_acquire_with_retry(timeout_s: float = 10.0):
    """Acquire one pool connection with retry-on-failure.

    Each attempt has its own ``asyncio.wait_for`` timeout (default
    10s). Between attempts we ``asyncio.sleep`` per
    ``_WARMUP_RETRY_DELAYS_S``. After all attempts fail, re-raises
    the LAST exception so the caller can distinguish timeout from
    other failures (current call site uses two ``except`` clauses).

    Returns the acquired AsyncSurreal connection.
    """
    from deeper_notebook.database.repository import _acquire

    last_exc: BaseException | None = None
    for attempt, delay in enumerate(_WARMUP_RETRY_DELAYS_S):
        try:
            return await asyncio.wait_for(_acquire(), timeout=timeout_s)
        except Exception as exc:
            last_exc = exc
            is_last = attempt == len(_WARMUP_RETRY_DELAYS_S) - 1
            if not is_last:
                logger.warning(
                    "DB pool warmup attempt {}/{} failed ({}); retrying in {}s",
                    attempt + 1,
                    len(_WARMUP_RETRY_DELAYS_S),
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
            # else: fall through, last_exc gets raised below
    # All attempts exhausted. last_exc should always be set here
    # (we only exit the loop after at least one failed attempt), but
    # defend against the edge case where the loop body was somehow
    # bypassed (e.g., empty _WARMUP_RETRY_DELAYS_S).
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("pool warmup failed with no exception captured")


# v0.7.190 — Module-level strong-ref set for background tasks. Anchors
# every long-running task we spawn (digest scheduler, checkpoint
# pruner, gmail pre-warm) so the asyncio event loop's weak-ref
# tracking can't GC them under pressure. Documented foot-gun: see
# https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
#   "Important: Save a reference to the result of this function, to
#    avoid a task disappearing mid-execution. The event loop only
#    keeps weak references to tasks. A task that isn't referenced
#    elsewhere may be garbage collected at any time, even before it's
#    done."
# The lifespan-local `digest_scheduler_task = asyncio.create_task(...)`
# pattern works today (the local var is held by the closure for the
# lifespan duration), but a future refactor that extracts the spawn
# into a helper function would silently lose the anchor. _track_task
# is the asyncio-recommended defensive pattern.
_BACKGROUND_TASKS: "set[asyncio.Task]" = set()


def _track_task(task: "asyncio.Task") -> "asyncio.Task":
    """Anchor `task` to the module-level set so the GC can't reap it.
    Auto-discards on completion via add_done_callback so the set
    doesn't leak across many short-lived tasks."""
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return task


def _clear_knowledge_engine_service(app: FastAPI) -> None:
    """Remove optional engine state without leaving a disabled sentinel."""
    if hasattr(app.state, "knowledge_engine_service"):
        delattr(app.state, "knowledge_engine_service")


def _clear_knowledge_navigation_service(app: FastAPI) -> None:
    """Remove navigation state at lifespan exit without a disabled sentinel."""
    if hasattr(app.state, "knowledge_navigation_service"):
        delattr(app.state, "knowledge_navigation_service")


async def _start_knowledge_navigation(
    app: FastAPI,
    *,
    engine_service: KnowledgeEngineService | None = None,
) -> None:
    """Own metadata navigation independently of optional engine hydration."""
    from deeper_notebook.knowledge_engine.navigation_repository import (
        KnowledgeNavigationRepository,
    )
    from deeper_notebook.knowledge_engine.navigation_service import (
        KnowledgeNavigationService,
    )

    _clear_knowledge_navigation_service(app)
    engine_repository = engine_service
    app.state.knowledge_navigation_service = KnowledgeNavigationService(
        metadata_repository=KnowledgeNavigationRepository(),
        engine_repository=engine_repository,
    )


def _create_knowledge_engine_runtime() -> KnowledgeEngineService:
    """Build the optional engine without exposing its storage dependencies."""
    from deeper_notebook.knowledge_engine.backfill import (
        CanonicalSourceCatalog,
        KnowledgeBackfillService,
    )
    from deeper_notebook.knowledge_engine.equivalence import legacy_projection_digest
    from deeper_notebook.knowledge_engine.repository import KnowledgeRepository
    from deeper_notebook.knowledge_engine.shadow import KnowledgeShadowCoordinator
    from deeper_notebook.overlay.paths import OverlayLayout
    from deeper_notebook.overlay.repository import OverlayRepository
    from deeper_notebook.overlay.storage import OverlayStorage
    from deeper_notebook.vault.repository import VaultRepository

    repository = KnowledgeRepository()
    coordinator = KnowledgeShadowCoordinator(repository=repository)
    catalog = CanonicalSourceCatalog(
        overlay_repository=OverlayRepository(),
        overlay_storage=OverlayStorage(OverlayLayout.active()),
        vault_repository=VaultRepository(),
    )
    backfill = KnowledgeBackfillService(catalog=catalog, repository=repository)

    async def legacy_digest_builder(space_id: str, exact_queries: tuple[str, ...]):
        return await legacy_projection_digest(
            catalog,
            space_id=space_id,
            exact_queries=exact_queries,
        )

    async def unified_digest_builder(space_id: str, exact_queries: tuple[str, ...]):
        return await repository.projection_digest(space_id, exact_queries)

    return KnowledgeEngineService(
        repository=repository,
        coordinator=coordinator,
        catalog=catalog,
        backfill=backfill,
        legacy_digest_builder=legacy_digest_builder,
        unified_digest_builder=unified_digest_builder,
    )


async def _start_knowledge_engine(
    app: FastAPI,
    *,
    runtime_factory=_create_knowledge_engine_runtime,
) -> tuple[object | None, asyncio.Task | None]:
    """Start the shadow-only engine and return its coordinator/task ownership."""
    _clear_knowledge_engine_service(app)
    try:
        shadow_enabled = enabled_setting(
            "DEEPER_NOTEBOOK_KNOWLEDGE_ENGINE_SHADOW_ENABLED"
        )
        backfill_enabled = enabled_setting(
            "DEEPER_NOTEBOOK_KNOWLEDGE_ENGINE_BACKFILL_ENABLED"
        )
    except Exception as exc:
        logger.warning(
            "knowledge_engine_configuration_invalid ({})", type(exc).__name__
        )
        return None, None

    if backfill_enabled and not shadow_enabled:
        logger.warning("knowledge_engine_configuration_invalid ({})", "ValueError")
        return None, None
    if not shadow_enabled:
        return None, None

    try:
        service = runtime_factory()
        app.state.knowledge_engine_service = service
        task = None
        if backfill_enabled:
            task = _track_task(
                asyncio.create_task(
                    service.run_backfill(), name="knowledge-engine-backfill"
                )
            )
        return service.coordinator, task
    except Exception as exc:
        _clear_knowledge_engine_service(app)
        logger.warning("knowledge_engine_startup_unavailable ({})", type(exc).__name__)
        return None, None


async def _stop_knowledge_engine(
    app: FastAPI,
    backfill_task: asyncio.Task | None,
) -> None:
    """Cancel only the task this lifespan invocation explicitly owns."""
    if backfill_task is not None:
        if not backfill_task.done():
            backfill_task.cancel()
        try:
            await backfill_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(
                "knowledge_engine_backfill_shutdown_unavailable ({})",
                type(exc).__name__,
            )
    _clear_knowledge_engine_service(app)


async def _reconcile_source_search_index_maintenance_at_startup() -> None:
    """Finish a crash-surviving source-search rebuild before serving requests."""
    try:
        from deeper_notebook.domain.notebook import (
            reconcile_source_search_index_maintenance,
        )

        if not await reconcile_source_search_index_maintenance():
            logger.warning(
                "Search relevance may be degraded: source-search index maintenance "
                "remains pending after startup reconciliation"
            )
    except asyncio.CancelledError:
        logger.warning(
            "Search relevance may be degraded: source-search index maintenance "
            "startup reconciliation was cancelled; the durable marker is retained"
        )
        raise
    except Exception as exc:
        logger.warning(
            "Search relevance may be degraded: source-search index maintenance "
            "startup reconciliation failed ({})",
            type(exc).__name__,
        )


async def _close_database_pool_after_source_search_index_maintenance() -> None:
    """Bounded clean-shutdown drain before closing the DB pool.

    The drain's durable marker survives a timeout or forced kill and is repaired
    by startup reconciliation. This is a clean-shutdown guarantee only: an
    unclean process kill can interrupt the pass but cannot erase its receipt.
    """
    try:
        from deeper_notebook.domain.notebook import (
            cancel_source_search_index_maintenance,
            drain_source_search_index_maintenance,
        )

        if not await drain_source_search_index_maintenance():
            logger.warning(
                "Search relevance may be degraded: source-search index maintenance "
                "did not finish during bounded shutdown drain; the durable marker "
                "will reconcile at next API startup"
            )
    except TimeoutError:
        # The timed-out drain shields its worker, so explicitly cancel and
        # await that exact task before the pool can close underneath it.
        await cancel_source_search_index_maintenance()
        logger.warning(
            "Search relevance may be degraded: source-search index maintenance "
            "shutdown drain timed out; the durable marker will reconcile at next "
            "API startup"
        )
    except asyncio.CancelledError:
        # Lifespan cancellation cannot leave a rebuild querying a closed pool.
        # The durable marker remains for next-startup reconciliation.
        await cancel_source_search_index_maintenance()
        logger.warning(
            "Search relevance may be degraded: source-search index maintenance "
            "shutdown drain was cancelled; the durable marker is retained"
        )
        raise
    except Exception as exc:
        logger.warning(
            "Search relevance may be degraded: source-search index maintenance "
            "shutdown drain failed ({}); the durable marker will reconcile at "
            "next API startup",
            type(exc).__name__,
        )

    try:
        from deeper_notebook.database.repository import close_pool

        await close_pool()
        logger.info("SurrealDB pool closed")
    except Exception as exc:
        logger.warning(f"Closing DB pool raised: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan event handler for the FastAPI application.
    Runs database migrations automatically on startup.
    """
    # v0.7.14 — configure rotated file logging before anything else, so
    # startup errors (migrations, encryption checks) land in a file the
    # user can `tail`. Default sink: ~/.deeper-notebook/logs/api.log
    # Honors DEEPER_NOTEBOOK_LOG_DIR, DEEPER_NOTEBOOK_LOG_LEVEL, DEEPER_NOTEBOOK_LOG_JSON.
    log_dir = configure_logging("api")

    # Startup: Security checks
    logger.info("Starting API initialization — logs at {}", log_dir)

    # Security check: Encryption key
    # v0.7.24 — also honor the v0.7.17 plural rotation env var. A user
    # who has finished rotation and only has DEEPER_NOTEBOOK_ENCRYPTION_KEYS
    # set was getting a spurious "encryption will fail" warning pointing
    # at the wrong variable.
    has_singular = bool(
        resolve_env("DEEPER_NOTEBOOK_ENCRYPTION_KEY", getter=get_secret_from_env)
    )
    has_plural = bool(
        resolve_env("DEEPER_NOTEBOOK_ENCRYPTION_KEYS", getter=get_secret_from_env)
    )
    if not (has_singular or has_plural):
        logger.warning(
            "Neither DEEPER_NOTEBOOK_ENCRYPTION_KEY nor "
            "DEEPER_NOTEBOOK_ENCRYPTION_KEYS is set. "
            "API key encryption will fail until one is configured. "
            "Set DEEPER_NOTEBOOK_ENCRYPTION_KEY=<secret> for a single "
            "key, or DEEPER_NOTEBOOK_ENCRYPTION_KEYS=<new>,<old> for "
            "rotation."
        )

    # Run database migrations

    try:
        migration_manager = AsyncMigrationManager()
        current_version = await migration_manager.get_current_version()
        logger.info(f"Current database version: {current_version}")

        if await migration_manager.needs_migration():
            logger.warning("Database migrations are pending. Running migrations...")
            await migration_manager.run_migration_up()
            new_version = await migration_manager.get_current_version()
            logger.success(
                f"Migrations completed successfully. Database is now at version {new_version}"
            )
        else:
            logger.info(
                "Database is already at the latest version. No migrations needed."
            )
    except Exception as e:
        logger.error(f"CRITICAL: Database migration failed: {str(e)}")
        logger.exception(e)
        # Fail fast - don't start the API with an outdated database schema
        raise RuntimeError(f"Failed to run database migrations: {str(e)}") from e

    await _reconcile_source_search_index_maintenance_at_startup()

    # The unified engine is strictly optional. Resolve it after durable schema
    # preparation and before legacy services so one coordinator can be injected
    # into both legacy projection paths without altering their availability.
    (
        knowledge_shadow_coordinator,
        knowledge_backfill_task,
    ) = await _start_knowledge_engine(app)
    await _start_knowledge_navigation(
        app,
        engine_service=getattr(app.state, "knowledge_engine_service", None),
    )

    # App-owned overlay startup is isolated from the rest of the API. The
    # canonical filesystem root is never exposed through app state or routes.
    overlay_service = None
    app.state.overlay_service = None
    try:
        from deeper_notebook.overlay.paths import OverlayLayout
        from deeper_notebook.overlay.repository import OverlayRepository
        from deeper_notebook.overlay.service import OverlayService
        from deeper_notebook.overlay.storage import OverlayStorage

        overlay_service = OverlayService(
            OverlayRepository(),
            OverlayStorage(OverlayLayout.active()),
            shadow_projector=knowledge_shadow_coordinator,
        )
        app.state.overlay_service = overlay_service
    except Exception as exc:
        logger.warning(
            "Overlay startup unavailable ({})",
            type(exc).__name__,
        )

    # Vault indexing begins only after migrations are durable. Unavailable roots
    # are contained by the service so local API startup remains available.
    vault_service = None
    vault_scan_task = None
    try:
        from deeper_notebook.vault.repository import VaultRepository
        from deeper_notebook.vault.service import VaultService

        vault_service = VaultService(
            VaultRepository(),
            shadow_projector=knowledge_shadow_coordinator,
        )
        app.state.vault_service = vault_service
        await vault_service.start_watchers()
        vault_scan_task = _track_task(
            asyncio.create_task(
                vault_service.scan_dirty_mounts(), name="vault-initial-dirty-scan"
            )
        )
    except Exception as exc:
        logger.warning(
            "Vault read-only index startup unavailable ({})", type(exc).__name__
        )

    # Run podcast profile data migration (legacy strings -> Model registry)
    try:
        from deeper_notebook.podcasts.migration import migrate_podcast_profiles

        await migrate_podcast_profiles()
    except Exception as e:
        logger.warning(f"Podcast profile migration encountered errors: {e}")
        # Non-fatal: profiles can be migrated manually via UI

    # v0.7.85 — one-shot legacy edge deduplicator. New duplicate edges are
    # already impossible to create thanks to the v0.7.60 + v0.7.73
    # idempotency fixes, but databases that ran older versions still
    # carry orphan duplicates that inflate source_count/note_count and
    # require multiple unlink clicks to actually remove a source. This
    # runs once per startup, is idempotent (clean DB → no-op), and is
    # non-fatal if it fails (we'll retry next boot).
    try:
        from deeper_notebook.database.dedup_edges import dedupe_legacy_edges

        await dedupe_legacy_edges()
    except Exception as e:
        logger.warning(f"Edge deduplication encountered errors (non-fatal): {e}")

    # v0.7.172 — Stale-command reaper. If the surreal-commands worker
    # crashed / was OOM-killed mid-job, the command row stays
    # `status="running"` (or "new" / "queued") and the corresponding
    # `source.command` pointer keeps the source stuck in a polling
    # loop on the frontend. `useSourceStatus` polls every 2 seconds
    # while status ∈ {new, queued, running} — silent CPU + DB load,
    # forever, with no path to recovery short of manual SurrealQL.
    #
    # On API restart we KNOW the worker isn't still mid-job
    # (workers and the API share a process tree under the launcher),
    # so any pre-restart row in a not-terminal state is stale. Mark
    # them all failed with a clear error message so the frontend
    # stops polling and the user can re-trigger via the existing
    # `useRetrySource` / retry-podcast paths.
    #
    # The 30-minute updated-time filter is belt-and-suspenders: in
    # the extremely unlikely case that a worker IS still running an
    # older job at the moment of restart (e.g. cross-process
    # supervision in a future deployment), we don't wipe its
    # status — only stale work older than 30m. For the desktop
    # launcher's process-tree model this is overkill but cheap.
    try:
        from deeper_notebook.database.repository import repo_query

        reaped = await repo_query(
            "UPDATE command "
            "SET status = 'failed', "
            "    error_message = $msg, "
            "    updated = time::now() "
            "WHERE status IN ['new', 'queued', 'running'] "
            "  AND updated < (time::now() - 30m) "
            "RETURN id",
            {
                "msg": (
                    "Marked stale on API restart — the worker did not "
                    "finish before restart. Re-trigger the operation if "
                    "needed."
                ),
            },
        )
        if reaped:
            logger.warning(
                "Reaped {} stale command row(s) left in new/queued/running "
                "state from a previous run. They've been marked failed so "
                "the frontend stops polling.",
                len(reaped),
            )
        else:
            logger.debug("No stale command rows to reap")
    except Exception as e:
        # Non-fatal — the API can still serve traffic with stale rows
        # around; the next worker startup will likely sort them out.
        logger.warning(
            "Stale-command reaper failed (non-fatal): {}",
            e,
        )

    # v0.7.210 — Periodic stale-command reaper. The startup pass
    # above only catches rows orphaned by the LAST shutdown; if the
    # worker dies mid-day (OOM, llama.cpp crash) while the API stays
    # up, the orphaned rows linger as "running" forever and the
    # frontend polls them until the next full API restart.
    #
    # Schedule a 5-minute loop that runs the same query. Cancelled
    # cleanly on shutdown.
    async def _reaper_loop() -> None:
        from deeper_notebook.database.repository import repo_query as _rq

        while True:
            try:
                await asyncio.sleep(300)  # 5 minutes
                rows = await _rq(
                    "UPDATE command "
                    "SET status = 'failed', "
                    "    error_message = $msg, "
                    "    updated = time::now() "
                    "WHERE status IN ['new', 'queued', 'running'] "
                    "  AND updated < (time::now() - 30m) "
                    "RETURN id",
                    {
                        "msg": (
                            "Marked stale by periodic reaper — the worker "
                            "did not progress this row in 30+ minutes. "
                            "Re-trigger the operation if needed."
                        ),
                    },
                )
                if rows:
                    logger.warning(
                        "Periodic reaper: marked {} stale command row(s) failed",
                        len(rows),
                    )
            except asyncio.CancelledError:
                logger.debug("Periodic reaper cancelled at shutdown")
                raise
            except Exception as exc:
                # Never crash the loop — log and try again next tick.
                logger.warning(
                    "Periodic reaper iteration failed (non-fatal): %s",
                    exc,
                )

    reaper_task: asyncio.Task | None = None
    try:
        # v0.7.190 — _track_task anchors in the module-level
        # _BACKGROUND_TASKS set so the GC doesn't reap the loose
        # task. Same pattern as the digest scheduler / checkpoint
        # prune loops below.
        reaper_task = _track_task(
            asyncio.create_task(
                _reaper_loop(),
                name="periodic_stale_command_reaper",
            )
        )
        logger.info("Started periodic stale-command reaper (every 5m)")
    except Exception as exc:
        logger.warning("Could not start periodic reaper: %s", exc)

    # ONP v0.6.1 — Start Gmail digest scheduler background task.
    # The scheduler wakes every 5 minutes, checks GmailIntegration state, and
    # fires daily/weekly digests when due. Non-fatal if it fails to start —
    # users can still trigger digests manually from the UI.
    digest_stop_event: asyncio.Event = asyncio.Event()
    digest_scheduler_task: asyncio.Task | None = None
    try:
        from deeper_notebook.digest.scheduler import run_forever as _digest_run_forever

        # v0.7.190 — wrap in _track_task so a future refactor that
        # loses the local-var anchor doesn't silently allow GC.
        digest_scheduler_task = _track_task(
            asyncio.create_task(
                _digest_run_forever(digest_stop_event),
                name="onp-digest-scheduler",
            )
        )
        logger.info("Digest scheduler task started")
    except Exception as e:
        logger.warning(f"Failed to start digest scheduler (non-fatal): {e}")

    # v0.7.44 — warm the DB connection pool before serving traffic.
    # The pool grows lazily on first `_acquire`, so the FIRST chat turn
    # after launch pays a ~150-300ms SurrealDB WS handshake before the
    # graph even runs. ContextBuilder then fans out to dozens of repo
    # queries; with 4 cold slots, the first 4 concurrent calls each
    # pay the handshake too. Prefilling 2 slots eliminates this on the
    # critical first-impression path. Further growth stays lazy.
    #
    # v0.7.52 — bound each acquire with asyncio.wait_for(timeout=10).
    # Previously a SurrealDB that came up cold or got stuck mid-handshake
    # could block the lifespan handler indefinitely — the API would
    # appear "starting" forever with no /readyz reachability. 10 s is
    # generous: a healthy SurrealDB handshake takes ~200 ms; anything
    # past 5 s already indicates trouble. We log the timeout and move
    # on (degrades to lazy-warmup, same as the pre-0.7.44 behavior).
    try:
        from deeper_notebook.database.repository import (
            _db_pool_size,
            _release,
        )

        warmup_n = min(2, _db_pool_size())
        warm_conns = []
        for _ in range(warmup_n):
            # v0.7.134 — _warmup_pool_acquire_with_retry retries each
            # acquire up to 3 times with exponential backoff before
            # giving up. Outer except clauses unchanged: timeout-after-
            # all-retries still distinguishes from generic-failure-after-
            # all-retries for log clarity.
            try:
                warm_conns.append(await _warmup_pool_acquire_with_retry())
            except asyncio.TimeoutError:
                logger.warning(
                    "DB pool warmup acquire timed out after all retries "
                    "({} attempts × 10s) — skipping remaining warmup, "
                    "falling back to lazy initialization on first request",
                    len(_WARMUP_RETRY_DELAYS_S),
                )
                break
            except Exception as exc:
                # Pool warmup is best-effort — a failure here shouldn't
                # prevent boot. The user can still recover via the
                # /readyz probe + a manual retry.
                logger.warning(
                    "DB pool warmup acquire failed after all retries: {}",
                    exc,
                )
                break
        for c in warm_conns:
            await _release(c)
        if warm_conns:
            logger.info(
                "DB pool pre-warmed with {} idle connection(s)",
                len(warm_conns),
            )
    except Exception as exc:
        logger.warning("DB pool warmup encountered an error: {}", exc)

    # v0.7.125 — LangGraph SQLite checkpoint pruning. Without this,
    # ~/.deeper-notebook/data/sqlite-db/checkpoints.sqlite grows
    # unbounded — every chat turn appends rows that LangGraph never
    # reads again (it only queries the latest checkpoint per thread
    # when resuming). After a year of moderate use on a single-user
    # install, the file is hundreds of MB. The prune loop keeps the
    # N most recent checkpoints per thread (default 50) and runs
    # every DEEPER_NOTEBOOK_CHECKPOINT_PRUNE_INTERVAL_HOURS (default 24).
    # Non-fatal if it fails to start — chat still works, just grows.
    checkpoint_prune_stop_event: asyncio.Event = asyncio.Event()
    checkpoint_prune_task: asyncio.Task | None = None
    try:
        from deeper_notebook.utils.checkpoint_prune import (
            run_prune_loop as _checkpoint_prune_loop,
        )

        # v0.7.190 — _track_task anchor (see digest scheduler above).
        checkpoint_prune_task = _track_task(
            asyncio.create_task(
                _checkpoint_prune_loop(checkpoint_prune_stop_event),
                name="onp-checkpoint-prune",
            )
        )
        logger.info("LangGraph checkpoint-prune task started")
    except Exception as e:
        logger.warning(
            f"Failed to start checkpoint-prune task (non-fatal): {e}",
        )

    # v0.7.157 — Pre-warm the GmailIntegration TTL cache. The frontend
    # polls /api/onp/gmail/status on mount; first cold call against the
    # singleton SurrealDB record takes 4-8s (slow-query warnings on
    # every fresh launch). Paying that cost ONCE here, in a background
    # task that doesn't block /readyz, means the user's page-load
    # poll hits a populated 30s cache instead of waiting on the DB.
    # If the warmup itself fails or times out, the next user request
    # will retry on cache miss — same as without this warmup, just
    # one user request slower.
    async def _prewarm_gmail_cache() -> None:
        try:
            from deeper_notebook.domain.gmail import GmailIntegration

            await GmailIntegration.get()
        except Exception as e:
            logger.debug(f"Gmail cache pre-warm failed (non-fatal): {e}")

    # v0.7.165 — Hold a strong reference to the task so Python's
    # asyncio event loop can't GC it before it runs. The fire-and-
    # forget `asyncio.create_task(...)` pattern only keeps a weak
    # ref in the loop, which is documented foot-gun: under 3.11+ with
    # GC pressure, a task that yields control immediately (e.g. our
    # `await GmailIntegration.get()` which awaits a SurrealDB roundtrip)
    # can be collected before it resumes — silently dropping the
    # pre-warm. The other two create_task calls in this lifespan
    # (digest_scheduler_task, checkpoint_prune_task) already assign
    # to local variables and cancel cleanly on shutdown; this one
    # was the outlier. Now matches that pattern.
    # v0.7.190 — _track_task anchor (see digest scheduler above).
    # Belt-and-suspenders: gmail_prewarm_task is also held by the
    # closure for await/cancel below.
    gmail_prewarm_task = _track_task(
        asyncio.create_task(
            _prewarm_gmail_cache(),
            name="onp-gmail-prewarm",
        )
    )
    logger.info("Gmail TTL-cache pre-warm task scheduled")

    logger.success("API initialization completed successfully")

    # Yield control to the application
    yield

    _clear_knowledge_navigation_service(app)
    await _stop_knowledge_engine(app, knowledge_backfill_task)

    if overlay_service is not None:
        app.state.overlay_service = None

    if vault_service is not None:
        try:
            await vault_service.stop_watchers()
        except Exception as exc:
            logger.warning("Vault observer shutdown raised ({})", type(exc).__name__)
        finally:
            app.state.vault_service = None

    if vault_scan_task is not None and not vault_scan_task.done():
        vault_scan_task.cancel()
        try:
            await vault_scan_task
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning(
                "Vault initial scan shutdown raised ({})", type(exc).__name__
            )

    # v0.7.165 — Cancel the gmail-prewarm task on shutdown if it's
    # still running. The task is short-lived (a single SurrealDB read)
    # and almost always completes before shutdown, but if a slow
    # SurrealDB held it open we'd otherwise leak it past the lifespan
    # tear-down. wait_for(2s) is generous for a single-record fetch.
    if not gmail_prewarm_task.done():
        try:
            await asyncio.wait_for(gmail_prewarm_task, timeout=2)
        except asyncio.TimeoutError:
            gmail_prewarm_task.cancel()
            try:
                await gmail_prewarm_task
            except (asyncio.CancelledError, Exception):
                pass

    # v0.7.210 — Stop the periodic stale-command reaper. It sleeps
    # in a 5-minute loop so a cancel needs to be propagated rather
    # than waiting; just cancel + suppress.
    if reaper_task is not None and not reaper_task.done():
        reaper_task.cancel()
        try:
            await reaper_task
        except (asyncio.CancelledError, Exception):
            pass

    # v0.7.125 — Stop the checkpoint-prune task FIRST (it's the most
    # likely to be mid-sleep so cancelling is quick), then the digest
    # scheduler. Both use the same wait_for(timeout=10) + cancel
    # fallback pattern as v0.6.1.
    if checkpoint_prune_task is not None:
        logger.info("Signalling checkpoint-prune task to stop...")
        checkpoint_prune_stop_event.set()
        try:
            await asyncio.wait_for(checkpoint_prune_task, timeout=10)
            logger.info("Checkpoint-prune task stopped cleanly")
        except asyncio.TimeoutError:
            logger.warning("Checkpoint-prune task did not stop in 10s — cancelling")
            checkpoint_prune_task.cancel()
            try:
                await checkpoint_prune_task
            except (asyncio.CancelledError, Exception):
                pass

    # Shutdown: signal the digest scheduler to stop and wait for it briefly.
    if digest_scheduler_task is not None:
        logger.info("Signalling digest scheduler to stop...")
        digest_stop_event.set()
        try:
            await asyncio.wait_for(digest_scheduler_task, timeout=10)
            logger.info("Digest scheduler stopped cleanly")
        except asyncio.TimeoutError:
            logger.warning("Digest scheduler did not stop in 10s — cancelling")
            digest_scheduler_task.cancel()
            try:
                await digest_scheduler_task
            except (asyncio.CancelledError, Exception):
                pass
        except Exception as e:
            logger.warning(f"Digest scheduler exit raised: {e}")

    # Durable source-search maintenance is drained before its database pool is
    # closed. A bounded timeout retains its fixed marker for next-startup
    # reconciliation rather than claiming a forced kill is durable completion.
    await _close_database_pool_after_source_search_index_maintenance()

    # v0.7.211 — Close the AsyncSqliteSaver connections that back
    # /chat and /source/chat streaming. Previously these aiosqlite
    # connections (+ their background threads) leaked past the
    # FastAPI shutdown — harmless on POSIX, but the SQLite file
    # stayed locked on Windows and the FD count crept up over
    # hundreds of relaunches. Idempotent + safe-on-never-
    # constructed; both helpers no-op if their graph was never
    # used this session.
    try:
        from deeper_notebook.graphs.chat import close_async_graph

        await close_async_graph()
        logger.debug("AsyncSqliteSaver (chat) closed")
    except Exception as e:
        logger.warning(f"Closing chat AsyncSqliteSaver raised: {e}")
    try:
        from deeper_notebook.graphs.source_chat import (
            close_async_source_chat_graph,
        )

        await close_async_source_chat_graph()
        logger.debug("AsyncSqliteSaver (source_chat) closed")
    except Exception as e:
        logger.warning(f"Closing source_chat AsyncSqliteSaver raised: {e}")

    logger.info("API shutdown complete")


app = FastAPI(
    title=PRODUCT_NAME,
    description=DESCRIPTION,
    lifespan=lifespan,
)

if CORS_IS_DEFAULT_WILDCARD:
    logger.warning(
        "CORS_ORIGINS is not set — API accepts cross-origin requests from any "
        "origin (default: '*'). For production deployments, set CORS_ORIGINS to "
        "your frontend origin(s), e.g. "
        "CORS_ORIGINS=https://notebook.example.com"
    )
else:
    logger.info(f"CORS allowed origins: {CORS_ALLOWED_ORIGINS}")

# v0.7.121 — Escalate from WARNING to ERROR-level log when the user has
# the DANGEROUS combination: CORS=* AND no password set. In that state
# *any* origin on the internet can hit the API with credential-less
# requests and read every notebook/source/note. The password
# middleware short-circuits at startup if `DEEPER_NOTEBOOK_PASSWORD` is
# unset (auth becomes a no-op), so CORS=* + no-password = open API
# wide open to the world. This is a foot-gun the README warns about
# but it's worth surfacing at process boot too — operators tail logs.
_password_is_set = bool(
    resolve_env("DEEPER_NOTEBOOK_PASSWORD", getter=get_secret_from_env)
)
# v0.7.154 — Severity downgrade: ERROR → WARNING for the desktop fork.
# The desktop launcher binds the API to 127.0.0.1 ONLY (see
# desktop/launcher.py:_spawn_api `--host 127.0.0.1`), so "anyone with
# the API URL" can never be reached from outside the local machine.
# The previous ERROR level was correct for the multi-user Docker
# deployment path it was written for, but on every desktop launch it
# fires once at startup and gets indexed by any log-aggregation tooling
# as a critical failure — drowning out actual ERROR-level events the
# operator needs to see. Downgraded to WARNING so it still appears in
# logs (the message itself is unchanged) without false-flagging the
# default desktop configuration as a security incident.
if CORS_IS_DEFAULT_WILDCARD and not _password_is_set:
    logger.warning(
        "⚠️ DANGEROUS CONFIG: CORS_ORIGINS='*' AND DEEPER_NOTEBOOK_PASSWORD is "
        "unset. Any origin can call this API without credentials. ANYONE "
        "with the API URL can read/write every notebook. This is fine ONLY "
        "for local development (desktop fork binds to 127.0.0.1, so this "
        "is the expected state). For ANY exposed deployment (Docker, "
        "Kubernetes, public IP), set BOTH: "
        "CORS_ORIGINS=https://your-frontend.example.com AND "
        "DEEPER_NOTEBOOK_PASSWORD=<strong-password>."
    )

# Middleware order matters — Starlette wraps in REVERSE order of registration.
# The OUTERMOST middleware (first to see request, last to see response) is
# the one added LAST. So the call chain on a request looks like:
#
#   request → CORS → RequestID → SecurityHeaders → GZip → PasswordAuth → handler
#                                                                              ↓
#   response ← CORS ← RequestID ← SecurityHeaders ← GZip ← PasswordAuth ← handler
#
# Rationale:
#  - PasswordAuth FIRST registered → innermost → only authenticated requests
#    flow through the rest. Saves CPU on unauthed traffic.
#  - GZip wraps PasswordAuth so 401 / 403 bodies also compress.
#  - SecurityHeaders wraps GZip so headers land on every response including
#    GZip's pre-encoded ones.
#  - RequestID wraps SecurityHeaders so every log line + the
#    `X-Request-ID` response header carries the same id even when an
#    auth failure short-circuits early.
#  - CORS is registered LAST → outermost → processes preflight OPTIONS
#    requests before they hit PasswordAuth (which would 401 them).

app.add_middleware(
    PasswordAuthMiddleware,
    excluded_paths=[
        "/",
        "/health",
        "/livez",
        "/readyz",
        "/api/readyz",
        "/healthz/deep",  # v0.7.112 — operators need to poll without auth
        # v0.7.148 — frontend reaches /healthz/deep through Next.js's /api/*
        # rewrite (frontend builds resolve `apiUrl` to a path that the
        # ApiClient interceptor still routes through /api), so the request
        # arrives here as `/api/healthz/deep`. Without this exemption +
        # the alias route below, the Setup Wizard's poll returns 404 and
        # hangs on "Loading..." indefinitely. See incident on 2026-05-20.
        "/api/healthz/deep",
        # v0.8.40d — launcher → API env-refresh has its own auth via
        # the DEEPER_NOTEBOOK_LAUNCHER_CONTROL_TOKEN bearer header (the
        # same secret the launcher control plane uses, scoped to the
        # parent↔child trust boundary). The launcher doesn't have the
        # user-facing DEEPER_NOTEBOOK_PASSWORD so it can't satisfy the
        # password middleware — the endpoint enforces its own typed
        # auth instead.
        "/api/system/env-refresh",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/auth/status",
        "/api/config",
        "/api/version",  # v0.7.210 — launch splash polls before auth
        "/api/local-models/health",  # v0.8.0 — launch splash polls before auth
        "/metrics",  # v0.7.124 — Prometheus scrapes without auth
    ],
)

# v0.7.120 — gzip compression. Bodies ≥ 1000 bytes get compressed when
# the client sends `Accept-Encoding: gzip` (every modern browser + httpx
# does). Smaller bodies skip compression — the overhead exceeds the
# savings for short payloads.
#
# v0.8.66 (audit H1) — but GZip must NOT wrap the streaming endpoints.
# Starlette 0.50.0's GZipMiddleware only exempts content-type
# `text/event-stream`; our token streams use `application/x-ndjson`
# (/chat/stream) and `text/plain` (/search/ask, source-chat /messages),
# so they would be compressed. `minimum_size` doesn't help — its
# short-circuit only fires for non-streaming (`not more_body`) responses;
# streaming chunks always carry `more_body=True` and get compressed per
# chunk (compresslevel=9, no Z_SYNC_FLUSH), so most token chunks are held
# back until a gzip frame flushes — defeating the real-time per-token
# delivery the streaming UX (v0.7.38/42/43) is built on, and delaying
# `is_disconnected()`. The SSE/NDJSON per-event payloads are tiny, so
# streaming them uncompressed costs almost nothing while GZip is retained
# for the large JSON CRUD responses it was added for.
_NO_GZIP_PREFIXES = ("/api/chat/stream", "/api/search/ask")


def _is_streaming_path(scope: Scope) -> bool:
    path = scope.get("path", "")
    if path.startswith(_NO_GZIP_PREFIXES):
        return True
    # Source-chat streams via POST to …/chat/sessions/{id}/messages. Gate on
    # POST so a future GET message-list can still be gzipped.
    if path.endswith("/messages") and scope.get("method") == "POST":
        return True
    return False


class SelectiveGZipMiddleware(GZipMiddleware):
    """GZipMiddleware that bypasses itself entirely for streaming endpoints,
    so their token chunks flush in real time instead of buffering inside the
    per-chunk gzip compressor."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and _is_streaming_path(scope):
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)


app.add_middleware(SelectiveGZipMiddleware, minimum_size=1000)

# v0.7.120 — defense-in-depth security headers. X-Content-Type-Options,
# X-Frame-Options, Referrer-Policy, CSP (skipped on /docs paths).
app.add_middleware(SecurityHeadersMiddleware)

# v0.7.124 — Prometheus request-timing capture. Records every request's
# method + route + status_code + duration into the metrics module's
# counter + histogram, exposed at /metrics. /metrics itself is
# excluded from capture so scrapes don't show up as user traffic.
app.add_middleware(PrometheusMetricsMiddleware)

# v0.7.120 — request-ID correlation. Generates (or accepts) a UUID4
# per request, binds it into loguru context, surfaces as
# X-Request-ID response header. Lets operators grep a single request
# across the codebase's log files.
app.add_middleware(RequestIDMiddleware)

# v0.8.66 (audit S-4) — env-gated rate limiter. Registered just BEFORE CORS, so
# CORS stays outermost (preflight OPTIONS bypass) while rate-limiting still runs
# BEFORE PasswordAuth — catching auth brute-force + download/discover
# cost-amplification. DEFAULT OFF (DEEPER_NOTEBOOK_RATE_LIMIT_PER_MIN unset/0) → zero change
# to the single-user local-first desktop path.
app.add_middleware(RateLimitMiddleware)

# CORS is OUTERMOST so it sees preflight OPTIONS before any other
# middleware short-circuits them.
#
# v0.7.209 — `allow_credentials=True` combined with
# `allow_origins=["*"]` is a contract the browser silently drops
# (Fetch spec disallows credentials when the response advertises a
# wildcard origin). The runtime then ends up sending
# `Access-Control-Allow-Origin: *` AND
# `Access-Control-Allow-Credentials: true`, which Chromium / Firefox
# treat as an error and refuse the response. Make the contract
# honest: when we're in the default-wildcard mode, allow_credentials
# is False (matches what the browser would actually permit). Users
# who explicitly set `CORS_ORIGINS=https://foo.example.com` keep
# the credentialed-CORS behaviour they configured for.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=not CORS_IS_DEFAULT_WILDCARD,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Custom exception handler to ensure CORS headers are included in error responses
# This helps when errors occur before the CORS middleware can process them
@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Custom exception handler that ensures CORS headers are included in error responses.
    This is particularly important for 413 (Payload Too Large) errors during file uploads.

    Note: If a reverse proxy (nginx, traefik) returns 413 before the request reaches
    FastAPI, this handler won't be called. In that case, configure your reverse proxy
    to add CORS headers to error responses.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers={**(exc.headers or {}), **_cors_headers(request)},
    )


@app.exception_handler(NotFoundError)
async def not_found_error_handler(request: Request, exc: NotFoundError):
    return JSONResponse(
        status_code=404,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(InvalidInputError)
async def invalid_input_error_handler(request: Request, exc: InvalidInputError):
    return JSONResponse(
        status_code=400,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError):
    return JSONResponse(
        status_code=401,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(RateLimitError)
async def rate_limit_error_handler(request: Request, exc: RateLimitError):
    return JSONResponse(
        status_code=429,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(ConfigurationError)
async def configuration_error_handler(request: Request, exc: ConfigurationError):
    return JSONResponse(
        status_code=422,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(NetworkError)
async def network_error_handler(request: Request, exc: NetworkError):
    return JSONResponse(
        status_code=502,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(ExternalServiceError)
async def external_service_error_handler(request: Request, exc: ExternalServiceError):
    return JSONResponse(
        status_code=502,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


@app.exception_handler(DeeperNotebookError)
async def deeper_notebook_error_handler(
    request: Request,
    exc: DeeperNotebookError,
):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers=_cors_headers(request),
    )


# Include routers
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(config.router, prefix="/api", tags=["config"])
app.include_router(notebooks.router, prefix="/api", tags=["notebooks"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(models.router, prefix="/api", tags=["models"])
app.include_router(transformations.router, prefix="/api", tags=["transformations"])
app.include_router(notes.router, prefix="/api", tags=["notes"])
app.include_router(
    deeper_notebook.router,
    prefix="/api/deeper-notebook",
    tags=["deeper-notebook"],
)
app.include_router(
    deeper_notebook.router,
    prefix="/api/onp",
    include_in_schema=False,
)
app.include_router(
    vault.router,
    prefix="/api/deeper-notebook",
    tags=["deeper-notebook-vault"],
)
app.include_router(
    overlay.router,
    prefix="/api/deeper-notebook",
    tags=["deeper-notebook-overlay"],
)
app.include_router(
    knowledge_engine.router,
    prefix="/api/deeper-notebook",
    tags=["deeper-notebook-knowledge-engine"],
)
app.include_router(
    knowledge_navigation.router,
    prefix="/api/deeper-notebook/knowledge",
    tags=["deeper-notebook-knowledge-navigation"],
)
app.include_router(
    knowledge_workspace.router,
    prefix="/api/deeper-notebook",
    tags=["deeper-notebook-workspace"],
)
app.include_router(
    gmail_router.router,
    prefix="/api/deeper-notebook",
    tags=["deeper-notebook-gmail"],
)
app.include_router(
    gmail_router.router,
    prefix="/api/onp",
    include_in_schema=False,
)
app.include_router(embedding.router, prefix="/api", tags=["embedding"])
app.include_router(
    embedding_rebuild.router, prefix="/api/embeddings", tags=["embeddings"]
)
app.include_router(settings.router, prefix="/api", tags=["settings"])
app.include_router(context.router, prefix="/api", tags=["context"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(source_visuals.router, prefix="/api", tags=["source-visuals"])
app.include_router(insights.router, prefix="/api", tags=["insights"])
app.include_router(commands_router.router, prefix="/api", tags=["commands"])
app.include_router(podcasts.router, prefix="/api", tags=["podcasts"])
# ONP v0.7.0 — Studio: one-shot upload + mode → notebook/podcast workflow
app.include_router(studio.router, prefix="/api", tags=["studio"])
app.include_router(episode_profiles.router, prefix="/api", tags=["episode-profiles"])
app.include_router(speaker_profiles.router, prefix="/api", tags=["speaker-profiles"])
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(source_chat.router, prefix="/api", tags=["source-chat"])
app.include_router(credentials.router, prefix="/api", tags=["credentials"])
app.include_router(languages.router, prefix="/api", tags=["languages"])
# v0.7.90 — Host filesystem listing/mkdir + notebook/note export endpoints.
# These let the frontend present a directory-picker UI and write notebook
# contents out to disk as markdown (folder or .zip).
app.include_router(filesystem.router, prefix="/api", tags=["filesystem"])
app.include_router(exports.router, prefix="/api", tags=["exports"])
app.include_router(evaluations.router, prefix="/api", tags=["evaluations"])
app.include_router(research.router, prefix="/api", tags=["research"])
app.include_router(capture.router, prefix="/api", tags=["capture"])
app.include_router(study.router, prefix="/api", tags=["study"])
app.include_router(study_plans.router, prefix="/api", tags=["study-plans"])
app.include_router(study_anki.router, prefix="/api", tags=["study-anki"])
# v0.8.97 — ExamLab: timed exam attempts over quiz artifacts.
app.include_router(study_exams.router, prefix="/api", tags=["study-exams"])
app.include_router(study_assistants.router, prefix="/api", tags=["study-assistants"])
app.include_router(study_voice.router, prefix="/api", tags=["study-voice"])
app.include_router(video_overviews.router, prefix="/api", tags=["video-overviews"])
app.include_router(
    _local_models_router.router, tags=["health"]
)  # v0.8.0 — local sidecar health; path already contains /api prefix
app.include_router(
    _mcp_router.router, tags=["mcp"]
)  # v0.8.0 Task 9 — MCP server registry CRUD; path already contains /api prefix
app.include_router(
    _launcher_prefs_router.router, tags=["launcher-prefs"]
)  # v0.8.6 Item D — launcher env-var preferences UI; path already contains /api prefix
app.include_router(
    _system_router.router, tags=["system"]
)  # v0.8.40d — launcher → API env push (n_ctx after hot-swap)
app.include_router(
    _updates_router.router, tags=["updates"]
)  # v0.8.70 — in-app update notifier
app.include_router(_runtime_router.router, tags=["runtime"])
from api.routers import audio_dictate as _audio_dictate_router

app.include_router(_audio_dictate_router.router, tags=["audio"])


@app.get("/")
async def root():
    return {
        "message": "Deeper Notebook API is running",
        "name": PRODUCT_NAME,
        "description": DESCRIPTION,
    }


@app.get("/health")
async def health():
    """Kept for backward compatibility — returns 200 if the process is up.

    Same shape as /livez. Existing dashboards and the launcher's wait
    loop point at /health; new code should use /livez (cheap) or
    /readyz (full dependency check)."""
    response = {
        "status": "healthy",
        "name": PRODUCT_NAME,
        "description": DESCRIPTION,
    }
    proof_revision = os.environ.get("DEEPER_NOTEBOOK_PROOF_REVISION")
    if (
        proof_revision is not None
        and re.fullmatch(r"[0-9a-f]{40}", proof_revision)
        and _checkout_head_revision() == proof_revision
    ):
        response["proof_revision"] = proof_revision
    return response


def _checkout_head_revision() -> str | None:
    """Read the loaded worktree's immutable HEAD without invoking a shell."""
    git_environment = os.environ.copy()
    for name in tuple(git_environment):
        if name in {
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_COMMON_DIR",
            "GIT_NAMESPACE",
        } or name.startswith("GIT_CONFIG_"):
            git_environment.pop(name, None)
    git_environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
            timeout=1,
            env=git_environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    revision = completed.stdout.strip()
    return (
        revision
        if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", revision)
        else None
    )


# v0.7.210 — Version endpoint. Drives the splash window's "Open
# Notebook Plus v0.7.X" badge + the About-dialog footer in the
# tray menu + support-ticket diagnostics. Cheap, no DB call,
# excluded from auth so the splash can hit it before the user
# enters credentials.
@app.get("/api/version")
async def api_version():
    """Return the bundled ONP version string + build metadata.

    The frontend's launch splash polls this to display the running
    version; ops dashboards / support requests can curl it to
    confirm what build the user is on without grepping logs.
    """
    try:
        # Import lazily so the test suite (which monkeypatches
        # `desktop` modules) doesn't pay the import cost on every
        # /api/version probe.
        from desktop import __version__ as desktop_version
    except Exception:
        desktop_version = "unknown"
    return {
        "version": desktop_version,
        "name": PRODUCT_NAME,
        "description": DESCRIPTION,
    }


# v0.7.15 — split liveness vs readiness so the user can actually
# diagnose "is the API up but stuck?" vs "is the DB unreachable?"
# without grepping logs. /livez: process is responding. /readyz:
# DB reachable + migrations applied. The launcher's progress UI and
# any external uptime checker should poll /readyz.
@app.get("/livez")
async def livez():
    """Liveness probe — the process is alive and serving HTTP.

    Intentionally trivial. Should return < 1ms. No DB call. If this
    fails, the process is wedged and needs a restart.
    """
    return {
        "status": "alive",
        "name": PRODUCT_NAME,
        "description": DESCRIPTION,
    }


@app.get("/readyz")
async def readyz():
    """Readiness probe — the API can serve real traffic.

    Checks:
      - SurrealDB reachable (2s timeout, via check_database_health)
      - Migrations applied (via AsyncMigrationManager)

    Returns 200 with detail on success, 503 on any failure so external
    pollers can distinguish "starting up" from "fully ready". The
    detail body always includes the same fields so a grep-friendly
    response shape stays consistent.
    """
    # Late-binding the imports keeps tests cheap (they can monkeypatch
    # without importing the whole api.main side-effect chain).
    from api.routers.config import check_database_health
    from deeper_notebook.database import async_migrate

    db_health = await check_database_health()
    db_status = db_health.get("status", "unknown")

    migrations_ok = False
    migrations_error: str | None = None
    pending_migrations = False
    try:
        manager = async_migrate.AsyncMigrationManager()
        pending_migrations = await manager.needs_migration()
        migrations_ok = not pending_migrations
    except Exception as exc:
        # v0.7.201 — was `str(exc)` returned inside the /readyz JSON
        # body. Migration exceptions can embed SurrealDB driver
        # frames, file paths from .surql migration files, and DB
        # DSN fragments. Log the full exception for operators; return
        # a generic placeholder to the probe response so the body is
        # safe to expose on a public health endpoint.
        migrations_error = "migrations check failed"
        logger.warning("readyz: migration check failed: {}", exc)

    ready = db_status == "online" and migrations_ok
    body = {
        "status": "ready" if ready else "not_ready",
        "name": PRODUCT_NAME,
        "description": DESCRIPTION,
        "checks": {
            "database": db_status,
            "database_error": db_health.get("error"),
            "migrations_applied": migrations_ok,
            "migrations_pending": pending_migrations,
            "migrations_error": migrations_error,
        },
    }
    status_code = 200 if ready else 503
    return JSONResponse(content=body, status_code=status_code)


@app.get("/api/readyz")
async def readyz_api_alias():
    """Alias for `/readyz` for clients whose base URL already ends in `/api`."""
    return await readyz()


# v0.7.112 — Deep healthcheck. /readyz checks only the must-have-to-serve-
# anything dependencies (DB + migrations); /healthz/deep additionally
# probes feature-tier dependencies (embedding model, chat model
# defaults, command worker) so an operator can answer "is search broken
# right now?" without grepping logs.
#
# Each subsystem reports independently — vector search being broken
# (no embedding model) doesn't make /healthz/deep itself return 503,
# because chat-only deployments are valid. The overall status is
# "healthy" if must-haves pass and "degraded" if any optional subsystem
# is missing/broken. Returns 200 unless a must-have fails (DB or
# migrations).
# v0.7.124 — Prometheus metrics endpoint. Returns the registry in
# the standard exposition format. Auth-exempt (operators / Prometheus
# scrapers poll without credentials). Excluded from the request-
# timing histogram itself (we don't want scrapes appearing as user
# traffic).
@app.get("/metrics")
async def metrics(request: Request):
    """v0.7.124 — Prometheus metrics exposition endpoint.

    Returns the global metric registry in the standard text format
    consumed by Prometheus / Grafana / Victoria Metrics / any
    OpenMetrics-compatible scraper.

    Metrics surfaced:
      - onp_http_requests_total{method, route, status_code}
      - onp_http_request_duration_seconds{method, route}
      - onp_db_query_duration_seconds
      - onp_db_slow_queries_total
      - onp_memory_recall_fallthrough_total{reason}
      - onp_memory_recall_duration_seconds
      - onp_studio_generations_total{mode, outcome}                  (v0.7.130)
      - onp_studio_outline_parse_failures_total{reason}              (v0.7.130)
      - onp_studio_single_note_fallbacks_total                       (v0.7.130)
      - (plus the default process_* + python_gc_* metrics from
        prometheus-client)

    Auth (v0.7.131 — Area for Review #19):
      - By default the endpoint is auth-exempt (still in
        PasswordAuthMiddleware excluded_paths) so a default install
        works with Prometheus out of the box.
      - Set DEEPER_NOTEBOOK_METRICS_AUTH_TOKEN=<random-secret> to require a
        bearer token: scrapers must send `Authorization: Bearer
        <token>`. The token is compared with `secrets.compare_digest`
        to keep timing-attacks out. The Authorization header is
        validated HERE inside the handler rather than via the
        general PasswordAuthMiddleware so the password ≠ the metrics
        scrape token (different rotation cadences, different
        operational owners).
      - Unset (the default) → no token check, behavior identical to
        v0.7.130 and earlier.

    Recommended scrape interval: 15s for high-traffic deployments,
    60s for single-user desktop installs.
    """
    import os
    import secrets

    from fastapi import HTTPException
    from fastapi.responses import Response

    from api.metrics import render_prometheus

    # v0.7.131 — optional token gate. Read at request time (not
    # startup) so operators can rotate the token via .env reload
    # without restarting the API. Cost is a single dict lookup;
    # negligible vs the actual metric rendering.
    expected_token = resolve_env("DEEPER_NOTEBOOK_METRICS_AUTH_TOKEN", "").strip()
    if expected_token:
        # Authorization parsing is intentionally strict — no
        # case-insensitive 'bearer ' match, no fallback to a query
        # string, no chained header. Prometheus + standard scrapers
        # all send the canonical form.
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Missing or malformed Authorization header",
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )
        provided = auth_header[len("Bearer ") :].strip()
        # Constant-time compare guards against timing-based oracle
        # attacks on the token value. compare_digest also handles
        # the length-mismatch case safely.
        if not secrets.compare_digest(provided, expected_token):
            raise HTTPException(
                status_code=401,
                detail="Invalid metrics token",
                headers={"WWW-Authenticate": 'Bearer realm="metrics"'},
            )

    body, content_type = render_prometheus()
    return Response(content=body, media_type=content_type)


@app.get("/healthz/deep")
async def healthz_deep(probe_providers: bool = False):
    """v0.7.112 — Deep dependency check.

    Probes each subsystem independently with a short timeout and
    reports per-feature status. Useful for:
      - Operators answering "is search broken?" / "is chat broken?"
      - First-launch wizards verifying setup before showing the
        notebook UI
      - Monitoring dashboards that need to differentiate "down" from
        "missing optional config"

    Response shape:
      {
        "status": "healthy" | "degraded" | "not_ready",
        "checks": {
          "database": {...},
          "migrations": {...},
          "embedding_model": {...},
          "chat_model": {...},
          "command_registry": {...},
          "upstream_providers": {...}   ← only when ?probe_providers=true
        }
      }

    Status codes:
      200 → healthy or degraded (some optional features unavailable)
      503 → not_ready (DB or migrations failed — nothing works)

    v0.7.132 — `?probe_providers=true` (Area for Review #12) enables
    a per-credential upstream probe via the existing connection_tester
    module. OFF by default because each probe burns one cheap API call
    against the provider (free in some plans, fractions of a cent in
    others), so we don't want a monitoring system hitting this every
    15 seconds and quietly running up the bill. Recommended cadence:
    once per minute at most. Each credential probe runs in parallel
    with a 5s timeout; a single slow provider doesn't gate the whole
    response. Failure modes (network error, quota exceeded, auth fail)
    are surfaced per-credential so operators see exactly which one is
    broken instead of a generic "providers degraded".
    """
    from api.routers.config import check_database_health
    from deeper_notebook.database import async_migrate

    checks: dict[str, dict] = {}
    # v0.7.202 — defensive defaults so `must_have_ok = checks["database"]
    # ["ok"] and checks["migrations"]["ok"]` below cannot KeyError
    # crash if either probe path raises before the assignment runs.
    # Without these, an exception inside the probe yields a 500 with
    # an unhelpful stack-trace body instead of the structured 503
    # health-check response the operator's probe-system expects.
    checks["database"] = {"ok": False, "status": "unknown"}
    checks["migrations"] = {"ok": False}

    # MUST-HAVE: Database
    db_health = await check_database_health()
    db_status = db_health.get("status", "unknown")
    checks["database"] = {
        "status": db_status,
        "ok": db_status == "online",
        "error": db_health.get("error"),
    }

    # MUST-HAVE: Migrations
    try:
        manager = async_migrate.AsyncMigrationManager()
        pending = await asyncio.wait_for(manager.needs_migration(), timeout=3.0)
        checks["migrations"] = {
            "status": "applied" if not pending else "pending",
            "ok": not pending,
            "error": None,
        }
    except asyncio.TimeoutError:
        checks["migrations"] = {
            "status": "timeout",
            "ok": False,
            "error": "needs_migration() took longer than 3s",
        }
    except Exception as exc:
        checks["migrations"] = {
            "status": "error",
            "ok": False,
            "error": str(exc),
        }

    # OPTIONAL: Embedding model (required for vector search + chat-with-sources)
    try:
        from deeper_notebook.ai.models import model_manager

        emb = await asyncio.wait_for(
            model_manager.get_embedding_model(),
            timeout=2.0,
        )
        checks["embedding_model"] = {
            "status": "configured" if emb else "missing",
            "ok": bool(emb),
            "error": None
            if emb
            else (
                "No default embedding model. Configure one in "
                "Settings → Models to enable vector search."
            ),
        }
    except asyncio.TimeoutError:
        checks["embedding_model"] = {
            "status": "timeout",
            "ok": False,
            "error": "embedding model lookup took longer than 2s",
        }
    except Exception as exc:
        checks["embedding_model"] = {
            "status": "error",
            "ok": False,
            "error": str(exc),
        }

    # OPTIONAL: Default chat model (required for /chat, /studio, /ask, etc.)
    try:
        from deeper_notebook.ai.models import model_manager

        chat = await asyncio.wait_for(
            model_manager.get_default_model("chat"),
            timeout=2.0,
        )
        checks["chat_model"] = {
            "status": "configured" if chat else "missing",
            "ok": bool(chat),
            "error": None
            if chat
            else (
                "No default chat model. Configure one in "
                "Settings → Models — without it, /chat, /studio, and "
                "/search/ask cannot generate responses."
            ),
        }
    except asyncio.TimeoutError:
        checks["chat_model"] = {
            "status": "timeout",
            "ok": False,
            "error": "chat model lookup took longer than 2s",
        }
    except Exception as exc:
        checks["chat_model"] = {
            "status": "error",
            "ok": False,
            "error": str(exc),
        }

    # OPTIONAL: Command registry (required for async embedding +
    # podcast generation + Studio extract). Importing the command
    # modules is the same check the routers do before submitting jobs;
    # if these imports fail, async jobs can't be queued.
    try:
        import commands.embedding_commands  # noqa: F401
        import commands.podcast_commands  # noqa: F401
        import commands.source_commands  # noqa: F401

        checks["command_registry"] = {
            "status": "loaded",
            "ok": True,
            "error": None,
        }
    except Exception as exc:
        checks["command_registry"] = {
            "status": "error",
            "ok": False,
            "error": (
                f"Failed to import command modules: {exc}. Async jobs "
                "(podcast generation, embeddings) will fail to queue. "
                "Check that the worker process is running."
            ),
        }

    # v0.7.132 — Optional: probe upstream providers (Area for Review
    # #12). Only runs when caller passes ?probe_providers=true, since
    # this burns one API call per credential. Each probe is its own
    # coroutine + 5s timeout, gathered with return_exceptions=True so
    # one bad provider doesn't gate the response.
    if probe_providers:
        upstream = await _probe_upstream_providers(timeout_seconds=5.0)
        checks["upstream_providers"] = upstream

    must_have_ok = checks["database"]["ok"] and checks["migrations"]["ok"]
    # v0.7.132 — `upstream_providers` is informational. A failing
    # provider knocks the overall to 'degraded' but doesn't flip to
    # 'not_ready'; an operator may have intentionally configured a
    # provider that's currently down (e.g., scheduled maintenance).
    all_ok = all(c["ok"] for c in checks.values())
    if not must_have_ok:
        overall = "not_ready"
        status_code = 503
    elif not all_ok:
        overall = "degraded"
        status_code = 200
    else:
        overall = "healthy"
        status_code = 200

    return JSONResponse(
        content={"status": overall, "checks": checks},
        status_code=status_code,
    )


# v0.7.148 — Alias route at `/api/healthz/deep`.
#
# The Setup Wizard's `useDeepHealth` hook polls `/healthz/deep` through
# the frontend's `apiClient`. `health.ts` correctly sets
# `baseURL: apiUrl` (without the `/api` suffix) so the path SHOULD reach
# the root-mounted handler above. In practice, the build pipeline +
# Next.js rewrites end up sending the request to the backend as
# `/api/healthz/deep`, which 404s, which leaves the wizard stuck on
# "Loading..." forever and blocks the user from progressing past the
# first launch screen (incident on 2026-05-20).
#
# Adding an alias on the backend is the most defensive and most
# backward-compatible fix: both `/healthz/deep` and `/api/healthz/deep`
# now resolve to the same probe. Existing operators / monitoring
# dashboards / curl recipes targeting the root path continue to work
# unchanged.
#
# `?probe_providers=` is passed through identically. The auth-middleware
# exempt-paths list above includes both paths.
@app.get("/api/healthz/deep")
async def healthz_deep_api_alias(probe_providers: bool = False):
    """Alias for `/healthz/deep` — same handler, accessible under `/api`."""
    return await healthz_deep(probe_providers=probe_providers)


# -------------------------------------------------------------------- #
# v0.7.132 — Upstream provider probing helper for /healthz/deep
#
# Lives outside the route handler so it can be called from tests in
# isolation and so the route handler stays readable.
# -------------------------------------------------------------------- #


async def _probe_upstream_providers(*, timeout_seconds: float = 5.0) -> dict:
    """Probe every configured Credential via connection_tester and
    return a dict suitable for inclusion in /healthz/deep.

    Returns shape:
      {
        "status": "ok" | "degraded" | "no_credentials" | "error",
        "ok": bool,
        "error": str | None,
        "credentials": [
          {
            "provider": "openai",
            "name": "My OpenAI Key",
            "ok": true,
            "message": "Connection successful",
          },
          ...
        ]
      }

    Edge cases handled:
      - No credentials configured at all → status='no_credentials',
        ok=True (it's a valid state, not an error)
      - Credential-list query failed → status='error', ok=False
      - One credential failed mid-probe → that entry has ok=False,
        but the overall response is still emitted with the others
    """
    from deeper_notebook.ai.connection_tester import test_provider_connection
    from deeper_notebook.domain.credential import Credential

    try:
        creds = await Credential.get_all()
    except Exception as exc:
        # We never want the probe to wedge the whole healthz response.
        return {
            "status": "error",
            "ok": False,
            "error": f"Could not list credentials: {exc}",
            "credentials": [],
        }

    if not creds:
        return {
            "status": "no_credentials",
            "ok": True,
            "error": None,
            "credentials": [],
        }

    async def _probe_one(cred) -> dict:
        # `test_provider_connection` accepts `config_id` to scope the
        # probe to a specific credential record (the same path used by
        # POST /credentials/{id}/test). Returns (success, message).
        #
        # Always-timeout pattern so a hung provider can't block the
        # whole probe. The tester has its own per-attempt timeouts but
        # the outer wait_for is the backstop.
        try:
            success, message = await asyncio.wait_for(
                test_provider_connection(
                    provider=cred.provider,
                    config_id=str(cred.id) if cred.id else None,
                ),
                timeout=timeout_seconds,
            )
            return {
                "provider": cred.provider,
                "name": cred.name,
                "ok": bool(success),
                "message": message,
            }
        except asyncio.TimeoutError:
            return {
                "provider": cred.provider,
                "name": cred.name,
                "ok": False,
                "message": f"Timed out after {timeout_seconds}s",
            }
        except Exception as exc:
            return {
                "provider": cred.provider,
                "name": cred.name,
                "ok": False,
                "message": f"Probe raised: {exc}",
            }

    # Run all probes in parallel.
    probe_results = await asyncio.gather(
        *(_probe_one(c) for c in creds),
        return_exceptions=False,  # _probe_one already catches everything
    )

    all_ok = all(p["ok"] for p in probe_results)
    return {
        "status": "ok" if all_ok else "degraded",
        "ok": all_ok,
        "error": None,
        "credentials": probe_results,
    }
