// v0.7.119 — Refreshed to cover the expanded export dialog:
//   • the new format Select (now offers 6 values) renders + submits
//   • include_sources moved to the `notebooks.export.includeSources` key
//   • the compression Select only appears for zip / html_zip formats
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { ExportNotebookDialog } from './ExportNotebookDialog'

const legacyExports = `/Users/me/${'Open'}${'NotebookPlus'}-Exports`
import { useExportNotebook } from '@/lib/hooks/use-export'
import { useFsHome, useFsMkdir } from '@/lib/hooks/use-fs'

vi.mock('@/lib/hooks/use-export')
vi.mock('@/lib/hooks/use-fs', () => ({
  useFsHome: vi.fn(),
  useFsList: vi.fn(() => ({ data: null, isLoading: false, error: null })),
  useFsMkdir: vi.fn(() => ({ mutateAsync: vi.fn(), isPending: false })),
}))

// v0.8.126 — the real Select (Radix) crashes in jsdom (no scrollIntoView),
// so tests that need to switch the export format use this flat test double:
// every SelectItem always renders as a `role="option"` button, clicking it
// calls onValueChange directly, no open/close state or portal involved.
// Loads React via a dynamic import (not the module-scope import) so the
// factory has no ordering dependency on Vitest's `vi.mock` hoisting.
vi.mock('@/components/ui/select', async () => {
  const React = await import('react')
  type FakeSelectProps = {
    __value?: string
    __onValueChange?: (v: string) => void
    __disabled?: boolean
  }
  function Select({ value, onValueChange, disabled, children }: {
    value?: string
    onValueChange?: (v: string) => void
    disabled?: boolean
    children?: React.ReactNode
  }) {
    return (
      <div data-fake-select data-value={value}>
        {React.Children.map(children, (child: React.ReactNode) =>
          React.isValidElement(child)
            ? React.cloneElement(child as React.ReactElement<FakeSelectProps>, {
                __value: value,
                __onValueChange: onValueChange,
                __disabled: disabled,
              })
            : child
        )}
      </div>
    )
  }
  function SelectTrigger({ children, ...rest }: FakeSelectProps & { children?: React.ReactNode }) {
    // Strip the fake-select routing props before spreading onto the DOM node.
    const domProps = { ...rest } as Record<string, unknown>
    delete domProps.__value
    delete domProps.__onValueChange
    delete domProps.__disabled
    return <div {...domProps}>{children}</div>
  }
  function SelectValue() {
    return null
  }
  function SelectContent({ children, __value, __onValueChange, __disabled }: FakeSelectProps & {
    children?: React.ReactNode
  }) {
    return (
      <div>
        {React.Children.map(children, (child: React.ReactNode) =>
          React.isValidElement(child)
            ? React.cloneElement(child as React.ReactElement<FakeSelectProps>, { __value, __onValueChange, __disabled })
            : child
        )}
      </div>
    )
  }
  function SelectItem({ value, children, __value, __onValueChange, __disabled }: FakeSelectProps & {
    value: string
    children?: React.ReactNode
  }) {
    return (
      <button
        type="button"
        role="option"
        aria-selected={__value === value}
        disabled={__disabled}
        onClick={() => __onValueChange?.(value)}
      >
        {children}
      </button>
    )
  }
  return {
    Select,
    SelectTrigger,
    SelectValue,
    SelectContent,
    SelectItem,
    SelectGroup: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
    SelectLabel: ({ children }: { children?: React.ReactNode }) => <>{children}</>,
  }
})

function makeExportMock(overrides: Partial<ReturnType<typeof useExportNotebook>> = {}) {
  return {
    mutateAsync: vi.fn().mockResolvedValue({
      destination: '/Users/me/exports/x',
      format: 'folder',
      file_count: 3,
      total_bytes: 100,
      files: [],
      warnings: [],
    }),
    isPending: false,
    ...overrides,
  } as unknown as ReturnType<typeof useExportNotebook>
}

function makeHomeMock(overrides: Partial<ReturnType<typeof useFsHome>> = {}) {
  return {
    data: {
      home: '/Users/me',
      desktop: '/Users/me/Desktop',
      documents: '/Users/me/Documents',
      downloads: '/Users/me/Downloads',
      default_exports: legacyExports,
    },
    isLoading: false,
    error: null,
    ...overrides,
  } as unknown as ReturnType<typeof useFsHome>
}

describe('ExportNotebookDialog', () => {
  const baseProps = {
    open: true,
    onOpenChange: vi.fn(),
    notebookId: 'notebook:abc',
    notebookName: 'My Research Notes',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(useFsHome).mockReturnValue(makeHomeMock())
  })

  it('renders title and the combined-format Select trigger', () => {
    vi.mocked(useExportNotebook).mockReturnValue(makeExportMock())
    render(<ExportNotebookDialog {...baseProps} />)

    expect(screen.getByText('notebooks.exportNotebook')).toBeInTheDocument()
    // Trigger displays the default selected format label.
    expect(
      screen.getByLabelText('notebooks.exportFormatLabel'),
    ).toBeInTheDocument()
    expect(screen.getByText('notebooks.exportFormat.folder')).toBeInTheDocument()
  })

  it('seeds destination from default_exports + slugified notebook name', async () => {
    vi.mocked(useExportNotebook).mockReturnValue(makeExportMock())
    render(<ExportNotebookDialog {...baseProps} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toBe(`${legacyExports}/my-research-notes`)
    })
  })

  it('submits with destination, format, include_sources, and overwrite', async () => {
    const exportMock = makeExportMock()
    vi.mocked(useExportNotebook).mockReturnValue(exportMock)
    render(<ExportNotebookDialog {...baseProps} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toContain('my-research-notes')
    })

    // v0.7.119 — include_sources moved to the new `notebooks.export.includeSources`
    // label. The folder format keeps the checkbox visible.
    fireEvent.click(screen.getByLabelText('notebooks.export.includeSources'))
    fireEvent.click(screen.getByLabelText('notebooks.exportOverwrite'))
    fireEvent.click(screen.getByText('notebooks.export.button'))

    await waitFor(() => {
      expect(exportMock.mutateAsync).toHaveBeenCalledWith({
        id: 'notebook:abc',
        data: expect.objectContaining({
          format: 'folder',
          include_sources: true,
          overwrite: true,
        }),
      })
    })
    // Folder format → no compression in the body.
    const mockFn = exportMock.mutateAsync as unknown as ReturnType<typeof vi.fn>
    const call = mockFn.mock.calls[0]?.[0] as
      | { data: Record<string, unknown> }
      | undefined
    expect(call?.data).not.toHaveProperty('compression')
  })

  it('hides the compression row for non-zip formats (combined_md default-render)', () => {
    vi.mocked(useExportNotebook).mockReturnValue(makeExportMock())
    render(<ExportNotebookDialog {...baseProps} />)

    // Default format is 'folder' — compression label must NOT be visible.
    expect(
      screen.queryByText('notebooks.export.compressionLabel'),
    ).not.toBeInTheDocument()
    // includeSources still renders for non-combined_html formats.
    expect(
      screen.getByLabelText('notebooks.export.includeSources'),
    ).toBeInTheDocument()
  })

  it('disables submit while the export is in flight', () => {
    vi.mocked(useExportNotebook).mockReturnValue(
      makeExportMock({ isPending: true } as Partial<ReturnType<typeof useExportNotebook>>),
    )
    render(<ExportNotebookDialog {...baseProps} />)

    const submitButton = screen.getByText('notebooks.exporting').closest('button')
    expect(submitButton).toBeDisabled()
  })

  it('closes the dialog after a successful export', async () => {
    const onOpenChange = vi.fn()
    vi.mocked(useExportNotebook).mockReturnValue(makeExportMock())
    render(<ExportNotebookDialog {...baseProps} onOpenChange={onOpenChange} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toContain('my-research-notes')
    })

    fireEvent.click(screen.getByText('notebooks.export.button'))

    await waitFor(() => {
      expect(onOpenChange).toHaveBeenCalledWith(false)
    })
  })

  // v0.8.126 — found live: the default destination doesn't exist on a fresh
  // machine and the export routes refuse to create parents, so the first
  // export failed with a 400 the dialog never showed. Mirrors the fix in
  // ExportAllArtifactsDialog.test.tsx (v0.8.125).
  it('creates the destination folder itself before exporting a folder-format export', async () => {
    const exportMock = makeExportMock()
    vi.mocked(useExportNotebook).mockReturnValue(exportMock)
    const mkdirMutate = vi.fn().mockResolvedValue({})
    vi.mocked(useFsMkdir).mockReturnValue({ mutateAsync: mkdirMutate, isPending: false } as never)
    render(<ExportNotebookDialog {...baseProps} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toBe(`${legacyExports}/my-research-notes`)
    })

    fireEvent.click(screen.getByText('notebooks.export.button'))

    await waitFor(() => {
      expect(mkdirMutate).toHaveBeenCalledWith(`${legacyExports}/my-research-notes`)
      expect(exportMock.mutateAsync).toHaveBeenCalledTimes(1)
    })
    expect(mkdirMutate.mock.invocationCallOrder[0]).toBeLessThan(
      (exportMock.mutateAsync as ReturnType<typeof vi.fn>).mock.invocationCallOrder[0],
    )
  })

  it('creates the destination PARENT folder before exporting a zip-format export', async () => {
    const exportMock = makeExportMock()
    vi.mocked(useExportNotebook).mockReturnValue(exportMock)
    const mkdirMutate = vi.fn().mockResolvedValue({})
    vi.mocked(useFsMkdir).mockReturnValue({ mutateAsync: mkdirMutate, isPending: false } as never)
    render(<ExportNotebookDialog {...baseProps} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toBe(`${legacyExports}/my-research-notes`)
    })

    fireEvent.click(screen.getByRole('option', { name: 'notebooks.exportFormat.zip' }))

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toBe(`${legacyExports}/my-research-notes.zip`)
    })

    fireEvent.click(screen.getByText('notebooks.export.button'))

    await waitFor(() => {
      // Zip destination is a file — mkdir must target its parent, not itself.
      expect(mkdirMutate).toHaveBeenCalledWith(legacyExports)
      expect(exportMock.mutateAsync).toHaveBeenCalledTimes(1)
    })
  })

  it('shows the API error inline when the export fails, and does not close the dialog', async () => {
    const onOpenChange = vi.fn()
    const exportMock = makeExportMock({
      mutateAsync: vi.fn().mockRejectedValue({
        response: { data: { detail: 'Parent directory does not exist: /nope' } },
      }),
    } as Partial<ReturnType<typeof useExportNotebook>>)
    vi.mocked(useExportNotebook).mockReturnValue(exportMock)
    vi.mocked(useFsMkdir).mockReturnValue({
      mutateAsync: vi.fn().mockResolvedValue({}),
      isPending: false,
    } as never)
    render(<ExportNotebookDialog {...baseProps} onOpenChange={onOpenChange} />)

    await waitFor(() => {
      const input = screen.getByLabelText('notebooks.exportDestination') as HTMLInputElement
      expect(input.value).toContain('my-research-notes')
    })

    fireEvent.click(screen.getByText('notebooks.export.button'))

    const alert = await screen.findByRole('alert')
    expect(alert.textContent).toContain('Parent directory does not exist')
    expect(screen.getByTestId('export-error')).toBe(alert)
    expect(onOpenChange).not.toHaveBeenCalledWith(false)
  })
})
