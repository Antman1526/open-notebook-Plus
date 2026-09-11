import asyncio
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TypeVar, Union

from loguru import logger
from surrealdb import AsyncSurreal, RecordID  # type: ignore

from deeper_notebook.environment import resolve_env

T = TypeVar("T", dict[str, Any], list[dict[str, Any]])


# ---------------------------------------------------------------------------
# v0.7.18 — SurrealDB connection pool
#
# Before v0.7.18, every repo_query / repo_create / etc. opened a fresh
# AsyncSurreal WebSocket, performed SCRAM auth, selected namespace +
# database, ran one query, and closed. A single chat turn fans out
# dozens of repo_query calls through ContextBuilder; on a local
# SurrealDB instance this cumulative handshake overhead is 50-200 ms
# per chat turn that we just don't need to pay.
#
# The pool design:
#   - asyncio.Queue holds idle, pre-authenticated AsyncSurreal clients.
#   - On acquire: pop the queue; if empty AND we haven't hit the cap,
#     spin up a new connection (lazy growth). If empty AND at cap,
#     block on the queue.
#   - On release: put the client back. If the client raised mid-query,
#     mark it dead and let the pool create a fresh one next time.
#   - Disable via DEEPER_NOTEBOOK_DB_POOL_DISABLED=1 for debugging or to fall back
#     to the old per-query behavior.
#   - Pool size via DEEPER_NOTEBOOK_DB_POOL_SIZE (default 4). Local use rarely
#     needs more; the chat graph + background workers usually share
#     less than 4 concurrent connections.
# ---------------------------------------------------------------------------

_DB_POOL_SIZE_DEFAULT = 4
_DB_POOL_MIN = 1
_DB_POOL_MAX = 32


def _db_pool_size() -> int:
    raw = resolve_env("DEEPER_NOTEBOOK_DB_POOL_SIZE")
    if not raw:
        return _DB_POOL_SIZE_DEFAULT
    try:
        val = int(raw)
        if val < _DB_POOL_MIN or val > _DB_POOL_MAX:
            logger.warning(
                f"DEEPER_NOTEBOOK_DB_POOL_SIZE={raw} outside [{_DB_POOL_MIN}, "
                f"{_DB_POOL_MAX}]; using default {_DB_POOL_SIZE_DEFAULT}"
            )
            return _DB_POOL_SIZE_DEFAULT
        return val
    except ValueError:
        logger.warning(
            f"DEEPER_NOTEBOOK_DB_POOL_SIZE={raw!r} not an int; using default "
            f"{_DB_POOL_SIZE_DEFAULT}"
        )
        return _DB_POOL_SIZE_DEFAULT


def _db_pool_disabled() -> bool:
    return resolve_env("DEEPER_NOTEBOOK_DB_POOL_DISABLED", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Module-level pool state. Lazily initialized on first acquire so the
# import doesn't try to talk to SurrealDB at module-load time.
_pool: Optional[asyncio.Queue] = None
_pool_lock: Optional[asyncio.Lock] = None
_pool_total: int = 0
_pool_cap: int = 0

# v0.8.66 (audit H6) — sentinel enqueued by a BROKEN release to wake a
# coroutine parked at cap on `await _pool.get()`. A broken release frees a
# creation slot (decrements `_pool_total`) WITHOUT putting a connection in the
# queue, so a parked acquirer would otherwise never wake even though capacity is
# now available — an unbounded hang (the exact "chatbot wedged until restart"
# class the pool exists to prevent). The sentinel means "a slot is free —
# create a new connection." It carries no `.close()`, so `close_pool`'s drain
# loop ignores it (the AttributeError is swallowed there).
_SLOT_FREED = object()


async def _ensure_pool_init() -> None:
    """Idempotent lazy init for the pool's asyncio primitives.

    Must be called from inside an event loop (the asyncio primitives
    require one). All pool acquires go through this path.
    """
    global _pool, _pool_lock, _pool_cap
    if _pool is not None and _pool_lock is not None:
        return
    _pool_cap = _db_pool_size()
    _pool = asyncio.Queue(maxsize=_pool_cap)
    _pool_lock = asyncio.Lock()


async def _new_connection() -> AsyncSurreal:
    """Open + authenticate a fresh AsyncSurreal connection."""
    db = AsyncSurreal(get_database_url())
    await db.signin(
        {
            "username": os.environ.get("SURREAL_USER"),
            "password": get_database_password(),
        }
    )
    await db.use(
        os.environ.get("SURREAL_NAMESPACE"),
        os.environ.get("SURREAL_DATABASE"),
    )
    return db


async def _acquire() -> AsyncSurreal:
    """Get a connection from the pool, growing the pool if needed.

    v0.7.24 — fixed a race: the previous version did
        if _pool_total < _pool_cap:
            conn = await _new_connection()   # ← await yields the loop!
            _pool_total += 1
    A concurrent release during that await landed a connection in the
    queue while we held the lock, but _pool_total still reflected only
    the pre-await count. After we incremented, total was correct, but
    the next release saw QueueFull and dropped a healthy connection
    onto the floor — slowly leaking pool capacity.

    Fix: reserve the slot under the lock BEFORE awaiting, so total is
    always >= queue.qsize() + checked-out connections. If
    _new_connection() raises, decrement back so the slot doesn't leak.
    """
    global _pool_total
    await _ensure_pool_init()
    assert _pool is not None and _pool_lock is not None
    # v0.8.66 (audit H6) — loop so a `_SLOT_FREED` sentinel (pulled from the
    # queue either on the fast path or after parking) routes us into the
    # reserve-and-create branch instead of being mistaken for a connection.
    while True:
        # Fast path: pull whatever is idle. A real connection → return it; a
        # _SLOT_FREED sentinel → fall through to reserve the freed slot.
        got_sentinel = False
        try:
            item = _pool.get_nowait()
            if item is not _SLOT_FREED:
                return item
            got_sentinel = True
        except asyncio.QueueEmpty:
            pass
        # Slow path: under the lock, reserve a slot then create.
        async with _pool_lock:
            if _pool_total < _pool_cap:
                _pool_total += 1
                reserved = True
            else:
                reserved = False
        if reserved:
            try:
                return await _new_connection()
            except Exception:
                # Connection creation failed — give back our reserved slot
                # so future acquires can try again. Otherwise total drifts
                # up and the pool gradually wedges.
                async with _pool_lock:
                    _pool_total -= 1
                raise
        # At cap. If we just consumed a sentinel but lost the slot race to a
        # concurrent acquirer, retry from the top. Otherwise park until a
        # release enqueues a connection OR a _SLOT_FREED signal.
        if got_sentinel:
            continue
        item = await _pool.get()
        if item is _SLOT_FREED:
            continue  # a broken release freed a slot — loop to reserve+create
        return item


async def _release(conn: AsyncSurreal, *, broken: bool = False) -> None:
    """Return a connection to the pool (or drop it if broken).

    v0.7.57 — every `_pool_total` mutation now happens under
    `_pool_lock`. The v0.7.24 acquire path moved the increment under
    the lock, but the two release paths (broken close + QueueFull
    overflow close) still decremented bare. Under concurrent broken
    releases two coroutines could read the same `_pool_total`, both
    write `total - 1`, and lose a decrement — total drifts UP from
    reality, eventually hitting `_pool_cap` and wedging every future
    acquire on `await _pool.get()`. Same root cause we fixed on the
    acquire side; same fix here.

    v0.7.62 — if the pool has already been closed (`close_pool` nulled
    out _pool while we still had a connection checked out), just close
    the connection here and bail. The previous code asserted
    `_pool is not None` which threw AssertionError mid-shutdown,
    turning a clean FastAPI lifespan exit into a noisy crash and
    leaking the underlying websocket to the OS.
    """
    global _pool_total
    if _pool is None or _pool_lock is None:
        try:
            await conn.close()
        except Exception:
            pass
        return
    if broken:
        try:
            await conn.close()
        except Exception:
            pass  # already broken; close failure is fine to swallow
        async with _pool_lock:
            _pool_total -= 1
        # v0.8.66 (audit H6) — we just freed a creation slot without enqueuing a
        # connection. If an acquirer is parked at cap on `_pool.get()`, wake it
        # with a sentinel so it can create a replacement; otherwise it would
        # hang forever despite the now-available capacity. We ONLY enqueue when
        # someone is actually waiting (asyncio.Queue hands a put straight to a
        # parked getter, so qsize stays 0 in that case) — when nobody waits we
        # must NOT leave a stray sentinel in the idle queue, since the next
        # acquire's reserve-and-create path already covers the freed slot and an
        # orphan sentinel would corrupt qsize-based bookkeeping (e.g. close_pool
        # and the broken-conn-dropped invariant).
        getters = getattr(_pool, "_getters", None)
        if getters:
            try:
                _pool.put_nowait(_SLOT_FREED)
            except asyncio.QueueFull:
                pass
        return
    try:
        _pool.put_nowait(conn)
    except asyncio.QueueFull:
        # Shouldn't happen — we never exceed the cap — but if it does,
        # close the extra rather than leak it.
        try:
            await conn.close()
        except Exception:
            pass
        async with _pool_lock:
            _pool_total -= 1


async def close_pool() -> None:
    """Close every connection in the pool. Call from API/launcher
    shutdown hooks so we exit cleanly.

    v0.7.62 — wait briefly for any checked-out connections to come
    back before nulling out state, then null. The previous version
    drained only the idle queue and immediately set `_pool = None`,
    which made every still-in-flight `_release(conn)` raise
    AssertionError. We now poll the checked-out count (`_pool_total -
    _pool.qsize()`) for up to ~2 s; remaining checkouts after that
    timeout are abandoned to the `_pool is None` guard inside
    `_release`, which closes the conn cleanly without touching pool
    state.
    """
    global _pool, _pool_lock, _pool_total
    if _pool is None:
        return
    # Drain any idle connections first.
    while True:
        try:
            conn = _pool.get_nowait()
        except asyncio.QueueEmpty:
            break
        try:
            await conn.close()
        except Exception:
            pass
    # Wait briefly for in-flight requests to release their checkouts.
    # 200 ms * 10 = 2 s total; if a request is still running past that,
    # the v0.7.62 _release guard handles its eventual close anyway.
    for _ in range(10):
        checked_out = _pool_total - _pool.qsize()
        if checked_out <= 0:
            break
        await asyncio.sleep(0.2)
        # Drain anything that came back during the sleep.
        while True:
            try:
                conn = _pool.get_nowait()
            except asyncio.QueueEmpty:
                break
            try:
                await conn.close()
            except Exception:
                pass
    _pool = None
    _pool_lock = None
    _pool_total = 0


async def _reset_pool_for_tests() -> None:
    """Test-only — drop the pool and force a fresh init on next use."""
    await close_pool()


def get_database_url():
    """Get database URL with backward compatibility.

    v0.7.6 — fixed a long-standing typo in the legacy fallback path:
    the previous version produced `ws://{address}/rpc:{port}` (port
    AFTER the path), which is not a valid WebSocket URL. SurrealDB's
    WebSocket endpoint is `ws://host:port/rpc` — port BEFORE path.
    Any deployment using SURREAL_ADDRESS + SURREAL_PORT without
    SURREAL_URL (legacy Docker setups, pre-2024 deploys) hit
    connection failures with cryptic URL-parse errors.

    The desktop bundle is unaffected because desktop/launcher.py
    always sets SURREAL_URL explicitly. This fix unblocks the
    documented backward-compat path.
    """
    surreal_url = os.getenv("SURREAL_URL")
    if surreal_url:
        return surreal_url

    # Fallback to old format - WebSocket URL format
    address = os.getenv("SURREAL_ADDRESS", "localhost")
    port = os.getenv("SURREAL_PORT", "8000")
    return f"ws://{address}:{port}/rpc"


def get_database_password():
    """Get password with backward compatibility"""
    return os.getenv("SURREAL_PASSWORD") or os.getenv("SURREAL_PASS")


def parse_record_ids(obj: Any) -> Any:
    """Recursively parse and convert RecordIDs into strings."""
    if isinstance(obj, dict):
        return {k: parse_record_ids(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [parse_record_ids(item) for item in obj]
    elif isinstance(obj, RecordID):
        return str(obj)
    return obj


def ensure_record_id(value: str | RecordID) -> RecordID:
    """Ensure a value is a RecordID."""
    if isinstance(value, RecordID):
        return value
    return RecordID.parse(value)


@asynccontextmanager
async def db_connection():
    """Acquire a SurrealDB connection.

    v0.7.18 — backed by a connection pool (see module-level pool docs).
    The interface is unchanged for callers — every `async with
    db_connection() as conn:` site continues to work — but the
    underlying client is now reused across calls, eliminating
    per-query handshake overhead. Set DEEPER_NOTEBOOK_DB_POOL_DISABLED=1 to
    fall back to the pre-pool per-query open/close behavior for
    debugging.

    On exception during the wrapped block, the connection is marked
    broken and dropped from the pool — the next acquire will create
    a fresh one. This protects against zombie connections that
    SurrealDB has closed server-side but the client doesn't know it.
    """
    if _db_pool_disabled():
        # Fallback path: behave like pre-v0.7.18 — open, use, close.
        db = await _new_connection()
        try:
            yield db
        finally:
            await db.close()
        return

    conn = await _acquire()
    broken = False
    try:
        yield conn
    except BaseException:
        # v0.8.65g — MUST be BaseException, not Exception. asyncio.CancelledError
        # is a BaseException (since 3.8), so the old `except Exception` MISSED
        # cancellation: when a query is cancelled (chat-stream client disconnect,
        # asyncio.wait_for timeout, route-handler cancel), the connection still
        # has a PENDING in-flight request, but `broken` stayed False and the
        # `finally` returned it to the pool as "healthy". The next acquirer's
        # query then collided with the stale response → KeyError(<uuid>) inside
        # the SurrealDB driver's _send/recv (async_ws.py) → e.g. the chat
        # model-record fetch (domain.base.get) failed and the whole chatbot
        # "stopped working" until restart, because the poisoned connection
        # lived on in the pool. Marking the connection broken on ANY abnormal
        # exit (incl. cancellation) closes + drops it; we only set the flag and
        # re-raise, so cancellation/exception semantics are unchanged.
        broken = True
        raise
    finally:
        await _release(conn, broken=broken)


def _is_retriable_conn_error(exc: BaseException) -> bool:
    """v0.8.66 (audit I-INFRA-1) — heuristic: does this look like a dead /
    idle-reaped pooled connection (SurrealDB closes idle WebSockets server-side)
    rather than a genuine query error? Deliberately CONSERVATIVE — only the
    socket-closed / connection-reset family — because it gates a retry, and we
    only ever retry read-only queries."""
    if isinstance(
        exc,
        (ConnectionError, ConnectionResetError, OSError, asyncio.IncompleteReadError),
    ):
        return True
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "connection closed",
            "connection reset",
            "connection is closed",
            "websocket",
            "going away",
            "broken pipe",
            "not connected",
        )
    )

_ORDERED_CLAUSE = re.compile(
    r"SELECT\s+(?!\*)(?!VALUE\b)([^;\"()]*?)\s+FROM\s+[A-Za-z_][\w:]*[^;\"()]*?\bORDER\s+BY\s+([A-Za-z_][\w.]*)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_projected_names(projection: str) -> set[str]:
    names: set[str] = set()
    for part in projection.split(","):
        part = part.strip()
        if not part:
            continue
        alias = re.split(r"\s+AS\s+", part, flags=re.IGNORECASE)
        names.add(alias[-1].strip().lower())
        names.add(alias[0].strip().lower())
    return names


def check_query_ordered_projection(query_str: str) -> str | None:
    """v0.8.128 — Runtime guard against SurrealDB 2.x 'Missing order idiom' rejections.

    SurrealDB 2.x rejects queries where an `ORDER BY field` is not in the
    SELECT projection. Returns a warning message if an unprojected ORDER BY
    field is detected, or None if valid.
    """
    if "SELECT" not in query_str.upper() or "ORDER" not in query_str.upper():
        return None

    for m in _ORDERED_CLAUSE.finditer(query_str):
        projection, order_field = m.group(1), m.group(2)
        # Skip aggregates or template braces
        if "count()" in projection.lower() or "{" in projection or "__" in projection:
            continue
        projected = _extract_projected_names(projection)
        order_lower = order_field.lower()
        # ORDER BY RAND() / count after GROUP BY are not projection lookups.
        if order_lower in ("rand", "count") or "group by" in query_str.lower():
            continue
        order_key = order_lower.split(".")[-1]
        order_root = order_lower.split(".")[0]
        if order_lower not in projected and order_key not in projected and order_root not in projected:
            return (
                f"repo_query: ORDER BY `{order_field}` is not in SELECT projection "
                f"`{projection.strip()[:80]}`. SurrealDB 2.x rejects unprojected order fields."
            )
    return None


async def repo_query(
    query_str: str,
    vars: Optional[dict[str, Any]] = None,
    *,
    timeout_s: Optional[float] = None,
) -> list[dict[str, Any]]:
    """Execute a SurrealQL query and return the results.

    v0.7.120 — Slow-query observability. Times every query; logs a
    WARNING when the elapsed wall time exceeds
    DEEPER_NOTEBOOK_SLOW_QUERY_LOG_MS (default 500ms). Without this, the v0.7.114
    memory-recall timeouts that just return `[]` silently are
    invisible — operators can't tell which query is timing out vs
    which is fast-but-frequently-empty. Truncates the logged query to
    300 chars so a multi-page UNION doesn't blow up the log line.

    v0.7.190 — Optional `timeout_s` keyword caps each query at
    `wait_for(timeout=timeout_s)`. Default None preserves the v0.7.120
    behaviour (only the outer route handler's timeout applies). Callers
    that fan out many small queries (ContextBuilder, memory_recall)
    can pass an explicit per-query budget so a single stuck pool
    connection doesn't pin the whole route handler — same defensive
    pattern the v0.7.52 pool-warmup uses.

    v0.8.128 — Runtime guard against SurrealDB 2.x unprojected ORDER BY fields.
    """
    guard_warning = check_query_ordered_projection(query_str)
    if guard_warning:
        logger.warning(guard_warning)
        if resolve_env("DEEPER_NOTEBOOK_STRICT_QUERY_GUARD", "").lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            raise ValueError(guard_warning)

    import os
    import time

    _slow_threshold_ms = float(
        resolve_env("DEEPER_NOTEBOOK_SLOW_QUERY_LOG_MS", "500").strip() or 500
    )
    start = time.monotonic()
    try:

        async def _run() -> list[dict[str, Any]]:
            async with db_connection() as connection:
                try:
                    result = parse_record_ids(await connection.query(query_str, vars))
                    if isinstance(result, str):
                        raise RuntimeError(result)
                    return result
                except RuntimeError as e:
                    # RuntimeError is raised for retriable transaction
                    # conflicts — log at debug to avoid noise.
                    logger.debug(str(e))
                    raise
                except Exception as e:
                    logger.exception(e)
                    raise

        async def _run_once() -> list[dict[str, Any]]:
            if timeout_s is not None:
                return await asyncio.wait_for(_run(), timeout=timeout_s)
            return await _run()

        # v0.8.66 (audit I-INFRA-1) — transparent single retry for a likely
        # idle-reaped pooled connection. SurrealDB closes idle WebSockets; the
        # first query after an idle stretch then hard-fails. db_connection has
        # already marked + dropped the dead conn, so the retry acquires a FRESH
        # one. RESTRICTED to read-only SELECT queries: a write might have reached
        # the server before the socket error surfaced, so retrying it could
        # double-execute. One retry only — never loop on a real outage.
        _read_only = query_str.lstrip()[:6].upper() == "SELECT"
        try:
            return await _run_once()
        except Exception as e:
            if _read_only and _is_retriable_conn_error(e):
                logger.debug(
                    "repo_query: retrying SELECT once after connection error: {}",
                    e,
                )
                return await _run_once()
            raise
    finally:
        # Always log slow queries, even when the query failed — a slow
        # query that ALSO errored is doubly worth surfacing.
        elapsed_s = time.monotonic() - start
        elapsed_ms = elapsed_s * 1000

        # v0.7.124 — Prometheus metrics. Record every query into the
        # duration histogram regardless of speed; bump the slow-query
        # counter only when over threshold. Done in a try/except so a
        # metrics-module import failure can't break the DB path.
        try:
            from api.metrics import db_query_duration_seconds, record_slow_query

            db_query_duration_seconds.observe(elapsed_s)
            if elapsed_ms > _slow_threshold_ms:
                record_slow_query()
        except Exception:
            # Metrics are best-effort. Never let an observability
            # failure interfere with the actual DB result.
            pass

        if elapsed_ms > _slow_threshold_ms:
            # The request_id from loguru's contextvar (set by
            # RequestIDMiddleware in v0.7.120) is already injected via
            # the format string — no need to thread it explicitly here.
            logger.warning(
                "slow query: {:.0f}ms (threshold {:.0f}ms) — {!r}",
                elapsed_ms,
                _slow_threshold_ms,
                query_str[:300],
            )


async def repo_create(table: str, data: dict[str, Any]) -> dict[str, Any]:
    """Create a new record in the specified table"""
    # Remove 'id' attribute if it exists in data
    data.pop("id", None)
    # v0.8.66 (audit D-M1) — preserve a caller-supplied `created` (reimport /
    # restore / migration paths) instead of stamping import-time over the
    # original. Normal creates omit `created`, so they still get the auto-stamp.
    data.setdefault("created", datetime.now(timezone.utc))
    data["updated"] = datetime.now(timezone.utc)
    try:
        async with db_connection() as connection:
            result = parse_record_ids(await connection.insert(table, data))
            # SurrealDB may return a string error message instead of the expected record
            if isinstance(result, str):
                raise RuntimeError(result)
            # v0.8.126 — found live: the 2.x driver returns a one-element list
            # from insert(); callers index the annotated dict (`created["id"]`)
            # and got TypeError. Honour the `-> dict` annotation here, once.
            if isinstance(result, list):
                if not result:
                    raise RuntimeError("Failed to create record: empty result")
                return result[0]
            return result
    except RuntimeError as e:
        logger.error(str(e))
        raise
    except Exception as e:
        logger.exception(e)
        raise RuntimeError("Failed to create record")


async def repo_relate(
    source: str, relationship: str, target: str, data: Optional[dict[str, Any]] = None
) -> list[dict[str, Any]]:
    """Create a relationship between two records with optional data"""
    if data is None:
        data = {}
    query = f"RELATE {source}->{relationship}->{target} CONTENT $data;"
    # logger.debug(f"Relate query: {query}")

    return await repo_query(
        query,
        {
            "data": data,
        },
    )


async def repo_upsert(
    table: str, id: Optional[str], data: dict[str, Any], add_timestamp: bool = False
) -> list[dict[str, Any]]:
    """Create or update a record in the specified table"""
    data.pop("id", None)
    if add_timestamp:
        data["updated"] = datetime.now(timezone.utc)
    query = f"UPSERT {id if id else table} MERGE $data;"
    return await repo_query(query, {"data": data})


async def repo_update(
    table: str, id: str, data: dict[str, Any]
) -> list[dict[str, Any]]:
    """Update an existing record by table and id"""
    # If id already contains the table name, use it as is
    try:
        # v0.8.66 (audit H2 defense-in-depth) — ALWAYS coerce the id to a real
        # RecordID and bind it as $rid below, instead of f-stringing it into the
        # query body. This was the codebase's sole raw-interpolation primitive
        # reachable from external input (PATCH /api/mcp/{id}); a crafted id like
        # "mcp_server:x; DELETE notebook; --" composed a second statement that
        # SurrealDB's multi-statement query() executed. ensure_record_id parses
        # and angle-bracket-escapes the record portion, and parameter binding
        # means the id can never break out of the value position again.
        if isinstance(id, RecordID):
            record_id = id
        elif ":" in id and id.startswith(f"{table}:"):
            record_id = ensure_record_id(id)
        else:
            record_id = ensure_record_id(f"{table}:{id}")
        data.pop("id", None)
        if "created" in data and isinstance(data["created"], str):
            # v0.7.170 — Normalize naive datetimes to UTC-aware.
            # `datetime.fromisoformat("2026-05-21T17:00:00")` (no tz
            # suffix) returns a NAIVE datetime. Mixing naive + aware
            # in downstream comparisons (e.g. gmail.py needs_refresh
            # at line 242: `datetime.now(timezone.utc) >= self.token_
            # expires_at`) raises `TypeError: can't compare offset-
            # naive and offset-aware datetimes`. Plus the adjacent
            # line below uses `datetime.now(timezone.utc)` (aware),
            # so writing back a naive `created` alongside an aware
            # `updated` is itself inconsistent. Treat any naive
            # input as UTC — matches the convention everywhere else
            # in the codebase that uses `timezone.utc` explicitly.
            parsed = datetime.fromisoformat(data["created"])
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            data["created"] = parsed
        data["updated"] = datetime.now(timezone.utc)
        # Bind the record id as a parameter — never interpolate it.
        query = "UPDATE $rid MERGE $data;"
        # logger.debug(f"Update query: {query}")
        result = await repo_query(query, {"rid": record_id, "data": data})
        # if isinstance(result, list):
        #     return [_return_data(item) for item in result]
        return parse_record_ids(result)
    except Exception as e:
        raise RuntimeError(f"Failed to update record: {str(e)}")


async def repo_delete(record_id: str | RecordID):
    """Delete a record by record id"""

    try:
        async with db_connection() as connection:
            return await connection.delete(ensure_record_id(record_id))
    except Exception as e:
        logger.exception(e)
        raise RuntimeError(f"Failed to delete record: {str(e)}")


async def repo_insert(
    table: str, data: list[dict[str, Any]], ignore_duplicates: bool = False
) -> list[dict[str, Any]]:
    """Create a new record in the specified table"""
    try:
        async with db_connection() as connection:
            result = parse_record_ids(await connection.insert(table, data))
            # SurrealDB may return a string error message instead of the expected records
            if isinstance(result, str):
                raise RuntimeError(result)
            return result
    except RuntimeError as e:
        if ignore_duplicates and "already contains" in str(e):
            return []
        # Log transaction conflicts at debug level (they are expected during concurrent operations)
        error_str = str(e).lower()
        if "transaction" in error_str or "conflict" in error_str:
            logger.debug(str(e))
        else:
            logger.error(str(e))
        raise
    except Exception as e:
        if ignore_duplicates and "already contains" in str(e):
            return []
        logger.exception(e)
        raise RuntimeError("Failed to create record")
