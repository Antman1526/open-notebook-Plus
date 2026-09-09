/**
 * v0.8.115 — Idle-timeout guard for streaming response readers.
 *
 * Problem: every SSE / NDJSON consumer (use-ask, useSourceChat, and
 * chatApi.streamMessage behind useNotebookChat) sits in
 * `await reader.read()` with no upper bound. When a local model daemon
 * (Ollama, llama.cpp, MLX) dies mid-generation, or the machine sleeps
 * and wakes with a half-open TCP connection, the read never settles:
 * the UI stays in "streaming" forever with a spinner and no Stop-able
 * error. The backend sends no heartbeat frames, so silence is the only
 * signal we get.
 *
 * Fix: race each read against an idle timer. The timer resets on every
 * chunk (it is per-read, not per-stream), so a slow-but-alive model that
 * trickles tokens is never cut off. Only total silence for `idleMs`
 * trips it. On a stall we cancel the reader (so the HTTP body is torn
 * down and the backend's `is_disconnected()` fires) and reject with a
 * `StreamStallError` the hooks can surface as a specific toast.
 *
 * Default is 60 s. Since v0.8.116 the backend emits a heartbeat frame
 * every DEEPER_NOTEBOOK_STREAM_HEARTBEAT_SEC (10 s) of model silence, so a
 * healthy connection never goes quiet for more than a few seconds and a
 * minute of nothing means the socket is dead (or the API process is).
 * A hung model call behind a live API is caught server-side by
 * DEEPER_NOTEBOOK_STREAM_IDLE_TIMEOUT_SEC, which ends the stream with an
 * error frame. Tune with `NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS`; `0`
 * disables the guard.
 */

export const DEFAULT_STREAM_IDLE_TIMEOUT_MS = 60_000

/**
 * Parse the idle timeout from the environment. Invalid, negative, or
 * empty values fall back to the default; `0` explicitly disables the
 * guard (plain `reader.read()` semantics).
 */
export function resolveStreamIdleTimeoutMs(
  raw: string | undefined = process.env.NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS,
): number {
  if (raw == null || raw.trim() === '') return DEFAULT_STREAM_IDLE_TIMEOUT_MS
  const parsed = Number(raw)
  if (!Number.isFinite(parsed) || parsed < 0) return DEFAULT_STREAM_IDLE_TIMEOUT_MS
  return parsed
}

export class StreamStallError extends Error {
  readonly idleMs: number
  /** Whole seconds, for user-facing copy. */
  readonly idleSeconds: number

  constructor(idleMs: number) {
    const idleSeconds = Math.round(idleMs / 1000)
    super(`Stream stalled: no data received for ${idleSeconds}s`)
    this.name = 'StreamStallError'
    this.idleMs = idleMs
    this.idleSeconds = idleSeconds
  }
}

/**
 * Duck-typed check so a stall thrown across a module boundary (or
 * re-wrapped by a generator) is still recognised.
 */
export function isStreamStallError(error: unknown): error is StreamStallError {
  if (error instanceof StreamStallError) return true
  return (
    typeof error === 'object' &&
    error !== null &&
    (error as { name?: unknown }).name === 'StreamStallError'
  )
}

/**
 * `reader.read()` with an idle timeout. Resolves with the normal read
 * result when a chunk (or `done`) arrives within `idleMs`; otherwise
 * cancels the reader and rejects with `StreamStallError`.
 *
 * The timer is cleared as soon as the read settles, so a healthy stream
 * pays only a setTimeout/clearTimeout pair per chunk.
 */
export async function readWithIdleTimeout<T>(
  reader: ReadableStreamDefaultReader<T>,
  idleMs: number,
): Promise<ReadableStreamReadResult<T>> {
  if (idleMs <= 0) return reader.read()

  let timer: ReturnType<typeof setTimeout> | null = null
  let stalled = false
  const stall = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      timer = null
      stalled = true
      // Reject BEFORE cancelling. reader.cancel() settles the pending
      // read() with `{ done: true }`, and if that reaction is queued
      // first the race resolves as a clean end-of-stream and the caller
      // never learns it stalled. Rejecting first wins the race; the
      // `stalled` check below makes the outcome independent of job
      // ordering regardless.
      reject(new StreamStallError(idleMs))
      // Tear the body down so the server sees the disconnect. Errors
      // here are irrelevant — we're already failing the read.
      reader.cancel().catch(() => {})
    }, idleMs)
  })

  try {
    const result = await Promise.race([reader.read(), stall])
    if (stalled) throw new StreamStallError(idleMs)
    return result
  } finally {
    if (timer != null) clearTimeout(timer)
  }
}
