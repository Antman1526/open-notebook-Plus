import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { SourceDetailContent } from './SourceDetailContent'
import { sourcesApi } from '@/lib/api/sources'
import { insightsApi } from '@/lib/api/insights'
import { transformationsApi } from '@/lib/api/transformations'
import type { SourceDetailResponse } from '@/lib/types/api'

vi.mock('@/lib/hooks/use-translation', () => {
  const t = (key: string) => key
  return {
    useTranslation: () => ({
      t,
      language: 'en-US',
      setLanguage: vi.fn(),
    }),
  }
})

vi.mock('@/lib/api/sources', () => ({
  sourcesApi: {
    get: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    downloadFile: vi.fn(),
    retry: vi.fn(),
    locatePassage: vi.fn(),
  },
}))

vi.mock('@/lib/api/insights', () => ({
  insightsApi: {
    listForSource: vi.fn(),
    create: vi.fn(),
    delete: vi.fn(),
    waitForCommand: vi.fn(),
  },
}))

vi.mock('@/lib/api/transformations', () => ({
  transformationsApi: {
    list: vi.fn(),
  },
}))

vi.mock('@/components/source/NotebookAssociations', () => ({
  NotebookAssociations: () => <div data-testid="notebook-associations" />,
}))

const baseSource: SourceDetailResponse = {
  id: 'source:detail',
  title: 'Scanned source',
  asset: { file_path: '/uploads/scanned.pdf' },
  embedded: true,
  embedded_chunks: 0,
  insights_count: 0,
  created: '2026-06-23T00:00:00Z',
  updated: '2026-06-23T00:00:00Z',
  full_text: '',
  notebooks: [],
}

function renderDetail(source: SourceDetailResponse) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })

  vi.mocked(sourcesApi.get).mockResolvedValue(source)
  vi.mocked(insightsApi.listForSource).mockResolvedValue([])
  vi.mocked(transformationsApi.list).mockResolvedValue([])

  return render(
    <QueryClientProvider client={queryClient}>
      <SourceDetailContent sourceId={source.id} />
    </QueryClientProvider>
  )
}

describe('SourceDetailContent', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows a details warning when a completed source has no extracted text', async () => {
    renderDetail({
      ...baseSource,
      extracted_char_count: 0,
      extraction_quality: 'no_text',
    })

    await waitFor(() => {
      expect(sourcesApi.get).toHaveBeenCalledWith('source:detail')
    })

    await waitFor(() => {
      expect(screen.getByText('sources.noExtractedText')).toBeInTheDocument()
      expect(screen.getByText('sources.noExtractedTextDesc')).toBeInTheDocument()
    })
  })

  it('lets a user retry source processing from the extraction warning', async () => {
    vi.mocked(sourcesApi.retry).mockResolvedValue({
      ...baseSource,
      status: 'queued',
      command_id: 'command:retry',
      extraction_quality: 'pending',
    })

    renderDetail({
      ...baseSource,
      extracted_char_count: 0,
      extraction_quality: 'no_text',
    })

    fireEvent.click(await screen.findByRole('button', { name: 'sources.retryProcessing' }))

    await waitFor(() => {
      expect(sourcesApi.retry).toHaveBeenCalledWith('source:detail')
    })
    await waitFor(() => {
      expect(sourcesApi.get).toHaveBeenCalledTimes(2)
    })
  })

  it('locates and highlights cited passage inline in the document text', async () => {
    vi.mocked(sourcesApi.locatePassage).mockResolvedValue({
      start: 10,
      end: 28,
      score: 0.95,
      snippet: 'Key research finding',
    })

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    vi.mocked(sourcesApi.get).mockResolvedValue({
      ...baseSource,
      full_text: 'Introductory text. Key research finding was observed here. Conclusion.',
    })
    vi.mocked(insightsApi.listForSource).mockResolvedValue([])
    vi.mocked(transformationsApi.list).mockResolvedValue([])

    render(
      <QueryClientProvider client={queryClient}>
        <SourceDetailContent
          sourceId="source:detail"
          highlightQuery="What was the key research finding?"
        />
      </QueryClientProvider>
    )

    await waitFor(() => {
      expect(sourcesApi.locatePassage).toHaveBeenCalledWith(
        'source:detail',
        'What was the key research finding?'
      )
    })

    // Callout banner and jump button
    await waitFor(() => {
      expect(screen.getByText('Jump to in-text passage')).toBeInTheDocument()
      expect(screen.getByTestId('inline-cited-passage')).toHaveTextContent('Key research finding')
    })
  })
})
