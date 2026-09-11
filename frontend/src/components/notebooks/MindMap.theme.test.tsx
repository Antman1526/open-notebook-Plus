import React from 'react'
import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

const reactFlowProps = vi.hoisted(() => vi.fn())
const useNotebookGraph = vi.hoisted(() => vi.fn())

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  MiniMap: () => null,
  ReactFlow: (props: Record<string, unknown>) => {
    reactFlowProps(props)
    return <div data-testid="react-flow" />
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
  it('forwards semantic graph roles to notebook, source, note, and edge styles', () => {
    useNotebookGraph.mockReturnValue({
      data: {
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
      },
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
  })
})
