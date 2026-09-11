import { QueryClient } from '@tanstack/react-query'

export function shouldRetryMutation(failureCount: number, error: unknown): boolean {
  const status = (error as { response?: { status?: number } })?.response?.status
  if (typeof status === 'number' && status >= 400 && status < 500) {
    return false
  }
  return failureCount < 1
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      gcTime: 10 * 60 * 1000, // 10 minutes
      retry: 2,
      refetchOnWindowFocus: false,
    },
    mutations: {
      retry: shouldRetryMutation,
    },
  },
})

export const QUERY_KEYS = {
  notebooks: ['notebooks'] as const,
  notebook: (id: string) => ['notebooks', id] as const,
  notes: (notebookId?: string) => ['notes', notebookId] as const,
  note: (id: string) => ['notes', id] as const,
  sources: (notebookId?: string) =>
    notebookId ? ['sources', 'list', notebookId] as const : ['sources', 'list'] as const,
  sourcesInfinite: (notebookId: string) => ['sources', 'infinite', notebookId] as const,
  source: (id: string) => ['sources', 'detail', id] as const,
  sourceStatus: (id: string) => ['sources', 'status', id] as const,
  sourceVisual: (id: string) => ['sources', 'visual', id] as const,
  recentVisualSources: (limit: number) => ['sources', 'visual', 'recent', limit] as const,
  searchSources: ['search', 'sources'] as const,
  knowledgeSourceSearch: ['knowledge-command-search'] as const,
  settings: ['settings'] as const,
  // v0.7.136 — Read-only observability config from /settings/observability.
  // Separate key from `settings` because the underlying endpoint is
  // env-derived and shouldn't be invalidated when the writable
  // settings change.
  observabilitySettings: ['settings', 'observability'] as const,
  sourceChatSessions: (sourceId: string) => ['source-chat', sourceId, 'sessions'] as const,
  sourceChatSession: (sourceId: string, sessionId: string) => ['source-chat', sourceId, 'sessions', sessionId] as const,
  notebookChatSessions: (notebookId: string) => ['notebook-chat', notebookId, 'sessions'] as const,
  notebookChatSession: (sessionId: string) => ['notebook-chat', 'sessions', sessionId] as const,
  studioArtifacts: (notebookId: string) => ['studio', notebookId, 'artifacts'] as const,
  studioArtifactRevisions: (artifactId: string) => ['studio', 'artifacts', artifactId, 'revisions'] as const,
  studioWorkflowRuns: (artifactId: string) => ['studio', 'artifacts', artifactId, 'workflow-runs'] as const,
  // v0.8.125 — Settings retention status card.
  studioRetentionStatus: ['studio', 'retention', 'status'] as const,
  podcastEpisodes: ['podcasts', 'episodes'] as const,
  podcastEpisode: (episodeId: string) => ['podcasts', 'episodes', episodeId] as const,
  episodeProfiles: ['podcasts', 'episode-profiles'] as const,
  speakerProfiles: ['podcasts', 'speaker-profiles'] as const,
  languages: ['languages'] as const,
  studyDue: ['study', 'due'] as const,
  // v0.8.97 — ExamLab
  studyExamAttempts: (notebookId?: string) => ['study', 'exams', 'attempts', notebookId ?? 'all'] as const,
  studyExamAttempt: (attemptId: string) => ['study', 'exams', 'attempts', 'byId', attemptId] as const,
  studyPlans: ['study', 'plans'] as const,
  studyPlan: (id: string) => ['study', 'plans', id] as const,
  studyPlanSources: (id: string) => ['study', 'plans', id, 'sources'] as const,
  studyPlanReadiness: (id: string) => ['study', 'plans', id, 'sources', 'readiness'] as const,
  studyPlanProgress: (id: string) => ['study', 'plans', id, 'progress'] as const,
  studyAnkiJob: (planId: string, jobId: string) => ['study', 'plans', planId, 'anki', 'jobs', jobId] as const,
  studySyllabus: (id: string) => ['study', 'plans', id, 'syllabus'] as const,
  captureRoots: ['capture', 'roots'] as const,
  captureItems: ['capture', 'items'] as const,
}

// v0.8.66 (audit F-2) — the chat hooks stash per-message badge data ad-hoc via
// queryClient.setQueryData([prefix, <messageId>], …), read by the per-message
// pill/badge components. There are exactly two such families (verified against
// the writers in useNotebookChat/useSourceChat + the readers in CitationPill /
// ChatMessageProviderBadge|PrivacyBadge|AgentStateBadge):
//   ['mcp', 'tool-calls', <id>]          — CitationPill MCP popover payloads
//   ['chat', 'selected-provider', <id>]  — provider + privacy + agent-state badge
// Each chat turn adds entries keyed by a fresh message id; over a long-lived tab
// or many navigations they accumulate (they only ever hold data streamed live in
// THIS tab and are never refetched). Prune them when the chat view unmounts.
// Both are 3-element prefixes, so prefix-matched removal can't collide with the
// non-ephemeral ['mcp','web-search'] status query or the session-list keys.
const MESSAGE_SCOPED_QUERY_PREFIXES = [
  ['mcp', 'tool-calls'],
  ['chat', 'selected-provider'],
] as const

/**
 * Remove the ad-hoc per-message chat cache entries (F-2). Call on chat-view
 * unmount to bound cache growth. Prefix-matched, so it clears every
 * `[prefix, <messageId>]` entry without touching other queries.
 */
export function pruneMessageScopedQueries(): void {
  for (const prefix of MESSAGE_SCOPED_QUERY_PREFIXES) {
    queryClient.removeQueries({ queryKey: [...prefix] })
  }
}
