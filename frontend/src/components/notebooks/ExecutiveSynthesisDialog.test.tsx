import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ExecutiveSynthesisDialog } from './ExecutiveSynthesisDialog'
import { notebooksApi } from '@/lib/api/notebooks'
import { useCreateNote } from '@/lib/hooks/use-notes'

vi.mock('@/lib/api/notebooks', () => ({
  notebooksApi: {
    getExecutiveSynthesis: vi.fn(),
  },
}))

vi.mock('@/lib/hooks/use-notes', () => ({
  useCreateNote: vi.fn(),
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}))

describe('ExecutiveSynthesisDialog', () => {
  const baseProps = {
    open: true,
    onOpenChange: vi.fn(),
    notebookId: 'nb-123',
    notebookName: 'Quantum Computing Research',
  }

  const mockMutateAsync = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useCreateNote).mockReturnValue({
      mutateAsync: mockMutateAsync,
      isPending: false,
    } as unknown as ReturnType<typeof useCreateNote>)

    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    })
  })

  it('renders loading state initially and fetches synthesis', async () => {
    vi.mocked(notebooksApi.getExecutiveSynthesis).mockReturnValue(
      new Promise(() => {})
    )

    render(<ExecutiveSynthesisDialog {...baseProps} />)

    expect(screen.getByText(/Analyzing corpus & generating executive synthesis/i)).toBeDefined()
  })

  it('renders synthesis result with badges and action buttons', async () => {
    vi.mocked(notebooksApi.getExecutiveSynthesis).mockResolvedValueOnce({
      notebook_id: 'nb-123',
      notebook_name: 'Quantum Computing Research',
      synthesis: 'All sources indicate solid state quantum superiority.',
      source_count: 4,
      sources: ['Source 1', 'Source 2', 'Source 3', 'Source 4'],
    })

    render(<ExecutiveSynthesisDialog {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByText(/4 Sources Synthesized/i)).toBeDefined()
    })

    expect(screen.getByText('All sources indicate solid state quantum superiority.')).toBeDefined()
    expect(screen.getByRole('button', { name: /Save as Note/i })).toBeDefined()
    expect(screen.getByRole('button', { name: /Copy/i })).toBeDefined()
  })

  it('handles Save as Note action', async () => {
    mockMutateAsync.mockResolvedValueOnce({ id: 'note-new' })
    vi.mocked(notebooksApi.getExecutiveSynthesis).mockResolvedValueOnce({
      notebook_id: 'nb-123',
      notebook_name: 'Quantum Computing Research',
      synthesis: 'Key consensus points.',
      source_count: 2,
      sources: ['Source 1', 'Source 2'],
    })

    render(<ExecutiveSynthesisDialog {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByText(/2 Sources Synthesized/i)).toBeDefined()
    })

    const saveButton = screen.getByRole('button', { name: /Save as Note/i })
    fireEvent.click(saveButton)

    await waitFor(() => {
      expect(mockMutateAsync).toHaveBeenCalledWith({
        notebook_id: 'nb-123',
        title: 'Executive Synthesis — Quantum Computing Research',
        content: 'Key consensus points.',
      })
    })
  })
})
