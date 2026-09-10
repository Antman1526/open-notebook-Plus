import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ArtifactExportMenu } from './ArtifactExportMenu'

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
  it('groups persisted exports by their intended use with local-file actions', () => {
    render(<ArtifactExportMenu artifact={artifact} markdown="# Quarterly Evidence Report" />)

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
    render(
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
    render(
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
})
