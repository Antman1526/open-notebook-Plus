'use client'

import { useState, useEffect, useCallback, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { sourcesApi } from '@/lib/api/sources'
import { SourceListResponse } from '@/lib/types/api'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { EmptyState } from '@/components/common/EmptyState'
import { AppShell } from '@/components/layout/AppShell'
import { ConfirmDialog } from '@/components/common/ConfirmDialog'
import { FileText, Link as LinkIcon, Upload, AlignLeft, Trash2, ArrowUpDown, Plus, Share2 } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getDateLocale } from '@/lib/utils/date-locale'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'
import { getApiErrorKey } from '@/lib/utils/error-handler'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import { KnowledgeRouteFrame } from '@/components/deeper-notebook/route-frames/KnowledgeRouteFrames'
import { SourceGallery } from '@/components/deeper-notebook/source-gallery/SourceGallery'
import { isVisualSystemV2Enabled } from '@/lib/features'
import { useSourceVisualsEnabled } from '@/lib/features-client'
import { useRefreshSourceVisual, useRemoveSourceVisual } from '@/lib/hooks/use-source-visuals'

export default function SourcesPage() {
  const { t, language } = useTranslation()
  const failedToLoadMessage = t('sources.failedToLoad')
  const { openSourceDialog } = useCreateDialogs()
  const { mutateAsync: refreshVisual } = useRefreshSourceVisual()
  const { mutateAsync: removeVisual } = useRemoveSourceVisual()
  const [sources, setSources] = useState<SourceListResponse[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [sortBy, setSortBy] = useState<'created' | 'updated'>('updated')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [deleteDialog, setDeleteDialog] = useState<{ open: boolean; source: SourceListResponse | null }>({
    open: false,
    source: null
  })
  const router = useRouter()
  const tableRef = useRef<HTMLTableElement>(null)
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const offsetRef = useRef(0)
  const loadingMoreRef = useRef(false)
  const hasMoreRef = useRef(true)
  const PAGE_SIZE = 30
  const sourceVisualsEnabled = useSourceVisualsEnabled()
  const visualGalleryEnabled = isVisualSystemV2Enabled() && sourceVisualsEnabled

  const fetchSources = useCallback(async (reset = false) => {
    try {
      // Check flags before proceeding
      if (!reset && (loadingMoreRef.current || !hasMoreRef.current)) {
        return
      }

      if (reset) {
        setLoading(true)
        offsetRef.current = 0
        setSources([])
        hasMoreRef.current = true
      } else {
        loadingMoreRef.current = true
        setLoadingMore(true)
      }

      const data = await sourcesApi.list({
        limit: PAGE_SIZE,
        offset: offsetRef.current,
        sort_by: sortBy,
        sort_order: sortOrder,
      })

      if (reset) {
        setSources(data)
      } else {
        setSources(prev => [...prev, ...data])
      }

      // Check if we have more data
      const hasMoreData = data.length === PAGE_SIZE
      hasMoreRef.current = hasMoreData
      offsetRef.current += data.length
    } catch (err) {
      console.error('Failed to fetch sources:', err)
      setError(failedToLoadMessage)
      toast.error(failedToLoadMessage)
    } finally {
      setLoading(false)
      setLoadingMore(false)
      loadingMoreRef.current = false
    }
  }, [sortBy, sortOrder, failedToLoadMessage])

  const openSourceDialogAndRefresh = useCallback(() => {
    openSourceDialog({
      onSourceCreated: () => {
        void fetchSources(true)
      },
    })
  }, [fetchSources, openSourceDialog])

  // Initial load and when sort changes
  useEffect(() => {
    fetchSources(true)
  }, [fetchSources])

  useEffect(() => {
    // Focus the table when component mounts or sources change
    if (sources.length > 0 && tableRef.current) {
      tableRef.current.focus()
    }
  }, [sources])

  const scrollToSelectedRow = useCallback((index: number) => {
    const scrollContainer = scrollContainerRef.current
    if (!scrollContainer) return

    // The legacy table and enabled gallery use different selectable elements,
    // but keyboard navigation keeps their shared source-index ordering.
    const selectedElement = visualGalleryEnabled
      ? scrollContainer.querySelectorAll('[data-dn-source-gallery] [role="listitem"]')[index] as HTMLElement
      : scrollContainer.querySelectorAll('tbody tr')[index] as HTMLElement
    if (!selectedElement) return

    const containerRect = scrollContainer.getBoundingClientRect()
    const elementRect = selectedElement.getBoundingClientRect()

    if (elementRect.top < containerRect.top) {
      selectedElement.scrollIntoView({ behavior: 'smooth', block: 'start' })
    } else if (elementRect.bottom > containerRect.bottom) {
      selectedElement.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [visualGalleryEnabled])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (sources.length === 0) return

      // v0.7.186 — Don't hijack arrows/Enter/Home/End when the user
      // is typing in an input. Previously this listener captured
      // every keystroke globally, so e.g. the AppShell search bar,
      // the command palette, any dialog input, or a contenteditable
      // anywhere in the tree all lost their arrow-key caret movement
      // while the Sources page was the active route. CommandPalette
      // already uses this guard pattern (CommandPalette.tsx:77-84).
      const target = e.target instanceof Element ? e.target : null
      const interactiveTarget = target?.closest(
        'button, a, input, textarea, select, option, summary, [role="button"], [role="link"], [role="menuitem"], [role="menuitemcheckbox"], [role="menuitemradio"]',
      )
      if (
        e.defaultPrevented
        || (target instanceof HTMLElement && target.isContentEditable)
        || interactiveTarget
      ) {
        return
      }

      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedIndex((prev) => {
            const newIndex = Math.min(prev + 1, sources.length - 1)
            // Scroll to keep selected row visible
            setTimeout(() => scrollToSelectedRow(newIndex), 0)
            return newIndex
          })
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedIndex((prev) => {
            const newIndex = Math.max(prev - 1, 0)
            // Scroll to keep selected row visible
            setTimeout(() => scrollToSelectedRow(newIndex), 0)
            return newIndex
          })
          break
        case 'Enter':
          e.preventDefault()
          if (sources[selectedIndex]) {
            router.push(`/sources/${sources[selectedIndex].id}`)
          }
          break
        case 'Home':
          e.preventDefault()
          setSelectedIndex(0)
          setTimeout(() => scrollToSelectedRow(0), 0)
          break
        case 'End':
          e.preventDefault()
          const lastIndex = sources.length - 1
          setSelectedIndex(lastIndex)
          setTimeout(() => scrollToSelectedRow(lastIndex), 0)
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [sources, selectedIndex, router, scrollToSelectedRow])

  // Set up scroll listener after sources are loaded
  useEffect(() => {
    const scrollContainer = scrollContainerRef.current
    if (!scrollContainer) return

    let scrollTimeout: NodeJS.Timeout | null = null

    const handleScroll = () => {
      if (scrollTimeout) {
        clearTimeout(scrollTimeout)
      }

      scrollTimeout = setTimeout(() => {
        if (!scrollContainerRef.current) return

        const { scrollTop, scrollHeight, clientHeight } = scrollContainerRef.current
        const distanceFromBottom = scrollHeight - scrollTop - clientHeight

        // Load more when within 200px of the bottom
        if (distanceFromBottom < 200 && !loadingMoreRef.current && hasMoreRef.current) {
          fetchSources(false)
        }
      }, 100)
    }

    scrollContainer.addEventListener('scroll', handleScroll)
    handleScroll() // Check on mount

    return () => {
      scrollContainer.removeEventListener('scroll', handleScroll)
      if (scrollTimeout) {
        clearTimeout(scrollTimeout)
      }
    }
  }, [fetchSources, sources.length])

  const toggleSort = (field: 'created' | 'updated') => {
    if (sortBy === field) {
      // Toggle order if clicking the same field
      setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')
    } else {
      // Switch to new field with default desc order
      setSortBy(field)
      setSortOrder('desc')
    }
  }

  const getSourceIcon = (source: SourceListResponse) => {
    if (source.source_type === 'web_import') return <LinkIcon className="h-4 w-4" />
    if (source.source_type === 'deep_research_report') return <FileText className="h-4 w-4" />
    if (source.asset?.url) return <LinkIcon className="h-4 w-4" />
    if (source.asset?.file_path) return <Upload className="h-4 w-4" />
    return <AlignLeft className="h-4 w-4" />
  }

  const getSourceType = (source: SourceListResponse) => {
    if (source.source_type === 'web_import') return 'Web import'
    if (source.source_type === 'deep_research_report') return 'Deep research'
    if (source.asset?.url) return t('sources.type.link')
    if (source.asset?.file_path) return t('sources.type.file')
    return t('sources.type.text')
  }

  const getProvenanceLabel = (source: SourceListResponse) => {
    const provenance = source.provenance ?? {}
    const value =
      provenance.domain ??
      provenance.original_filename ??
      provenance.file_name ??
      provenance.origin
    return typeof value === 'string' && value.trim() ? value.trim() : null
  }

  const handleRowClick = useCallback((index: number, sourceId: string) => {
    setSelectedIndex(index)
    router.push(`/sources/${sourceId}`)
  }, [router])

  const handleGallerySelect = useCallback((sourceId: string) => {
    const index = sources.findIndex(source => source.id === sourceId)
    if (index >= 0) setSelectedIndex(index)
  }, [sources])

  const handleGalleryOpen = useCallback((sourceId: string) => {
    router.push(`/sources/${sourceId}`)
  }, [router])

  const refreshSourceAfterVisualMutation = useCallback(async (sourceId: string) => {
    try {
      const refreshed = await sourcesApi.get(sourceId)
      setSources(prev => prev.map(source => {
        if (source.id !== sourceId) return source
        const merged: SourceListResponse = { ...source, ...refreshed }
        return merged
      }))
    } catch (err) {
      console.error('Failed to refresh source after visual mutation:', err)
      throw err
    }
  }, [])

  const handleGalleryRefresh = useCallback(async (sourceId: string) => {
    await refreshVisual(sourceId)
    await refreshSourceAfterVisualMutation(sourceId)
  }, [refreshSourceAfterVisualMutation, refreshVisual])

  const handleGalleryRemove = useCallback(async (sourceId: string) => {
    await removeVisual(sourceId)
    await refreshSourceAfterVisualMutation(sourceId)
  }, [refreshSourceAfterVisualMutation, removeVisual])

  const handleDeleteClick = useCallback((e: React.MouseEvent, source: SourceListResponse) => {
    e.stopPropagation() // Prevent row click
    setDeleteDialog({ open: true, source })
  }, [])

  const handleGalleryDelete = useCallback((sourceId: string) => {
    const source = sources.find(candidate => candidate.id === sourceId)
    if (source) setDeleteDialog({ open: true, source })
  }, [sources])

  const handleDeleteConfirm = async () => {
    if (!deleteDialog.source) return

    try {
      await sourcesApi.delete(deleteDialog.source.id)
      toast.success(t('sources.deleteSuccess'))
      // Remove the deleted source from the list
      setSources(prev => prev.filter(s => s.id !== deleteDialog.source?.id))
      setDeleteDialog({ open: false, source: null })
    } catch (err: unknown) {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      console.error('Failed to delete source:', error)
      toast.error(t(getApiErrorKey(error.response?.data?.detail || error.message)))
    }
  }

  const sourceAction = () => (
    <Button onClick={openSourceDialogAndRefresh} className="shrink-0">
      <Plus className="mr-2 h-4 w-4" />
      {t('sources.addNew')}
    </Button>
  )

  if (loading) {
    return (
      <AppShell>
        <KnowledgeRouteFrame
          route="/sources"
          description={t('sources.allSourcesDesc')}
          actions={sourceAction()}
        >
          <div className="flex h-full items-center justify-center">
            <LoadingSpinner />
          </div>
        </KnowledgeRouteFrame>
      </AppShell>
    )
  }

  if (error) {
    return (
      <AppShell>
        <KnowledgeRouteFrame
          route="/sources"
          description={t('sources.allSourcesDesc')}
          actions={sourceAction()}
        >
          <div className="flex h-full items-center justify-center">
            {/* v0.7.180 — text-red-500 → text-destructive so the error
                line absorbs the active theme's destructive hue (same as the
                v0.7.165 ErrorBoundary fix). */}
            <p className="text-destructive">{error}</p>
          </div>
        </KnowledgeRouteFrame>
      </AppShell>
    )
  }

  if (sources.length === 0) {
    return (
      <AppShell>
        <KnowledgeRouteFrame
          route="/sources"
          description={t('sources.allSourcesDesc')}
          actions={sourceAction()}
        >
          <EmptyState
            icon={FileText}
            title={t('sources.noSourcesYet')}
            description={t('sources.allSourcesDescShort')}
          />
        </KnowledgeRouteFrame>
      </AppShell>
    )
  }

  return (
    <AppShell>
      <KnowledgeRouteFrame
        route="/sources"
        description={t('sources.allSourcesDesc')}
        actions={sourceAction()}
      >
        <div className="flex h-full min-h-0 w-full max-w-none flex-col">
          {visualGalleryEnabled ? (
            <div
              ref={scrollContainerRef}
              data-dn-horizontal-scroll="sources-gallery"
              className="flex-1 overflow-auto rounded-md border"
            >
              <SourceGallery
                sources={sources}
                selectedId={sources[selectedIndex]?.id ?? null}
                filters={
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => toggleSort('created')}
                      className="h-11 min-w-11 px-2 hover:bg-muted"
                    >
                      {t('common.created_label')}
                      <ArrowUpDown className={cn(
                        'ml-2 h-3 w-3',
                        sortBy === 'created' ? 'opacity-100' : 'opacity-30'
                      )} />
                      {sortBy === 'created' && (
                        <span className="ml-1 text-xs">{sortOrder === 'asc' ? '↑' : '↓'}</span>
                      )}
                    </Button>
                  </div>
                }
                onSelect={handleGallerySelect}
                onOpen={handleGalleryOpen}
                onRefresh={handleGalleryRefresh}
                onRemove={handleGalleryRemove}
                onDelete={handleGalleryDelete}
              />
            </div>
          ) : (
            <div
              ref={scrollContainerRef}
              data-dn-horizontal-scroll="sources-table"
              className="flex-1 overflow-auto rounded-md border"
            >
          <table
            ref={tableRef}
            tabIndex={0}
            role="grid"
            aria-label={t('navigation.sources')}
            data-dn-sources-table="true"
            className="w-full min-w-[288px] outline-none table-fixed xl:min-w-[800px]"
          >
            <colgroup>
              <col className="w-[7rem] sm:w-[120px]" />
              <col className="w-auto" />
              <col className="hidden w-[140px] xl:table-column" />
              <col className="hidden w-[100px] xl:table-column" />
              <col className="hidden w-[100px] xl:table-column" />
              <col className="w-[4.5rem] sm:w-[100px]" />
            </colgroup>
            <thead className="sticky top-0 bg-background z-10">
              <tr className="border-b bg-muted/50">
                <th className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">
                  {t('common.type')}
                </th>
                <th className="h-12 px-4 text-left align-middle font-medium text-muted-foreground">
                  {t('common.title')}
                </th>
                <th className="h-12 px-4 text-left align-middle font-medium text-muted-foreground hidden xl:table-cell">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => toggleSort('created')}
                    className="h-8 px-2 hover:bg-muted"
                  >
                    {t('common.created_label')}
                    <ArrowUpDown className={cn(
                      "ml-2 h-3 w-3",
                      sortBy === 'created' ? 'opacity-100' : 'opacity-30'
                    )} />
                    {sortBy === 'created' && (
                      <span className="ml-1 text-xs">
                        {sortOrder === 'asc' ? '↑' : '↓'}
                      </span>
                    )}
                  </Button>
                </th>
                <th className="h-12 px-4 text-center align-middle font-medium text-muted-foreground hidden xl:table-cell">
                  {t('sources.insights')}
                </th>
                <th className="h-12 px-4 text-center align-middle font-medium text-muted-foreground hidden xl:table-cell">
                  {t('sources.embedded')}
                </th>
                <th className="h-12 px-4 text-right align-middle font-medium text-muted-foreground">
                  {t('common.actions')}
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.map((source, index) => (
                <tr
                  key={source.id}
                  onClick={() => handleRowClick(index, source.id)}
                  onMouseEnter={() => setSelectedIndex(index)}
                  className={cn(
                    "border-b transition-colors cursor-pointer",
                    selectedIndex === index
                      ? "bg-accent"
                      : "hover:bg-muted/50"
                  )}
                >
                  <td className="h-12 px-4">
                    <div className="flex items-center gap-2">
                      {getSourceIcon(source)}
                      <Badge variant="secondary" className="text-xs">
                        {getSourceType(source)}
                      </Badge>
                    </div>
                  </td>
                  <td className="h-12 px-4">
                    <div className="flex min-w-0 w-full flex-col overflow-hidden">
                      <span className="block min-w-0 truncate font-medium">
                        {source.title || t('sources.untitledSource')}
                      </span>
                      {source.asset?.url && (
                        <span className="text-xs text-muted-foreground truncate">
                          {source.asset.url}
                        </span>
                      )}
                      <div className="mt-1 flex flex-wrap items-center gap-1.5">
                        {(source.is_shared || (source.notebook_count ?? 0) > 1) && (
                          <Badge variant="outline" className="gap-1 text-[11px]">
                            <Share2 className="h-3 w-3" />
                            {(source.notebook_count ?? 0) > 1
                              ? `Shared with ${source.notebook_count}`
                              : 'Shared'}
                          </Badge>
                        )}
                        {getProvenanceLabel(source) && (
                          <Badge variant="outline" className="max-w-[180px] truncate text-[11px]">
                            {getProvenanceLabel(source)}
                          </Badge>
                        )}
                        {source.topics?.slice(0, 2).map((topic) => (
                          <Badge key={topic} variant="outline" className="text-[11px]">
                            {topic}
                          </Badge>
                        ))}
                      </div>
                    </div>
                  </td>
                  <td className="h-12 px-4 text-muted-foreground text-sm hidden xl:table-cell">
                    {(() => {
                      try {
                        const d = new Date(source.created)
                        if (Number.isNaN(d.getTime())) return source.created || '—'
                        return formatDistanceToNow(d, { 
                          addSuffix: true,
                          locale: getDateLocale(language)
                        })
                      } catch {
                        return source.created || '—'
                      }
                    })()}
                  </td>
                  <td className="h-12 px-4 text-center hidden xl:table-cell">
                    <span className="text-sm font-medium">{source.insights_count || 0}</span>
                  </td>
                  <td className="h-12 px-4 text-center hidden xl:table-cell">
                    <Badge variant={source.embedded ? "default" : "secondary"} className="text-xs">
                      {source.embedded ? t('sources.yes') : t('sources.no')}
                    </Badge>
                  </td>
                  <td className="h-12 px-4 text-right">
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={t('sources.delete', { defaultValue: 'Delete source' })}
                      onClick={(e) => handleDeleteClick(e, source)}
                      className="text-destructive hover:text-destructive"
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </td>
                </tr>
              ))}
              {loadingMore && (
                <tr>
                  <td colSpan={6} className="h-16 text-center">
                    <div className="flex items-center justify-center">
                      <LoadingSpinner />
                      <span className="ml-2 text-muted-foreground">{t('sources.loadingMore')}</span>
                    </div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          </div>
          )}

          <ConfirmDialog
            open={deleteDialog.open}
            onOpenChange={(open) => setDeleteDialog({ open, source: deleteDialog.source })}
            title={t('sources.delete')}
            description={t('sources.deleteConfirmWithTitle').replace('{title}', deleteDialog.source?.title || t('sources.untitledSource'))}
            confirmText={t('common.delete')}
            confirmVariant="destructive"
            onConfirm={handleDeleteConfirm}
          />
        </div>
      </KnowledgeRouteFrame>
    </AppShell>
  )
}
