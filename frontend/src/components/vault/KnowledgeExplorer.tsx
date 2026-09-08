'use client'

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent } from 'react'
import { GitBranch, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { KnowledgePane, KnowledgeTab, KnowledgeWorkspaceDocument, OpenKnowledgeTab } from '@/lib/api/knowledge-workspace'
import type { VaultFile } from '@/lib/api/vault'
import { vaultApi } from '@/lib/api/vault'
import { overlayApi } from '@/lib/api/overlay'
import {
  useHydrateKnowledgeWorkspace,
  usePersistKnowledgeWorkspace,
} from '@/lib/hooks/use-knowledge-workspace'
import {
  useScanVault,
  useVaultFiles,
  useVaults,
} from '@/lib/hooks/use-vault'
import { useTodayOverlayNote } from '@/lib/hooks/use-overlay'
import {
  useCreateKnowledgeBookmark,
  useCreateKnowledgeWorkspace,
  useDeleteKnowledgeBookmark,
  useDeleteKnowledgeFolder,
  useDeleteKnowledgeWorkspace,
  useDuplicateKnowledgeWorkspace,
  useKnowledgeBookmarks,
  useKnowledgeFolders,
  useKnowledgeWorkspaces,
  useRandomKnowledgeNote,
  useRestoreKnowledgeWorkspace,
  useUpdateKnowledgeBookmark,
  useUpdateKnowledgeWorkspace,
} from '@/lib/hooks/use-knowledge-navigation'
import type {
  KnowledgeBookmark,
  KnowledgeOpenDescriptor,
  KnowledgeTarget,
  NamedKnowledgeWorkspaceSummary,
  NamedWorkspaceTab,
  NamedWorkspaceSnapshot,
  WorkspaceRestorePlan,
} from '@/lib/api/knowledge-navigation'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useMediaQuery } from '@/lib/hooks/use-media-query'
import { useKnowledgeIndexedSearch } from '@/lib/hooks/use-knowledge-command-data'
import { useLocalModelsHealth } from '@/lib/hooks/use-local-models'
import { RESEARCH_MODE_DESCRIPTORS, type ResearchMode } from '@/lib/knowledge/research-modes'
import { createKnowledgeWorkspaceTab, useKnowledgeWorkspaceStore } from '@/lib/stores/knowledge-workspace-store'
import { useOverlayDraftStore } from '@/lib/stores/overlay-draft-store'
import {
  KnowledgePaneContent,
  type KnowledgeNavigate,
} from './KnowledgePaneContent'
import { KnowledgeWorkspaceLayout } from './KnowledgeWorkspaceLayout'
import { VaultFileTree } from './VaultFileTree'
import { KnowledgeCommandBridge } from './KnowledgeCommandBridge'
import { KnowledgeQuickSwitcher } from './KnowledgeQuickSwitcher'
import { KnowledgeUtilityRail } from './KnowledgeUtilityRail'
import { KnowledgeModeLauncher } from './KnowledgeModeLauncher'
import { KnowledgeIntelligenceRail } from './KnowledgeIntelligenceRail'
import { ResearchCoreHeader } from './ResearchCoreHeader'
import { ResearchCoreFolioFrame } from '@/components/deeper-notebook/ResearchCoreFolioFrame'
import { KnowledgeBookmarksPanel } from './KnowledgeBookmarksPanel'
import { KnowledgeWorkspacesPanel } from './KnowledgeWorkspacesPanel'
import { WorkspaceRestoreDialog } from './WorkspaceRestoreDialog'
import { VaultGitHistoryDialog } from './VaultGitHistoryDialog'
import { CreateUniqueNoteDialog } from '../overlay/CreateUniqueNoteDialog'
import { OverlayUtilityPanel, localDateKey, tabFromOverlay } from '../overlay/OverlayUtilityPanel'

type SelectedKnowledgeRoot =
  | { authority: 'overlay'; id: 'overlay_space:default' }
  | { authority: 'external-vault'; id: string }

function titleFromRelativePath(relativePath: string): string {
  return relativePath.split('/').pop()?.replace(/\.(?:md|canvas)$/i, '') || relativePath
}

function tabFromFile(file: VaultFile): OpenKnowledgeTab {
  return {
    vaultId: file.vault_id,
    noteId: file.note_id,
    title: titleFromRelativePath(file.relative_path),
    relativePath: file.relative_path,
    viewMode: file.relative_path.toLocaleLowerCase().endsWith('.canvas')
      ? 'canvas'
      : 'reading',
    sourceAuthority: 'external-vault',
  }
}

function tabFromDescriptor(document: KnowledgeOpenDescriptor): OpenKnowledgeTab {
  return {
    vaultId: document.legacyContainerId,
    noteId: document.legacyNoteId,
    title: document.title,
    relativePath: document.relativeLocator,
    sourceAuthority: document.authorityKind === 'app_owned' ? 'overlay' : 'external-vault',
    knowledgeDocumentId: document.documentId,
  }
}

type GraphBookmarkContext = {
  rootDocumentId: string | null
  spaceIds: string[]
  relationKinds: string[]
  viewport: { x: number; y: number; zoom: number }
} | null

interface PostRestoreState {
  blocks: Array<{
    paneId: string
    tabId: string
    block: { blockId: string; sourceRevisionId: string | null }
  }>
  activeGraphContext: GraphBookmarkContext
}

function graphContextsEqual(left: GraphBookmarkContext, right: GraphBookmarkContext): boolean {
  return JSON.stringify(left) === JSON.stringify(right)
}

function createResearchModeTab(
  mode: ResearchMode,
  id: string,
  activeTab: KnowledgeTab | undefined,
  navigation: ReturnType<typeof useKnowledgeWorkspaceStore.getState>['navigation'],
): KnowledgeTab | null {
  const base = {
    id, vaultId: '', noteId: '', relativePath: '', viewMode: 'reading' as const,
    sourceAuthority: 'overlay' as const, knowledgeDocumentId: null, graphViewport: null,
  }
  const document = activeTab?.target?.kind === 'document' ? activeTab.target : null
  if (mode === 'read' || mode === 'write') {
    if (!document || (mode === 'write' && document.authority !== 'overlay')) return null
    return {
      id, vaultId: activeTab?.vaultId ?? '', noteId: activeTab?.noteId ?? '',
      title: activeTab?.title ?? document.title,
      relativePath: activeTab?.relativePath ?? document.relative_locator,
      mode,
      viewMode: mode === 'write' ? 'source' : 'reading',
      sourceAuthority: document.authority,
      knowledgeDocumentId: document.knowledge_document_id,
      graphViewport: activeTab?.graphViewport ?? null,
      target: { ...document, render_mode: mode === 'write' ? 'source' : 'reading' },
    }
  }
  if (mode === 'graph') {
    return {
      ...(activeTab ?? base), id, title: activeTab?.title ?? 'Graph', mode,
      viewMode: 'graph', graphViewport: activeTab?.graphViewport ?? { x: 0, y: 0, zoom: 1 },
      target: {
        kind: 'graph', root_document_id: document?.knowledge_document_id ?? activeTab?.knowledgeDocumentId ?? null,
        space_ids: navigation.selectedSpaceIds, relation_kinds: [],
        viewport: activeTab?.graphViewport ?? { x: 0, y: 0, zoom: 1 }, origin: document,
      },
    }
  }
  if (mode === 'ask') return { ...base, title: 'Ask', mode, target: { kind: 'ask', thread_id: null, selected_document_ids: document?.knowledge_document_id ? [document.knowledge_document_id] : [] } }
  if (mode === 'search') return { ...base, title: 'Search', mode, target: { kind: 'search', query: navigation.searchQuery, search_mode: navigation.searchMode, space_ids: navigation.selectedSpaceIds, authority_kinds: navigation.authorityFilters } }
  return { ...base, title: 'Podcast', mode, target: { kind: 'podcast', production_id: null, seed_document_ids: document?.knowledge_document_id ? [document.knowledge_document_id] : [] } }
}

function namedTargetForTab(
  tab: KnowledgeTab,
  documentId: string | null,
  focusedBlock: { blockId: string; sourceRevisionId: string | null } | undefined,
  graphContext: GraphBookmarkContext,
): KnowledgeTarget | null {
  // Named workspaces retain V2 research targets directly. This keeps
  // non-document modes durable without fabricating document identities.
  if (tab.target?.kind === 'search') {
    return {
      kind: 'search',
      query: tab.target.query,
      searchMode: tab.target.search_mode,
      spaceIds: tab.target.space_ids,
      authorityKinds: tab.target.authority_kinds,
      tags: [],
    }
  }
  if (tab.target?.kind === 'ask') {
    return {
      kind: 'ask',
      threadId: tab.target.thread_id,
      selectedDocumentIds: tab.target.selected_document_ids,
    }
  }
  if (tab.target?.kind === 'podcast') {
    return {
      kind: 'podcast',
      productionId: tab.target.production_id,
      seedDocumentIds: tab.target.seed_document_ids,
    }
  }
  if (focusedBlock && documentId) return { kind: 'block', documentId, ...focusedBlock }
  const persistedGraphTarget = tab.mode === 'graph' && tab.target?.kind === 'graph'
    ? tab.target
    : null
  const persistedGraphContext = tab.graphBookmarkContext ?? null
  if (persistedGraphTarget || tab.viewMode === 'graph') {
    const context = persistedGraphContext
      ?? (graphContext?.rootDocumentId === documentId ? graphContext : null)
    return {
      kind: 'graph',
      rootDocumentId: context?.rootDocumentId ?? documentId,
      spaceIds: context?.spaceIds ?? [],
      relationKinds: context?.relationKinds ?? [],
      viewport: context?.viewport ?? tab.graphViewport ?? { x: 0, y: 0, zoom: 1 },
    }
  }
  return documentId ? { kind: 'document', documentId } : null
}

async function namedSnapshotFromCurrentWorkspace(): Promise<NamedWorkspaceSnapshot> {
  const state = useKnowledgeWorkspaceStore.getState()
  const unresolved = new Map<string, Promise<string | null>>()
  const resolveDocumentId = (tab: OpenKnowledgeTab & { knowledgeDocumentId: string | null }) => {
    if (tab.knowledgeDocumentId) return Promise.resolve(tab.knowledgeDocumentId)
    const key = `${tab.sourceAuthority}:${tab.vaultId}:${tab.noteId}`
    let request = unresolved.get(key)
    if (!request) {
      request = tab.sourceAuthority === 'overlay'
        ? overlayApi.page(tab.noteId).then((page) => page.knowledge_document_id ?? null)
        : vaultApi.page(tab.vaultId, tab.noteId).then((page) => page.knowledge_document_id ?? null)
      unresolved.set(key, request)
    }
    return request
  }
  const documentBackedTabs = Object.values(state.panes).flatMap((pane) => pane.tabs)
    .filter((tab) => tab.target?.kind !== 'search' && tab.target?.kind !== 'ask' && tab.target?.kind !== 'podcast' && tab.target?.kind !== 'graph')
  const entries = await Promise.all(documentBackedTabs.map(async (tab) => {
    try {
      return [tab.id, await resolveDocumentId(tab)] as const
    } catch {
      return [tab.id, null] as const
    }
  }))
  const documentIds = new Map(entries)
  if (entries.some(([, documentId]) => !documentId)) {
    const unresolvedTabs = documentBackedTabs
      .filter((tab) => !documentIds.get(tab.id))
      .map((tab) => `${tab.id}:${tab.sourceAuthority}:${tab.vaultId}:${tab.noteId}`)
    throw new Error(`A tab could not be resolved to a stable knowledge document ID: ${unresolvedTabs.join(', ')}`)
  }
  return {
    version: 1,
    activePaneId: state.activePaneId,
    nextId: state.nextId,
    layout: state.layout,
    navigation: state.navigation,
    panes: Object.fromEntries(Object.entries(state.panes).map(([paneId, pane]) => {
      const tabs = pane.tabs.map((tab): NamedWorkspaceTab | null => {
        const target = namedTargetForTab(
          tab,
          tab.target?.kind === 'graph'
            ? tab.target.root_document_id ?? tab.knowledgeDocumentId
            : documentIds.get(tab.id) ?? null,
          state.focusedBlocksByTab[tab.id],
          state.graphBookmarkContext,
        )
        if (!target) return null
        const mode: NamedWorkspaceTab['mode'] = target.kind === 'search'
          ? 'search'
          : target.kind === 'graph'
            ? 'graph'
            : target.kind === 'ask'
              ? 'ask'
              : target.kind === 'podcast'
                ? 'podcast'
                : target.kind === 'block' ? 'read' : tab.mode === 'write' ? 'write' : 'read'
        return {
          id: tab.id,
          target,
          displayLabel: tab.title,
          viewMode: tab.viewMode,
          mode,
        }
      }).filter((tab): tab is NamedWorkspaceTab => tab !== null)
      return [paneId, {
        id: pane.id,
        activeTabId: tabs.some((tab) => tab.id === pane.activeTabId)
          ? pane.activeTabId
          : tabs[0]?.id ?? null,
        tabs,
      }]
    })),
  }
}

function workspaceFromRestorePlan(plan: WorkspaceRestorePlan): KnowledgeWorkspaceDocument {
  return {
    version: 2,
    activePaneId: plan.activePaneId,
    nextId: plan.nextId,
    layout: plan.layout,
    navigation: plan.navigation,
    panes: Object.fromEntries(Object.entries(plan.panes).map(([paneId, pane]) => {
      const tabs: KnowledgeTab[] = pane.tabs.flatMap<KnowledgeTab>((tab) => {
        if (tab.targetState !== 'available') return []
        if (tab.target.kind === 'search') return [{
          id: tab.id,
          vaultId: '',
          noteId: '',
          title: tab.displayLabel,
          relativePath: '',
          viewMode: 'reading' as const,
          sourceAuthority: 'overlay' as const,
          knowledgeDocumentId: null,
          graphViewport: null,
          mode: 'search' as const,
          target: {
            kind: 'search' as const,
            query: tab.target.query,
            search_mode: tab.target.searchMode,
            space_ids: tab.target.spaceIds,
            authority_kinds: tab.target.authorityKinds,
          },
        }]
        if (tab.target.kind === 'ask') return [{
          id: tab.id,
          vaultId: '',
          noteId: '',
          title: tab.displayLabel,
          relativePath: '',
          viewMode: 'reading' as const,
          sourceAuthority: 'overlay' as const,
          knowledgeDocumentId: null,
          graphViewport: null,
          mode: 'ask' as const,
          target: {
            kind: 'ask' as const,
            thread_id: tab.target.threadId,
            selected_document_ids: tab.target.selectedDocumentIds,
          },
        }]
        if (tab.target.kind === 'podcast') return [{
          id: tab.id,
          vaultId: '',
          noteId: '',
          title: tab.displayLabel,
          relativePath: '',
          viewMode: 'reading' as const,
          sourceAuthority: 'overlay' as const,
          knowledgeDocumentId: null,
          graphViewport: null,
          mode: 'podcast' as const,
          target: {
            kind: 'podcast' as const,
            production_id: tab.target.productionId,
            seed_document_ids: tab.target.seedDocumentIds,
          },
        }]
        if (tab.target.kind === 'graph' && !tab.targetDocument) return [{
          id: tab.id,
          vaultId: '',
          noteId: '',
          title: tab.displayLabel,
          relativePath: '',
          viewMode: 'graph' as const,
          sourceAuthority: 'overlay' as const,
          knowledgeDocumentId: tab.target.rootDocumentId,
          graphViewport: tab.target.viewport,
          graphBookmarkContext: {
            rootDocumentId: tab.target.rootDocumentId,
            spaceIds: tab.target.spaceIds,
            relationKinds: tab.target.relationKinds,
            viewport: tab.target.viewport,
          },
          mode: 'graph' as const,
          target: {
            kind: 'graph' as const,
            root_document_id: tab.target.rootDocumentId,
            space_ids: tab.target.spaceIds,
            relation_kinds: tab.target.relationKinds,
            viewport: tab.target.viewport,
            origin: null,
          },
        }]
        if (!tab.targetDocument) return []
        return [createKnowledgeWorkspaceTab({
          vaultId: tab.targetDocument!.legacyContainerId,
          noteId: tab.targetDocument!.legacyNoteId,
          title: tab.targetDocument!.title,
          relativePath: tab.targetDocument!.relativeLocator,
          sourceAuthority: tab.targetDocument!.authorityKind === 'app_owned' ? 'overlay' as const : 'external-vault' as const,
          knowledgeDocumentId: tab.targetDocument!.documentId,
          viewMode: tab.viewMode,
          mode: tab.mode,
          graphViewport: tab.target.kind === 'graph' ? tab.target.viewport : null,
          graphBookmarkContext: tab.target.kind === 'graph' ? {
            rootDocumentId: tab.target.rootDocumentId ?? tab.targetDocument!.documentId,
            spaceIds: tab.target.spaceIds,
            relationKinds: tab.target.relationKinds,
            viewport: tab.target.viewport,
          } : null,
        }, tab.id)]
      })
      return [paneId, {
        id: pane.id,
        activeTabId: tabs.some((tab) => tab.id === pane.activeTabId) ? pane.activeTabId : tabs[0]?.id ?? null,
        tabs,
      }] as [string, KnowledgePane]
    })),
  }
}

export function KnowledgeExplorer() {
  const { t } = useTranslation()
  const hydration = useHydrateKnowledgeWorkspace()
  const persistence = usePersistKnowledgeWorkspace()
  const mounts = useVaults()
  const [selectedRootState, setSelectedRootState] = useState<SelectedKnowledgeRoot | null>(null)
  const [uniqueDialogOpen, setUniqueDialogOpen] = useState(false)
  const [gitHistoryOpen, setGitHistoryOpen] = useState(false)
  const [restoreApplying, setRestoreApplying] = useState(false)
  const [restoreError, setRestoreError] = useState<string | null>(null)
  const [workspaceCommandIntent, setWorkspaceCommandIntent] = useState<{ id: number; kind: 'save' | 'replace' } | null>(null)
  const [postRestoreState, setPostRestoreState] = useState<PostRestoreState | null>(null)
  const [activePaneElement, setActivePaneElement] = useState<HTMLElement | null>(null)
  const isNarrowLayout = useMediaQuery('(max-width: 1023px)')
  const [utilityDrawerOpen, setUtilityDrawerOpen] = useState(false)
  const [intelligenceDrawerOpen, setIntelligenceDrawerOpen] = useState(false)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const utilityDrawerTriggerRef = useRef<HTMLButtonElement>(null)
  const intelligenceDrawerTriggerRef = useRef<HTMLButtonElement>(null)
  const fileTreeRef = useRef<HTMLElement>(null)
  const sidebarRef = useRef<HTMLElement>(null)
  const measuredSidebarElementRef = useRef<HTMLElement | null>(null)
  const linksRef = useRef<HTMLDivElement>(null)
  const paneElementsRef = useRef<Record<string, HTMLElement | null>>({})
  const resizeStartRef = useRef<{ x: number; width: number } | null>(null)
  const semanticSearchKeyRef = useRef<string | null>(null)
  const restoreInvokerRef = useRef<HTMLElement | null>(null)
  const selectedRoot = selectedRootState
    ?? (mounts.data?.[0]
      ? { authority: 'external-vault' as const, id: mounts.data[0].id }
      : { authority: 'overlay' as const, id: 'overlay_space:default' as const })
  const selectedVaultId = selectedRoot.authority === 'external-vault' ? selectedRoot.id : ''
  const files = useVaultFiles(selectedVaultId)
  const activePane = useKnowledgeWorkspaceStore(
    (state) => state.panes[state.activePaneId],
  )
  const activePaneId = useKnowledgeWorkspaceStore((state) => state.activePaneId)
  const panes = useKnowledgeWorkspaceStore((state) => state.panes)
  const workspaceHydrated = useKnowledgeWorkspaceStore((state) => state.hydrated)
  const activeTab = activePane?.tabs.find(
    (tab) => tab.id === activePane.activeTabId,
  ) ?? activePane?.tabs[0]
  const openTab = useKnowledgeWorkspaceStore((state) => state.openTab)
  const activateTab = useKnowledgeWorkspaceStore((state) => state.activateTab)
  const navigation = useKnowledgeWorkspaceStore((state) => state.navigation)
  const setNavigation = useKnowledgeWorkspaceStore((state) => state.setNavigation)
  const setGraphBookmarkContext = useKnowledgeWorkspaceStore((state) => state.setGraphBookmarkContext)
  const setPendingWorkspaceRestore = useKnowledgeWorkspaceStore((state) => state.setPendingWorkspaceRestore)
  const pendingWorkspaceRestore = useKnowledgeWorkspaceStore((state) => state.pendingWorkspaceRestore)
  const activeSearchContext = useKnowledgeWorkspaceStore((state) => state.activeSearchContext)
  const setActiveSearchContext = useKnowledgeWorkspaceStore((state) => state.setActiveSearchContext)
  const overlayDrafts = useOverlayDraftStore((state) => state.drafts)
  const localModelsHealth = useLocalModelsHealth()
  const semanticSearchDescriptorKey = activeSearchContext?.mode === 'semantic' && activeSearchContext.query
    ? JSON.stringify({
      mode: activeSearchContext.mode,
      query: activeSearchContext.query,
      spaceIds: [...activeSearchContext.spaceIds].sort(),
      authorityKinds: [...activeSearchContext.authorityKinds].sort(),
      tags: [...activeSearchContext.tags].sort(),
    })
    : null
  const indexedSearch = useKnowledgeIndexedSearch(activeSearchContext?.query || '', Boolean(activeSearchContext), {
    mode: activeSearchContext?.mode || 'text',
    spaceIds: activeSearchContext?.spaceIds || [],
    authorityKinds: activeSearchContext?.authorityKinds || [],
    tags: activeSearchContext?.tags || [],
  })
  const { runSemanticSearch } = indexedSearch
  const setTabViewMode = useKnowledgeWorkspaceStore((state) => state.setTabViewMode)
  const setTabGraphViewport = useKnowledgeWorkspaceStore((state) => state.setTabGraphViewport)
  const bookmarks = useKnowledgeBookmarks({
    folderId: navigation.activeBookmarkFolderId ?? undefined,
    tags: navigation.bookmarkTags.length ? navigation.bookmarkTags : undefined,
  })
  const folders = useKnowledgeFolders()
  const namedWorkspaces = useKnowledgeWorkspaces()
  const { mutateAsync: randomNote, isPending: randomNotePending } = useRandomKnowledgeNote()
  const { mutateAsync: createBookmark } = useCreateKnowledgeBookmark()
  const { mutateAsync: updateBookmark } = useUpdateKnowledgeBookmark()
  const { mutateAsync: deleteBookmark } = useDeleteKnowledgeBookmark()
  const { mutateAsync: deleteFolder } = useDeleteKnowledgeFolder()
  const { mutateAsync: restoreWorkspace } = useRestoreKnowledgeWorkspace()
  const { mutateAsync: createWorkspace } = useCreateKnowledgeWorkspace()
  const { mutateAsync: updateWorkspace } = useUpdateKnowledgeWorkspace()
  const { mutateAsync: duplicateWorkspace } = useDuplicateKnowledgeWorkspace()
  const { mutateAsync: deleteWorkspace } = useDeleteKnowledgeWorkspace()
  const {
    mutateAsync: scanVault,
    isPending: scanPending,
    error: scanError,
  } = useScanVault(
    selectedVaultId,
    activeTab?.sourceAuthority === 'external-vault' && activeTab.vaultId === selectedVaultId
      ? activeTab.noteId
      : undefined,
  )
  const {
    mutateAsync: createTodayOverlay,
    isPending: todayOverlayPending,
    isError: todayOverlayError,
  } = useTodayOverlayNote()

  useEffect(() => {
    if (!semanticSearchDescriptorKey) {
      semanticSearchKeyRef.current = null
      return
    }
    if (semanticSearchKeyRef.current === semanticSearchDescriptorKey) return
    semanticSearchKeyRef.current = semanticSearchDescriptorKey
    runSemanticSearch()
  }, [runSemanticSearch, semanticSearchDescriptorKey])

  useEffect(() => {
    const context = activeTab?.viewMode === 'graph'
      ? activeTab.graphBookmarkContext ?? null
      : null
    if (!context) return
    const current = useKnowledgeWorkspaceStore.getState().graphBookmarkContext
    if (!graphContextsEqual(current, context)) setGraphBookmarkContext(context)
  }, [activeTab?.graphBookmarkContext, activeTab?.viewMode, setGraphBookmarkContext])

  useEffect(() => {
    if (!postRestoreState) return
    // Pane selection effects clear their old focus during the restore commit.
    // Run after that commit so only confirmed, still-present tab IDs are restored.
    const timer = window.setTimeout(() => {
      const state = useKnowledgeWorkspaceStore.getState()
      postRestoreState.blocks.forEach(({ paneId, tabId, block }) => {
        state.setFocusedBlock(paneId, tabId, block)
      })
      if (postRestoreState.activeGraphContext) {
        state.setGraphBookmarkContext(postRestoreState.activeGraphContext)
      }
      setPostRestoreState(null)
    }, 0)
    return () => window.clearTimeout(timer)
  }, [postRestoreState])

  const openFile = (file: VaultFile, paneId?: string) => {
    openTab(tabFromFile(file), paneId)
  }

  const openResearchMode = useCallback((mode: ResearchMode, paneId: string) => {
    const state = useKnowledgeWorkspaceStore.getState()
    const pane = state.panes[paneId]
    if (!pane) return
    const existing = pane.tabs.find((tab) => (
      tab.mode === mode && tab.target?.kind === RESEARCH_MODE_DESCRIPTORS[mode].targetKind
    ))
    if (existing) {
      state.activateTab(paneId, existing.id)
      return
    }
    if (pane.tabs.length >= 128) return
    const active = pane.tabs.find((tab) => tab.id === pane.activeTabId) ?? pane.tabs[0]
    let nextId = state.nextId
    const usedIds = new Set(Object.values(state.panes).flatMap((candidate) => candidate.tabs.map((tab) => tab.id)))
    while (usedIds.has(`tab-${nextId}`)) nextId += 1
    const created = createResearchModeTab(mode, `tab-${nextId}`, active, state.navigation)
    if (!created) return
    useKnowledgeWorkspaceStore.setState({
      activePaneId: paneId,
      nextId: nextId + 1,
      revision: state.revision + 1,
      focusedBlocksByTab: {},
      pendingWorkspaceRestore: null,
      activeSearchContext: mode === 'search' && created.target?.kind === 'search'
        ? {
            query: created.target.query,
            mode: created.target.search_mode,
            spaceIds: created.target.space_ids,
            authorityKinds: created.target.authority_kinds,
            tags: state.navigation.bookmarkTags,
          }
        : null,
      panes: {
        ...state.panes,
        [paneId]: { ...pane, activeTabId: created.id, tabs: [...pane.tabs, created] },
      },
    })
  }, [])

  const authoritySummary = Object.values(panes).flatMap((pane) => pane.tabs)
    .reduce((summary, tab) => ({
      appOwned: summary.appOwned + (tab.sourceAuthority === 'overlay' ? 1 : 0),
      externalReadOnly: summary.externalReadOnly + (tab.sourceAuthority === 'external-vault' ? 1 : 0),
    }), { appOwned: 0, externalReadOnly: 0 })
  const healthyLocalModels = localModelsHealth.data?.models.filter(
    (model) => model.status === 'healthy',
  ) ?? []
  const localReadiness = localModelsHealth.isLoading
    ? { state: 'loading' as const, detail: 'Checking local model readiness', models: [] }
    : localModelsHealth.isError || localModelsHealth.data?.overall !== 'healthy'
      ? { state: 'unavailable' as const, detail: 'Local model readiness is unavailable', models: [] }
      : {
          state: 'ready' as const,
          detail: `${healthyLocalModels.length} local model${healthyLocalModels.length === 1 ? '' : 's'} ready`,
          models: healthyLocalModels.map((model) => ({ id: model.name, provider: model.runtime ?? 'Local' })),
        }
  const modeAvailability = useMemo(() => {
    const hasOpenMode = (mode: ResearchMode) => activePane?.tabs.some((tab) => (
      tab.mode === mode && tab.target?.kind === RESEARCH_MODE_DESCRIPTORS[mode].targetKind
    )) ?? false
    return {
    read: activeTab?.target?.kind === 'document' || hasOpenMode('read')
      ? { available: true, reason: null }
      : { available: false, reason: 'Requires a document target' },
    write: (activeTab?.target?.kind === 'document' && activeTab.target.authority === 'overlay') || hasOpenMode('write')
      ? { available: true, reason: null }
      : { available: false, reason: 'External source — read only' },
    ask: localReadiness.state === 'ready'
      ? { available: true, reason: null }
      : { available: false, reason: localReadiness.detail },
    search: { available: true, reason: null },
    graph: { available: true, reason: null },
    podcast: { available: true, reason: null },
  }
  }, [activePane?.tabs, activeTab?.target, localReadiness.detail, localReadiness.state])
  const hasUnsavedOverlayDraft = Boolean(
    activeTab?.sourceAuthority === 'overlay' && overlayDrafts[`${activePaneId}:${activeTab.id}`],
  )
  const openResearchModeForActivePane = useCallback(
    (mode: ResearchMode) => openResearchMode(mode, activePaneId),
    [activePaneId, openResearchMode],
  )

  const navigate: KnowledgeNavigate = (
    targetVaultId,
    targetNoteId,
    relativePathHint,
    titleHint,
    paneId,
    targetText,
    sourceAuthority,
  ) => {
    const listedFile = sourceAuthority === 'external-vault'
      ? files.data?.find(
          (file) => file.vault_id === targetVaultId
            && file.note_id === targetNoteId,
        )
      : undefined
    if (listedFile) {
      openTab({
        vaultId: listedFile.vault_id,
        noteId: listedFile.note_id,
        title: titleHint?.trim()
          || targetText
          || titleFromRelativePath(listedFile.relative_path),
        relativePath: listedFile.relative_path,
        sourceAuthority,
      }, paneId)
      return
    }

    const existingTab = Object.values(
      useKnowledgeWorkspaceStore.getState().panes,
    )
      .flatMap((pane) => pane.tabs)
      .find((tab) => tab.vaultId === targetVaultId
        && tab.noteId === targetNoteId
        && tab.sourceAuthority === sourceAuthority)
    if (existingTab) {
      openTab({
        vaultId: existingTab.vaultId,
        noteId: existingTab.noteId,
        title: existingTab.title,
        relativePath: existingTab.relativePath,
        viewMode: existingTab.viewMode,
        sourceAuthority,
      }, paneId)
      return
    }

    if (!relativePathHint) return
    openTab({
      vaultId: targetVaultId,
      noteId: targetNoteId,
      title: titleHint?.trim() || targetText || titleFromRelativePath(relativePathHint),
      relativePath: relativePathHint,
      sourceAuthority,
    }, paneId)
  }

  const selected = selectedRoot.authority === 'external-vault'
    ? mounts.data?.find((mount) => mount.id === selectedRoot.id)
    : undefined
  const selectedNoteId = activeTab?.sourceAuthority === 'external-vault' && activeTab.vaultId === selectedVaultId
    ? activeTab.noteId
    : ''
  const scanSelectedVault = useCallback(
    async () => {
      if (selectedRoot.authority !== 'external-vault') return
      await scanVault()
    },
    [scanVault, selectedRoot.authority],
  )
  const openTodayOverlay = useCallback(async () => {
    const page = await createTodayOverlay(localDateKey())
    openTab(tabFromOverlay(page))
  }, [createTodayOverlay, openTab])
  const openUniqueOverlayDialog = useCallback(() => setUniqueDialogOpen(true), [])
  const openDescriptor = useCallback((document: KnowledgeOpenDescriptor) => {
    openTab(tabFromDescriptor(document))
  }, [openTab])
  const applyRestorePlan = useCallback(async (plan: WorkspaceRestorePlan) => {
    setRestoreApplying(true)
    setRestoreError(null)
    try {
      const workspace = workspaceFromRestorePlan(plan)
      const restoredBlocks = Object.values(plan.panes).flatMap((pane) => pane.tabs.flatMap((tab) => {
        if (tab.targetState !== 'available' || tab.target.kind !== 'block') return []
        return [{ paneId: pane.id, tabId: tab.id, block: {
          blockId: tab.target.blockId, sourceRevisionId: tab.target.sourceRevisionId,
        } }]
      }))
      const applied = useKnowledgeWorkspaceStore.getState().applyNamedWorkspace(workspace)
      if (!applied) throw new Error('The restore plan is not a valid workspace.')
      const activePane = plan.panes[plan.activePaneId]
      const activeTab = activePane?.tabs.find((tab) => tab.id === activePane.activeTabId)
      const activeGraphContext = activeTab?.targetState === 'available' && activeTab.target.kind === 'graph'
        ? {
          rootDocumentId: activeTab.target.rootDocumentId ?? activeTab.targetDocument?.documentId ?? null,
          spaceIds: activeTab.target.spaceIds,
          relationKinds: activeTab.target.relationKinds,
          viewport: activeTab.target.viewport,
        }
        : null
      setPostRestoreState({ blocks: restoredBlocks, activeGraphContext })
      setPendingWorkspaceRestore(null)
    } catch {
      setRestoreError('Available targets could not be opened. Your current session was left unchanged.')
    } finally {
      setRestoreApplying(false)
    }
  }, [setPendingWorkspaceRestore])
  const openNamedWorkspace = useCallback(async (workspace: NamedKnowledgeWorkspaceSummary) => {
    restoreInvokerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const plan = await restoreWorkspace({ workspaceId: workspace.id, revision: workspace.revision })
    const hasUnavailableTargets = plan.summary.stale > 0
      || plan.summary.unavailable > 0
      || plan.summary.missing > 0
    if (hasUnavailableTargets) {
      setRestoreError(null)
      setPendingWorkspaceRestore(plan)
      return
    }
    await applyRestorePlan(plan)
  }, [applyRestorePlan, restoreWorkspace, setPendingWorkspaceRestore])
  const saveCurrentWorkspaceAs = useCallback(async (name: string) => {
    await createWorkspace({ name, snapshot: await namedSnapshotFromCurrentWorkspace() })
  }, [createWorkspace])
  const renameWorkspace = useCallback(async (workspace: NamedKnowledgeWorkspaceSummary, name: string) => {
    await updateWorkspace({ workspaceId: workspace.id, command: { expectedRevision: workspace.revision, name } })
  }, [updateWorkspace])
  const duplicateNamedWorkspace = useCallback(async (workspace: NamedKnowledgeWorkspaceSummary, name: string) => {
    await duplicateWorkspace({ workspaceId: workspace.id, command: { name } })
  }, [duplicateWorkspace])
  const replaceNamedWorkspace = useCallback(async (workspace: NamedKnowledgeWorkspaceSummary) => {
    await updateWorkspace({
      workspaceId: workspace.id,
      command: { expectedRevision: workspace.revision, snapshot: await namedSnapshotFromCurrentWorkspace() },
    })
  }, [updateWorkspace])
  const deleteNamedWorkspace = useCallback(async (workspace: NamedKnowledgeWorkspaceSummary) => {
    await deleteWorkspace({ workspaceId: workspace.id, command: { expectedRevision: workspace.revision } })
  }, [deleteWorkspace])
  const openRandomNote = useCallback(async () => {
    const result = await randomNote({
      spaceIds: navigation.selectedSpaceIds,
      authorityKinds: navigation.authorityFilters,
      tags: navigation.bookmarkTags,
    })
    if (result.state === 'selected') openDescriptor(result.document)
  }, [navigation.authorityFilters, navigation.bookmarkTags, navigation.selectedSpaceIds, openDescriptor, randomNote])
  const openBookmarks = useCallback(() => {
    setNavigation({ utilityMode: 'bookmarks' })
  }, [setNavigation])
  const openWorkspaces = useCallback(() => {
    setNavigation({ utilityMode: 'workspaces' })
  }, [setNavigation])
  const requestWorkspaceAction = useCallback((kind: 'save' | 'replace') => {
    setNavigation({ utilityMode: 'workspaces' })
    setWorkspaceCommandIntent((current) => ({ id: (current?.id ?? 0) + 1, kind }))
  }, [setNavigation])
  const requestSaveWorkspaceAs = useCallback(() => requestWorkspaceAction('save'), [requestWorkspaceAction])
  const requestReplaceWorkspace = useCallback(() => requestWorkspaceAction('replace'), [requestWorkspaceAction])
  const toggleMetrics = useCallback(() => {
    setNavigation({ metricsVisible: !useKnowledgeWorkspaceStore.getState().navigation.metricsVisible })
  }, [setNavigation])
  const bookmarkCurrentTarget = useCallback(async () => {
    if (!activeTab?.knowledgeDocumentId) return
    const currentWorkspace = useKnowledgeWorkspaceStore.getState()
    const currentNavigation = currentWorkspace.navigation
    const currentGraphContext = currentWorkspace.graphBookmarkContext
    const focusedBlock = currentWorkspace.focusedBlocksByTab[activeTab.id] ?? null
    const persistedGraphTarget = activeTab.mode === 'graph' && activeTab.target?.kind === 'graph'
      ? activeTab.target
      : null
    const persistedGraphContext = activeTab.graphBookmarkContext ?? null
    await createBookmark({
      target: focusedBlock
        ? {
            kind: 'block', documentId: activeTab.knowledgeDocumentId,
            blockId: focusedBlock.blockId, sourceRevisionId: focusedBlock.sourceRevisionId,
          }
        : (activeTab.mode === 'graph' && activeTab.target?.kind === 'graph') || activeTab.viewMode === 'graph'
        ? {
            kind: 'graph', rootDocumentId: persistedGraphContext?.rootDocumentId
              ?? persistedGraphTarget?.root_document_id
              ?? activeTab.knowledgeDocumentId,
            spaceIds: persistedGraphContext
              ? persistedGraphContext.spaceIds
              : currentGraphContext?.rootDocumentId === activeTab.knowledgeDocumentId
              ? currentGraphContext.spaceIds : currentNavigation.selectedSpaceIds,
            relationKinds: persistedGraphContext
              ? persistedGraphContext.relationKinds
              : currentGraphContext?.rootDocumentId === activeTab.knowledgeDocumentId
              ? currentGraphContext.relationKinds : [],
            viewport: persistedGraphContext
              ? persistedGraphContext.viewport
              : currentGraphContext?.rootDocumentId === activeTab.knowledgeDocumentId
              ? currentGraphContext.viewport
              : activeTab.graphViewport ?? { x: 0, y: 0, zoom: 1 },
          }
        : { kind: 'document', documentId: activeTab.knowledgeDocumentId },
      displayLabel: activeTab.title,
      authorityKind: activeTab.sourceAuthority === 'overlay' ? 'app_owned' : 'external_read_only',
      spaceId: null,
      folderId: currentNavigation.activeBookmarkFolderId,
      tags: currentNavigation.bookmarkTags,
      position: 0,
    })
  }, [activeTab, createBookmark])
  const bookmarkSearch = useCallback(async (
    query: string,
    searchMode: 'exact' | 'text' | 'semantic',
  ) => {
    await createBookmark({
      target: {
        kind: 'search', query, searchMode, spaceIds: navigation.selectedSpaceIds,
        authorityKinds: navigation.authorityFilters,
        tags: navigation.bookmarkTags,
      },
      displayLabel: `Search: ${query}`,
      authorityKind: null,
      spaceId: null,
      folderId: navigation.activeBookmarkFolderId,
      tags: navigation.bookmarkTags,
      position: 0,
    })
  }, [createBookmark, navigation.activeBookmarkFolderId, navigation.authorityFilters, navigation.bookmarkTags, navigation.selectedSpaceIds])
  const openBookmark = useCallback(async (bookmark: KnowledgeBookmark) => {
    if (bookmark.target.kind === 'search') {
      setActiveSearchContext({
        query: bookmark.target.query, mode: bookmark.target.searchMode,
        spaceIds: bookmark.target.spaceIds, authorityKinds: bookmark.target.authorityKinds, tags: bookmark.target.tags,
      })
      setNavigation({
        searchQuery: bookmark.target.query,
        searchMode: bookmark.target.searchMode,
        selectedSpaceIds: bookmark.target.spaceIds,
        authorityFilters: bookmark.target.authorityKinds,
        bookmarkTags: bookmark.target.tags,
      })
      return
    }
    if (bookmark.target.kind === 'workspace') {
      const workspaceId = bookmark.target.workspaceId
      const workspace = namedWorkspaces.data?.items.find((item) => item.id === workspaceId)
      if (workspace) {
        await openNamedWorkspace(workspace)
        setNavigation({ utilityMode: 'workspaces' })
      }
      return
    }
    if (!bookmark.targetDocument) return
    if (bookmark.target.kind === 'graph') {
      openTab({ ...tabFromDescriptor(bookmark.targetDocument), viewMode: 'graph', graphViewport: bookmark.target.viewport })
      const openedWorkspace = useKnowledgeWorkspaceStore.getState()
      const openedPane = openedWorkspace.panes[openedWorkspace.activePaneId]
      const openedTabId = openedPane?.activeTabId
      if (openedPane && openedTabId) {
        setTabViewMode(openedPane.id, openedTabId, 'graph')
        setTabGraphViewport(openedPane.id, openedTabId, bookmark.target.viewport)
      }
      setNavigation({ selectedSpaceIds: bookmark.target.spaceIds })
      if (bookmark.target.rootDocumentId) {
        setGraphBookmarkContext({
          rootDocumentId: bookmark.target.rootDocumentId,
          spaceIds: bookmark.target.spaceIds,
          relationKinds: bookmark.target.relationKinds,
          viewport: bookmark.target.viewport,
        })
      }
      return
    }
    openDescriptor(bookmark.targetDocument)
    if (bookmark.target.kind === 'block') {
      const state = useKnowledgeWorkspaceStore.getState()
      const pane = state.panes[state.activePaneId]
      const tabId = pane?.activeTabId
      if (tabId) state.setFocusedBlock(state.activePaneId, tabId, {
        blockId: bookmark.target.blockId, sourceRevisionId: bookmark.target.sourceRevisionId,
      })
    }
  }, [namedWorkspaces.data?.items, openDescriptor, openNamedWorkspace, openTab, setActiveSearchContext, setGraphBookmarkContext, setNavigation, setTabGraphViewport, setTabViewMode])
  const editBookmark = useCallback(() => undefined, [])
  const updateBookmarkMetadata = useCallback((
    bookmark: KnowledgeBookmark,
    patch: Pick<import('@/lib/api/knowledge-navigation').UpdateBookmarkCommand, 'displayLabel' | 'tags' | 'target'>,
  ) => updateBookmark({
    bookmarkId: bookmark.id,
    command: { expectedRevision: bookmark.revision, ...patch },
  }).then(() => undefined), [updateBookmark])
  const removeBookmark = useCallback((bookmark: KnowledgeBookmark) => {
    void deleteBookmark({ bookmarkId: bookmark.id, command: { expectedRevision: bookmark.revision } })
  }, [deleteBookmark])
  const removeFolder = useCallback((folder: import('@/lib/api/knowledge-navigation').KnowledgeBookmarkFolder, policy: 'move_children' | 'delete_tree') => {
    void deleteFolder({ folderId: folder.id, command: { expectedRevision: folder.revision, childDisposition: policy } })
  }, [deleteFolder])

  useEffect(() => {
    // The measured rail width is a local layout effect, not a user edit.  Do
    // not let it advance the workspace revision before Current Session has
    // hydrated, or the late GET is correctly treated as stale and its V2
    // split/overlay state is discarded.
    if (!workspaceHydrated) return
    const sidebar = sidebarRef.current
    if (!sidebar || !navigation.sidebarVisible) return
    let skipInitialMeasurement = measuredSidebarElementRef.current !== sidebar
    measuredSidebarElementRef.current = sidebar
    const clampWidth = (value: number) => Math.min(640, Math.max(240, Math.round(value)))
    const observer = new ResizeObserver((entries) => {
      // The first observation reports CSS layout, not a user resize. Persisting
      // it made a read-only visit issue a timing-dependent PUT at some
      // viewports. Establish the local baseline first; subsequent observations
      // still capture genuine resizes of the mounted rail.
      if (skipInitialMeasurement) {
        skipInitialMeasurement = false
        return
      }
      const width = entries[0]?.contentRect.width
      if (width && clampWidth(width) !== navigation.sidebarWidth) {
        setNavigation({ sidebarWidth: clampWidth(width) })
      }
    })
    observer.observe(sidebar)
    return () => observer.disconnect()
  }, [navigation.sidebarVisible, navigation.sidebarWidth, setNavigation, workspaceHydrated])

  useEffect(() => {
    setActivePaneElement(paneElementsRef.current[activePaneId] ?? null)
  }, [activePaneId])

  const onPaneElement = useCallback((paneId: string, element: HTMLElement | null) => {
    paneElementsRef.current[paneId] = element
    if (paneId === activePaneId) setActivePaneElement(element)
  }, [activePaneId])

  const closeUtilityDrawer = useCallback(() => {
    setUtilityDrawerOpen(false)
    requestAnimationFrame(() => utilityDrawerTriggerRef.current?.focus())
  }, [])

  const closeIntelligenceDrawer = useCallback(() => {
    setIntelligenceDrawerOpen(false)
    requestAnimationFrame(() => intelligenceDrawerTriggerRef.current?.focus())
  }, [])

  return (
    <div
      ref={workspaceRef}
      className="research-core-canvas flex min-h-0 w-full max-w-full min-w-0 flex-1 flex-col"
      data-testid="knowledge-workspace"
      style={{ '--knowledge-sidebar-width': `${navigation.sidebarWidth}px` } as CSSProperties}
      tabIndex={-1}
    >
      <ResearchCoreFolioFrame header={<>
      <ResearchCoreHeader
        workspaceTitle={t('navigation.knowledge')}
        authoritySummary={authoritySummary}
        saveState={persistence.isPending ? 'Saving locally' : persistence.isError ? 'Save needs attention' : 'Saved locally'}
        readiness={localReadiness}
        memoryPressure={{ state: 'normal', detail: 'Memory pressure not reported' }}
        queuedWorkCount={Number(persistence.isPending) + Number(scanPending)}
        actions={selectedRoot.authority === 'external-vault' ? (
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => setGitHistoryOpen(true)}
            >
              <GitBranch className="mr-2 h-4 w-4" />
              History
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => { void scanVault().catch(() => undefined) }}
              disabled={scanPending}
            >
              <RefreshCw
                className={`mr-2 h-4 w-4 ${scanPending ? 'animate-spin' : ''}`}
              />
              {t('knowledge.scan')}
            </Button>
          </div>
        ) : null}
      />
      <div className="research-core-mode-toolbar border-b px-4 py-2 sm:px-6">
        <KnowledgeModeLauncher
          activePaneId={activePaneId}
          tabs={activePane?.tabs ?? []}
          activeTabId={activeTab?.id ?? null}
          hasUnsavedOverlayDraft={hasUnsavedOverlayDraft}
          availability={modeAvailability}
          onActivateTab={activateTab}
          onOpenMode={openResearchMode}
        />
        <div className="research-core-drawer-triggers" aria-label={t('knowledge.researchDrawers')}>
          <Button
            ref={utilityDrawerTriggerRef}
            type="button"
            size="sm"
            variant="outline"
            aria-controls="research-core-utility-drawer"
            aria-expanded={utilityDrawerOpen}
            aria-label={t('knowledge.openUtilityDrawer')}
            onClick={() => setUtilityDrawerOpen(true)}
            className="research-core-drawer-trigger"
          >
            {t('knowledge.utilityDrawer')}
          </Button>
          <Button
            ref={intelligenceDrawerTriggerRef}
            type="button"
            size="sm"
            variant="outline"
            aria-controls="research-core-intelligence-drawer"
            aria-expanded={intelligenceDrawerOpen}
            aria-label={t('knowledge.openIntelligenceDrawer')}
            onClick={() => setIntelligenceDrawerOpen(true)}
            className="research-core-drawer-trigger"
          >
            {t('knowledge.intelligenceDrawer')}
          </Button>
        </div>
        <div className="mt-3 space-y-1" aria-live="polite">
          {hydration.isLoading && (
            <p role="status" className="text-sm text-muted-foreground">
              {t('knowledge.workspaceLoading')}
            </p>
          )}
          {hydration.isError && (
            <p role="alert" className="text-sm text-destructive">
              {t('knowledge.workspaceLoadError')}
            </p>
          )}
          {persistence.isPending && (
            <p role="status" className="text-sm text-muted-foreground">
              {t('knowledge.workspaceSaving')}
            </p>
          )}
          {persistence.isError && (
            <p role="alert" className="text-sm text-destructive">
              {t('knowledge.workspaceSaveError')}
            </p>
          )}
          {selectedRoot.authority === 'external-vault' && scanError && (
            <p role="alert" className="text-sm text-destructive">
              {t('knowledge.loadError')}
            </p>
          )}
        </div>
      </div>
      </>} index={<>
        {(navigation.sidebarVisible || isNarrowLayout) && <aside
          id="research-core-utility-drawer"
          ref={(element) => { fileTreeRef.current = element; sidebarRef.current = element }}
          className="research-core-utility-drawer flex min-h-64 flex-col gap-4 border-b p-4 lg:border-b-0 lg:border-r"
          aria-label={t('knowledge.utilityDrawer')}
          aria-hidden={isNarrowLayout && !utilityDrawerOpen}
          data-drawer-open={utilityDrawerOpen ? 'true' : 'false'}
          tabIndex={-1}
        >
          <KnowledgeUtilityRail
            mode={navigation.utilityMode}
            sidebarVisible={navigation.sidebarVisible}
            canBookmarkCurrent={Boolean(activeTab?.knowledgeDocumentId)}
            randomPending={randomNotePending}
            drawerCloseLabel={t('knowledge.closeUtilityDrawer')}
            onCloseDrawer={closeUtilityDrawer}
            onNavigationChange={setNavigation}
            onToday={() => { void openTodayOverlay() }}
            onRandomNote={() => { void openRandomNote() }}
            onBookmarkCurrent={() => { void bookmarkCurrentTarget() }}
          />
          {navigation.utilityMode === 'bookmarks' ? (
            <KnowledgeBookmarksPanel
              bookmarks={bookmarks.data?.items || []}
              folders={folders.data?.items || []}
              onOpen={openBookmark}
              onEdit={editBookmark}
              onUpdate={updateBookmarkMetadata}
              onDelete={removeBookmark}
              onSelectFolder={(folderId) => setNavigation({ activeBookmarkFolderId: folderId })}
              onDeleteFolder={removeFolder}
            />
          ) : navigation.utilityMode === 'workspaces' ? (
            <KnowledgeWorkspacesPanel
              workspaces={namedWorkspaces.data?.items || []}
              onSaveCurrentAs={saveCurrentWorkspaceAs}
              onOpen={openNamedWorkspace}
              onRename={renameWorkspace}
              onDuplicate={duplicateNamedWorkspace}
              onReplaceWithCurrent={replaceNamedWorkspace}
              onDelete={deleteNamedWorkspace}
              onRefresh={async () => { await namedWorkspaces.refetch() }}
              commandIntent={workspaceCommandIntent}
            />
          ) : <>
          <label className="text-sm font-medium" htmlFor="vault-mount">
            {t('knowledge.mounts')}
          </label>
          <select
            id="vault-mount"
            value={`${selectedRoot.authority}:${selectedRoot.id}`}
            onChange={(event) => {
              const next = event.target.value
              setSelectedRootState(next === 'overlay:overlay_space:default'
                ? { authority: 'overlay', id: 'overlay_space:default' }
                : { authority: 'external-vault', id: next.replace(/^external-vault:/, '') })
            }}
            className="h-9 rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="overlay:overlay_space:default">{t('knowledge.overlay.name')}</option>
            {mounts.data?.map((mount) => (
              <option key={mount.id} value={`external-vault:${mount.id}`}>
                {mount.name} · {mount.format_mode}
              </option>
            ))}
          </select>
          <OverlayUtilityPanel onOpen={openTab} onNewUnique={openUniqueOverlayDialog} onToday={openTodayOverlay} todayPending={todayOverlayPending} todayError={todayOverlayError} />
          {selectedRoot.authority === 'external-vault' && (mounts.isLoading ? (
            <p className="text-sm text-muted-foreground">
              {t('knowledge.mountsLoading')}
            </p>
          ) : mounts.isError ? (
            <p role="alert" className="text-sm text-destructive">
              {t('knowledge.loadError')}
            </p>
          ) : !mounts.data?.length ? (
            <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              {t('knowledge.noMounts')}
            </p>
          ) : (
            <>
              <div className="rounded-md bg-muted p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{t('knowledge.status')}</span>
                  <Badge variant="outline">{t('knowledge.readOnly')}</Badge>
                </div>
                <p className="mt-1 text-muted-foreground">
                  {selected?.state || t('common.unknown')}
                </p>
              </div>
              {files.isLoading ? (
                <p className="text-sm text-muted-foreground">
                  {t('knowledge.filesLoading')}
                </p>
              ) : files.isError ? (
                <p role="alert" className="text-sm text-destructive">
                  {t('knowledge.loadError')}
                </p>
              ) : (
                <VaultFileTree
                  files={files.data || []}
                  selectedNoteId={selectedNoteId}
                  onSelect={(noteId) => {
                    const file = files.data?.find(
                      (candidate) => candidate.note_id === noteId,
                    )
                    if (file) openFile(file)
                  }}
                />
              )}
            </>
          ))}</>}
        </aside>}
        {navigation.sidebarVisible && <div
          role="separator"
          aria-label="Resize utility sidebar"
          aria-orientation="vertical"
          aria-valuemin={240}
          aria-valuemax={640}
          aria-valuenow={navigation.sidebarWidth}
          tabIndex={0}
          className="hidden w-1 cursor-col-resize bg-border focus-visible:w-2 focus-visible:bg-ring lg:block"
          onKeyDown={(event) => {
            if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
            event.preventDefault()
            setNavigation({ sidebarWidth: Math.min(640, Math.max(240, navigation.sidebarWidth + (event.key === 'ArrowRight' ? 16 : -16))) })
          }}
          onPointerDown={(event: PointerEvent<HTMLDivElement>) => {
            resizeStartRef.current = { x: event.clientX, width: navigation.sidebarWidth }
            if (typeof event.currentTarget.setPointerCapture === 'function') {
              event.currentTarget.setPointerCapture(event.pointerId)
            }
          }}
          onPointerMove={(event: PointerEvent<HTMLDivElement>) => {
            const start = resizeStartRef.current
            if (!start) return
            setNavigation({ sidebarWidth: Math.min(640, Math.max(240, start.width + event.clientX - start.x)) })
          }}
          onPointerUp={() => { resizeStartRef.current = null }}
          onMouseDown={(event) => { resizeStartRef.current = { x: event.clientX, width: navigation.sidebarWidth } }}
          onMouseMove={(event) => {
            const start = resizeStartRef.current
            if (!start) return
            setNavigation({ sidebarWidth: Math.min(640, Math.max(240, start.width + event.clientX - start.x)) })
          }}
          onMouseUp={() => { resizeStartRef.current = null }}
        />}
        </>} workspace={<>
        {!navigation.sidebarVisible && !isNarrowLayout && <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Restore utility sidebar"
          className="absolute left-2 top-2 z-10"
          onClick={() => setNavigation({ sidebarVisible: true })}
        >
          <span aria-hidden="true">›</span>
        </Button>}
        <div className="research-core-main min-h-0 w-full max-w-full min-w-0 overflow-hidden">
          {activeSearchContext && <section aria-label="Active knowledge search" className="border-b px-3 py-2 text-sm">
            <p>{activeSearchContext.mode}: {activeSearchContext.query}</p>
            <p className="text-muted-foreground">Spaces: {activeSearchContext.spaceIds.join(', ') || 'all'} · Authorities: {activeSearchContext.authorityKinds.join(', ') || 'all'}</p>
            <ul aria-label="Knowledge search results">{(activeSearchContext.mode === 'semantic' ? indexedSearch.semantic.data?.results : indexedSearch.text.data?.results)?.map((result) => <li key={result.id}>{result.title}</li>)}</ul>
          </section>}
          <KnowledgeWorkspaceLayout
            onPaneElement={onPaneElement}
            renderPane={(pane) => (
              <KnowledgePaneContent
                pane={pane}
                mounts={mounts.data || []}
                vaultFiles={files.data || []}
                onNavigate={navigate}
              />
            )}
          />
        </div>
        </>} lens={<div ref={linksRef} tabIndex={-1}>
          <KnowledgeIntelligenceRail
            drawerId="research-core-intelligence-drawer"
            drawerLabel={t('knowledge.intelligenceDrawer')}
            drawerOpen={!isNarrowLayout || intelligenceDrawerOpen}
            drawerCloseLabel={t('knowledge.closeIntelligenceDrawer')}
            onCloseDrawer={closeIntelligenceDrawer}
            activeContext={{
              evidence: activeTab?.knowledgeDocumentId
                ? 'Active document is ready for evidence review'
                : 'Select a document to review evidence',
              properties: activeTab ? `${activeTab.sourceAuthority === 'overlay' ? 'App-owned' : 'External read-only'} source` : 'No active source',
              production: 'No production queued',
            }}
            initialPanel="connections"
            onNavigate={navigate}
          />
        </div>} overlays={<>
      <KnowledgeCommandBridge
        workspaceRef={workspaceRef}
        activePaneElement={activePaneElement}
        fileTreeRef={fileTreeRef}
        linksRef={linksRef}
        selectedVaultId={selectedRoot.authority === 'external-vault' ? selectedRoot.id : null}
        scanSelectedVault={scanSelectedVault}
        openTodayOverlay={openTodayOverlay}
        openUniqueOverlayDialog={openUniqueOverlayDialog}
        bookmarkCurrentTarget={bookmarkCurrentTarget}
        openBookmarks={openBookmarks}
        randomNote={openRandomNote}
        openWorkspaces={openWorkspaces}
        saveWorkspaceAs={requestSaveWorkspaceAs}
        replaceWorkspace={requestReplaceWorkspace}
        toggleMetrics={toggleMetrics}
        researchModeAvailability={modeAvailability}
        openResearchMode={openResearchModeForActivePane}
      />
      <KnowledgeQuickSwitcher mounts={mounts.data || []} searchMode={navigation.searchMode} onBookmarkSearch={(query, mode) => { void bookmarkSearch(query, mode) }} />
      <CreateUniqueNoteDialog
        open={uniqueDialogOpen}
        onOpenChange={setUniqueDialogOpen}
        onOpen={openTab}
      />
      <WorkspaceRestoreDialog
        plan={pendingWorkspaceRestore}
        applying={restoreApplying}
        error={restoreError}
        onOpenAvailable={() => {
          if (pendingWorkspaceRestore) void applyRestorePlan(pendingWorkspaceRestore)
        }}
        onCancel={() => {
          if (restoreApplying) return
          setRestoreError(null)
          setPendingWorkspaceRestore(null)
          const invoker = restoreInvokerRef.current
          restoreInvokerRef.current = null
          setTimeout(() => invoker?.isConnected && invoker.focus(), 0)
        }}
      />
      <VaultGitHistoryDialog
        open={gitHistoryOpen}
        onOpenChange={setGitHistoryOpen}
        vaultId={selectedRoot.authority === 'external-vault' ? selectedRoot.id : ''}
        vaultName={selectedRoot.authority === 'external-vault' ? (mounts.data?.find(m => m.id === selectedRoot.id)?.name || 'Vault') : 'Vault'}
      />
      </>} />
    </div>
  )
}
