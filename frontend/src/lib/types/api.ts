export interface NotebookResponse {
  id: string
  name: string
  description: string
  archived: boolean
  created: string
  updated: string
  source_count: number
  note_count: number
}

export interface NoteResponse {
  id: string
  title: string | null
  content: string | null
  note_type: string | null
  created: string
  updated: string
}

import type { SourceVisualReceipt, SourceVisualStatus } from './source-visuals'

export interface SourceListResponse {
  id: string
  title: string | null
  topics?: string[]                  // Make optional to match Python API
  provenance?: Record<string, unknown>
  source_type?: 'link' | 'upload' | 'text' | 'web_import' | 'deep_research_report' | string | null
  notebook_count?: number
  is_shared?: boolean
  asset: {
    file_path?: string
    url?: string
  } | null
  embedded: boolean
  embedded_chunks: number            // ADD: From Python API
  insights_count: number
  // v0.8.88 — one-line preview of the auto-summary insight (opt-in feature).
  summary_preview?: string | null
  created: string
  updated: string
  file_available?: boolean
  extracted_char_count?: number | null
  extraction_quality?: 'pending' | 'no_text' | 'low_text' | 'ok' | null
  // ADD: Async processing fields from Python API
  command_id?: string
  status?: string
  processing_info?: Record<string, unknown>
  visual?: SourceVisualReceipt | null
  visual_status?: SourceVisualStatus | null
}

export interface SourceDetailResponse extends SourceListResponse {
  full_text: string
  notebooks?: string[]  // List of notebook IDs this source is linked to
}

export type SourceResponse = SourceDetailResponse

export interface SourceStatusResponse {
  status?: string
  message: string
  processing_info?: Record<string, unknown>
  command_id?: string
}

export interface SettingsResponse {
  default_content_processing_engine_doc?: string
  default_content_processing_engine_url?: string
  default_embedding_option?: string
  auto_delete_files?: string
  youtube_preferred_languages?: string[]
  // v0.8.68 — forced offline mode toggle.
  offline_mode?: boolean
  // v0.8.88 — opt-in source auto-summary on ingest (default off).
  auto_summarize_on_ingest?: boolean
  // v0.8.91 — opt-in source key-topics extraction on ingest (default off).
  auto_extract_topics_on_ingest?: boolean
}

// v0.7.136 — Read-only observability config from GET /settings/observability
// (backend added in v0.7.130). Mirrors api/routers/settings.py:ObservabilityResponse.
// Every field reflects an DEEPER_NOTEBOOK_* env var read at request time, so the UI
// shows operators what their running process is actually seeing.
export interface ObservabilityResponse {
  slow_query_log_ms: number | null
  encryption_kdf: string
  checkpoint_keep_per_thread: number
  checkpoint_prune_interval_hours: number
  db_pool_size: number
  db_pool_disabled: boolean
  metrics_endpoint_path: string
}

export interface CreateNotebookRequest {
  name: string
  description?: string
}

export interface UpdateNotebookRequest {
  name?: string
  description?: string
  archived?: boolean
}

export interface NotebookDeletePreview {
  notebook_id: string
  notebook_name: string
  note_count: number
  exclusive_source_count: number
  shared_source_count: number
}

export interface NotebookDeleteResponse {
  message: string
  deleted_notes: number
  deleted_sources: number
  unlinked_sources: number
}

export interface CreateNoteRequest {
  title?: string
  content: string
  note_type?: string
  notebook_id?: string
}

export interface CreateSourceRequest {
  // Backward compatibility: support old single notebook_id
  notebook_id?: string
  // New multi-notebook support
  notebooks?: string[]
  // Required fields
  type: 'link' | 'upload' | 'text'
  url?: string
  file_path?: string
  content?: string
  title?: string
  topics?: string[]
  provenance?: Record<string, unknown>
  source_type?: 'link' | 'upload' | 'text' | 'web_import' | 'deep_research_report'
  transformations?: string[]
  embed?: boolean
  delete_source?: boolean
  // New async processing support
  async_processing?: boolean
}

export interface UpdateNoteRequest {
  title?: string
  content?: string
  note_type?: string
}

export interface UpdateSourceRequest {
  title?: string
  topics?: string[]
  provenance?: Record<string, unknown>
  source_type?: 'link' | 'upload' | 'text' | 'web_import' | 'deep_research_report'
}

export interface APIError {
  detail: string
}

// Source Chat Types
// Base session interface with common fields
export interface BaseChatSession {
  id: string
  title: string
  created: string
  updated: string
  message_count?: number
  model_override?: string | null
  // v0.8.43 — persistent per-conversation MCP server disable picks.
  // `useNotebookChat` hydrates its `disabledMcpServers` state from
  // this on session load so the v0.8.42 picks survive page reloads.
  // null / undefined = no picks (all servers visible).
  disabled_mcp_servers?: string[] | null
}

export interface SourceChatSession extends BaseChatSession {
  source_id: string
  model_override?: string
}

export interface SourceChatMessage {
  id: string
  type: 'human' | 'ai'
  content: string
  timestamp?: string
  // v0.8.117 — local-only UI flag set when a stream-stall recovery (v0.8.116)
  // kept this partial AI message. The server never sends this field; it is
  // cleared the next time the session is refetched.
  interrupted?: boolean
}

export interface SourceChatContextIndicator {
  sources: string[]
  insights: string[]
  notes: string[]
}

export interface SourceChatSessionWithMessages extends SourceChatSession {
  messages: SourceChatMessage[]
  context_indicators?: SourceChatContextIndicator
}

export interface CreateSourceChatSessionRequest {
  source_id: string
  title?: string
  model_override?: string
}

export interface UpdateSourceChatSessionRequest {
  title?: string
  model_override?: string | null
  // v0.8.44b — persistent source-chat MCP picks (parity with notebook
  // chat's v0.8.43 UpdateNotebookChatSessionRequest). null clears;
  // omitting the field leaves the persisted value untouched (the API
  // uses exclude_unset semantics).
  disabled_mcp_servers?: string[] | null
}

export interface SendMessageRequest {
  message: string
  model_override?: string
  // v0.8.44 — per-request MCP server disable list (source-chat
  // parity with notebook-chat's v0.8.42). Same shape, same backend
  // case-insensitive matching against `mcp_server.name`. Undefined =
  // all enabled servers visible.
  disabled_mcp_servers?: string[]
}

export interface SourceChatStreamEvent {
  type: 'user_message' | 'ai_message' | 'context_indicators' | 'complete' | 'error'
  content?: string
  data?: unknown
  message?: string
  timestamp?: string
}

// Notebook Chat Types
export interface NotebookChatSession extends BaseChatSession {
  notebook_id: string
}

export interface NotebookChatMessage {
  id: string
  type: 'human' | 'ai'
  content: string
  timestamp?: string
  // v0.8.117 — local-only UI flag set when a stream-stall recovery (v0.8.116)
  // kept this partial AI message. The server never sends this field; it is
  // cleared the next time the session is refetched.
  interrupted?: boolean
}

export interface NotebookChatSessionWithMessages extends NotebookChatSession {
  messages: NotebookChatMessage[]
}

export interface CreateNotebookChatSessionRequest {
  notebook_id: string
  title?: string
  model_override?: string
}

export interface UpdateNotebookChatSessionRequest {
  title?: string
  model_override?: string | null
  // v0.8.43 — persistent per-conversation MCP server disable picks.
  // Send `disabled_mcp_servers: [<names>]` to persist the picks
  // across page reloads; send `null` to clear them. Omitting the
  // field on PATCH does NOT touch the persisted value (the API
  // uses `exclude_unset=True` to distinguish "absent" from "clear").
  disabled_mcp_servers?: string[] | null
}

// v0.8.1 Item 3 — shape of a single MCP tool-call capture.
// Each record maps to one [mcp:N] marker in the AI message text.
// v0.8.18 — interface updated to match the post-v0.8.10/v0.8.13
// backend shape:
//   - `name` is the remote MCP tool name as exposed by the server
//     (gbrain: "search"/"think"/"find_trajectory"; Brave:
//     "brave_web_search"; etc.) — was wrongly documented as a
//     fixed "web_search"/"fetch_url" pair pre-v0.8.10.
//   - `blocks` is the new optional rich-content array from v0.8.13
//     (text / image / resource / unknown). Future frontend work
//     (v0.9) will render image thumbnails + resource chips in the
//     pill popover; declaring it now prevents type drift between
//     wire and UI when that lands.
export interface McpToolCall {
  /** 1-based index matching the [mcp:N] citation marker */
  index: number
  /** Remote MCP tool name as exposed by the server (server-dependent). */
  name: string
  /** Tool arguments forwarded to the MCP call. Shape depends on the tool. */
  args: Record<string, unknown>
  /** Concatenated text from all returned content blocks, truncated to 4000 chars. */
  text: string
  /**
   * v0.8.13 — Full content block list. Optional for back-compat with
   * cache entries written by pre-v0.8.13 backends. Each block has a
   * `type` discriminator: "text", "image", "resource", or "unknown".
   * Pill popover currently renders `text` only; thumbnails/resource
   * chips are v0.9 frontend work.
   */
  blocks?: Array<
    | { type: 'text'; text: string }
    | { type: 'image'; mime_type: string; data: string; bytes: number }
    | { type: 'resource'; uri: string; mime_type: string; text?: string; data?: string; bytes?: number }
    | { type: 'unknown'; repr: string }
  >
}

export interface SendNotebookChatMessageRequest {
  session_id: string
  message: string
  context: {
    sources: Array<Record<string, unknown>>
    notes: Array<Record<string, unknown>>
  }
  model_override?: string
  // v0.8.42 — per-request MCP server disable list. Names match
  // `mcp_server.name` case-insensitively on the backend
  // (`_resolve_chat_tools.exclude_server_names`). Omit / undefined =
  // all enabled servers visible (the v0.8.0 default). Used by the
  // MCP tool picker above the chat input to implement the
  // XDA-Developers / Pi-harness "load only what I need" pattern.
  disabled_mcp_servers?: string[]
  // v0.8.63 — explicit user consent to send THIS turn to cloud even though the
  // fail-closed privacy gate flagged it ("Re-ask allowing cloud"). Omit /
  // false → the gate stays active (default).
  bypass_privacy_gate?: boolean
  // v0.8.97 — per-turn conversation mode. 'debate' makes the assistant argue
  // the strongest opposing case grounded in the selected sources with
  // citations (prompts/chat/debate.jinja). Omit / 'standard' = normal chat.
  chat_mode?: 'standard' | 'debate'
}

export interface BuildContextRequest {
  notebook_id: string
  context_config: {
    sources: Record<string, string>
    notes: Record<string, string>
  }
}

export interface BuildContextResponse {
  context: {
    sources: Array<Record<string, unknown>>
    notes: Array<Record<string, unknown>>
  }
  token_count: number
  char_count: number
}

// v0.7.105 — Filesystem + export schemas. Mirrors the v0.7.90 backend
// routers (api/routers/filesystem.py, api/routers/exports.py) used by
// the directory-picker / export-dialog UI.

export interface FsEntry {
  name: string
  path: string
  is_dir: boolean
  size: number | null
  modified: string | null
}

export interface FsListResponse {
  path: string
  parent: string | null
  entries: FsEntry[]
  truncated: boolean
  warnings: string[]
}

export interface FsHomeResponse {
  home: string
  desktop: string | null
  documents: string | null
  downloads: string | null
  default_exports: string
}

export interface FsMkdirRequest {
  path: string
  parents?: boolean
}

export interface FsMkdirResponse {
  path: string
  created: boolean
}

export type FsListFilter = 'all' | 'dirs' | 'files'
// v0.7.119 — Expanded export formats. The backend (api/routers/exports.py
// NotebookExportRequest) accepts these six values. Names match the
// backend Literal so we don't have to translate on submit.
export type ExportFormat =
  | 'folder'
  | 'zip'
  | 'html_folder'
  | 'html_zip'
  | 'combined_md'
  | 'combined_html'
  | 'obsidian_folder'
  | 'obsidian_zip'

// v0.7.119 — Zip compression algorithm. Only meaningful when
// `format` ends in `zip`. Defaults to 'deflated' to match the backend.
export type ExportCompression = 'deflated' | 'stored' | 'bzip2' | 'lzma'

export interface NotebookExportRequest {
  destination: string
  format: ExportFormat
  include_sources?: boolean
  overwrite?: boolean
  compression?: ExportCompression
}

// v0.7.119 — Notebook import (dry-run preview + commit).
// Mirrors api/routers/exports.py NotebookImportPreviewRequest/Response
// and NotebookImportRequest/Response.
export type ImportKind = 'folder' | 'zip' | 'single_md'
export type ImportMode = 'new' | 'into_existing'

export interface NotebookImportPreviewRequest {
  source_path: string
}

export interface NotebookImportPreviewItem {
  relative_path: string
  title: string
  bytes: number
  is_overview: boolean
}

export interface NotebookImportPreviewResponse {
  source_path: string
  detected_kind: ImportKind
  notebook_name_hint: string | null
  description_hint: string | null
  notes: NotebookImportPreviewItem[]
  sources: NotebookImportPreviewItem[]
  has_manifest: boolean
  total_bytes: number
  warnings: string[]
}

export interface NotebookImportRequest {
  source_path: string
  mode: ImportMode
  target_notebook_id?: string | null
  new_name?: string | null
  import_sources?: boolean
}

export interface ImportedItemEntry {
  kind: 'note' | 'source'
  id: string
  title: string
  bytes: number
}

export interface NotebookImportResponse {
  notebook_id: string
  notebook_name: string
  mode: string
  note_ids: string[]
  source_ids: string[]
  file_count: number
  items: ImportedItemEntry[]
  warnings: string[]
}

// v0.7.119 — Bulk vectorize for a notebook's sources.
// Mirrors api/routers/embedding.py NotebookVectorizeRequest/Response.
export interface NotebookVectorizeRequest {
  only_missing?: boolean
}

export interface NotebookVectorizeSourceEntry {
  source_id: string
  title: string
  queued: boolean
  command_id: string | null
  skip_reason: string | null
}

export interface NotebookVectorizeResponse {
  notebook_id: string
  notebook_name: string
  total_sources: number
  queued: number
  skipped: number
  failed: number
  sources: NotebookVectorizeSourceEntry[]
  warnings: string[]
}

export interface NoteExportRequest {
  destination: string
  overwrite?: boolean
}

export interface ExportFileEntry {
  relative_path: string
  bytes: number
}

export interface ExportResponse {
  destination: string
  format: string
  file_count: number
  total_bytes: number
  files: ExportFileEntry[]
  warnings: string[]
}
