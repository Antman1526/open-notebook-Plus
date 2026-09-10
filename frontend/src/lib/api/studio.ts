/**
 * ONP v0.7.0 — Studio API client.
 *
 * Wraps POST /api/studio/generate with proper multipart handling. Uses the
 * shared apiClient (axios) so the auth interceptor + base-URL discovery
 * apply automatically — same path used by sourcesApi.upload.
 */
import apiClient from './client'

export type StudioMode = 'notebook' | 'podcast' | 'both'
export type StudioArtifactType =
  | 'report'
  | 'study_guide'
  | 'course_pack'
  | 'training_guide'
  | 'briefing'
  | 'faq'
  | 'flashcards'
  | 'quiz'
  | 'data_table'
  | 'mind_map'
  | 'timeline'
  | 'infographic'
  | 'slide_deck'
  | 'podcast_outline'
  | 'podcast_audio'
  | 'research_run'

export type StudioArtifactStatus =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface StudioGenerateOptions {
  /** Files to ingest. Link-only requests may leave this empty. */
  files: File[]
  /** Optional http(s) links to ingest with the files. */
  links?: string[]
  /** Output mode. */
  mode: StudioMode
  /** Optional notebook title. Auto-generated from first filename if absent. */
  title?: string
  /** Required for podcast and combined mode. */
  episode_profile_name?: string
  /** Required for podcast and combined mode. */
  speaker_profile_name?: string
}

export interface StudioGenerateResponse {
  notebook_id: string
  mode: StudioMode
  /** Notebook / combined mode: id of the generated study-notes Note. */
  note_id?: string
  /** Podcast / combined mode: surreal_commands job id to poll for progress. */
  job_id?: string
  source_ids: string[]
  title: string
  /** Non-fatal issues during ingestion (e.g. one of several files
   *  couldn't be parsed). Frontend surfaces these as warnings. */
  warnings: string[]
}

export type StudioArtifactOutputPayload = Record<string, unknown> & {
  schema_version?: unknown
  document?: unknown
  markdown?: unknown
  content?: unknown
  validation?: unknown
  study_progress?: unknown
}

/**
 * Studio keeps exports extensible because artifact generators can add a
 * validated format without requiring a frontend API migration.
 */
export type StudioArtifactExportFormat =
  | 'markdown'
  | 'md'
  | 'json'
  | 'docx'
  | 'xlsx'
  | 'pptx'
  | 'pdf'
  | 'png'
  | 'svg'
  | 'csv'
  | 'research_bundle'
  | 'scorm_package'
  | 'xapi_package'
  | (string & {})

export type StudioArtifactExportPaths = Partial<
  Record<StudioArtifactExportFormat, string>
>

export interface StudioArtifact {
  id: string
  notebook_id: string
  artifact_type: StudioArtifactType
  title: string
  status: StudioArtifactStatus
  source_ids: string[]
  prompt?: string | null
  model_id?: string | null
  provider?: string | null
  output_format?: string | null
  output_payload: StudioArtifactOutputPayload
  citations: Array<Record<string, unknown>>
  export_paths: StudioArtifactExportPaths
  stale_export_formats?: string[]
  revision_of_id?: string | null
  created?: string | null
  updated?: string | null
}

export interface StudioArtifactCreate {
  notebook_id: string
  artifact_type: StudioArtifactType
  title: string
  source_ids?: string[]
  prompt?: string | null
  model_id?: string | null
  provider?: string | null
  output_format?: string | null
  revision_of_id?: string | null
}

export interface StudioArtifactUpdate {
  title?: string
  status?: StudioArtifactStatus
  source_ids?: string[]
  prompt?: string | null
  model_id?: string | null
  provider?: string | null
  output_format?: string | null
  output_payload?: StudioArtifactOutputPayload
  citations?: Array<Record<string, unknown>>
  export_paths?: StudioArtifactExportPaths
  revision_of_id?: string | null
}

export interface StudioArtifactDeleteResponse {
  deleted: boolean
  id: string
}

export type StudioWorkflowRunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface StudioWorkflowStep {
  id: string
  label: string
  status: string
}

export interface StudioWorkflowRun {
  id: string
  artifact_id: string
  notebook_id: string
  title: string
  status: StudioWorkflowRunStatus
  source_ids: string[]
  approval_required: boolean
  steps: StudioWorkflowStep[]
  command_id?: string | null
  created?: string | null
  updated?: string | null
}

export interface StudioWorkflowRunCreate {
  title: string
  source_ids?: string[]
  approval_required?: boolean
}

export const studioApi = {
  /**
   * Submit a Studio generation request.
   *
   * The backend will:
   *   1. Create a Notebook record
   *   2. Stream each file to UPLOADS_FOLDER + create a Source record
   *   3. Create link Source records for provided URLs
   *   4. Parse each source via content_core
   *   4. For notebook mode: invoke the LLM with the combined parsed text
   *      and save the response as an AI-authored Note attached to the
   *      notebook
   *   5. For podcast / combined mode: submit a podcast generation job against the
   *      newly-created notebook, return job_id
   *
   * Throws on HTTP errors; the caller's hook should display the message.
   */
  generate: async (opts: StudioGenerateOptions): Promise<StudioGenerateResponse> => {
    const links = (opts.links ?? []).map((link) => link.trim()).filter(Boolean)
    if (opts.files.length === 0 && links.length === 0) {
      throw new Error('At least one file or link is required')
    }
    if (opts.mode !== 'notebook' && (!opts.episode_profile_name || !opts.speaker_profile_name)) {
      throw new Error('Podcast and combined modes require episode_profile_name + speaker_profile_name')
    }

    const formData = new FormData()
    for (const file of opts.files) {
      formData.append('files', file)
    }
    for (const link of links) {
      formData.append('links', link)
    }
    formData.append('mode', opts.mode)
    if (opts.title) formData.append('title', opts.title)
    if (opts.episode_profile_name) {
      formData.append('episode_profile_name', opts.episode_profile_name)
    }
    if (opts.speaker_profile_name) {
      formData.append('speaker_profile_name', opts.speaker_profile_name)
    }

    // apiClient's interceptor (client.ts) deletes Content-Type for
    // FormData, letting the browser set the multipart boundary itself.
    const response = await apiClient.post<StudioGenerateResponse>(
      '/studio/generate',
      formData,
    )
    return response.data
  },
  listArtifacts: async (notebookId: string): Promise<StudioArtifact[]> => {
    const response = await apiClient.get<StudioArtifact[]>(
      `/studio/notebooks/${encodeURIComponent(notebookId)}/artifacts`,
      { headers: { 'x-skip-error-toast': '1' } },
    )
    return response.data
  },
  listArtifactRevisions: async (artifactId: string): Promise<StudioArtifact[]> => {
    const response = await apiClient.get<StudioArtifact[]>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}/revisions`,
      { headers: { 'x-skip-error-toast': '1' } },
    )
    return response.data
  },
  createArtifact: async (
    payload: StudioArtifactCreate,
  ): Promise<StudioArtifact> => {
    const response = await apiClient.post<StudioArtifact>(
      '/studio/artifacts',
      payload,
    )
    return response.data
  },
  updateArtifact: async (
    artifactId: string,
    payload: StudioArtifactUpdate,
  ): Promise<StudioArtifact> => {
    const response = await apiClient.patch<StudioArtifact>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}`,
      payload,
    )
    return response.data
  },
  deleteArtifact: async (
    artifactId: string,
  ): Promise<StudioArtifactDeleteResponse> => {
    const response = await apiClient.delete<StudioArtifactDeleteResponse>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}`,
    )
    return response.data
  },
  generateArtifact: async (
    artifactId: string,
  ): Promise<StudioArtifact> => {
    const response = await apiClient.post<StudioArtifact>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}/generate`,
    )
    return response.data
  },
  createWorkflowRun: async (
    artifactId: string,
    payload: StudioWorkflowRunCreate,
  ): Promise<StudioWorkflowRun> => {
    const response = await apiClient.post<StudioWorkflowRun>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}/workflow-runs`,
      payload,
    )
    return response.data
  },
  listWorkflowRuns: async (
    artifactId: string,
  ): Promise<StudioWorkflowRun[]> => {
    const response = await apiClient.get<StudioWorkflowRun[]>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}/workflow-runs`,
      { headers: { 'x-skip-error-toast': '1' } },
    )
    return response.data
  },
  approveWorkflowRun: async (
    runId: string,
  ): Promise<StudioWorkflowRun> => {
    const response = await apiClient.post<StudioWorkflowRun>(
      `/studio/workflow-runs/${encodeURIComponent(runId)}/approve`,
    )
    return response.data
  },
  // v0.8.119 — regenerate a single persisted export format (e.g. a course
  // pack's EPUB or PDF) without regenerating the whole artifact.
  regenerateExport: async (
    artifactId: string,
    format: StudioArtifactExportFormat,
  ): Promise<StudioArtifact> => {
    const response = await apiClient.post<StudioArtifact>(
      `/studio/artifacts/${encodeURIComponent(artifactId)}/exports/${encodeURIComponent(format)}`,
    )
    return response.data
  },
}
