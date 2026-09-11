import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useMindMapStore } from '@/lib/stores/mind-map-store'

const reactFlowProps = vi.hoisted(() => vi.fn())
const miniMapProps = vi.hoisted(() => vi.fn())
const useNotebookGraph = vi.hoisted(() => vi.fn())
const fitViewMock = vi.hoisted(() => vi.fn())

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  MiniMap: (props: Record<string, unknown>) => {
    miniMapProps(props)
    return null
  },
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useReactFlow: () => ({ fitView: fitViewMock }),
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

import MindMap from './MindMap'

describe('MindMap semantic theme roles', () => {
  const sampleData = {
    nodes: [
      { id: 'notebook:one', type: 'notebook', label: 'Notebook' },
      { id: 'source:one', type: 'source', label: 'Source' },
      { id: 'note:one', type: 'note', label: 'Note' },
      { id: 'artifact:one', type: 'studio_artifact', label: 'Artifact' },
    ],
    edges: [
      { source: 'notebook:one', target: 'source:one' },
      { source: 'notebook:one', target: 'note:one' },
      { source: 'notebook:one', target: 'artifact:one' },
    ],
  }

  beforeEach(() => {
    localStorage.clear()
    useMindMapStore.setState({ byNotebook: {} })
  })

  it('forwards semantic graph roles to notebook, source, note, and edge styles', () => {
    useNotebookGraph.mockReturnValue({
      data: sampleData,
      isLoading: false,
      isError: false,
    })

    render(<MindMap notebookId="notebook:one" />)

    const props = reactFlowProps.mock.calls.at(-1)?.[0] as {
      nodes: Array<{ id: string; style: React.CSSProperties }>
      edges: Array<{ style: React.CSSProperties }>
    }
    expect(props.nodes.find(node => node.id === 'notebook:one')?.style).toMatchObject({
      background: 'var(--dn-graph-fallback)',
      color: 'var(--dn-graph-fallback-foreground)',
    })
    expect(props.nodes.find(node => node.id === 'source:one')?.style).toMatchObject({
      background: 'var(--dn-graph-source)',
      color: 'var(--dn-graph-source-foreground)',
    })
    expect(props.nodes.find(node => node.id === 'note:one')?.style).toMatchObject({
      background: 'var(--dn-graph-note)',
      color: 'var(--dn-graph-note-foreground)',
    })
    expect(props.nodes.find(node => node.id === 'artifact:one')?.style).toMatchObject({
      background: 'var(--dn-graph-artifact, var(--dn-graph-note))',
      color: 'var(--dn-graph-artifact-foreground, var(--dn-graph-note-foreground))',
    })
    expect(props.edges).toHaveLength(3)
    expect(props.edges.every(edge => edge.style.stroke === 'var(--dn-graph-edge)')).toBe(true)

    // Verify MiniMap nodeColor mapping
    const mProps = miniMapProps.mock.calls.at(-1)?.[0] as {
      nodeColor?: (node: { id: string }) => string
    }
    expect(mProps.nodeColor).toBeDefined()
    expect(mProps.nodeColor?.({ id: 'notebook:one' })).toBe('var(--dn-graph-fallback)')
    expect(mProps.nodeColor?.({ id: 'source:one' })).toBe('var(--dn-graph-source)')
    expect(mProps.nodeColor?.({ id: 'note:one' })).toBe('var(--dn-graph-note)')
    expect(mProps.nodeColor?.({ id: 'artifact:one' })).toBe('var(--dn-graph-artifact, var(--dn-graph-note))')
  })

  it('renders filter chips and filters nodes/edges by selected type', () => {
    useNotebookGraph.mockReturnValue({
      data: sampleData,
      isLoading: false,
      isError: false,
    })

    render(<MindMap notebookId="notebook:one" />)

    // Chips rendered with counts
    expect(screen.getByRole('button', { name: 'All (3)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Sources (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Notes (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Artifacts (1)' })).toBeInTheDocument()

    // Filter to Sources
    fireEvent.click(screen.getByRole('button', { name: 'Sources (1)' }))

    let props = reactFlowProps.mock.calls.at(-1)?.[0] as {
      nodes: Array<{ id: string }>
      edges: Array<{ source: string; target: string }>
    }
    // Hub + 1 source node
    expect(props.nodes.map(n => n.id)).toEqual(['notebook:one', 'source:one'])
    expect(props.edges).toHaveLength(1)
    expect(props.edges[0]).toMatchObject({ source: 'notebook:one', target: 'source:one' })

    // Filter to Artifacts
    fireEvent.click(screen.getByRole('button', { name: 'Artifacts (1)' }))

    props = reactFlowProps.mock.calls.at(-1)?.[0] as {
      nodes: Array<{ id: string }>
      edges: Array<{ source: string; target: string }>
    }
    expect(props.nodes.map(n => n.id)).toEqual(['notebook:one', 'artifact:one'])
    expect(props.edges).toHaveLength(1)
    expect(props.edges[0]).toMatchObject({ source: 'notebook:one', target: 'artifact:one' })

    // Reset to All
    fireEvent.click(screen.getByRole('button', { name: 'All (3)' }))

    props = reactFlowProps.mock.calls.at(-1)?.[0] as {
      nodes: Array<{ id: string }>
      edges: Array<{ source: string; target: string }>
    }
    expect(props.nodes).toHaveLength(4)
    expect(props.edges).toHaveLength(3)
  })
})

