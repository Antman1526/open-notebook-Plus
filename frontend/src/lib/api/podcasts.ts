import apiClient from './client'
import { getApiUrl } from '@/lib/config'
import {
  PodcastEpisode,
  EpisodeProfile,
  SpeakerProfile,
  Language,
  PodcastGenerationRequest,
  PodcastGenerationResponse,
  PodcastOverviewMode,
  PodcastReadiness,
  PodcastSelectionPreview,
  PodcastSelectionPreviewEntry,
  PodcastStageModelPlan,
  PodcastProductionRole,
  PodcastEditorialIntent,
  PodcastStudioSubmitResponse,
} from '@/lib/types/podcasts'
import {
  normalizePodcastSelections,
  fromPodcastSelectionWire,
  toPodcastSelectionWire,
  type PodcastSelection,
} from '@/lib/podcasts/selection'

export type EpisodeProfileInput = Omit<EpisodeProfile, 'id'>
export type SpeakerProfileInput = Omit<SpeakerProfile, 'id'>

interface PodcastSelectionPreviewWire {
  selection_fingerprint: string
  entries: Array<{
    stable_id: string
    title: string
    authority_kind: PodcastSelectionPreviewEntry['authorityKind']
    relative_locator: string | null
    revision_id: string | null
    fingerprint: string | null
    state: PodcastSelectionPreviewEntry['state']
    reason: string
    estimated_characters: number
  }>
  included_characters: number
  requires_batch_engine: boolean
  current_worker_eligible: boolean
  blocked_reasons: string[]
}

interface PodcastReadinessWire {
  preview: PodcastSelectionPreviewWire
  stage_plans: Array<{
    role: PodcastStageModelPlan['role']
    outcome: PodcastStageModelPlan['outcome']
    model_id: string | null
    provider: string | null
    resource_tier: PodcastStageModelPlan['resourceTier']
    selection_source: PodcastStageModelPlan['selectionSource']
    reason: string
    blocked_reason: string | null
    override_choices?: string[]
  }>
  ready: boolean
  blocked_reasons: string[]
}

interface PodcastStudioSubmitWire {
  job_id: string
  status: 'submitted'
  message: string
  episode_profile: string
  episode_name: string
  mode: PodcastOverviewMode
}

interface PodcastRetryPreviewRequiredWire {
  status: 'preview_required'
  code: 'podcast_selection_changed' | 'podcast_selection_unavailable' | 'podcast_selection_tampered'
  message: string
  episode_id: string
  selections: unknown[]
  selection_fingerprint?: string | null
  preview?: PodcastSelectionPreviewWire | null
}

interface PodcastRetrySubmittedWire {
  job_id: string
  message: string
}

export interface PodcastRetryPreviewRequired {
  status: 'preview_required'
  code: PodcastRetryPreviewRequiredWire['code']
  message: string
  episodeId: string
  selections: PodcastSelection[]
  selectionFingerprint: string | null
  preview: PodcastSelectionPreview | null
}

export interface PodcastRetrySubmitted {
  status: 'submitted'
  jobId: string
  message: string
}

export type PodcastRetryResult = PodcastRetryPreviewRequired | PodcastRetrySubmitted

function toPodcastSelectionPreview(wire: PodcastSelectionPreviewWire): PodcastSelectionPreview {
  return {
    selectionFingerprint: wire.selection_fingerprint,
    entries: wire.entries.map((entry) => ({
      stableId: entry.stable_id,
      title: entry.title,
      authorityKind: entry.authority_kind,
      relativeLocator: entry.relative_locator,
      revisionId: entry.revision_id,
      fingerprint: entry.fingerprint,
      state: entry.state,
      reason: entry.reason,
      estimatedCharacters: entry.estimated_characters,
    })),
    includedCharacters: wire.included_characters,
    requiresBatchEngine: wire.requires_batch_engine,
    currentWorkerEligible: wire.current_worker_eligible,
    blockedReasons: wire.blocked_reasons,
  }
}

function toPodcastReadiness(
  wire: PodcastReadinessWire,
  productionOverrides: Partial<Record<PodcastProductionRole, string>> = {},
): PodcastReadiness {
  return {
    preview: toPodcastSelectionPreview(wire.preview),
    stagePlans: wire.stage_plans.map((plan) => ({
      role: plan.role,
      outcome: plan.outcome,
      modelId: plan.model_id,
      provider: plan.provider,
      resourceTier: plan.resource_tier,
      selectionSource: plan.selection_source,
      reason: plan.reason,
      blockedReason: plan.blocked_reason,
      overrideChoices: plan.override_choices ?? [],
    })),
    ready: wire.ready,
    blockedReasons: wire.blocked_reasons,
    productionOverrides,
  }
}

export async function resolvePodcastAssetUrl(path?: string | null): Promise<string | undefined> {
  if (!path) {
    return undefined
  }

  if (/^https?:\/\//i.test(path)) {
    return path
  }

  const base = await getApiUrl()

  if (path.startsWith('/')) {
    return `${base}${path}`
  }

  return `${base}/${path}`
}

export const podcastsApi = {
  previewPodcastSelection: async (selections: PodcastSelection[]) => {
    const response = await apiClient.post<PodcastSelectionPreviewWire>(
      '/podcasts/selection/preview',
      { selections: normalizePodcastSelections(selections).map(toPodcastSelectionWire) },
    )
    return toPodcastSelectionPreview(response.data)
  },

  getPodcastReadiness: async (
    selections: PodcastSelection[],
    options: {
      executionPolicy?: 'strict_local' | 'local_preferred' | 'custom'
      computeProfile?: 'efficient' | 'balanced' | 'maximum_quality'
      includeTranscription?: boolean
      productionOverrides?: Partial<Record<PodcastProductionRole, string>>
    } = {},
  ) => {
    const response = await apiClient.post<PodcastReadinessWire>('/podcasts/readiness', {
      selections: normalizePodcastSelections(selections).map(toPodcastSelectionWire),
      execution_policy: options.executionPolicy ?? 'strict_local',
      compute_profile: options.computeProfile ?? 'balanced',
      include_transcription: options.includeTranscription ?? false,
      production_overrides: options.productionOverrides ?? {},
    })
    return toPodcastReadiness(response.data, options.productionOverrides)
  },

  submitStudioPodcast: async (payload: {
    selections: PodcastSelection[]
    selectionFingerprint: string
    idempotencyKey: string
    episodeProfile: string
    speakerProfile: string
    episodeName: string
    // v0.8.127 — set when the Studio was opened from a notebook so the
    // resulting episode is notebook-scoped, like the standard generation
    // path (v0.8.126). Undefined when opened globally.
    notebookId?: string
    mode?: PodcastOverviewMode
    customPrompt?: string | null
    episodeLength?: 'short' | 'medium' | 'long' | null
    reviewOutline?: boolean
    editorialBrief?: Partial<PodcastEditorialIntent> | {
      centralQuestion?: string | null
      audience?: string | null
      purpose?: string | null
      format?: PodcastOverviewMode | null
      targetMinutes?: number | null
      requiredTakeaway?: string | null
      includeUnansweredQuestions?: boolean | null
      evidencePolicy?: 'strict' | 'interpretation' | string | null
      episodeProfileName?: string | null
      speakerProfileName?: string | null
      outline?: string[]
    } | null
    executionPolicy?: 'strict_local' | 'local_preferred' | 'custom'
    computeProfile?: 'efficient' | 'balanced' | 'maximum_quality'
    includeTranscription?: boolean
    productionOverrides?: Partial<Record<PodcastProductionRole, string>>
  }): Promise<PodcastStudioSubmitResponse> => {
    const response = await apiClient.post<PodcastStudioSubmitWire>('/podcasts/studio/submit', {
      selections: normalizePodcastSelections(payload.selections).map(toPodcastSelectionWire),
      selection_fingerprint: payload.selectionFingerprint,
      idempotency_key: payload.idempotencyKey,
      confirmed: true,
      episode_profile: payload.episodeProfile,
      speaker_profile: payload.speakerProfile,
      episode_name: payload.episodeName,
      notebook_id: payload.notebookId ?? null,
      mode: payload.mode ?? 'deep_dive',
      custom_prompt: payload.customPrompt ?? null,
      episode_length: payload.episodeLength ?? null,
      review_outline: payload.reviewOutline ?? true,
      editorial_brief: payload.editorialBrief ? {
        central_question: payload.editorialBrief.centralQuestion ?? null,
        audience: payload.editorialBrief.audience ?? null,
        purpose: payload.editorialBrief.purpose ?? null,
        format: payload.editorialBrief.format ?? null,
        target_minutes: payload.editorialBrief.targetMinutes ?? null,
        required_takeaway: payload.editorialBrief.requiredTakeaway ?? null,
        include_unanswered_questions: payload.editorialBrief.includeUnansweredQuestions ?? null,
        evidence_policy: payload.editorialBrief.evidencePolicy ?? null,
        episode_profile_name: payload.editorialBrief.episodeProfileName ?? null,
        speaker_profile_name: payload.editorialBrief.speakerProfileName ?? null,
        outline: payload.editorialBrief.outline ?? [],
      } : null,
      execution_policy: payload.executionPolicy ?? 'strict_local',
      compute_profile: payload.computeProfile ?? 'balanced',
      include_transcription: payload.includeTranscription ?? false,
      production_overrides: payload.productionOverrides ?? {},
    })
    return {
      jobId: response.data.job_id,
      status: response.data.status,
      message: response.data.message,
      episodeProfile: response.data.episode_profile,
      episodeName: response.data.episode_name,
      mode: response.data.mode,
    }
  },

  listEpisodes: async () => {
    const response = await apiClient.get<PodcastEpisode[]>('/podcasts/episodes')
    return response.data
  },

  deleteEpisode: async (episodeId: string) => {
    await apiClient.delete(`/podcasts/episodes/${episodeId}`)
  },

  retryEpisode: async (episodeId: string) => {
    const response = await apiClient.post<PodcastRetrySubmittedWire | PodcastRetryPreviewRequiredWire>(
      `/podcasts/episodes/${episodeId}/retry`
    )
    if ('job_id' in response.data) {
      return {
        status: 'submitted' as const,
        jobId: response.data.job_id,
        message: response.data.message,
      } satisfies PodcastRetrySubmitted
    }
    if (response.data.status === 'preview_required') {
      return {
        status: 'preview_required' as const,
        code: response.data.code,
        message: response.data.message,
        episodeId: response.data.episode_id,
        selections: response.data.selections.map(fromPodcastSelectionWire),
        selectionFingerprint: response.data.selection_fingerprint ?? null,
        preview: response.data.preview
          ? toPodcastSelectionPreview(response.data.preview)
          : null,
      } satisfies PodcastRetryPreviewRequired
    }
    throw new Error('Unexpected podcast retry response')
  },

  // v0.8.68 — cancel an in-flight generation (worker polls the flag).
  cancelEpisode: async (episodeId: string) => {
    const response = await apiClient.post<{ message: string }>(
      `/podcasts/episodes/${episodeId}/cancel`
    )
    return response.data
  },

  // v0.8.68 — outline-review workflow.
  updateEpisodeOutline: async (
    episodeId: string,
    segments: import('@/lib/types/podcasts').OutlineSegment[],
  ) => {
    const response = await apiClient.put<{ message: string; outline: unknown }>(
      `/podcasts/episodes/${episodeId}/outline`,
      { segments }
    )
    return response.data
  },

  approveEpisodeOutline: async (episodeId: string) => {
    const response = await apiClient.post<{ job_id: string; message: string }>(
      `/podcasts/episodes/${episodeId}/approve-outline`
    )
    return response.data
  },

  listEpisodeProfiles: async () => {
    const response = await apiClient.get<EpisodeProfile[]>('/episode-profiles')
    return response.data
  },

  createEpisodeProfile: async (payload: EpisodeProfileInput) => {
    const response = await apiClient.post<EpisodeProfile>(
      '/episode-profiles',
      payload
    )
    return response.data
  },

  updateEpisodeProfile: async (profileId: string, payload: EpisodeProfileInput) => {
    const response = await apiClient.put<EpisodeProfile>(
      `/episode-profiles/${profileId}`,
      payload
    )
    return response.data
  },

  deleteEpisodeProfile: async (profileId: string) => {
    await apiClient.delete(`/episode-profiles/${profileId}`)
  },

  duplicateEpisodeProfile: async (profileId: string) => {
    const response = await apiClient.post<EpisodeProfile>(
      `/episode-profiles/${profileId}/duplicate`
    )
    return response.data
  },

  listSpeakerProfiles: async () => {
    const response = await apiClient.get<SpeakerProfile[]>('/speaker-profiles')
    return response.data
  },

  createSpeakerProfile: async (payload: SpeakerProfileInput) => {
    const response = await apiClient.post<SpeakerProfile>(
      '/speaker-profiles',
      payload
    )
    return response.data
  },

  updateSpeakerProfile: async (profileId: string, payload: SpeakerProfileInput) => {
    const response = await apiClient.put<SpeakerProfile>(
      `/speaker-profiles/${profileId}`,
      payload
    )
    return response.data
  },

  deleteSpeakerProfile: async (profileId: string) => {
    await apiClient.delete(`/speaker-profiles/${profileId}`)
  },

  duplicateSpeakerProfile: async (profileId: string) => {
    const response = await apiClient.post<SpeakerProfile>(
      `/speaker-profiles/${profileId}/duplicate`
    )
    return response.data
  },

  generatePodcast: async (payload: PodcastGenerationRequest) => {
    const response = await apiClient.post<PodcastGenerationResponse>(
      '/podcasts/generate',
      {
        // Make the new closed format explicit for callers compiled before
        // the dialog update; the backend keeps the same deep_dive default.
        ...payload,
        mode: payload.mode ?? 'deep_dive',
      }
    )
    return response.data
  },

  // v0.7.31 — heuristic auto-suggester. Given a notebook or list of
  // source IDs, returns recommended episode profile + length + title +
  // briefing addition. No LLM call; instant + deterministic.
  suggestEpisode: async (payload: {
    notebook_id?: string
    source_ids?: string[]
  }) => {
    const response = await apiClient.post<{
      episode_profile_name: string
      length_minutes: number
      title: string
      briefing_addition: string
      reasoning: string
      matched_signals: Record<string, number>
    }>('/podcasts/suggest', payload)
    return response.data
  },

  listLanguages: async () => {
    const response = await apiClient.get<Language[]>('/languages')
    return response.data
  },
}
