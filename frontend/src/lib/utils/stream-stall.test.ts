// v0.8.115 — Unit tests for the streaming idle-timeout guard.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  DEFAULT_STREAM_IDLE_TIMEOUT_MS,
  StreamStallError,
  isStreamStallError,
  readWithIdleTimeout,
  resolveStreamIdleTimeoutMs,
} from './stream-stall'

type Reader = ReadableStreamDefaultReader<Uint8Array>

function makeReader(readImpl: () => Promise<ReadableStreamReadResult<Uint8Array>>) {
  const cancel = vi.fn(() => Promise.resolve())
  const read = vi.fn(readImpl)
  const reader = { read, cancel, releaseLock: vi.fn(), closed: Promise.resolve(undefined) } as unknown as Reader
  return { reader, read, cancel }
}

describe('resolveStreamIdleTimeoutMs', () => {
  it('falls back to the default when unset or blank', () => {
    expect(resolveStreamIdleTimeoutMs(undefined)).toBe(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
    expect(resolveStreamIdleTimeoutMs('')).toBe(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
    expect(resolveStreamIdleTimeoutMs('   ')).toBe(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
  })

  it('falls back to the default for non-numeric or negative values', () => {
    expect(resolveStreamIdleTimeoutMs('abc')).toBe(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
    expect(resolveStreamIdleTimeoutMs('-5')).toBe(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
    expect(resolveStreamIdleTimeoutMs('Infinity')).toBe(DEFAULT_STREAM_IDLE_TIMEOUT_MS)
  })

  it('honours explicit values, including 0 to disable', () => {
    expect(resolveStreamIdleTimeoutMs('30000')).toBe(30_000)
    expect(resolveStreamIdleTimeoutMs('0')).toBe(0)
  })
})

describe('isStreamStallError', () => {
  it('recognises instances and duck-typed errors', () => {
    expect(isStreamStallError(new StreamStallError(1000))).toBe(true)
    expect(isStreamStallError({ name: 'StreamStallError' })).toBe(true)
  })

  it('rejects other errors and non-objects', () => {
    expect(isStreamStallError(new Error('nope'))).toBe(false)
    expect(isStreamStallError({ name: 'AbortError' })).toBe(false)
    expect(isStreamStallError(null)).toBe(false)
    expect(isStreamStallError('StreamStallError')).toBe(false)
  })
})

describe('readWithIdleTimeout', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('returns the read result when a chunk arrives before the timeout', async () => {
    const chunk = new Uint8Array([1, 2, 3])
    const { reader, cancel } = makeReader(() => Promise.resolve({ done: false, value: chunk }))

    const result = await readWithIdleTimeout(reader, 5_000)

    expect(result).toEqual({ done: false, value: chunk })
    // The idle timer must be cleared: advancing past the deadline after
    // a successful read must NOT cancel the reader.
    await vi.advanceTimersByTimeAsync(10_000)
    expect(cancel).not.toHaveBeenCalled()
  })

  it('passes through `done` results', async () => {
    const { reader } = makeReader(() => Promise.resolve({ done: true, value: undefined }))
    await expect(readWithIdleTimeout(reader, 5_000)).resolves.toEqual({ done: true, value: undefined })
  })

  it('rejects with StreamStallError and cancels the reader when the read hangs', async () => {
    const { reader, cancel } = makeReader(() => new Promise(() => {}))

    const pending = readWithIdleTimeout(reader, 5_000)
    // Attach the rejection handler before advancing so the rejection is
    // never observed as unhandled.
    const outcome = pending.then(
      () => 'resolved',
      (error: unknown) => error,
    )

    await vi.advanceTimersByTimeAsync(4_999)
    expect(cancel).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1)
    const error = await outcome

    expect(isStreamStallError(error)).toBe(true)
    expect((error as StreamStallError).idleMs).toBe(5_000)
    expect((error as StreamStallError).message).toContain('5s')
    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('reports a stall (not a clean end) against a real ReadableStream whose cancel settles the read', async () => {
    // Regression: with a real stream, reader.cancel() resolves the pending
    // read() as `{ done: true }`. If the guard cancelled before rejecting,
    // that resolution won the race and callers saw a normal end-of-stream
    // instead of a stall.
    const stream = new ReadableStream<Uint8Array>({ start() {} })
    const reader = stream.getReader()

    const outcome = readWithIdleTimeout(reader, 5_000).then(
      () => 'resolved',
      (error: unknown) => error,
    )
    await vi.advanceTimersByTimeAsync(5_000)
    const error = await outcome

    expect(isStreamStallError(error)).toBe(true)
    // The body was torn down: the next read settles as done.
    await expect(reader.read()).resolves.toMatchObject({ done: true })
  })

  it('resets per read: a slow trickle never trips the guard', async () => {
    let calls = 0
    const { reader, cancel } = makeReader(
      () =>
        new Promise((resolve) => {
          calls += 1
          setTimeout(() => resolve({ done: false, value: new Uint8Array([calls]) }), 4_000)
        }),
    )

    for (let i = 0; i < 3; i += 1) {
      const pending = readWithIdleTimeout(reader, 5_000)
      await vi.advanceTimersByTimeAsync(4_000)
      await expect(pending).resolves.toMatchObject({ done: false })
    }

    expect(cancel).not.toHaveBeenCalled()
  })

  it('bypasses the timer entirely when idleMs is 0', async () => {
    const setTimeoutSpy = vi.spyOn(globalThis, 'setTimeout')
    const { reader, read } = makeReader(() => Promise.resolve({ done: true, value: undefined }))

    await readWithIdleTimeout(reader, 0)

    expect(read).toHaveBeenCalledTimes(1)
    expect(setTimeoutSpy).not.toHaveBeenCalled()
    setTimeoutSpy.mockRestore()
  })
})
