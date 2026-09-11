import { create } from 'zustand'

import {
  normalizePodcastSelections,
  podcastSelectionSchema,
  type PodcastDestination,
  type PodcastSelection,
} from '@/lib/podcasts/selection'

interface PodcastStudioState {
  isOpen: boolean
  destination: PodcastDestination | null
  selections: PodcastSelection[]
  // v0.8.127 — the notebook this review was opened from, if any. Derived
  // from the selections passed to `open()`: a `kind: 'notebook'` selection
  // (the only selection shape that already carries a notebook id — see
  // NotebookRow.tsx / NotebookCard.tsx) sets it; anything else leaves it
  // null, i.e. "opened globally". Lets the eventual submit tag the episode
  // with `notebook_id` without every call site threading it through.
  notebookId: string | null
  invoker: HTMLElement | null
  open: (
    selections: PodcastSelection[],
    destination: PodcastDestination,
    explicitNotebookId?: string | null,
  ) => void
  handoffToStudio: () => void
  dismiss: () => void
}

const emptyStudioState = {
  isOpen: false,
  destination: null,
  selections: [] as PodcastSelection[],
  notebookId: null as string | null,
  invoker: null as HTMLElement | null,
}

/**
 * Transient review state only. It deliberately has no persistence, API call,
 * model request, or generation command; confirmation belongs to the Studio.
 */
export const usePodcastStudioStore = create<PodcastStudioState>()((set, get) => ({
  ...emptyStudioState,
  open: (selections, destination, explicitNotebookId) => {
    const parsed = selections.map((selection) => podcastSelectionSchema.parse(selection))
    const activeElement = typeof document === 'undefined' ? null : document.activeElement
    const notebookSelection = parsed.find((selection) => selection.kind === 'notebook')
    set({
      isOpen: true,
      destination,
      selections: normalizePodcastSelections(parsed),
      notebookId: explicitNotebookId ?? (notebookSelection ? notebookSelection.notebookId : null),
      invoker: activeElement instanceof HTMLElement ? activeElement : null,
    })
  },
  handoffToStudio: () => set((state) => ({
    ...state,
    isOpen: false,
    destination: 'studio',
  })),
  dismiss: () => {
    const invoker = get().invoker
    set(emptyStudioState)
    if (
      typeof window === 'undefined'
      || !invoker
      || !invoker.isConnected
      || invoker.hasAttribute('disabled')
    ) return
    window.setTimeout(() => {
      if (invoker.isConnected) invoker.focus()
    }, 0)
  },
}))
