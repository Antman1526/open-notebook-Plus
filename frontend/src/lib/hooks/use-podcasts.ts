import { useMemo } from 'react'
import { useRouter } from 'next/navigation'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { podcastsApi, EpisodeProfileInput, SpeakerProfileInput } from '@/lib/api/podcasts'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { useToast } from '@/lib/hooks/use-toast'
import { useTranslation } from '@/lib/hooks/use-translation'
// v0.7.196 — see use-models.ts: getApiErrorKey returns a raw i18n key
// string. Direct use as a toast description renders the key as text
// to the user on mapped errors.
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'
import {
  ACTIVE_EPISODE_STATUSES,
  EpisodeProfile,
  EpisodeStatusGroups,
  PodcastEpisode,
  PodcastGenerationRequest,
  groupEpisodesByStatus,
  speakerUsageMap,
} from '@/lib/types/podcasts'

// v0.8.119 — the podcasts.*Desc toasts carry a single-brace `{name}`
// placeholder (the app's manual-interpolation convention) but were called
// with a bare t(key), so English users saw a literal "{name}". Substitute
// the profile name; on delete the name is read from the cached list before
// the invalidation clears it.
function describeWithName(text: string, name: string | undefined): string {
  return text.replace('{name}', name ?? '')
}

function cachedProfileName(
  queryClient: ReturnType<typeof useQueryClient>,
  queryKey: readonly unknown[],
  profileId: string,
): string | undefined {
  const list = queryClient.getQueryData<Array<{ id: string; name?: string }>>(queryKey)
  return list?.find((item) => item.id === profileId)?.name
}


export function useLanguages() {
  return useQuery({
    queryKey: QUERY_KEYS.languages,
    queryFn: podcastsApi.listLanguages,
    staleTime: Infinity,
  })
}

interface EpisodeStatusCounts {
  total: number
  running: number
  completed: number
  failed: number
  pending: number
}

function hasActiveEpisodes(episodes: PodcastEpisode[]) {
  return episodes.some((episode) => {
    const status = episode.job_status ?? 'unknown'
    return ACTIVE_EPISODE_STATUSES.includes(status)
  })
}

export function usePodcastEpisodes(options?: { autoRefresh?: boolean }) {
  const { autoRefresh = true } = options ?? {}

  const query = useQuery({
    queryKey: QUERY_KEYS.podcastEpisodes,
    queryFn: podcastsApi.listEpisodes,
    refetchInterval: (current) => {
      if (!autoRefresh) {
        return false
      }

      const data = current.state.data as PodcastEpisode[] | undefined
      if (!data || data.length === 0) {
        return false
      }

      return hasActiveEpisodes(data) ? 15_000 : false
    },
  })

  const episodes = useMemo(() => query.data ?? [], [query.data])

  const statusGroups = useMemo<EpisodeStatusGroups>(
    () => groupEpisodesByStatus(episodes),
    [episodes]
  )

  const statusCounts = useMemo<EpisodeStatusCounts>(
    () => ({
      total: episodes.length,
      running: statusGroups.running.length,
      completed: statusGroups.completed.length,
      failed: statusGroups.failed.length,
      pending: statusGroups.pending.length,
    }),
    [episodes.length, statusGroups]
  )

  const active = useMemo(() => hasActiveEpisodes(episodes), [episodes])

  return {
    ...query,
    episodes,
    statusGroups,
    statusCounts,
    hasActiveEpisodes: active,
  }
}

export function useRetryPodcastEpisode() {
  const queryClient = useQueryClient()
  const router = useRouter()
  const openStudio = usePodcastStudioStore((state) => state.open)
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (episodeId: string) => podcastsApi.retryEpisode(episodeId),
    onSuccess: async (response) => {
      await queryClient.refetchQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      if (response.status === 'preview_required') {
        openStudio(response.selections, 'studio')
        router.push('/podcasts/studio')
        return
      }
      toast({
        title: t('podcasts.retryStarted'),
        description: t('podcasts.retryStartedDesc'),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToRetry'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

// v0.8.68 — cancel an in-flight generation.
export function useCancelPodcastEpisode() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (episodeId: string) => podcastsApi.cancelEpisode(episodeId),
    onSuccess: async () => {
      await queryClient.refetchQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.cancelRequested', { defaultValue: 'Cancellation requested' }),
        description: t('podcasts.cancelRequestedDesc', {
          defaultValue: 'The generation will stop within a few seconds.',
        }),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToCancel', { defaultValue: 'Could not cancel' }),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

// v0.8.68 — outline-review workflow: save edits + approve.
export function useUpdateEpisodeOutline() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({
      episodeId,
      segments,
    }: {
      episodeId: string
      segments: import('@/lib/types/podcasts').OutlineSegment[]
    }) => podcastsApi.updateEpisodeOutline(episodeId, segments),
    onSuccess: async () => {
      await queryClient.refetchQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.outlineSaved', { defaultValue: 'Outline saved' }),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToSaveOutline', { defaultValue: 'Could not save outline' }),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useApproveEpisodeOutline() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (episodeId: string) => podcastsApi.approveEpisodeOutline(episodeId),
    onSuccess: async () => {
      await queryClient.refetchQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.outlineApproved', { defaultValue: 'Outline approved' }),
        description: t('podcasts.outlineApprovedDesc', {
          defaultValue: 'Generating transcript and audio…',
        }),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToApprove', { defaultValue: 'Could not approve outline' }),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useDeletePodcastEpisode() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (episodeId: string) => podcastsApi.deleteEpisode(episodeId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.episodeDeleted'),
        description: t('podcasts.episodeDeletedDesc'),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToDeleteEpisode'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useEpisodeProfiles() {
  const query = useQuery({
    queryKey: QUERY_KEYS.episodeProfiles,
    queryFn: podcastsApi.listEpisodeProfiles,
  })

  return {
    ...query,
    episodeProfiles: query.data ?? [],
  }
}

export function useCreateEpisodeProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (payload: EpisodeProfileInput) =>
      podcastsApi.createEpisodeProfile(payload),
    onSuccess: (_data, payload) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.profileCreated'),
        description: describeWithName(t('podcasts.profileCreatedDesc'), payload.name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToCreateProfile'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useUpdateEpisodeProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({
      profileId,
      payload,
    }: {
      profileId: string
      payload: EpisodeProfileInput
    }) => podcastsApi.updateEpisodeProfile(profileId, payload),
    onSuccess: (_data, { payload }) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.profileUpdated'),
        description: describeWithName(t('podcasts.profileUpdatedDesc'), payload.name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToUpdateProfile'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useDeleteEpisodeProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (profileId: string) => podcastsApi.deleteEpisodeProfile(profileId),
    onSuccess: (_data, profileId) => {
      const name = cachedProfileName(queryClient, QUERY_KEYS.episodeProfiles, profileId)
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.profileDeleted'),
        description: describeWithName(t('podcasts.profileDeletedDesc'), name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToDeleteProfile'),
        description: getApiErrorMessage(error, t, 'podcasts.failedToDeleteProfileDesc'),
        variant: 'destructive',
      })
    },
  })
}

export function useDuplicateEpisodeProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (profileId: string) =>
      podcastsApi.duplicateEpisodeProfile(profileId),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.profileDuplicated'),
        description: describeWithName(t('podcasts.profileDuplicatedDesc'), created?.name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToDuplicateProfile'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useSpeakerProfiles(episodeProfiles?: EpisodeProfile[]) {
  const query = useQuery({
    queryKey: QUERY_KEYS.speakerProfiles,
    queryFn: podcastsApi.listSpeakerProfiles,
  })

  const speakerProfiles = useMemo(() => query.data ?? [], [query.data])

  const usage = useMemo(
    () => speakerUsageMap(speakerProfiles, episodeProfiles),
    [speakerProfiles, episodeProfiles]
  )

  return {
    ...query,
    speakerProfiles,
    usage,
  }
}

export function useCreateSpeakerProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (payload: SpeakerProfileInput) =>
      podcastsApi.createSpeakerProfile(payload),
    onSuccess: (_data, payload) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.speakerProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.speakerCreated'),
        description: describeWithName(t('podcasts.speakerCreatedDesc'), payload.name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToCreateSpeaker'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useUpdateSpeakerProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: ({
      profileId,
      payload,
    }: {
      profileId: string
      payload: SpeakerProfileInput
    }) => podcastsApi.updateSpeakerProfile(profileId, payload),
    onSuccess: (_data, { payload }) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.speakerProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.speakerUpdated'),
        description: describeWithName(t('podcasts.speakerUpdatedDesc'), payload.name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToUpdateSpeaker'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useDeleteSpeakerProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (profileId: string) => podcastsApi.deleteSpeakerProfile(profileId),
    onSuccess: (_data, profileId) => {
      const name = cachedProfileName(queryClient, QUERY_KEYS.speakerProfiles, profileId)
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.speakerProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.episodeProfiles })
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.speakerDeleted'),
        description: describeWithName(t('podcasts.speakerDeletedDesc'), name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToDeleteSpeaker'),
        description: getApiErrorMessage(error, t, 'podcasts.failedToDeleteSpeakerDesc'),
        variant: 'destructive',
      })
    },
  })
}

export function useDuplicateSpeakerProfile() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (profileId: string) =>
      podcastsApi.duplicateSpeakerProfile(profileId),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: QUERY_KEYS.speakerProfiles })
      toast({
        title: t('podcasts.speakerDuplicated'),
        description: describeWithName(t('podcasts.speakerDuplicatedDesc'), created?.name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToDuplicateSpeaker'),
        description: getApiErrorMessage(error, t, 'common.error'),
        variant: 'destructive',
      })
    },
  })
}

export function useGeneratePodcast() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const { t } = useTranslation()

  return useMutation({
    mutationFn: (payload: PodcastGenerationRequest) =>
      podcastsApi.generatePodcast(payload),
    onSuccess: async (response) => {
      // Immediately refetch to show the new episode
      await queryClient.refetchQueries({ queryKey: QUERY_KEYS.podcastEpisodes })
      toast({
        title: t('podcasts.generationStarted'),
        description: t('podcasts.generationStartedDesc').replace('{name}', response.episode_name),
      })
    },
    onError: (error: unknown) => {
      toast({
        title: t('podcasts.failedToStartGeneration'),
        description: getApiErrorMessage(error, t, 'podcasts.tryAgainMoment'),
        variant: 'destructive',
      })
    },
  })
}
