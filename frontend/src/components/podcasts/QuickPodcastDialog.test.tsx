import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const routerPush = vi.hoisted(() => vi.fn())

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: routerPush }),
}))

vi.mock('@/lib/api/podcasts', () => ({
  podcastsApi: {
    getPodcastReadiness: vi.fn(),
    listEpisodeProfiles: vi.fn(),
    listSpeakerProfiles: vi.fn(),
    submitStudioPodcast: vi.fn(),
  },
}))

import { podcastsApi } from '@/lib/api/podcasts'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'
import { QuickPodcastDialog } from './QuickPodcastDialog'

describe('QuickPodcastDialog', () => {
  beforeEach(() => {
    vi.resetAllMocks()
    vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([{
      id: 'episode_profile:local', name: 'Local Episode', description: '', speaker_config: 'Local Voice',
      default_briefing: '', num_segments: 4,
    }])
    vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([{
      id: 'speaker_profile:local', name: 'Local Voice', description: '', speakers: [],
    }])
    usePodcastStudioStore.getState().dismiss()
  })

  it('carries the reviewed selection into Studio without submitting', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'e'.repeat(64), entries: [{
          stableId: 'notebook:research', title: 'Research', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'f'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    })
    const selection = { kind: 'notebook' as const, notebookId: 'notebook:research' }
    usePodcastStudioStore.getState().open([selection], 'quick')
    document.body.style.pointerEvents = 'none'

    render(<QuickPodcastDialog />)
    fireEvent.click(await screen.findByRole('button', { name: 'Customize in Studio' }))

    await waitFor(() => expect(usePodcastStudioStore.getState()).toMatchObject({
      isOpen: false,
      destination: 'studio',
      selections: [selection],
    }))
    await waitFor(() => expect(routerPush).toHaveBeenCalledWith('/podcasts/studio'))
    await waitFor(() => expect(document.body.style.pointerEvents).toBe(''))
    expect(podcastsApi.submitStudioPodcast).not.toHaveBeenCalled()
  })

  it('reviews server-resolved selections and dismisses without a submission', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'a'.repeat(64),
        entries: [{
          stableId: 'notebook:research', title: 'Research', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'b'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }],
        includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      },
      stagePlans: [], ready: true, blockedReasons: [],
    })
    usePodcastStudioStore.getState().open([{
      kind: 'notebook', notebookId: 'notebook:research',
    }], 'quick')

    render(<QuickPodcastDialog />)

    expect(await screen.findByText('Review selection')).toBeVisible()
    expect(screen.getByText('Research')).toBeVisible()
    expect(screen.getByText(/Outline storyboard review/)).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(usePodcastStudioStore.getState().isOpen).toBe(false))
    expect(podcastsApi.getPodcastReadiness).toHaveBeenCalledOnce()
  })

  it('returns keyboard focus to the action that opened the review', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'f'.repeat(64), entries: [], includedCharacters: 0,
        requiresBatchEngine: false, currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    })
    const invoker = document.createElement('button')
    document.body.append(invoker)
    invoker.focus()
    usePodcastStudioStore.getState().open([{
      kind: 'notebook', notebookId: 'notebook:research',
    }], 'quick')

    render(<QuickPodcastDialog />)
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(invoker).toHaveFocus())
    invoker.remove()
  })

  it('requires explicit confirmation and closes only after one accepted submit', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'c'.repeat(64), entries: [{
          stableId: 'notebook:research', title: 'Research', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'd'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    })
    vi.mocked(podcastsApi.submitStudioPodcast).mockResolvedValue({
      jobId: 'command:podcast-one', status: 'submitted', message: 'accepted',
      episodeProfile: 'Local Episode', episodeName: 'Research', mode: 'deep_dive',
    })
    usePodcastStudioStore.getState().open([{
      kind: 'notebook', notebookId: 'notebook:research',
    }], 'quick')

    render(<QuickPodcastDialog />)

    const continueButton = await screen.findByRole('button', { name: 'Continue to confirmation' })
    await waitFor(() => expect(continueButton).toBeEnabled())
    fireEvent.click(continueButton)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))

    await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalledWith(
      expect.objectContaining({ notebookId: 'notebook:research' }),
    ))
    expect(usePodcastStudioStore.getState().isOpen).toBe(false)
  })

  it('forwards explicit notebookId when opened from a source selection', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 's'.repeat(64), entries: [{
          stableId: 'source:one', title: 'Source 1', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'd'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 50,
        }], includedCharacters: 50, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    })
    vi.mocked(podcastsApi.submitStudioPodcast).mockResolvedValue({
      jobId: 'command:podcast-two', status: 'submitted', message: 'accepted',
      episodeProfile: 'Local Episode', episodeName: 'Source 1', mode: 'deep_dive',
    })
    usePodcastStudioStore.getState().open([{
      kind: 'app_source', sourceId: 'source:one', inclusionMode: 'full',
    }], 'quick', 'notebook:alpha')

    render(<QuickPodcastDialog />)

    const continueButton = await screen.findByRole('button', { name: 'Continue to confirmation' })
    await waitFor(() => expect(continueButton).toBeEnabled())
    fireEvent.click(continueButton)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))

    await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalledWith(
      expect.objectContaining({ notebookId: 'notebook:alpha' }),
    ))
    expect(usePodcastStudioStore.getState().isOpen).toBe(false)
  })
})
