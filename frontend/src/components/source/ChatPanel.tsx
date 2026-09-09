'use client'

import { useState, useRef, useEffect, useId, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { SourceDialog } from './SourceDialog'
import { Bot, User, Send, Loader2, FileText, Lightbulb, StickyNote, Clock, Square, Swords, X, ChevronDown, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import {
  SourceChatMessage,
  SourceChatContextIndicator,
  BaseChatSession
} from '@/lib/types/api'
import { ModelSelector } from './ModelSelector'
import { McpToolPicker } from '@/components/chat/McpToolPicker'
import { ContextIndicator } from '@/components/common/ContextIndicator'
import { SessionManager } from '@/components/source/SessionManager'
import { MessageActions } from '@/components/source/MessageActions'
import { MessageCopyEditActions } from '@/components/chat/MessageCopyEditActions'
import { convertReferencesToCompactMarkdown, createCompactReferenceLinkComponent } from '@/lib/utils/source-references'
import { splitCitations } from '@/lib/utils/citations'
import { CitationPill } from '@/components/chat/CitationPill'
import { AudioDictateButton } from '@/components/common/AudioDictateButton'
// v0.8.35c — small "local"/"cloud" chip next to AI messages, lit when
// the smart router (v0.8.0) actually ran for this notebook turn.
// Reads from the TanStack Query cache populated by useNotebookChat on
// the /chat/stream `done` event; renders null for source-chat (no
// cache entry) and pre-v0.8.1 sessions.
import { ChatMessageProviderBadge } from '@/components/chat/ChatMessageProviderBadge'
import { ChatMessagePrivacyBadge } from '@/components/chat/ChatMessagePrivacyBadge'
import { ChatMessageAgentStateBadge } from '@/components/chat/ChatMessageAgentStateBadge'
import { EvidenceReview } from '@/components/evaluation/EvidenceReview'
import { RunTimeline } from '@/components/deeper-notebook'
import { useLatestMessageEvaluations } from '@/lib/hooks/use-evaluation'
import { useModalManager } from '@/lib/hooks/use-modal-manager'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { MindMapChatContext } from '@/components/deeper-notebook/MindMapArtifactViewer'

interface NotebookContextStats {
  sourcesInsights: number
  sourcesFull: number
  notesCount: number
  tokenCount?: number
  charCount?: number
  // v0.8.89 — total sources in the notebook + names of the in-context ones,
  // for the "Using X of Y sources" indicator + popover.
  totalSources?: number
  contextSourceTitles?: string[]
}

// v0.8.67 (audit F5) — pure, testable predicate: is the scroll viewport within
// `threshold` px of the bottom? Used to decide whether to keep auto-scrolling
// during a streamed reply (don't yank a user who scrolled up to read).
export function isNearBottom(
  el: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold = 120,
): boolean {
  return el.scrollHeight - el.scrollTop - el.clientHeight < threshold
}

interface ChatPanelProps {
  messages: SourceChatMessage[]
  isStreaming: boolean
  contextIndicators: SourceChatContextIndicator | null
  onSendMessage: (message: string, modelOverride?: string) => void
  modelOverride?: string
  onModelChange?: (model?: string) => void
  // Session management props
  sessions?: BaseChatSession[]
  currentSessionId?: string | null
  onCreateSession?: (title: string) => void
  onSelectSession?: (sessionId: string) => void
  onDeleteSession?: (sessionId: string) => void
  onUpdateSession?: (sessionId: string, title: string) => void
  loadingSessions?: boolean
  // Generic props for reusability
  title?: string
  contextType?: 'source' | 'notebook'
  // Notebook context stats (for notebook chat)
  notebookContextStats?: NotebookContextStats
  // Notebook ID for saving notes
  notebookId?: string
  // v0.8.46 — MCP tool picker wiring. Optional so callers that don't
  // care (or have no MCP servers) simply omit them — the picker
  // self-hides when there are no enabled servers. `disabledMcpServers`
  // is the current per-conversation disable list; `onToggleMcpServer`
  // flips one server's state. Both come straight from
  // useNotebookChat / useSourceChat (v0.8.42-v0.8.44b). Pre-v0.8.46
  // the <McpToolPicker> existed + was tested but was never mounted,
  // so the entire feature chain was unreachable from the UI.
  disabledMcpServers?: string[]
  onToggleMcpServer?: (name: string) => void
  // v0.8.97 — Debate mode toggle (notebook chat only; callers that don't
  // pass onToggleDebateMode simply never render the control). When active,
  // every sent turn carries chat_mode: 'debate' and the assistant argues
  // the opposing case grounded in the selected sources.
  debateMode?: boolean
  onToggleDebateMode?: () => void
  // v0.8.63 — "Re-ask allowing cloud" handler for the privacy review sheet.
  // Given the original question text, re-sends it with the privacy gate
  // bypassed (explicit user consent). Only notebook chat provides it; source
  // chat omits it (no privacy badge there), so the review popover is
  // review-only in that case.
  onReaskAllowCloud?: (message: string) => void
  // v0.8.69 — public stop control for notebook streaming. The hook
  // already had AbortController plumbing; ChatPanel now exposes it as
  // a visible action so long local generations are user-controllable.
  onCancelStreaming?: () => void
  // v0.8.74 — optional starter-question chips shown in the empty chat state
  // (improvement roadmap, Batch 1). Notebook chat passes corpus-grounded
  // questions from GET /notebooks/{id}/suggested-questions; clicking one sends
  // it. Omitted by source chat → no chips, no behavior change.
  suggestedQuestions?: string[]
  onSuggestedQuestionClick?: (question: string) => void
}

export function ChatPanel({
  messages,
  isStreaming,
  contextIndicators,
  onSendMessage,
  modelOverride,
  onModelChange,
  sessions = [],
  currentSessionId,
  onCreateSession,
  onSelectSession,
  onDeleteSession,
  onUpdateSession,
  loadingSessions = false,
  title,
  contextType = 'source',
  notebookContextStats,
  notebookId,
  disabledMcpServers,
  onToggleMcpServer,
  debateMode = false,
  onToggleDebateMode,
  onReaskAllowCloud,
  onCancelStreaming,
  suggestedQuestions,
  onSuggestedQuestionClick,
}: ChatPanelProps) {
  const { t } = useTranslation()
  const chatInputId = useId()
  const [input, setInput] = useState('')
  const [sessionManagerOpen, setSessionManagerOpen] = useState(false)
  // v0.8.79 — local source viewer opened from a citation (improvement roadmap,
  // Batch 2). Kept separate from the global modal so it can carry the citing
  // sentence as the highlight query.
  const [citationSource, setCitationSource] = useState<{ id: string; query: string } | null>(null)
  // Mind-map viewers publish this browser-local event only after the server has
  // re-resolved the stable path and citation source subset. The chip remains
  // visible until removed, rather than hiding important scope in prompt text.
  const [mindMapContext, setMindMapContext] = useState<MindMapChatContext | null>(null)
  const scrollAreaRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  // v0.8.67 (F5) — true while the user is at/near the bottom; gates auto-scroll
  // so streamed tokens don't yank a user who scrolled up. Defaults true so the
  // first render + the common "user just sent" case scroll to bottom.
  const stickToBottomRef = useRef(true)
  // v0.8.65g — ref so "Edit" can focus the input after loading a message into it.
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const { openModal } = useModalManager()
  const evaluationMessageIds = useMemo(
    () => contextType === 'notebook' && notebookId
      ? messages
        .filter((message) => message.type === 'ai')
        .map((message) => message.id)
        .filter((messageId) => !messageId.startsWith('temp-') && !messageId.startsWith('streaming-'))
        .slice(-100)
      : [],
    [contextType, messages, notebookId],
  )
  const evaluationMessageIdSet = useMemo(
    () => new Set(evaluationMessageIds),
    [evaluationMessageIds],
  )
  const messageEvaluations = useLatestMessageEvaluations(
    contextType === 'notebook' ? notebookId : undefined,
    evaluationMessageIds,
  )

  useEffect(() => {
    const receiveContext = (event: Event) => {
      const context = (event as CustomEvent<MindMapChatContext>).detail
      if (!context?.artifact_id || !context.label || !Array.isArray(context.source_ids)) return
      setMindMapContext(context)
      requestAnimationFrame(() => textareaRef.current?.focus())
    }
    window.addEventListener('onp:mind-map-context', receiveContext)
    return () => window.removeEventListener('onp:mind-map-context', receiveContext)
  }, [])

  // v0.8.65g — "Edit" a message: load its text into the chat input + focus so
  // the user can tweak and resend it (reuse a prompt / refine an answer).
  const handleEditMessage = (content: string) => {
    setInput(content)
    requestAnimationFrame(() => {
      const el = textareaRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(el.value.length, el.value.length)
      }
    })
  }

  const handleReferenceClick = (type: string, id: string) => {
    const modalType = type === 'source_insight' ? 'insight' : type as 'source' | 'note' | 'insight'

    try {
      openModal(modalType, id)
      // Note: The modal system uses URL parameters and doesn't throw errors for missing items.
      // The modal component itself will handle displaying "not found" states.
      // This try-catch is here for future enhancements or unexpected errors.
    } catch {
      toast.error(t('common.noResults'))
    }
  }

  // v0.8.67 (audit F5) — auto-scroll, fixed for streaming.
  // Was: scrollIntoView({behavior:'smooth'}) on every `messages` change. During
  // a streamed reply that fires on EVERY token (50+ stacked smooth animations →
  // jank) AND yanked the view down even when the user had scrolled up to read.
  // Now: (1) behavior:'auto' (no stacked animations), and (2) only stick to the
  // bottom when the user is already near it. `stickToBottomRef` DEFAULTS true and
  // is only ever set false by a real scroll event, so if the Radix viewport
  // can't be found the behavior safely degrades to today's always-scroll.
  useEffect(() => {
    const root = scrollAreaRef.current
    const viewport = root?.querySelector<HTMLElement>(
      '[data-radix-scroll-area-viewport]',
    )
    if (!viewport) return
    const onScroll = () => {
      stickToBottomRef.current = isNearBottom(viewport)
    }
    viewport.addEventListener('scroll', onScroll, { passive: true })
    return () => viewport.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    if (stickToBottomRef.current) {
      messagesEndRef.current?.scrollIntoView({ behavior: 'auto' })
    }
  }, [messages])

  const handleSend = () => {
    if (input.trim() && !isStreaming) {
      const scopedMessage = mindMapContext
        ? `${mindMapContext.prompt_context}\n\nQuestion: ${input.trim()}`
        : input.trim()
      onSendMessage(scopedMessage, modelOverride)
      setInput('')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // Detect platform for correct modifier key
    const isMac = typeof navigator !== 'undefined' && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0
    const isModifierPressed = isMac ? e.metaKey : e.ctrlKey

    if (e.key === 'Enter' && isModifierPressed) {
      e.preventDefault()
      handleSend()
    }
  }

  // Detect platform for placeholder text
  const isMac = typeof navigator !== 'undefined' && navigator.userAgent.toUpperCase().indexOf('MAC') >= 0
  const keyHint = isMac ? '⌘+Enter' : 'Ctrl+Enter'

  return (
    <>
    <Card className="flex flex-col h-full flex-1 overflow-hidden">
      <CardHeader className="pb-3 flex-shrink-0">
        <div className="flex min-w-0 items-center justify-between gap-2">
          <CardTitle className="flex min-w-0 flex-1 items-center gap-2">
            <Bot className="h-5 w-5" />
            {title || (contextType === 'source' ? t('chat.chatWith').replace('{name}', t('navigation.sources')) : t('chat.chatWith').replace('{name}', t('common.notebook')))}
          </CardTitle>
          {onSelectSession && onCreateSession && onDeleteSession && (
            <Dialog open={sessionManagerOpen} onOpenChange={setSessionManagerOpen}>
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 gap-2"
                onClick={() => setSessionManagerOpen(true)}
                disabled={loadingSessions}
              >
                <Clock className="h-4 w-4" />
                <span className="text-xs">{t('chat.sessions')}</span>
              </Button>
              <DialogContent className="sm:max-w-[420px] p-0 overflow-hidden">
                <DialogTitle className="sr-only">{t('chat.sessionsTitle')}</DialogTitle>
                <SessionManager
                  sessions={sessions}
                  currentSessionId={currentSessionId ?? null}
                  onCreateSession={(title) => onCreateSession?.(title)}
                  onSelectSession={(sessionId) => {
                    onSelectSession(sessionId)
                    setSessionManagerOpen(false)
                  }}
                  onUpdateSession={(sessionId, title) => onUpdateSession?.(sessionId, title)}
                  onDeleteSession={(sessionId) => onDeleteSession?.(sessionId)}
                  loadingSessions={loadingSessions}
                />
              </DialogContent>
            </Dialog>
          )}
        </div>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col min-h-0 p-0">
        <RunTimeline
          messages={messages}
          isStreaming={isStreaming}
          contextStats={notebookContextStats}
          currentModel={modelOverride}
          disabledMcpServers={disabledMcpServers}
        />
        <ScrollArea className="flex-1 min-h-0 px-4" ref={scrollAreaRef}>
          <div className="space-y-4 py-4">
            {messages.length === 0 ? (
              <div className="text-center text-muted-foreground py-8">
                <Bot className="h-12 w-12 mx-auto mb-4 opacity-50" />
                <p className="text-sm">
                  {t('chat.startConversation').replace('{type}', contextType === 'source' ? t('navigation.sources') : t('common.notebook'))}
                </p>
                <p className="text-xs mt-2">{t('chat.askQuestions')}</p>
                {/* v0.8.74 — corpus-grounded starter questions (roadmap Batch 1).
                    Removes the blank-slate problem; clicking a chip sends it. */}
                {suggestedQuestions && suggestedQuestions.length > 0 && (
                  <div className="mx-auto mt-5 max-w-md">
                    <p className="mb-2 text-xs font-medium text-muted-foreground/80">
                      {t('chat.tryAsking', { defaultValue: 'Try asking' })}
                    </p>
                    <div className="flex flex-wrap justify-center gap-2">
                      {suggestedQuestions.map((q) => (
                        <button
                          key={q}
                          type="button"
                          onClick={() => onSuggestedQuestionClick?.(q)}
                          disabled={isStreaming || !onSuggestedQuestionClick}
                          className="rounded-full border border-border/70 bg-card px-3 py-1.5 text-left text-xs text-foreground/90 shadow-sm transition-colors hover:border-primary/50 hover:bg-primary/5 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 disabled:opacity-50"
                        >
                          {q}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            ) : (
              messages.map((message, idx) => (
                <div
                  key={message.id}
                  className={`flex gap-3 ${
                    message.type === 'human' ? 'justify-end' : 'justify-start'
                  }`}
                >
                  {message.type === 'ai' && (
                    <div className="flex-shrink-0">
                      <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center">
                        <Bot className="h-4 w-4" />
                      </div>
                    </div>
                  )}
                  <div className="flex flex-col gap-2 max-w-[80%]">
                    <div
                      className={`rounded-2xl px-4 py-3 shadow-xs transition-all duration-200 ${
                        message.type === 'human'
                          ? 'bg-gradient-to-br from-primary via-primary/95 to-primary/85 text-primary-foreground shadow-sm ring-1 ring-primary/30'
                          : 'border border-border/60 bg-card/95 ring-1 ring-border/20 shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)] dark:shadow-[inset_0_1px_1px_rgba(255,255,255,0.03)]'
                      }`}
                    >
                      {message.type === 'ai' ? (
                        <AIMessageContent
                          content={message.content}
                          onReferenceClick={handleReferenceClick}
                          messageId={message.id}
                          onViewSource={(sourceId, query) =>
                            setCitationSource({ id: sourceId, query })
                          }
                        />
                      ) : (
                        // v0.7.25 — `break-all` breaks between any two
                        // characters, so normal English wrapped mid-word
                        // ("perfor-mance"). `break-words` only wraps
                        // at word boundaries (with overflow-wrap for
                        // unbreakable URLs/tokens).
                        <p className="text-sm break-words">{message.content}</p>
                      )}
                    </div>
                    {message.type === 'human' && (
                      // v0.8.65g — Copy (reuse) + Edit for the user's own
                      // messages, which previously had no actions row.
                      <div className="flex items-center justify-end gap-2 flex-wrap">
                        <MessageCopyEditActions
                          content={message.content}
                          onEdit={handleEditMessage}
                        />
                      </div>
                    )}
                    {message.type === 'ai' && (
                      <div className="flex items-center gap-2 flex-wrap">
                        <MessageActions
                          content={message.content}
                          notebookId={notebookId}
                        />
                        {/* v0.8.65g — Edit (reuse the answer as a new prompt);
                            AI messages already expose Copy via MessageActions. */}
                        <MessageCopyEditActions
                          content={message.content}
                          onEdit={handleEditMessage}
                          showCopy={false}
                        />
                        {/* v0.8.35c — only shows for notebook chat
                            sessions where the smart router populated
                            the cache via /chat/stream's done event.
                            Source-chat and pre-v0.8.1 sessions render
                            null naturally (no cache entry). */}
                        <ChatMessageProviderBadge messageId={message.id} />
                        {/* v0.8.61 — "On-device" chip when the privacy gate
                            kept this turn local. Renders null unless
                            privacy_gated === true in the cached done event. */}
                        <ChatMessagePrivacyBadge
                          messageId={message.id}
                          // v0.8.63 — re-ask the PRECEDING user question with
                          // the privacy gate bypassed (explicit consent). Only
                          // when the host wired onReaskAllowCloud (notebook
                          // chat) and a preceding human message exists.
                          onReask={
                            onReaskAllowCloud &&
                            // v0.8.66 (audit F-5) — don't offer "re-ask allowing
                            // cloud" while a stream is in flight; firing it
                            // mid-stream started a second send that aborted the
                            // in-flight one. The control reappears once idle.
                            !isStreaming &&
                            idx > 0 &&
                            messages[idx - 1]?.type === 'human'
                              ? () =>
                                  onReaskAllowCloud(messages[idx - 1].content)
                              : undefined
                          }
                        />
                        {/* v0.8.62 — agent-FSM "needs input"/"truncated" chip;
                            null unless DEEPER_NOTEBOOK_AGENT_FSM surfaced a non-complete
                            terminal state on the done event. */}
                        <ChatMessageAgentStateBadge messageId={message.id} />
                        {contextType === 'notebook' && notebookId && evaluationMessageIdSet.has(message.id) && (
                          <EvidenceReview
                            notebookId={notebookId}
                            messageId={message.id}
                            evaluation={messageEvaluations.data
                              ? (messageEvaluations.data[message.id] ?? null)
                              : undefined}
                            batchLoading={messageEvaluations.isLoading}
                            batchError={messageEvaluations.isError}
                          />
                        )}
                      </div>
                    )}
                  </div>
                  {message.type === 'human' && (
                    <div className="flex-shrink-0">
                      <div className="h-8 w-8 rounded-full bg-primary flex items-center justify-center">
                        <User className="h-4 w-4 text-primary-foreground" />
                      </div>
                    </div>
                  )}
                </div>
              ))
            )}
            {isStreaming && (
              <div className="flex gap-3 justify-start">
                <div className="flex-shrink-0">
                  <div className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center">
                    <Bot className="h-4 w-4" />
                  </div>
                </div>
                {/* v0.8.70 — a "typing" dot wave reads more alive than a
                    spinner; the global reduced-motion rule freezes it. */}
                <div className="rounded-2xl border border-border/60 bg-card px-4 py-3 shadow-sm">
                  <div className="flex gap-1">
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary/70 [animation-delay:-0.3s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary/70 [animation-delay:-0.15s]" />
                    <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary/70" />
                  </div>
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
        </ScrollArea>

        {/* Context Indicators */}
        {contextIndicators && (
          <div className="border-t px-4 py-2">
            <div className="flex flex-wrap gap-2 text-xs">
              {contextIndicators.sources?.length > 0 && (
                <Badge variant="outline" className="gap-1">
                  <FileText className="h-3 w-3" />
                  {contextIndicators.sources.length} {t('navigation.sources')}
                </Badge>
              )}
              {contextIndicators.insights?.length > 0 && (
                <Badge variant="outline" className="gap-1">
                  <Lightbulb className="h-3 w-3" />
                  {contextIndicators.insights.length} {contextIndicators.insights.length === 1 ? t('common.insight') : t('common.insights')}
                </Badge>
              )}
              {contextIndicators.notes?.length > 0 && (
                <Badge variant="outline" className="gap-1">
                  <StickyNote className="h-3 w-3" />
                  {contextIndicators.notes.length} {contextIndicators.notes.length === 1 ? t('common.note') : t('common.notes')}
                </Badge>
              )}
            </div>
          </div>
        )}

        {/* Notebook Context Indicator */}
        {notebookContextStats && (
          <ContextIndicator
            sourcesInsights={notebookContextStats.sourcesInsights}
            sourcesFull={notebookContextStats.sourcesFull}
            notesCount={notebookContextStats.notesCount}
            tokenCount={notebookContextStats.tokenCount}
            charCount={notebookContextStats.charCount}
            totalSources={notebookContextStats.totalSources}
            contextSourceTitles={notebookContextStats.contextSourceTitles}
          />
        )}

        {/* Input Area */}
        <div className="flex-shrink-0 p-4 space-y-3 border-t">
          {mindMapContext && (
            <div data-testid="mind-map-context-chip" className="flex items-start justify-between gap-2 rounded-md border bg-muted/40 px-3 py-2 text-xs">
              <div className="min-w-0">
                <div className="font-medium">Mind-map context: {mindMapContext.label}</div>
                <div className="mt-0.5 break-words text-muted-foreground">
                  {mindMapContext.relationship || 'No relationship specified'} · {mindMapContext.citations.join(' ') || 'No citations'} · artifact {mindMapContext.artifact_id}
                </div>
              </div>
              <Button type="button" variant="ghost" size="icon" className="h-6 w-6 shrink-0" aria-label="Remove mind-map context" onClick={() => setMindMapContext(null)}>
                <X className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
          {/* Model selector + v0.8.46 MCP tool picker on one row.
              The picker self-hides when there are no enabled MCP
              servers, so the row collapses to just the model selector
              for users without MCP configured. */}
          {(onModelChange || onToggleMcpServer || onToggleDebateMode) && (
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">{t('chat.model')}</span>
                {onModelChange && (
                  <ModelSelector
                    currentModel={modelOverride}
                    onModelChange={onModelChange}
                    disabled={isStreaming}
                  />
                )}
              </div>
              <div className="flex items-center gap-2">
                {/* v0.8.97 — Debate mode: argue the other side from the sources. */}
                {onToggleDebateMode && (
                  <Button
                    type="button"
                    variant={debateMode ? 'secondary' : 'ghost'}
                    size="sm"
                    aria-pressed={debateMode}
                    aria-label={debateMode ? 'Leave Debate mode' : 'Enter Debate mode'}
                    title="Debate mode — the assistant argues the opposing case, grounded in your sources"
                    onClick={onToggleDebateMode}
                    className="h-7 gap-1.5 px-2 text-xs"
                    data-testid="debate-mode-toggle"
                  >
                    <Swords className="h-3.5 w-3.5" aria-hidden="true" />
                    Debate
                  </Button>
                )}
                {onToggleMcpServer && (
                  <McpToolPicker
                    disabled={disabledMcpServers ?? []}
                    onToggle={onToggleMcpServer}
                  />
                )}
              </div>
            </div>
          )}

          <div className="flex gap-2 items-end min-w-0">
            <Textarea
              id={chatInputId}
              ref={textareaRef}
              name="chat-message"
              autoComplete="off"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={`${t('chat.sendPlaceholder')} (${t('chat.pressToSend').replace('{key}', keyHint)})`}
              disabled={isStreaming}
              className="flex-1 min-h-[40px] max-h-[100px] resize-none py-2 px-3 min-w-0"
              rows={1}
            />
            <AudioDictateButton
              onTranscribed={(text) => setInput((prev) => (prev ? `${prev} ${text}` : text))}
              disabled={isStreaming}
              className="h-[40px] w-[40px] flex-shrink-0"
            />
            {isStreaming && onCancelStreaming && (
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label="Stop generating"
                title="Stop generating"
                onClick={onCancelStreaming}
                className="h-[40px] w-[40px] flex-shrink-0"
              >
                <Square className="h-4 w-4" />
              </Button>
            )}
            <Button
              onClick={handleSend}
              disabled={!input.trim() || isStreaming}
              size="icon"
              aria-label={t('chat.send', { defaultValue: 'Send message' })}
              className="h-[40px] w-[40px] flex-shrink-0"
            >
              {isStreaming ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          </div>
        </div>
      </CardContent>
    </Card>

      {/* v0.8.79 — citation jump-to-highlight: a source viewer opened from a
          [source:ID] citation, scrolled to + highlighting the cited passage.
          Kept local (not the global modal) so it can carry the highlight query. */}
      <SourceDialog
        open={!!citationSource}
        onOpenChange={(o) => {
          if (!o) setCitationSource(null)
        }}
        sourceId={citationSource?.id ?? null}
        highlightQuery={citationSource?.query}
      />

    </>
  )
}

// Helper component to render AI messages with clickable references
// v0.8.0 Phase 4 Task 14 — split on citation markers before markdown rendering.
// [mcp:N] markers are rendered as CitationPill components inline; [source/note/insight:ID]
// markers within text segments are handled by the existing compact-reference system.
// v0.8.1 Item 3 — messageId passed through to CitationPill so the MCP
// popover can look up tool-call payloads from the TanStack Query cache.
// v0.8.79 — the "citing sentence" for a citation is the last sentence of the
// text immediately preceding the marker — i.e. the claim the citation grounds.
// Used as the query to locate + highlight the cited passage in the source.
function lastSentence(text: string): string {
  const trimmed = (text || '').trim()
  if (!trimmed) return ''
  const parts = trimmed.split(/(?<=[.!?])\s+/)
  return (parts[parts.length - 1] || trimmed).slice(-400)
}

interface ParsedContent {
  thinking: string | null
  isThinkingActive: boolean
  answer: string
}

function parseThinking(rawContent: string): ParsedContent {
  const thinkStart = rawContent.indexOf('<think>')
  if (thinkStart === -1) {
    return { thinking: null, isThinkingActive: false, answer: rawContent }
  }

  const afterStart = rawContent.slice(thinkStart + 7)
  const thinkEnd = afterStart.indexOf('</think>')

  if (thinkEnd === -1) {
    // Currently still thinking (streaming inside <think>)
    const thinking = afterStart.trim()
    const answer = rawContent.slice(0, thinkStart).trim()
    return { thinking, isThinkingActive: true, answer }
  }

  const thinking = afterStart.slice(0, thinkEnd).trim()
  const answer = (rawContent.slice(0, thinkStart) + afterStart.slice(thinkEnd + 8)).trim()
  return { thinking, isThinkingActive: false, answer }
}

function ThoughtAccordion({
  thinking,
  isThinkingActive,
}: {
  thinking: string
  isThinkingActive: boolean
}) {
  const [isOpen, setIsOpen] = useState(isThinkingActive)

  const wasActiveRef = useRef(isThinkingActive)
  useEffect(() => {
    if (wasActiveRef.current && !isThinkingActive) {
      setIsOpen(false)
    }
    wasActiveRef.current = isThinkingActive
  }, [isThinkingActive])

  if (!thinking) return null

  return (
    <div className="not-prose mb-3 overflow-hidden rounded-xl border border-primary/20 bg-primary/[0.03] transition-all duration-200">
      <button
        type="button"
        onClick={() => setIsOpen(!isOpen)}
        className="flex w-full cursor-pointer items-center justify-between px-3.5 py-2 text-left text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
        aria-expanded={isOpen}
      >
        <div className="flex items-center gap-2">
          <Sparkles className={cn('h-3.5 w-3.5 transition-colors', isThinkingActive ? 'text-primary animate-pulse' : 'text-muted-foreground')} />
          <span className="font-mono text-[11px] uppercase tracking-wider">
            {isThinkingActive ? 'Thinking…' : 'Thought process'}
          </span>
          {isThinkingActive && (
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
            </span>
          )}
        </div>
        <ChevronDown
          className={cn(
            'h-3.5 w-3.5 text-muted-foreground transition-transform duration-200',
            isOpen && 'rotate-180'
          )}
        />
      </button>
      {isOpen && (
        <div className="max-h-72 overflow-y-auto border-t border-primary/10 bg-muted/20 px-3.5 py-2.5 font-mono text-xs leading-relaxed text-muted-foreground/90 whitespace-pre-wrap selection:bg-primary/20">
          {thinking}
        </div>
      )}
    </div>
  )
}

function AIMessageContent({
  content,
  onReferenceClick,
  messageId,
  onViewSource,
}: {
  content: string
  onReferenceClick: (type: string, id: string) => void
  messageId?: string
  // v0.8.79 — open the source reading view for a [source:ID] citation,
  // passing the citing sentence so it can highlight the grounded passage.
  onViewSource?: (sourceId: string, query: string) => void
}) {
  const { t } = useTranslation()
  const { thinking, isThinkingActive, answer } = parseThinking(content)

  // Create custom link component for compact references
  const LinkComponent = createCompactReferenceLinkComponent(onReferenceClick)

  // Shared ReactMarkdown component overrides — reused per text segment.
  const mdComponents = {
    a: LinkComponent,
    p: ({ children }: { children?: React.ReactNode }) => <p className="mb-4">{children}</p>,
    h1: ({ children }: { children?: React.ReactNode }) => <h1 className="mb-4 mt-6">{children}</h1>,
    h2: ({ children }: { children?: React.ReactNode }) => <h2 className="mb-3 mt-5">{children}</h2>,
    h3: ({ children }: { children?: React.ReactNode }) => <h3 className="mb-3 mt-4">{children}</h3>,
    h4: ({ children }: { children?: React.ReactNode }) => <h4 className="mb-2 mt-4">{children}</h4>,
    h5: ({ children }: { children?: React.ReactNode }) => <h5 className="mb-2 mt-3">{children}</h5>,
    h6: ({ children }: { children?: React.ReactNode }) => <h6 className="mb-2 mt-3">{children}</h6>,
    li: ({ children }: { children?: React.ReactNode }) => <li className="mb-1">{children}</li>,
    ul: ({ children }: { children?: React.ReactNode }) => <ul className="mb-4 space-y-1">{children}</ul>,
    ol: ({ children }: { children?: React.ReactNode }) => <ol className="mb-4 space-y-1">{children}</ol>,
    table: ({ children }: { children?: React.ReactNode }) => (
      <div className="my-4 overflow-x-auto">
        <table className="min-w-full border-collapse border border-border">{children}</table>
      </div>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => <thead className="bg-muted">{children}</thead>,
    tbody: ({ children }: { children?: React.ReactNode }) => <tbody>{children}</tbody>,
    tr: ({ children }: { children?: React.ReactNode }) => <tr className="border-b border-border">{children}</tr>,
    th: ({ children }: { children?: React.ReactNode }) => <th className="border border-border px-3 py-2 text-left font-semibold">{children}</th>,
    td: ({ children }: { children?: React.ReactNode }) => <td className="border border-border px-3 py-2">{children}</td>,
    pre: ({ children }: { children?: React.ReactNode }) => (
      <div className="my-3 overflow-hidden rounded-lg border bg-muted/40 font-mono text-xs shadow-xs">
        <pre className="overflow-x-auto p-3.5 leading-relaxed">{children}</pre>
      </div>
    ),
    code: ({ className, children, ...props }: any) => {
      const isInline = !className && typeof children === 'string' && !children.includes('\n')
      if (isInline) {
        return (
          <code className="rounded bg-muted/70 px-1.5 py-0.5 font-mono text-xs font-medium text-foreground" {...props}>
            {children}
          </code>
        )
      }
      return (
        <code className={className} {...props}>
          {children}
        </code>
      )
    },
  }

  // Split the answer on ALL citation markers ([mcp:N], [source:ID], etc.).
  // Text segments are rendered via ReactMarkdown (with compact-reference conversion).
  // Citation segments are rendered as CitationPill components inline.
  const segments = splitCitations(answer)

  // v0.7.25 — was `prose-a:text-blue-600 prose-a:break-all`. The
  // hardcoded blue-600 fails WCAG AA against the dark muted
  // background in dark themes (~3.2:1), and break-all hyphenates
  // URLs mid-character. Theme-aware token + break-words.
  return (
    <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none break-words prose-headings:font-semibold prose-a:text-primary dark:prose-a:text-blue-400 prose-a:underline prose-a:break-words prose-p:mb-4 prose-p:leading-7 prose-li:mb-2">
      {thinking && (
        <ThoughtAccordion thinking={thinking} isThinkingActive={isThinkingActive} />
      )}
      {segments.map((seg, idx) => {
        if (seg.kind === 'text') {
          // Pass text segments through the existing compact-reference pipeline.
          const markdownWithCompactRefs = convertReferencesToCompactMarkdown(
            seg.value,
            t('common.references')
          )
          return (
            <ReactMarkdown
              key={idx}
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex]}
              components={mdComponents}
            >
              {markdownWithCompactRefs}
            </ReactMarkdown>
          )
        }
        // Citation segment → render as an inline pill.
        // v0.8.1 Item 3 — pass messageId so MCP pills can look up
        // tool-call payloads from the TanStack Query cache.
        // v0.8.79 — for source citations, wire "View source" to open the
        // reading view highlighting the cited passage (citing sentence = the
        // last sentence of the preceding text segment).
        const prev = idx > 0 ? segments[idx - 1] : undefined
        const citingSentence = prev && prev.kind === 'text' ? lastSentence(prev.value) : ''
        return (
          <CitationPill
            key={`${seg.kind}-${idx}`}
            kind={seg.kind}
            value={seg.value}
            messageId={messageId}
            onViewSource={
              seg.kind === 'source' && onViewSource
                ? () => onViewSource(`source:${seg.value}`, citingSentence)
                : undefined
            }
          />
        )
      })}
    </div>
  )
}
