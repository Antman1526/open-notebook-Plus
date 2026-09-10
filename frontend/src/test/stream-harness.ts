// Reusable SSE test harness for the streaming hooks (use-ask.ts,
// useSourceChat.ts, useNotebookChat.ts). Builds a ReadableStream<Uint8Array>
// that a test can push server-sent-event frames into on demand, mirroring
// what `fetch(...).then(r => r.body)` hands the hooks' `reader.getReader()`
// loop. Pairs with `vi.useFakeTimers()` + `vi.stubEnv('NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS', ...)`
// the way src/lib/api/chat.stream-stall.test.ts does for the idle-timeout guard.

export interface SseTestStream {
  /** The ReadableStream to hand back as the mocked fetch response body. */
  stream: ReadableStream<Uint8Array>
  /** Encode `obj` as a `data: <json>\n\n` SSE frame and enqueue it. */
  push: (obj: Record<string, unknown>) => void
  /** Enqueue a raw (already-encoded) chunk, e.g. to test partial-line buffering. */
  pushRaw: (chunk: string) => void
  /** Close the stream (clean end-of-stream, `{ done: true }` on the next read). */
  close: () => void
  /** Error the stream (the reader's pending/next read rejects with `err`). */
  error: (err: unknown) => void
  /** True once the stream's `cancel()` callback has fired (reader torn down). */
  readonly cancelled: boolean
}

/**
 * Creates a controllable SSE-over-ReadableStream test double.
 *
 * `push` mirrors the hooks' expected wire format: each call enqueues one
 * `data: ${JSON.stringify(obj)}\n\n` frame, exactly what
 * `TextDecoder` + `buffer.split('\n')` in use-ask.ts / useSourceChat.ts
 * expect to consume.
 */
export function createSseStream(): SseTestStream {
  const encoder = new TextEncoder()
  let controllerRef: ReadableStreamDefaultController<Uint8Array> | null = null
  let cancelled = false

  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controllerRef = controller
    },
    cancel() {
      cancelled = true
    },
  })

  return {
    stream,
    get cancelled() {
      return cancelled
    },
    push(obj) {
      controllerRef?.enqueue(encoder.encode(`data: ${JSON.stringify(obj)}\n\n`))
    },
    pushRaw(chunk) {
      controllerRef?.enqueue(encoder.encode(chunk))
    },
    close() {
      try {
        controllerRef?.close()
      } catch {
        // Already closed/errored — ignore, mirrors native ReadableStream semantics.
      }
    },
    error(err) {
      controllerRef?.error(err)
    },
  }
}

/**
 * Stubs `requestAnimationFrame` / `cancelAnimationFrame` to run via
 * `setTimeout(..., 0)` instead of the browser's paint loop. Several
 * streaming hooks batch per-token setState calls with rAF
 * (use-ask.ts, useSourceChat.ts, useNotebookChat.ts); under
 * `vi.useFakeTimers()` this makes the flush deterministically advance
 * with `vi.advanceTimersByTimeAsync(...)` instead of depending on
 * whether the installed fake-timer set happens to fake rAF too.
 *
 * Call in `beforeEach` alongside `vi.useFakeTimers()`; `vi.unstubAllGlobals()`
 * in `afterEach` restores the real implementation.
 */
export function stubRafWithTimeout(): void {
  let nextId = 1
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).requestAnimationFrame = (cb: FrameRequestCallback): number => {
    const id = nextId++
    setTimeout(() => cb(Date.now()), 0)
    return id
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  ;(globalThis as any).cancelAnimationFrame = (): void => {
    // The stubbed rAF above doesn't expose the underlying setTimeout id,
    // so cancellation is a no-op here. Tests that need to assert a flush
    // was skipped rely on the hook's own cancelled-flag bookkeeping
    // (e.g. `finalRafId`/`streamRafId` reset to null) instead.
  }
}
