// v0.8.124 — canvas search/dim, cluster-by-type layout, and preview-vs-navigate
// click routing for the mind map (improvement roadmap).
// v0.8.125 — source/note preview click routing, search-to-focus (Enter/arrow
// keys), and per-notebook persisted canvas state (improvement roadmap).
import React from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useMindMapStore } from '@/lib/stores/mind-map-store'

const reactFlowProps = vi.hoisted(() => vi.fn())
const useNotebookGraph = vi.hoisted(() => vi.fn())
const mindMapNodePreviewProps = vi.hoisted(() => vi.fn())
const fitViewMock = vi.hoisted(() => vi.fn())

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useReactFlow: () => ({ fitView: fitViewMock }),
  ReactFlow: (props: { children?: React.ReactNode; nodes?: Array<{ id: string }> } & Record<string, unknown>) => {
    reactFlowProps(props)
    return (
      <div data-testid="react-flow">
        {(props.nodes ?? []).map((n) => (
          <div key={n.id} data-id={n.id} tabIndex={-1} />
        ))}
        {props.children}
      </div>
    )
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

  beforeEach(() => {
    localStorage.clear()
    useMindMapStore.setState({ byNotebook: {} })
  })

  function setup() {
    useNotebookGraph.mockReturnValue({ data: sampleData, isLoading: false, isError: false })
    const onSelectArtifact = vi.fn()
    const onSelectSource = vi.fn()
    const onSelectNote = vi.fn()
    render(
      <MindMap
        notebookId="notebook:one"
        onSelectArtifact={onSelectArtifact}
        onSelectSource={onSelectSource}
        onSelectNote={onSelectNote}
      />
    )
    return { onSelectArtifact, onSelectSource, onSelectNote }
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

  // v0.8.126 — "1 matches" read wrong; a single match uses the singular key.
  it('uses the singular match count copy for exactly one match', () => {
    setup()

    const input = screen.getByLabelText('Search nodes')
    fireEvent.change(input, { target: { value: 'Beta' } })

    expect(screen.getByText('1 match')).toBeInTheDocument()
    expect(screen.queryByText('1 matches')).not.toBeInTheDocument()
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
    const previewProps = mindMapNodePreviewProps.mock.calls.at(-1)?.[0] as { nodeId: string; artifactType: string }
    expect(previewProps.nodeId).toBe('artifact:pod')
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

  // v0.8.125 — source/note preview click routing.
  it('opens the preview for a plain click on a source node', () => {
    const { onSelectSource } = setup()

    const props = latestProps()
    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: false }, { id: 'source:one' })
    })

    expect(onSelectSource).not.toHaveBeenCalled()
    const previewProps = mindMapNodePreviewProps.mock.calls.at(-1)?.[0] as { nodeId: string; nodeType: string }
    expect(previewProps.nodeId).toBe('source:one')
    expect(previewProps.nodeType).toBe('source')
  })

  it('Shift-click on a source node calls onSelectSource directly', () => {
    const { onSelectSource } = setup()

    const props = latestProps()
    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: true }, { id: 'source:one' })
    })

    expect(onSelectSource).toHaveBeenCalledWith('source:one')
    expect(screen.queryByTestId('mind-map-node-preview')).not.toBeInTheDocument()
  })

  it('opens the preview for a plain click on a note node, and Shift-click navigates directly', () => {
    const { onSelectNote } = setup()

    const props = latestProps()
    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: false }, { id: 'note:one' })
    })
    expect(onSelectNote).not.toHaveBeenCalled()
    const previewProps = mindMapNodePreviewProps.mock.calls.at(-1)?.[0] as { nodeId: string; nodeType: string }
    expect(previewProps.nodeId).toBe('note:one')
    expect(previewProps.nodeType).toBe('note')

    act(() => {
      props.onNodeClick({ clientX: 10, clientY: 10, shiftKey: true }, { id: 'note:one' })
    })
    expect(onSelectNote).toHaveBeenCalledWith('note:one')
  })

  // v0.8.125 — search-to-focus.
  it('Enter calls fitView with the matching node ids', () => {
    setup()

    const input = screen.getByLabelText('Search nodes')
    fireEvent.change(input, { target: { value: 'Alpha' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(fitViewMock).toHaveBeenCalledWith({
      nodes: [{ id: 'source:one' }, { id: 'note:one' }],
      padding: 0.3,
      duration: 300,
    })
  })

  it('ArrowDown steps the active match, focuses the node, and wraps around', () => {
    setup()

    const input = screen.getByLabelText('Search nodes')
    fireEvent.change(input, { target: { value: 'Alpha' } })

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(document.activeElement?.getAttribute('data-id')).toBe('source:one')
    expect(screen.getByText('1 of 2')).toBeInTheDocument()

    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(document.activeElement?.getAttribute('data-id')).toBe('note:one')
    expect(screen.getByText('2 of 2')).toBeInTheDocument()

    // Wraps back around to the first match.
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(document.activeElement?.getAttribute('data-id')).toBe('source:one')
  })

  it('Escape clears the search query', () => {
    setup()

    const input = screen.getByLabelText('Search nodes') as HTMLInputElement
    fireEvent.change(input, { target: { value: 'Alpha' } })
    expect(input.value).toBe('Alpha')

    fireEvent.keyDown(input, { key: 'Escape' })
    expect(input.value).toBe('')
  })

  // v0.8.125 — per-notebook persisted canvas state.
  it('applies a pre-seeded store value on mount', () => {
    useMindMapStore.getState().setState('notebook:one', {
      filter: 'source',
      query: 'Beta',
      clusterByType: false,
    })

    setup()

    expect(screen.getByRole('button', { name: 'Sources (2)' })).toHaveAttribute('aria-pressed', 'true')
    const input = screen.getByLabelText('Search nodes') as HTMLInputElement
    expect(input.value).toBe('Beta')

    const props = latestProps()
    // Filtered to sources only: hub + the 2 source nodes.
    expect(props.nodes.map((n) => n.id).sort()).toEqual(['notebook:one', 'source:one', 'source:two'].sort())
  })

  it('writes filter/search/cluster changes back to the store for this notebook', () => {
    setup()

    fireEvent.click(screen.getByRole('button', { name: 'Notes (1)' }))
    const input = screen.getByLabelText('Search nodes')
    fireEvent.change(input, { target: { value: 'Alpha' } })
    fireEvent.click(screen.getByRole('button', { name: 'Cluster by type' }))

    expect(useMindMapStore.getState().byNotebook['notebook:one']).toEqual({
      filter: 'note',
      query: 'Alpha',
      clusterByType: true,
    })
  })
})
