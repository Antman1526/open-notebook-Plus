'use client'

// v0.8.83 — mind-map view (improvement roadmap, Batch 3). Renders the notebook
// as a hub with its sources and notes around it (radial layout, no extra layout
// dep) using React Flow. Clicking a source/note node deep-links to it via the
// callbacks. Loaded with next/dynamic ssr:false by the caller (React Flow needs
// the DOM). Data comes from GET /api/notebooks/{id}/graph.
// v0.8.124 — canvas search/dim, cluster-by-type layout, and a media preview
// popover for podcast_audio/slide_deck studio_artifact nodes (improvement
// roadmap).
// v0.8.125 — source/note node preview on a plain click (Shift-click keeps the
// direct navigation), search-to-focus (Enter fits matches, arrow keys step
// through them), and per-notebook canvas state persisted via
// lib/stores/mind-map-store.ts (improvement roadmap). useReactFlow() needs a
// ReactFlowProvider ancestor, so the exported component now wraps the canvas
// in one and keeps the actual implementation in an inner component.
import { useCallback, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent } from 'react'
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Loader2 } from 'lucide-react'

import { useNotebookGraph } from '@/lib/hooks/use-notebook-graph'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'
import { useMindMapStore, DEFAULT_MIND_MAP_NOTEBOOK_STATE, type MindMapFilterType } from '@/lib/stores/mind-map-store'
import MindMapNodePreview, { type MindMapNodePreviewAnchor, type MindMapNodeType } from './MindMapNodePreview'

interface MindMapProps {
  notebookId: string
  /** Only fetch when the host (dialog) is actually open. */
  open?: boolean
  onSelectSource?: (sourceId: string) => void
  onSelectNote?: (noteId: string) => void
  onSelectArtifact?: (artifactId: string) => void
}

type FilterType = MindMapFilterType

const NODE_BG: Record<string, string> = {
  notebook: 'var(--dn-graph-fallback)',
  source: 'var(--dn-graph-source)',
  note: 'var(--dn-graph-note)',
  studio_artifact: 'var(--dn-graph-artifact, var(--dn-graph-note))',
}

const NODE_FG: Record<string, string> = {
  notebook: 'var(--dn-graph-fallback-foreground)',
  source: 'var(--dn-graph-source-foreground)',
  note: 'var(--dn-graph-note-foreground)',
  studio_artifact: 'var(--dn-graph-artifact-foreground, var(--dn-graph-note-foreground))',
}

function nodeStyle(type: string): CSSProperties {
  return {
    background: NODE_BG[type] ?? 'var(--dn-graph-fallback)',
    color: NODE_FG[type] ?? 'var(--dn-graph-fallback-foreground)',
    border: 'none',
    borderRadius: type === 'notebook' ? 12 : 8,
    padding: type === 'notebook' ? '10px 16px' : '6px 12px',
    fontSize: type === 'notebook' ? 14 : 12,
    fontWeight: type === 'notebook' ? 600 : 500,
    maxWidth: 220,
    textAlign: 'center',
    cursor: type === 'notebook' ? 'default' : 'pointer',
  }
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full w-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  )
}

export default function MindMap(props: MindMapProps) {
  return (
    <ReactFlowProvider>
      <MindMapCanvas {...props} />
    </ReactFlowProvider>
  )
}

function MindMapCanvas({
  notebookId,
  open = true,
  onSelectSource,
  onSelectNote,
  onSelectArtifact,
}: MindMapProps) {
  const { t } = useTranslation()
  const { fitView } = useReactFlow()

  // v0.8.125 — per-notebook canvas state (filter/query/clusterByType) is the
  // store, not local state, so it survives dialog close/reopen and app
  // restarts without a hydration race: the store starts empty and every
  // reader falls back to DEFAULT_MIND_MAP_NOTEBOOK_STATE until a value is
  // written for this notebookId.
  const notebookState = useMindMapStore(
    (state) => state.byNotebook[notebookId] ?? DEFAULT_MIND_MAP_NOTEBOOK_STATE
  )
  const setStoredState = useMindMapStore((state) => state.setState)
  const { filter, query, clusterByType } = notebookState

  const setFilter = useCallback(
    (value: FilterType) => setStoredState(notebookId, { filter: value }),
    [notebookId, setStoredState]
  )
  const setQuery = useCallback(
    (value: string) => setStoredState(notebookId, { query: value }),
    [notebookId, setStoredState]
  )
  const setClusterByType = useCallback(
    (updater: boolean | ((prev: boolean) => boolean)) => {
      const next = typeof updater === 'function' ? updater(clusterByType) : updater
      setStoredState(notebookId, { clusterByType: next })
    },
    [notebookId, setStoredState, clusterByType]
  )

  const [activeMatchId, setActiveMatchId] = useState<string | null>(null)
  const [preview, setPreview] = useState<{
    id: string
    nodeType: MindMapNodeType
    artifactType?: string | null
    anchor: MindMapNodePreviewAnchor
  } | null>(null)
  const canvasRef = useRef<HTMLDivElement>(null)
  const { data, isLoading, isError } = useNotebookGraph(notebookId, open)

  const typeById = useMemo(() => {
    const m = new Map<string, string>()
    data?.nodes.forEach((n) => m.set(n.id, n.type))
    return m
  }, [data])

  const artifactTypeById = useMemo(() => {
    const m = new Map<string, string | null | undefined>()
    data?.nodes.forEach((n) => m.set(n.id, n.artifact_type))
    return m
  }, [data])

  const counts = useMemo(() => {
    let sources = 0
    let notes = 0
    let artifacts = 0
    if (data?.nodes) {
      for (const n of data.nodes) {
        if (n.type === 'source') sources++
        else if (n.type === 'note') notes++
        else if (n.type === 'studio_artifact') artifacts++
      }
    }
    return {
      all: sources + notes + artifacts,
      sources,
      notes,
      artifacts,
    }
  }, [data])

  const spokes = useMemo(() => {
    if (!data) return []
    return data.nodes.filter((n) => {
      if (n.type === 'notebook') return false
      if (filter === 'all') return true
      return n.type === filter
    })
  }, [data, filter])

  const normalizedQuery = query.trim().toLowerCase()

  // v0.8.124 — canvas search: the hub always matches (it's not a search
  // target, just always "in view"); spokes match on a case-insensitive
  // substring of their label.
  const matchesQuery = useCallback(
    (label: string) => !normalizedQuery || label.toLowerCase().includes(normalizedQuery),
    [normalizedQuery]
  )

  // v0.8.125 — matched node ids, in spoke order, for search-to-focus
  // (Enter fits them all; arrow keys step through them one at a time).
  const matchedNodeIds = useMemo(() => {
    if (!normalizedQuery) return []
    return spokes.filter((n) => matchesQuery(n.label)).map((n) => n.id)
  }, [spokes, normalizedQuery, matchesQuery])

  const matchCount = normalizedQuery ? matchedNodeIds.length : spokes.length

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] }
    const hub = data.nodes.find((n) => n.type === 'notebook')

    const rfNodes: Node[] = []
    const visibleNodeIds = new Set<string>()
    const matchedIds = new Set<string>()

    if (hub) {
      visibleNodeIds.add(hub.id)
      matchedIds.add(hub.id)
      rfNodes.push({
        id: hub.id,
        position: { x: 0, y: 0 },
        data: { label: hub.label },
        style: nodeStyle('notebook'),
        draggable: false,
      })
    }

    // v0.8.124 — cluster-by-type: one satellite center per present type,
    // evenly spaced around the hub; each type's nodes lay out on a
    // sub-circle around its own satellite, using the same angle formula as
    // the single-circle layout below. Applied to `spokes`, i.e. after the
    // existing type filter.
    const positionsById = new Map<string, { x: number; y: number }>()
    if (clusterByType) {
      const typeOrder: Array<'source' | 'note' | 'studio_artifact'> = ['source', 'note', 'studio_artifact']
      const groups = typeOrder
        .map((type) => ({ type, nodes: spokes.filter((n) => n.type === type) }))
        .filter((group) => group.nodes.length > 0)
      const satelliteRadius = Math.max(320, spokes.length * 28)
      groups.forEach((group, gi) => {
        const satelliteAngle = (gi / Math.max(1, groups.length)) * Math.PI * 2
        const center = {
          x: Math.cos(satelliteAngle) * satelliteRadius,
          y: Math.sin(satelliteAngle) * satelliteRadius,
        }
        const subRadius = Math.max(120, group.nodes.length * 26)
        group.nodes.forEach((n, ni) => {
          const angle = (ni / Math.max(1, group.nodes.length)) * Math.PI * 2
          positionsById.set(n.id, {
            x: center.x + Math.cos(angle) * subRadius,
            y: center.y + Math.sin(angle) * subRadius,
          })
        })
      })
    } else {
      const radius = Math.max(260, spokes.length * 32)
      spokes.forEach((n, i) => {
        const angle = (i / Math.max(1, spokes.length)) * Math.PI * 2
        positionsById.set(n.id, { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius })
      })
    }

    spokes.forEach((n) => {
      visibleNodeIds.add(n.id)
      const matches = matchesQuery(n.label)
      if (matches) matchedIds.add(n.id)
      const searchStyle: CSSProperties = normalizedQuery
        ? matches
          ? { border: n.id === activeMatchId ? '3px solid var(--dn-graph-edge)' : '2px solid var(--dn-graph-edge)' }
          : { opacity: 0.25 }
        : {}
      rfNodes.push({
        id: n.id,
        position: positionsById.get(n.id) ?? { x: 0, y: 0 },
        data: { label: n.label },
        style: { ...nodeStyle(n.type), ...searchStyle },
      })
    })

    const rfEdges: Edge[] = data.edges
      .filter((e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target))
      .map((e, i) => {
        const dimmed = normalizedQuery && !matchedIds.has(e.source) && !matchedIds.has(e.target)
        return {
          id: `e${i}`,
          source: e.source,
          target: e.target,
          style: {
            stroke: 'var(--dn-graph-edge)',
            strokeWidth: 1.5,
            ...(dimmed ? { opacity: 0.2 } : {}),
          },
        }
      })
    return { nodes: rfNodes, edges: rfEdges }
  }, [data, spokes, clusterByType, normalizedQuery, matchesQuery, activeMatchId])

  // v0.8.125 — a plain click on a source/note node opens the inline preview
  // instead of navigating away; Shift-click still deep-links directly, same
  // as the studio_artifact preview added in v0.8.124.
  const onNodeClick = useCallback(
    (event: MouseEvent, node: Node) => {
      const type = typeById.get(node.id)
      if (type === 'source') {
        if (event.shiftKey) {
          onSelectSource?.(node.id)
          return
        }
        const rect = canvasRef.current?.getBoundingClientRect()
        const anchor = {
          x: rect ? event.clientX - rect.left : 0,
          y: rect ? event.clientY - rect.top : 0,
        }
        setPreview({ id: node.id, nodeType: 'source', anchor })
        return
      }
      if (type === 'note') {
        if (event.shiftKey) {
          onSelectNote?.(node.id)
          return
        }
        const rect = canvasRef.current?.getBoundingClientRect()
        const anchor = {
          x: rect ? event.clientX - rect.left : 0,
          y: rect ? event.clientY - rect.top : 0,
        }
        setPreview({ id: node.id, nodeType: 'note', anchor })
        return
      }
      if (type === 'studio_artifact') {
        const artifactType = artifactTypeById.get(node.id)
        const previewable = artifactType === 'podcast_audio' || artifactType === 'slide_deck'
        if (previewable && !event.shiftKey) {
          const rect = canvasRef.current?.getBoundingClientRect()
          const anchor = {
            x: rect ? event.clientX - rect.left : 0,
            y: rect ? event.clientY - rect.top : 0,
          }
          setPreview({ id: node.id, nodeType: 'studio_artifact', artifactType, anchor })
          return
        }
        onSelectArtifact?.(node.id)
      }
    },
    [typeById, artifactTypeById, onSelectSource, onSelectNote, onSelectArtifact]
  )

  // v0.8.125 — search-to-focus: Enter fits the view to every current match;
  // Arrow Up/Down step the active match (wrapping around) and move DOM focus
  // to it, the same nearest-node-by-id model VaultGraph.tsx uses; Escape
  // clears the query entirely.
  const handleSearchKeyDown = useCallback(
    (event: KeyboardEvent<HTMLInputElement>) => {
      if (event.key === 'Enter') {
        if (matchedNodeIds.length === 0) return
        fitView({ nodes: matchedNodeIds.map((id) => ({ id })), padding: 0.3, duration: 300 })
        return
      }
      if (event.key === 'Escape') {
        setQuery('')
        setActiveMatchId(null)
        return
      }
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        if (matchedNodeIds.length === 0) return
        event.preventDefault()
        const currentIndex = activeMatchId ? matchedNodeIds.indexOf(activeMatchId) : -1
        const delta = event.key === 'ArrowDown' ? 1 : -1
        const nextIndex = (currentIndex + delta + matchedNodeIds.length) % matchedNodeIds.length
        const nextId = matchedNodeIds[nextIndex]
        setActiveMatchId(nextId)
        canvasRef.current?.querySelector<HTMLElement>(`[data-id="${CSS.escape(nextId)}"]`)?.focus()
      }
    },
    [matchedNodeIds, activeMatchId, fitView, setQuery]
  )

  const minimapNodeColor = useCallback(
    (node: Node) => {
      const type = typeById.get(node.id) ?? 'notebook'
      return NODE_BG[type] ?? 'var(--dn-graph-fallback)'
    },
    [typeById]
  )

  if (isLoading) {
    return (
      <Centered>
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </Centered>
    )
  }
  if (isError) {
    return <Centered>{t('mindMap.error', { defaultValue: 'Could not load the mind map.' })}</Centered>
  }
  if (!data || data.nodes.length <= 1) {
    return (
      <Centered>
        {t('mindMap.empty', {
          defaultValue: 'Add sources or notes to this notebook to see its mind map.',
        })}
      </Centered>
    )
  }

  const activeMatchIndex = activeMatchId ? matchedNodeIds.indexOf(activeMatchId) : -1

  return (
    <div ref={canvasRef} className="relative h-full w-full">
      <div className="absolute top-3 left-4 z-10 flex flex-wrap items-center gap-1.5 rounded-lg border bg-background/90 p-1 backdrop-blur-xs shadow-xs">
        <button
          type="button"
          onClick={() => setFilter('all')}
          className={cn(
            'inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            filter === 'all'
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
          )}
          aria-pressed={filter === 'all'}
        >
          {t('mindMap.filterAll', { defaultValue: 'All ({count})' }).replace('{count}', String(counts.all))}
        </button>
        <button
          type="button"
          onClick={() => setFilter('source')}
          className={cn(
            'inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            filter === 'source'
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
          )}
          aria-pressed={filter === 'source'}
        >
          {t('mindMap.filterSources', { defaultValue: 'Sources ({count})' }).replace('{count}', String(counts.sources))}
        </button>
        <button
          type="button"
          onClick={() => setFilter('note')}
          className={cn(
            'inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            filter === 'note'
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
          )}
          aria-pressed={filter === 'note'}
        >
          {t('mindMap.filterNotes', { defaultValue: 'Notes ({count})' }).replace('{count}', String(counts.notes))}
        </button>
        <button
          type="button"
          onClick={() => setFilter('studio_artifact')}
          className={cn(
            'inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            filter === 'studio_artifact'
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
          )}
          aria-pressed={filter === 'studio_artifact'}
        >
          {t('mindMap.filterArtifacts', { defaultValue: 'Artifacts ({count})' }).replace('{count}', String(counts.artifacts))}
        </button>
        <input
          type="text"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value)
            setActiveMatchId(null)
          }}
          onKeyDown={handleSearchKeyDown}
          aria-label={t('mindMap.searchLabel', { defaultValue: 'Search nodes' })}
          placeholder={t('mindMap.searchPlaceholder', { defaultValue: 'Search…' })}
          title={t('mindMap.focusHint', { defaultValue: 'Enter to focus matches, arrows to step' })}
          className="h-[26px] w-32 rounded-md border bg-background px-2 text-xs outline-none focus-visible:ring-1 focus-visible:ring-ring"
        />
        {normalizedQuery && (
          <span className="text-xs text-muted-foreground">
            {t('mindMap.matches', { defaultValue: '{count} matches' }).replace('{count}', String(matchCount))}
          </span>
        )}
        {activeMatchIndex >= 0 && (
          <span className="text-xs text-muted-foreground">
            {t('mindMap.matchPosition', { defaultValue: '{index} of {count}' })
              .replace('{index}', String(activeMatchIndex + 1))
              .replace('{count}', String(matchCount))}
          </span>
        )}
        <button
          type="button"
          onClick={() => setClusterByType((v) => !v)}
          aria-pressed={clusterByType}
          className={cn(
            'inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium transition-colors',
            clusterByType
              ? 'bg-primary text-primary-foreground shadow-xs'
              : 'text-muted-foreground hover:bg-accent hover:text-foreground'
          )}
        >
          {t('mindMap.clusterByType', { defaultValue: 'Cluster by type' })}
        </button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodeClick={onNodeClick}
        fitView
        minZoom={0.2}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={minimapNodeColor} />
      </ReactFlow>
      {preview && (
        <MindMapNodePreview
          notebookId={notebookId}
          nodeType={preview.nodeType}
          nodeId={preview.id}
          artifactType={preview.artifactType}
          anchor={preview.anchor}
          onClose={() => setPreview(null)}
          onOpenSource={(id) => {
            setPreview(null)
            onSelectSource?.(id)
          }}
          onOpenNote={(id) => {
            setPreview(null)
            onSelectNote?.(id)
          }}
          onOpenArtifact={(id) => {
            setPreview(null)
            onSelectArtifact?.(id)
          }}
        />
      )}
    </div>
  )
}
