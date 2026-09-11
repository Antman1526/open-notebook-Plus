'use client'

import React, { useState, useEffect, useRef } from 'react'
import { SourceListResponse } from '@/lib/types/api'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  DropdownMenuSeparator
} from '@/components/ui/dropdown-menu'
import {
  FileText,
  ExternalLink,
  Upload,
  MoreVertical,
  Trash2,
  RefreshCw,
  Clock,
  CheckCircle,
  AlertTriangle,
  Loader2,
  Unlink,
  Share2,
  Podcast
} from 'lucide-react'
import { useSourceStatus } from '@/lib/hooks/use-sources'
import { useTranslation } from '@/lib/hooks/use-translation'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'
import type { TFunction } from 'i18next'
import { cn } from '@/lib/utils'
import { ContextToggle } from '@/components/common/ContextToggle'
import { ContextMode } from '@/app/(dashboard)/notebooks/[id]/page'
import { SourceCover } from '@/components/deeper-notebook/source-gallery/SourceCover'
import { isVisualSystemV2Enabled } from '@/lib/features'
import { useSourceVisualsEnabled } from '@/lib/features-client'

interface SourceCardProps {
  source: SourceListResponse
  onDelete?: (sourceId: string) => void
  onRetry?: (sourceId: string) => void
  onRemoveFromNotebook?: (sourceId: string) => void
  onClick?: (sourceId: string) => void
  onRefresh?: () => void
  className?: string
  showRemoveFromNotebook?: boolean
  showVisualCover?: boolean
  contextMode?: ContextMode
  onContextModeChange?: (mode: ContextMode) => void
  // v0.8.128 — explicit notebookId scoping when rendered inside a notebook
  notebookId?: string | null
}

const SOURCE_TYPE_ICONS = {
  link: ExternalLink,
  upload: Upload,
  text: FileText,
  web_import: ExternalLink,
  deep_research_report: FileText,
} as const

const getStatusConfig = (t: TFunction) => ({
  new: {
    icon: Clock,
    color: 'text-blue-700 dark:text-blue-300',
    bgColor: 'bg-blue-50 dark:bg-blue-950/40',
    borderColor: 'border-blue-200 dark:border-blue-900/50',
    label: t('sources.statusProcessing'),
    description: t('sources.statusPreparingDesc')
  },
  queued: {
    icon: Clock,
    color: 'text-blue-700 dark:text-blue-300',
    bgColor: 'bg-blue-50 dark:bg-blue-950/40',
    borderColor: 'border-blue-200 dark:border-blue-900/50',
    label: t('sources.statusQueued'),
    description: t('sources.statusQueuedDesc')
  },
  running: {
    icon: Loader2,
    color: 'text-blue-700 dark:text-blue-300',
    bgColor: 'bg-blue-50 dark:bg-blue-950/40',
    borderColor: 'border-blue-200 dark:border-blue-900/50',
    label: t('sources.statusProcessing'),
    description: t('sources.statusProcessingDesc')
  },
  completed: {
    icon: CheckCircle,
    color: 'text-emerald-700 dark:text-emerald-300',
    bgColor: 'bg-emerald-50 dark:bg-emerald-950/40',
    borderColor: 'border-emerald-200 dark:border-emerald-900/50',
    label: t('sources.statusCompleted'),
    description: t('sources.statusCompletedDesc')
  },
  failed: {
    icon: AlertTriangle,
    color: 'text-destructive',
    bgColor: 'bg-destructive/10',
    borderColor: 'border-destructive/30',
    label: t('sources.statusFailed'),
    description: t('sources.statusFailedDesc')
  }
} as const)

type SourceStatus = 'new' | 'queued' | 'running' | 'completed' | 'failed'

function isSourceStatus(status: unknown): status is SourceStatus {
  return typeof status === 'string' && ['new', 'queued', 'running', 'completed', 'failed'].includes(status)
}

type SourceType = keyof typeof SOURCE_TYPE_ICONS

function getSourceType(source: SourceListResponse): SourceType {
  if (source.source_type && source.source_type in SOURCE_TYPE_ICONS) {
    return source.source_type as SourceType
  }
  // Determine type based on asset information
  if (source.asset?.url) return 'link'
  if (source.asset?.file_path) return 'upload'
  return 'text'
}

function readString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function fileNameFromPath(path: string | undefined): string | null {
  if (!path) return null
  return path.split('/').filter(Boolean).at(-1) ?? null
}

function getSourceTypeLabel(sourceType: SourceType, t: TFunction): string {
  if (sourceType === 'link') return t('sources.addUrl')
  if (sourceType === 'upload') return t('sources.uploadFile')
  if (sourceType === 'web_import') return 'Web import'
  if (sourceType === 'deep_research_report') return 'Deep research'
  return t('sources.enterText')
}

function getProvenanceLabel(source: SourceListResponse): string | null {
  const provenance = source.provenance ?? {}
  return (
    readString(provenance.domain) ??
    readString(provenance.original_filename) ??
    readString(provenance.file_name) ??
    fileNameFromPath(source.asset?.file_path) ??
    readString(provenance.origin)
  )
}

function readNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function clampProgress(value: number): number {
  return Math.min(100, Math.max(0, value))
}

function getProgressPercent(info: Record<string, unknown> | undefined): number | null {
  if (!info) return null

  const directProgress =
    readNumber(info.progress) ??
    readNumber(info.percentage) ??
    readNumber(info.percent)
  if (directProgress !== null) {
    return clampProgress(directProgress)
  }

  const processed =
    readNumber(info.processed) ??
    readNumber(info.processed_items) ??
    readNumber(info.completed)
  const total =
    readNumber(info.total) ??
    readNumber(info.total_items)

  if (processed === null || total === null || total <= 0) return null
  return clampProgress((processed / total) * 100)
}

export function SourceCard({
  source,
  onClick,
  onDelete,
  onRetry,
  onRemoveFromNotebook,
  onRefresh,
  className,
  showRemoveFromNotebook = false,
  showVisualCover,
  contextMode,
  onContextModeChange,
  notebookId,
}: SourceCardProps) {
  const { t } = useTranslation()
  const sourceVisualsEnabled = useSourceVisualsEnabled()
  const visualCoversEnabled = isVisualSystemV2Enabled() && sourceVisualsEnabled
  const shouldShowVisualCover = visualCoversEnabled && (showVisualCover ?? true)
  const openPodcastReview = usePodcastStudioStore((state) => state.open)
  const statusConfigMap = getStatusConfig(t)
  const resolvedNotebookId =
    notebookId ??
    ((source as SourceListResponse & { notebooks?: string[] }).notebooks &&
    (source as SourceListResponse & { notebooks?: string[] }).notebooks!.length > 0
      ? (source as SourceListResponse & { notebooks?: string[] }).notebooks![0]
      : null)
  
  // Only fetch status for sources that might have async processing
  const sourceWithStatus = source as SourceListResponse & { command_id?: string; status?: string }

  // Track processing state to continue polling until we detect completion
  const [wasProcessing, setWasProcessing] = useState(false)

  const shouldFetchStatus = !!sourceWithStatus.command_id ||
    sourceWithStatus.status === 'new' ||
    sourceWithStatus.status === 'queued' ||
    sourceWithStatus.status === 'running' ||
    wasProcessing // Keep polling if we were processing to catch the completion

  const { data: statusData, isLoading: statusLoading } = useSourceStatus(
    source.id,
    shouldFetchStatus
  )

  // Determine current status
  // If source has a command_id but no status, treat as "new" (just created)
  const rawStatus = statusData?.status || sourceWithStatus.status
  const currentStatus: SourceStatus = isSourceStatus(rawStatus)
    ? rawStatus
    : (sourceWithStatus.command_id ? 'new' : 'completed')


  // v0.7.56 — track the post-completion refresh timeout in a ref so
  // unmount + rapid status flips don't leak a setTimeout that fires
  // after the parent stopped caring. The previous bare `setTimeout`
  // had no cleanup: filter changes, page nav, or back-to-back status
  // flips queued multiple refreshes on a possibly-unmounted parent.
  const refreshTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    const currentStatusFromData = statusData?.status || sourceWithStatus.status

    // If we're currently processing, mark that we were processing
    if (currentStatusFromData === 'new' || currentStatusFromData === 'running' || currentStatusFromData === 'queued') {
      setWasProcessing(true)
    }

    // If we were processing and now completed/failed, trigger refresh and stop polling
    if (wasProcessing &&
        (currentStatusFromData === 'completed' || currentStatusFromData === 'failed')) {
      setWasProcessing(false) // Stop polling

      if (onRefresh) {
        // Clear any previously queued refresh so rapid flips don't
        // pile up.
        if (refreshTimeoutRef.current) {
          clearTimeout(refreshTimeoutRef.current)
        }
        refreshTimeoutRef.current = setTimeout(() => {
          refreshTimeoutRef.current = null
          onRefresh()
        }, 500) // Small delay to ensure API is updated
      }
    }
  }, [statusData, sourceWithStatus.status, wasProcessing, onRefresh, source.id])

  // Cancel the pending refresh on unmount to avoid calling onRefresh
  // against a stale parent (and avoid React's "state update on
  // unmounted component" warnings on slow consumers).
  useEffect(() => {
    return () => {
      if (refreshTimeoutRef.current) {
        clearTimeout(refreshTimeoutRef.current)
        refreshTimeoutRef.current = null
      }
    }
  }, [])
  
  const statusConfig = statusConfigMap[currentStatus] || statusConfigMap.completed
  const StatusIcon = statusConfig.icon
  const sourceType = getSourceType(source)
  const SourceTypeIcon = SOURCE_TYPE_ICONS[sourceType]
  
   const title = source.title || t('sources.untitledSource')

  const isProcessing: boolean = currentStatus === 'new' || currentStatus === 'running' || currentStatus === 'queued'
  const isFailed: boolean = currentStatus === 'failed'
  const isCompleted: boolean = currentStatus === 'completed'
  const isFileUnavailable = sourceType === 'upload' && source.file_available === false
  const hasNoExtractedText = isCompleted && source.extraction_quality === 'no_text'
  const hasLowExtractedText = isCompleted && source.extraction_quality === 'low_text'
  const canRetry = !isFileUnavailable
  const podcastDisabledReason = !isCompleted
    ? 'Source processing must finish before it can become a podcast.'
    : isFileUnavailable
      ? 'The original source file is unavailable.'
      : hasNoExtractedText
        ? 'No readable source content is available.'
        : undefined
  const insightsPodcastDisabledReason = !isCompleted
    ? 'Source processing must finish before its insights can become a podcast.'
    : source.insights_count <= 0
      ? 'No source insights are available.'
      : undefined
  const progressPercent = getProgressPercent(statusData?.processing_info ?? source.processing_info)
  const notebookCount = source.notebook_count ?? 0
  const isShared = source.is_shared || notebookCount > 1
  const provenanceLabel = getProvenanceLabel(source)

  const handleRetry = () => {
    if (onRetry && canRetry) {
      onRetry(source.id)
    }
  }

  const handleDelete = () => {
    if (onDelete) {
      onDelete(source.id)
    }
  }

  const handleRemoveFromNotebook = () => {
    if (onRemoveFromNotebook) {
      onRemoveFromNotebook(source.id)
    }
  }

  const handleCardClick = () => {
    if (onClick) {
      onClick(source.id)
    }
  }

  return (
    <Card
      className={cn(
        'group relative cursor-pointer rounded-xl border border-border/50 bg-card/95 transition-all duration-200 ease-out',
        'ring-1 ring-border/30 hover:ring-primary/40 hover:border-border/80 hover:shadow-md active:scale-[0.99]',
        'shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)] dark:shadow-[inset_0_1px_1px_rgba(255,255,255,0.03)]',
        className
      )}
      onClick={handleCardClick}
    >
      <CardContent className="px-3.5 py-2.5">
        {/* Header with status indicator */}
        <div className="flex items-start justify-between gap-3 mb-1">
          <div className="flex-1 min-w-0">
            {shouldShowVisualCover && (
              <div className="mb-2">
                <SourceCover source={source} variant="compact" />
              </div>
            )}
            {/* Status badge - only show if not completed */}
            {!isCompleted && (
              <div className="flex items-center gap-2 mb-2">
                <div className={cn(
                  'flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium',
                  statusConfig.bgColor,
                  statusConfig.color
                )}>
                  <StatusIcon className={cn(
                    'h-3 w-3',
                    isProcessing && 'animate-spin'
                  )} />
                  {statusLoading && shouldFetchStatus ? t('sources.checking') : statusConfig.label}
                </div>

                {/* Source type indicator */}
                <div className="flex items-center gap-1 text-muted-foreground">
                  <SourceTypeIcon className="h-3 w-3" />
                  <span className="text-xs capitalize">{t('common.source')}</span>
                </div>
              </div>
            )}

            {/* Title */}
            <div className={cn('mb-1.5', !isCompleted && 'mb-1')}>
              <h4
                className="text-sm font-medium leading-snug line-clamp-2 break-words transition-colors group-hover:text-primary"
                title={title}
              >
                {title}
              </h4>
            </div>

            {/* Processing message for active statuses */}
            {statusData?.message && (isProcessing || isFailed) && (
              <p className="text-xs text-muted-foreground mb-2 italic">
                {statusData.message}
              </p>
            )}

            {/* Metadata badges */}
            <div className="flex items-center gap-1.5 flex-wrap">
              {/* Source type badge */}
              <Badge variant="secondary" className="text-[11px] rounded-full px-2 py-0.5 flex items-center gap-1">
                <SourceTypeIcon className="h-3 w-3" />
                {getSourceTypeLabel(sourceType, t)}
              </Badge>

              {isShared && (
                <Badge variant="outline" className="text-[11px] rounded-full px-2 py-0.5 flex items-center gap-1 border-border/60 bg-muted/20">
                  <Share2 className="h-3 w-3" />
                  {notebookCount > 1 ? `Shared with ${notebookCount}` : 'Shared'}
                </Badge>
              )}

              {provenanceLabel && (
                <Badge variant="outline" className="text-[11px] rounded-full px-2 py-0.5 max-w-[180px] truncate border-border/60 bg-muted/20">
                  {provenanceLabel}
                </Badge>
              )}

              {isFileUnavailable && (
                <Badge
                  variant="outline"
                  className="text-[11px] rounded-full px-2 py-0.5 flex items-center gap-1 border-destructive/50 text-destructive bg-destructive/10"
                >
                  <AlertTriangle className="h-3 w-3" />
                  {t('sources.fileUnavailable')}
                </Badge>
              )}

              {hasNoExtractedText && (
                <Badge
                  variant="outline"
                  className="text-[11px] rounded-full px-2 py-0.5 flex items-center gap-1 border-destructive/50 text-destructive bg-destructive/10"
                >
                  <AlertTriangle className="h-3 w-3" />
                  {t('sources.noExtractedText')}
                </Badge>
              )}

              {hasLowExtractedText && (
                <Badge
                  variant="outline"
                  className="text-[11px] rounded-full px-2 py-0.5 flex items-center gap-1 border-amber-500/60 text-amber-700 dark:text-amber-300 bg-amber-500/10"
                >
                  <AlertTriangle className="h-3 w-3" />
                  {t('sources.lowExtractedText')}
                </Badge>
              )}

              {isCompleted && source.insights_count > 0 && (
                <Badge variant="outline" className="text-[11px] rounded-full px-2 py-0.5 border-border/60 bg-muted/20">
                  {t('sources.insightsCount').replace('{count}', source.insights_count.toString())}
                </Badge>
              )}
              {source.topics && source.topics.length > 0 && isCompleted && (
                <>
                  {source.topics.slice(0, 2).map((topic, index) => (
                    <Badge key={index} variant="outline" className="text-[11px] rounded-full px-2 py-0.5 border-border/60 bg-muted/20">
                      {topic}
                    </Badge>
                  ))}
                  {source.topics.length > 2 && (
                    <Badge variant="outline" className="text-[11px] rounded-full px-2 py-0.5 border-border/60 bg-muted/20">
                      +{source.topics.length - 2}
                    </Badge>
                  )}
                </>
              )}
            </div>
            {/* v0.8.88 — auto-summary preview (opt-in source auto-summary). */}
            {isCompleted && source.summary_preview && (
              <p className="mt-1 line-clamp-1 text-xs italic text-muted-foreground">
                {source.summary_preview}
              </p>
            )}
          </div>

          {/* Context toggle and actions */}
          <div className="flex items-center gap-1">
            {/* Context toggle - only show if handler provided */}
            {onContextModeChange && contextMode && (
              <ContextToggle
                mode={contextMode}
                hasInsights={source.insights_count > 0}
                onChange={onContextModeChange}
              />
            )}

            {/* Actions dropdown */}
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="Source actions"
                  className="h-8 w-8 p-0 opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity"
                  onClick={(e) => e.stopPropagation()}
                >
                  <MoreVertical className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-48">
              {showRemoveFromNotebook && (
                <>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      handleRemoveFromNotebook()
                    }}
                    disabled={!onRemoveFromNotebook}
                  >
                    <Unlink className="h-4 w-4 mr-2" />
                    {t('sources.removeFromNotebook')}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </>
              )}

              {isFailed && (
                <>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      handleRetry()
                    }}
                    disabled={!onRetry || !canRetry}
                  >
                    <RefreshCw className="h-4 w-4 mr-2" />
                    {t('sources.retryProcessing')}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                </>
              )}

              <DropdownMenuItem
                onClick={(e) => {
                  e.stopPropagation()
                  openPodcastReview(
                    [{ kind: 'app_source', sourceId: source.id, inclusionMode: 'full' }],
                    'quick',
                    resolvedNotebookId
                  )
                }}
                disabled={Boolean(podcastDisabledReason)}
              >
                <Podcast className="h-4 w-4 mr-2" />
                {podcastDisabledReason
                  ? `Turn source into podcast — ${podcastDisabledReason}`
                  : 'Turn source into podcast'}
              </DropdownMenuItem>

              <DropdownMenuItem
                onClick={(e) => {
                  e.stopPropagation()
                  openPodcastReview(
                    [{ kind: 'app_source', sourceId: source.id, inclusionMode: 'insights' }],
                    'quick',
                    resolvedNotebookId
                  )
                }}
                disabled={Boolean(insightsPodcastDisabledReason)}
              >
                <Podcast className="h-4 w-4 mr-2" />
                {insightsPodcastDisabledReason
                  ? `Turn source insights into podcast — ${insightsPodcastDisabledReason}`
                  : 'Turn source insights into podcast'}
              </DropdownMenuItem>

              <DropdownMenuSeparator />

              <DropdownMenuItem
                onClick={(e) => {
                  e.stopPropagation()
                  handleDelete()
                }}
                disabled={!onDelete}
                className="text-destructive focus:text-destructive"
              >
                <Trash2 className="h-4 w-4 mr-2" />
                {t('sources.deleteSource')}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          </div>
        </div>
        {isFailed && (
          <div className="flex gap-2 pt-2 border-t">
            <Button
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation()
                handleRetry()
              }}
              disabled={!onRetry || !canRetry}
              className="h-7 text-xs"
            >
              <RefreshCw className="h-3 w-3 mr-1" />
              {t('sources.retry')}
            </Button>
          </div>
        )}

        {/* Processing progress indicator */}
        {isProcessing && progressPercent !== null && (
          <div className="mt-3 pt-2 border-t">
            <div className="flex justify-between items-center mb-1">
              <span className="text-xs text-muted-foreground">{t('common.progress')}</span>
              <span className="text-xs text-muted-foreground">
                {Math.round(progressPercent)}%
              </span>
            </div>
            <Progress value={progressPercent} className="h-1.5" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}
