import apiClient from './client'
import { SearchRequest, SearchResponse, AskRequest } from '@/lib/types/search'
import { decodeSearchResponse } from '@/lib/types/source-visuals'

export const searchApi = {
  // Standard search (non-streaming)
  search: async (params: SearchRequest) => {
    const response = await apiClient.post<SearchResponse>('/search', params)
    return decodeSearchResponse(response.data) as SearchResponse
  },

  // Ask with streaming (uses relative URL for Docker compatibility)
  // v0.6.23 — accepts optional AbortSignal so the caller (useAsk) can
  // cancel an in-flight stream on unmount or user navigation, preventing
  // setState-on-unmounted-component warnings + connection leaks.
  askKnowledgeBase: async (params: AskRequest, signal?: AbortSignal) => {
    // Get auth token using the same logic as apiClient interceptor
    let token = null
    if (typeof window !== 'undefined') {
      const authStorage = localStorage.getItem('auth-storage')
      if (authStorage) {
        try {
          const { state } = JSON.parse(authStorage)
          if (state?.token) {
            token = state.token
          }
        } catch (error) {
          console.error('Error parsing auth storage:', error)
        }
      }
    }

    // Use relative URL to leverage Next.js rewrites
    // This works both in dev (Next.js proxy) and production (Docker network)
    const url = '/api/search/ask'

    // Use fetch with ReadableStream for SSE
    const response = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token && { Authorization: `Bearer ${token}` })
      },
      body: JSON.stringify(params),
      signal,
    })

    if (!response.ok) {
      // Try to extract error message from response
      let errorMessage = `HTTP error! status: ${response.status}`
      try {
        const errorData = await response.json()
        errorMessage = errorData.detail || errorData.message || errorMessage
      } catch {
        // If response isn't JSON, use status text
        errorMessage = response.statusText || errorMessage
      }
      throw new Error(errorMessage)
    }

    if (!response.body) {
      throw new Error('No response body received')
    }

    return response.body
  },

  // Deep Research agent invocation
  deepResearch: async (params: {
    objective: string
    notebook_id?: string
    max_queries?: number
    strategy_model?: string
    synthesis_model?: string
  }) => {
    const response = await apiClient.post<{
      objective: string
      plan: {
        strategy_summary?: string
        inquiry_paths?: Array<{
          sub_question: string
          search_query: string
          rationale: string
          facet: string
        }>
      }
      evidence_count: number
      research_brief: string
      citations: Array<{
        ref: string
        id: string
        title: string
        score?: number
      }>
      agent_state: string
    }>('/search/deep-research', params)
    return response.data
  }
}
