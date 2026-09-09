'use client'

import { useState, useCallback, useRef, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import {
  isStreamStallError,
  readWithIdleTimeout,
  resolveStreamIdleTimeoutMs,
} from '@/lib/utils/stream-stall'
import { useTranslation } from '@/lib/hooks/use-translation'
import { sourceChatApi } from '@/lib/api/source-chat'
import { pruneMessageScopedQueries } from '@/lib/api/query-client'
import {
  SourceChatSession,
  SourceChatMessage,
  SourceChatContextIndicator,
  CreateSourceChatSessionRequest,
  UpdateSourceChatSessionRequest
} from '@/lib/types/api'

export function useSourceChat(sourceId: string) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<SourceChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [contextIndicators, setContextIndicators] = useState<SourceChatContextIndicator | null>(null)
  const [pendingModelOverride, setPendingModelOverride] = useState<string | null>(null)
  // v0.8.44 — per-request MCP server disable picks for source chat
  // (parity with the notebook-chat v0.8.42 picker).
  // v0.8.44b — now session-persistent: hydrated from the session row
  // on load + PATCHed on toggle (source-chat sessions share the
  // `chat_session` table, so migration 20 already provisions the
  // `disabled_mcp_servers` column — no new migration needed).
  const [disabledMcpServers, setDisabledMcpServers] = useState<string[]>([])
  // v0.8.44b — ref to the (later-declared) update mutation so the
  // toggle callback can persist without a forward-const reference in
  // its deps array (JS temporal dead zone). Same pattern as the
  // notebook-chat hook's v0.8.43b fix. `.current` is assigned in a
  // useEffect placed after `updateSessionMutation` is in scope.
  const updateSessionMutationRef = useRef<{
    mutate: (args: { sessionId: string; data: UpdateSourceChatSessionRequest }) => void
  } | null>(null)
  const toggleDisabledMcpServer = useCallback((name: string) => {
    setDisabledMcpServers(prev => {
      const next = prev.includes(name)
        ? prev.filter(n => n !== name)
        : [...prev, name]
      // v0.8.44b — persist to SurrealDB (best-effort; optimistic
      // local state already updated). Requires a current session —
      // a brand-new source-chat with no session yet just keeps the
      // pick in local state until the first send creates one.
      if (currentSessionId && updateSessionMutationRef.current) {
        updateSessionMutationRef.current.mutate({
          sessionId: currentSessionId,
          data: { disabled_mcp_servers: next },
        })
      }
      return next
    })
  }, [currentSessionId])
  const abortControllerRef = useRef<AbortController | null>(null)
  // v0.8.21 — Guard against the message-sync useEffect (line 41-45)
  // clobbering optimistic / streamed messages when a refetch returns
  // during or right after a send. Race scenario this prevents:
  //   1) User sends msg #1 → stream completes → refetch starts
  //   2) User rapidly sends msg #2 → setMessages adds user_2_optimistic
  //      and (once tokens arrive) ai_2_streaming placeholder
  //   3) Refetch from step 1 returns with [user_1, ai_1] (msg #2 not
  //      yet persisted) → useEffect fires → setMessages overwrites
  //      → user_2_optimistic + ai_2_streaming WIPED from the UI
  //   4) Token deltas for msg #2 try to map onto streamingAiId_2 but
  //      that ID is no longer in local state → no-op → user sees msg
  //      #2's response only after its OWN stream-complete refetch
  // Why a counter, not a boolean: msg #1's finally{} runs while msg #2
  // is still in flight. A boolean would get cleared by msg #1's exit
  // even though msg #2 is mid-send, reopening the race. The counter
  // stays > 0 until BOTH have settled.
  // Trade-off: cross-tab edits made during a send won't appear locally
  // until the send finishes — acceptable for a single-user local-deploy
  // app.
  const inFlightSendsRef = useRef(0)

  // Fetch sessions
  const { data: sessions = [], isLoading: loadingSessions, refetch: refetchSessions } = useQuery<SourceChatSession[]>({
    queryKey: ['sourceChatSessions', sourceId],
    queryFn: () => sourceChatApi.listSessions(sourceId),
    enabled: !!sourceId
  })

  // Fetch current session with messages
  const { data: currentSession, refetch: refetchCurrentSession } = useQuery({
    queryKey: ['sourceChatSession', sourceId, currentSessionId],
    queryFn: () => sourceChatApi.getSession(sourceId, currentSessionId!),
    enabled: !!sourceId && !!currentSessionId
  })

  // Update messages when session changes
  // v0.8.21 — Skip the overwrite while a send is in flight (see the
  // isHandlingSendRef rationale above). The guard prevents the
  // refetch-after-stream from wiping a concurrent rapid second send's
  // optimistic user bubble + streaming AI placeholder.
  useEffect(() => {
    if (currentSession?.messages && inFlightSendsRef.current === 0) {
      setMessages(currentSession.messages)
    }
  }, [currentSession])

  // v0.8.66 (audit F-2) — on unmount, drop the ad-hoc per-message chat cache
  // entries (mcp tool-calls + selected-provider/privacy/agent-state badges) so
  // they don't accumulate across navigations. They only hold live-streamed data
  // for this tab and are never refetched, so dropping on unmount is safe
  // (mirrors the useNotebookChat cleanup).
  useEffect(() => {
    return () => {
      pruneMessageScopedQueries()
    }
  }, [])

  // Auto-select most recent session when sessions are loaded
  useEffect(() => {
    if (sessions.length > 0 && !currentSessionId) {
      // Find most recent session (sessions are sorted by created date desc from API)
      const mostRecentSession = sessions[0]
      setCurrentSessionId(mostRecentSession.id)
    }
  }, [sessions, currentSessionId])

  // Create session mutation
  const createSessionMutation = useMutation({
    mutationFn: (data: Omit<CreateSourceChatSessionRequest, 'source_id'>) => 
      sourceChatApi.createSession(sourceId, data),
    onSuccess: (newSession) => {
      queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      setCurrentSessionId(newSession.id)
      toast.success(t('chat.sessionCreated'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
    }
  })

  // Update session mutation
  const updateSessionMutation = useMutation({
    mutationFn: ({ sessionId, data }: { sessionId: string, data: UpdateSourceChatSessionRequest }) =>
      sourceChatApi.updateSession(sourceId, sessionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      queryClient.invalidateQueries({ queryKey: ['sourceChatSession', sourceId, currentSessionId] })
      toast.success(t('chat.sessionUpdated'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToUpdateSession'))
    }
  })

  // v0.8.44b — keep the toggle callback's ref pointed at the live
  // mutation (see updateSessionMutationRef rationale near its decl).
  useEffect(() => {
    updateSessionMutationRef.current = updateSessionMutation
  }, [updateSessionMutation])

  // v0.8.44b — hydrate the source-chat MCP picks from the session row
  // on load / switch. Gated on `!updateSessionMutation.isPending` so
  // an in-flight PATCH (triggered by toggling) doesn't get clobbered
  // by the refetch it itself triggers — identical race + fix as the
  // notebook-chat hook's v0.8.43b hydration guard.
  useEffect(() => {
    if (currentSession && !updateSessionMutation.isPending) {
      setDisabledMcpServers(currentSession.disabled_mcp_servers ?? [])
    }
  }, [
    currentSessionId,
    currentSession?.id,
    currentSession?.disabled_mcp_servers,
    updateSessionMutation.isPending,
    currentSession,
  ])

  // Delete session mutation
  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) => 
      sourceChatApi.deleteSession(sourceId, sessionId),
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      if (currentSessionId === deletedId) {
        setCurrentSessionId(null)
        setMessages([])
      }
      toast.success(t('chat.sessionDeleted'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToDeleteSession'))
    }
  })

  // Set model override - handles both existing sessions and pending state
  const setModelOverride = useCallback((model: string | null) => {
    if (currentSessionId) {
      updateSessionMutation.mutate({
        sessionId: currentSessionId,
        data: { model_override: model }
      })
    } else {
      setPendingModelOverride(model)
    }
  }, [currentSessionId, updateSessionMutation])

  // Send message with streaming
  const sendMessage = useCallback(async (message: string, modelOverride?: string) => {
    let sessionId = currentSessionId

    // Auto-create session if none exists
    if (!sessionId) {
      try {
        const defaultTitle = message.length > 30 ? `${message.substring(0, 30)}...` : message
        const newSession = await sourceChatApi.createSession(sourceId, {
          title: defaultTitle,
          model_override: modelOverride ?? pendingModelOverride ?? undefined
        })
        sessionId = newSession.id
        setCurrentSessionId(sessionId)
        setPendingModelOverride(null)
        queryClient.invalidateQueries({ queryKey: ['sourceChatSessions', sourceId] })
      } catch (err: unknown) {
        const error = err as { response?: { data?: { detail?: string } }, message?: string };
        console.error('Failed to create chat session:', error)
        toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
        return
      }
    }

    // v0.7.49 — unique per-send temp id (was `temp-${Date.now()}` which
    // collides on rapid-fire sends and triggered the prefix-filter bug
    // below). Mirrors the v0.7.26 fix on useNotebookChat.
    const tempId = `temp-${(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)}`
    const userMessage: SourceChatMessage = {
      id: tempId,
      type: 'human',
      content: message,
      timestamp: new Date().toISOString()
    }
    setMessages(prev => [...prev, userMessage])
    setIsStreaming(true)
    // v0.8.21 — Increment the in-flight counter BEFORE any await so the
    // useEffect's overwrite path is closed for the full duration of
    // THIS send (including the trailing refetchCurrentSession in
    // finally{}). Counter is decremented in finally{}; the counter
    // pattern means msg #1's finally{} running while msg #2 is mid-flight
    // doesn't clobber msg #2's optimistic state (counter still >0).
    inFlightSendsRef.current += 1

    // v0.6.32 — actually wire the AbortController. The previous version
    // declared abortControllerRef but NEVER assigned to .current, so
    // cancelStreaming() was a no-op AND the unmount path leaked the
    // stream reader + setState'd on a dead component.
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller

    // v0.7.49 — stable AI streaming id captured in the closure so the
    // map-update callbacks don't need to read the mutating `aiMessage`
    // object. We also no longer mutate the in-state message in place.
    const streamingAiId = `streaming-${(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)}`
    let aiCreated = false
    let aiAccumulated = ''

    // v0.8.70 — batch streamed deltas with requestAnimationFrame (≤1 render per
    // paint frame) instead of a setMessages() per delta. `aiAccumulated` is the
    // source of truth; the flush just syncs the placeholder message to it.
    // Mirrors the useNotebookChat token-batching fix.
    let streamRafId: number | null = null
    const flushStream = () => {
      streamRafId = null
      if (!aiCreated) return
      setMessages(prev =>
        prev.map(msg =>
          msg.id === streamingAiId ? { ...msg, content: aiAccumulated } : msg,
        ),
      )
    }
    const scheduleStreamFlush = () => {
      if (streamRafId == null) streamRafId = requestAnimationFrame(flushStream)
    }

    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
    try {
      const response = await sourceChatApi.sendMessage(
        sourceId, sessionId,
        {
          message,
          model_override: modelOverride,
          // v0.8.44 — surface the source-chat MCP picks. Empty array =
          // no disables (default v0.8.0 behavior, no regression for
          // users who never touch the picker).
          disabled_mcp_servers:
            disabledMcpServers.length > 0 ? disabledMcpServers : undefined,
        },
        controller.signal,
      )

      if (!response) {
        throw new Error('No response body')
      }

      reader = response.getReader()
      const decoder = new TextDecoder()
      // v0.7.49 — multi-bug fix in this section:
      //   (a) `decoder.decode(value)` must use `{ stream: true }`
      //       so multibyte UTF-8 (CJK, emoji) split across two TCP
      //       chunks doesn't get replaced with U+FFFD. Mirrors the
      //       correct usage in chat.ts:129 and use-ask.ts:106.
      //   (b) lines need a buffer kept across reads — a `data: …\n\n`
      //       frame is regularly split across two TCP chunks, and
      //       `text.split('\n')` without buffering silently drops
      //       events. We now `buffer.split('\n')` with `buffer = lines.pop()`
      //       to carry the trailing partial line forward.
      //   (c) bounded buffer so a pathological stream that never emits
      //       a newline can't grow unbounded.
      let buffer = ''
      const BUFFER_MAX = 4 * 1024 * 1024
      // v0.8.115 — idle-timeout guard (see stream-stall.ts).
      const idleTimeoutMs = resolveStreamIdleTimeoutMs()

      while (true) {
        const { done, value } = await readWithIdleTimeout(reader, idleTimeoutMs)
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        if (buffer.length > BUFFER_MAX) {
          throw new Error('source-chat stream buffer exceeded 4 MiB')
        }

        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''  // keep partial last line for next read

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))

              if (data.type === 'ai_message_delta') {
                // v0.7.42/v0.7.49 — append delta. No object mutation;
                // closure variables only.
                aiAccumulated += data.content || ''
                if (!aiCreated) {
                  const initial: SourceChatMessage = {
                    id: streamingAiId,
                    type: 'ai',
                    content: aiAccumulated,
                    timestamp: new Date().toISOString(),
                  }
                  setMessages(prev => [...prev, initial])
                  aiCreated = true
                } else {
                  // v0.8.70 — batch (rAF) instead of a setMessages per delta.
                  scheduleStreamFlush()
                }
              } else if (data.type === 'ai_message') {
                // Terminal canonical message — replaces accumulator.
                aiAccumulated = data.content || aiAccumulated
                if (!aiCreated) {
                  const initial: SourceChatMessage = {
                    id: streamingAiId,
                    type: 'ai',
                    content: aiAccumulated,
                    timestamp: new Date().toISOString(),
                  }
                  setMessages(prev => [...prev, initial])
                  aiCreated = true
                } else {
                  setMessages(prev =>
                    prev.map(msg => msg.id === streamingAiId
                      ? { ...msg, content: aiAccumulated }
                      : msg
                    )
                  )
                }
              } else if (data.type === 'context_indicators') {
                setContextIndicators(data.data)
              } else if (data.type === 'mcp_tool_calls') {
                // v0.8.17 — mirrors useNotebookChat's handling. Stash
                // the MCP tool-call payloads in the TanStack Query
                // cache keyed by streamingAiId so CitationPill's
                // McpPopoverContent can look them up by messageId.
                // Pre-v0.8.17 v0.8.16 wired the chat graph + SSE
                // event but no client handler stashed the payloads,
                // so source-chat pills always showed the v0.8.10
                // placeholder fallback.
                if (Array.isArray(data.calls) && data.calls.length > 0) {
                  queryClient.setQueryData(
                    ['mcp', 'tool-calls', streamingAiId],
                    data.calls,
                  )
                }
              } else if (data.type === 'selected_provider') {
                // v0.8.68 — smart-router decision for the local/cloud badge.
                // MERGE into the cache entry (the offline_fallback event may
                // land on the same key in the same stream).
                queryClient.setQueryData(
                  ['chat', 'selected-provider', streamingAiId],
                  (old: Record<string, unknown> | undefined) => ({
                    ...(old ?? {}),
                    selected_provider: data.selected_provider ?? null,
                    selected_model_id: data.selected_model_id ?? null,
                  }),
                )
              } else if (data.type === 'offline_fallback') {
                // v0.8.68 — the offline gate answered this turn with a
                // local model. Stash under the same cache key
                // ChatMessageProviderBadge reads (keyed by the streamed
                // message id, which is what ChatPanel renders) so the
                // amber "Answered with <model> (offline)" pill shows in
                // source chat exactly like notebook chat. Merge so a
                // selected_provider event on the same turn isn't lost.
                if (data.data) {
                  queryClient.setQueryData(
                    ['chat', 'selected-provider', streamingAiId],
                    (old: Record<string, unknown> | undefined) => ({
                      selected_provider: null,
                      selected_model_id: null,
                      ...(old ?? {}),
                      offline_fallback: data.data,
                    }),
                  )
                }
              } else if (data.type === 'error') {
                throw new Error(data.message || 'Stream error')
              }
            } catch (e) {
              if (e instanceof SyntaxError) {
                console.error('Error parsing SSE data:', e)
              } else {
                throw e
              }
            }
          }
        }
      }

      // v0.8.70 — drain any delta still buffered when the stream closed.
      if (streamRafId != null) {
        cancelAnimationFrame(streamRafId)
        streamRafId = null
      }
      flushStream()
    } catch (err: unknown) {
      // AbortError = user clicked Stop OR component unmounted; silent.
      if ((err as { name?: string }).name === 'AbortError') {
        // v0.7.49 — drop ONLY this send's optimistic message + streamed
        // AI placeholder. Was `!msg.id.startsWith('temp-')` which wiped
        // EVERY temp-prefixed message — including the NEW send's
        // optimistic message when a prior in-flight send was aborted
        // by the new send (line 138). The new send would see its own
        // message disappear seconds after submitting. Mirrors v0.7.26
        // fix on useNotebookChat.
        setMessages(prev =>
          prev.filter(msg => msg.id !== tempId && msg.id !== streamingAiId)
        )
        return
      }
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      console.error('Error sending message:', error)
      if (isStreamStallError(err)) {
        // v0.8.115 — stalled stream (dead local daemon / sleep-wake socket).
        // Same cleanup as any other failure, but copy that names the cause.
        toast.error(t('apiErrors.streamStalled'), {
          description: t('apiErrors.streamStalledHint', { seconds: err.idleSeconds }),
        })
      } else {
        toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToSendMessage'))
      }
      // v0.7.54 — same fix as the AbortError branch above: filter ONLY
      // THIS send's tempId + streamingAiId. The old `startsWith('temp-')`
      // also wiped a concurrent retry's optimistic message because the
      // prefix is shared across sends. Mirrors the v0.7.49 fix on the
      // success-cancel path.
      setMessages(prev =>
        prev.filter(msg => msg.id !== tempId && msg.id !== streamingAiId)
      )
    } finally {
      setIsStreaming(false)
      // v0.7.54 — cancel the reader BEFORE releasing the lock so the
      // underlying HTTP response body is actually torn down. Just
      // releaseLock() leaves the connection open until GC and FastAPI's
      // `is_disconnected()` doesn't fire — the local LLM keeps
      // generating tokens nobody will see. Mirrors v0.7.50 chat.ts.
      if (reader) {
        try {
          await reader.cancel()
        } catch {
          // cancel can throw if the stream is already errored; ignore.
        }
        try {
          reader.releaseLock()
        } catch {
          // Reader may already be released by abort/cancel path; ignore.
        }
      }
      // Clear the controller ref if it's still ours.
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
      // Refetch session to get persisted messages.
      // v0.8.21 — Await the refetch BEFORE decrementing the counter so
      // the useEffect fired by the resulting currentSession change
      // still sees inFlightSendsRef.current >= 1 and skips the
      // overwrite. Without `await`, the counter could drop to 0 before
      // the refetch settles, reopening the race for the very query
      // whose return we're trying to filter out.
      try {
        const { data: newSession } = await refetchCurrentSession()
        if (newSession?.messages) {
          const lastAiMsg = [...newSession.messages].reverse().find(m => m.type === 'ai')
          if (lastAiMsg) {
            const cachedCalls = queryClient.getQueryData(['mcp', 'tool-calls', streamingAiId])
            if (cachedCalls) {
              queryClient.setQueryData(
                ['mcp', 'tool-calls', lastAiMsg.id],
                cachedCalls,
              )
            }
          }
        }
      } finally {
        inFlightSendsRef.current = Math.max(
          0, inFlightSendsRef.current - 1,
        )
      }
    }
  }, [
    sourceId, currentSessionId, refetchCurrentSession, queryClient, t,
    // v0.8.46b — same stale-closure fix as the notebook hook:
    // `sendMessage` reads `disabledMcpServers` (v0.8.44) for the
    // per-turn MCP disable list, so it must be a dependency or a
    // toggle-then-send captures a stale list.
    disabledMcpServers,
    pendingModelOverride,
  ]) // v0.6.32 — abort the in-flight controller on unmount.
  useEffect(() => () => {
    abortControllerRef.current?.abort()
    abortControllerRef.current = null
  }, [])

  // Cancel streaming
  const cancelStreaming = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsStreaming(false)
    }
  }, [])

  // Switch session
  const switchSession = useCallback((sessionId: string) => {
    setCurrentSessionId(sessionId)
    setContextIndicators(null)
    pruneMessageScopedQueries()
  }, [])

  // Create session
  const createSession = useCallback((data: Omit<CreateSourceChatSessionRequest, 'source_id'>) => {
    return createSessionMutation.mutate(data)
  }, [createSessionMutation])

  // Update session
  const updateSession = useCallback((sessionId: string, data: UpdateSourceChatSessionRequest) => {
    return updateSessionMutation.mutate({ sessionId, data })
  }, [updateSessionMutation])

  // Delete session
  const deleteSession = useCallback((sessionId: string) => {
    return deleteSessionMutation.mutate(sessionId)
  }, [deleteSessionMutation])

  return {
    // State
    sessions,
    currentSession: sessions.find(s => s.id === currentSessionId),
    currentSessionId,
    messages,
    isStreaming,
    contextIndicators,
    loadingSessions,
    pendingModelOverride,
    
    // Actions
    createSession,
    updateSession,
    deleteSession,
    switchSession,
    sendMessage,
    cancelStreaming,
    refetchSessions,
    setModelOverride,
    // v0.8.44 — per-request MCP server disable picks for source chat.
    // Mirror of the notebook-chat v0.8.42 exposure. UI passes
    // `disabledMcpServers` as the `disabled` prop and
    // `toggleDisabledMcpServer` as `onToggle` to the shared
    // `<McpToolPicker>` component.
    disabledMcpServers,
    toggleDisabledMcpServer,
  }
}
