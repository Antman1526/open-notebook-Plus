// v0.8.119 — The profile/speaker toasts must substitute the `{name}`
// placeholder. Resolves real en-US strings (unlike use-podcasts.test.tsx,
// whose t() echoes keys) so the substitution is observable.

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

const { toast } = vi.hoisted(() => ({ toast: vi.fn() }))

vi.mock('@/lib/api/podcasts', () => ({
  podcastsApi: {
    createEpisodeProfile: vi.fn(async (payload: { name: string }) => ({ id: 'ep:1', ...payload })),
    updateEpisodeProfile: vi.fn(async () => ({})),
    deleteEpisodeProfile: vi.fn(async () => ({})),
    duplicateEpisodeProfile: vi.fn(async () => ({ id: 'ep:2', name: 'Weekly (copy)' })),
    createSpeakerProfile: vi.fn(async (payload: { name: string }) => ({ id: 'sp:1', ...payload })),
    updateSpeakerProfile: vi.fn(async () => ({})),
    deleteSpeakerProfile: vi.fn(async () => ({})),
    duplicateSpeakerProfile: vi.fn(async () => ({ id: 'sp:2', name: 'Ada (copy)' })),
  },
}))
vi.mock('@/lib/hooks/use-toast', () => ({ useToast: () => ({ toast }) }))
vi.mock('@/lib/hooks/use-translation', async () => {
  const { enUS } = await import('@/lib/locales/en-US')
  const resolve = (key: string): string => {
    let node: unknown = enUS
    for (const part of key.split('.')) {
      if (typeof node !== 'object' || node === null || !(part in (node as Record<string, unknown>))) return key
      node = (node as Record<string, unknown>)[part]
    }
    return typeof node === 'string' ? node : key
  }
  return { useTranslation: () => ({ t: resolve }) }
})

import { QUERY_KEYS } from '@/lib/api/query-client'
import {
  useCreateEpisodeProfile,
  useDeleteEpisodeProfile,
  useDeleteSpeakerProfile,
  useDuplicateSpeakerProfile,
  useUpdateEpisodeProfile,
} from './use-podcasts'

function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const Wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
  return { Wrapper, qc }
}

function lastDescription(): string {
  const call = toast.mock.calls.at(-1)?.[0] as { description?: string } | undefined
  return call?.description ?? ''
}

describe('podcast profile toasts substitute {name}', () => {
  afterEach(() => {
    toast.mockReset()
  })

  it('create uses the submitted name', async () => {
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useCreateEpisodeProfile(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ name: 'Weekly Digest' } as never)
    })
    expect(lastDescription()).toBe('The episode profile "Weekly Digest" has been successfully created.')
    expect(lastDescription()).not.toContain('{name}')
  })

  it('update uses the payload name', async () => {
    const { Wrapper } = makeWrapper()
    const { result } = renderHook(() => useUpdateEpisodeProfile(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync({ profileId: 'ep:1', payload: { name: 'Renamed' } as never })
    })
    expect(lastDescription()).toBe('The episode profile "Renamed" has been successfully updated.')
  })

  it('delete reads the name from the cached list before invalidation', async () => {
    const { Wrapper, qc } = makeWrapper()
    qc.setQueryData(QUERY_KEYS.episodeProfiles, [{ id: 'ep:1', name: 'Weekly Digest' }])
    const { result } = renderHook(() => useDeleteEpisodeProfile(), { wrapper: Wrapper })
    await act(async () => {
      await result.current.mutateAsync('ep:1')
    })
    expect(lastDescription()).toBe('The episode profile "Weekly Digest" has been successfully removed.')
  })

  it('speaker delete reads from the speaker cache; duplicate uses the created name', async () => {
    const { Wrapper, qc } = makeWrapper()
    qc.setQueryData(QUERY_KEYS.speakerProfiles, [{ id: 'sp:1', name: 'Ada' }])
    const del = renderHook(() => useDeleteSpeakerProfile(), { wrapper: Wrapper })
    await act(async () => {
      await del.result.current.mutateAsync('sp:1')
    })
    expect(lastDescription()).toBe('The speaker "Ada" has been successfully removed.')

    const dup = renderHook(() => useDuplicateSpeakerProfile(), { wrapper: Wrapper })
    await act(async () => {
      await dup.result.current.mutateAsync('sp:1')
    })
    expect(lastDescription()).toBe('The speaker "Ada (copy)" has been successfully duplicated.')
  })
})
