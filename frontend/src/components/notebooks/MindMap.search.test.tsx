// v0.8.124 — canvas search/dim, cluster-by-type layout, and preview-vs-navigate
// click routing for the mind map (improvement roadmap).
import React from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const reactFlowProps = vi.hoisted(() => vi.fn())
const useNotebookGraph = vi.hoisted(() => vi.fn())
const mindMapNodePreviewProps = vi.hoisted(() => vi.fn())

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  ReactFlow: (props: { children?: React.ReactNode } & Record<string, unknown>) => {
    reactFlowProps(props)
    return <div data-testid="react-flow">{props.children}</div>
  },
}))

vi.mock('@xyflow/react/dist/style.css', () => ({}))

vi.mock('@/lib/hooks/use-notebook-graph', () => ({
  useNotebookGraph: (...args: unknown[]) => useNotebookGraph(...args),
}))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? '' }),
}))

vi.mock('./MindMapNodePreview', () => ({
  default: (props: Record<string, unknown>) => {
    mindMapNodePreviewProps(props)
    return <div data-testid="mind-map-node-preview" />
  },
}))

import MindMap from './MindMap'

type CapturedNode = { id: string; position: { x: number; y: number }; style: React.CSSProperties }
type CapturedEdge = { source: string; target: string; style: React.CSSProperties }

function latestProps() {
  return reactFlowProps.mock.calls.at(-1)?.[0] as {
    nodes: CapturedNode[]
    edges: CapturedEdge[]
    onNodeClick: (event: unknown, node: { id: string }) => void
  }
}

describe('MindMap canvas search and clustering', () => {
  const sampleData = {
    nodes: [
      { id: 'notebook:one', type: 'notebook', label: 'Notebook' },
      { id: 'source:one', type: 'source', label: 'Alpha Source' },
      { id: 'source:two', type: 'source', label: 'Beta Source' },
      { id: 'note:one', type: 'note', label: 'Alpha Note' },
      { id: 'artifact:pod', type: 'studio_artifact', artifact_type: 'podcast_audio', label: 'Audio Overview' },
      { id: 'artifact:rep', type: 'studio_artifact', artifact_type: 'report', label: 'Report X' },
    ],
    edges: [
      { source: 'notebook:one', target: 'source:one' },
      { source: 'notebook:one', target: 'source:two' },
      { source: 'notebook:one', target: 'note:one' },
      { source: 'notebook:one', target: 'artifact:pod' },
      { source: 'notebook:one', target: 'artifact:rep' },
    ],
  }

  function setup() {
    useNotebookGraph.mockReturnValue({ data: sampleData, isLoading: false, isError: false })
    const onSelectArtifact = vi.fn()
    render(<MindMap notebookId="notebook:one" onSelectArtifact={onSelectArtifact} />)
    return { onSelectArtifact }
  }

  it('dims non-matching nodes on search and shows a match count, without removing nodes', () => {
    setup()

    const input = screen.getByLabelText('Search nodes')
    fireEvent.change(input, { target: { value: 'Alpha' } })

    const props = latestProps()
    // Still every node present — search never removes nodes from the layout.
    expect(props.nodes).toHaveLength(6)

    const byId = (id: string) => props.nodes.find((n) => n.id === id)

    // Matches: hub (always), "Alpha Source", "Alpha Note".
    expect(byId('source:one')?.style.opacity).toBeUndefined()
    expect(byId('note:one')?.style.opacity).toBeUndefined()
    expect(byId('source:one')?.style.border).toBe('2px solid var(--dn-graph-edge)')

    // Misses get dimmed, no stronger border.
    expect(byId('source:two')?.style.opacity).toBe(0.25)
    expect(byId('artifact:pod')?.style.opacity).toBe(0.25)
    expect(byId('artifact:rep')?.style.opacity).toBe(0.25)

    // Match count is scoped to spokes (2 of 5 under the "all" filter).
    expect(screen.getByText('2 matches')).toBeInTheDocument()
  })

  it('clusters same-type nodes closer together than to other types when toggled on', () => {
    setup()

    fireEvent.click(screen.getByRole('button', { name: 'Cluster by type' }))

    const props = latestProps()
    const pos = (id: string) => props.nodes.find((n) => n.id === id)!.position
    const dist = (a: { x: number; y: number }, b: { x: number; y: number }) =>
      Math.hypot(a.x - b.x, a.y - b.y)

    const sourceSourceDist = dist(pos('source:one'), pos('source:two'))
    const sourceOtherDist = dist(pos('source:one'), pos('note:one'))
    expect(sourceSourceDist).toBeLessThan(sourceOtherDist)

    const artifactArtifactDist = dist(pos('artifact:pod'), pos('artifact:rep'))
    const artifactOtherDist = dist(pos('artifact:pod'), pos('source:one'))
    expect(artifactArtifactDist).toBeLessThan(artifactOtherDist)
  })

  it('opens the preview instead of navigating for a podcast_audio artifact node', () => {
    const { onSelectArtifact } = setup()

    const props = latestProps()
    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: false }, { id: 'artifact:pod' })
    })

    expect(onSelectArtifact).not.toHaveBeenCalled()
    expect(mindMapNodePreviewProps).toHaveBeenCalled()
    const previewProps = mindMapNodePreviewProps.mock.calls.at(-1)?.[0] as { artifactId: string; artifactType: string }
    expect(previewProps.artifactId).toBe('artifact:pod')
    expect(previewProps.artifactType).toBe('podcast_audio')
  })

  it('still navigates directly for a non-previewable artifact type (report)', () => {
    const { onSelectArtifact } = setup()

    const props = latestProps()
    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: false }, { id: 'artifact:rep' })
    })

    expect(onSelectArtifact).toHaveBeenCalledWith('artifact:rep')
    expect(screen.queryByTestId('mind-map-node-preview')).not.toBeInTheDocument()
  })

  it('Shift-click on a podcast_audio node navigates instead of previewing', () => {
    const { onSelectArtifact } = setup()

    const props = latestProps()
    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: true }, { id: 'artifact:pod' })
    })

    expect(onSelectArtifact).toHaveBeenCalledWith('artifact:pod')
    expect(screen.queryByTestId('mind-map-node-preview')).not.toBeInTheDocument()
  })
})
