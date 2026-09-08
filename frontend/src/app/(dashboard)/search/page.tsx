'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { useTranslation } from '@/lib/hooks/use-translation'
import { AppShell } from '@/components/layout/AppShell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Search, ChevronDown, AlertCircle, Settings, Save, MessageCircleQuestion, Sparkles, BookOpen, Layers } from 'lucide-react'
import { toast } from 'sonner'
import { useSearch } from '@/lib/hooks/use-search'
import { useAsk } from '@/lib/hooks/use-ask'
import { useModelDefaults, useModels } from '@/lib/hooks/use-models'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { searchApi } from '@/lib/api/search'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { AudioDictateButton } from '@/components/common/AudioDictateButton'
import { StreamingResponse } from '@/components/search/StreamingResponse'
import { AdvancedModelsDialog } from '@/components/search/AdvancedModelsDialog'
import { SaveToNotebooksDialog } from '@/components/search/SaveToNotebooksDialog'
import { KnowledgeRouteFrame } from '@/components/deeper-notebook/route-frames/KnowledgeRouteFrames'
import { EvidencePeek } from '@/components/deeper-notebook/source-gallery/EvidencePeek'
import { SourceCover } from '@/components/deeper-notebook/source-gallery/SourceCover'
import { isVisualSystemV2Enabled } from '@/lib/features'
import { useSourceVisualsEnabled } from '@/lib/features-client'
import type { SourceListResponse } from '@/lib/types/api'
import type { SearchResult } from '@/lib/types/search'

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

export default function SearchPage() {
  const { t } = useTranslation()
  // URL params
  const searchParams = useSearchParams()
  const urlQuery = searchParams?.get('q') || ''
  const rawMode = searchParams?.get('mode')
  const urlMode = rawMode === 'search' ? 'search' : 'ask'

  // Tab state (controlled)
  const [activeTab, setActiveTab] = useState<'ask' | 'search'>(
    urlMode === 'search' ? 'search' : 'ask'
  )

  // Search state
  const [searchQuery, setSearchQuery] = useState(urlMode === 'search' ? urlQuery : '')
  const [searchType, setSearchType] = useState<'text' | 'vector' | 'hybrid'>('text')
  const [searchSources, setSearchSources] = useState(true)
  const [searchNotes, setSearchNotes] = useState(true)

  // Ask state
  const [askQuestion, setAskQuestion] = useState(urlMode === 'ask' ? urlQuery : '')

  // Advanced models dialog
  const [showAdvancedModels, setShowAdvancedModels] = useState(false)
  const [customModels, setCustomModels] = useState<{
    strategy: string
    answer: string
    finalAnswer: string
  } | null>(null)

  // Save to notebooks dialog
  const [showSaveDialog, setShowSaveDialog] = useState(false)

  // Deep Research state
  const [isDeepResearching, setIsDeepResearching] = useState(false)
  const [deepResearchResult, setDeepResearchResult] = useState<any | null>(null)

  // Hooks
  const searchMutation = useSearch()
  const ask = useAsk()
  const { data: modelDefaults, isLoading: modelsLoading } = useModelDefaults()
  const { data: availableModels } = useModels()
  const { openModal } = useModalManager()
  const sourceVisualsEnabled = useSourceVisualsEnabled()
  const visualGalleryEnabled = isVisualSystemV2Enabled() && sourceVisualsEnabled
  const [evidenceResult, setEvidenceResult] = useState<SearchResult | null>(null)
  const closeEvidence = useCallback(() => setEvidenceResult(null), [])

  const modelNameById = useMemo(() => {
    if (!availableModels) {
      return new Map<string, string>()
    }
    return new Map(availableModels.map((model) => [model.id, model.name]))
  }, [availableModels])

  const resolveModelName = (id?: string | null) => {
    if (!id) return t('searchPage.notSet')
    return modelNameById.get(id) ?? id
  }

  const hasEmbeddingModel = !!modelDefaults?.default_embedding_model

  // Track if we've already auto-triggered from URL params
  const hasAutoTriggeredRef = useRef(false)
  const lastUrlParamsRef = useRef({ q: '', mode: '' })

  const handleSearch = useCallback(() => {
    if (!searchQuery.trim()) return

    setEvidenceResult(null)
    searchMutation.mutate({
      query: searchQuery,
      type: searchType,
      limit: 100,
      search_sources: searchSources,
      search_notes: searchNotes,
      minimum_score: 0.2
    })
  }, [searchQuery, searchType, searchSources, searchNotes, searchMutation])

  useEffect(() => {
    if (!evidenceResult) return
    const current = searchMutation.data?.results.some(result =>
      result.id === evidenceResult.id
      && result.parent_id === evidenceResult.parent_id
      && result.updated === evidenceResult.updated
      && result.matches?.[0] === evidenceResult.matches?.[0]
    )
    if (!current) setEvidenceResult(null)
  }, [evidenceResult, searchMutation.data])

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      handleSearch()
    }
  }

  const handleAsk = useCallback(() => {
    if (!askQuestion.trim() || !modelDefaults?.default_chat_model) return

    const models = customModels || {
      strategy: modelDefaults.default_chat_model,
      answer: modelDefaults.default_chat_model,
      finalAnswer: modelDefaults.default_chat_model
    }

    ask.sendAsk(askQuestion, models)
  }, [askQuestion, modelDefaults, customModels, ask])

  const handleDeepResearch = useCallback(async () => {
    if (!askQuestion.trim()) return
    setIsDeepResearching(true)
    setDeepResearchResult(null)
    try {
      const data = await searchApi.deepResearch({
        objective: askQuestion,
        max_queries: 4,
        strategy_model: customModels?.strategy || modelDefaults?.default_chat_model || undefined,
        synthesis_model: customModels?.finalAnswer || modelDefaults?.default_reasoning_model || modelDefaults?.default_chat_model || undefined,
      })
      setDeepResearchResult(data)
      toast.success('Deep Research brief ready')
    } catch (err: any) {
      console.error('Deep research error:', err)
      toast.error(err.message || 'Deep Research failed')
    } finally {
      setIsDeepResearching(false)
    }
  }, [askQuestion, customModels, modelDefaults])

  // v0.7.204 — stash the latest handlers in refs so the auto-trigger
  // effect doesn't need them in its deps. Previously the effect
  // listed `handleSearch` / `handleAsk` (and via them: searchQuery,
  // searchType, searchSources, searchNotes, askQuestion,
  // modelDefaults, customModels) — each of those changing as the
  // user typed re-ran the effect. The `hasAutoTriggeredRef` guard
  // saved correctness but the effect was deeply tangled with
  // half the page state. Narrow the deps to JUST the URL-driven
  // trigger inputs.
  const handleSearchRef = useRef(handleSearch)
  const handleAskRef = useRef(handleAsk)
  useEffect(() => {
    handleSearchRef.current = handleSearch
    handleAskRef.current = handleAsk
  }, [handleSearch, handleAsk])

  // Auto-trigger search/ask when arriving with URL params
  useEffect(() => {
    // Skip if already triggered or no query
    if (hasAutoTriggeredRef.current || !urlQuery) return

    // Wait for models to load before triggering ask
    if (urlMode === 'ask' && modelsLoading) return

    if (urlMode === 'search') {
      handleSearchRef.current()
      hasAutoTriggeredRef.current = true
    } else if (urlMode === 'ask' && modelDefaults?.default_chat_model) {
      handleAskRef.current()
      hasAutoTriggeredRef.current = true
    }
    // v0.7.204 — intentionally narrow deps. handleSearch/handleAsk
    // accessed via refs above so they're always current without
    // re-running the trigger logic.
  }, [urlQuery, urlMode, modelsLoading, modelDefaults?.default_chat_model])

  // Handle URL param changes while on page (e.g., from command palette again)
  useEffect(() => {
    const currentQ = searchParams?.get('q') || ''
    const rawCurrentMode = searchParams?.get('mode')
    const currentMode = rawCurrentMode === 'search' ? 'search' : 'ask'

    // Check if URL params have changed
    if (currentQ !== lastUrlParamsRef.current.q || currentMode !== lastUrlParamsRef.current.mode) {
      lastUrlParamsRef.current = { q: currentQ, mode: currentMode }

      if (currentQ) {
        // Update state based on mode
        if (currentMode === 'search') {
          setSearchQuery(currentQ)
          setActiveTab('search')
          // Reset trigger flag so we auto-trigger with new params
          hasAutoTriggeredRef.current = false
        } else {
          setAskQuestion(currentQ)
          setActiveTab('ask')
          hasAutoTriggeredRef.current = false
        }
      }
    }
  }, [searchParams])

  return (
    <AppShell>
      {/* v0.7.164 — Visual sweep. Was `p-4 md:p-6` (smaller than every
          other dashboard page) + `text-xl md:text-2xl font-bold`
          (smaller H1). The Ask & Search page is a flagship feature
          competing with NotebookLM — it shouldn't read as a junior
          screen. Standardised to:
            - `px-6 py-10 sm:px-8` (matches Podcasts/Settings/Models)
            - `text-3xl font-semibold tracking-tight` H1
            - Removed the noisy "CHOOSE A MODE" all-caps caption above
              the tabs (same fix as Podcasts in v0.7.153 — two-tab
              toggles are self-explanatory).
          v0.7.180 — the orphaned `searchPage.chooseAMode` key has
          since been removed from all 10 locale files. */}
      <KnowledgeRouteFrame route="/search" title={t('searchPage.askAndSearch')}>
      <div className="space-y-8">

        <Tabs value={activeTab} onValueChange={(v) => setActiveTab(v as 'ask' | 'search')} className="w-full space-y-8">
          <TabsList aria-label={t('common.accessibility.searchKB')} className="w-full max-w-xl">
            <TabsTrigger value="ask">
              <MessageCircleQuestion className="h-4 w-4" />
              {t('searchPage.askBeta')}
            </TabsTrigger>
            <TabsTrigger value="search">
              <Search className="h-4 w-4" />
              {t('searchPage.search')}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="ask" className="mt-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">{t('searchPage.askYourKb')}</CardTitle>
                <p className="text-sm text-muted-foreground">
                  {t('searchPage.askYourKbDesc')}
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Question Input */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="ask-question">{t('searchPage.question')}</Label>
                    <AudioDictateButton
                      onTranscribed={(t) => setAskQuestion((prev) => (prev ? `${prev} ${t}` : t))}
                      disabled={ask.isStreaming || isDeepResearching}
                    />
                  </div>
                  <Textarea
                    id="ask-question"
                    name="ask-question"
                    placeholder={t('searchPage.enterQuestionPlaceholder')}
                    value={askQuestion}
                    onChange={(e) => setAskQuestion(e.target.value)}
                    onKeyDown={(e) => {
                      // Submit on Cmd/Ctrl+Enter
                      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && !ask.isStreaming && !isDeepResearching && askQuestion.trim()) {
                        e.preventDefault()
                        handleAsk()
                      }
                    }}
                    disabled={ask.isStreaming || isDeepResearching}
                    rows={3}
                    aria-label={t('common.accessibility.enterQuestion')}
                  />
                  <p className="text-xs text-muted-foreground">{t('searchPage.pressToSubmit')}</p>
                </div>

                {/* Models Display */}
                {!hasEmbeddingModel ? (
                  <div className="flex items-center gap-2 p-3 text-sm text-amber-600 dark:text-amber-500 bg-amber-50 dark:bg-amber-950/20 rounded-md">
                    <AlertCircle className="h-4 w-4" />
                    <span>{t('searchPage.noEmbeddingModel')}</span>
                  </div>
                ) : (
                  <>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label className="text-xs text-muted-foreground">
                          {customModels ? t('searchPage.usingCustomModels') : t('searchPage.usingDefaultModels')}
                        </Label>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setShowAdvancedModels(true)}
                          disabled={ask.isStreaming}
                          className="h-auto py-1 px-2"
                        >
                          <Settings className="h-3 w-3 mr-1" />
                          {t('searchPage.advanced')}
                        </Button>
                      </div>
                      <div className="flex gap-2 text-xs flex-wrap">
                        <Badge variant="secondary">
                          {t('searchPage.strategy')}: {resolveModelName(customModels?.strategy || modelDefaults?.default_chat_model)}
                        </Badge>
                        <Badge variant="secondary">
                          {t('searchPage.answer')}: {resolveModelName(customModels?.answer || modelDefaults?.default_chat_model)}
                        </Badge>
                        <Badge variant="secondary">
                          {t('searchPage.final')}: {resolveModelName(customModels?.finalAnswer || modelDefaults?.default_chat_model)}
                        </Badge>
                      </div>
                    </div>

                    <div className="flex flex-col sm:flex-row gap-2">
                      <Button
                        onClick={handleAsk}
                        disabled={ask.isStreaming || isDeepResearching || !askQuestion.trim()}
                        className="w-full"
                      >
                        {ask.isStreaming ? (
                          <>
                            <LoadingSpinner size="sm" className="mr-2" />
                            {t('searchPage.processing')}
                          </>
                        ) : (
                          t('searchPage.ask')
                        )}
                      </Button>

                      <Button
                        type="button"
                        variant="secondary"
                        onClick={handleDeepResearch}
                        disabled={isDeepResearching || ask.isStreaming || !askQuestion.trim()}
                        className="w-full sm:w-auto shrink-0 flex items-center justify-center gap-1.5"
                      >
                        {isDeepResearching ? (
                          <>
                            <LoadingSpinner size="sm" className="mr-1.5" />
                            Deep Researching...
                          </>
                        ) : (
                          <>
                            <Sparkles className="h-4 w-4 text-primary" />
                            Deep Research
                          </>
                        )}
                      </Button>

                      {ask.finalAnswer && (
                        <Button
                          variant="outline"
                          onClick={() => setShowSaveDialog(true)}
                          className="w-full"
                        >
                          <Save className="h-4 w-4 mr-2" />
                          {t('searchPage.saveToNotebooks')}
                        </Button>
                      )}
                    </div>
                  </>
                )}

                {/* Streaming Response */}
                <StreamingResponse
                  isStreaming={ask.isStreaming}
                  strategy={ask.strategy}
                  answers={ask.answers}
                  finalAnswer={ask.finalAnswer}
                />

                {/* Deep Research Result */}
                {deepResearchResult && (
                  <Card className="border-primary/30 bg-primary/[0.02] shadow-sm">
                    <CardHeader className="pb-3">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-2">
                          <Sparkles className="h-5 w-5 text-primary" />
                          <CardTitle className="text-base font-semibold">Deep Research Synthesis</CardTitle>
                        </div>
                        <Badge variant="secondary" className="text-xs">
                          {deepResearchResult.evidence_count} evidence items
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        Objective: {deepResearchResult.objective}
                      </p>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {deepResearchResult.plan?.inquiry_paths && deepResearchResult.plan.inquiry_paths.length > 0 && (
                        <div className="space-y-2">
                          <Label className="text-xs font-medium text-muted-foreground">Inquiry Paths Explored</Label>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                            {deepResearchResult.plan.inquiry_paths.map((p: any, idx: number) => (
                              <div key={idx} className="p-2.5 rounded-lg border bg-background/80 text-xs space-y-1">
                                <div className="font-medium text-foreground flex items-center gap-1.5">
                                  <Layers className="h-3.5 w-3.5 text-primary/70 shrink-0" />
                                  <span>{p.sub_question}</span>
                                </div>
                                <div className="text-muted-foreground italic font-mono text-[11px]">
                                  Query: &quot;{p.search_query}&quot;
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="space-y-2">
                        <Label className="text-xs font-medium text-muted-foreground">Research Brief</Label>
                        <div className="p-4 rounded-lg border bg-background text-sm leading-relaxed whitespace-pre-wrap font-sans">
                          {deepResearchResult.research_brief}
                        </div>
                      </div>

                      {deepResearchResult.citations && deepResearchResult.citations.length > 0 && (
                        <div className="space-y-2">
                          <Label className="text-xs font-medium text-muted-foreground">Citations</Label>
                          <div className="flex flex-wrap gap-1.5">
                            {deepResearchResult.citations.map((c: any, idx: number) => (
                              <Badge
                                key={idx}
                                variant="outline"
                                className="text-xs cursor-pointer hover:bg-muted"
                                onClick={() => {
                                  if (c.id) {
                                    openModal('source', c.id)
                                  }
                                }}
                              >
                                [{c.ref || idx + 1}] {c.title || c.id}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                {/* Advanced Models Dialog */}
                <AdvancedModelsDialog
                  open={showAdvancedModels}
                  onOpenChange={setShowAdvancedModels}
                  defaultModels={{
                    strategy: customModels?.strategy || modelDefaults?.default_chat_model || '',
                    answer: customModels?.answer || modelDefaults?.default_chat_model || '',
                    finalAnswer: customModels?.finalAnswer || modelDefaults?.default_chat_model || ''
                  }}
                  onSave={setCustomModels}
                />

                {/* Save to Notebooks Dialog */}
                {ask.finalAnswer && (
                  <SaveToNotebooksDialog
                    open={showSaveDialog}
                    onOpenChange={setShowSaveDialog}
                    question={askQuestion}
                    answer={ask.finalAnswer}
                  />
                )}
              </CardContent>
            </Card>
          </TabsContent>

          <TabsContent value="search" className="mt-6">
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">{t('searchPage.search')}</CardTitle>
                <p className="text-sm text-muted-foreground">
                  {t('searchPage.searchDesc')}
                </p>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Search Input */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label htmlFor="search-query" className="text-sm font-medium">
                      {t('searchPage.search')}
                    </Label>
                    <AudioDictateButton
                      onTranscribed={(t) => setSearchQuery((prev) => (prev ? `${prev} ${t}` : t))}
                      disabled={searchMutation.isPending}
                    />
                  </div>
                  <div className="flex flex-col sm:flex-row gap-2">
                    <Input
                      id="search-query"
                      name="search-query"
                      placeholder={t('searchPage.enterSearchPlaceholder')}
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      // v0.7.200 — React 19 deprecates onKeyPress.
                      // onKeyDown matches the Ask textarea below.
                      onKeyDown={handleKeyPress}
                      disabled={searchMutation.isPending}
                      className="flex-1"
                      aria-label={t('common.accessibility.enterSearch')}
                      autoComplete="off"
                    />
                    <Button
                      onClick={handleSearch}
                      disabled={searchMutation.isPending || !searchQuery.trim()}
                      aria-label={t('common.accessibility.searchKBBtn')}
                      className="w-full sm:w-auto"
                    >
                      {searchMutation.isPending ? (
                        <LoadingSpinner size="sm" />
                      ) : (
                        <Search className="h-4 w-4 mr-2" />
                      )}
                      {t('searchPage.search')}
                    </Button>
                  </div>
                  <p className="text-xs text-muted-foreground">{t('searchPage.pressToSearch')}</p>
                </div>

                {/* Search Options */}
                <div className="space-y-4">
                  {/* Search Type */}
                  <div className="space-y-2" role="group" aria-labelledby="search-type-label">
                    <span id="search-type-label" className="text-sm font-medium leading-none">{t('searchPage.searchType')}</span>
                    {!hasEmbeddingModel && (
                      <div className="flex items-center gap-2 text-sm text-amber-600 dark:text-amber-500">
                        <AlertCircle className="h-4 w-4" />
                        <span>{t('searchPage.vectorSearchWarning')}</span>
                      </div>
                    )}
                    <RadioGroup
                      name="search-type"
                      value={searchType}
                      onValueChange={(value: 'text' | 'vector' | 'hybrid') => setSearchType(value)}
                      disabled={modelsLoading || searchMutation.isPending}
                    >
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem value="text" id="text" />
                        <Label htmlFor="text" className="font-normal cursor-pointer">
                          {t('searchPage.textSearch')}
                        </Label>
                      </div>
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem
                          value="vector"
                          id="vector"
                          disabled={!hasEmbeddingModel || searchMutation.isPending}
                        />
                        <Label
                          htmlFor="vector"
                          className={`font-normal ${!hasEmbeddingModel ? 'text-muted-foreground cursor-not-allowed' : 'cursor-pointer'}`}
                        >
                          {t('searchPage.vectorSearch')}
                        </Label>
                      </div>
                      {/* v0.8.113 — runs both legs and fuses them by rank.
                          NOT disabled without an embedding model: the backend
                          degrades to the text leg alone, which still beats the
                          400 that plain vector search returns. */}
                      <div className="flex items-center space-x-2">
                        <RadioGroupItem
                          value="hybrid"
                          id="hybrid"
                          disabled={searchMutation.isPending}
                        />
                        <Label htmlFor="hybrid" className="font-normal cursor-pointer">
                          {t('searchPage.hybridSearch', {
                            defaultValue: hasEmbeddingModel
                              ? 'Hybrid (keyword + meaning)'
                              : 'Hybrid (keyword only — no embedding model)',
                          })}
                        </Label>
                      </div>
                    </RadioGroup>
                  </div>

                  {/* Search Locations */}
                  <div className="space-y-2" role="group" aria-labelledby="search-in-label">
                    <span id="search-in-label" className="text-sm font-medium leading-none">{t('searchPage.searchIn')}</span>
                    <div className="space-y-2">
                      <div className="flex items-center space-x-2">
                        <Checkbox
                          id="sources"
                          name="sources"
                          checked={searchSources}
                          onCheckedChange={(checked) => setSearchSources(checked as boolean)}
                          disabled={searchMutation.isPending}
                        />
                        <Label htmlFor="sources" className="font-normal cursor-pointer">
                          {t('searchPage.searchSources')}
                        </Label>
                      </div>
                      <div className="flex items-center space-x-2">
                        <Checkbox
                          id="notes"
                          name="notes"
                          checked={searchNotes}
                          onCheckedChange={(checked) => setSearchNotes(checked as boolean)}
                          disabled={searchMutation.isPending}
                        />
                        <Label htmlFor="notes" className="font-normal cursor-pointer">
                          {t('searchPage.searchNotes')}
                        </Label>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Search Results */}
                {searchMutation.data && (
                  <div className="mt-6 space-y-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-medium">
                        {t('searchPage.resultsFound').replace('{count}', searchMutation.data.total_count.toString())}
                      </h3>
                      <Badge variant="outline">{searchMutation.data.search_type === 'text' ? t('searchPage.textSearch') : t('searchPage.vectorSearch')}</Badge>
                    </div>

                    {searchMutation.data.results.length === 0 ? (
                      <Card>
                        <CardContent className="pt-6 text-center text-muted-foreground">
                          {t('searchPage.noResultsFor').replace('{query}', searchQuery)}
                        </CardContent>
                      </Card>
                    ) : (
                      <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-2">
                        {searchMutation.data.results.map((result, index) => {
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
                            <Card key={index}>
                              <CardContent className="pt-4">
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
                                          className="min-h-11 min-w-11 text-primary hover:underline font-medium"
                                        >
                                          {result.title}
                                        </button>
                                        <Badge variant="secondary" className="ml-2">
                                          {result.final_score.toFixed(2)}
                                        </Badge>
                                        {result.vault_provenance && (
                                          <p className="mt-1 text-xs text-muted-foreground">
                                            {result.vault_provenance.relative_path}
                                          </p>
                                        )}
                                      </div>
                                    </div>
                                    {sourceResult && result.matches?.[0] ? (
                                      <Button
                                        className="mt-3 min-h-11 min-w-11"
                                        onClick={() => setEvidenceResult(result)}
                                        size="sm"
                                        type="button"
                                        variant="outline"
                                      >
                                        View evidence for {result.title}
                                      </Button>
                                    ) : null}
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
                        })}
                      </div>
                    )}
                    {evidenceResult ? (
                      <EvidencePeek
                        evidenceQuery={evidenceResult.matches?.[0]}
                        onClose={closeEvidence}
                        sourceId={evidenceResult.id}
                        title={evidenceResult.title}
                      />
                    ) : null}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
      </KnowledgeRouteFrame>
    </AppShell>
  )
}
