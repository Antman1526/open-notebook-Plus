// Behavioural tests for useAsk's SSE streaming loop: real event framing
// through a ReadableStream, the v0.8.70 rAF-batched final_answer_delta
// flush, the v0.8.115/v0.8.116 idle-stall → toast → retry path, and the
// silent-AbortError handling when a second sendAsk supersedes the first.
// Mirrors the fake-timer + ReadableStream technique from
// src/lib/api/chat.stream-stall.test.ts.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { act, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createSseStream, stubRafWithTimeout } from '@/test/stream-harness'

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }))
vi.mock('sonner', () => ({
  toast: { error: toastError, success: vi.fn() },
}))

const { t } = vi.hoisted(() => ({
  // Return `key` alone, or `key::<json options>` so tests can assert the
  // `seconds` interpolation passed to apiErrors.streamStalledHint.
  t: vi.fn((key: string, options?: Record<string, unknown>) =>
    options ? `${key}::${JSON.stringify(options)}` : key,
  ),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t, language: 'en-US', setLanguage: vi.fn() }),
}))

vi.mock('@/lib/api/search', () => ({
  searchApi: { askKnowledgeBase: vi.fn() },
}))

import { searchApi } from '@/lib/api/search'
import { useAsk } from './use-ask'

const MODELS = {
  strategy: 'strategy-model',
  answer: 'answer-model',
  finalAnswer: 'final-model',
}

function abortableStream(harness: ReturnType<typeof createSseStream>) {
  return async (_params: unknown, signal?: AbortSignal) => {
    signal?.addEventListener('abort', () => {
      harness.error(Object.assign(new Error('The operation was aborted'), { name: 'AbortError' }))
    })
    return harness.stream
  }
}

describe('useAsk (behavioural)', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.stubEnv('NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS', '1000')
    stubRafWithTimeout()
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it('streams strategy → answer → final_answer_delta x2 → final_answer → complete', async () => {
    const harness = createSseStream()
    vi.mocked(searchApi.askKnowledgeBase).mockResolvedValue(harness.stream)

    const { result } = renderHook(() => useAsk())

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendAsk('What is X?', MODELS)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.isStreaming).toBe(true)

    await act(async () => {
      harness.push({ type: 'strategy', reasoning: 'thinking it through', searches: [{ term: 'x', instructions: 'find x' }] })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.strategy).toEqual({
      reasoning: 'thinking it through',
      searches: [{ term: 'x', instructions: 'find x' }],
    })

    await act(async () => {
      harness.push({ type: 'answer', content: 'partial answer 1' })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.answers).toEqual(['partial answer 1'])

    // final_answer_delta is accumulated and flushed via rAF (stubbed to
    // setTimeout(0) by stubRafWithTimeout), so advancing timers is required
    // to observe each intermediate flush.
    await act(async () => {
      harness.push({ type: 'final_answer_delta', content: 'Hel' })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.finalAnswer).toBe('Hel')

    await act(async () => {
      harness.push({ type: 'final_answer_delta', content: 'lo!' })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.finalAnswer).toBe('Hello!')

    // The terminal final_answer event replaces the streamed buffer with the
    // server's canonical text (post-processing may differ from the raw
    // concatenation of deltas) and flips isStreaming off immediately.
    await act(async () => {
      harness.push({ type: 'final_answer', content: 'Hello, world!' })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.finalAnswer).toBe('Hello, world!')
    expect(result.current.isStreaming).toBe(false)

    await act(async () => {
      harness.push({ type: 'complete' })
      harness.close()
      await vi.advanceTimersByTimeAsync(20)
      await sendPromise
    })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.error).toBeNull()
    expect(searchApi.askKnowledgeBase).toHaveBeenCalledWith(
      {
        question: 'What is X?',
        strategy_model: 'strategy-model',
        answer_model: 'answer-model',
        final_answer_model: 'final-model',
      },
      expect.any(AbortSignal),
    )
  })

  it('surfaces a stall as a retryable toast while keeping partial state, and retries with the same args', async () => {
    const harness = createSseStream()
    vi.mocked(searchApi.askKnowledgeBase).mockResolvedValue(harness.stream)

    const { result } = renderHook(() => useAsk())

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendAsk('Stall please', MODELS)
      await vi.advanceTimersByTimeAsync(0)
    })

    await act(async () => {
      harness.push({ type: 'strategy', reasoning: 'r', searches: [] })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.strategy).not.toBeNull()

    // Total silence for the full 1000ms idle window trips the guard.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
      await sendPromise
    })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.error).toContain('stalled')
    // Partial strategy stays in state — only isStreaming flips.
    expect(result.current.strategy).not.toBeNull()
    expect(harness.cancelled).toBe(true)

    expect(toastError).toHaveBeenCalledTimes(1)
    const [message, options] = toastError.mock.calls[0]
    expect(message).toBe('apiErrors.streamStalled')
    expect(options.description).toBe(
      `apiErrors.streamStalledHint::${JSON.stringify({ seconds: 1 })}`,
    )
    expect(options.action.label).toBe('common.accessibility.retry')

    // Retry re-invokes askKnowledgeBase with the same question/models.
    const harness2 = createSseStream()
    vi.mocked(searchApi.askKnowledgeBase).mockResolvedValue(harness2.stream)

    await act(async () => {
      options.action.onClick()
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(searchApi.askKnowledgeBase).toHaveBeenCalledTimes(2)
    expect(vi.mocked(searchApi.askKnowledgeBase).mock.calls[1][0]).toEqual({
      question: 'Stall please',
      strategy_model: 'strategy-model',
      answer_model: 'answer-model',
      final_answer_model: 'final-model',
    })
    expect(result.current.isStreaming).toBe(true)

    await act(async () => {
      harness2.push({ type: 'complete' })
      harness2.close()
      await vi.advanceTimersByTimeAsync(20)
    })
  })

  it('silently drops an aborted first stream when a second sendAsk supersedes it, and the second stream drives state', async () => {
    const harness1 = createSseStream()
    const harness2 = createSseStream()
    vi.mocked(searchApi.askKnowledgeBase)
      .mockImplementationOnce(abortableStream(harness1) as any)
      .mockImplementationOnce(async () => harness2.stream)

    const { result } = renderHook(() => useAsk())

    let firstPromise!: Promise<void>
    await act(async () => {
      firstPromise = result.current.sendAsk('First question', MODELS)
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.isStreaming).toBe(true)

    let secondPromise!: Promise<void>
    await act(async () => {
      // Starting the second call aborts the first's controller; our mock
      // wires that AbortSignal to error harness1's stream, mirroring what
      // a real aborted fetch() does to its response body reader.
      secondPromise = result.current.sendAsk('Second question', MODELS)
      await vi.advanceTimersByTimeAsync(0)
      await firstPromise
    })

    expect(toastError).not.toHaveBeenCalled()
    expect(result.current.isStreaming).toBe(true)
    expect(result.current.error).toBeNull()

    await act(async () => {
      harness2.push({ type: 'strategy', reasoning: 'second', searches: [] })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.strategy).toEqual({ reasoning: 'second', searches: [] })

    await act(async () => {
      harness2.push({ type: 'final_answer', content: 'Second answer' })
      harness2.push({ type: 'complete' })
      harness2.close()
      await vi.advanceTimersByTimeAsync(20)
      await secondPromise
    })

    expect(result.current.finalAnswer).toBe('Second answer')
    expect(result.current.isStreaming).toBe(false)
    expect(toastError).not.toHaveBeenCalled()
  })
})
