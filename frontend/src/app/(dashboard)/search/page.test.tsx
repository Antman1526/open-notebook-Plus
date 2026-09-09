import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { isLuminousFolioEnabled } from '@/lib/features'
import { sourcesApi } from '@/lib/api/sources'

import SearchPage from './page'

vi.mock('next/navigation', () => ({ useSearchParams: () => new URLSearchParams(mockSearchParams.current) }))
vi.mock('@/components/layout/AppShell', () => ({ AppShell: ({ children }: { children: React.ReactNode }) => <>{children}</> }))
const { mockSearchData, mockSearchParams, mockVisualSystemEnabled, mockSourceVisualsEnabled, mockOpenModal } = vi.hoisted(() => ({
  mockSearchData: { current: undefined as unknown },
  mockSearchParams: { current: '' },
  mockVisualSystemEnabled: vi.fn(() => false),
  mockSourceVisualsEnabled: vi.fn(() => false),
  mockOpenModal: vi.fn(),
}))

vi.mock('@/lib/hooks/use-search', () => ({ useSearch: () => ({ mutate: vi.fn(), isPending: false, data: mockSearchData.current }) }))
vi.mock('@/lib/hooks/use-ask', () => ({ useAsk: () => ({ isStreaming: false, strategy: '', answers: [], finalAnswer: null, sendAsk: vi.fn() }) }))
vi.mock('@/lib/hooks/use-models', () => ({
  useModelDefaults: () => ({ data: { default_embedding_model: 'embed', default_chat_model: 'chat' }, isLoading: false }),
  useModels: () => ({ data: [] }),
}))
vi.mock('@/lib/hooks/use-modal-manager', () => ({ useModalManager: () => ({ openModal: mockOpenModal }) }))
vi.mock('@/components/search/StreamingResponse', () => ({ StreamingResponse: () => <div>Streaming response</div> }))
vi.mock('@/components/search/AdvancedModelsDialog', () => ({ AdvancedModelsDialog: () => null }))
vi.mock('@/components/search/SaveToNotebooksDialog', () => ({ SaveToNotebooksDialog: () => null }))
vi.mock('@/components/common/LoadingSpinner', () => ({ LoadingSpinner: () => <div>Loading</div> }))
vi.mock('@/lib/hooks/use-translation', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))
vi.mock('@/lib/features', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/features')>()
  return {
    ...actual,
    isVisualSystemV2Enabled: mockVisualSystemEnabled,
  }
})
vi.mock('@/lib/features-client', () => ({ useSourceVisualsEnabled: mockSourceVisualsEnabled }))

describe('SearchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockSearchData.current = undefined
    mockSearchParams.current = ''
    mockVisualSystemEnabled.mockReturnValue(false)
    mockSourceVisualsEnabled.mockReturnValue(false)
  })

  it('keeps ask controls inside the Discover folio without auto-running a request', () => {
    render(<SearchPage />)

    const routeFrameRole = isLuminousFolioEnabled() ? 'main' : 'region'
    const routeFrame = screen.getByRole(routeFrameRole, { name: 'searchPage.askAndSearch' })
    expect(routeFrame).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'searchPage.askAndSearch' })).toHaveAttribute(
      'id',
      routeFrame.getAttribute('aria-labelledby'),
    )
    expect(screen.getByText('Discover')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'searchPage.askBeta' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'common.accessibility.enterQuestion' })).toBeInTheDocument()
  })

  it('adds a compact cover and exact first-match Evidence Peek only to source results', async () => {
    const hash = 'b'.repeat(64)
    const opaqueToken = 'c'.repeat(64)
    mockVisualSystemEnabled.mockReturnValue(true)
    mockSourceVisualsEnabled.mockReturnValue(true)
    mockSearchParams.current = 'mode=search'
    mockSearchData.current = {
      total_count: 3,
      search_type: 'text',
      results: [
        {
          id: 'source:one', title: 'Source result', parent_id: '', final_score: 0.9,
          matches: ['first exact match', 'second match'], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z',
          visual: { source_id: 'source:one', content_sha256: hash, asset_sha256: hash, alt_text: 'Source evidence cover', width: 640, height: 360, mime_type: 'image/webp', asset_url: `/api/sources/source%3Aone/visual?v=${opaqueToken}`, created_at: '2026-08-10T00:00:00Z', updated_at: '2026-08-10T00:01:00Z', origin: 'embedded', source_locator: { page: 1 } },
        },
        {
          id: 'source_insight:two', title: 'Insight result', parent_id: 'source:one', final_score: 0.8,
          matches: ['insight match'], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z',
          visual: { source_id: 'source:two', content_sha256: hash, asset_sha256: hash, alt_text: 'Must not render', width: 640, height: 360, mime_type: 'image/webp', asset_url: `/api/sources/source%3Atwo/visual?v=${opaqueToken}`, created_at: '2026-08-10T00:00:00Z', updated_at: '2026-08-10T00:01:00Z', origin: 'embedded', source_locator: { page: 1 } },
        },
        {
          id: 'note:three', title: 'Note result', parent_id: 'source:one', final_score: 0.7,
          matches: ['note match'], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
        },
      ],
    }
    const locate = vi.spyOn(sourcesApi, 'locatePassage').mockResolvedValue({ start: 0, end: 5, score: 0.95, snippet: 'Exact passage' })

    render(<SearchPage />)

    expect(screen.getAllByRole('img')).toHaveLength(1)
    expect(screen.getByRole('img', { name: /Source evidence cover/ })).toBeVisible()
    expect(screen.queryByRole('img', { name: /Must not render/ })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /View evidence/ })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Source result' }))
    expect(mockOpenModal).toHaveBeenCalledWith('source', 'one')
    fireEvent.click(screen.getByRole('button', { name: 'Insight result' }))
    expect(mockOpenModal).toHaveBeenCalledWith('insight', 'two')
    fireEvent.click(screen.getByRole('button', { name: 'Note result' }))
    expect(mockOpenModal).toHaveBeenCalledWith('note', 'three')
    fireEvent.click(screen.getByRole('button', { name: 'View evidence for Source result' }))
    await waitFor(() => expect(locate).toHaveBeenCalledWith('source:one', 'first exact match'))
    expect(screen.getByRole('dialog', { name: 'Evidence in Source result' })).toBeInTheDocument()

    locate.mockRestore()
  })

  it('keeps source visuals absent when either visual gate is off', () => {
    mockVisualSystemEnabled.mockReturnValue(true)
    mockSourceVisualsEnabled.mockReturnValue(false)
    mockSearchParams.current = 'mode=search'
    mockSearchData.current = {
      total_count: 1, search_type: 'text', results: [{
        id: 'source:one', title: 'Source result', parent_id: '', final_score: 0.9,
        matches: ['match'], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
      }],
    }

    render(<SearchPage />)

    expect(screen.queryByTestId('search-result-cover-source:one')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /View evidence/ })).not.toBeInTheDocument()
  })

  it('closes stale evidence when a new search result set replaces its source', async () => {
    mockVisualSystemEnabled.mockReturnValue(true)
    mockSourceVisualsEnabled.mockReturnValue(true)
    mockSearchParams.current = 'mode=search'
    mockSearchData.current = {
      total_count: 1, search_type: 'text', results: [{
        id: 'source:old', title: 'Old source', parent_id: '', final_score: 0.9,
        matches: ['old exact match'], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
      }],
    }
    const locate = vi.spyOn(sourcesApi, 'locatePassage').mockResolvedValue(null)
    const { rerender } = render(<SearchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'View evidence for Old source' }))
    expect(await screen.findByRole('dialog', { name: 'Evidence in Old source' })).toBeInTheDocument()

    mockSearchData.current = {
      total_count: 1, search_type: 'text', results: [{
        id: 'source:new', title: 'New source', parent_id: '', final_score: 0.8,
        matches: ['new exact match'], created: '2026-08-10T00:02:00Z', updated: '2026-08-10T00:03:00Z', visual: null,
      }],
    }
    rerender(<SearchPage />)

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Evidence in Old source' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'View evidence for New source' })).toBeInTheDocument()
    locate.mockRestore()
  })

  // v0.8.116 — above VIRTUALIZE_THRESHOLD (50), results render through
  // VirtualizedListAuto. jsdom does no layout, so `offsetHeight` reads 0
  // on every element by default — react-virtual then measures a 0px
  // viewport and renders NOTHING, not an overscan window (confirmed by
  // probing VirtualizedListAuto directly: 0 rows with jsdom's default
  // 0-height container). Stubbing `offsetHeight` gives the virtualizer a
  // real viewport to compute against, which is what actually makes it
  // render its overscan window instead of the full 120 — restored after
  // the test so it doesn't affect other tests' layout assumptions.
  it('virtualizes large result sets instead of rendering every card', () => {
    const offsetHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight')
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, value: 600 })

    try {
      mockSearchParams.current = 'mode=search'
      mockSearchData.current = {
        total_count: 120,
        search_type: 'text',
        results: Array.from({ length: 120 }, (_, i) => ({
          id: `source:${i}`,
          title: `Result ${i}`,
          parent_id: '',
          final_score: 0.5,
          matches: [],
          created: '2026-08-10T00:00:00Z',
          updated: '2026-08-10T00:01:00Z',
          visual: null,
        })),
      }

      render(<SearchPage />)

      const renderedCards = screen.getAllByTestId(/^search-result-card-/)
      expect(renderedCards.length).toBeGreaterThan(0)
      expect(renderedCards.length).toBeLessThan(120)
    } finally {
      if (offsetHeightDescriptor) {
        Object.defineProperty(HTMLElement.prototype, 'offsetHeight', offsetHeightDescriptor)
      }
    }
  })

  it('renders every card for a small result set (below the virtualization threshold)', () => {
    mockSearchParams.current = 'mode=search'
    mockSearchData.current = {
      total_count: 3,
      search_type: 'text',
      results: [
        {
          id: 'source:one', title: 'Result one', parent_id: '', final_score: 0.9,
          matches: [], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
        },
        {
          id: 'source:two', title: 'Result two', parent_id: '', final_score: 0.8,
          matches: [], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
        },
        {
          id: 'source:three', title: 'Result three', parent_id: '', final_score: 0.7,
          matches: [], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
        },
      ],
    }

    render(<SearchPage />)

    expect(screen.getAllByTestId(/^search-result-card-/)).toHaveLength(3)
  })

  it('returns focus to the original evidence invoker after a parent rerender', async () => {
    mockVisualSystemEnabled.mockReturnValue(true)
    mockSourceVisualsEnabled.mockReturnValue(true)
    mockSearchParams.current = 'mode=search'
    mockSearchData.current = {
      total_count: 1, search_type: 'text', results: [{
        id: 'source:one', title: 'Source result', parent_id: '', final_score: 0.9,
        matches: ['exact match'], created: '2026-08-10T00:00:00Z', updated: '2026-08-10T00:01:00Z', visual: null,
      }],
    }
    const locate = vi.spyOn(sourcesApi, 'locatePassage').mockResolvedValue(null)
    const scrollTo = vi.spyOn(window, 'scrollTo').mockImplementation(() => undefined)
    const { rerender } = render(<SearchPage />)
    const invoker = screen.getByRole('button', { name: 'View evidence for Source result' })
    invoker.focus()
    fireEvent.click(invoker)
    expect(await screen.findByRole('dialog', { name: 'Evidence in Source result' })).toBeInTheDocument()

    rerender(<SearchPage />)
    fireEvent.click(screen.getByRole('button', { name: 'Close evidence peek' }))

    await waitFor(() => expect(invoker).toHaveFocus())
    locate.mockRestore()
    scrollTo.mockRestore()
  })
})
