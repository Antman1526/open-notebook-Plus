// v0.8.124 — Tests for the "export all completed artifacts" dialog.
// Mirrors the pattern in
// frontend/src/app/(dashboard)/notebooks/components/ExportNotebookDialog.test.tsx:
// useTranslation resolves to the raw key (global test setup), useFsHome is
// mocked for destination prefill, and the mutation hook is mocked directly.
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ExportAllArtifactsDialog } from './ExportAllArtifactsDialog'
import { useExportNotebookArtifactBundle } from '@/lib/hooks/use-studio'
import { useFsHome } from '@/lib/hooks/use-fs'

vi.mock('@/lib/hooks/use-studio', () => ({
  useExportNotebookArtifactBundle: vi.fn(),
}))
vi.mock('@/lib/hooks/use-fs', () => ({
  useFsHome: vi.fn(),
  useFsList: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useFsMkdir: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}))

function makeBundleMock(
  overrides: Partial<ReturnType<typeof useExportNotebookArtifactBundle>> = {},
) {
  return {
    mutateAsync: vi.fn().mockResolvedValue({
      destination: '/Users/me/exports/notebook-artifacts.zip',
      file_count: 3,
      total_bytes: 100,
      artifact_count: 2,
      skipped: 0,
      warnings: [],
    }),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useExportNotebookArtifactBundle>
}

function makeHomeMock(overrides: Partial<ReturnType<typeof useFsHome>> = {}) {
  return {
    data: {
      home: '/Users/me',
      desktop: '/Users/me/Desktop',
      documents: '/Users/me/Documents',
      downloads: '/Users/me/Downloads',
      default_exports: '/Users/me/DeeperNotebook-Exports',
    },
    isLoading: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useFsHome>
}

describe('ExportAllArtifactsDialog', () => {
  const baseProps = {
    open: true,
    onOpenChange: vi.fn(),
    notebookId: 'notebook:abc123',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useFsHome).mockReturnValue(makeHomeMock())
  })

  it('renders the bundle title and description', () => {
    vi.mocked(useExportNotebookArtifactBundle).mockReturnValue(makeBundleMock())
    render(<ExportAllArtifactsDialog {...baseProps} />)

    expect(screen.getByText('studio.export.bundleTitle')).toBeInTheDocument()
    expect(screen.getByText('studio.export.bundleDescription')).toBeInTheDocument()
  })

  it('seeds destination from default_exports + a slug derived from the notebook id', async () => {
    vi.mocked(useExportNotebookArtifactBundle).mockReturnValue(makeBundleMock())
    render(<ExportAllArtifactsDialog {...baseProps} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toBe('/Users/me/DeeperNotebook-Exports/abc123-artifacts.zip')
    })
  })

  it('submits with notebookId and the request body, including regenerate_stale default-on', async () => {
    const bundleMock = makeBundleMock()
    vi.mocked(useExportNotebookArtifactBundle).mockReturnValue(bundleMock)
    render(<ExportAllArtifactsDialog {...baseProps} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toContain('abc123-artifacts.zip')
    })

    fireEvent.click(screen.getByLabelText('notebooks.exportOverwrite'))
    fireEvent.click(screen.getByText('notebooks.export.button'))

    await waitFor(() => {
      expect(bundleMock.mutateAsync).toHaveBeenCalledWith({
        notebookId: 'notebook:abc123',
        data: expect.objectContaining({
          overwrite: true,
          compression: 'deflated',
          regenerate_stale: true,
        }),
      })
    })
  })

  it('disables submit while the export is in flight', () => {
    vi.mocked(useExportNotebookArtifactBundle).mockReturnValue(
      makeBundleMock({ isPending: true } as Partial<
        ReturnType<typeof useExportNotebookArtifactBundle>
      >),
    )
    render(<ExportAllArtifactsDialog {...baseProps} />)

    const submitButton = screen.getByText('notebooks.exporting').closest('button')
    expect(submitButton).toBeDisabled()
  })

  it('closes the dialog after a successful export', async () => {
    const onOpenChange = vi.fn()
    vi.mocked(useExportNotebookArtifactBundle).mockReturnValue(makeBundleMock())
    render(<ExportAllArtifactsDialog {...baseProps} onOpenChange={onOpenChange} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toContain('abc123-artifacts.zip')
    })

    fireEvent.click(screen.getByText('notebooks.export.button'))

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })
})
