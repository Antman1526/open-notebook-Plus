'use client'

// v0.8.124 — mind-map node preview popover (improvement roadmap). Small,
// anchored card shown when a studio_artifact node whose artifact_type is
// podcast_audio or slide_deck is clicked in the mind map, so the user can
// play the audio / watch the video overview without leaving the canvas.
//
// There is no single-artifact GET wrapped on the frontend studioApi client
// (only listArtifacts(notebookId) and listArtifactRevisions(artifactId) —
// see frontend/src/lib/api/studio.ts, which this task must not touch), so
// this component fetches the notebook's artifact list and finds the one it
// needs client-side. That's why it takes `notebookId` in addition to
// `artifactId`.
//
// podcast_audio note: nothing in this codebase currently creates a
// StudioArtifact with artifact_type "podcast_audio" — the generic studio
// generation pipeline explicitly rejects it
// (deeper_notebook/studio/schemas: schema_for_artifact_type("podcast_audio")
// raises InvalidInputError), and PodcastEpisode is an unrelated domain model
// with no StudioArtifact reference. So the episode lookup below is
// best-effort only: it looks for a plausible episode id on the artifact's
// output_payload and falls back to the "unavailable" copy when it can't
// resolve one.
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Play, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { studioApi, type StudioArtifact } from '@/lib/api/studio'
import { podcastsApi, resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import { useAudioPlayerStore } from '@/lib/stores/audio-player-store'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'

export interface MindMapNodePreviewAnchor {
  x: number
  y: number
}

interface MindMapNodePreviewProps {
  notebookId: string
  artifactId: string
  artifactType?: string | null
  anchor: MindMapNodePreviewAnchor | null
  onClose: () => void
  onOpenArtifact: (artifactId: string) => void
}

// Copied from ArtifactRail.tsx's videoOverviewPayload() — deliberately not
// imported (ArtifactRail.tsx is owned by another engineer in this batch).
function videoOverviewPayload(value: unknown): { media_url: string; captions_url: string } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (typeof candidate.media_url !== 'string' || typeof candidate.captions_url !== 'string') {
    return null
  }
  return { media_url: candidate.media_url, captions_url: candidate.captions_url }
}

// Best-effort: the podcast_audio artifact_type has no established payload
// shape in this codebase (see module comment above). Check the couple of
// plausible field spellings rather than guessing further.
function podcastEpisodeIdFromPayload(payload: Record<string, unknown> | undefined): string | null {
  if (!payload) return null
  const candidate = payload.episode_id ?? payload.episodeId
  return typeof candidate === 'string' && candidate.length > 0 ? candidate : null
}

export default function MindMapNodePreview({
  notebookId,
  artifactId,
  artifactType,
  anchor,
  onClose,
  onOpenArtifact,
}: MindMapNodePreviewProps) {
  const { t } = useTranslation()
  const cardRef = useRef<HTMLDivElement>(null)
  const setEpisode = useAudioPlayerStore((state) => state.setEpisode)
  const [videoUrls, setVideoUrls] = useState<{ media: string; captions: string } | null>(null)

  const open = Boolean(anchor)

  const { data: artifact, isLoading } = useQuery({
    queryKey: ['studio', 'artifact', artifactId],
    queryFn: async (): Promise<StudioArtifact | null> => {
      const artifacts = await studioApi.listArtifacts(notebookId)
      return artifacts.find((candidate) => candidate.id === artifactId) ?? null
    },
    enabled: open && Boolean(notebookId) && Boolean(artifactId),
  })

  const resolvedType = artifactType ?? artifact?.artifact_type ?? null
  const isPodcastAudio = resolvedType === 'podcast_audio'
  const isSlideDeck = resolvedType === 'slide_deck'

  const episodeId = useMemo(
    () => podcastEpisodeIdFromPayload(artifact?.output_payload),
    [artifact],
  )

  const { data: episodes = [] } = useQuery({
    queryKey: ['podcasts', 'episodes'],
    queryFn: podcastsApi.listEpisodes,
    enabled: open && isPodcastAudio && Boolean(episodeId),
  })
  const episode = episodeId ? episodes.find((candidate) => candidate.id === episodeId) : undefined
  const episodeAudioPath = episode ? episode.audio_url ?? episode.audio_file ?? '' : ''

  const videoOverview = isSlideDeck
    ? videoOverviewPayload(artifact?.output_payload.video_overview)
    : null

  useEffect(() => {
    if (!videoOverview) {
      setVideoUrls(null)
      return
    }
    let active = true
    void Promise.all([
      resolvePodcastAssetUrl(videoOverview.media_url),
      resolvePodcastAssetUrl(videoOverview.captions_url),
    ]).then(([media, captions]) => {
      if (active && media && captions) setVideoUrls({ media, captions })
    })
    return () => {
      active = false
    }
  }, [videoOverview])

  useEffect(() => {
    if (!open) return
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
    }
    function handlePointerDown(event: MouseEvent) {
      if (cardRef.current && !cardRef.current.contains(event.target as Node)) {
        onClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    document.addEventListener('mousedown', handlePointerDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.removeEventListener('mousedown', handlePointerDown)
    }
  }, [open, onClose])

  if (!anchor) return null

  const style: CSSProperties = {
    left: anchor.x,
    top: anchor.y,
    transform: 'translate(-50%, 12px)',
  }

  return (
    <div
      ref={cardRef}
      role="dialog"
      aria-label={artifact?.title ?? t('mindMap.previewLoading', { defaultValue: 'Loading…' })}
      className="absolute z-20 w-64 rounded-lg border bg-background p-3 shadow-lg"
      style={style}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <p className="line-clamp-2 text-sm font-medium">
          {artifact?.title ?? (isLoading
            ? t('mindMap.previewLoading', { defaultValue: 'Loading…' })
            : '')}
        </p>
        <button
          type="button"
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground"
          aria-label={t('common.close', { defaultValue: 'Close' })}
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center py-4">
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
        </div>
      ) : (
        <div className="space-y-2">
          {isPodcastAudio && (
            episodeAudioPath ? (
              <Button
                type="button"
                size="sm"
                variant="outline"
                className="w-full gap-2"
                onClick={() =>
                  episode &&
                  setEpisode({
                    id: episode.id,
                    title: episode.name,
                    sourcePath: episodeAudioPath,
                    transcriptSegments: episode.transcript_segments ?? [],
                  })
                }
              >
                <Play className="h-3.5 w-3.5" />
                {t('mindMap.previewPlay', { defaultValue: 'Play' })}
              </Button>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t('mindMap.previewUnavailable', { defaultValue: 'Preview unavailable.' })}
              </p>
            )
          )}

          {isSlideDeck && (
            videoUrls ? (
              <video className="aspect-video w-full rounded border bg-black" controls preload="metadata">
                <source src={videoUrls.media} type="video/mp4" />
                <track kind="captions" src={videoUrls.captions} srcLang="en" label="English" default />
              </video>
            ) : (
              <p className="text-xs text-muted-foreground">
                {t('mindMap.previewOpen', { defaultValue: 'Open in Studio' })}
              </p>
            )
          )}

          {!isPodcastAudio && !isSlideDeck && (
            <p className="text-xs text-muted-foreground">
              {t('mindMap.previewOpen', { defaultValue: 'Open in Studio' })}
            </p>
          )}
        </div>
      )}

      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={cn('mt-2 w-full')}
        onClick={() => onOpenArtifact(artifactId)}
      >
        {t('mindMap.previewOpen', { defaultValue: 'Open in Studio' })}
      </Button>
    </div>
  )
}
