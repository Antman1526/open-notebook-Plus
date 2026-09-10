// v0.8.117 — extracted from page.tsx (~945 lines) as part of the
// per-mode component split. Carries the Ask-mode TabsContent verbatim
// (page.tsx lines 417-725), minus the Deep Research result Card, which
// is now DeepResearchPanel (rendered here). State (askQuestion,
// customModels, showAdvancedModels, showSaveDialog, savePayload,
// copiedBrief, etc.) and mutations stay in page.tsx per the task's
// instructions — this component only renders and calls back via props.

import type { Dispatch, SetStateAction } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { AlertCircle, Settings, Save, Sparkles } from 'lucide-react'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { AudioDictateButton } from '@/components/common/AudioDictateButton'
import { StreamingResponse } from '@/components/search/StreamingResponse'
import { AdvancedModelsDialog } from '@/components/search/AdvancedModelsDialog'
import { SaveToNotebooksDialog } from '@/components/search/SaveToNotebooksDialog'
import { useAsk } from '@/lib/hooks/use-ask'
import { useModelDefaults } from '@/lib/hooks/use-models'
import type { ModalType } from '@/lib/hooks/use-modal-manager'
import { DeepResearchPanel, type DeepResearchResult } from './DeepResearchPanel'

type CustomModels = { strategy: string; answer: string; finalAnswer: string }

interface AskPanelProps {
  t: (key: string) => string
  askQuestion: string
  setAskQuestion: Dispatch<SetStateAction<string>>
  ask: ReturnType<typeof useAsk>
  isDeepResearching: boolean
  onAsk: () => void
  onDeepResearch: () => void
  hasEmbeddingModel: boolean
  modelDefaults: ReturnType<typeof useModelDefaults>['data']
  resolveModelName: (id?: string | null) => string
  customModels: CustomModels | null
  setCustomModels: Dispatch<SetStateAction<CustomModels | null>>
  showAdvancedModels: boolean
  setShowAdvancedModels: Dispatch<SetStateAction<boolean>>
  onSaveAnswer: () => void
  showSaveDialog: boolean
  savePayload: { question: string; answer: string } | null
  onSaveDialogOpenChange: (open: boolean) => void
  deepResearchResult: DeepResearchResult | null
  copiedBrief: boolean
  onCopyBrief: () => void
  onSaveDeepResearchNote: () => void
  openModal: (type: ModalType, id: string) => void
}

export function AskPanel({
  t,
  askQuestion,
  setAskQuestion,
  ask,
  isDeepResearching,
  onAsk,
  onDeepResearch,
  hasEmbeddingModel,
  modelDefaults,
  resolveModelName,
  customModels,
  setCustomModels,
  showAdvancedModels,
  setShowAdvancedModels,
  onSaveAnswer,
  showSaveDialog,
  savePayload,
  onSaveDialogOpenChange,
  deepResearchResult,
  copiedBrief,
  onCopyBrief,
  onSaveDeepResearchNote,
  openModal,
}: AskPanelProps) {
  return (
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
                onAsk()
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
                onClick={onAsk}
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
                onClick={onDeepResearch}
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
                  onClick={onSaveAnswer}
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
          <DeepResearchPanel
            result={deepResearchResult}
            openModal={openModal}
            copiedBrief={copiedBrief}
            onCopyBrief={onCopyBrief}
            onSaveNote={onSaveDeepResearchNote}
          />
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
        {savePayload && (
          <SaveToNotebooksDialog
            open={showSaveDialog}
            onOpenChange={onSaveDialogOpenChange}
            question={savePayload.question}
            answer={savePayload.answer}
          />
        )}
      </CardContent>
    </Card>
  )
}
