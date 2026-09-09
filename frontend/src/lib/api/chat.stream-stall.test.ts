// v0.8.115 — Behavioural test: chatApi.streamMessage must reject with a
// StreamStallError when the response body goes silent for longer than
// NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS, and must keep yielding normally
// while chunks keep arriving.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { isStreamStallError } from '@/lib/utils/stream-stall'

vi.mock('@/lib/config', () => ({
  getApiUrl: vi.fn(async () => 'http://test.local'),
}))

import { chatApi } from './chat'

type Controller = ReadableStreamDefaultController<Uint8Array>

function mockFetchWithBody(setup: (controller: Controller) => void) {
  const cancelSpy = vi.fn()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      setup(controller)
    },
    cancel() {
      cancelSpy()
    },
  })
  const fetchMock = vi.fn(async () => ({ ok: true, status: 200, body }) as unknown as Response)
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, cancelSpy }
}

const REQUEST = {
  session_id: 'sess-1',
  message: 'hello',
  context: {},
} as unknown as Parameters<typeof chatApi.streamMessage>[0]

describe('chatApi.streamMessage idle-timeout guard', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubEnv('NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS', '1000')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it('rejects with StreamStallError and cancels the body when the stream goes silent', async () => {
    const { cancelSpy } = mockFetchWithBody(() => {
      // Never enqueue anything: simulates a dead daemon / half-open socket.
    })

    const iterator = chatApi.streamMessage(REQUEST)
    const outcome = iterator.next().then(
      () => 'resolved',
      (error: unknown) => error,
    )

    await vi.advanceTimersByTimeAsync(999)
    expect(cancelSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    const error = await outcome

    expect(isStreamStallError(error)).toBe(true)
    expect(cancelSpy).toHaveBeenCalled()
  })

  it('keeps yielding while chunks arrive slower than the timeout but faster than silence', async () => {
    const encoder = new TextEncoder()
    let controller!: Controller
    mockFetchWithBody((c) => {
      controller = c
    })

    const iterator = chatApi.streamMessage(REQUEST)

    // First event lands at 800ms — inside the 1000ms window.
    const first = iterator.next()
    await vi.advanceTimersByTimeAsync(800)
    controller.enqueue(encoder.encode('{"type":"token","content":"a"}\n'))
    await expect(first).resolves.toEqual({ done: false, value: { type: 'token', content: 'a' } })

    // Second event lands another 800ms later. The guard is per-read, so
    // 1600ms of total elapsed time must NOT trip it.
    const second = iterator.next()
    await vi.advanceTimersByTimeAsync(800)
    controller.enqueue(encoder.encode('{"type":"token","content":"b"}\n'))
    await expect(second).resolves.toEqual({ done: false, value: { type: 'token', content: 'b' } })

    controller.close()
    await expect(iterator.next()).resolves.toMatchObject({ done: true })
  })

  it('never arms a timer when the timeout is disabled with 0', async () => {
    vi.stubEnv('NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS', '0')
    let controller!: Controller
    const { cancelSpy } = mockFetchWithBody((c) => {
      controller = c
    })

    const iterator = chatApi.streamMessage(REQUEST)
    let settled = false
    const first = iterator.next().then(
      (result) => { settled = true; return result },
      (error: unknown) => { settled = true; throw error },
    )

    await vi.advanceTimersByTimeAsync(10 * 60 * 1000)

    expect(settled).toBe(false)
    expect(cancelSpy).not.toHaveBeenCalled()

    // Close the body so the pending read settles and the generator
    // finishes cleanly (an async generator's return() would otherwise
    // queue behind the still-pending read).
    controller.close()
    await expect(first).resolves.toMatchObject({ done: true })
  })
})
