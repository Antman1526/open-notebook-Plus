import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api/podcasts', () => ({
  podcastsApi: {
    getPodcastReadiness: vi.fn(),
    listEpisodeProfiles: vi.fn(),
    listSpeakerProfiles: vi.fn(),
    submitStudioPodcast: vi.fn(),
  },
}))

import { podcastsApi } from '@/lib/api/podcasts'
import type { PodcastReadiness } from '@/lib/types/podcasts'
import { PodcastStudio } from './PodcastStudio'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve
    reject = nextReject
  })
  return { promise, resolve, reject }
}

const readinessForOverride = (modelId: string, title = 'Research plan'): PodcastReadiness => ({
  preview: {
    selectionFingerprint: modelId === 'model-a' ? 'a'.repeat(64) : 'b'.repeat(64),
    entries: [{
      stableId: 'knowledge_engine_document:plan', title, authorityKind: 'app_owned',
      relativeLocator: null, revisionId: null, fingerprint: 'c'.repeat(64),
      state: 'included', reason: `${modelId} readiness`, estimatedCharacters: 120,
    }], includedCharacters: 120, requiresBatchEngine: false,
    currentWorkerEligible: true, blockedReasons: [],
  },
  stagePlans: [{
    role: 'podcast_outline', outcome: 'ready', modelId, provider: 'mlx',
    resourceTier: 'standard', selectionSource: 'production_override', reason: `${modelId} route is ready.`, blockedReason: null,
    overrideChoices: ['model-a', 'model-b'],
  }],
  ready: true, blockedReasons: [],
})

describe('PodcastStudio', () => {
  beforeEach(() => vi.resetAllMocks())

  it('renders one sequential four-region production layout and locked Phase 3 stages', () => {
    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} />)

    const studio = screen.getByRole('region', { name: 'Podcast Intelligence Studio' })
    expect(screen.getByRole('heading', { name: 'Podcast Intelligence Studio', level: 2 })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Podcast production folio' })).toBeInTheDocument()
    const regions = Array.from(studio.querySelectorAll<HTMLElement>('[data-studio-region]'))
    expect(regions.map((region) => region.dataset.studioRegion)).toEqual([
      'research-set', 'editorial-brief', 'outline-workspace', 'production-timeline',
    ])
    expect(screen.getByRole('region', { name: 'Research Set' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Editorial Brief' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Outline Storyboard' })).toBeVisible()
    expect(screen.getByRole('region', { name: 'Production Timeline' })).toBeVisible()
    expect(screen.getAllByText('Available after intellectual engine upgrade')).toHaveLength(2)
    expect(podcastsApi.getPodcastReadiness).not.toHaveBeenCalled()
    expect(podcastsApi.submitStudioPodcast).not.toHaveBeenCalled()
  })

  it('can promote its title to the route-level heading without changing the embedded default', () => {
    render(<PodcastStudio headingLevel={1} seedDocumentIds={[]} />)

    expect(screen.getByRole('heading', { name: 'Podcast Intelligence Studio', level: 1 })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Podcast Intelligence Studio', level: 2 })).not.toBeInTheDocument()
  })

  it('keeps an editable editorial brief local until a later confirmation', () => {
    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} />)

    fireEvent.change(screen.getByLabelText('Central question'), {
      target: { value: 'What changes after the research is connected?' },
    })
    fireEvent.change(screen.getByLabelText('Audience'), { target: { value: 'expert' } })

    expect(screen.getByLabelText('Central question')).toHaveValue('What changes after the research is connected?')
    expect(screen.getByLabelText('Audience')).toHaveValue('expert')
    expect(screen.getByText('Opening the Studio does not submit a production job.')).toBeVisible()
  })

  it('keeps model overrides local to the controller until a later confirmation', () => {
    render(<PodcastStudio
      seedDocumentIds={['knowledge_engine_document:plan']}
      modelPlans={[{
        stage: 'outline', label: 'Outline route', overrideChoices: ['outline-local', 'outline-alt'],
        plan: { outcome: 'ready', reason: 'Verified local route.', modelId: 'outline-local', role: 'podcast_outline' },
      }]}
    />)

    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'outline-alt' } })
    expect(screen.getByLabelText('Override Outline route model')).toHaveValue('outline-alt')
    expect(podcastsApi.getPodcastReadiness).not.toHaveBeenCalled()
    expect(podcastsApi.submitStudioPodcast).not.toHaveBeenCalled()
  })

  it('fences stale deferred readiness before a fresh override can confirm and submit', async () => {
    const staleReadiness = deferred<PodcastReadiness>()
    vi.mocked(podcastsApi.getPodcastReadiness)
      .mockImplementationOnce(() => staleReadiness.promise)
      .mockResolvedValueOnce(readinessForOverride('model-b', 'Fresh model-b fingerprint'))
    vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([{
      id: 'episode_profile:local', name: 'Local Episode', description: '', speaker_config: 'Local Voice', default_briefing: '', num_segments: 4,
    }])
    vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([{
      id: 'speaker_profile:local', name: 'Local Voice', description: '', speakers: [],
    }])
    vi.mocked(podcastsApi.submitStudioPodcast).mockResolvedValue({
      jobId: 'command:model-b', status: 'submitted', message: 'accepted', episodeProfile: 'Local Episode', episodeName: 'Fresh model-b fingerprint', mode: 'deep_dive',
    })

    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} modelPlans={[{
      stage: 'outline', label: 'Outline route', overrideChoices: ['model-a', 'model-b'],
      plan: { outcome: 'ready', reason: 'Preloaded route.', modelId: 'model-a', role: 'podcast_outline' },
    }]} />)

    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'model-a' } })
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
    await waitFor(() => expect(podcastsApi.getPodcastReadiness).toHaveBeenCalledWith(
      [{ kind: 'knowledge_document', documentId: 'knowledge_engine_document:plan' }],
      { productionOverrides: { podcast_outline: 'model-a' } },
    ))

    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'model-b' } })
    expect(screen.getByRole('button', { name: 'Prepare production review' })).toBeEnabled()

    await act(async () => {
      staleReadiness.resolve(readinessForOverride('model-a', 'Stale model-a fingerprint'))
      await staleReadiness.promise
    })

    expect(screen.queryByText('Stale model-a fingerprint')).not.toBeInTheDocument()
    expect(screen.queryByText('model-a route is ready.')).not.toBeInTheDocument()
    expect(screen.queryByText('Production profiles')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Continue to confirmation' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
    expect(await screen.findByText('Fresh model-b fingerprint')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Continue to confirmation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))
    await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalledWith(expect.objectContaining({
      selectionFingerprint: 'b'.repeat(64), productionOverrides: { podcast_outline: 'model-b' },
    })))
  })

  it('does not surface an error from an invalidated deferred readiness request', async () => {
    const staleReadiness = deferred<PodcastReadiness>()
    vi.mocked(podcastsApi.getPodcastReadiness).mockImplementationOnce(() => staleReadiness.promise)

    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} modelPlans={[{
      stage: 'outline', label: 'Outline route', overrideChoices: ['model-a', 'model-b'],
      plan: { outcome: 'ready', reason: 'Preloaded route.', modelId: 'model-a', role: 'podcast_outline' },
    }]} />)

    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'model-a' } })
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
    await waitFor(() => expect(podcastsApi.getPodcastReadiness).toHaveBeenCalledTimes(1))
    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'model-b' } })

    await act(async () => {
      staleReadiness.reject(new Error('stale readiness failed'))
      await staleReadiness.promise.catch(() => undefined)
    })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Prepare production review' })).toBeEnabled()
  })

  it('uses blocked readiness plans over stale Knowledge plans after an override review', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'f'.repeat(64), entries: [{
          stableId: 'knowledge_engine_document:plan', title: 'Research plan', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'a'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: ['Override is blocked by the planner.'],
      },
      stagePlans: [{
        role: 'podcast_outline', outcome: 'blocked', modelId: null, provider: null,
        resourceTier: null, selectionSource: 'automatic', reason: 'Override is blocked by the planner.', blockedReason: 'Override is blocked by the planner.',
        overrideChoices: ['outline-local'],
      }],
      ready: false, blockedReasons: ['Override is blocked by the planner.'],
    })
    vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([])
    vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([])

    render(<PodcastStudio
      seedDocumentIds={['knowledge_engine_document:plan']}
      modelPlans={[{
        stage: 'outline', label: 'Outline route', overrideChoices: ['outline-local', 'outline-alt'],
        plan: {
          outcome: 'ready', reason: 'Stale Knowledge route is ready.', modelId: 'outline-local', provider: 'stale-provider',
          resourceTier: 'standard', selectionSource: 'automatic', role: 'podcast_outline',
        },
      }]}
    />)

    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'outline-alt' } })
    expect(screen.getByText('Override pending review')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))

    expect((await screen.findAllByText('Override is blocked by the planner.')).length).toBeGreaterThan(0)
    expect(screen.getByText('Blocked')).toBeVisible()
    expect(screen.queryByText('Stale Knowledge route is ready.')).not.toBeInTheDocument()
    expect(screen.queryByText(/stale-provider/)).not.toBeInTheDocument()
    expect(screen.getByText('Selection source: automatic')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Continue to confirmation' })).toBeDisabled()
  })

  it('sends the selected production override to both readiness and submit', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'e'.repeat(64), entries: [{
          stableId: 'knowledge_engine_document:plan', title: 'Research plan', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'f'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      },
      stagePlans: [{
        role: 'podcast_outline', outcome: 'ready', modelId: 'outline-alt', provider: 'mlx',
        resourceTier: 'standard', selectionSource: 'production_override', reason: 'override', blockedReason: null,
        overrideChoices: ['outline-alt', 'outline-heavy'],
      }],
      ready: true, blockedReasons: [],
    })
    vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([{
      id: 'episode_profile:local', name: 'Local Episode', description: '', speaker_config: 'Local Voice', default_briefing: '', num_segments: 4,
    }])
    vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([{
      id: 'speaker_profile:local', name: 'Local Voice', description: '', speakers: [],
    }])
    vi.mocked(podcastsApi.submitStudioPodcast).mockResolvedValue({
      jobId: 'command:override', status: 'submitted', message: 'accepted', episodeProfile: 'Local Episode', episodeName: 'Research plan', mode: 'deep_dive',
    })

    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} modelPlans={[{
      stage: 'outline', label: 'Outline route', overrideChoices: ['outline-local', 'outline-alt'],
      plan: { outcome: 'ready', reason: 'Verified local route.', modelId: 'outline-local', role: 'podcast_outline' },
    }]} />)
    fireEvent.change(screen.getByLabelText('Override Outline route model'), { target: { value: 'outline-alt' } })
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
    await screen.findByText('Production profiles')
    expect(podcastsApi.getPodcastReadiness).toHaveBeenCalledWith(
      [{ kind: 'knowledge_document', documentId: 'knowledge_engine_document:plan' }],
      { productionOverrides: { podcast_outline: 'outline-alt' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'Continue to confirmation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))
    await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalledWith(expect.objectContaining({
      productionOverrides: { podcast_outline: 'outline-alt' },
    })))
  })

  it('moves outline segments with explicit keyboard-accessible controls', () => {
    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} />)

    fireEvent.click(screen.getByRole('button', { name: 'Move Findings earlier' }))

    expect(screen.getByRole('status')).toHaveTextContent('Findings moved to position 1')
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Move Findings earlier' }))
  })

  it('checks readiness only after explicit review and submits the editorial brief after confirmation', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'a'.repeat(64), entries: [{
          stableId: 'knowledge_engine_document:plan', title: 'Research plan', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'b'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    })
    vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([{
      id: 'episode_profile:local', name: 'Local Episode', description: '', speaker_config: 'Local Voice',
      default_briefing: '', num_segments: 4,
    }])
    vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([{
      id: 'speaker_profile:local', name: 'Local Voice', description: '', speakers: [],
    }])
    vi.mocked(podcastsApi.submitStudioPodcast).mockResolvedValue({
      jobId: 'command:podcast-one', status: 'submitted', message: 'accepted',
      episodeProfile: 'Local Episode', episodeName: 'Research plan', mode: 'deep_dive',
    })

    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} />)

    expect(podcastsApi.getPodcastReadiness).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('Central question'), {
      target: { value: 'What should change after the research?' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))

    expect(await screen.findByText('Production profiles')).toBeVisible()
    expect(podcastsApi.getPodcastReadiness).toHaveBeenCalledWith([
      { kind: 'knowledge_document', documentId: 'knowledge_engine_document:plan' },
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Continue to confirmation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))

    await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalledWith(expect.objectContaining({
      selections: [{ kind: 'knowledge_document', documentId: 'knowledge_engine_document:plan' }],
      editorialBrief: {
        centralQuestion: 'What should change after the research?',
        audience: 'practitioner',
        purpose: 'explain',
        format: 'deep_dive',
        targetMinutes: 20,
        requiredTakeaway: null,
        includeUnansweredQuestions: false,
        evidencePolicy: 'strict',
        episodeProfileName: 'Local Episode',
        speakerProfileName: 'Local Voice',
        outline: ['Introduction', 'Findings', 'Takeaway'],
      },
      reviewOutline: true,
    })))
  })

  it('keeps the honest briefing state after a failed pre-submission request', async () => {
    vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue({
      preview: {
        selectionFingerprint: 'c'.repeat(64), entries: [{
          stableId: 'knowledge_engine_document:plan', title: 'Research plan', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'd'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    })
    vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([{
      id: 'episode_profile:local', name: 'Local Episode', description: '', speaker_config: 'Local Voice',
      default_briefing: '', num_segments: 4,
    }])
    vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([{
      id: 'speaker_profile:local', name: 'Local Voice', description: '', speakers: [],
    }])
    vi.mocked(podcastsApi.submitStudioPodcast).mockRejectedValueOnce(new Error('submit failed'))

    render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} />)
    fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
    await screen.findByText('Production profiles')
    fireEvent.click(screen.getByRole('button', { name: 'Continue to confirmation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))

    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('could not be submitted'))
    expect(screen.getByRole('tab', { name: 'Editorial Brief' })).toHaveAttribute('data-status', 'current')
    expect(screen.getByRole('tab', { name: 'Outline Storyboard' })).toHaveAttribute('data-status', 'upcoming')
    expect(screen.getByRole('tab', { name: 'Script/Voice Job' })).toHaveAttribute('data-status', 'upcoming')
  })

  // v0.8.127 — the Studio's `notebookId` prop (sourced from the store) must
  // reach the submit call so the resulting episode is notebook-scoped.
  describe('notebookId', () => {
    const readyReadiness: PodcastReadiness = {
      preview: {
        selectionFingerprint: 'd'.repeat(64), entries: [{
          stableId: 'knowledge_engine_document:plan', title: 'Research plan', authorityKind: 'app_owned',
          relativeLocator: null, revisionId: null, fingerprint: 'e'.repeat(64),
          state: 'included', reason: 'included', estimatedCharacters: 120,
        }], includedCharacters: 120, requiresBatchEngine: false,
        currentWorkerEligible: true, blockedReasons: [],
      }, stagePlans: [], ready: true, blockedReasons: [],
    }

    beforeEach(() => {
      vi.mocked(podcastsApi.getPodcastReadiness).mockResolvedValue(readyReadiness)
      vi.mocked(podcastsApi.listEpisodeProfiles).mockResolvedValue([{
        id: 'episode_profile:local', name: 'Local Episode', description: '', speaker_config: 'Local Voice',
        default_briefing: '', num_segments: 4,
      }])
      vi.mocked(podcastsApi.listSpeakerProfiles).mockResolvedValue([{
        id: 'speaker_profile:local', name: 'Local Voice', description: '', speakers: [],
      }])
      vi.mocked(podcastsApi.submitStudioPodcast).mockResolvedValue({
        jobId: 'command:notebook-scoped', status: 'submitted', message: 'accepted',
        episodeProfile: 'Local Episode', episodeName: 'Research plan', mode: 'deep_dive',
      })
    })

    it('submits with the notebookId prop when the Studio was opened from a notebook', async () => {
      render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} notebookId="notebook:research" />)

      fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
      await screen.findByText('Production profiles')
      fireEvent.click(screen.getByRole('button', { name: 'Continue to confirmation' }))
      fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))

      await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalledWith(
        expect.objectContaining({ notebookId: 'notebook:research' }),
      ))
    })

    it('omits notebookId when the Studio was opened globally', async () => {
      render(<PodcastStudio seedDocumentIds={['knowledge_engine_document:plan']} />)

      fireEvent.click(screen.getByRole('button', { name: 'Prepare production review' }))
      await screen.findByText('Production profiles')
      fireEvent.click(screen.getByRole('button', { name: 'Continue to confirmation' }))
      fireEvent.click(screen.getByRole('button', { name: 'Confirm production' }))

      await waitFor(() => expect(podcastsApi.submitStudioPodcast).toHaveBeenCalled())
      const call = vi.mocked(podcastsApi.submitStudioPodcast).mock.calls[0][0]
      expect(call.notebookId).toBeUndefined()
    })
  })
})
