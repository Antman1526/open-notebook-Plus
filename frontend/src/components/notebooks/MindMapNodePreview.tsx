'use client'

// v0.8.124 — mind-map node preview popover (improvement roadmap). Small,
// anchored card shown when a studio_artifact node whose artifact_type is
// podcast_audio or slide_deck is clicked in the mind map, so the user can
// play the audio / watch the video overview without leaving the canvas.
//
// v0.8.125 — extended to source and note nodes (improvement roadmap): a
// plain click on a source/note node now opens this same popover (title +
// a short excerpt) instead of navigating straight to its dialog;
// Shift-click keeps the direct navigation. studio_artifact behavior is
// unchanged.
//
// There is no single-artifact GET wrapped on the frontend studioApi client
// (only listArtifacts(notebookId) and listArtifactRevisions(artifactId) —
// see frontend/src/lib/api/studio.ts, which this task must not touch), so
// this component fetches the notebook's artifact list and finds the one it
// needs client-side. That's why it takes `notebookId` in addition to
// `nodeId`.
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
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type RefObject } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2, Play, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { studioApi, type StudioArtifact } from '@/lib/api/studio'
import { podcastsApi, resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import { sourcesApi } from '@/lib/api/sources'
import { notesApi } from '@/lib/api/notes'
import { useAudioPlayerStore } from '@/lib/stores/audio-player-store'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'
import { SourceCover } from '@/components/deeper-notebook/source-gallery/SourceCover'
import type { SourceDetailResponse } from '@/lib/types/api'

export interface MindMapNodePreviewAnchor {
  x: number
  y: number
}

export type MindMapNodeType = 'source' | 'note' | 'studio_artifact'

interface MindMapNodePreviewProps {
  notebookId: string
  nodeType: MindMapNodeType
  nodeId: string
  artifactType?: string | null
  anchor: MindMapNodePreviewAnchor | null
  // v0.8.126 — the canvas wrapper the anchor's x/y are relative to (MindMap's
  // canvasRef). Used to clamp the popover so it can't render off-screen when
  // the source node clicked is near the canvas edge.
  containerRef?: RefObject<HTMLDivElement | null>
  onClose: () => void
  onOpenArtifact: (artifactId: string) => void
  onOpenSource: (sourceId: string) => void
  onOpenNote: (noteId: string) => void
}

// v0.8.126 — popover clipping fix. The card is `w-64` (256px) with a
// variable height depending on content (excerpt text vs. a 16:9 video), so
// rather than measure the rendered card (which would need a layout-effect
// render pass, and can't measure the video/image state ahead of load), clamp
// against a fixed max footprint plus the fixed 12px translateY offset the
// `style.transform` below applies.
const POPOVER_WIDTH = 256
const POPOVER_MAX_HEIGHT = 320
const POPOVER_MARGIN = 12
const POPOVER_Y_OFFSET = 12

function clampAnchor(
  anchor: MindMapNodePreviewAnchor,
  container: { width: number; height: number } | null
): { left: number; top: number } {
  if (!container) return { left: anchor.x, top: anchor.y }

  const halfWidth = POPOVER_WIDTH / 2
  const minLeft = POPOVER_MARGIN + halfWidth
  const maxLeft = Math.max(minLeft, container.width - POPOVER_MARGIN - halfWidth)
  const left = Math.min(Math.max(anchor.x, minLeft), maxLeft)

  const maxTop = Math.max(
    POPOVER_MARGIN,
    container.height - POPOVER_MARGIN - POPOVER_MAX_HEIGHT - POPOVER_Y_OFFSET
  )
  const top = Math.min(Math.max(anchor.y, POPOVER_MARGIN), maxTop)

  return { left, top }
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

// v0.8.125 — first ~240 chars of a source/note body, for the preview excerpt.
function excerpt(text: string | null | undefined, max = 240): string {
  const trimmed = (text ?? '').trim()
  if (!trimmed) return ''
  return trimmed.length > max ? `${trimmed.slice(0, max)}…` : trimmed
}

// v0.8.125 — defensive: only render SourceCover when the fetched detail
// actually carries the SourceListResponse-shaped fields it needs.
function isSourceCoverCompatible(candidate: SourceDetailResponse | null | undefined): candidate is SourceDetailResponse {
  return candidate != null
    && typeof candidate.id === 'string'
    && typeof candidate.embedded === 'boolean'
    && typeof candidate.embedded_chunks === 'number'
    && typeof candidate.insights_count === 'number'
}

export default function MindMapNodePreview({
  notebookId,
  nodeType,
  nodeId,
  artifactType,
  anchor,
  containerRef,
  onClose,
  onOpenArtifact,
  onOpenSource,
  onOpenNote,
}: MindMapNodePreviewProps) {
  const { t } = useTranslation()
  const cardRef = useRef<HTMLDivElement>(null)
  const setEpisode = useAudioPlayerStore((state) => state.setEpisode)
  const [videoUrls, setVideoUrls] = useState<{ media: string; captions: string } | null>(null)

  // v0.8.126 — clamp against the canvas wrapper (MindMap's canvasRef, passed
  // down as containerRef) so the popover can't render off-screen when the
  // clicked node is near the canvas edge. containerRef belongs to an
  // ancestor component, so reading `.current` has to happen in an effect,
  // not inline during render (react-hooks/refs); useLayoutEffect keeps the
  // correction synchronous, before paint.
  const [clampedPos, setClampedPos] = useState<{ left: number; top: number } | null>(null)
  useLayoutEffect(() => {
    if (!anchor) {
      setClampedPos(null)
      return
    }
    const containerRect = containerRef?.current?.getBoundingClientRect() ?? null
    setClampedPos(clampAnchor(anchor, containerRect))
  }, [anchor, containerRef])

  const open = Boolean(anchor)
  const isSourceNode = nodeType === 'source'
  const isNoteNode = nodeType === 'note'
  const isArtifactNode = nodeType === 'studio_artifact'

  const { data: artifact, isLoading: artifactLoading } = useQuery({
    queryKey: ['studio', 'artifact', nodeId],
    queryFn: async (): Promise<StudioArtifact | null> => {
      const artifacts = await studioApi.listArtifacts(notebookId)
      return artifacts.find((candidate) => candidate.id === nodeId) ?? null
    },
    enabled: open && isArtifactNode && Boolean(notebookId) && Boolean(nodeId),
  })

  const { data: source, isLoading: sourceLoading } = useQuery({
    queryKey: ['mindmap', 'source', nodeId],
    queryFn: () => sourcesApi.get(nodeId),
    enabled: open && isSourceNode && Boolean(nodeId),
  })

  const { data: note, isLoading: noteLoading } = useQuery({
    queryKey: ['mindmap', 'note', nodeId],
    queryFn: () => notesApi.get(nodeId),
    enabled: open && isNoteNode && Boolean(nodeId),
  })

  const resolvedType = artifactType ?? artifact?.artifact_type ?? null
  const isPodcastAudio = isArtifactNode && resolvedType === 'podcast_audio'
  const isSlideDeck = isArtifactNode && resolvedType === 'slide_deck'

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

  const { left, top } = clampedPos ?? { left: anchor.x, top: anchor.y }
  const style: CSSProperties = {
    left,
    top,
    transform: 'translate(-50%, 12px)',
  }

  const isLoading = isArtifactNode ? artifactLoading : isSourceNode ? sourceLoading : noteLoading
  const title = isSourceNode ? source?.title : isNoteNode ? note?.title : artifact?.title

  const openLabel = isSourceNode
    ? t('mindMap.previewOpenSource', { defaultValue: 'Open source' })
    : isNoteNode
      ? t('mindMap.previewOpenNote', { defaultValue: 'Open note' })
      : t('mindMap.previewOpen', { defaultValue: 'Open in Studio' })

  function handleOpen() {
    if (isSourceNode) onOpenSource(nodeId)
    else if (isNoteNode) onOpenNote(nodeId)
    else onOpenArtifact(nodeId)
  }

  return (
    <div
      ref={cardRef}
      role="dialog"
      aria-label={title ?? t('mindMap.previewLoading', { defaultValue: 'Loading…' })}
      className="absolute z-20 w-64 rounded-lg border bg-background p-3 shadow-lg"
      style={style}
    >
      <div className="mb-2 flex items-start justify-between gap-2">
        <p className="line-clamp-2 text-sm font-medium">
          {title ?? (isLoading
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
          {isSourceNode && (
            <>
              {isSourceCoverCompatible(source) && <SourceCover source={source} variant="compact" />}
              <p className="text-xs text-muted-foreground">
                {excerpt(source?.full_text || source?.summary_preview)}
              </p>
            </>
          )}

          {isNoteNode && (
            <p className="text-xs text-muted-foreground">{excerpt(note?.content)}</p>
          )}

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

          {isArtifactNode && !isPodcastAudio && !isSlideDeck && (
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
        onClick={handleOpen}
      >
        {openLabel}
      </Button>
    </div>
  )
}
