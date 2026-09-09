'use client'

import { useCallback, useEffect, useId, useMemo, useRef, type KeyboardEvent, type MouseEvent } from 'react'
import { Background, Controls, ReactFlow, type Edge, type Node, type Viewport } from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import type { VaultGraph as VaultGraphData, VaultLink } from '@/lib/api/vault'
import { TurnIntoPodcastAction } from '@/components/podcasts/TurnIntoPodcastAction'
import { useTranslation } from '@/lib/hooks/use-translation'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'
import { GraphAtlasFrame } from '@/components/deeper-notebook/GraphAtlasFrame'
import './vault.css'

const EMPTY_STRING_ARRAY: string[] = []

type BookmarkContext = { rootDocumentId: string; spaceIds: string[]; relationKinds: string[]; viewport: Viewport }

type ArrowDirection = 'ArrowUp' | 'ArrowDown' | 'ArrowLeft' | 'ArrowRight'

// Finds the nearest node whose position lies in `direction` from `currentId`:
// the delta along that axis must have the right sign and be at least as large
// (in magnitude) as the delta on the cross axis; ties broken by Euclidean distance.
function findNearestNodeInDirection(
  currentId: string,
  direction: ArrowDirection,
  positions: Map<string, { x: number; y: number }>,
): string | null {
  const current = positions.get(currentId)
  if (!current) return null
  let bestId: string | null = null
  let bestDistance = Infinity
  positions.forEach((position, id) => {
    if (id === currentId) return
    const dx = position.x - current.x
    const dy = position.y - current.y
    const alongAxis = direction === 'ArrowRight' ? dx : direction === 'ArrowLeft' ? -dx : direction === 'ArrowDown' ? dy : -dy
    const crossAxis = direction === 'ArrowRight' || direction === 'ArrowLeft' ? dy : dx
    if (alongAxis <= 0 || alongAxis < Math.abs(crossAxis)) return
    const distance = Math.hypot(dx, dy)
    if (distance < bestDistance) {
      bestDistance = distance
      bestId = id
    }
  })
  return bestId
}

function bookmarkContextsEqual(left: BookmarkContext | null, right: BookmarkContext): boolean {
  if (!left) return false
  return left.rootDocumentId === right.rootDocumentId
    && left.viewport.x === right.viewport.x
    && left.viewport.y === right.viewport.y
    && left.viewport.zoom === right.viewport.zoom
    && left.spaceIds.length === right.spaceIds.length
    && left.spaceIds.every((spaceId, index) => spaceId === right.spaceIds[index])
    && left.relationKinds.length === right.relationKinds.length
    && left.relationKinds.every((relationKind, index) => relationKind === right.relationKinds[index])
}

export function VaultGraph({ graph, unresolved, onNavigate, viewport, onMoveEnd, rootDocumentId, spaceIds = EMPTY_STRING_ARRAY, relationKinds = EMPTY_STRING_ARRAY, onBookmarkContext }: {
  graph?: VaultGraphData
  unresolved: VaultLink[]
  onNavigate: (noteId: string) => void
  viewport?: Viewport
  onMoveEnd?: (viewport: Viewport) => void
  rootDocumentId?: string | null
  spaceIds?: string[]
  relationKinds?: string[]
  onBookmarkContext?: (context: BookmarkContext) => void
}) {
  const { t } = useTranslation()
  const openPodcastReview = usePodcastStudioStore((state) => state.open)
  const { nodes, edges } = useMemo(() => {
    const source = graph?.nodes ?? []
    const flowNodes: Node[] = source.map((node, index) => ({ id: node.id, data: { label: node.title || node.id }, position: { x: (index % 3) * 240, y: Math.floor(index / 3) * 130 }, draggable: false, className: `vault-node--${node.source_format || 'markdown'}` }))
    const allowedRelations = relationKinds.length ? new Set(relationKinds) : null
    const flowEdges: Edge[] = (graph?.edges ?? []).filter((edge) => !allowedRelations || allowedRelations.has(edge.kind || 'related')).map((edge) => ({ id: edge.id, source: edge.source, target: edge.target, className: 'vault-edge--resolved' }))
    unresolved.forEach((link, index) => {
      const id = `unresolved:${link.id}`
      flowNodes.push({ id, data: { label: link.target_text }, position: { x: 720, y: index * 130 }, draggable: false, selectable: false, className: 'vault-node--unresolved' })
      flowEdges.push({ id: `unresolved-edge:${link.id}`, source: link.source_note_id, target: id, className: 'vault-edge--unresolved' })
    })
    return { nodes: flowNodes, edges: flowEdges }
  }, [graph, relationKinds, unresolved])
  const liveRelationKinds = useMemo(() => relationKinds.length
    ? relationKinds
    : [...new Set((graph?.edges ?? []).map((edge) => edge.kind || 'related'))].filter((kind) => /^[A-Za-z0-9_.:-]{1,64}$/.test(kind)), [graph, relationKinds])
  const lastBookmarkContext = useRef<BookmarkContext | null>(null)
  useEffect(() => {
    if (!rootDocumentId || !onBookmarkContext || !viewport) return
    const context = { rootDocumentId, spaceIds, relationKinds: liveRelationKinds, viewport }
    if (bookmarkContextsEqual(lastBookmarkContext.current, context)) return
    lastBookmarkContext.current = context
    onBookmarkContext(context)
  }, [liveRelationKinds, onBookmarkContext, rootDocumentId, spaceIds, viewport])
  const wrapperRef = useRef<HTMLDivElement>(null)
  const keyboardHintId = useId()
  const nodePositions = useMemo(() => new Map(nodes.map((node) => [node.id, node.position])), [nodes])
  // v0.8.116 — React Flow's own keydown handler on a focused node only toggles
  // selection (Enter/Space never fires onNodeClick), and arrow keys drag the
  // selected node instead of moving focus. We handle Enter/Space/Arrow* here,
  // at the wrapper level, and preventDefault so React Flow's own handling
  // doesn't also run for the keys we've claimed.
  const handleKeyDown = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    const nodeEl = (event.target as HTMLElement).closest('[data-id]')
    const id = nodeEl?.getAttribute('data-id')
    if (!id) return
    if (event.key === 'Enter' || event.key === ' ') {
      if (!id.startsWith('unresolved:')) {
        onNavigate(id)
        event.preventDefault()
      }
      return
    }
    if (event.key === 'ArrowUp' || event.key === 'ArrowDown' || event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      const nextId = findNearestNodeInDirection(id, event.key, nodePositions)
      if (!nextId) return
      event.preventDefault()
      wrapperRef.current?.querySelector<HTMLElement>(`[data-id="${CSS.escape(nextId)}"]`)?.focus()
    }
  }, [nodePositions, onNavigate])
  if (!nodes.length) return <p className="flex h-full items-center justify-center rounded-md border border-dashed p-6 text-sm text-muted-foreground">{t('knowledge.noGraphLinks')}</p>
  const podcastDocumentIds = [...new Set(
    (graph?.nodes ?? [])
      .map((node) => node.knowledge_document_id)
      .filter((documentId): documentId is string => Boolean(documentId)),
  )].slice(0, 128)
  return <GraphAtlasFrame
    actions={<TurnIntoPodcastAction
      selection={podcastDocumentIds.length > 0
        ? { kind: 'graph_selection', documentIds: podcastDocumentIds }
        : undefined}
      destination="quick"
      label="Turn graph into podcast"
      disabledReason={podcastDocumentIds.length > 0
        ? undefined
        : 'This graph has no unified document selection yet.'}
      onOpen={openPodcastReview}
    />}
    legend={<ul className="space-y-1 text-sm">
      <li>{graph?.nodes.length ?? 0} connected source{(graph?.nodes.length ?? 0) === 1 ? '' : 's'}</li>
      <li>{liveRelationKinds.length} relation type{liveRelationKinds.length === 1 ? '' : 's'}</li>
      {unresolved.length ? <li>{unresolved.length} unresolved link{unresolved.length === 1 ? '' : 's'}</li> : null}
    </ul>}
    canvas={<div ref={wrapperRef} className="vault-flow h-[480px] overflow-hidden rounded-md border" aria-label={t('knowledge.localGraph')} aria-describedby={keyboardHintId} onKeyDown={handleKeyDown}><p id={keyboardHintId} className="sr-only">{t('knowledge.graphKeyboardHint')}</p><ReactFlow nodes={nodes} edges={edges} viewport={viewport} fitView={!viewport} nodesConnectable={false} nodesDraggable={false} onConnect={() => undefined} onMoveEnd={(_event, nextViewport) => onMoveEnd?.(nextViewport)} onNodeClick={(_event: MouseEvent, node) => { if (!node.id.startsWith('unresolved:')) onNavigate(node.id) }} proOptions={{ hideAttribution: true }}><Background /><Controls showInteractive={false} /></ReactFlow></div>}
    inspector={<p className="text-sm text-muted-foreground">Open a connected note to inspect it in the existing workspace.</p>}
  />
}
