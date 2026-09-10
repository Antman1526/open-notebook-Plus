import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/lib/api/studio', async () => {
  const actual = await vi.importActual<typeof import('@/lib/api/studio')>('@/lib/api/studio')
  return {
    ...actual,
    studioApi: {
      ...actual.studioApi,
      regenerateExport: vi.fn(),
    },
  }
})

import { studioApi } from '@/lib/api/studio'
import { ArtifactExportMenu } from './ArtifactExportMenu'

function renderWithQueryClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    React.createElement(QueryClientProvider, { client: queryClient }, ui),
  )
}

const exportTranslations: Record<string, string> = {
  'studio.export.regionLabel': 'Artifact exports',
  'studio.export.savedExports': 'Saved exports',
  'studio.export.availableCount': '{count} available',
  'studio.export.groupEditable': 'Editable',
  'studio.export.groupVisual': 'Visual',
  'studio.export.groupData': 'Data',
  'studio.export.groupSource': 'Source',
  'studio.export.groupBundle': 'Bundle',
  'studio.export.download': 'Download {label}',
  'studio.export.open': 'Open',
  'studio.export.openTooltip': 'Open {label}',
  'studio.export.copy': 'Copy',
  'studio.export.copyPath': 'Copy {label} path',
  'studio.export.copied': 'Copied',
  'studio.export.copiedPath': 'Copied path',
  'studio.export.folder': 'Folder',
  'studio.export.openFolder': 'Open {label} folder',
  'studio.export.browserDownloadPrefix': 'Browser download - ',
  'studio.export.generate': 'Generate {label}',
  'studio.export.generating': 'Generating…',
  'studio.export.stale': 'Outdated',
  'studio.export.regenerate': 'Regenerate {label}',
  'studio.export.regenerateTooltip': 'Regenerate {label}',
  'studio.export.regenerateOutdatedTooltip': 'Content changed since export. Click to refresh {label}.',
}

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string) => exportTranslations[key] ?? key,
    language: 'en-US',
    setLanguage: vi.fn(),
  }),
}))

const coursePackArtifact = {
  id: 'artifact:1',
  notebook_id: 'notebook:alpha',
  artifact_type: 'course_pack' as const,
  title: 'Onboarding Course Pack',
  status: 'completed' as const,
  source_ids: ['source:one'],
  output_payload: {},
  citations: [],
  export_paths: {
    docx: '/exports/onboarding.docx',
    markdown: '/exports/onboarding.md',
  },
}

const artifact = {
  id: 'studio_artifact:exports',
  notebook_id: 'notebook:alpha',
  artifact_type: 'report' as const,
  title: 'Quarterly Evidence Report',
  status: 'completed' as const,
  source_ids: ['source:one'],
  output_payload: {},
  citations: [],
  export_paths: {
    docx: '/exports/quarterly-report.docx',
    pptx: '/exports/quarterly-report.pptx',
    csv: '/exports/quarterly-report.csv',
    markdown: '/exports/quarterly-report.md',
    research_bundle: '/exports/quarterly-report-research-bundle.zip',
  },
}

describe('ArtifactExportMenu', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('groups persisted exports by their intended use with local-file actions', () => {
    renderWithQueryClient(<ArtifactExportMenu artifact={artifact} markdown="# Quarterly Evidence Report" />)

    expect(screen.getByRole('region', { name: 'Artifact exports' })).toBeInTheDocument()
    expect(screen.getByText('Editable')).toBeInTheDocument()
    expect(screen.getByText('Visual')).toBeInTheDocument()
    expect(screen.getByText('Data')).toBeInTheDocument()
    expect(screen.getByText('Source')).toBeInTheDocument()
    expect(screen.getByText('Bundle')).toBeInTheDocument()
    expect(screen.getByText('/exports/quarterly-report.docx')).toBeInTheDocument()

    expect(screen.getAllByRole('link', { name: 'Open' })[0]).toHaveAttribute(
      'href',
      'file:///exports/quarterly-report.docx',
    )
    expect(screen.getAllByRole('link', { name: 'Folder' })[0]).toHaveAttribute(
      'href',
      'file:///exports',
    )
    expect(screen.getAllByRole('button', { name: 'Copy' })[0]).toHaveClass('min-h-11', 'min-w-11')
  })

  it('keeps source downloads reachable when a completed artifact has not persisted files yet', () => {
    renderWithQueryClient(
      <ArtifactExportMenu
        artifact={{ ...artifact, export_paths: {} }}
        markdown="# Quarterly Evidence Report"
      />,
    )

    const markdownDownload = screen.getByRole('link', { name: 'Download Markdown' })
    expect(markdownDownload).toHaveAttribute('download', 'Quarterly-Evidence-Report.md')
    expect(markdownDownload).toHaveAttribute('href', expect.stringContaining('data:text/markdown'))
    expect(screen.getByRole('link', { name: 'Download JSON' })).toHaveAttribute(
      'download',
      'Quarterly-Evidence-Report.json',
    )

    for (const action of screen.getAllByRole('link')) {
      expect(action).toHaveClass('min-h-11', 'min-w-11')
    }
  })

  it('renders a persisted EPUB export with the EPUB label in the Bundle group', () => {
    renderWithQueryClient(
      <ArtifactExportMenu
        artifact={{
          ...artifact,
          export_paths: {
            ...artifact.export_paths,
            epub: '/exports/quarterly-report.epub',
          },
        }}
        markdown="# Quarterly Evidence Report"
      />,
    )

    const bundleHeading = screen.getByText('Bundle').closest('div')
    const bundleGroup = bundleHeading?.parentElement
    expect(bundleGroup).not.toBeNull()
    expect(within(bundleGroup as HTMLElement).getByText('EPUB')).toBeInTheDocument()
    expect(
      within(bundleGroup as HTMLElement).getByText('/exports/quarterly-report.epub'),
    ).toBeInTheDocument()
  })

  it('renders EPUB and PDF entries with their labels under the translated group headings', () => {
    renderWithQueryClient(
      <ArtifactExportMenu
        artifact={{
          ...artifact,
          export_paths: {
            ...artifact.export_paths,
            epub: '/exports/quarterly-report.epub',
            pdf: '/exports/quarterly-report.pdf',
          },
        }}
        markdown="# Quarterly Evidence Report"
      />,
    )

    const bundleGroup = screen.getByText('Bundle').closest('div')?.parentElement
    const visualGroup = screen.getByText('Visual').closest('div')?.parentElement
    expect(bundleGroup).not.toBeNull()
    expect(visualGroup).not.toBeNull()

    expect(within(bundleGroup as HTMLElement).getByText('EPUB')).toBeInTheDocument()
    expect(within(visualGroup as HTMLElement).getByText('PDF')).toBeInTheDocument()
  })

  it('shows a Generate PDF button for a course pack missing pdf and calls the API on click', async () => {
    vi.mocked(studioApi.regenerateExport).mockResolvedValue({
      ...coursePackArtifact,
      export_paths: { ...coursePackArtifact.export_paths, pdf: '/exports/onboarding.pdf' },
    })

    renderWithQueryClient(
      <ArtifactExportMenu artifact={coursePackArtifact} markdown="# Onboarding Course Pack" />,
    )

    const generatePdf = screen.getByRole('button', { name: 'Generate PDF' })
    expect(generatePdf).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate EPUB' })).toBeInTheDocument()

    fireEvent.click(generatePdf)

    await waitFor(() => {
      expect(studioApi.regenerateExport).toHaveBeenCalledWith('artifact:1', 'pdf')
    })
  })

  it('does not show a Generate PDF button once pdf is already present', () => {
    renderWithQueryClient(
      <ArtifactExportMenu
        artifact={{
          ...coursePackArtifact,
          export_paths: { ...coursePackArtifact.export_paths, pdf: '/exports/onboarding.pdf' },
        }}
        markdown="# Onboarding Course Pack"
      />,
    )

    expect(screen.queryByRole('button', { name: 'Generate PDF' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Generate EPUB' })).toBeInTheDocument()
  })

  it('renders a stale badge when an export format is marked stale', () => {
    renderWithQueryClient(
      <ArtifactExportMenu
        artifact={{
          ...artifact,
          stale_export_formats: ['docx'],
        }}
        markdown="# Quarterly Evidence Report"
      />,
    )

    const staleBadge = screen.getByTestId('stale-badge-docx')
    expect(staleBadge).toBeInTheDocument()
    expect(staleBadge).toHaveTextContent('Outdated')
    expect(screen.queryByTestId('stale-badge-pptx')).not.toBeInTheDocument()
  })

  it('allows regenerating an existing persisted export item directly', async () => {
    vi.mocked(studioApi.regenerateExport).mockResolvedValue({
      ...artifact,
      export_paths: { ...artifact.export_paths, docx: '/exports/quarterly-report-refreshed.docx' },
    })

    renderWithQueryClient(
      <ArtifactExportMenu artifact={artifact} markdown="# Quarterly Evidence Report" />,
    )

    const regenDocx = screen.getByRole('button', { name: 'Regenerate DOCX' })
    expect(regenDocx).toBeInTheDocument()

    fireEvent.click(regenDocx)

    await waitFor(() => {
      expect(studioApi.regenerateExport).toHaveBeenCalledWith('studio_artifact:exports', 'docx')
    })
  })
})

