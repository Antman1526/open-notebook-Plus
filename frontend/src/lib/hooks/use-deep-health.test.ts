// v0.7.117 — tests for the deep-health hook. We mock the API module
// rather than the underlying axios client so we exercise the hook +
// React Query plumbing without standing up the full apiClient runtime
// config dance.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { renderHook, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import React from 'react'

vi.mock('@/lib/api/health', () => ({
  healthApi: {
    getDeepHealth: vi.fn(),
  },
}))

import { healthApi } from '@/lib/api/health'
import { useDeepHealth } from './use-deep-health'

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return React.createElement(QueryClientProvider, { client: qc }, children)
}

describe('useDeepHealth', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns the healthy shape from the API', async () => {
    vi.mocked(healthApi.getDeepHealth).mockResolvedValue({
      status: 'healthy',
      checks: {
        database: { status: 'online', ok: true, error: null },
        migrations: { status: 'applied', ok: true, error: null },
        embedding_model: { status: 'configured', ok: true, error: null },
        chat_model: { status: 'configured', ok: true, error: null },
        command_registry: { status: 'loaded', ok: true, error: null },
      },
    } as any)

    const { result } = renderHook(() => useDeepHealth(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.status).toBe('healthy')
    expect(result.current.data?.checks.database.ok).toBe(true)
    expect(healthApi.getDeepHealth).toHaveBeenCalledOnce()
  })

  it('returns the degraded shape and surfaces per-subsystem errors', async () => {
    vi.mocked(healthApi.getDeepHealth).mockResolvedValue({
      status: 'degraded',
      checks: {
        database: { status: 'online', ok: true, error: null },
        migrations: { status: 'applied', ok: true, error: null },
        embedding_model: {
          status: 'missing',
          ok: false,
          error: 'No default embedding model assigned',
        },
        chat_model: { status: 'configured', ok: true, error: null },
        command_registry: { status: 'loaded', ok: true, error: null },
      },
    } as any)

    const { result } = renderHook(() => useDeepHealth(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.status).toBe('degraded')
    expect(result.current.data?.checks.embedding_model.error).toBe(
      'No default embedding model assigned',
    )
  })

  it('returns the not_ready shape on 503', async () => {
    vi.mocked(healthApi.getDeepHealth).mockResolvedValue({
      status: 'not_ready',
      checks: {
        database: { status: 'offline', ok: false, error: 'connection refused' },
        migrations: { status: 'error', ok: false, error: null },
        embedding_model: { status: 'missing', ok: false, error: null },
        chat_model: { status: 'missing', ok: false, error: null },
        command_registry: { status: 'error', ok: false, error: null },
      },
    } as any)

    const { result } = renderHook(() => useDeepHealth(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.status).toBe('not_ready')
    expect(result.current.data?.checks.database.error).toBe('connection refused')
  })

  it('passes through a worker subsystem in checks (v0.8.127)', async () => {
    // v0.8.127 — the backend's /healthz/deep now returns a `worker`
    // subsystem (background worker heartbeat). `SubsystemKey`
    // (frontend/src/lib/api/health.ts) hasn't been widened to include
    // it yet, so callers read it via a cast — same as the mock data
    // below, which is already `as any`. This test only asserts the
    // hook/React Query plumbing passes an extra key through unchanged;
    // it is not a claim that `checks.worker` is statically typed.
    vi.mocked(healthApi.getDeepHealth).mockResolvedValue({
      status: 'degraded',
      checks: {
        database: { status: 'online', ok: true, error: null },
        migrations: { status: 'applied', ok: true, error: null },
        embedding_model: { status: 'configured', ok: true, error: null },
        chat_model: { status: 'configured', ok: true, error: null },
        command_registry: { status: 'loaded', ok: true, error: null },
        worker: {
          status: 'offline',
          ok: false,
          error: 'No background worker heartbeat in the last 180 s',
        },
      },
    } as any)

    const { result } = renderHook(() => useDeepHealth(), { wrapper })

    await waitFor(() => expect(result.current.isLoading).toBe(false))
    expect(result.current.data?.status).toBe('degraded')
    const checks = result.current.data?.checks as any
    expect(checks.worker.ok).toBe(false)
    expect(checks.worker.status).toBe('offline')
  })

  it('exposes refetch for the wizard re-check button', async () => {
    vi.mocked(healthApi.getDeepHealth).mockResolvedValue({
      status: 'healthy',
      checks: {
        database: { status: 'online', ok: true, error: null },
        migrations: { status: 'applied', ok: true, error: null },
        embedding_model: { status: 'configured', ok: true, error: null },
        chat_model: { status: 'configured', ok: true, error: null },
        command_registry: { status: 'loaded', ok: true, error: null },
      },
    } as any)

    const { result } = renderHook(() => useDeepHealth(), { wrapper })
    await waitFor(() => expect(result.current.isLoading).toBe(false))
    await result.current.refetch()
    expect(healthApi.getDeepHealth).toHaveBeenCalledTimes(2)
  })
})
