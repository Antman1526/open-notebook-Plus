// v0.8.116 — Behavioural tests for useSourceChat's SSE streaming loop:
// real event framing through a ReadableStream, the rAF-batched delta
// flush, and the stall → keep-partial → retry path. Complements the
// source-text contract in stream-stall-guard.test.ts with coverage that
// actually drives the hook. Same fake-timer + ReadableStream technique as
// src/lib/api/chat.stream-stall.test.ts and use-ask.behaviour.test.ts.

/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createSseStream, stubRafWithTimeout } from '@/test/stream-harness'

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }))
vi.mock('sonner', () => ({
  toast: { error: toastError, success: vi.fn() },
}))

const { t } = vi.hoisted(() => ({
  t: vi.fn((key: string, options?: Record<string, unknown>) =>
    options ? `${key}::${JSON.stringify(options)}` : key,
  ),
}))
vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t, language: 'en-US', setLanguage: vi.fn() }),
}))

vi.mock('@/lib/api/query-client', () => ({
  pruneMessageScopedQueries: vi.fn(),
}))

vi.mock('@/lib/api/source-chat', () => ({
  sourceChatApi: {
    listSessions: vi.fn(),
    getSession: vi.fn(),
    createSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    sendMessage: vi.fn(),
  },
}))

import { sourceChatApi } from '@/lib/api/source-chat'
import { useSourceChat } from './useSourceChat'

const SOURCE_ID = 'source:abc'
const SESSION_ID = 'chat_session:s1'

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = function Wrapper({ children }: { children: React.ReactNode }) {
    return React.createElement(QueryClientProvider, { client: qc }, children)
  }
  return { Wrapper, qc }
}

async function renderReadyHook() {
  vi.mocked(sourceChatApi.listSessions).mockResolvedValue([
    { id: SESSION_ID, title: 'Session', created: '2026-09-09T00:00:00Z', updated: '2026-09-09T00:00:00Z' } as any,
  ])
  vi.mocked(sourceChatApi.getSession).mockResolvedValue({
    id: SESSION_ID, title: 'Session', messages: [],
  } as any)

  const { Wrapper, qc } = makeWrapper()
  const hook = renderHook(() => useSourceChat(SOURCE_ID), { wrapper: Wrapper })
  // Sessions load → auto-select → session fetch. Real timers here are fine;
  // fake timers are installed per test after this settles.
  await waitFor(() => expect(hook.result.current.currentSessionId).toBe(SESSION_ID))
  await waitFor(() => expect(sourceChatApi.getSession).toHaveBeenCalled())
  return { ...hook, qc }
}

async function stallWithPartial(result: { current: ReturnType<typeof useSourceChat> }, text: string) {
  const harness = createSseStream()
  vi.mocked(sourceChatApi.sendMessage).mockResolvedValue(harness.stream as any)
  let sendPromise!: Promise<void>
  await act(async () => {
    sendPromise = result.current.sendMessage(text)
    await vi.advanceTimersByTimeAsync(0)
  })
  await act(async () => {
    harness.push({ type: 'ai_message_delta', content: 'partial' })
    await vi.advanceTimersByTimeAsync(20)
  })
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1000)
    await sendPromise
  })
}

describe('useSourceChat (behavioural)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.stubEnv('NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS', '1000')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllEnvs()
    vi.unstubAllGlobals()
  })

  it('streams deltas into one AI message and finishes with the canonical ai_message', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    const harness = createSseStream()
    vi.mocked(sourceChatApi.sendMessage).mockResolvedValue(harness.stream as any)

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendMessage('hello')
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.isStreaming).toBe(true)
    expect(result.current.messages.map(m => m.type)).toEqual(['human'])

    await act(async () => {
      harness.push({ type: 'ai_message_delta', content: 'a' })
      harness.push({ type: 'ai_message_delta', content: 'b' })
      harness.push({ type: 'ai_message_delta', content: 'c' })
      await vi.advanceTimersByTimeAsync(20)
    })
    const streaming = result.current.messages.find(m => m.type === 'ai')
    expect(streaming?.content).toBe('abc')

    await act(async () => {
      harness.push({ type: 'ai_message', content: 'abc!' })
      harness.push({ type: 'complete' })
      harness.close()
      await vi.advanceTimersByTimeAsync(20)
      await sendPromise
    })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages.find(m => m.type === 'ai')?.content).toBe('abc!')
    expect(toastError).not.toHaveBeenCalled()
    expect(sourceChatApi.sendMessage).toHaveBeenCalledTimes(1)
  })

  it('keeps the partial answer on a stall, tears the reader down, and retries via the toast action', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    const harness = createSseStream()
    vi.mocked(sourceChatApi.sendMessage).mockResolvedValue(harness.stream as any)

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendMessage('stall with text')
      await vi.advanceTimersByTimeAsync(0)
    })

    await act(async () => {
      harness.push({ type: 'ai_message_delta', content: 'partial ' })
      harness.push({ type: 'ai_message_delta', content: 'answer' })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.messages.find(m => m.type === 'ai')?.content).toBe('partial answer')

    // Total silence for the idle window trips the guard.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
      await sendPromise
    })

    expect(result.current.isStreaming).toBe(false)
    expect(harness.cancelled).toBe(true)
    // v0.8.116 — partial text and the user's bubble are preserved.
    expect(result.current.messages.map(m => [m.type, m.content])).toEqual([
      ['human', 'stall with text'],
      ['ai', 'partial answer'],
    ])

    expect(toastError).toHaveBeenCalledTimes(1)
    const [message, options] = toastError.mock.calls[0]
    expect(message).toBe('apiErrors.streamStalled')
    expect(options.description).toBe(
      `apiErrors.streamStalledHint::${JSON.stringify({ seconds: 1 })}`,
    )
    expect(options.action.label).toBe('common.retry')

    const harness2 = createSseStream()
    vi.mocked(sourceChatApi.sendMessage).mockResolvedValue(harness2.stream as any)
    await act(async () => {
      options.action.onClick()
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(sourceChatApi.sendMessage).toHaveBeenCalledTimes(2)
    expect(vi.mocked(sourceChatApi.sendMessage).mock.calls[1][2]).toMatchObject({
      message: 'stall with text',
    })
    expect(result.current.isStreaming).toBe(true)

    await act(async () => {
      harness2.push({ type: 'ai_message', content: 'second try' })
      harness2.push({ type: 'complete' })
      harness2.close()
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.isStreaming).toBe(false)
  })

  it('removes the empty placeholder and the optimistic user bubble when a stall produced nothing', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    const harness = createSseStream()
    vi.mocked(sourceChatApi.sendMessage).mockResolvedValue(harness.stream as any)

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendMessage('stall silently')
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.messages.map(m => m.type)).toEqual(['human'])

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
      await sendPromise
    })

    expect(result.current.isStreaming).toBe(false)
    expect(result.current.messages).toEqual([])
    expect(harness.cancelled).toBe(true)
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError.mock.calls[0][0]).toBe('apiErrors.streamStalled')
  })

  it('keeps the interrupted partial across a refetch that returns unchanged server data (v0.8.118)', async () => {
    const { result, qc } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    await stallWithPartial(result, 'q')
    expect(result.current.messages.find(m => m.type === 'ai')).toMatchObject({ content: 'partial', interrupted: true })

    // A later refetch (window focus, invalidation) that yields the same
    // server list must not wipe the local interrupted message: react-query's
    // structural sharing keeps the data reference stable, so the sync
    // effect does not re-run.
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ['sourceChatSession', SOURCE_ID, SESSION_ID] })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.messages.map(m => [m.type, m.content])).toEqual([
      ['human', 'q'],
      ['ai', 'partial'],
    ])
  })

  it('replaces the interrupted partial once the server list actually changes (v0.8.118)', async () => {
    const { result, qc } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    await stallWithPartial(result, 'q')
    expect(result.current.messages.some(m => m.interrupted)).toBe(true)

    // The next completed turn lands server-side: the canonical list wins.
    vi.mocked(sourceChatApi.getSession).mockResolvedValue({
      id: SESSION_ID, title: 'Session',
      messages: [
        { id: 'm1', type: 'human', content: 'q' },
        { id: 'm2', type: 'ai', content: 'full answer' },
      ],
    } as any)
    await act(async () => {
      await qc.invalidateQueries({ queryKey: ['sourceChatSession', SOURCE_ID, SESSION_ID] })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.messages.map(m => [m.id, m.content])).toEqual([
      ['m1', 'q'],
      ['m2', 'full answer'],
    ])
    expect(result.current.messages.some(m => m.interrupted)).toBe(false)
  })

  it('surfaces a server error event through the generic failure toast, not the stall toast', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    const harness = createSseStream()
    vi.mocked(sourceChatApi.sendMessage).mockResolvedValue(harness.stream as any)

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendMessage('boom')
      await vi.advanceTimersByTimeAsync(0)
    })

    await act(async () => {
      harness.push({ type: 'error', message: 'Model exploded' })
      harness.close()
      await vi.advanceTimersByTimeAsync(20)
      await sendPromise
    })

    expect(result.current.isStreaming).toBe(false)
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError.mock.calls[0][0]).not.toBe('apiErrors.streamStalled')
  })
})
