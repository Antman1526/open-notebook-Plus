'use client'

import { useState, useMemo, useCallback, type UIEvent, type DragEvent } from 'react'
import { SourceListResponse } from '@/lib/types/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Plus, FileText, Link2, ChevronDown, Loader2, ListChecks, Compass } from 'lucide-react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { EmptyState } from '@/components/common/EmptyState'
import { AddSourceDialog } from '@/components/sources/AddSourceDialog'
import { AddExistingSourceDialog } from '@/components/sources/AddExistingSourceDialog'
import { DiscoverSourcesDialog } from '@/components/sources/DiscoverSourcesDialog'
import { SourceCard } from '@/components/sources/SourceCard'
import { VirtualizedListAuto } from '@/components/ui/virtualized-list'

// v0.7.45 — virtualize the sources list only when it gets large
// enough to feel the cost of full rendering. Below the threshold the
// plain map keeps SSR-friendly behavior + zero virtualization overhead.
const VIRTUALIZE_THRESHOLD = 50
// Each SourceCard is variable-height (depends on title length, status
// badges, etc.). 96px is the median in practice — close-to-real means
// fewer scroll jumps as the virtualizer measures real heights.
const SOURCE_CARD_ESTIMATE_PX = 96
import { useDeleteSource, useRetrySource, useRemoveSourceFromNotebook } from '@/lib/hooks/use-sources'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { ContextMode } from '../[id]/page'
import type { SourceBulkAction } from '@/lib/utils/source-context'
import { CollapsibleColumn, createCollapseButton } from '@/components/notebooks/CollapsibleColumn'
import { useNotebookColumnsStore } from '@/lib/stores/notebook-columns-store'
import { useTranslation } from '@/lib/hooks/use-translation'
import { isVisualSystemV2Enabled } from '@/lib/features'
import { useSourceVisualsEnabled } from '@/lib/features-client'
// v0.7.119 — Bulk-vectorize button surfaces the per-notebook
// vectorize_sources endpoint next to the existing "+" trigger.
import { BulkVectorizeButton } from './BulkVectorizeButton'

interface SourcesColumnProps {
  sources?: SourceListResponse[]
  isLoading: boolean
  notebookId: string
  notebookName?: string
  onRefresh?: () => void
  contextSelections?: Record<string, ContextMode>
  onContextModeChange?: (sourceId: string, mode: ContextMode) => void
  onBulkContextModeChange?: (action: SourceBulkAction) => void
  // Pagination props
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  fetchNextPage?: () => void
}

export function SourcesColumn({
  sources,
  isLoading,
  notebookId,
  onRefresh,
  contextSelections,
  onContextModeChange,
  onBulkContextModeChange,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
}: SourcesColumnProps) {
  const { t } = useTranslation()
  const sourceVisualsEnabled = useSourceVisualsEnabled()
  const showVisualCover = isVisualSystemV2Enabled() && sourceVisualsEnabled
  const sourcesLabel = t('navigation.sources')
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [addExistingDialogOpen, setAddExistingDialogOpen] = useState(false)
  // v0.8.87 — Discover sources (guarded web search) dialog.
  const [discoverDialogOpen, setDiscoverDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [sourceToDelete, setSourceToDelete] = useState<string | null>(null)
  const [removeDialogOpen, setRemoveDialogOpen] = useState(false)
  const [sourceToRemove, setSourceToRemove] = useState<string | null>(null)
  // v0.8.77 — drag-drop-anywhere (improvement roadmap, Batch 1). Drop files
  // onto the sources panel to open AddSourceDialog prefilled with them. The
  // file prefill is best-effort (AddSourceDialog degrades to a manual pick).
  // NOTE: needs a real in-app file-drag test to confirm the prefill lands.
  const [droppedFiles, setDroppedFiles] = useState<File[] | undefined>(undefined)
  const [isDragging, setIsDragging] = useState(false)

  const handleDragOver = useCallback((e: DragEvent) => {
    if (e.dataTransfer?.types?.includes('Files')) {
      e.preventDefault()
      setIsDragging(true)
    }
  }, [])
  const handleDragLeave = useCallback((e: DragEvent) => {
    // Ignore leaves into child elements (avoids overlay flicker).
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return
    setIsDragging(false)
  }, [])
  const handleDrop = useCallback((e: DragEvent) => {
    if (!e.dataTransfer?.types?.includes('Files')) return
    e.preventDefault()
    setIsDragging(false)
    const files = Array.from(e.dataTransfer.files || [])
    if (files.length === 0) return
    setDroppedFiles(files)
    setAddDialogOpen(true)
  }, [])

  const { openModal } = useModalManager()
  const deleteSource = useDeleteSource()
  const retrySource = useRetrySource()
  const removeFromNotebook = useRemoveSourceFromNotebook()

  // Collapsible column state
  const { sourcesCollapsed, toggleSources } = useNotebookColumnsStore()
  const collapseButton = useMemo(
    () => createCollapseButton(toggleSources, sourcesLabel),
    [toggleSources, sourcesLabel]
  )

  // v0.7.51 — read scroll metrics from `event.currentTarget`, NOT from a
  // pinned ref. We render two different scroll surfaces depending on list
  // size:
  //   - small list: CardContent itself scrolls
  //   - virtualized: the VirtualizedListAuto's inner `<div overflow-auto>`
  //     scrolls; CardContent never sees the wheel events because the inner
  //     element consumes them first.
  // The old `scrollContainerRef` pinned to CardContent was correct for the
  // small-list branch but stuck at scrollTop=0 forever in the virtualized
  // branch — infinite scroll silently died past `VIRTUALIZE_THRESHOLD`.
  // Using `e.currentTarget` lets the same handler work on whichever
  // element fired the scroll event.
  const handleScroll = useCallback((e: UIEvent<HTMLDivElement>) => {
    if (!hasNextPage || isFetchingNextPage || !fetchNextPage) return
    const { scrollTop, scrollHeight, clientHeight } = e.currentTarget
    // Load more when user scrolls within 200px of the bottom.
    if (scrollHeight - scrollTop - clientHeight < 200) {
      fetchNextPage()
    }
  }, [hasNextPage, isFetchingNextPage, fetchNextPage])


  const handleDeleteClick = (sourceId: string) => {
    setSourceToDelete(sourceId)
    setDeleteDialogOpen(true)
  }

  const handleDeleteConfirm = async () => {
    if (!sourceToDelete) return

    try {
      await deleteSource.mutateAsync(sourceToDelete)
      setDeleteDialogOpen(false)
      setSourceToDelete(null)
      onRefresh?.()
    } catch (error) {
      console.error('Failed to delete source:', error)
    }
  }

  const handleRemoveFromNotebook = (sourceId: string) => {
    setSourceToRemove(sourceId)
    setRemoveDialogOpen(true)
  }

  const handleRemoveConfirm = async () => {
    if (!sourceToRemove) return

    try {
      await removeFromNotebook.mutateAsync({
        notebookId,
        sourceId: sourceToRemove
      })
      setRemoveDialogOpen(false)
      setSourceToRemove(null)
    } catch (error) {
      console.error('Failed to remove source from notebook:', error)
      // Error toast is handled by the hook
    }
  }

  const handleRetry = async (sourceId: string) => {
    try {
      await retrySource.mutateAsync(sourceId)
    } catch (error) {
      console.error('Failed to retry source:', error)
    }
  }

  const handleSourceClick = (sourceId: string) => {
    openModal('source', sourceId)
  }

  return (
    <>
      <CollapsibleColumn
        isCollapsed={sourcesCollapsed}
        onToggle={toggleSources}
        collapsedIcon={FileText}
        collapsedLabel={t('navigation.sources')}
      >
        <Card
          className="relative h-full flex flex-col flex-1 overflow-hidden"
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          {isDragging && (
            <div className="pointer-events-none absolute inset-0 z-20 m-2 flex items-center justify-center rounded-lg border-2 border-dashed border-primary bg-primary/10 backdrop-blur-sm">
              <p className="text-sm font-medium text-primary">
                {t('sources.dropToAdd', { defaultValue: 'Drop files to add as sources' })}
              </p>
            </div>
          )}
          <CardHeader className="pb-3 flex-shrink-0">
            <div className="flex items-center justify-between gap-2">
              <CardTitle className="text-lg">{t('navigation.sources')}</CardTitle>
              <div className="flex items-center gap-2">
                {onBulkContextModeChange && sources && sources.length > 0 && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="sm" title={t('sources.bulkContext')}>
                        <ListChecks className="h-4 w-4" />
                        <ChevronDown className="h-4 w-4 ml-1" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onClick={() => onBulkContextModeChange('insights')}>
                        {t('sources.includeAllInsights')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => onBulkContextModeChange('full')}>
                        {t('sources.includeAllFull')}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => onBulkContextModeChange('exclude')}>
                        {t('sources.excludeAllFromContext')}
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                <BulkVectorizeButton notebookId={notebookId} />
                <DropdownMenu open={dropdownOpen} onOpenChange={setDropdownOpen}>
                  <DropdownMenuTrigger asChild>
                    <Button size="sm">
                      <Plus className="h-4 w-4 mr-2" />
                      {t('sources.addSource')}
                      <ChevronDown className="h-4 w-4 ml-2" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem onClick={() => { setDropdownOpen(false); setAddDialogOpen(true); }}>
                      <Plus className="h-4 w-4 mr-2" />
                      {t('sources.addSource')}
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => { setDropdownOpen(false); setAddExistingDialogOpen(true); }}>
                      <Link2 className="h-4 w-4 mr-2" />
                      {t('sources.addExistingTitle')}
                    </DropdownMenuItem>
                    {/* v0.8.87 — Discover: guarded web search → add link sources. */}
                    <DropdownMenuItem onClick={() => { setDropdownOpen(false); setDiscoverDialogOpen(true); }}>
                      <Compass className="h-4 w-4 mr-2" />
                      {t('sources.discover', { defaultValue: 'Discover sources' })}
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                {collapseButton}
              </div>
            </div>
          </CardHeader>

          {/* v0.7.45 — body splits into three render paths:
              1. Loading: spinner (unchanged)
              2. Empty: EmptyState (unchanged)
              3a. Small list (<50 sources): standard `.map()` rendering
                  (SSR-friendly, zero virtualization overhead)
              3b. Large list (>=50): VirtualizedListAuto — only viewport
                  rows + overscan are kept in the DOM. Infinite-scroll
                  hook fires via the virtualizer's onScroll instead of
                  the old addEventListener pattern. */}
          <CardContent onScroll={handleScroll} className="flex-1 overflow-y-auto min-h-0">
            {isLoading ? (
              <div className="flex items-center justify-center py-8">
                <LoadingSpinner />
              </div>
            ) : !sources || sources.length === 0 ? (
              <EmptyState
                icon={FileText}
                title={t('sources.noSourcesYet')}
                description={t('sources.createFirstSource')}
                action={
                  // v0.8.75 — actionable empty state (improvement roadmap,
                  // Batch 1): a clear CTA to add the first source instead of a
                  // dead-end message. Opens the existing AddSourceDialog.
                  <Button size="sm" onClick={() => setAddDialogOpen(true)}>
                    <Plus className="mr-2 h-4 w-4" />
                    {t('sources.addSource')}
                  </Button>
                }
              />
            ) : sources.length >= VIRTUALIZE_THRESHOLD ? (
              <VirtualizedListAuto
                items={sources}
                estimateSize={SOURCE_CARD_ESTIMATE_PX}
                className="h-full"
                getItemKey={(source) => source.id}
                onScroll={handleScroll}
                renderItem={(source) => (
                  <div className="pb-3">
                    <SourceCard
                      source={source}
                      onClick={handleSourceClick}
                      onDelete={handleDeleteClick}
                      onRetry={handleRetry}
                      onRemoveFromNotebook={handleRemoveFromNotebook}
                      onRefresh={onRefresh}
                      showVisualCover={showVisualCover}
                      showRemoveFromNotebook={true}
                      notebookId={notebookId}
                      contextMode={contextSelections?.[source.id]}
                      onContextModeChange={onContextModeChange
                        ? (mode) => onContextModeChange(source.id, mode)
                        : undefined
                      }
                    />
                  </div>
                )}
                footer={isFetchingNextPage ? (
                  <div className="flex items-center justify-center py-4">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                ) : undefined}
              />
            ) : (
              <div className="space-y-3">
                {sources.map((source) => (
                  <SourceCard
                    key={source.id}
                    source={source}
                    onClick={handleSourceClick}
                    onDelete={handleDeleteClick}
                    onRetry={handleRetry}
                    onRemoveFromNotebook={handleRemoveFromNotebook}
                    onRefresh={onRefresh}
                    showVisualCover={showVisualCover}
                    showRemoveFromNotebook={true}
                    notebookId={notebookId}
                    contextMode={contextSelections?.[source.id]}
                    onContextModeChange={onContextModeChange
                      ? (mode) => onContextModeChange(source.id, mode)
                      : undefined
                    }
                  />
                ))}
                {/* Loading indicator for infinite scroll */}
                {isFetchingNextPage && (
                  <div className="flex items-center justify-center py-4">
                    <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </CollapsibleColumn>

      <AddSourceDialog
        open={addDialogOpen}
        onOpenChange={(o) => {
          setAddDialogOpen(o)
          // Clear dropped files when the dialog closes so a later manual "Add
          // source" click doesn't re-prefill stale drag-drop files.
          if (!o) setDroppedFiles(undefined)
        }}
        defaultNotebookId={notebookId}
        onSourceCreated={onRefresh}
        initialFiles={droppedFiles}
      />

      <AddExistingSourceDialog
        open={addExistingDialogOpen}
        onOpenChange={setAddExistingDialogOpen}
        notebookId={notebookId}
        onSuccess={onRefresh}
      />

      {/* v0.8.87 — Discover sources (guarded web search). */}
      <DiscoverSourcesDialog
        open={discoverDialogOpen}
        onOpenChange={setDiscoverDialogOpen}
        notebookId={notebookId}
      />

      <ConfirmDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        title={t('sources.delete')}
        description={t('sources.deleteConfirm')}
        confirmText={t('common.delete')}
        onConfirm={handleDeleteConfirm}
        isLoading={deleteSource.isPending}
        confirmVariant="destructive"
      />

      <ConfirmDialog
        open={removeDialogOpen}
        onOpenChange={setRemoveDialogOpen}
        title={t('sources.removeFromNotebook')}
        description={t('sources.removeConfirm')}
        confirmText={t('common.remove')}
        onConfirm={handleRemoveConfirm}
        isLoading={removeFromNotebook.isPending}
        confirmVariant="default"
      />
    </>
  )
}
