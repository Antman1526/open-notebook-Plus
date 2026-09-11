import { create } from 'zustand'
import { persist } from 'zustand/middleware'

// v0.8.125 — per-notebook mind-map canvas state (improvement roadmap).
// Remembers the active filter/search/cluster toggle for each notebook's
// mind map across dialog open/close and app restarts, keyed by notebookId
// so switching notebooks doesn't bleed one notebook's canvas state into
// another's. Follows display-preferences-store's validated
// partialize/merge pattern; setters are wrapped in try/catch like
// theme-store's setters so a storage failure (quota, unavailable
// localStorage) can never crash the mind map — it just won't remember the
// canvas state for that session.

export type MindMapFilterType = 'all' | 'source' | 'note' | 'studio_artifact'

export interface MindMapNotebookState {
  filter: MindMapFilterType
  query: string
  clusterByType: boolean
}

export interface MindMapStoreState {
  byNotebook: Record<string, MindMapNotebookState>
  setState: (notebookId: string, patch: Partial<MindMapNotebookState>) => void
  reset: (notebookId: string) => void
}

export const MIND_MAP_STORAGE_KEY = 'dn-mind-map'

export const DEFAULT_MIND_MAP_NOTEBOOK_STATE: MindMapNotebookState = {
  filter: 'all',
  query: '',
  clusterByType: false,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null
}

export function isMindMapFilterType(value: unknown): value is MindMapFilterType {
  return value === 'all' || value === 'source' || value === 'note' || value === 'studio_artifact'
}

function safeNotebookState(value: unknown): MindMapNotebookState {
  if (!isRecord(value)) return { ...DEFAULT_MIND_MAP_NOTEBOOK_STATE }
  return {
    filter: isMindMapFilterType(value.filter) ? value.filter : DEFAULT_MIND_MAP_NOTEBOOK_STATE.filter,
    query: typeof value.query === 'string' ? value.query : DEFAULT_MIND_MAP_NOTEBOOK_STATE.query,
    clusterByType: typeof value.clusterByType === 'boolean'
      ? value.clusterByType
      : DEFAULT_MIND_MAP_NOTEBOOK_STATE.clusterByType,
  }
}

function safeByNotebook(value: unknown): Record<string, MindMapNotebookState> {
  if (!isRecord(value)) return {}
  const out: Record<string, MindMapNotebookState> = {}
  for (const [notebookId, notebookState] of Object.entries(value)) {
    out[notebookId] = safeNotebookState(notebookState)
  }
  return out
}

export const useMindMapStore = create<MindMapStoreState>()(
  persist(
    (set) => ({
      byNotebook: {},
      setState: (notebookId, patch) => {
        try {
          set((state) => ({
            byNotebook: {
              ...state.byNotebook,
              [notebookId]: safeNotebookState({
                ...DEFAULT_MIND_MAP_NOTEBOOK_STATE,
                ...state.byNotebook[notebookId],
                ...patch,
              }),
            },
          }))
        } catch {
          // Persistence failure (quota, storage unavailable) must not crash
          // the mind map — the canvas state just won't be remembered.
        }
      },
      reset: (notebookId) => {
        try {
          set((state) => {
            if (!(notebookId in state.byNotebook)) return state
            const next = { ...state.byNotebook }
            delete next[notebookId]
            return { byNotebook: next }
          })
        } catch {
          // Same as above.
        }
      },
    }),
    {
      name: MIND_MAP_STORAGE_KEY,
      partialize: (state) => ({ byNotebook: state.byNotebook }),
      merge: (persistedState, currentState) => ({
        ...currentState,
        byNotebook: safeByNotebook(
          isRecord(persistedState) ? (persistedState as { byNotebook?: unknown }).byNotebook : undefined,
        ),
      }),
    },
  ),
)
