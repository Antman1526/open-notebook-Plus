'use client'

import { useEffect, useMemo, useState } from 'react'
import { formatDistanceToNow } from 'date-fns'
import { getDateLocale } from '@/lib/utils/date-locale'
import { Headphones, InfoIcon, ListChecks, RefreshCcw, Square, Trash2 } from 'lucide-react'

import { resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import {
  EpisodeStatus,
  FAILED_EPISODE_STATUSES,
  OutlineSegment as EditableOutlineSegment,
  PodcastEpisode,
} from '@/lib/types/podcasts'
import {
  useApproveEpisodeOutline,
  useCancelPodcastEpisode,
  useUpdateEpisodeOutline,
} from '@/lib/hooks/use-podcasts'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useAudioPlayerStore } from '@/lib/stores/audio-player-store'
import { EpisodeResearchReceipt } from '@/components/podcasts/EpisodeResearchReceipt'
import type { TFunction } from 'i18next'

interface EpisodeCardProps {
  episode: PodcastEpisode
  onDelete: (episodeId: string) => Promise<void> | void
  deleting?: boolean
  onRetry?: (episodeId: string) => Promise<void> | void
  retrying?: boolean
}

const getSTATUS_META = (t: TFunction): Record<
  EpisodeStatus | 'unknown',
  { label: string; className: string }
> => ({
  running: {
    label: t('podcasts.processingLabel'),
    className: 'bg-amber-100 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800/50',
  },
  processing: {
    label: t('podcasts.processingLabel'),
    className: 'bg-amber-100 dark:bg-amber-950/40 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-800/50',
  },
  completed: {
    label: t('podcasts.completedLabel'),
    className: 'bg-emerald-100 dark:bg-emerald-950/40 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-800/50',
  },
  failed: {
    label: t('podcasts.failedLabel'),
    className: 'bg-destructive/10 text-destructive border-destructive/30',
  },
  error: {
    label: t('podcasts.failedLabel'),
    className: 'bg-destructive/10 text-destructive border-destructive/30',
  },
  pending: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-sky-100 dark:bg-sky-950/40 text-sky-800 dark:text-sky-300 border-sky-200 dark:border-sky-800/50',
  },
  submitted: {
    label: t('podcasts.pendingLabel'),
    className: 'bg-sky-100 dark:bg-sky-950/40 text-sky-800 dark:text-sky-300 border-sky-200 dark:border-sky-800/50',
  },
  unknown: {
    label: t('common.unknown'),
    className: 'bg-muted text-muted-foreground border-transparent',
  },
})

function StatusBadge({ status }: { status?: EpisodeStatus | null }) {
  const { t } = useTranslation()
  // Don't show badge for completed episodes
  if (status === 'completed') {
    return null
  }

  const meta = getSTATUS_META(t)[status ?? 'unknown']
  return (
    <Badge
      variant="outline"
      className={cn('uppercase tracking-wide text-xs rounded-full px-2.5 py-0.5 font-medium shadow-xs', meta.className)}
    >
      {meta.label}
    </Badge>
  )
}

type OutlineSegment = {
  name?: string
  description?: string
  size?: string
}

type OutlineData = {
  segments?: OutlineSegment[]
}

type TranscriptEntry = {
  speaker?: string
  dialogue?: string
}

type TranscriptData = {
  transcript?: TranscriptEntry[]
}

function extractOutlineSegments(outline: unknown): OutlineSegment[] {
  if (outline && typeof outline === 'object' && 'segments' in outline) {
    const data = outline as OutlineData
    if (Array.isArray(data.segments)) {
      return data.segments
    }
  }
  return []
}

function extractTranscriptEntries(transcript: unknown): TranscriptEntry[] {
  if (transcript && typeof transcript === 'object' && 'transcript' in transcript) {
    const data = transcript as TranscriptData
    if (Array.isArray(data.transcript)) {
      return data.transcript
    }
  }
  return []
}

// v0.7.33 — derive the current generation stage from the episode's
// data fields, no backend change needed. The worker writes:
//   outline (object with segments)  → ~30% of total time
//   transcript (object with array)  → +30%
//   audio_file (string path)        → +40% (TTS dominates)
// So a stage indicator is: outline absent? Generating outline.
// Outline present, transcript absent? Drafting transcript. etc.
type GenerationStage = 'outline' | 'transcript' | 'tts' | 'done' | 'idle'

function deriveStage(episode: PodcastEpisode): GenerationStage {
  // v0.8.68 — prefer the authoritative stage the worker writes as the
  // generation graph's nodes complete. The field-presence heuristic below
  // only moves when intermediates are persisted (i.e. at the very end),
  // so without this the indicator sat on "Generating outline…" for the
  // whole run.
  switch (episode.generation_stage) {
    case 'generating_outline':
      return 'outline'
    case 'generating_transcript':
      return 'transcript'
    case 'generating_audio':
    case 'combining_audio':
      return 'tts'
  }
  if (episode.audio_file) return 'done'
  const t = extractTranscriptEntries(episode.transcript)
  if (t.length > 0) return 'tts'
  const o = extractOutlineSegments(episode.outline)
  if (o.length > 0) return 'transcript'
  return 'outline'
}

function stageLabel(
  stage: GenerationStage,
  numSegments?: number,
  builtSegments?: number,
): string {
  switch (stage) {
    case 'outline':
      return 'Generating outline…'
    case 'transcript':
      return numSegments
        ? `Drafting transcript (${builtSegments ?? 0}/${numSegments} segments)…`
        : 'Drafting transcript…'
    case 'tts':
      return 'Synthesizing speech (this is the slow part)…'
    case 'done':
      return 'Ready'
    default:
      return 'Queued'
  }
}

function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '—'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function estimateLengthMinutes(episode: PodcastEpisode): number | undefined {
  // EpisodeProfile.default_length_minutes isn't stored as a domain
  // field (yet) but num_segments is. Two-host conversational pacing
  // averages ~2 min per segment.
  const n = episode.episode_profile?.num_segments
  if (typeof n === 'number' && n > 0) return Math.round(n * 2)
  return undefined
}

// v0.8.68 — outline-review editor: edit segment names/descriptions/sizes,
// then approve to generate transcript + audio from the edited outline.
function OutlineReviewDialog({ episode }: { episode: PodcastEpisode }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const updateOutline = useUpdateEpisodeOutline()
  const approveOutline = useApproveEpisodeOutline()

  const initialSegments = useMemo<EditableOutlineSegment[]>(() => {
    const raw = (episode.outline as { segments?: unknown[] } | null)?.segments
    if (!Array.isArray(raw)) return []
    return raw.map((s) => {
      const seg = s as Partial<EditableOutlineSegment>
      return {
        name: seg.name ?? '',
        description: seg.description ?? '',
        size: (['short', 'medium', 'long'] as const).includes(
          seg.size as 'short' | 'medium' | 'long',
        )
          ? (seg.size as EditableOutlineSegment['size'])
          : 'medium',
      }
    })
  }, [episode.outline])

  const [segments, setSegments] = useState<EditableOutlineSegment[]>(initialSegments)
  useEffect(() => setSegments(initialSegments), [initialSegments])

  const dirty = useMemo(
    () => JSON.stringify(segments) !== JSON.stringify(initialSegments),
    [segments, initialSegments],
  )
  const valid = segments.length > 0 && segments.every(
    (s) => s.name.trim().length > 0 && s.description.trim().length > 0,
  )
  const busy = updateOutline.isPending || approveOutline.isPending

  const setSegment = (index: number, patch: Partial<EditableOutlineSegment>) =>
    setSegments((prev) =>
      prev.map((s, i) => (i === index ? { ...s, ...patch } : s)),
    )

  const handleApprove = async () => {
    if (dirty) {
      await updateOutline.mutateAsync({ episodeId: episode.id, segments })
    }
    await approveOutline.mutateAsync(episode.id)
    setOpen(false)
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="default" size="sm">
          <ListChecks className="mr-2 h-4 w-4" />
          {t('podcasts.reviewOutline', { defaultValue: 'Review outline' })}
        </Button>
      </DialogTrigger>
      <DialogContent className="w-[min(90vw,720px)] max-h-[85vh] overflow-hidden flex flex-col">
        <DialogHeader>
          <DialogTitle>
            {t('podcasts.reviewOutlineTitle', { defaultValue: 'Review the outline' })}
          </DialogTitle>
          <DialogDescription>
            {t('podcasts.reviewOutlineDesc', {
              defaultValue:
                'Edit the segments below; the transcript and audio will follow your edits.',
            })}
          </DialogDescription>
        </DialogHeader>
        <ScrollArea className="flex-1 pr-3">
          <div className="space-y-4">
            {segments.map((segment, index) => (
              <div key={index} className="rounded border p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <Input
                    value={segment.name}
                    onChange={(e) => setSegment(index, { name: e.target.value })}
                    placeholder={t('podcasts.segmentName', { defaultValue: 'Segment title' })}
                    className="h-8 text-xs"
                  />
                  <select
                    value={segment.size}
                    onChange={(e) =>
                      setSegment(index, { size: e.target.value as EditableOutlineSegment['size'] })
                    }
                    className="h-8 rounded border bg-background px-2 text-xs"
                    aria-label={t('podcasts.segmentSize', { defaultValue: 'Segment length' })}
                  >
                    <option value="short">{t('podcasts.sizeShort', { defaultValue: 'Short' })}</option>
                    <option value="medium">{t('podcasts.sizeMedium', { defaultValue: 'Medium' })}</option>
                    <option value="long">{t('podcasts.sizeLong', { defaultValue: 'Long' })}</option>
                  </select>
                </div>
                <Textarea
                  value={segment.description}
                  onChange={(e) => setSegment(index, { description: e.target.value })}
                  placeholder={t('podcasts.segmentDescription', {
                    defaultValue: 'What should this segment cover?',
                  })}
                  className="min-h-[70px] text-xs"
                />
              </div>
            ))}
            {segments.length === 0 && (
              <p className="text-xs text-muted-foreground">
                {t('podcasts.noOutline', { defaultValue: 'No outline available.' })}
              </p>
            )}
          </div>
        </ScrollArea>
        <div className="flex justify-end gap-2 pt-2">
          <Button
            variant="outline"
            size="sm"
            disabled={!dirty || !valid || busy}
            onClick={() => updateOutline.mutate({ episodeId: episode.id, segments })}
          >
            {t('podcasts.saveOutline', { defaultValue: 'Save outline' })}
          </Button>
          <Button size="sm" disabled={!valid || busy} onClick={handleApprove}>
            {busy
              ? t('podcasts.approving', { defaultValue: 'Submitting…' })
              : t('podcasts.approveGenerate', {
                  defaultValue: 'Approve & generate audio',
                })}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function EpisodeCard({ episode, onDelete, deleting, onRetry, retrying }: EpisodeCardProps) {
  const { t, language } = useTranslation()
  // v0.8.68 — cancel an in-flight generation.
  const cancelEpisode = useCancelPodcastEpisode()
  const setPlayingEpisode = useAudioPlayerStore((state) => state.setEpisode)
  const [audioSrc, setAudioSrc] = useState<string | undefined>()
  const [audioError, setAudioError] = useState<string | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)

  const outlineSegments = useMemo(() => extractOutlineSegments(episode.outline), [episode.outline])
  const transcriptEntries = useMemo(() => extractTranscriptEntries(episode.transcript), [episode.transcript])

  // v0.7.33 — derive stage from episode fields. Refreshes whenever
  // the parent polls and gets new outline/transcript data.
  const stage = useMemo(() => deriveStage(episode), [episode])
  const isProcessing =
    stage !== 'done' && !FAILED_EPISODE_STATUSES.includes(
      episode.job_status as EpisodeStatus,
    )
  const estimatedMinutes = estimateLengthMinutes(episode)
  const [actualDurationSec, setActualDurationSec] = useState<number | null>(null)

  useEffect(() => {
    // v0.7.186 — Rewritten to fix three race conditions the audit
    // caught:
    //
    //  (a) Object-URL LEAK on rapid unmount: previously the cleanup
    //      function closed over `revokeUrl` at the time it was
    //      returned. The assignment `revokeUrl = URL.createObjectURL(blob)`
    //      happens LATER inside the async path. On quick unmount,
    //      cleanup ran while `revokeUrl` was still undefined, then
    //      the URL was created and never revoked.
    //
    //  (b) setState-after-unmount: `setAudioSrc/setAudioError`
    //      could run after the component had unmounted, triggering
    //      React warnings + memory pinning.
    //
    //  (c) Stale-blob-wins on rapid episode switching: with no
    //      AbortController on the fetch, a slow first fetch could
    //      resolve AFTER a faster second fetch, stomping the
    //      correct `audioSrc` with the stale blob.
    //
    // The fix: a single `objectUrlRef`-style local that cleanup
    // CAN see (set BEFORE the async work returns), a `cancelled`
    // flag guarding every setState, AND an AbortController so an
    // in-flight fetch aborts on episode-switch / unmount.
    let cancelled = false
    let currentObjectUrl: string | null = null
    const controller = new AbortController()
    setAudioError(null)

    const loadProtectedAudio = async () => {
      const directAudioUrl = await resolvePodcastAssetUrl(
        episode.audio_url ?? episode.audio_file
      )

      if (cancelled) return

      if (!directAudioUrl || !episode.audio_url) {
        if (!cancelled) setAudioSrc(directAudioUrl)
        return
      }

      try {
        let token: string | undefined
        if (typeof window !== 'undefined') {
          const raw = window.localStorage.getItem('auth-storage')
          if (raw) {
            try {
              const parsed = JSON.parse(raw)
              token = parsed?.state?.token
            } catch (error) {
              console.error('Failed to parse auth storage', error)
            }
          }
        }

        const headers: HeadersInit = {}
        if (token) {
          headers.Authorization = `Bearer ${token}`
        }

        const response = await fetch(directAudioUrl, {
          headers,
          signal: controller.signal,
        })
        if (!response.ok) {
          throw new Error(`Audio request failed with status ${response.status}`)
        }

        const blob = await response.blob()
        if (cancelled) return  // unmounted/switched between fetch & blob()

        const url = URL.createObjectURL(blob)
        currentObjectUrl = url  // set BEFORE setState so cleanup sees it
        setAudioSrc(url)
      } catch (error) {
        // AbortError is the expected outcome of switching episodes
        // mid-fetch — don't surface that to the user as a failure.
        if (cancelled || (error as Error).name === 'AbortError') return
        console.error('Unable to load podcast audio', error)
        setAudioError(t('podcasts.audioUnavailable'))
        setAudioSrc(undefined)
      }
    }

    void loadProtectedAudio()

    return () => {
      cancelled = true
      controller.abort()
      if (currentObjectUrl) {
        URL.revokeObjectURL(currentObjectUrl)
      }
    }
  }, [episode.audio_url, episode.audio_file, t])

  const distance = episode.created
    ? formatDistanceToNow(new Date(episode.created), {
        addSuffix: true,
        locale: getDateLocale(language),
      })
    : null

  const createdLabel = distance
    ? t('podcasts.created').replace('{time}', distance)
    : null

  const handleDelete = () => {
    void onDelete(episode.id)
  }

  const handleRetry = () => {
    if (onRetry) {
      void onRetry(episode.id)
    }
  }

  const isFailed = FAILED_EPISODE_STATUSES.includes(episode.job_status as EpisodeStatus)
  // v0.8.68 — outline-review workflow: the phase-1 job completed and the
  // episode is parked until the user approves (or edits) the outline.
  const isAwaitingReview = episode.generation_stage === 'awaiting_review'
  // v0.8.68 — completed episodes can be regenerated (the backend retry
  // endpoint now accepts terminal states, not just failures). Episodes
  // awaiting review have no audio yet — review UI takes over instead.
  const isCompleted = episode.job_status === 'completed' && !isAwaitingReview

  return (
    <div className="group relative rounded-2xl p-[1.5px] bg-gradient-to-b from-border/80 via-border/40 to-border/20 shadow-sm hover:shadow-md hover:from-border hover:via-border/60 hover:to-border/30 transition-all duration-200">
      <div className="rounded-[14.5px] bg-card/95 backdrop-blur-sm p-4 sm:p-5 space-y-4 border border-white/[0.04] dark:border-white/[0.02] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-base font-semibold text-foreground">
                {episode.name}
              </h3>
              <StatusBadge status={episode.job_status} />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('podcasts.profile')}: {episode.episode_profile?.name || t('common.unknown')}
              {/* v0.7.33 — show audible duration once known, else
                  the estimate from num_segments. Either way the user
                  sees "is this a 4-min brief or a 12-min deep dive?"
                  before opening the player. */}
              {actualDurationSec != null
                ? ` • ${formatDuration(actualDurationSec)}`
                : estimatedMinutes
                  ? ` • ~${estimatedMinutes} min`
                  : ''}
              {createdLabel ? ` • ${createdLabel}` : ''}
            </p>
            {/* v0.7.33 — stage indicator. Surfaces what the worker is
                actually doing right now (outline/transcript/TTS) so a
                long-running podcast generation doesn't look hung.
                v0.8.68 — now driven by the worker's authoritative
                generation_stage; suppressed while awaiting outline review
                (the review banner below takes over). */}
            {isProcessing && !isAwaitingReview && (
              <div className="flex items-center gap-2 text-xs text-amber-700 dark:text-amber-300">
                <div className="flex items-center gap-0.5 h-3.5" aria-hidden="true">
                  <span className="w-1 bg-amber-500/80 rounded-full animate-[pulse_1s_ease-in-out_infinite] h-2" />
                  <span className="w-1 bg-amber-500 rounded-full animate-[pulse_1.2s_ease-in-out_infinite_200ms] h-3.5" />
                  <span className="w-1 bg-amber-500/90 rounded-full animate-[pulse_0.9s_ease-in-out_infinite_400ms] h-2.5" />
                  <span className="w-1 bg-amber-500/70 rounded-full animate-[pulse_1.1s_ease-in-out_infinite_100ms] h-1.5" />
                </div>
                <span>
                  {stageLabel(
                    stage,
                    episode.episode_profile?.num_segments,
                    outlineSegments.length,
                  )}
                </span>
              </div>
            )}
            {isAwaitingReview && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                {t('podcasts.awaitingReview', {
                  defaultValue:
                    'Outline ready — review it to generate the audio.',
                })}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {episode.audio_url || episode.audio_file ? (
              <Button
                variant="outline"
                size="sm"
                className="h-8 rounded-xl px-2.5 gap-2 border-primary/30 bg-primary/10 hover:bg-primary/20 text-primary hover:text-primary font-medium active:scale-95 shadow-[0_0_12px_rgba(45,212,191,0.15),inset_0_1px_0_rgba(255,255,255,0.1)] transition-all duration-150"
                onClick={() =>
                  setPlayingEpisode({
                    id: episode.id,
                    title: episode.name,
                    sourcePath: episode.audio_url ?? episode.audio_file ?? '',
                    transcriptSegments: episode.transcript_segments ?? [],
                  })
                }
              >
                <span className="flex h-5 w-5 items-center justify-center rounded-lg bg-primary/20">
                  <Headphones className="h-3 w-3 text-primary" />
                </span>
                <span>Listen</span>
              </Button>
            ) : null}
            <Dialog open={detailsOpen} onOpenChange={setDetailsOpen}>
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="h-8 rounded-xl px-2.5 active:scale-95 transition-all duration-150">
                  <InfoIcon className="mr-1.5 h-3.5 w-3.5 text-muted-foreground" /> {t('podcasts.details')}
                </Button>
              </DialogTrigger>
              <DialogContent className="w-[min(90vw,720px)] max-h-[85vh] overflow-hidden">
                <DialogHeader>
                  <DialogTitle>{episode.name}</DialogTitle>
                  <DialogDescription>
                    {episode.episode_profile?.name || t('common.unknown')}
                    {createdLabel ? ` • ${createdLabel}` : ''}
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-4 overflow-hidden">
                  {audioSrc ? (
                    <audio
                      controls
                      preload="metadata"
                      src={audioSrc}
                      className="w-full"
                      // v0.7.33 — read the actual duration as soon as
                      // the audio metadata loads. preload="metadata"
                      // (was "none") makes this fire without forcing a
                      // full download; effectively zero perceptible cost.
                      onLoadedMetadata={(e) => {
                        const dur = (e.target as HTMLAudioElement).duration
                        if (isFinite(dur) && dur > 0) {
                          setActualDurationSec(dur)
                        }
                      }}
                    />
                  ) : audioError ? (
                    <p className="text-sm text-destructive">{audioError}</p>
                  ) : null}

                  <Tabs defaultValue="summary" className="h-[60vh] flex flex-col">
                    <TabsList className="grid w-full grid-cols-3">
                      <TabsTrigger value="summary">{t('podcasts.summaryTab')}</TabsTrigger>
                      <TabsTrigger value="outline">{t('podcasts.outlineTab')}</TabsTrigger>
                      <TabsTrigger value="transcript">{t('podcasts.transcriptTab')}</TabsTrigger>
                    </TabsList>

                    <TabsContent value="summary" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4">
                        <div className="space-y-6">
                          <EpisodeResearchReceipt
                            selectionSummary={episode.selection_summary}
                            selectionFingerprint={episode.selection_fingerprint}
                            editorialBrief={episode.editorial_brief}
                            modelPlanReceipts={episode.model_plan_receipts}
                          />
                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.episodeProfile')}</h4>
                            <div className="grid gap-2 text-sm md:grid-cols-2">
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.outlineModel')}</p>
                                <p>
                                  {episode.episode_profile?.outline_provider ?? '—'} /
                                  {' '}
                                  {episode.episode_profile?.outline_model ?? '—'}
                                </p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.transcriptModel')}</p>
                                <p>
                                  {episode.episode_profile?.transcript_provider ?? '—'} /
                                  {' '}
                                  {episode.episode_profile?.transcript_model ?? '—'}
                                </p>
                              </div>
                              <div>
                                <p className="text-muted-foreground">{t('podcasts.segments')}</p>
                                <p>{episode.episode_profile?.num_segments ?? '—'}</p>
                              </div>
                            </div>
                            {episode.episode_profile?.default_briefing ? (
                              <div className="rounded border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                                {episode.episode_profile.default_briefing}
                              </div>
                            ) : null}
                          </section>

                          <section className="space-y-2">
                            <h4 className="text-sm font-semibold text-foreground">{t('podcasts.speakerProfile')}</h4>
                            <p className="text-xs text-muted-foreground">
                              {episode.speaker_profile?.tts_provider ?? '—'} /{' '}
                              {episode.speaker_profile?.tts_model ?? '—'}
                            </p>
                            {episode.speaker_profile?.speakers?.map((speaker, index) => (
                              <div
                                key={`${speaker.name}-${index}`}
                                className="rounded-md border bg-muted/20 p-3 text-xs"
                              >
                                <p className="font-semibold text-foreground">{speaker.name}</p>
                                <p className="text-muted-foreground">{t('podcasts.voiceId')}: {speaker.voice_id}</p>
                                <p className="mt-2 whitespace-pre-wrap text-muted-foreground">
                                  <span className="font-semibold">{t('podcasts.backstory')}:</span> {speaker.backstory}
                                </p>
                                <p className="mt-2 whitespace-pre-wrap text-muted-foreground">
                                  <span className="font-semibold">{t('podcasts.personality')}:</span> {speaker.personality}
                                </p>
                              </div>
                            ))}
                          </section>

                          {episode.briefing ? (
                            <section className="space-y-2">
                              <h4 className="text-sm font-semibold text-foreground">{t('podcasts.briefing')}</h4>
                              <div className="rounded border bg-muted/30 p-3 text-xs whitespace-pre-wrap">
                                {episode.briefing}
                              </div>
                            </section>
                          ) : null}
                        </div>
                      </ScrollArea>
                    </TabsContent>

                    <TabsContent value="outline" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4">
                        {outlineSegments.length > 0 ? (
                          <div className="space-y-3">
                            {outlineSegments.map((segment, index) => (
                              <div key={index} className="rounded border bg-muted/20 p-3 text-xs space-y-1">
                                <div className="flex items-center justify-between gap-2">
                                  <p className="font-semibold text-foreground">{segment.name ?? `${t('podcasts.segment')} ${index + 1}`}</p>
                                  {segment.size ? (
                                    <Badge variant="outline" className="text-[10px] uppercase tracking-wide">{segment.size}</Badge>
                                  ) : null}
                                </div>
                                <p className="text-muted-foreground whitespace-pre-wrap">{segment.description ?? t('podcasts.noDescription')}</p>
                              </div>
                            ))}
                          </div>
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('podcasts.noOutline')}</p>
                        )}
                      </ScrollArea>
                    </TabsContent>

                    <TabsContent value="transcript" className="flex-1 overflow-hidden">
                      <ScrollArea className="h-full pr-4 space-y-3">
                        {transcriptEntries.length > 0 ? (
                          transcriptEntries.map((entry, index) => (
                            <div key={index} className="rounded border bg-muted/20 p-3 text-xs space-y-1">
                              <p className="font-semibold text-foreground">{entry.speaker ?? t('podcasts.speaker')}</p>
                              <p className="text-muted-foreground whitespace-pre-wrap">{entry.dialogue ?? ''}</p>
                            </div>
                          ))
                        ) : (
                          <p className="text-xs text-muted-foreground">{t('podcasts.noTranscript')}</p>
                        )}
                      </ScrollArea>
                    </TabsContent>
                  </Tabs>
                </div>
              </DialogContent>
            </Dialog>
            {/* v0.8.68 — cancel an in-flight generation. */}
            {isProcessing && !isAwaitingReview ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => cancelEpisode.mutate(episode.id)}
                disabled={cancelEpisode.isPending}
              >
                <Square className="mr-2 h-4 w-4" />
                {cancelEpisode.isPending
                  ? t('podcasts.cancelling', { defaultValue: 'Cancelling…' })
                  : t('common.cancel')}
              </Button>
            ) : null}
            {/* v0.8.68 — outline-review workflow entry point. */}
            {isAwaitingReview ? (
              <OutlineReviewDialog episode={episode} />
            ) : null}
            {isFailed && onRetry ? (
              <Button
                variant="outline"
                size="sm"
                onClick={handleRetry}
                disabled={retrying}
              >
                <RefreshCcw className={cn('mr-2 h-4 w-4', retrying && 'animate-spin')} />
                {retrying ? t('podcasts.retrying') : t('podcasts.retry')}
              </Button>
            ) : null}
            {/* v0.8.68 — regenerate a completed episode (NotebookLM-style
                "make it again"). Confirm-gated: the existing audio is
                replaced, so this is destructive in a way plain retry of a
                failed episode is not. */}
            {isCompleted && onRetry ? (
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" size="sm" disabled={retrying}>
                    <RefreshCcw className={cn('mr-2 h-4 w-4', retrying && 'animate-spin')} />
                    {retrying
                      ? t('podcasts.regenerating', { defaultValue: 'Regenerating…' })
                      : t('podcasts.regenerate', { defaultValue: 'Regenerate' })}
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>
                      {t('podcasts.regenerateTitle', { defaultValue: 'Regenerate episode?' })}
                    </AlertDialogTitle>
                    <AlertDialogDescription>
                      {t('podcasts.regenerateDesc', {
                        defaultValue:
                          'This replaces the current audio with a freshly generated version using the same content, profiles, and instructions. The existing audio will be deleted.',
                      })}
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                    <AlertDialogAction onClick={handleRetry}>
                      {t('podcasts.regenerate', { defaultValue: 'Regenerate' })}
                    </AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            ) : null}
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="ghost" size="sm" className="text-destructive">
                  <Trash2 className="mr-2 h-4 w-4" />
                  {t('podcasts.delete')}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>{t('podcasts.deleteEpisodeTitle')}</AlertDialogTitle>
                  <AlertDialogDescription>
                    {t('podcasts.deleteEpisodeDesc').replace('{name}', episode.name)}
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
                  <AlertDialogAction onClick={handleDelete} disabled={deleting}>
                    {deleting ? t('podcasts.deleting') : t('podcasts.delete')}
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          </div>
        </div>

        {audioSrc ? (
          <audio controls preload="none" src={audioSrc} className="w-full" />
        ) : audioError ? (
          <p className="text-sm text-destructive">{audioError}</p>
        ) : null}

        {isFailed && episode.error_message ? (
          <div className="rounded-md border border-red-200 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950/30">
            <p className="text-xs font-medium text-red-800 dark:text-red-300">{t('podcasts.errorDetails')}</p>
            <p className="mt-1 text-xs whitespace-pre-wrap text-red-700 dark:text-red-400">{episode.error_message}</p>
          </div>
        ) : null}
      </div>
    </div>
  )
}
