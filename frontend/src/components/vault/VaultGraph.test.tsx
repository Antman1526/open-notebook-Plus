import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'

const flowProps = vi.hoisted(() => ({
  viewport: undefined as undefined | { x: number; y: number; zoom: number },
}))

vi.mock('@xyflow/react/dist/style.css', () => ({}))
vi.mock('./vault.css', () => ({}))

vi.mock('@xyflow/react', () => ({
  Background: () => null,
  Controls: () => null,
  ReactFlow: ({
    children,
    nodes,
    viewport,
    fitView,
    onMoveEnd,
  }: {
    children: React.ReactNode
    nodes: { id: string; data: { label: string } }[]
    viewport?: { x: number; y: number; zoom: number }
    fitView: boolean
    onMoveEnd: (
      event: null,
      viewport: { x: number; y: number; zoom: number },
    ) => void
  }) => {
    flowProps.viewport = viewport
    return (
      <div data-fit-view={fitView}>
        <button
          type="button"
          onClick={() => onMoveEnd(null, { x: 7, y: -2, zoom: 1.5 })}
        >
          Move graph
        </button>
        {nodes.map((node) => (
          <div
            key={node.id}
            role="group"
            tabIndex={0}
            data-id={node.id}
            data-testid={`rf__node-${node.id}`}
          >
            {node.data.label}
          </div>
        ))}
        {children}
      </div>
    )
  },
}))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

import { VaultGraph } from './VaultGraph'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'

describe('VaultGraph controlled viewport', () => {
  it('passes a controlled viewport to React Flow and reports onMoveEnd', () => {
    const onMoveEnd = vi.fn()
    const onBookmarkContext = vi.fn()
    render(
      <VaultGraph
        graph={{
          nodes: [{
            id: 'note:one', title: 'One', source_format: 'markdown',
          }],
          edges: [],
        }}
        unresolved={[]}
        onNavigate={vi.fn()}
        viewport={{ x: 3, y: 6, zoom: 2 }}
        onMoveEnd={onMoveEnd}
        rootDocumentId="knowledge_engine_document:one"
        spaceIds={['knowledge_engine_space:research']}
        onBookmarkContext={onBookmarkContext}
      />,
    )

    expect(flowProps.viewport).toEqual({ x: 3, y: 6, zoom: 2 })
    expect(screen.getByRole('region', { name: 'Connection atlas' })).toBeInTheDocument()
    expect(screen.getByLabelText('Graph legend')).toHaveTextContent('1 connected source')
    expect(screen.getByLabelText('Graph inspector')).toHaveTextContent('Open a connected note')
    expect(screen.getByText('Move graph').parentElement).toHaveAttribute('data-fit-view', 'false')
    fireEvent.click(screen.getByRole('button', { name: 'Move graph' }))
    expect(onMoveEnd).toHaveBeenCalledWith({ x: 7, y: -2, zoom: 1.5 })
    expect(onBookmarkContext).toHaveBeenCalledWith({
      rootDocumentId: 'knowledge_engine_document:one',
      spaceIds: ['knowledge_engine_space:research'],
      relationKinds: [],
      viewport: { x: 3, y: 6, zoom: 2 },
    })
  })

  it('does not repeat bookmark publication after an implicit empty relation filter rerenders its parent', async () => {
    const onBookmarkContext = vi.fn()
    function BookmarkContextHarness() {
      const [, setBookmarkContext] = useState<unknown>(null)
      return <VaultGraph
        graph={{
          nodes: [{ id: 'note:one', title: 'One', source_format: 'markdown' }],
          edges: [],
        }}
        unresolved={[]}
        onNavigate={vi.fn()}
        viewport={{ x: 3, y: 6, zoom: 2 }}
        rootDocumentId="knowledge_engine_document:one"
        spaceIds={['knowledge_engine_space:research']}
        onBookmarkContext={(context) => {
          onBookmarkContext(context)
          setBookmarkContext(context)
        }}
      />
    }

    render(<BookmarkContextHarness />)

    await waitFor(() => expect(onBookmarkContext).toHaveBeenCalledTimes(1))
  })

  it('opens a bounded unified graph selection through review-only podcast state', () => {
    usePodcastStudioStore.getState().dismiss()
    render(
      <VaultGraph
        graph={{
          nodes: [{
            id: 'note:one', title: 'One', source_format: 'markdown',
            knowledge_document_id: 'knowledge_engine_document:one',
          }],
          edges: [],
        }}
        unresolved={[]}
        onNavigate={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Turn graph into podcast' }))

    expect(usePodcastStudioStore.getState()).toMatchObject({
      isOpen: true,
      destination: 'quick',
      selections: [{
        kind: 'graph_selection', documentIds: ['knowledge_engine_document:one'],
      }],
    })
  })
})

describe('VaultGraph keyboard navigation', () => {
  it('opens the focused node with Enter', () => {
    const onNavigate = vi.fn()
    render(
      <VaultGraph
        graph={{
          nodes: [{ id: 'note:one', title: 'One', source_format: 'markdown' }],
          edges: [],
        }}
        unresolved={[]}
        onNavigate={onNavigate}
      />,
    )

    fireEvent.keyDown(screen.getByTestId('rf__node-note:one'), { key: 'Enter' })

    expect(onNavigate).toHaveBeenCalledWith('note:one')
  })

  it('does not open an unresolved node on Enter', () => {
    const onNavigate = vi.fn()
    render(
      <VaultGraph
        graph={{ nodes: [], edges: [] }}
        unresolved={[{
          id: 'note_link:one', source_note_id: 'note:source', target_note_id: null,
          target_text: 'Missing note', link_kind: 'wikilink', resolved: false,
          source_start: 0, source_end: 12,
        }]}
        onNavigate={onNavigate}
      />,
    )

    fireEvent.keyDown(screen.getByTestId('rf__node-unresolved:note_link:one'), { key: 'Enter' })

    expect(onNavigate).not.toHaveBeenCalled()
  })

  it('moves focus to the nearest node to the right with ArrowRight', () => {
    render(
      <VaultGraph
        graph={{
          nodes: [
            { id: 'note:a', title: 'A', source_format: 'markdown' },
            { id: 'note:b', title: 'B', source_format: 'markdown' },
            { id: 'note:c', title: 'C', source_format: 'markdown' },
          ],
          edges: [],
        }}
        unresolved={[]}
        onNavigate={vi.fn()}
      />,
    )

    const nodeA = screen.getByTestId('rf__node-note:a')
    nodeA.focus()
    fireEvent.keyDown(nodeA, { key: 'ArrowRight' })

    expect(document.activeElement).toHaveAttribute('data-id', 'note:b')
  })

  it('does not move focus with ArrowUp when nothing lies above', () => {
    render(
      <VaultGraph
        graph={{
          nodes: [
            { id: 'note:a', title: 'A', source_format: 'markdown' },
            { id: 'note:b', title: 'B', source_format: 'markdown' },
            { id: 'note:c', title: 'C', source_format: 'markdown' },
          ],
          edges: [],
        }}
        unresolved={[]}
        onNavigate={vi.fn()}
      />,
    )

    const nodeA = screen.getByTestId('rf__node-note:a')
    nodeA.focus()
    fireEvent.keyDown(nodeA, { key: 'ArrowUp' })

    expect(document.activeElement).toHaveAttribute('data-id', 'note:a')
  })
})
