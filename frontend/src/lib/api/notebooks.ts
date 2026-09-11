import apiClient from './client'
import {
  NotebookResponse,
  CreateNotebookRequest,
  UpdateNotebookRequest,
  NotebookDeletePreview,
  NotebookDeleteResponse,
  NotebookImportPreviewRequest,
  NotebookImportPreviewResponse,
  NotebookImportRequest,
  NotebookImportResponse,
  NotebookVectorizeRequest,
  NotebookVectorizeResponse,
} from '@/lib/types/api'

// v0.8.83 — mind-map graph types (improvement roadmap, Batch 3)
export interface NotebookGraphNode {
  id: string
  type: 'notebook' | 'source' | 'note' | 'studio_artifact'
  label: string
}

export interface NotebookGraphEdge {
  source: string
  target: string
  kind: 'reference' | 'artifact' | 'grounded_in'
}

export interface NotebookGraph {
  nodes: NotebookGraphNode[]
  edges: NotebookGraphEdge[]
}

// v0.8.87 — Discover sources (improvement roadmap, Batch 3)
export interface DiscoverResult {
  title: string
  url: string
  snippet: string
}

export interface DiscoverSourcesResponse {
  enabled: boolean
  provider: string | null
  results: DiscoverResult[]
}

export const notebooksApi = {
  list: async (params?: { archived?: boolean; order_by?: string }) => {
    const response = await apiClient.get<NotebookResponse[]>('/notebooks', { params })
    return response.data
  },

  get: async (id: string) => {
    const response = await apiClient.get<NotebookResponse>(`/notebooks/${id}`)
    return response.data
  },

  // v0.8.74 — corpus-grounded starter questions for the empty chat state
  // (improvement roadmap, Batch 1). Best-effort on the backend: returns [] on
  // any failure, so callers never need to special-case errors.
  suggestedQuestions: async (id: string, limit = 4): Promise<string[]> => {
    const response = await apiClient.get<{ questions: string[] }>(
      `/notebooks/${id}/suggested-questions`,
      { params: { limit } }
    )
    return response.data.questions ?? []
  },

  // v0.8.83 — mind-map graph (improvement roadmap, Batch 3): the notebook hub
  // plus its sources/notes as nodes, grounded in the reference/artifact edges.
  getGraph: async (id: string): Promise<NotebookGraph> => {
    const response = await apiClient.get<NotebookGraph>(`/notebooks/${id}/graph`)
    return response.data
  },

  // v0.8.87 — Discover sources: guarded web search (search-only; the caller
  // adds chosen results as link sources). enabled=false → no provider key set.
  discoverSources: async (
    id: string,
    query: string,
    limit?: number
  ): Promise<DiscoverSourcesResponse> => {
    const response = await apiClient.post<DiscoverSourcesResponse>(
      `/notebooks/${id}/discover-sources`,
      { query, limit }
    )
    return response.data
  },

  create: async (data: CreateNotebookRequest) => {
    const response = await apiClient.post<NotebookResponse>('/notebooks', data)
    return response.data
  },

  update: async (id: string, data: UpdateNotebookRequest) => {
    const response = await apiClient.put<NotebookResponse>(`/notebooks/${id}`, data)
    return response.data
  },

  deletePreview: async (id: string) => {
    const response = await apiClient.get<NotebookDeletePreview>(
      `/notebooks/${id}/delete-preview`
    )
    return response.data
  },

  delete: async (id: string, deleteExclusiveSources: boolean = false) => {
    const response = await apiClient.delete<NotebookDeleteResponse>(`/notebooks/${id}`, {
      params: { delete_exclusive_sources: deleteExclusiveSources },
    })
    return response.data
  },

  addSource: async (notebookId: string, sourceId: string) => {
    const response = await apiClient.post(`/notebooks/${notebookId}/sources/${sourceId}`)
    return response.data
  },

  removeSource: async (notebookId: string, sourceId: string) => {
    const response = await apiClient.delete(`/notebooks/${notebookId}/sources/${sourceId}`)
    return response.data
  },

  // v0.7.119 — Dry-run an import. Returns the planned notebook name,
  // detected layout, and the notes / sources that would be created.
  // No domain records are touched.
  importPreview: async (data: NotebookImportPreviewRequest) => {
    const response = await apiClient.post<NotebookImportPreviewResponse>(
      '/notebooks/import/preview',
      data,
    )
    return response.data
  },

  // v0.7.119 — Commit the import. Creates a new Notebook + its Notes /
  // Sources, or appends to an existing one.
  importNotebook: async (data: NotebookImportRequest) => {
    const response = await apiClient.post<NotebookImportResponse>(
      '/notebooks/import',
      data,
    )
    return response.data
  },

  // v0.7.119 — Bulk vectorize every Source attached to a notebook.
  // Fire-and-forget: returns immediately with queued command_ids.
  vectorizeSources: async (
    notebookId: string,
    data: NotebookVectorizeRequest = {},
  ) => {
    const response = await apiClient.post<NotebookVectorizeResponse>(
      `/notebooks/${notebookId}/vectorize_sources`,
      data,
    )
    return response.data
  },

  getExecutiveSynthesis: async (notebookId: string) => {
    const response = await apiClient.post<ExecutiveSynthesisResponse>(
      `/notebooks/${notebookId}/synthesis`,
    )
    return response.data
  },
}

export interface ExecutiveSynthesisResponse {
  notebook_id: string
  notebook_name: string
  synthesis: string
  source_count: number
  sources: string[]
}