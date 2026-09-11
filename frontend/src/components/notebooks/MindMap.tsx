'use client'

// v0.8.83 — mind-map view (improvement roadmap, Batch 3). Renders the notebook
// as a hub with its sources and notes around it (radial layout, no extra layout
// dep) using React Flow. Clicking a source/note node deep-links to it via the
// callbacks. Loaded with next/dynamic ssr:false by the caller (React Flow needs
// the DOM). Data comes from GET /api/notebooks/{id}/graph.
import { useCallback, useMemo, type CSSProperties, type MouseEvent } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  type Node,
  type Edge,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Loader2 } from 'lucide-react'

import { useNotebookGraph } from '@/lib/hooks/use-notebook-graph'
import { useTranslation } from '@/lib/hooks/use-translation'

interface MindMapProps {
  notebookId: string
  /** Only fetch when the host (dialog) is actually open. */
  open?: boolean
  onSelectSource?: (sourceId: string) => void
  onSelectNote?: (noteId: string) => void
  onSelectArtifact?: (artifactId: string) => void
}

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

export default function MindMap({
  notebookId,
  open = true,
  onSelectSource,
  onSelectNote,
  onSelectArtifact,
}: MindMapProps) {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useNotebookGraph(notebookId, open)

  const typeById = useMemo(() => {
    const m = new Map<string, string>()
    data?.nodes.forEach((n) => m.set(n.id, n.type))
    return m
  }, [data])

  const { nodes, edges } = useMemo(() => {
    if (!data) return { nodes: [] as Node[], edges: [] as Edge[] }
    const hub = data.nodes.find((n) => n.type === 'notebook')
    const spokes = data.nodes.filter((n) => n.type !== 'notebook')
    const radius = Math.max(260, spokes.length * 32)

    const rfNodes: Node[] = []
    if (hub) {
      rfNodes.push({
        id: hub.id,
        position: { x: 0, y: 0 },
        data: { label: hub.label },
        style: nodeStyle('notebook'),
        draggable: false,
      })
    }
    spokes.forEach((n, i) => {
      const angle = (i / Math.max(1, spokes.length)) * Math.PI * 2
      rfNodes.push({
        id: n.id,
        position: { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius },
        data: { label: n.label },
        style: nodeStyle(n.type),
      })
    })

    const rfEdges: Edge[] = data.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      style: { stroke: 'var(--dn-graph-edge)', strokeWidth: 1.5 },
    }))
    return { nodes: rfNodes, edges: rfEdges }
  }, [data])

  const onNodeClick = useCallback(
    (_event: MouseEvent, node: Node) => {
      const type = typeById.get(node.id)
      if (type === 'source') onSelectSource?.(node.id)
      else if (type === 'note') onSelectNote?.(node.id)
      else if (type === 'studio_artifact') onSelectArtifact?.(node.id)
    },
    [typeById, onSelectSource, onSelectNote, onSelectArtifact]
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

  return (
    <div className="h-full w-full">
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
        <MiniMap pannable zoomable />
      </ReactFlow>
    </div>
  )
}
