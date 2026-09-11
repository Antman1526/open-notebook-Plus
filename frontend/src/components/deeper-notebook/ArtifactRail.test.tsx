import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { applyRuntimeFeatures, resetRuntimeFeatures } from '@/lib/features'

import { ArtifactRail } from './ArtifactRail'

const useStudioArtifacts = vi.fn()
const useStudioArtifactRevisions = vi.fn()
const useCreateStudioArtifact = vi.fn()
const useGenerateStudioArtifact = vi.fn()
const useDeleteStudioArtifact = vi.fn()
const useStudioWorkflowRuns = vi.fn()
const useCreateStudioWorkflowRun = vi.fn()
const useApproveStudioWorkflowRun = vi.fn()
const useUpdateStudioArtifact = vi.fn()
const useRegenerateStudioExport = vi.fn()
const useComposeVideoOverview = vi.fn()
const isEvidenceStudioEnabled = vi.fn()
const evidenceReviewProps = vi.fn()

vi.mock('@/lib/hooks/use-studio', () => ({
  useStudioArtifacts: (...args: unknown[]) => useStudioArtifacts(...args),
  useStudioArtifactRevisions: (...args: unknown[]) => useStudioArtifactRevisions(...args),
  useCreateStudioArtifact: (...args: unknown[]) => useCreateStudioArtifact(...args),
  useGenerateStudioArtifact: (...args: unknown[]) => useGenerateStudioArtifact(...args),
  useDeleteStudioArtifact: (...args: unknown[]) => useDeleteStudioArtifact(...args),
  useStudioWorkflowRuns: (...args: unknown[]) => useStudioWorkflowRuns(...args),
  useCreateStudioWorkflowRun: (...args: unknown[]) => useCreateStudioWorkflowRun(...args),
  useApproveStudioWorkflowRun: (...args: unknown[]) => useApproveStudioWorkflowRun(...args),
  useUpdateStudioArtifact: (...args: unknown[]) => useUpdateStudioArtifact(...args),
  useRegenerateStudioExport: (...args: unknown[]) => useRegenerateStudioExport(...args),
}))

vi.mock('@/lib/features', async importOriginal => {
  const actual = await importOriginal<typeof import('@/lib/features')>()
  return {
    ...actual,
    isEvidenceStudioEnabled: () => isEvidenceStudioEnabled(),
  }
})

vi.mock('@/components/evaluation/EvidenceReview', () => ({
  EvidenceReview: (props: Record<string, unknown>) => {
    evidenceReviewProps(props)
    return <span data-testid="evidence-review" />
  },
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: [], isLoading: false }),
}))

vi.mock('@/lib/hooks/use-video-overviews', () => ({
  useComposeVideoOverview: (...args: unknown[]) => useComposeVideoOverview(...args),
}))

// v0.8.118 — ArtifactExportMenu is now localized, so resolve keys through
// the real en-US strings (falling back to the key) to keep the English
// assertions below meaningful.
vi.mock('@/lib/hooks/use-translation', async () => {
  const { enUS } = await import('@/lib/locales/en-US')
  const resolve = (key: string): string => {
    let node: unknown = enUS
    for (const part of key.split('.')) {
      if (typeof node !== 'object' || node === null || !(part in (node as Record<string, unknown>))) return key
      node = (node as Record<string, unknown>)[part]
    }
    return typeof node === 'string' ? node : key
  }
  return { useTranslation: () => ({ t: resolve }) }
})

describe('ArtifactRail', () => {
  const createArtifact = vi.fn()
  const generateArtifact = vi.fn()
  const deleteArtifact = vi.fn()
  const createWorkflowRun = vi.fn()
  const approveWorkflowRun = vi.fn()
  const updateArtifact = vi.fn()

  beforeEach(() => {
    vi.restoreAllMocks()
    vi.clearAllMocks()
    resetRuntimeFeatures()
    evidenceReviewProps.mockClear()
    createArtifact.mockResolvedValue({ id: 'studio_artifact:new' })
    createWorkflowRun.mockResolvedValue({
      id: 'studio_workflow_run:new',
      artifact_id: 'studio_artifact:new',
      notebook_id: 'notebook:alpha',
      title: 'Generate Report',
      status: 'awaiting_approval',
      source_ids: [],
      approval_required: true,
      steps: [],
      command_id: null,
    })
    approveWorkflowRun.mockResolvedValue({
      id: 'studio_workflow_run:new',
      artifact_id: 'studio_artifact:new',
      notebook_id: 'notebook:alpha',
      title: 'Generate Report',
      status: 'queued',
      source_ids: [],
      approval_required: false,
      steps: [],
      command_id: null,
    })
    generateArtifact.mockResolvedValue({})
    deleteArtifact.mockResolvedValue({})
    updateArtifact.mockResolvedValue({})
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    useCreateStudioArtifact.mockReturnValue({
      mutateAsync: createArtifact,
      isPending: false,
    })
    useGenerateStudioArtifact.mockReturnValue({
      mutateAsync: generateArtifact,
      isPending: false,
    })
    useStudioArtifactRevisions.mockReturnValue({
      data: [],
      isLoading: false,
    })
    useDeleteStudioArtifact.mockReturnValue({
      mutateAsync: deleteArtifact,
      isPending: false,
    })
    useStudioWorkflowRuns.mockReturnValue({
      data: [],
      isLoading: false,
    })
    useCreateStudioWorkflowRun.mockReturnValue({
      mutateAsync: createWorkflowRun,
      isPending: false,
    })
    useApproveStudioWorkflowRun.mockReturnValue({
      mutateAsync: approveWorkflowRun,
      isPending: false,
    })
    useUpdateStudioArtifact.mockReturnValue({
      mutateAsync: updateArtifact,
      isPending: false,
    })
    useRegenerateStudioExport.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
      variables: undefined,
    })
    useComposeVideoOverview.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    })
  })

  it('mounts evidence review only for the selected artifact', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:one',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Report',
          status: 'completed',
          source_ids: [],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    expect(evidenceReviewProps).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Open Report' }))
    expect(evidenceReviewProps).toHaveBeenCalledWith(
      expect.objectContaining({
        notebookId: 'notebook:alpha',
        artifactId: 'studio_artifact:one',
      }),
    )
  })

  it('renders nothing when Evidence Studio is disabled', () => {
    isEvidenceStudioEnabled.mockReturnValue(false)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    const { container } = render(<ArtifactRail notebookId="notebook:alpha" />)

    expect(container).toBeEmptyDOMElement()
  })

  it('shows an empty state when no artifacts exist yet', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(<ArtifactRail notebookId="notebook:alpha" />)

    expect(screen.getAllByText('Evidence Studio').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Awaiting first artifact')).toBeInTheDocument()
    expect(screen.getByText('No saved research outputs in this notebook.')).toBeInTheDocument()
  })

  it('frames quick actions as App Mode templates', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByText('App Mode templates')).toBeInTheDocument()
    expect(screen.getByText(/Source readiness/)).toBeInTheDocument()
    expect(screen.getByText(/Artifact generation/)).toBeInTheDocument()
    expect(screen.getByText(/Evidence export/)).toBeInTheDocument()
  })

  it('blocks artifact generation until the notebook has sources', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(<ArtifactRail notebookId="notebook:alpha" />)

    expect(screen.getByText('Add at least one ready source before generating artifacts.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Report' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Report' }))

    expect(createArtifact).not.toHaveBeenCalled()
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('summarizes stored artifacts by status', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'study_guide',
          title: 'Study guide',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {},
          citations: [
            {
              source_id: 'source:one',
              title: 'Source One',
              preview: 'A grounded note.',
            },
          ],
          export_paths: {},
        },
        {
          id: 'studio_artifact:2',
          notebook_id: 'notebook:alpha',
          artifact_type: 'quiz',
          title: 'Quiz',
          status: 'pending',
          source_ids: [],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByText('2 artifacts')).toBeInTheDocument()
    expect(screen.getAllByText('Study guide').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByRole('button', { name: 'Open Quiz' })).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    expect(screen.getByText('pending')).toBeInTheDocument()
    expect(screen.getByText('1 completed')).toBeInTheDocument()
    expect(screen.getByText('1 in progress')).toBeInTheDocument()
    expect(screen.getAllByText('1 citation').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('No citations').length).toBeGreaterThanOrEqual(1)
  })

  it('uses distinct icons for saved infographic and podcast outline artifacts', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:infographic',
          notebook_id: 'notebook:alpha',
          artifact_type: 'infographic',
          title: 'Infographic',
          status: 'completed',
          source_ids: [],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
        {
          id: 'studio_artifact:podcast',
          notebook_id: 'notebook:alpha',
          artifact_type: 'podcast_outline',
          title: 'Podcast outline',
          status: 'completed',
          source_ids: [],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)

    expect(
      screen.getByRole('button', { name: 'Open Infographic' }).querySelector('.lucide-layers'),
    ).toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Open Podcast outline' }).querySelector('.lucide-mic-vocal'),
    ).toBeInTheDocument()
  })

  it('shows a loading state while artifact records load', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: undefined, isLoading: true })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByText('Loading artifacts')).toBeInTheDocument()
  })

  it('queues a report artifact behind an approval checkpoint', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Report' }))

    await waitFor(() => {
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'report',
        title: 'Report',
        source_ids: [],
      })
    })
    await waitFor(() => {
      expect(createWorkflowRun).toHaveBeenCalledWith({
        artifactId: 'studio_artifact:new',
        payload: {
          title: 'Generate Report',
          source_ids: [],
          approval_required: true,
        },
      })
    })
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('keeps Research run hidden while the experimental flag is disabled', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    applyRuntimeFeatures({ researchRuns: false })
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Research run' })).not.toBeInTheDocument()
  })

  it('creates and generates a Research run when the experimental flag is enabled', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Research run' }))

    await waitFor(() => {
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'research_run',
        title: 'Research run',
        source_ids: [],
      })
    })
    await waitFor(() => {
      expect(createWorkflowRun).toHaveBeenCalledWith({
        artifactId: 'studio_artifact:new',
        payload: {
          title: 'Generate Research run',
          source_ids: [],
          approval_required: true,
        },
      })
    })
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('hides Research run after a delayed runtime rollback', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByRole('button', { name: 'Research run' })).toBeInTheDocument()

    applyRuntimeFeatures({ researchRuns: false })

    await waitFor(() => {
      expect(screen.queryByRole('button', { name: 'Research run' })).not.toBeInTheDocument()
    })
  })

  it('creates artifacts from selected sources when the source selector is used', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
          {
            id: 'source:two',
            title: 'Source Two',
            asset: null,
            embedded: true,
            embedded_chunks: 2,
            insights_count: 0,
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Artifact sources: All sources' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Source One' }))
    fireEvent.click(screen.getByRole('button', { name: 'Report' }))

    await waitFor(() => {
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'report',
        title: 'Report',
        source_ids: ['source:one'],
      })
    })
    await waitFor(() => {
      expect(createWorkflowRun).toHaveBeenCalledWith({
        artifactId: 'studio_artifact:new',
        payload: {
          title: 'Generate Report',
          source_ids: ['source:one'],
          approval_required: true,
        },
      })
    })
  })

  it('shows source health and blocks generation when scoped sources are not ready', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:ready',
            title: 'Ready Source',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
          {
            id: 'source:failed',
            title: 'Failed Source',
            asset: null,
            embedded: false,
            embedded_chunks: 0,
            insights_count: 0,
            status: 'failed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Artifact sources: All sources' }))
    expect(screen.getByText('Ready')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('1 source is not ready for artifact generation.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Report' })).toBeDisabled()

    fireEvent.click(screen.getByRole('checkbox', { name: 'Ready Source' }))
    expect(screen.getByRole('button', { name: 'Report' })).not.toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Report' }))

    await waitFor(() => {
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'report',
        title: 'Report',
        source_ids: ['source:ready'],
      })
    })
  })

  it('shows workflow run history with approval checkpoints', () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Report',
          status: 'pending',
          source_ids: ['source:one'],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })
    useStudioWorkflowRuns.mockReturnValue({
      data: [
        {
          id: 'studio_workflow_run:1',
          artifact_id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          title: 'Generate Report',
          status: 'awaiting_approval',
          source_ids: ['source:one'],
          approval_required: true,
          steps: [
            { id: 'context', label: 'Context built', status: 'completed' },
            { id: 'privacy_gate', label: 'Privacy gate', status: 'pending' },
          ],
          command_id: null,
        },
      ],
      isLoading: false,
    })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    expect(screen.getByText('Workflow runs')).toBeInTheDocument()
    expect(screen.getByText('Generate Report')).toBeInTheDocument()
    expect(screen.getByText('awaiting approval')).toBeInTheDocument()
    expect(screen.getByText('Context built')).toBeInTheDocument()
    expect(screen.getByText('Privacy gate')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Approve Generate Report' })).toBeInTheDocument()
  })

  it('approves a queued workflow and starts artifact generation', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Report',
          status: 'pending',
          source_ids: ['source:one'],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })
    useStudioWorkflowRuns.mockReturnValue({
      data: [
        {
          id: 'studio_workflow_run:1',
          artifact_id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          title: 'Generate Report',
          status: 'awaiting_approval',
          source_ids: ['source:one'],
          approval_required: true,
          steps: [],
          command_id: null,
        },
      ],
      isLoading: false,
    })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Approve Generate Report' }))

    await waitFor(() => {
      expect(approveWorkflowRun).toHaveBeenCalledWith('studio_workflow_run:1')
    })
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('blocks artifact generation when a scoped source has no extracted text', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:empty',
            title: 'Image-only PDF',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            extraction_quality: 'no_text',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Artifact sources: All sources' }))

    expect(screen.getByText('No text')).toBeInTheDocument()
    expect(screen.getByText('1 source is not ready for artifact generation.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Report' })).toBeDisabled()
  })

  it('creates and queues additional text artifact types from the notebook', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({ data: [], isLoading: false })

    render(
      <ArtifactRail
        notebookId="notebook:alpha"
        sources={[
          {
            id: 'source:one',
            title: 'Source One',
            asset: null,
            embedded: true,
            embedded_chunks: 3,
            insights_count: 0,
            status: 'completed',
            created: '2026-06-23T00:00:00Z',
            updated: '2026-06-23T00:00:00Z',
          },
        ]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Briefing' }))
    fireEvent.click(screen.getByRole('button', { name: 'FAQ' }))
    fireEvent.click(screen.getByRole('button', { name: 'Timeline' }))
    fireEvent.click(screen.getByRole('button', { name: 'Course Pack' }))
    fireEvent.click(screen.getByRole('button', { name: 'Flashcards' }))
    fireEvent.click(screen.getByRole('button', { name: 'Quiz' }))
    fireEvent.click(screen.getByRole('button', { name: 'Data Table' }))
    fireEvent.click(screen.getByRole('button', { name: 'Mind map' }))
    fireEvent.click(screen.getByRole('button', { name: 'Slide deck' }))
    fireEvent.click(screen.getByRole('button', { name: 'Infographic' }))
    fireEvent.click(screen.getByRole('button', { name: 'Podcast outline' }))

    await waitFor(() => {
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'briefing',
        title: 'Briefing',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'faq',
        title: 'FAQ',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'timeline',
        title: 'Timeline',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'course_pack',
        title: 'Course Pack',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'flashcards',
        title: 'Flashcards',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'quiz',
        title: 'Quiz',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'data_table',
        title: 'Data Table',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'mind_map',
        title: 'Mind map',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'slide_deck',
        title: 'Slide deck',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'infographic',
        title: 'Infographic',
        source_ids: [],
      })
      expect(createArtifact).toHaveBeenCalledWith({
        notebook_id: 'notebook:alpha',
        artifact_type: 'podcast_outline',
        title: 'Podcast outline',
        source_ids: [],
      })
    })
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('opens a completed artifact with markdown, citations, and download link', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Quarterly Report',
          status: 'completed',
          source_ids: ['source:one'],
          model_id: 'model:qwen-coder',
          provider: 'openai_compatible',
          output_payload: {
            schema_version: 1,
            document: {
              schema_version: 1,
              artifact_type: 'report',
              title: 'Quarterly Report',
              sections: [{ heading: 'Evidence', body: 'Structured result.' }],
            },
            markdown: '# Quarterly Report\n\nStructured result.',
            content: '# Quarterly Report\n\nCompatibility result.',
            validation: { status: 'valid', errors: [], strategy: 'native', attempts: 1 },
          },
          citations: [
            {
              source_id: 'source:one',
              title: 'Source One',
              preview: 'Important excerpt from the source.',
            },
          ],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Quarterly Report' }))

    await waitFor(() => {
      expect(screen.getAllByRole('heading', { name: 'Quarterly Report' }).length).toBeGreaterThanOrEqual(1)
    })
    expect(screen.getAllByText('1 citation').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('model:qwen-coder')).toBeInTheDocument()
    expect(screen.getByText('openai_compatible')).toBeInTheDocument()
    expect(screen.getByText('Structured result.')).toBeInTheDocument()
    expect(screen.queryByText('Compatibility result.')).not.toBeInTheDocument()
    const sourceLink = screen.getByRole('link', { name: 'Source One' })
    expect(sourceLink).toHaveAttribute('href', '/sources/source%3Aone')
    expect(screen.getByText('Important excerpt from the source.')).toBeInTheDocument()
    const download = screen.getByRole('link', { name: 'Download Markdown' })
    expect(download).toHaveAttribute('download', 'Quarterly-Report.md')
    expect(download).toHaveAttribute('href', expect.stringContaining('data:text/markdown'))
    const jsonDownload = screen.getByRole('link', { name: 'Download JSON' })
    expect(jsonDownload).toHaveAttribute('download', 'Quarterly-Report.json')
    expect(jsonDownload).toHaveAttribute('href', expect.stringContaining('data:application/json'))
  })

  it('shows durable export paths when an artifact has been saved to disk', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    const markdownPath = '/Users/Antman/BrainPulseKnowledge/open-notebook-plus-imports/evidence-studio/quarterly-report.md'
    const jsonPath = '/Users/Antman/BrainPulseKnowledge/open-notebook-plus-imports/evidence-studio/quarterly-report.json'
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Quarterly Report',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: { content: '# Quarterly Report\n\nGrounded result.' },
          citations: [],
          export_paths: {
            markdown: markdownPath,
            json: jsonPath,
          },
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Quarterly Report' }))

    expect(screen.getByText('Saved exports')).toBeInTheDocument()
    expect(screen.getByText('Markdown')).toBeInTheDocument()
    expect(screen.getByText('JSON')).toBeInTheDocument()
    expect(screen.getByText(markdownPath)).toBeInTheDocument()
    expect(screen.getByText(jsonPath)).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: 'Open' })[0]).toHaveAttribute(
      'href',
      `file://${markdownPath}`,
    )
    expect(screen.getAllByRole('button', { name: 'Copy' })).toHaveLength(2)
    expect(screen.getAllByRole('link', { name: 'Folder' })[0]).toHaveAttribute(
      'href',
      'file:///Users/Antman/BrainPulseKnowledge/open-notebook-plus-imports/evidence-studio',
    )
  })

  it('opens structured slides and prioritizes visual export formats', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:slides',
          notebook_id: 'notebook:alpha',
          artifact_type: 'slide_deck',
          title: 'Evidence Slides',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            schema_version: 1,
            document: {
              schema_version: 1,
              artifact_type: 'slide_deck',
              title: 'Evidence Slides',
              audience: 'Researchers',
              slides: [
                {
                  title: 'Grounded output',
                  bullets: ['Claims remain traceable.'],
                  speaker_notes: 'Explain the evidence trail.',
                  visual_direction: 'Use a source flow.',
                  citations: ['[S1]'],
                },
              ],
            },
            markdown: '# Evidence Slides\n\n## Slide 1: Grounded output\n',
            content: '# Evidence Slides\n\n## Slide 1: Grounded output\n',
            validation: { status: 'valid', errors: [] },
          },
          citations: [],
          export_paths: {
            markdown: '/tmp/evidence-slides.md',
            json: '/tmp/evidence-slides.json',
            pdf: '/tmp/evidence-slides.pdf',
            pptx: '/tmp/evidence-slides.pptx',
          },
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Evidence Slides' }))

    expect(screen.getByRole('dialog')).toHaveClass(
      'w-[calc(100%-2rem)]',
      'max-w-[calc(100%-2rem)]',
      'overflow-y-auto',
      'sm:max-w-4xl',
      'lg:overflow-hidden',
    )
    expect(screen.getByRole('region', { name: 'Slide deck' })).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { name: 'Evidence Slides' })).toHaveLength(2)
    expect(screen.getByText('Prepared for Researchers')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next slide' }))
    expect(screen.getByRole('heading', { name: 'Grounded output' })).toBeInTheDocument()
    expect(screen.getByText('PPTX')).toBeInTheDocument()
    expect(screen.getByText('PDF')).toBeInTheDocument()
    const openLinks = screen.getAllByRole('link', { name: 'Open' })
    expect(openLinks[0]).toHaveAttribute('href', 'file:///tmp/evidence-slides.pptx')
    expect(openLinks[1]).toHaveAttribute('href', 'file:///tmp/evidence-slides.pdf')
  })

  it('opens citation evidence in a focused drawer from the artifact viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Quarterly Report',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: { content: '# Quarterly Report\n\nGrounded result.' },
          citations: [
            {
              source_id: 'source:one',
              title: 'Source One',
              marker: '[S1]',
              preview: 'Exact supporting sentence from the imported source.',
            },
          ],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Quarterly Report' }))
    fireEvent.click(screen.getByRole('button', { name: 'Inspect evidence for Source One' }))

    expect(screen.getByText('Citation evidence')).toBeInTheDocument()
    expect(screen.getAllByText('Exact supporting sentence from the imported source.').length).toBeGreaterThanOrEqual(2)
    expect(screen.getAllByText('source:one').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('[S1]')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open source record' })).toHaveAttribute('href', '/sources/source%3Aone')
  })

  it('shows revision history and opens a saved revision', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Quarterly Report',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            schema_version: 1,
            document: {
              schema_version: 1,
              artifact_type: 'report',
              title: 'Quarterly Report',
            },
            markdown: '# Quarterly Report\n\nCurrent structured result.',
            content: '# Quarterly Report\n\nCurrent compatibility result.',
            validation: { status: 'valid', errors: [] },
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })
    useStudioArtifactRevisions.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:revision',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Quarterly Report revision',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            schema_version: 1,
            document: {
              schema_version: 1,
              artifact_type: 'report',
              title: 'Quarterly Report revision',
            },
            markdown: '# Quarterly Report\n\nPrevious structured result.',
            content: '# Quarterly Report\n\nPrevious compatibility result.',
            validation: { status: 'valid', errors: [] },
          },
          citations: [],
          export_paths: {},
          revision_of_id: 'studio_artifact:1',
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Quarterly Report' }))

    expect(screen.getByText('Revision history')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open Quarterly Report revision' }))

    expect(screen.getByText('Previous structured result.')).toBeInTheDocument()
    expect(screen.queryByText('Previous compatibility result.')).not.toBeInTheDocument()
  })

  it('opens flashcards in an interactive review deck', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:flashcards',
          notebook_id: 'notebook:alpha',
          artifact_type: 'flashcards',
          title: 'Flashcards',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: [
              '## Flashcards',
              '',
              '### Card 1',
              'Front: What does Evidence Studio preserve?',
              'Back: Source-grounded artifacts and revisions.',
              'Source: Product plan',
              '',
              '### Card 2',
              'Front: What should users inspect?',
              'Back: Citations and source previews.',
              'Source: Product plan',
            ].join('\n'),
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Flashcards' }))

    expect(screen.getByText('Flashcard review')).toBeInTheDocument()
    expect(screen.getByText('Card 1 of 2')).toBeInTheDocument()
    expect(screen.getByText('What does Evidence Studio preserve?')).toBeInTheDocument()
    expect(screen.queryByText('Source-grounded artifacts and revisions.')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Reveal answer' }))
    expect(screen.getByText('Source-grounded artifacts and revisions.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Next card' }))
    expect(screen.getByText('Card 2 of 2')).toBeInTheDocument()
    expect(screen.getByText('What should users inspect?')).toBeInTheDocument()
  })

  it('opens quizzes in an interactive answer runner', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:quiz',
          notebook_id: 'notebook:alpha',
          artifact_type: 'quiz',
          title: 'Quiz',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: [
              '## Quiz',
              '',
              '### Question 1',
              'What powers the local model fleet?',
              'A. Cloud-only routing',
              'B. Managed local model inventory',
              'C. Manual copy paste',
              'Answer: B',
              'Explanation: The app scans the local AI_Models directory.',
              'Source: Product plan',
            ].join('\n'),
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Quiz' }))

    expect(screen.getByText('Quiz runner')).toBeInTheDocument()
    expect(screen.getByText('Question 1 of 1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'B. Managed local model inventory' }))

    expect(screen.getByText('Correct')).toBeInTheDocument()
    expect(screen.getByText('Score: 1 / 1')).toBeInTheDocument()
    expect(screen.getByText('The app scans the local AI_Models directory.')).toBeInTheDocument()
  })

  it('opens Data Tables in a native table viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:data-table',
          notebook_id: 'notebook:alpha',
          artifact_type: 'data_table',
          title: 'Data Table',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: [
              '# Data Table',
              '',
              '| Topic | Evidence | Source | Confidence | Notes |',
              '|---|---|---|---|---|',
              '| Local models | Scans AI_Models and routes roles [S1] | Source One | High | User-owned runtime |',
            ].join('\n'),
            data_table_rows: [
              {
                Topic: 'Evidence Studio',
                Evidence: 'Generates citation-backed artifacts [S1]',
                Source: 'Source One',
                Confidence: 'High',
                Notes: 'Exportable',
              },
            ],
          },
          citations: [],
          export_paths: {
            csv: '/tmp/data-table.csv',
          },
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Data Table' }))

    expect(screen.getByText('Data table')).toBeInTheDocument()
    expect(screen.getByText('1 row extracted from source-grounded output.')).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'Topic' })).toBeInTheDocument()
    expect(screen.getAllByText('Evidence Studio').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('Generates citation-backed artifacts [S1]')).toBeInTheDocument()
    expect(screen.getByText('CSV')).toBeInTheDocument()
    expect(screen.getByText('/tmp/data-table.csv')).toBeInTheDocument()
  })

  it('opens Mind Maps in the interactive canvas viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:mind-map',
          notebook_id: 'notebook:alpha',
          artifact_type: 'mind_map',
          title: 'Mind map',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: [
              '# Mind Map',
              '',
              '- Open Notebook Plus [S1]',
              '  - Source-grounded chat [S1]',
              '    - Citation drawer [S1]',
              '  - Local model control [S1]',
              '  - Evidence Studio [S1]',
            ].join('\n'),
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Mind map' }))

    expect(screen.getAllByText('Mind map').length).toBeGreaterThanOrEqual(1)
    expect(screen.getByText('5 nodes arranged from the source-grounded outline.')).toBeInTheDocument()
    expect(screen.getByLabelText(/Mind map canvas/i)).toBeInTheDocument()
    expect(screen.getByText('Selected topic')).toBeInTheDocument()
    expect(screen.getAllByText('Open Notebook Plus').length).toBeGreaterThanOrEqual(2)
    expect(screen.getByRole('button', { name: 'Collapse branch' })).toBeInTheDocument()
  })

  it('opens Course Packs in a module checklist viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:course-pack',
          notebook_id: 'notebook:alpha',
          artifact_type: 'course_pack',
          title: 'Course Pack',
          status: 'completed',
          source_ids: ['source:video', 'source:pdf'],
          output_payload: {
            content: [
              '# Course Pack',
              '',
              '## Audience',
              'New workspace admins. [S1]',
              '',
              '## Module 1: Local Model Orientation',
              'Duration: 20 minutes',
              'Learners map the local model fleet to common research jobs. [S1]',
              '',
              '### Hands-on exercise',
              'Compare source synthesis and study-fast model roles. [S1]',
              '',
              '### Facilitator notes',
              'Open the admin console only during lab time.',
              '',
              '## Module 2: Source-Grounded Assessment',
              'Duration: 15 minutes',
              'Learners verify citations before export. [S2]',
            ].join('\n'),
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Course Pack' }))

    expect(screen.getByText('Course Pack workspace')).toBeInTheDocument()
    expect(screen.getByText('Module checklist')).toBeInTheDocument()
    expect(screen.getAllByText('Module 1: Local Model Orientation').length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText('Open the admin console only during lab time.')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Facilitator notes' }))
    expect(screen.getByText('Open the admin console only during lab time.')).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText('Mark Module 1: Local Model Orientation complete'))
    expect(screen.getByText('1 complete')).toBeInTheDocument()
  })

  it('shows unsupported citation marker warnings in the artifact viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:citation-warning',
          notebook_id: 'notebook:alpha',
          artifact_type: 'course_pack',
          title: 'Course Pack',
          status: 'completed',
          source_ids: ['source:video', 'source:pdf'],
          output_payload: {
            content: [
              '# Course Pack',
              '',
              '## Module 1: Citation Review',
              'Supported claim. [S1]',
              'Unsupported claim. [S3]',
            ].join('\n'),
            citation_warnings: {
              unsupported_markers: ['[S3]'],
            },
          },
          citations: [
            {
              source_id: 'source:video',
              title: 'Video source',
              marker: '[S1]',
              preview: 'Supported transcript text.',
            },
            {
              source_id: 'source:pdf',
              title: 'PDF source',
              marker: '[S2]',
              preview: 'Supported document text.',
            },
          ],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Course Pack' }))

    expect(screen.getByTestId('artifact-citation-warning')).toHaveTextContent(
      'Citation markers need review',
    )
    expect(screen.getByTestId('artifact-citation-warning')).toHaveTextContent('[S3]')
    expect(screen.getByText('Video source')).toBeInTheDocument()
    expect(screen.getByText('PDF source')).toBeInTheDocument()
  })

  it('opens Research runs in a staged investigation viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:research-run',
          notebook_id: 'notebook:alpha',
          artifact_type: 'research_run',
          title: 'Research Run',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: [
              '# Research Run',
              '',
              '## Research objective',
              'Compare Open Notebook Plus with NotebookLM. [S1]',
              '',
              '## Working hypotheses',
              '- Local model routing is a differentiator. [S1]',
              '',
              '## Evidence-backed findings',
              '- Evidence Studio can generate study artifacts. [S1]',
              '',
              '## Gaps and contradictions',
              '- Runtime validation still needs browser coverage.',
              '',
              '## Follow-up questions',
              '- Which local model handles source synthesis best?',
              '',
              '## Recommended next actions',
              '- Run a local model benchmark.',
            ].join('\n'),
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Research Run' }))

    expect(screen.getByText('Research run workspace')).toBeInTheDocument()
    expect(screen.getByText('6 stages')).toBeInTheDocument()
    expect(screen.getByText('Research objective')).toBeInTheDocument()
    expect(screen.getByText('Working hypotheses')).toBeInTheDocument()
    expect(screen.getByText('Evidence-backed findings')).toBeInTheDocument()
    expect(screen.getByText('Runtime validation still needs browser coverage.')).toBeInTheDocument()
  })

  it('uses persisted Research run stage metadata before parsing markdown', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:research-run-structured',
          notebook_id: 'notebook:alpha',
          artifact_type: 'research_run',
          title: 'Research Run',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: '# Research Run\n\nFlat markdown without stage headings.',
            research_stages: [
              {
                title: 'Evidence-backed findings',
                items: ['Structured finding from saved JSON metadata.'],
              },
              {
                title: 'Recommended next actions',
                items: ['Use the structured payload for BrainPulseKnowledge import.'],
              },
            ],
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Research Run' }))

    expect(screen.getByText('Research run workspace')).toBeInTheDocument()
    expect(screen.getByText('2 stages')).toBeInTheDocument()
    expect(screen.getByText('Structured metadata')).toBeInTheDocument()
    expect(screen.getByText('Structured finding from saved JSON metadata.')).toBeInTheDocument()
    expect(screen.queryByText('Flat markdown without stage headings.')).not.toBeInTheDocument()
  })

  it('falls back to markdown when a Research run has no stage metadata or section headings', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:research-run-flat',
          notebook_id: 'notebook:alpha',
          artifact_type: 'research_run',
          title: 'Research Run',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            content: 'Flat research narrative without generated stage headings.',
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Research Run' }))

    expect(screen.getByText('Flat research narrative without generated stage headings.')).toBeInTheDocument()
  })

  it('regenerates a completed artifact from the viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:1',
          notebook_id: 'notebook:alpha',
          artifact_type: 'report',
          title: 'Quarterly Report',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: { content: '# Quarterly Report\n\nGrounded result.' },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Quarterly Report' }))
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }))

    await waitFor(() => {
      expect(createWorkflowRun).toHaveBeenCalledWith({
        artifactId: 'studio_artifact:1',
        payload: {
          title: 'Regenerate Quarterly Report',
          source_ids: ['source:one'],
          approval_required: false,
        },
      })
    })
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('retries a failed artifact from the viewer', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:failed',
          notebook_id: 'notebook:alpha',
          artifact_type: 'briefing',
          title: 'Failed Briefing',
          status: 'failed',
          source_ids: ['source:one'],
          output_payload: {},
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Failed Briefing' }))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => {
      expect(createWorkflowRun).toHaveBeenCalledWith({
        artifactId: 'studio_artifact:failed',
        payload: {
          title: 'Retry Failed Briefing',
          source_ids: ['source:one'],
          approval_required: false,
        },
      })
    })
    expect(generateArtifact).not.toHaveBeenCalled()
  })

  it('saves quiz study progress when a learner answers a question', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    const quizDocument = {
      schema_version: 1,
      artifact_type: 'quiz',
      title: 'Safety Quiz',
      questions: [
        {
          prompt: 'What should the learner do first?',
          options: [
            { id: 'A', text: 'Guess' },
            { id: 'B', text: 'Read the procedure' },
          ],
          correct_option_id: 'B',
        },
      ],
    }
    const quizMarkdown = [
      '# Safety Quiz',
      '',
      '## Question 1',
      'What should the learner do first?',
      'A. Guess',
      'B. Read the procedure',
      'Answer: B',
    ].join('\n')
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:quiz',
          notebook_id: 'notebook:alpha',
          artifact_type: 'quiz',
          title: 'Safety Quiz',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: {
            schema_version: 1,
            document: quizDocument,
            markdown: quizMarkdown,
            content: quizMarkdown,
            validation: { status: 'valid', errors: [], strategy: 'json', attempts: 1 },
            exporter_metadata: { retained: true },
          },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Safety Quiz' }))
    fireEvent.click(screen.getByRole('button', { name: 'B. Read the procedure' }))

    await waitFor(() => {
      expect(updateArtifact).toHaveBeenCalledWith({
        artifactId: 'studio_artifact:quiz',
        payload: {
          output_payload: expect.objectContaining({
            schema_version: 1,
            document: quizDocument,
            markdown: quizMarkdown,
            content: quizMarkdown,
            validation: { status: 'valid', errors: [], strategy: 'json', attempts: 1 },
            exporter_metadata: { retained: true },
            study_progress: expect.objectContaining({
              version: 1,
              quiz: {
                index: 0,
                answers: { '0': 'B' },
              },
            }),
          }),
        },
      })
    })
  })

  it('deletes an artifact from the viewer after confirmation', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:delete-me',
          notebook_id: 'notebook:alpha',
          artifact_type: 'quiz',
          title: 'Old Quiz',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: { content: '# Old Quiz' },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    fireEvent.click(screen.getByRole('button', { name: 'Open Old Quiz' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }))

    expect(window.confirm).toHaveBeenCalledWith('Delete "Old Quiz"?')
    await waitFor(() => {
      expect(deleteArtifact).toHaveBeenCalledWith('studio_artifact:delete-me')
    })
  })

  it('opens an artifact when dn:select-artifact event is dispatched', async () => {
    isEvidenceStudioEnabled.mockReturnValue(true)
    useStudioArtifacts.mockReturnValue({
      data: [
        {
          id: 'studio_artifact:target',
          notebook_id: 'notebook:alpha',
          artifact_type: 'notes',
          title: 'Target Notes',
          status: 'completed',
          source_ids: ['source:one'],
          output_payload: { content: '# Target Notes Content' },
          citations: [],
          export_paths: {},
        },
      ],
      isLoading: false,
    })

    render(<ArtifactRail notebookId="notebook:alpha" />)
    expect(screen.queryByRole('heading', { name: 'Target Notes' })).not.toBeInTheDocument()

    window.dispatchEvent(
      new CustomEvent('dn:select-artifact', {
        detail: { artifactId: 'studio_artifact:target' },
      })
    )

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Target Notes' })).toBeInTheDocument()
    })
  })
})

