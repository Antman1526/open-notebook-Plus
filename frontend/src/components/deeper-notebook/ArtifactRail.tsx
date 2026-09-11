'use client'

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, ArrowRight, BookOpenCheck, CheckCircle2, Clock3, Cpu, Download, FileQuestion, GraduationCap, Layers3, ListChecks, Loader2, Map as MapIcon, Mic2, Newspaper, Play, Presentation, RefreshCw, Search, SlidersHorizontal, Table2, Trash2, Video } from 'lucide-react'
import ReactMarkdown from 'react-markdown'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { CitationDrawer, citationEvidenceFromRecord, type CitationEvidence } from '@/components/deeper-notebook/CitationDrawer'
import { CitationCoverageBadge } from '@/components/deeper-notebook/CitationCoverageBadge'
import { ArtifactExportMenu } from '@/components/deeper-notebook/ArtifactExportMenu'
import { ExportAllArtifactsDialog } from '@/components/deeper-notebook/ExportAllArtifactsDialog'
import { EvidenceReview } from '@/components/evaluation/EvidenceReview'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'
import { getSourceReadiness, SourceHealthPill } from '@/components/deeper-notebook/SourceHealthPill'
import {
  CoursePackViewer,
  CoursePackProgress,
  DataTableViewer,
  FlashcardDeck,
  FlashcardProgress,
  MindMapViewer,
  parseFlashcards,
  parseQuizQuestions,
  QuizProgress,
  QuizRunner,
  ResearchRunViewer,
  StudyProgress,
} from '@/components/deeper-notebook/StudyArtifactViewers'
import {
  InfographicViewer,
  isInfographicDocument,
  isSlideDeckDocument,
  SlideDeckViewer,
} from '@/components/deeper-notebook/VisualArtifactViewers'
import { isEvidenceStudioEnabled } from '@/lib/features'
import { useResearchRunsEnabled } from '@/lib/features-client'
import { artifactMarkdown } from '@/lib/studio-artifacts'
import { useTranslation } from '@/lib/hooks/use-translation'
import {
  useCreateStudioArtifact,
  useApproveStudioWorkflowRun,
  useCreateStudioWorkflowRun,
  useDeleteStudioArtifact,
  useStudioArtifactRevisions,
  useStudioArtifacts,
  useStudioWorkflowRuns,
  useUpdateStudioArtifact,
} from '@/lib/hooks/use-studio'
import { useComposeVideoOverview } from '@/lib/hooks/use-video-overviews'
import { podcastsApi, resolvePodcastAssetUrl } from '@/lib/api/podcasts'
import { cn } from '@/lib/utils'
import type { StudioArtifact, StudioArtifactType, StudioWorkflowRun } from '@/lib/api/studio'
import type { SourceListResponse } from '@/lib/types/api'

const ICONS: Partial<Record<StudioArtifactType, typeof Newspaper>> = {
  report: Newspaper,
  study_guide: BookOpenCheck,
  course_pack: GraduationCap,
  training_guide: GraduationCap,
  briefing: Newspaper,
  faq: FileQuestion,
  timeline: ListChecks,
  quiz: FileQuestion,
  flashcards: ListChecks,
  data_table: Table2,
  mind_map: MapIcon,
  slide_deck: Presentation,
  infographic: Layers3,
  podcast_outline: Mic2,
  research_run: Search,
}

type QuickArtifactType =
  | 'report'
  | 'study_guide'
  | 'course_pack'
  | 'briefing'
  | 'faq'
  | 'timeline'
  | 'data_table'
  | 'mind_map'
  | 'slide_deck'
  | 'infographic'
  | 'podcast_outline'
  | 'research_run'
  | 'flashcards'
  | 'quiz'

const QUICK_ARTIFACTS: Array<{
  type: QuickArtifactType
  title: string
  label: string
  Icon: typeof Newspaper
}> = [
  { type: 'report', title: 'Report', label: 'Report', Icon: Newspaper },
  { type: 'study_guide', title: 'Study guide', label: 'Study guide', Icon: BookOpenCheck },
  { type: 'course_pack', title: 'Course Pack', label: 'Course Pack', Icon: GraduationCap },
  { type: 'briefing', title: 'Briefing', label: 'Briefing', Icon: Newspaper },
  { type: 'faq', title: 'FAQ', label: 'FAQ', Icon: FileQuestion },
  { type: 'timeline', title: 'Timeline', label: 'Timeline', Icon: ListChecks },
  { type: 'data_table', title: 'Data Table', label: 'Data Table', Icon: Table2 },
  { type: 'mind_map', title: 'Mind map', label: 'Mind map', Icon: MapIcon },
  { type: 'slide_deck', title: 'Slide deck', label: 'Slide deck', Icon: Presentation },
  { type: 'infographic', title: 'Infographic', label: 'Infographic', Icon: Layers3 },
  { type: 'podcast_outline', title: 'Podcast outline', label: 'Podcast outline', Icon: Mic2 },
  { type: 'flashcards', title: 'Flashcards', label: 'Flashcards', Icon: ListChecks },
  { type: 'quiz', title: 'Quiz', label: 'Quiz', Icon: FileQuestion },
]

const RESEARCH_RUN_ARTIFACT = {
  type: 'research_run',
  title: 'Research run',
  label: 'Research run',
  Icon: Search,
} satisfies {
  type: QuickArtifactType
  title: string
  label: string
  Icon: typeof Newspaper
}

function artifactTypeLabel(type: StudioArtifactType): string {
  if (type === 'course_pack' || type === 'training_guide') return 'Course Pack'
  return type.replace(/_/g, ' ')
}

function statusClassName(status: StudioArtifact['status']): string {
  if (status === 'completed') return 'border-[var(--dn-success)] text-[var(--dn-success)]'
  if (status === 'failed' || status === 'cancelled') return 'border-destructive text-destructive'
  if (status === 'running') return 'border-[var(--dn-info)] text-[var(--dn-info)]'
  return 'border-[var(--dn-warning)] text-[var(--dn-warning)]'
}

function unsupportedCitationMarkers(artifact: StudioArtifact | null): string[] {
  const warnings = artifact?.output_payload?.citation_warnings
  if (!warnings || typeof warnings !== 'object' || Array.isArray(warnings)) return []
  const markers = (warnings as Record<string, unknown>).unsupported_markers
  if (!Array.isArray(markers)) return []
  return markers.filter((marker): marker is string => typeof marker === 'string')
}

function studyContentFingerprint(markdown: string): string {
  let hash = 0
  for (let index = 0; index < markdown.length; index += 1) {
    hash = ((hash << 5) - hash + markdown.charCodeAt(index)) | 0
  }
  return `${markdown.length}:${(hash >>> 0).toString(36)}`
}

function readStudyProgress(
  artifact: StudioArtifact | null,
  markdown: string,
): StudyProgress | null {
  const progress = artifact?.output_payload.study_progress
  if (!progress || typeof progress !== 'object' || Array.isArray(progress)) return null
  const candidate = progress as Partial<StudyProgress>
  if (candidate.version !== 1) return null
  if (candidate.content_fingerprint !== studyContentFingerprint(markdown)) return null
  return candidate as StudyProgress
}

function sourceTitle(source: SourceListResponse): string {
  return source.title || source.asset?.file_path || source.asset?.url || source.id
}

function sourceSelectionLabel(selectedCount: number): string {
  if (selectedCount === 0) return 'All sources'
  return `${selectedCount} ${selectedCount === 1 ? 'source' : 'sources'} selected`
}

function sourceHref(sourceId: string): string {
  return `/sources/${encodeURIComponent(sourceId)}`
}

function regenerateArtifactLabel(status: StudioArtifact['status']): string {
  return status === 'failed' ? 'Retry' : 'Regenerate'
}

function artifactStats(artifacts: StudioArtifact[]) {
  return {
    completed: artifacts.filter((artifact) => artifact.status === 'completed').length,
    active: artifacts.filter((artifact) => artifact.status === 'pending' || artifact.status === 'running').length,
    citations: artifacts.reduce((total, artifact) => total + artifact.citations.length, 0),
  }
}

function workflowRunStatusLabel(status?: StudioWorkflowRun['status']): string {
  return (status ?? 'queued').replace(/_/g, ' ')
}

function workflowRunStatusClassName(status?: StudioWorkflowRun['status']): string {
  if (status === 'completed') return 'border-[var(--dn-success)] text-[var(--dn-success)]'
  if (status === 'failed' || status === 'cancelled') return 'border-destructive text-destructive'
  if (status === 'running') return 'border-[var(--dn-info)] text-[var(--dn-info)]'
  return 'border-[var(--dn-warning)] text-[var(--dn-warning)]'
}

function workflowStepClassName(status: string): string {
  if (status === 'completed') return 'border-[var(--dn-success)] bg-[var(--dn-success-soft)]'
  if (status === 'running') return 'border-[var(--dn-info)] bg-[var(--dn-info-soft)]'
  if (status === 'failed') return 'border-destructive bg-destructive/10'
  if (status === 'blocked') return 'border-muted bg-muted/50 text-muted-foreground'
  return 'border-[var(--dn-warning)] bg-[var(--dn-warning-soft)]'
}

function workflowRunTimestamp(run: StudioWorkflowRun): string {
  return run.updated || run.created || run.id
}

function videoOverviewPayload(value: unknown): { media_url: string; captions_url: string } | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const candidate = value as Record<string, unknown>
  if (typeof candidate.media_url !== 'string' || typeof candidate.captions_url !== 'string') {
    return null
  }
  return { media_url: candidate.media_url, captions_url: candidate.captions_url }
}

export function ArtifactRail({
  notebookId,
  sources = [],
  sourcesLoading = false,
}: {
  notebookId: string
  sources?: SourceListResponse[]
  sourcesLoading?: boolean
}) {
  const [selectedArtifact, setSelectedArtifact] = useState<StudioArtifact | null>(null)
  const [selectedCitation, setSelectedCitation] = useState<CitationEvidence | null>(null)
  const [selectedSourceIds, setSelectedSourceIds] = useState<string[]>([])
  const { t } = useTranslation()
  const [videoDialogOpen, setVideoDialogOpen] = useState(false)
  const [exportAllDialogOpen, setExportAllDialogOpen] = useState(false)
  const [selectedEpisodeId, setSelectedEpisodeId] = useState('')
  const [videoUrls, setVideoUrls] = useState<{ media: string; captions: string } | null>(null)
  const enabled = isEvidenceStudioEnabled()
  const researchRunsEnabled = useResearchRunsEnabled()
  const { data: artifacts = [], isLoading } = useStudioArtifacts(notebookId, {
    enabled,
  })
  const createArtifact = useCreateStudioArtifact(notebookId)
  const createWorkflowRun = useCreateStudioWorkflowRun(notebookId)
  const approveWorkflowRun = useApproveStudioWorkflowRun(notebookId)
  const deleteArtifact = useDeleteStudioArtifact(notebookId)
  const updateArtifact = useUpdateStudioArtifact(notebookId)
  const composeVideoOverview = useComposeVideoOverview(notebookId)
  const artifactIds = artifacts.map((artifact) => artifact.id)
  const { data: workflowRuns = [], isLoading: workflowRunsLoading } = useStudioWorkflowRuns(
    artifactIds,
    { enabled: enabled && artifactIds.length > 0 },
  )
  const { data: artifactRevisions = [], isLoading: revisionsLoading } = useStudioArtifactRevisions(
    selectedArtifact?.id ?? null,
    { enabled: enabled && Boolean(selectedArtifact) },
  )
  useEffect(() => {
    const handleSelectArtifact = (event: Event) => {
      const customEvent = event as CustomEvent<{ artifactId?: string }>
      const targetId = customEvent.detail?.artifactId
      if (!targetId) return
      const found = artifacts.find((a) => a.id === targetId)
      if (found) {
        setSelectedArtifact(found)
        setSelectedCitation(null)
      }
    }
    window.addEventListener('dn:select-artifact', handleSelectArtifact)
    return () => {
      window.removeEventListener('dn:select-artifact', handleSelectArtifact)
    }
  }, [artifacts])

  const isCreating = (
    createArtifact.isPending
    || createWorkflowRun.isPending
    || approveWorkflowRun.isPending
  )
  const selectedMarkdown = artifactMarkdown(selectedArtifact?.output_payload)
  const selectedDocument = selectedArtifact?.output_payload.document
  const selectedSlideDeck = isSlideDeckDocument(selectedDocument) ? selectedDocument : null
  const videoOverview = videoOverviewPayload(selectedArtifact?.output_payload.video_overview)
  const { data: episodes = [] } = useQuery({
    queryKey: ['podcasts', 'episodes'],
    queryFn: podcastsApi.listEpisodes,
    enabled: enabled && Boolean(selectedSlideDeck),
  })
  const videoEligibleEpisodes = episodes.filter((episode) => (
    episode.job_status === 'completed'
    && Boolean(episode.audio_url)
    && (episode.transcript_segments?.length ?? 0) > 0
  ))
  const selectedInfographic = isInfographicDocument(selectedDocument) ? selectedDocument : null
  const selectedUnsupportedCitationMarkers = unsupportedCitationMarkers(selectedArtifact)
  const selectedStudyProgress = readStudyProgress(selectedArtifact, selectedMarkdown)
  const flashcardCount = selectedArtifact?.artifact_type === 'flashcards'
    ? parseFlashcards(selectedMarkdown).length
    : 0
  const quizQuestionCount = selectedArtifact?.artifact_type === 'quiz'
    ? parseQuizQuestions(selectedMarkdown).length
    : 0
  const sourceLabel = sourceSelectionLabel(selectedSourceIds.length)
  const scopedSources = selectedSourceIds.length === 0
    ? sources
    : sources.filter((source) => selectedSourceIds.includes(source.id))
  const blockedSources = scopedSources.filter((source) => getSourceReadiness(source).blocksGeneration)
  const generationBlocked = sourcesLoading || sources.length === 0 || blockedSources.length > 0
  const blockedSourceMessage = sourcesLoading
    ? 'Sources are still loading.'
    : sources.length === 0
      ? 'Add at least one ready source before generating artifacts.'
      : blockedSources.length === 1
        ? '1 source is not ready for artifact generation.'
        : `${blockedSources.length} sources are not ready for artifact generation.`
  const stats = artifactStats(artifacts)
  const artifactsById = new Map(artifacts.map((artifact) => [artifact.id, artifact]))
  const quickArtifacts = researchRunsEnabled
    ? [...QUICK_ARTIFACTS, RESEARCH_RUN_ARTIFACT]
    : QUICK_ARTIFACTS

  useEffect(() => {
    if (sources.length === 0 || selectedSourceIds.length === 0) return
    const sourceIds = new Set(sources.map((source) => source.id))
    const nextSelected = selectedSourceIds.filter((sourceId) => sourceIds.has(sourceId))
    if (nextSelected.length !== selectedSourceIds.length) {
      setSelectedSourceIds(nextSelected)
    }
  }, [sources, selectedSourceIds])

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
    return () => { active = false }
  }, [videoOverview])

  useEffect(() => {
    if (!videoEligibleEpisodes.length) {
      setSelectedEpisodeId('')
      return
    }
    if (!videoEligibleEpisodes.some((episode) => episode.id === selectedEpisodeId)) {
      setSelectedEpisodeId(videoEligibleEpisodes[0].id)
    }
  }, [selectedEpisodeId, videoEligibleEpisodes])

  function toggleSource(sourceId: string, checked: boolean) {
    setSelectedSourceIds((current) => {
      if (checked) {
        return current.includes(sourceId) ? current : [...current, sourceId]
      }
      return current.filter((id) => id !== sourceId)
    })
  }

  async function createAndQueue(type: QuickArtifactType, title: string) {
    if (generationBlocked) return
    const artifact = await createArtifact.mutateAsync({
      notebook_id: notebookId,
      artifact_type: type,
      title,
      source_ids: selectedSourceIds,
    })
    await createWorkflowRun.mutateAsync({
      artifactId: artifact.id,
      payload: {
        title: `Generate ${title}`,
        source_ids: selectedSourceIds,
        approval_required: true,
      },
    })
  }

  async function approveAndGenerate(run: StudioWorkflowRun) {
    await approveWorkflowRun.mutateAsync(run.id)
  }

  async function queueExistingArtifact(artifact: StudioArtifact) {
    const action = regenerateArtifactLabel(artifact.status)
    await createWorkflowRun.mutateAsync({
      artifactId: artifact.id,
      payload: {
        title: `${action} ${artifact.title}`,
        source_ids: artifact.source_ids,
        approval_required: false,
      },
    })
  }

  async function deleteSelectedArtifact(artifact: StudioArtifact) {
    if (!window.confirm(`Delete "${artifact.title}"?`)) return
    await deleteArtifact.mutateAsync(artifact.id)
    setSelectedArtifact(null)
    setSelectedCitation(null)
  }

  async function saveStudyProgress(
    patch: Partial<Pick<StudyProgress, 'course_pack' | 'flashcards' | 'quiz'>>,
  ) {
    if (!selectedArtifact || !selectedMarkdown) return

    const nextProgress: StudyProgress = {
      ...(selectedStudyProgress ?? {
        version: 1,
        content_fingerprint: studyContentFingerprint(selectedMarkdown),
        updated_at: new Date().toISOString(),
      }),
      ...patch,
      version: 1,
      content_fingerprint: studyContentFingerprint(selectedMarkdown),
      updated_at: new Date().toISOString(),
    }
    const outputPayload = {
      ...selectedArtifact.output_payload,
      study_progress: nextProgress,
    }
    setSelectedArtifact({
      ...selectedArtifact,
      output_payload: outputPayload,
    })

    try {
      await updateArtifact.mutateAsync({
        artifactId: selectedArtifact.id,
        payload: { output_payload: outputPayload },
      })
    } catch (error) {
      console.error('Failed to save study progress:', error)
    }
  }

  async function createVideoOverview() {
    if (!selectedArtifact || !selectedEpisodeId) return
    await composeVideoOverview.mutateAsync({
      slide_deck_artifact_id: selectedArtifact.id,
      podcast_episode_id: selectedEpisodeId,
    })
    setVideoDialogOpen(false)
  }

  if (!enabled) return null

  return (
    <section
      aria-label="Evidence Studio artifacts"
      className="mb-5 overflow-hidden rounded-lg border border-[var(--dn-border-strong)] bg-card shadow-[var(--dn-elevation-md)]"
    >
      <div className="flex flex-col gap-3 border-b bg-[var(--dn-surface-raised)] px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-10 w-10 flex-none items-center justify-center rounded-md border border-[var(--dn-border-strong)] bg-background text-primary shadow-[var(--dn-elevation-low)]">
            <Layers3 className="h-4 w-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="text-sm font-semibold">Evidence Studio</div>
            <div className="text-xs text-muted-foreground">
              {isLoading
                ? 'Artifacts are loading'
                : artifacts.length === 0
                  ? 'Awaiting first artifact'
                  : `${artifacts.length} ${artifacts.length === 1 ? 'artifact' : 'artifacts'}`}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Badge variant="outline" className="bg-background/70 text-[0.72rem]">
            {stats.completed} completed
          </Badge>
          <Badge variant="outline" className="bg-background/70 text-[0.72rem]">
            {stats.active} in progress
          </Badge>
          <Badge variant="outline" className="bg-background/70 text-[0.72rem]">
            {stats.citations} {stats.citations === 1 ? 'citation' : 'citations'}
          </Badge>
          {/* v0.8.124 — one-click export of every completed artifact as a zip. */}
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={t('studio.export.exportAll')}
            disabled={stats.completed === 0}
            onClick={() => setExportAllDialogOpen(true)}
          >
            <Download className="mr-1.5 h-3.5 w-3.5" aria-hidden="true" />
            {t('studio.export.exportAll')}
          </Button>
        </div>
      </div>

      <div className="grid gap-3 px-4 py-3">
        <div className="flex min-w-0 gap-2 overflow-x-auto pb-1">
          {isLoading && (
            <div className="flex min-h-12 items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Loading artifacts
            </div>
          )}

          {!isLoading && artifacts.length === 0 && (
            <div className="flex min-h-12 items-center rounded-md border border-dashed px-3 text-sm text-muted-foreground">
              No saved research outputs in this notebook.
            </div>
          )}

          {!isLoading && artifacts.map((artifact) => {
            const Icon = ICONS[artifact.artifact_type] ?? Newspaper
            return (
              <button
                type="button"
                key={artifact.id}
                aria-label={`Open ${artifact.title}`}
                onClick={() => {
                  setSelectedArtifact(artifact)
                  setSelectedCitation(null)
                }}
                className="flex min-h-14 min-w-56 max-w-72 flex-none items-center gap-2 rounded-md border bg-background px-3 py-2 text-left transition-colors hover:border-[var(--dn-border-strong)] hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Icon className="h-4 w-4 flex-none text-muted-foreground" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{artifact.title}</div>
                  <div className="mt-1 flex min-w-0 items-center gap-1.5">
                    <span className="truncate text-xs text-muted-foreground">
                      {artifactTypeLabel(artifact.artifact_type)}
                    </span>
                    <CitationCoverageBadge citationCount={artifact.citations.length} />
                  </div>
                </div>
                <Badge
                  variant="outline"
                  className={cn('flex-none text-[0.68rem]', statusClassName(artifact.status))}
                >
                  {artifact.status}
                </Badge>
              </button>
            )
          })}
        </div>

        <div className="rounded-md border bg-background/70 p-3">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="text-sm font-semibold">App Mode templates</div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                <span>Source readiness</span>
                <ArrowRight className="h-3 w-3" aria-hidden="true" />
                <span>Artifact generation</span>
                <ArrowRight className="h-3 w-3" aria-hidden="true" />
                <span>Evidence export</span>
              </div>
            </div>
            <div className="text-xs text-muted-foreground">
              Pick sources once, then run a reusable grounded workflow.
            </div>
          </div>
        </div>

        {(workflowRunsLoading || workflowRuns.length > 0) && (
          <div className="rounded-md border bg-background/70 p-3">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-center gap-2">
                <Clock3 className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <div className="text-sm font-semibold">Workflow runs</div>
              </div>
              <Badge variant="outline" className="w-fit text-[0.68rem]">
                {workflowRuns.length} {workflowRuns.length === 1 ? 'run' : 'runs'}
              </Badge>
            </div>

            {workflowRunsLoading ? (
              <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading run history
              </div>
            ) : (
              <div className="mt-3 grid gap-2">
                {workflowRuns.slice(0, 5).map((run) => {
                  const artifact = artifactsById.get(run.artifact_id)
                  const canApprove = run.status === 'awaiting_approval' && Boolean(artifact)
                  return (
                    <div
                      key={run.id}
                      className="rounded-md border bg-card px-3 py-2 shadow-[var(--dn-elevation-low)]"
                    >
                      <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <div className="truncate text-sm font-medium">{run.title}</div>
                            <Badge
                              variant="outline"
                              className={cn('text-[0.68rem]', workflowRunStatusClassName(run.status))}
                            >
                              {workflowRunStatusLabel(run.status)}
                            </Badge>
                          </div>
                          <div className="mt-1 text-xs text-muted-foreground">
                            {artifact?.title ?? run.artifact_id} - {workflowRunTimestamp(run)}
                          </div>
                        </div>

                        {canApprove && (
                          <Button
                            type="button"
                            size="sm"
                            disabled={isCreating}
                            aria-label={`Approve ${run.title}`}
                            onClick={() => void approveAndGenerate(run)}
                          >
                            {approveWorkflowRun.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                            ) : (
                              <Play className="h-4 w-4" aria-hidden="true" />
                            )}
                            Approve
                          </Button>
                        )}
                      </div>

                      {run.steps.length > 0 && (
                        <div className="mt-3 flex flex-wrap gap-1.5">
                          {run.steps.map((step) => (
                            <span
                              key={`${run.id}-${step.id}`}
                              className={cn(
                                'inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[0.68rem]',
                                workflowStepClassName(step.status),
                              )}
                            >
                              {step.status === 'completed' ? (
                                <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                              ) : step.status === 'running' ? (
                                <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
                              ) : (
                                <Clock3 className="h-3 w-3" aria-hidden="true" />
                              )}
                              {step.label}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )}

        <div className="flex max-w-full flex-wrap gap-2">
          <Popover>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                aria-label={`Artifact sources: ${sourceLabel}`}
                disabled={sourcesLoading || sources.length === 0}
              >
                <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
                {sourceLabel}
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-80 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-sm font-medium">Artifact sources</div>
                  <div className="text-xs text-muted-foreground">
                    Empty selection uses every notebook source.
                  </div>
                </div>
                {selectedSourceIds.length > 0 && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelectedSourceIds([])}
                  >
                    Use all
                  </Button>
                )}
              </div>

              <ScrollArea className="mt-3 max-h-64 pr-2">
                <div className="space-y-2">
                  {sources.map((source) => {
                    const title = sourceTitle(source)
                    const checkboxId = `artifact-source-${source.id.replace(/[^A-Za-z0-9_-]/g, '-')}`
                    return (
                      <div
                        key={source.id}
                        className="flex items-start gap-2 rounded-md px-2 py-1.5 hover:bg-accent"
                      >
                        <Checkbox
                          id={checkboxId}
                          aria-label={title}
                          checked={selectedSourceIds.includes(source.id)}
                          onCheckedChange={(checked) => toggleSource(source.id, checked === true)}
                        />
                        <label
                          htmlFor={checkboxId}
                          className="min-w-0 flex-1 cursor-pointer text-sm leading-5"
                        >
                          <span className="block truncate">{title}</span>
                          <span className="mt-1 block">
                            <SourceHealthPill source={source} />
                          </span>
                        </label>
                      </div>
                    )
                  })}
                </div>
              </ScrollArea>
            </PopoverContent>
          </Popover>

          {quickArtifacts.map(({ type, title, label, Icon }) => (
            <Button
              key={type}
              variant="outline"
              size="sm"
              disabled={isCreating || generationBlocked}
              onClick={() => void createAndQueue(type, title)}
            >
              <Icon className="h-4 w-4" aria-hidden="true" />
              {label}
            </Button>
          ))}
        </div>
      </div>

      {generationBlocked && (
        <div className="mt-2 rounded-md border border-[var(--dn-warning)] bg-[var(--dn-warning-soft)] px-3 py-2 text-xs text-muted-foreground">
          {blockedSourceMessage}
        </div>
      )}

      <Dialog
        open={Boolean(selectedArtifact)}
        onOpenChange={(open) => {
          if (!open) {
            setSelectedArtifact(null)
            setSelectedCitation(null)
          }
        }}
      >
        <DialogContent className="max-h-[85vh] w-[calc(100%-2rem)] max-w-[calc(100%-2rem)] overflow-y-auto bg-card text-card-foreground shadow-2xl sm:max-w-4xl lg:overflow-hidden">
          {selectedArtifact && (
            <>
              <DialogHeader>
                <DialogTitle>{selectedArtifact.title}</DialogTitle>
              </DialogHeader>

              <div className="grid min-h-0 gap-4 lg:grid-cols-[minmax(0,1fr)_15rem]">
                <ScrollArea className="max-h-[55vh] rounded-md border bg-background p-4">
                  <div className="space-y-4">
                    {selectedUnsupportedCitationMarkers.length > 0 && (
                      <div
                        className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm"
                        role="status"
                        data-testid="artifact-citation-warning"
                      >
                        <div className="flex items-start gap-2">
                          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden="true" />
                          <div>
                            <div className="font-medium text-destructive">
                              Citation markers need review
                            </div>
                            <div className="mt-1 text-xs text-muted-foreground">
                              This artifact cites markers that are not attached to selected sources:{' '}
                              <span className="font-mono">
                                {selectedUnsupportedCitationMarkers.join(', ')}
                              </span>
                            </div>
                          </div>
                        </div>
                      </div>
                    )}
                    {selectedSlideDeck ? (
                      <div className="space-y-4">
                        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-muted/30 px-3 py-2">
                          <div className="text-sm text-muted-foreground">Local Video Overview</div>
                          <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={() => setVideoDialogOpen(true)}
                          >
                            <Video className="h-4 w-4" aria-hidden="true" />
                            {videoOverview ? 'Regenerate video' : 'Create video'}
                          </Button>
                        </div>
                        {videoUrls && (
                          <video className="aspect-video w-full border bg-black" controls preload="metadata">
                            <source src={videoUrls.media} type="video/mp4" />
                            <track kind="captions" src={videoUrls.captions} srcLang="en" label="English" default />
                          </video>
                        )}
                        <SlideDeckViewer document={selectedSlideDeck} />
                      </div>
                    ) : selectedInfographic ? (
                      <InfographicViewer document={selectedInfographic} />
                    ) : selectedMarkdown && selectedArtifact.artifact_type === 'flashcards' && flashcardCount > 0 ? (
                      <FlashcardDeck
                        markdown={selectedMarkdown}
                        progress={selectedStudyProgress?.flashcards}
                        onProgressChange={(flashcards: FlashcardProgress) => {
                          void saveStudyProgress({ flashcards })
                        }}
                      />
                    ) : selectedMarkdown && selectedArtifact.artifact_type === 'quiz' && quizQuestionCount > 0 ? (
                      <QuizRunner
                        markdown={selectedMarkdown}
                        progress={selectedStudyProgress?.quiz}
                        onProgressChange={(quiz: QuizProgress) => {
                          void saveStudyProgress({ quiz })
                        }}
                      />
                    ) : selectedMarkdown && selectedArtifact.artifact_type === 'research_run' ? (
                      <ResearchRunViewer
                        markdown={selectedMarkdown}
                        stages={selectedArtifact.output_payload.research_stages}
                      />
                    ) : selectedMarkdown && selectedArtifact.artifact_type === 'data_table' ? (
                      <DataTableViewer
                        markdown={selectedMarkdown}
                        rows={selectedArtifact.output_payload.data_table_rows}
                      />
                    ) : selectedMarkdown && selectedArtifact.artifact_type === 'mind_map' ? (
                      <MindMapViewer
                        markdown={selectedMarkdown}
                        artifactId={selectedArtifact.id}
                        notebookId={notebookId}
                      />
                    ) : selectedMarkdown && (
                      selectedArtifact.artifact_type === 'course_pack'
                      || selectedArtifact.artifact_type === 'training_guide'
                    ) ? (
                      <CoursePackViewer
                        markdown={selectedMarkdown}
                        progress={selectedStudyProgress?.course_pack}
                        onProgressChange={(coursePack: CoursePackProgress) => {
                          void saveStudyProgress({ course_pack: coursePack })
                        }}
                      />
                    ) : selectedMarkdown ? (
                      <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none break-words prose-headings:font-semibold prose-p:leading-7">
                        <ReactMarkdown>{selectedMarkdown}</ReactMarkdown>
                      </div>
                    ) : (
                      <div className="text-sm text-muted-foreground">
                        This artifact does not have markdown output yet.
                      </div>
                    )}
                  </div>
                </ScrollArea>

                <aside className="rounded-md border bg-muted/30 p-3">
                  {(selectedArtifact.model_id || selectedArtifact.provider) && (
                    <div className="mb-4 rounded-md border bg-background px-2 py-2">
                      <div className="flex items-center gap-2 text-sm font-medium">
                        <Cpu className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
                        Model
                      </div>
                      {selectedArtifact.model_id && (
                        <div className="mt-1 truncate font-mono text-xs text-muted-foreground">
                          {selectedArtifact.model_id}
                        </div>
                      )}
                      {selectedArtifact.provider && (
                        <Badge variant="outline" className="mt-2 text-[0.68rem]">
                          {selectedArtifact.provider}
                        </Badge>
                      )}
                    </div>
                  )}
                  <div className="mb-4 rounded-md border bg-background px-2 py-2">
                    <div className="text-xs font-medium text-muted-foreground">Evidence review</div>
                    <div className="mt-2">
                      <EvidenceReview
                        notebookId={notebookId}
                        artifactId={selectedArtifact.id}
                      />
                    </div>
                  </div>
                  <ArtifactExportMenu artifact={selectedArtifact} markdown={selectedMarkdown} />
                  <div className="flex items-center justify-between gap-2">
                    <div className="text-sm font-medium">Citations</div>
                    <CitationCoverageBadge citationCount={selectedArtifact.citations.length} />
                  </div>
                  {selectedArtifact.citations.length > 0 ? (
                    <ul className="mt-3 space-y-2 text-sm">
                      {selectedArtifact.citations.map((citation, index) => {
                        const hasSourceId = typeof citation.source_id === 'string'
                        const sourceId = hasSourceId
                          ? (citation.source_id as string)
                          : `source-${index + 1}`
                        const title =
                          typeof citation.title === 'string'
                            ? citation.title
                            : sourceId
                        const preview =
                          typeof citation.preview === 'string'
                            ? citation.preview
                            : ''
                        return (
                          <li key={`${sourceId}-${index}`} className="rounded-md bg-background px-2 py-1.5">
                            <div className="flex items-start justify-between gap-2">
                              {hasSourceId ? (
                                <a
                                  href={sourceHref(sourceId)}
                                  className="min-w-0 truncate font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                >
                                  {title}
                                </a>
                              ) : (
                                <div className="min-w-0 truncate font-medium">{title}</div>
                              )}
                              <Button
                                type="button"
                                variant="ghost"
                                size="sm"
                                aria-label={`Inspect evidence for ${title}`}
                                className="h-7 w-7 flex-none p-0"
                                onClick={() => {
                                  setSelectedCitation(
                                    citationEvidenceFromRecord(citation, sourceId),
                                  )
                                }}
                              >
                                <Search className="h-3.5 w-3.5" aria-hidden="true" />
                              </Button>
                            </div>
                            <div className="truncate text-xs text-muted-foreground">{sourceId}</div>
                            {preview && (
                              <blockquote className="mt-2 line-clamp-4 border-l-2 border-[var(--dn-accent-strong)] pl-2 text-xs leading-5 text-muted-foreground">
                                {preview}
                              </blockquote>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  ) : (
                    <div className="mt-3 text-sm text-muted-foreground">
                      No citations stored yet.
                    </div>
                  )}

                  <CitationDrawer
                    evidence={selectedCitation}
                    onClose={() => setSelectedCitation(null)}
                  />

                  <div className="mt-4 border-t pt-3">
                    <div className="flex items-center justify-between gap-2">
                      <div className="text-sm font-medium">Revision history</div>
                      {artifactRevisions.length > 0 && (
                        <Badge variant="outline" className="text-[0.68rem]">
                          {artifactRevisions.length} {artifactRevisions.length === 1 ? 'revision' : 'revisions'}
                        </Badge>
                      )}
                    </div>
                    {revisionsLoading ? (
                      <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                        Loading revisions
                      </div>
                    ) : artifactRevisions.length > 0 ? (
                      <ul className="mt-3 space-y-2">
                        {artifactRevisions.map((revision) => (
                          <li key={revision.id}>
                            <button
                              type="button"
                              aria-label={`Open ${revision.title}`}
                              onClick={() => setSelectedArtifact(revision)}
                              className="w-full rounded-md bg-background px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              <span className="block truncate font-medium">{revision.title}</span>
                              <span className="block truncate text-xs text-muted-foreground">
                                {revision.updated || revision.created || revision.id}
                              </span>
                            </button>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <div className="mt-3 text-sm text-muted-foreground">
                        No revisions stored yet.
                      </div>
                    )}
                  </div>
                </aside>
              </div>

              <DialogFooter className="gap-2 sm:gap-2">
                <Button
                  type="button"
                  variant="destructive"
                  disabled={deleteArtifact.isPending}
                  onClick={() => void deleteSelectedArtifact(selectedArtifact)}
                >
                  {deleteArtifact.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  )}
                  Delete
                </Button>
                <Button
                  type="button"
                  disabled={createWorkflowRun.isPending || selectedArtifact.status === 'running'}
                  onClick={() => void queueExistingArtifact(selectedArtifact)}
                >
                  {createWorkflowRun.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                  ) : (
                    <RefreshCw className="h-4 w-4" aria-hidden="true" />
                  )}
                  {regenerateArtifactLabel(selectedArtifact.status)}
                </Button>
              </DialogFooter>

              <Dialog open={videoDialogOpen} onOpenChange={setVideoDialogOpen}>
                <DialogContent className="max-w-md bg-card text-card-foreground">
                  <DialogHeader>
                    <DialogTitle>Create local Video Overview</DialogTitle>
                  </DialogHeader>
                  <div className="space-y-3">
                    <p className="text-sm text-muted-foreground">
                      Choose a completed Audio Overview with timestamps. The video stays on this device and uses this slide deck as its visual source.
                    </p>
                    {videoEligibleEpisodes.length > 0 ? (
                      <select
                        aria-label="Audio Overview for Video Overview"
                        value={selectedEpisodeId}
                        onChange={(event) => setSelectedEpisodeId(event.target.value)}
                        className="h-10 w-full border bg-background px-3 text-sm"
                      >
                        {videoEligibleEpisodes.map((episode) => (
                          <option key={episode.id} value={episode.id}>{episode.name}</option>
                        ))}
                      </select>
                    ) : (
                      <div className="rounded-md border border-dashed px-3 py-4 text-sm text-muted-foreground">
                        Create and finish an Audio Overview with timestamped captions before making a Video Overview.
                      </div>
                    )}
                  </div>
                  <DialogFooter>
                    <Button type="button" variant="outline" onClick={() => setVideoDialogOpen(false)}>Cancel</Button>
                    <Button
                      type="button"
                      disabled={!selectedEpisodeId || composeVideoOverview.isPending}
                      onClick={() => void createVideoOverview()}
                    >
                      {composeVideoOverview.isPending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                      Create video
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>
            </>
          )}
        </DialogContent>
      </Dialog>

      <ExportAllArtifactsDialog
        open={exportAllDialogOpen}
        onOpenChange={setExportAllDialogOpen}
        notebookId={notebookId}
      />
    </section>
  )
}
