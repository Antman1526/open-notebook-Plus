import apiClient from './client'
import { getApiUrl } from '@/lib/config'
import { readWithIdleTimeout, resolveStreamIdleTimeoutMs } from '@/lib/utils/stream-stall'
import {
  NotebookChatSession,
  NotebookChatSessionWithMessages,
  CreateNotebookChatSessionRequest,
  UpdateNotebookChatSessionRequest,
  SendNotebookChatMessageRequest,
  NotebookChatMessage,
  BuildContextRequest,
  BuildContextResponse,
  McpToolCall,
} from '@/lib/types/api'

// v0.7.38 — streaming chat events. One discriminated union per
// type emitted by /chat/stream's NDJSON wire format.
// v0.8.1 Item 3 — added mcp_tool_calls event emitted just before done.
// v0.8.1 follow-up — `done` now also carries `selected_provider` /
// `selected_model_id` (parity with the /chat/execute response). The
// fields are always present in the wire payload (null when smart
// routing didn't run), so destructuring is safe without optional chains.
export type ChatStreamEvent =
  | { type: 'start'; session_id: string }
  | { type: 'token'; content: string }
  | { type: 'mcp_tool_calls'; calls: McpToolCall[] }
  | {
      type: 'done'
      messages: NotebookChatMessage[]
      selected_provider: string | null
      selected_model_id: string | null
      // v0.8.58 — privacy-gate decision (kept-on-device). v0.8.60 —
      // agent-FSM terminal state. Optional: older backends omit them.
      privacy_gated?: boolean | null
      privacy_categories?: string[] | null
      agent_state?: string | null
      // v0.8.68 — offline gate substitution info (null when it didn't act).
      offline_fallback?: {
        offline_fallback: boolean
        from_model_id?: string | null
        to_model_id?: string | null
        to_model_name?: string | null
        reason?: string
      } | null
    }
  | { type: 'error'; detail: string }

export const chatApi = {
  // Session management
  listSessions: async (notebookId: string) => {
    const response = await apiClient.get<NotebookChatSession[]>(
      `/chat/sessions`,
      { params: { notebook_id: notebookId } }
    )
    return response.data
  },

  createSession: async (data: CreateNotebookChatSessionRequest) => {
    const response = await apiClient.post<NotebookChatSession>(
      `/chat/sessions`,
      data
    )
    return response.data
  },

  getSession: async (sessionId: string) => {
    const response = await apiClient.get<NotebookChatSessionWithMessages>(
      `/chat/sessions/${sessionId}`
    )
    return response.data
  },

  updateSession: async (sessionId: string, data: UpdateNotebookChatSessionRequest) => {
    const response = await apiClient.put<NotebookChatSession>(
      `/chat/sessions/${sessionId}`,
      data
    )
    return response.data
  },

  deleteSession: async (sessionId: string) => {
    await apiClient.delete(`/chat/sessions/${sessionId}`)
  },

  // Messaging (synchronous, no streaming)
  // v0.8.1 — response now includes selected_provider / selected_model_id
  // (the chat smart-router decision) and mcp_tool_calls (Item 3). Inline
  // type stays here rather than in lib/types/api.ts to keep call-site
  // typing tight; promote if a second consumer appears.
  sendMessage: async (data: SendNotebookChatMessageRequest) => {
    const response = await apiClient.post<{
      session_id: string
      messages: NotebookChatMessage[]
      selected_provider: string | null
      selected_model_id: string | null
      mcp_tool_calls: McpToolCall[] | null
      privacy_gated?: boolean | null
      privacy_categories?: string[] | null
      agent_state?: string | null
    }>(
      `/chat/execute`,
      data
    )
    return response.data
  },

  // v0.7.38 — streaming variant. Returns an async iterable of
  // ChatStreamEvent so callers can yield-loop over the stream:
  //
  //   for await (const event of chatApi.streamMessage(data, signal)) {
  //     if (event.type === 'token') ...
  //   }
  //
  // The fetch API is used directly (not axios) because axios doesn't
  // expose a streaming Response body in a usable shape. Auth header is
  // copied from the same localStorage location apiClient reads from
  // (no Zustand dependency at this level).
  streamMessage: async function* (
    data: SendNotebookChatMessageRequest,
    signal?: AbortSignal,
  ): AsyncGenerator<ChatStreamEvent, void, unknown> {
    const apiUrl = await getApiUrl()
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      Accept: 'application/x-ndjson',
    }
    if (typeof window !== 'undefined') {
      const authStorage = localStorage.getItem('auth-storage')
      if (authStorage) {
        try {
          const { state } = JSON.parse(authStorage)
          if (state?.token) headers.Authorization = `Bearer ${state.token}`
        } catch {
          // Ignore parse failure — request will go out unauthenticated
          // and 401 will redirect to /login per the standard flow.
        }
      }
    }

    const resp = await fetch(`${apiUrl}/api/chat/stream`, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
      signal,
    })
    if (!resp.ok || !resp.body) {
      const errBody = await resp.text().catch(() => '')
      throw new Error(
        `chat/stream returned HTTP ${resp.status}${errBody ? `: ${errBody}` : ''}`,
      )
    }

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    // v0.7.38 — defensive buffer cap. A pathological stream that never
    // emits a newline would grow unbounded. 4 MiB is generous (typical
    // chat response is <50 KiB) and well under heap pressure.
    const BUFFER_MAX = 4 * 1024 * 1024

    // v0.8.115 — idle-timeout guard. A dead local daemon or a sleep/wake
    // half-open socket otherwise parks this read forever with the UI
    // stuck in "streaming". Resolved per call so NEXT_PUBLIC_STREAM_IDLE_TIMEOUT_MS
    // can be tuned (0 disables). See stream-stall.ts.
    const idleTimeoutMs = resolveStreamIdleTimeoutMs()

    try {
      while (true) {
        const { value, done } = await readWithIdleTimeout(reader, idleTimeoutMs)
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        if (buffer.length > BUFFER_MAX) {
          throw new Error('stream buffer exceeded 4 MiB without a newline')
        }
        let nl = buffer.indexOf('\n')
        while (nl !== -1) {
          const line = buffer.slice(0, nl).trim()
          buffer = buffer.slice(nl + 1)
          if (line) {
            try {
              yield JSON.parse(line) as ChatStreamEvent
            } catch {
              // Malformed line — skip it, keep going. Errors are
              // surfaced separately as {"type":"error"} events.
            }
          }
          nl = buffer.indexOf('\n')
        }
      }
      // Flush any trailing partial line (server should always end on a
      // newline, but defensive).
      const tail = buffer.trim()
      if (tail) {
        try {
          yield JSON.parse(tail) as ChatStreamEvent
        } catch {
          // Same as above — silently drop unparseable trailing data
        }
      }
    } finally {
      // v0.7.50 — cancel BEFORE releaseLock so the underlying network
      // stream is actually torn down (releaseLock alone doesn't close
      // the response body). Without this, an aborted/iterator-break
      // path leaves the HTTP connection open until GC, and FastAPI's
      // `is_disconnected()` doesn't fire — the local LLM keeps
      // producing tokens nobody will see.
      try {
        await reader.cancel().catch(() => {})
      } catch {
        // cancel can throw if the stream is already errored; ignore.
      }
      try {
        reader.releaseLock()
      } catch {
        // ReadableStream lock release can no-op if the stream is
        // already locked elsewhere; not worth surfacing.
      }
    }
  },

  buildContext: async (data: BuildContextRequest) => {
    const response = await apiClient.post<BuildContextResponse>(
      `/chat/context`,
      data
    )
    return response.data
  },
}

export default chatApi
