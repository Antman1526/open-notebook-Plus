// v0.8.117 — extracted from page.tsx (~945 lines) as part of the
// per-mode component split. Carries the v0.8.116 virtualized/plain
// results branch verbatim (page.tsx lines 868-936), plus the module
// constants and helpers it depends on (page.tsx lines 44-192):
// VIRTUALIZE_THRESHOLD, SEARCH_RESULT_CARD_ESTIMATE_PX,
// sourceCoverFromResult, searchResultTarget, renderSearchResultCard.
// No behaviour change — props stand in for what were page-local state
// and closures.

import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { ChevronDown, Layers, Sparkles } from 'lucide-react'
import { EvidencePeek } from '@/components/deeper-notebook/source-gallery/EvidencePeek'
import { SourceCover } from '@/components/deeper-notebook/source-gallery/SourceCover'
import { VirtualizedListAuto } from '@/components/ui/virtualized-list'
import type { SourceListResponse } from '@/lib/types/api'
import type { SearchResponse, SearchResult } from '@/lib/types/search'
import type { ModalType } from '@/lib/hooks/use-modal-manager'

// v0.8.116 — virtualize the results list only once it's large enough to
// feel the cost of full rendering. `handleSearch` hardcodes `limit: 100`,
// so 100 is the real ceiling; 50 mirrors the threshold SourcesColumn.tsx
// established for the same variable-height-card tradeoff. Below the
// threshold the plain map keeps SSR-friendly behavior + zero
// virtualization overhead, and the existing tests (1-3 results) are
// unaffected.
const VIRTUALIZE_THRESHOLD = 50
// Result cards are variable-height (optional cover image, collapsible
// match list). 140px is a close-to-real estimate for the common case
// (title + score badge, no expanded matches) — fewer scroll jumps as
// the virtualizer measures real heights.
const SEARCH_RESULT_CARD_ESTIMATE_PX = 140

function sourceCoverFromResult(result: SearchResult): SourceListResponse {
  return {
    id: result.id,
    title: result.title,
    source_type: result.source_type ?? null,
    asset: null,
    embedded: true,
    embedded_chunks: 0,
    insights_count: 0,
    created: result.created,
    updated: result.updated,
    visual: result.visual ?? null,
    visual_status: result.visual_status ?? null,
  }
}

function searchResultTarget(result: SearchResult): {
  recordType: 'source' | 'note' | 'source_insight'
  modalType: 'source' | 'note' | 'insight'
  modalId: string
} | null {
  const separator = result.id.indexOf(':')
  if (separator <= 0 || separator === result.id.length - 1) return null
  const recordType = result.id.slice(0, separator)
  const modalId = result.id.slice(separator + 1)
  if (recordType === 'source') return { recordType, modalType: 'source', modalId }
  if (recordType === 'note') return { recordType, modalType: 'note', modalId }
  if (recordType === 'source_insight') return { recordType, modalType: 'insight', modalId }
  return null
}

// v0.8.116 — the per-result card, extracted so both the plain-map path
// and the VirtualizedListAuto path (see VIRTUALIZE_THRESHOLD below) render
// identical markup instead of maintaining it twice. `result.id` already
// carries its record-type prefix (e.g. "source:one", "note:three"), so it
// is a stable, collision-free key/data-testid on its own — no extra
// prefixing needed.
function renderSearchResultCard(
  result: SearchResult,
  {
    visualGalleryEnabled,
    openModal,
    onViewEvidence,
    t,
  }: {
    visualGalleryEnabled: boolean
    openModal: (type: ModalType, id: string) => void
    onViewEvidence: (result: SearchResult) => void
    t: (key: string) => string
  }
): React.ReactNode {
  // A result's own record ID defines its route and kind.
  // parent_id is only relationship metadata (for example,
  // a source insight's parent source) and never visual or
  // modal identity authority.
  const target = searchResultTarget(result)
  if (!target) {
    console.warn('Search result with invalid record id:', result)
    return null
  }
  const sourceResult = visualGalleryEnabled && target.recordType === 'source'

  return (
    <Card
      key={result.id}
      data-testid={`search-result-card-${result.id}`}
      className="group rounded-xl border border-border/60 bg-card/95 p-1 transition-all duration-200 hover:border-primary/40 hover:shadow-md active:scale-[0.99] ring-1 ring-border/20 shadow-[inset_0_1px_1px_rgba(255,255,255,0.05)] dark:shadow-[inset_0_1px_1px_rgba(255,255,255,0.03)]"
    >
      <CardContent className="pt-3 pb-3 px-3.5">
        <div className={sourceResult ? 'grid min-w-0 gap-4 sm:grid-cols-[10rem_minmax(0,1fr)]' : ''}>
          {sourceResult ? (
            <div className="min-w-0" data-testid={`search-result-cover-${result.id}`}>
              <SourceCover source={sourceCoverFromResult(result)} variant="compact" />
            </div>
          ) : null}
          <div className="min-w-0">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0 flex-1">
                <button
                  onClick={() => openModal(target.modalType, target.modalId)}
                  className="text-foreground hover:text-primary transition-colors font-medium text-left leading-snug cursor-pointer"
                >
                  {result.title}
                </button>
                <Badge variant="secondary" className="ml-2 rounded-full text-xs font-mono">
                  {result.final_score.toFixed(2)}
                </Badge>
                {result.rerank_score !== undefined && (
                  <Badge variant="outline" className="ml-2 rounded-full border-primary/30 text-primary text-xs font-mono bg-primary/[0.04]">
                    Rerank: {result.rerank_score.toFixed(3)}
                  </Badge>
                )}
                {result.vault_provenance && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    {result.vault_provenance.relative_path}
                  </p>
                )}
              </div>
              {sourceResult && result.matches?.[0] ? (
                <Button
                  variant="ghost"
                  size="sm"
                  type="button"
                  onClick={() => onViewEvidence(result)}
                  aria-label={`View evidence for ${result.title}`}
                  className="gap-1.5 rounded-full text-xs text-muted-foreground hover:text-foreground transition-all duration-150"
                >
                  <Layers className="h-3.5 w-3.5" />
                  View evidence for {result.title}
                </Button>
              ) : null}
            </div>
          </div>
        </div>

        {result.matches && result.matches.length > 0 && (
          <Collapsible className="mt-3">
            <CollapsibleTrigger className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground">
              <ChevronDown className="h-4 w-4" />
              {t('searchPage.matches').replace('{count}', result.matches.length.toString())}
            </CollapsibleTrigger>
            <CollapsibleContent className="mt-2 space-y-1">
              {result.matches.map((match, i) => (
                <div key={i} className="text-sm pl-6 py-1 border-l-2 border-muted">
                  {match}
                </div>
              ))}
            </CollapsibleContent>
          </Collapsible>
        )}
      </CardContent>
    </Card>
  )
}

interface SearchResultsListProps {
  searchData: SearchResponse | undefined
  searchQuery: string
  visualGalleryEnabled: boolean
  openModal: (type: ModalType, id: string) => void
  evidenceResult: SearchResult | null
  onViewEvidence: (result: SearchResult) => void
  onCloseEvidence: () => void
  t: (key: string) => string
}

export function SearchResultsList({
  searchData,
  searchQuery,
  visualGalleryEnabled,
  openModal,
  evidenceResult,
  onViewEvidence,
  onCloseEvidence,
  t,
}: SearchResultsListProps) {
  if (!searchData) return null

  return (
    <div className="mt-6 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">
          {t('searchPage.resultsFound').replace('{count}', searchData.total_count.toString())}
        </h3>
        <div className="flex items-center gap-2">
          {searchData.reranked && (
            <Badge variant="secondary" className="border-primary/30 text-primary gap-1">
              <Sparkles className="h-3 w-3" />
              Cross-Encoder Reranked
            </Badge>
          )}
          <Badge variant="outline">
            {searchData.search_type === 'text'
              ? t('searchPage.textSearch')
              : searchData.search_type === 'hybrid'
                ? 'Hybrid Search'
                : t('searchPage.vectorSearch')}
          </Badge>
        </div>
      </div>

      {searchData.results.length === 0 ? (
        <Card>
          <CardContent className="pt-6 text-center text-muted-foreground">
            {t('searchPage.noResultsFor').replace('{query}', searchQuery)}
          </CardContent>
        </Card>
      ) : searchData.results.length >= VIRTUALIZE_THRESHOLD ? (
        <VirtualizedListAuto
          items={searchData.results}
          estimateSize={SEARCH_RESULT_CARD_ESTIMATE_PX}
          className="max-h-[60vh] overflow-y-auto pr-2"
          getItemKey={(result, index) => result.id ?? index}
          renderItem={(result) => (
            <div className="pb-2">
              {renderSearchResultCard(result, {
                visualGalleryEnabled,
                openModal,
                onViewEvidence,
                t,
              })}
            </div>
          )}
        />
      ) : (
        <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-2">
          {searchData.results.map((result) =>
            renderSearchResultCard(result, {
              visualGalleryEnabled,
              openModal,
              onViewEvidence,
              t,
            })
          )}
        </div>
      )}
      {evidenceResult ? (
        <EvidencePeek
          evidenceQuery={evidenceResult.matches?.[0]}
          onClose={onCloseEvidence}
          sourceId={evidenceResult.id}
          title={evidenceResult.title}
        />
      ) : null}
    </div>
  )
}
