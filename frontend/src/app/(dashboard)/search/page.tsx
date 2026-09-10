'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'next/navigation'
import { useTranslation } from '@/lib/hooks/use-translation'
import { AppShell } from '@/components/layout/AppShell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Search, AlertCircle, MessageCircleQuestion } from 'lucide-react'
import { toast } from 'sonner'
import { useSearch } from '@/lib/hooks/use-search'
import { useAsk } from '@/lib/hooks/use-ask'
import { useModelDefaults, useModels } from '@/lib/hooks/use-models'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { searchApi } from '@/lib/api/search'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { AudioDictateButton } from '@/components/common/AudioDictateButton'
import { KnowledgeRouteFrame } from '@/components/deeper-notebook/route-frames/KnowledgeRouteFrames'
import { isVisualSystemV2Enabled } from '@/lib/features'
import { useSourceVisualsEnabled } from '@/lib/features-client'
import type { SearchResult } from '@/lib/types/search'
import { AskPanel } from './components/AskPanel'
import { SearchResultsList } from './components/SearchResultsList'
import type { DeepResearchResult } from './components/DeepResearchPanel'

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
  const [savePayload, setSavePayload] = useState<{ question: string; answer: string } | null>(null)
  const [copiedBrief, setCopiedBrief] = useState(false)

  // Deep Research state
  const [isDeepResearching, setIsDeepResearching] = useState(false)
  const [deepResearchResult, setDeepResearchResult] = useState<DeepResearchResult | null>(null)

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

  const handleAsk = useCallback(async () => {
    if (!askQuestion.trim()) return

    const models = {
      strategy: customModels?.strategy || modelDefaults?.default_chat_model || '',
      answer: customModels?.answer || modelDefaults?.default_chat_model || '',
      finalAnswer: customModels?.finalAnswer || modelDefaults?.default_reasoning_model || modelDefaults?.default_chat_model || '',
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
    } catch (err: unknown) {
      console.error('Deep research error:', err)
      toast.error(err instanceof Error ? err.message : 'Deep Research failed')
    } finally {
      setIsDeepResearching(false)
    }
  }, [askQuestion, customModels, modelDefaults])

  // v0.8.117 — named handlers for AskPanel/DeepResearchPanel, same
  // logic as the inline closures they replace.
  const handleCopyBrief = useCallback(() => {
    if (!deepResearchResult) return
    void navigator.clipboard.writeText(deepResearchResult.research_brief)
    setCopiedBrief(true)
    toast.success('Research brief copied')
    setTimeout(() => setCopiedBrief(false), 2000)
  }, [deepResearchResult])

  const handleSaveDeepResearchNote = useCallback(() => {
    if (!deepResearchResult) return
    setSavePayload({
      question: `Deep Research: ${deepResearchResult.objective}`,
      answer: deepResearchResult.research_brief,
    })
    setShowSaveDialog(true)
  }, [deepResearchResult])

  const handleSaveAnswer = useCallback(() => {
    if (!ask.finalAnswer) return
    setSavePayload({ question: askQuestion, answer: ask.finalAnswer })
    setShowSaveDialog(true)
  }, [askQuestion, ask.finalAnswer])

  const handleSaveDialogOpenChange = useCallback((open: boolean) => {
    setShowSaveDialog(open)
    if (!open) setSavePayload(null)
  }, [])

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
            <AskPanel
              t={t}
              askQuestion={askQuestion}
              setAskQuestion={setAskQuestion}
              ask={ask}
              isDeepResearching={isDeepResearching}
              onAsk={handleAsk}
              onDeepResearch={handleDeepResearch}
              hasEmbeddingModel={hasEmbeddingModel}
              modelDefaults={modelDefaults}
              resolveModelName={resolveModelName}
              customModels={customModels}
              setCustomModels={setCustomModels}
              showAdvancedModels={showAdvancedModels}
              setShowAdvancedModels={setShowAdvancedModels}
              onSaveAnswer={handleSaveAnswer}
              showSaveDialog={showSaveDialog}
              savePayload={savePayload}
              onSaveDialogOpenChange={handleSaveDialogOpenChange}
              deepResearchResult={deepResearchResult}
              copiedBrief={copiedBrief}
              onCopyBrief={handleCopyBrief}
              onSaveDeepResearchNote={handleSaveDeepResearchNote}
              openModal={openModal}
            />
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
                          {hasEmbeddingModel
                            ? t('searchPage.hybridSearch', {
                                defaultValue: 'Hybrid (keyword + meaning)',
                              })
                            : t('searchPage.hybridSearchNoEmbedding', {
                                defaultValue: 'Hybrid (keyword only — no embedding model)',
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
                <SearchResultsList
                  searchData={searchMutation.data}
                  searchQuery={searchQuery}
                  visualGalleryEnabled={visualGalleryEnabled}
                  openModal={openModal}
                  evidenceResult={evidenceResult}
                  onViewEvidence={setEvidenceResult}
                  onCloseEvidence={closeEvidence}
                  t={t}
                />
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
      </div>
      </KnowledgeRouteFrame>
    </AppShell>
  )
}
