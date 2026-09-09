'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { isStreamStallError } from '@/lib/utils/stream-stall'
import { useTranslation } from '@/lib/hooks/use-translation'
import { markEvaluationPersistencePending } from '@/lib/hooks/use-evaluation'
import { chatApi } from '@/lib/api/chat'
import { QUERY_KEYS, pruneMessageScopedQueries } from '@/lib/api/query-client'
import {
  NotebookChatMessage,
  CreateNotebookChatSessionRequest,
  UpdateNotebookChatSessionRequest,
  SourceListResponse,
  NoteResponse
} from '@/lib/types/api'
import { ContextSelections } from '@/app/(dashboard)/notebooks/[id]/page'

interface UseNotebookChatParams {
  notebookId: string
  sources: SourceListResponse[]
  notes: NoteResponse[]
  contextSelections: ContextSelections
  contextCountsEnabled: boolean
}

export function useNotebookChat({
  notebookId,
  sources,
  notes,
  contextSelections,
  contextCountsEnabled,
}: UseNotebookChatParams) {
  const { t } = useTranslation()
  const queryClient = useQueryClient()
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<NotebookChatMessage[]>([])
  const [isSending, setIsSending] = useState(false)
  const [tokenCount, setTokenCount] = useState<number>(0)
  const [charCount, setCharCount] = useState<number>(0)
  // Pending model override for when user changes model before a session exists
  const [pendingModelOverride, setPendingModelOverride] = useState<string | null>(null)
  // v0.8.42 — per-conversation MCP server disable picks. Reset implicitly
  // when the user switches session (we keep it simple: state is hook-
  // local, not persisted to the chat session row). UI surfaces a small
  // tool picker above the message input; setters expose Add/Remove
  // semantics so consumers don't have to immutably-clone the array.
  const [disabledMcpServers, setDisabledMcpServers] = useState<string[]>([])
  // v0.8.97 — Debate mode toggle. Hook-local like the MCP picks: flips per
  // conversation without persisting to the session row, and applies to every
  // turn sent while on ('debate' swaps the backend system prompt for the
  // source-grounded opposition template).
  const [debateMode, setDebateMode] = useState(false)
  // v0.8.43b — ref-based access to `updateSessionMutation` so the
  // useCallback can reference it WITHOUT needing it in the deps
  // array (the mutation object is hoisted later in this function
  // body — JS temporal dead zone makes the deps-array fix
  // syntactically impossible without a forward decl). Pattern
  // matches how `abortControllerRef` etc. are used elsewhere in
  // this hook to dodge the same circular-dep problem.
  //
  // The ref's `.current` is assigned in a useEffect below (after
  // `updateSessionMutation` is in scope). The callback dereferences
  // at call time, so stale-closure is impossible: by the time the
  // user clicks, the ref points at the live mutation object.
  const updateSessionMutationRef = useRef<{
    mutate: (args: { sessionId: string; data: UpdateNotebookChatSessionRequest }) => void
  } | null>(null)
  const toggleDisabledMcpServer = useCallback((name: string) => {
    setDisabledMcpServers(prev => {
      const next = prev.includes(name)
        ? prev.filter(n => n !== name)
        : [...prev, name]
      // v0.8.43 — persist to SurrealDB so the picks survive page
      // reload + session navigation. Best-effort: a failed PATCH
      // logs (via the existing toast surface on updateSession) but
      // doesn't block the local UI toggle — the optimistic state
      // already updated above.
      if (currentSessionId && updateSessionMutationRef.current) {
        updateSessionMutationRef.current.mutate({
          sessionId: currentSessionId,
          data: { disabled_mcp_servers: next },
        })
      }
      return next
    })
  }, [currentSessionId])

  // v0.7.50 — AbortController for the v0.7.38 streaming send. Was
  // missing — useSourceChat / use-ask both wire one and the streaming
  // path's resource-leak class of bugs (LLM keeps generating after the
  // user navigates away, setState on a dead component) was reintroduced
  // when v0.7.38 added streaming for notebook chat. Mirrors the v0.6.32
  // useSourceChat pattern. mountedRef is declared later (v0.6.24 used
  // it for a separate race guard); we extend its existing cleanup to
  // also abort the streaming controller.
  const abortControllerRef = useRef<AbortController | null>(null)
  // v0.8.21 — See the matching ref in useSourceChat for full rationale.
  // TL;DR: blocks the message-sync useEffect from clobbering optimistic
  // state when a refetch returns mid-second-send. Notebook chat already
  // applies canonical messages via the `done` event's `setMessages(
  // canonicalMessages)`, so the subsequent refetch's setMessages call
  // here is redundant in the happy path and harmful during rapid sends.
  // Counter (not boolean) so msg #1's finally{} running mid-send #2
  // doesn't release the guard while msg #2 is still in flight.
  const inFlightSendsRef = useRef(0)

  // Fetch sessions for this notebook
  const {
    data: sessions = [],
    isLoading: loadingSessions,
    refetch: refetchSessions
  } = useQuery({
    queryKey: QUERY_KEYS.notebookChatSessions(notebookId),
    queryFn: () => chatApi.listSessions(notebookId),
    enabled: !!notebookId
  })

  // Fetch current session with messages
  const {
    data: currentSession,
    refetch: refetchCurrentSession
  } = useQuery({
    queryKey: QUERY_KEYS.notebookChatSession(currentSessionId!),
    queryFn: () => chatApi.getSession(currentSessionId!),
    enabled: !!notebookId && !!currentSessionId
  })

  // Update messages when current session changes
  // v0.8.21 — Skip overwrite while a send is in flight (see
  // isHandlingSendRef rationale above). Prevents a refetch from
  // wiping a concurrent rapid second send's optimistic state.
  useEffect(() => {
    if (currentSession?.messages && inFlightSendsRef.current === 0) {
      setMessages(currentSession.messages)
    }
  }, [currentSession])

  // v0.8.43 hydration effect was here; v0.8.43b moved it BELOW
  // `updateSessionMutation` to honor the JS temporal-dead-zone rule
  // (the effect's deps array references `updateSessionMutation.isPending`).

  // Auto-select most recent session when sessions are loaded
  useEffect(() => {
    if (sessions.length > 0 && !currentSessionId) {
      // Sessions are sorted by created date desc from API
      const mostRecentSession = sessions[0]
      setCurrentSessionId(mostRecentSession.id)
    }
  }, [sessions, currentSessionId])

  // Create session mutation
  const createSessionMutation = useMutation({
    mutationFn: (data: CreateNotebookChatSessionRequest) =>
      chatApi.createSession(data),
    onSuccess: (newSession) => {
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.notebookChatSessions(notebookId)
      })
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
    mutationFn: ({ sessionId, data }: {
      sessionId: string
      data: UpdateNotebookChatSessionRequest
    }) => chatApi.updateSession(sessionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.notebookChatSessions(notebookId)
      })
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.notebookChatSession(currentSessionId!)
      })
      toast.success(t('chat.sessionUpdated'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToUpdateSession'))
    }
  })

  // v0.8.43b — Keep `updateSessionMutationRef.current` in sync with
  // the live mutation object on every render. The
  // `toggleDisabledMcpServer` callback (declared earlier — JS
  // temporal-dead-zone forbids forward `const` references in its
  // deps array) reads through this ref so the call always hits the
  // current mutation, not a stale closure capture. Pattern matches
  // the `abortControllerRef` / `inFlightSendsRef` usage elsewhere in
  // this hook.
  useEffect(() => {
    updateSessionMutationRef.current = updateSessionMutation
  }, [updateSessionMutation])

  // v0.8.43 — Hydrate `disabledMcpServers` from the session's
  // persisted picks when a session is loaded or switched into. The
  // toggle path then writes any picks made this session back to
  // SurrealDB via PATCH. Initial load → state matches server; per-turn
  // changes → state diverges → next PATCH sync keeps server in sync.
  //
  // v0.8.43b — Gate the hydration on `updateSessionMutation.isPending`
  // so the in-flight PATCH triggered by `toggleDisabledMcpServer`
  // doesn't clobber the optimistic local state when the session
  // refetch returns. Without this guard, a rapid double-click could
  // lose the user's second toggle to a stale-data race:
  //   1. toggle off → setDisabledMcpServers([X])
  //   2. PATCH fires → mutation.onSuccess invalidates session query
  //   3. session refetches with disabled_mcp_servers=[X]
  //   4. user toggles on while #3 in flight → setDisabledMcpServers([])
  //   5. refetch lands → useEffect overwrites with [X] from server
  //   ⇒ user's second click lost.
  // Skipping hydration while ANY PATCH is in flight keeps the
  // optimistic state visible until the user's last write lands.
  // Declared AFTER `updateSessionMutation` because JS temporal-
  // dead-zone forbids const references in deps arrays defined
  // earlier than the const itself — same reason
  // `updateSessionMutationRef` exists for the toggle callback.
  useEffect(() => {
    if (currentSession && !updateSessionMutation.isPending) {
      setDisabledMcpServers(currentSession.disabled_mcp_servers ?? [])
    }
  }, [
    currentSessionId,
    currentSession?.id,
    // v0.8.43b — react to changes in the persisted field, not just
    // the session-id rotation. A different tab editing the same
    // session should re-hydrate this tab on the next refetch.
    currentSession?.disabled_mcp_servers,
    updateSessionMutation.isPending,
    currentSession,
  ])

  // Delete session mutation
  const deleteSessionMutation = useMutation({
    mutationFn: (sessionId: string) =>
      chatApi.deleteSession(sessionId),
    onSuccess: (_, deletedId) => {
      queryClient.invalidateQueries({
        queryKey: QUERY_KEYS.notebookChatSessions(notebookId)
      })
      // v0.7.34 — if the user deleted the session they're currently
      // in, jump directly to the next-best session instead of leaving
      // them in a transient null state. The auto-select effect at
      // line 65 would eventually pick one anyway, but only AFTER the
      // sessions query refetches — in the meantime ChatPanel renders
      // a blank "no session" state for a frame or two, scroll resets,
      // and the user sees a jarring flicker.
      //
      // v0.7.59 — read sessions from the TanStack cache instead of the
      // outer closure. The closure captured `sessions` at the render
      // where this mutation object was created. If a second delete
      // fires before the first onSuccess runs, both closures still
      // point at the same pre-delete list and the "next" session
      // picked might already be the one being deleted by the in-flight
      // sibling mutation. The cache always reflects the latest
      // server-confirmed truth.
      if (currentSessionId === deletedId) {
        const cached = queryClient.getQueryData<typeof sessions>(
          QUERY_KEYS.notebookChatSessions(notebookId)
        )
        const list = cached ?? sessions
        const next = list.find((s) => s.id !== deletedId)
        setCurrentSessionId(next?.id ?? null)
        setMessages([])
      }
      toast.success(t('chat.sessionDeleted'))
    },
    onError: (err: unknown) => {
      const error = err as { response?: { data?: { detail?: string } }, message?: string };
      toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToDeleteSession'))
    }
  })

  // Build context from sources and notes based on user selections.
  // v0.6.24 — no longer setState-s tokenCount/charCount internally.
  // The previous version did, but that created a race when called twice
  // concurrently (e.g. user rapidly toggling source inclusion modes):
  // the LAST setState to land could be the FIRST request to start, leaving
  // counts stuck on a stale intermediate value. State updates are now the
  // caller's responsibility — see the gated effect below.
  // v0.7.191 — Stable identity for buildContext.
  //
  // Pre-fix problem (audit finding #4): `useCallback(..., [sources,
  // notes, contextSelections])` depended on ARRAY REFERENCES. TanStack
  // Query returns a fresh array on every refetch (even when the row
  // set is identical), so `sources` identity churned on every poll,
  // window-focus refetch, sibling mutation invalidation, etc.
  // `buildContext` identity therefore changed too, retriggering the
  // gated effect below, which POST'd `/chat/build-context` again
  // even though nothing the function cares about (IDs + modes) had
  // changed. Spurious network calls per refetch.
  //
  // The fix: derive a stable string fingerprint of just the
  // semantically-relevant data (source IDs, note IDs, the selections
  // map) and depend on that. The arrays + selections object live as
  // closure-captured refs but DON'T appear in the deps array.
  //
  // Why this is safe: buildContext only reads `.id` from each source/
  // note + the mode flag. If those don't change, the request body
  // doesn't change either.
  const sourcesKey = sources.map(s => s.id).join('|')
  const notesKey = notes.map(n => n.id).join('|')
  const selectionsKey = JSON.stringify(contextSelections)

  const buildContext = useCallback(async () => {
    const context_config: { sources: Record<string, string>, notes: Record<string, string> } = {
      sources: {},
      notes: {}
    }

    sources.forEach(source => {
      const mode = contextSelections.sources[source.id]
      if (mode === 'insights') {
        context_config.sources[source.id] = 'insights'
      } else if (mode === 'full') {
        context_config.sources[source.id] = 'full content'
      } else {
        context_config.sources[source.id] = 'not in'
      }
    })

    notes.forEach(note => {
      const mode = contextSelections.notes[note.id]
      if (mode === 'full') {
        context_config.notes[note.id] = 'full content'
      } else {
        context_config.notes[note.id] = 'not in'
      }
    })

    const response = await chatApi.buildContext({
      notebook_id: notebookId,
      context_config
    })
    return response  // { context, token_count, char_count }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [notebookId, sourcesKey, notesKey, selectionsKey])

  // Send message (synchronous, no streaming)
  // v0.8.63 — `bypassPrivacyGate` is the explicit "Re-ask allowing cloud"
  // consent from the redaction-review sheet; threaded to the request body so
  // the backend skips the fail-closed gate for this one turn. Default false.
  const sendMessage = useCallback(async (
    message: string,
    modelOverride?: string,
    bypassPrivacyGate?: boolean,
  ) => {
    let sessionId = currentSessionId

    // Auto-create session if none exists
    if (!sessionId) {
      try {
        const defaultTitle = message.length > 30
          ? `${message.substring(0, 30)}...`
          : message
        const newSession = await chatApi.createSession({
          notebook_id: notebookId,
          title: defaultTitle,
          // Include pending model override when creating session
          model_override: pendingModelOverride ?? undefined
        })
        sessionId = newSession.id
        setCurrentSessionId(sessionId)
        // Clear pending model override now that it's applied to the session
        setPendingModelOverride(null)
        queryClient.invalidateQueries({
          queryKey: QUERY_KEYS.notebookChatSessions(notebookId)
        })
      } catch (err: unknown) {
        const error = err as { response?: { data?: { detail?: string } }, message?: string };
        toast.error(getApiErrorMessage(error.response?.data?.detail || error.message, (key) => t(key), 'apiErrors.failedToCreateSession'))
        return
      }
    }

    // v0.7.26 — generate a UNIQUE temp id per send via crypto.randomUUID()
    // instead of `temp-${Date.now()}`. Two issues with the timestamp
    // approach:
    //   1. Double-click within the same millisecond produced duplicate
    //      IDs — React key warnings, and a partial-failure scenario
    //      could wipe both messages.
    //   2. On error, the catch handler filtered *every* `temp-` message,
    //      so if send A succeeded then B was in-flight and C errored,
    //      the rollback removed B's optimistic copy too — the user
    //      saw their B vanish even though the server had it.
    // Now: each send gets its own UUID, and rollback only removes the
    // specific one this call created.
    const tempId = `temp-${(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)}`
    const userMessage: NotebookChatMessage = {
      id: tempId,
      type: 'human',
      content: message,
      timestamp: new Date().toISOString()
    }
    setMessages(prev => [...prev, userMessage])
    setIsSending(true)
    // v0.8.21 — Increment in-flight counter BEFORE any await so the
    // currentSession useEffect doesn't clobber optimistic state when
    // a concurrent refetch returns. Decremented in finally{}.
    inFlightSendsRef.current += 1

    // v0.7.38 — token-streaming send. Replaces the buffered
    // chatApi.sendMessage with chatApi.streamMessage. While streaming,
    // a placeholder `streaming-${uuid}` AI message is appended and its
    // .content gets concatenated as tokens arrive. The user sees the
    // response build up character by character — critical for local
    // LLMs at 5-30 tok/s where the full response would otherwise be a
    // 15-30s wall of blank.
    const streamingAiId = `streaming-${(globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`)}`

    // v0.7.50 — bind a per-send AbortController. If a previous send is
    // still in flight when this one starts, abort it (the second send
    // wins). On unmount the effect's cleanup also aborts. Threaded
    // through chatApi.streamMessage as the `signal` argument.
    abortControllerRef.current?.abort()
    const controller = new AbortController()
    abortControllerRef.current = controller

    try {
      const built = await buildContext()

      // Append a placeholder AI message we'll mutate as tokens arrive.
      const placeholder: NotebookChatMessage = {
        id: streamingAiId,
        type: 'ai',
        content: '',
        timestamp: new Date().toISOString(),
      }
      setMessages(prev => [...prev, placeholder])

      let canonicalMessages: NotebookChatMessage[] | null = null
      let streamError: string | null = null
      // v0.8.1 Item 3 — accumulate MCP tool-call payloads from the
      // mcp_tool_calls event so we can stash them after the done event
      // tells us the canonical message IDs.
      let pendingMcpCalls: import('@/lib/types/api').McpToolCall[] | null = null

      // v0.8.70 — batch streamed tokens with requestAnimationFrame instead of
      // a setMessages() per token. At 50–150 tokens/sec the per-token version
      // re-rendered (and re-laid-out) the whole message list 50–100×/sec,
      // visibly janky in WKWebView. rAF coalesces to ≤1 render per paint frame.
      let tokenBuffer = ''
      let rafId: number | null = null
      const flushTokens = () => {
        rafId = null
        if (!tokenBuffer || !mountedRef.current) {
          tokenBuffer = ''
          return
        }
        const chunk = tokenBuffer
        tokenBuffer = ''
        setMessages(prev =>
          prev.map(m =>
            m.id === streamingAiId ? { ...m, content: m.content + chunk } : m,
          ),
        )
      }
      const scheduleFlush = () => {
        if (rafId == null) rafId = requestAnimationFrame(flushTokens)
      }

      for await (const event of chatApi.streamMessage({
        session_id: sessionId,
        message,
        context: built.context,
        model_override: modelOverride ?? (currentSession?.model_override ?? undefined),
        // v0.8.42 — surface the user's MCP server disable picks for
        // THIS turn. The disabledMcpServers state lives on the
        // useNotebookChat hook itself so the UI can manage it as a
        // simple Set<string>. undefined / empty array → no disables
        // (default v0.8.0 behaviour, no regression).
        disabled_mcp_servers:
          disabledMcpServers.length > 0 ? disabledMcpServers : undefined,
        // v0.8.63 — only sent (true) on an explicit "Re-ask allowing cloud".
        bypass_privacy_gate: bypassPrivacyGate || undefined,
        // v0.8.97 — omit when standard so pre-upgrade servers never see the field.
        chat_mode: debateMode ? 'debate' : undefined,
      }, controller.signal)) {
        // v0.7.50 — bail mid-stream if the component unmounted. Avoids
        // setState-on-dead-component warnings + extra setMessages
        // batch updates after the React tree is gone.
        if (!mountedRef.current) break

        if (event.type === 'token') {
          // v0.8.70 — buffer + rAF-flush rather than setMessages per token.
          tokenBuffer += event.content
          scheduleFlush()
        } else if (event.type === 'mcp_tool_calls') {
          // v0.8.1 Item 3 — stash MCP call payloads until we know the
          // canonical AI message ID (arrives in the 'done' event next).
          pendingMcpCalls = event.calls
        } else if (event.type === 'done') {
          // Server's canonical message list — wins over our streamed
          // buffer (which lacks IDs, timestamps, and any pre-existing
          // messages from the session checkpoint).
          // v0.7.50 — only accept the canonical replacement if it
          // actually has messages. An empty list comes from an outer
          // chain output we couldn't parse (LangGraph state shape
          // variance) and would otherwise WIPE the just-streamed reply.
          if (event.messages && event.messages.length > 0) {
            canonicalMessages = event.messages
            // v0.8.1 Item 3 — now that we have canonical message IDs,
            // stash any pending MCP call payloads keyed by the last
            // AI message's ID so CitationPill can look them up.
            const lastAiMsg = [...event.messages].reverse().find(m => m.type === 'ai')
            if (pendingMcpCalls && pendingMcpCalls.length > 0 && lastAiMsg) {
              queryClient.setQueryData(
                ['mcp', 'tool-calls', lastAiMsg.id],
                pendingMcpCalls,
              )
            }
            // v0.8.35c — stash the smart-router decision keyed by the
            // last AI message ID, same pattern as MCP captures above.
            // <ChatMessageProviderBadge messageId={...}> reads it via
            // useQuery and renders a small "local"/"cloud" chip next
            // to the message. We stash unconditionally (even when
            // selected_provider is null) so the badge consumer can
            // distinguish "smart routing didn't run" (null cached →
            // no badge) from "no data yet" (no cache entry → no
            // badge). The result is identical UI but the cache key's
            // presence is meaningful for debugging.
            if (lastAiMsg) {
              // The server evaluates notebook replies after returning the
              // completed turn. Mark this canonical ID before rendering it so
              // an empty first lookup receives a bounded persistence-grace
              // retry instead of remaining stale until remount.
              markEvaluationPersistencePending(
                queryClient,
                notebookId,
                lastAiMsg.id,
              )
              queryClient.setQueryData(
                ['chat', 'selected-provider', lastAiMsg.id],
                {
                  selected_provider: event.selected_provider,
                  selected_model_id: event.selected_model_id,
                  // v0.8.58/v0.8.60 — privacy-gate decision + agent-FSM state,
                  // read by ChatMessagePrivacyBadge from the same cache entry.
                  privacy_gated: event.privacy_gated ?? null,
                  privacy_categories: event.privacy_categories ?? null,
                  agent_state: event.agent_state ?? null,
                  // v0.8.68 — offline local-model fallback info, read by
                  // ChatMessageProviderBadge for the amber offline pill.
                  offline_fallback: event.offline_fallback ?? null,
                },
              )
            }
          }
        } else if (event.type === 'error') {
          streamError = event.detail
          break
        }
        // 'start' event acknowledged but not surfaced — UI already
        // shows the placeholder.
      }

      // v0.8.70 — drain any tokens still buffered when the stream closed, so
      // the streamed reply is complete before the canonical/error branch runs.
      if (rafId != null) {
        cancelAnimationFrame(rafId)
        rafId = null
      }
      flushTokens()

      if (streamError) {
        // Error path — clean up the streamed placeholder + the user
        // optimistic message; toast the failure.
        setMessages(prev =>
          prev.filter(m => m.id !== streamingAiId && m.id !== tempId),
        )
        toast.error(
          getApiErrorMessage(streamError, (key) => t(key), 'apiErrors.failedToSendMessage'),
        )
      } else if (canonicalMessages) {
        // Replace local streaming buffer with the server's canonical
        // list — same shape as the non-streaming /chat/execute response.
        setMessages(canonicalMessages)
        await refetchCurrentSession()
        // v0.7.189 — also invalidate the session LIST so the sidebar's
        // "last updated" timestamp on this session refreshes
        // immediately. Without this, the session card showed a stale
        // updated time until the next window-focus refetch.
        // Matches the pattern useSourceChat already uses.
        queryClient.invalidateQueries({
          queryKey: QUERY_KEYS.notebookChatSessions(notebookId)
        })
      } else {
        // Stream ended without an error or a done event — unusual
        // (server bug?). Keep what we have; refetch for safety.
        await refetchCurrentSession()
        queryClient.invalidateQueries({
          queryKey: QUERY_KEYS.notebookChatSessions(notebookId)
        })
      }
    } catch (err: unknown) {
      // v0.7.50 — AbortError = user navigated away mid-stream or a
      // second send aborted us. Silent: don't toast (no real failure).
      // Clean up only THIS send's IDs so the new send's optimistic
      // message survives. mountedRef guard prevents setMessages on
      // unmount.
      if ((err as { name?: string }).name === 'AbortError') {
        if (mountedRef.current) {
          setMessages(prev =>
            prev.filter(m => m.id !== tempId && m.id !== streamingAiId),
          )
        }
        return
      }
      const error = err as { response?: { data?: { detail?: string } }; message?: string };
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
      // Clean up both the user's optimistic message AND the streaming
      // AI placeholder.
      setMessages(prev =>
        prev.filter(m => m.id !== tempId && m.id !== streamingAiId),
      )
    } finally {
      // v0.7.50 — clear the ref ONLY if it's still pointing at OUR
      // controller. A second concurrent send would have replaced it
      // already; we don't want to null out the live ref.
      if (abortControllerRef.current === controller) {
        abortControllerRef.current = null
      }
      setIsSending(false)
      // v0.8.21 — Decrement the in-flight counter. Counter pattern
      // means another concurrent send keeps the guard armed even after
      // THIS send finishes. Math.max guards against the (purely
      // defensive) underflow case.
      inFlightSendsRef.current = Math.max(
        0, inFlightSendsRef.current - 1,
      )
    }
  }, [
    notebookId,
    currentSessionId,
    currentSession,
    pendingModelOverride,
    buildContext,
    refetchCurrentSession,
    queryClient,
    t,
    // v0.8.46b — `sendMessage` reads `disabledMcpServers` (v0.8.42) to
    // build the per-turn MCP disable list. It was missing from the
    // deps, so a toggle-then-send with no other dep change captured a
    // stale list (the server would see the picks from BEFORE the last
    // toggle). Adding it here makes the next send always reflect the
    // current picks. Caught by react-hooks/exhaustive-deps.
    disabledMcpServers,
    // v0.8.97 — same stale-closure hazard as disabledMcpServers above:
    // without this, toggling Debate then sending captured the old mode.
    debateMode,
  ])

  // Switch session
  const switchSession = useCallback((sessionId: string) => {
    setCurrentSessionId(sessionId)
    pruneMessageScopedQueries()
  }, [])

  // Create session
  const createSession = useCallback((title?: string) => {
    return createSessionMutation.mutate({
      notebook_id: notebookId,
      title
    })
  }, [createSessionMutation, notebookId])

  // Update session
  const updateSession = useCallback((sessionId: string, data: UpdateNotebookChatSessionRequest) => {
    return updateSessionMutation.mutate({
      sessionId,
      data
    })
  }, [updateSessionMutation])

  // Delete session
  const deleteSession = useCallback((sessionId: string) => {
    return deleteSessionMutation.mutate(sessionId)
  }, [deleteSessionMutation])

  // Set model override - handles both existing sessions and pending state
  const setModelOverride = useCallback((model: string | null) => {
    if (currentSessionId) {
      // Session exists - update it directly
      updateSessionMutation.mutate({
        sessionId: currentSessionId,
        data: { model_override: model }
      })
    } else {
      // No session yet - store as pending
      setPendingModelOverride(model)
    }
  }, [currentSessionId, updateSessionMutation])

  // v0.6.24 — fix a real out-of-order race when the user rapidly toggles
  // source/note inclusion. Each toggle triggers a new buildContext call,
  // and the previous effect simply awaited each and overwrote
  // tokenCount/charCount in completion order. Network latency varies, so
  // the LAST call to START was not always the LAST to FINISH — tokenCount
  // could end up stuck on a stale intermediate value (e.g. the user
  // selected A, A+B, A+B+C in fast succession, and the counts showed
  // the A+B total because that request finished last).
  //
  // Two guards:
  //   1. A monotonic request counter — each effect run captures its own
  //      counter, and only commits its result if its counter is STILL
  //      the most recent. Stale completions are dropped silently.
  //   2. A mountedRef — never setState after unmount.
  const contextRequestSeq = useRef(0)
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      // v0.7.50 — abort any in-flight streaming send on unmount so the
      // local LLM stops generating and the FastAPI is_disconnected()
      // check fires. Otherwise the worker keeps producing tokens until
      // it finishes the full response.
      abortControllerRef.current?.abort()
      // v0.8.66 (audit F-2) — drop the ad-hoc per-message chat cache entries
      // (mcp tool-calls + selected-provider/privacy/agent-state badges) so they
      // don't accumulate across navigations. They only hold live-streamed data
      // for this tab and are never refetched, so dropping on unmount is safe.
      pruneMessageScopedQueries()
    }
  }, [])

  useEffect(() => {
    if (!contextCountsEnabled) return

    const mySeq = ++contextRequestSeq.current
    const updateContextCounts = async () => {
      try {
        const result = await buildContext()
        // Drop stale results. mySeq < contextRequestSeq.current means
        // another effect run started after us; its result will land
        // shortly and overwrite ours, so don't commit ours.
        if (!mountedRef.current || mySeq !== contextRequestSeq.current) return
        setTokenCount(result.token_count)
        setCharCount(result.char_count)
      } catch (error) {
        if (!mountedRef.current || mySeq !== contextRequestSeq.current) return
        console.error('Error updating context counts:', error)
      }
    }
    updateContextCounts()
  }, [buildContext, contextCountsEnabled])

  // v0.7.191 — public cancelStreaming, parity with useSourceChat
  // (audit finding #3). Previously useNotebookChat had the
  // abortController plumbing but no public way for the UI to
  // trigger a cancel — only the unmount effect would abort.
  // Users couldn't stop a runaway local-LLM mid-generation.
  const cancelStreaming = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort()
      setIsSending(false)
    }
  }, [])

  return {
    // State
    sessions,
    currentSession: currentSession || sessions.find(s => s.id === currentSessionId),
    currentSessionId,
    messages,
    isSending,
    loadingSessions,
    tokenCount,
    charCount,
    pendingModelOverride,

    // v0.8.42 — per-conversation MCP server disable state. UI uses
    // these to render a tool picker above the chat input. Names are
    // matched case-insensitively + whitespace-trimmed on the backend
    // (`_resolve_chat_tools` normalises both sides), so the UI can
    // pass the raw `mcp_server.name` from the registry.
    disabledMcpServers,
    toggleDisabledMcpServer,

    // v0.8.97 — Debate mode state for the chat input toolbar.
    debateMode,
    setDebateMode,

    // Actions
    createSession,
    updateSession,
    deleteSession,
    switchSession,
    sendMessage,
    cancelStreaming,  // v0.7.191 — expose cancel control to UI
    setModelOverride,
    refetchSessions
  }
}
