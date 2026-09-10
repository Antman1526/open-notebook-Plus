// v0.8.117 — Behavioural tests for useNotebookChat's streaming send.
// chatApi.streamMessage is mocked as an async generator (its own idle
// guard is covered by src/lib/api/chat.stream-stall.test.ts); here we
// drive the hook: rAF-batched tokens, canonical `done` replacement,
// stall-keeps-partial + toast retry, stall-with-nothing cleanup, and a
// server error event using the generic toast.

/* eslint-disable @typescript-eslint/no-explicit-any */
import React from 'react'
import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { stubRafWithTimeout } from '@/test/stream-harness'
import { StreamStallError } from '@/lib/utils/stream-stall'
import { QUERY_KEYS } from '@/lib/api/query-client'

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

vi.mock('@/lib/hooks/use-evaluation', () => ({
  markEvaluationPersistencePending: vi.fn(),
}))

// The hook imports a type from the notebook page; keep the page out of
// the module graph.
vi.mock('@/app/(dashboard)/notebooks/[id]/page', () => ({}))

vi.mock('@/lib/api/query-client', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  pruneMessageScopedQueries: vi.fn(),
}))

vi.mock('@/lib/api/chat', () => ({
  chatApi: {
    listSessions: vi.fn(),
    getSession: vi.fn(),
    createSession: vi.fn(),
    updateSession: vi.fn(),
    deleteSession: vi.fn(),
    buildContext: vi.fn(),
    streamMessage: vi.fn(),
  },
}))

import { chatApi } from '@/lib/api/chat'
import { useNotebookChat } from './useNotebookChat'

const NOTEBOOK_ID = 'notebook:n1'
const SESSION_ID = 'chat_session:s1'

type Event = Record<string, unknown>

/** Async generator that yields `events` and then throws `thenThrow` if given. */
const EVENT_GAP_MS = 10

function makeStream(events: Event[], thenThrow?: unknown) {
  return async function* () {
    for (const event of events) {
      // Space events out on the (fake) clock so rAF-batched flushes can
      // interleave and tests can observe the in-flight state.
      await new Promise((resolve) => setTimeout(resolve, EVENT_GAP_MS))
      yield event
    }
    if (thenThrow) {
      // A stall is preceded by silence; give the rAF flush time to land
      // the last token before the error arrives, as it would in reality.
      await new Promise((resolve) => setTimeout(resolve, EVENT_GAP_MS))
      throw thenThrow
    }
  }
}

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
  vi.mocked(chatApi.listSessions).mockResolvedValue([
    { id: SESSION_ID, title: 'Session', created: '2026-09-09T00:00:00Z', updated: '2026-09-09T00:00:00Z' } as any,
  ])
  vi.mocked(chatApi.getSession).mockResolvedValue({ id: SESSION_ID, title: 'Session', messages: [] } as any)
  vi.mocked(chatApi.buildContext).mockResolvedValue({ context: { sources: [], notes: [] }, token_count: 0, char_count: 0 } as any)

  const { Wrapper, qc } = makeWrapper()
  const hook = renderHook(
    () => useNotebookChat({
      notebookId: NOTEBOOK_ID,
      sources: [],
      notes: [],
      contextSelections: { sources: {}, notes: {} } as any,
      contextCountsEnabled: false,
    }),
    { wrapper: Wrapper },
  )
  await waitFor(() => expect(hook.result.current.currentSessionId).toBe(SESSION_ID))
  await waitFor(() => expect(chatApi.getSession).toHaveBeenCalled())
  return { ...hook, qc }
}

describe('useNotebookChat (behavioural)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('batches tokens into the streaming bubble and adopts the canonical list from done', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    const canonical = [
      { id: 'm1', type: 'human', content: 'hello' },
      { id: 'm2', type: 'ai', content: 'Hello world!' },
    ]
    vi.mocked(chatApi.streamMessage).mockImplementation(makeStream([
      { type: 'start', session_id: SESSION_ID },
      { type: 'token', content: 'Hello ' },
      { type: 'token', content: 'world' },
      { type: 'token', content: '!' },
      { type: 'done', messages: canonical, selected_provider: null, selected_model_id: null },
    ]) as any)

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendMessage('hello')
      await vi.advanceTimersByTimeAsync(0)
    })
    expect(result.current.isSending).toBe(true)

    // start + two tokens have landed (10 ms apart) and the rAF flush ran.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3 * EVENT_GAP_MS + 2)
    })
    expect(result.current.isSending).toBe(true)
    const streaming = result.current.messages.find(m => m.type === 'ai')
    expect(streaming?.content).toBe('Hello world')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10 * EVENT_GAP_MS)
      await sendPromise
    })

    expect(result.current.isSending).toBe(false)
    expect(result.current.messages.map(m => [m.id, m.content])).toEqual([
      ['m1', 'hello'],
      ['m2', 'Hello world!'],
    ])
    expect(toastError).not.toHaveBeenCalled()
    expect(vi.mocked(chatApi.streamMessage).mock.calls[0][0]).toMatchObject({
      session_id: SESSION_ID,
      message: 'hello',
    })
  })

  it('keeps the partial answer on a stall and retries the same message from the toast action', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    vi.mocked(chatApi.streamMessage).mockImplementation(makeStream([
      { type: 'start', session_id: SESSION_ID },
      { type: 'token', content: 'par' },
      { type: 'token', content: 'tial' },
    ], new StreamStallError(60_000)) as any)

    let sendPromise!: Promise<void>
    await act(async () => {
      sendPromise = result.current.sendMessage('stall with text')
      await vi.advanceTimersByTimeAsync(10 * EVENT_GAP_MS)
      await sendPromise
    })

    expect(result.current.isSending).toBe(false)
    expect(result.current.messages.map(m => [m.type, m.content])).toEqual([
      ['human', 'stall with text'],
      ['ai', 'partial'],
    ])

    expect(toastError).toHaveBeenCalledTimes(1)
    const [message, options] = toastError.mock.calls[0]
    expect(message).toBe('apiErrors.streamStalled')
    expect(options.description).toBe(
      `apiErrors.streamStalledHint::${JSON.stringify({ seconds: 60 })}`,
    )
    expect(options.action.label).toBe('common.retry')

    vi.mocked(chatApi.streamMessage).mockImplementation(makeStream([
      { type: 'done', messages: [{ id: 'm1', type: 'human', content: 'stall with text' }, { id: 'm2', type: 'ai', content: 'second try' }], selected_provider: null, selected_model_id: null },
    ]) as any)
    await act(async () => {
      options.action.onClick()
      await vi.advanceTimersByTimeAsync(10 * EVENT_GAP_MS)
    })
    expect(chatApi.streamMessage).toHaveBeenCalledTimes(2)
    expect(vi.mocked(chatApi.streamMessage).mock.calls[1][0]).toMatchObject({ message: 'stall with text' })
    expect(result.current.messages.find(m => m.id === 'm2')?.content).toBe('second try')
  })

  it('keeps the interrupted partial across an unchanged refetch and drops it when the server list changes (v0.8.118)', async () => {
    const { result, qc } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    vi.mocked(chatApi.streamMessage).mockImplementation(makeStream([
      { type: 'start', session_id: SESSION_ID },
      { type: 'token', content: 'partial' },
    ], new StreamStallError(60_000)) as any)
    await act(async () => {
      const p = result.current.sendMessage('q')
      await vi.advanceTimersByTimeAsync(10 * EVENT_GAP_MS)
      await p
    })
    expect(result.current.messages.find(m => m.type === 'ai')).toMatchObject({ content: 'partial', interrupted: true })

    // Unchanged server data → structural sharing → sync effect does not run.
    await act(async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEYS.notebookChatSession(SESSION_ID) })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.messages.map(m => [m.type, m.content])).toEqual([
      ['human', 'q'],
      ['ai', 'partial'],
    ])

    // Changed server data (a completed turn) → canonical list replaces it.
    vi.mocked(chatApi.getSession).mockResolvedValue({
      id: SESSION_ID, title: 'Session',
      messages: [
        { id: 'm1', type: 'human', content: 'q' },
        { id: 'm2', type: 'ai', content: 'full answer' },
      ],
    } as any)
    await act(async () => {
      await qc.invalidateQueries({ queryKey: QUERY_KEYS.notebookChatSession(SESSION_ID) })
      await vi.advanceTimersByTimeAsync(20)
    })
    expect(result.current.messages.map(m => [m.id, m.content])).toEqual([
      ['m1', 'q'],
      ['m2', 'full answer'],
    ])
    expect(result.current.messages.some(m => m.interrupted)).toBe(false)
  })

  it('removes the empty placeholder and optimistic bubble when a stall produced nothing', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    vi.mocked(chatApi.streamMessage).mockImplementation(makeStream([
      { type: 'start', session_id: SESSION_ID },
    ], new StreamStallError(60_000)) as any)

    await act(async () => {
      const p = result.current.sendMessage('silent stall')
      await vi.advanceTimersByTimeAsync(10 * EVENT_GAP_MS)
      await p
    })

    expect(result.current.isSending).toBe(false)
    expect(result.current.messages).toEqual([])
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError.mock.calls[0][0]).toBe('apiErrors.streamStalled')
  })

  it('surfaces a server error event through the generic toast and cleans up', async () => {
    const { result } = await renderReadyHook()
    vi.useFakeTimers()
    stubRafWithTimeout()

    vi.mocked(chatApi.streamMessage).mockImplementation(makeStream([
      { type: 'start', session_id: SESSION_ID },
      { type: 'token', content: 'x' },
      { type: 'error', detail: 'Model exploded' },
    ]) as any)

    await act(async () => {
      const p = result.current.sendMessage('boom')
      await vi.advanceTimersByTimeAsync(10 * EVENT_GAP_MS)
      await p
    })

    expect(result.current.isSending).toBe(false)
    expect(result.current.messages).toEqual([])
    expect(toastError).toHaveBeenCalledTimes(1)
    expect(toastError.mock.calls[0][0]).not.toBe('apiErrors.streamStalled')
  })
})
