// v0.8.124 — mind-map node preview popover tests (improvement roadmap).
// v0.8.125 — source/note node preview tests (improvement roadmap).
import React, { useEffect, useRef, useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

const setEpisodeMock = vi.hoisted(() => vi.fn())
const listArtifactsMock = vi.hoisted(() => vi.fn())
const listEpisodesMock = vi.hoisted(() => vi.fn())
const sourcesGetMock = vi.hoisted(() => vi.fn())
const notesGetMock = vi.hoisted(() => vi.fn())
const resolvePodcastAssetUrlMock = vi.hoisted(() =>
  vi.fn(async (path?: string | null) => (path ? `https://cdn.test${path}` : undefined))
)
const useQueryMock = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-query', () => ({
  useQuery: (...args: unknown[]) => useQueryMock(...args),
}))

vi.mock('@/lib/api/studio', () => ({
  studioApi: { listArtifacts: (...args: unknown[]) => listArtifactsMock(...args) },
}))

vi.mock('@/lib/api/podcasts', () => ({
  podcastsApi: { listEpisodes: (...args: unknown[]) => listEpisodesMock(...args) },
  resolvePodcastAssetUrl: (path?: string | null) => resolvePodcastAssetUrlMock(path),
}))

vi.mock('@/lib/api/sources', () => ({
  sourcesApi: { get: (...args: unknown[]) => sourcesGetMock(...args) },
}))

vi.mock('@/lib/api/notes', () => ({
  notesApi: { get: (...args: unknown[]) => notesGetMock(...args) },
}))

vi.mock('@/components/deeper-notebook/source-gallery/SourceCover', () => ({
  SourceCover: ({ source }: { source: { id?: string | null } }) => (
    <div data-testid="source-cover">{source?.id}</div>
  ),
}))

vi.mock('@/lib/stores/audio-player-store', () => ({
  useAudioPlayerStore: (selector: (state: { setEpisode: typeof setEpisodeMock }) => unknown) =>
    selector({ setEpisode: setEpisodeMock }),
}))

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({ t: (_key: string, options?: { defaultValue?: string }) => options?.defaultValue ?? '' }),
}))

import MindMapNodePreview from './MindMapNodePreview'

function mockQueries(opts: {
  artifact?: Record<string, unknown> | null
  episodes?: Array<Record<string, unknown>>
  source?: Record<string, unknown> | null
  note?: Record<string, unknown> | null
}) {
  useQueryMock.mockImplementation((config: { queryKey: unknown[] }) => {
    const [scope, kind] = config.queryKey
    if (scope === 'studio') {
      return { data: opts.artifact ?? null, isLoading: false }
    }
    if (scope === 'podcasts') {
      return { data: opts.episodes ?? [], isLoading: false }
    }
    if (scope === 'mindmap' && kind === 'source') {
      return { data: opts.source ?? null, isLoading: false }
    }
    if (scope === 'mindmap' && kind === 'note') {
      return { data: opts.note ?? null, isLoading: false }
    }
    return { data: undefined, isLoading: false }
  })
}

describe('MindMapNodePreview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders a Play button for podcast_audio and calls setEpisode on click', () => {
    mockQueries({
      artifact: {
        id: 'a1',
        title: 'Audio Overview',
        artifact_type: 'podcast_audio',
        output_payload: { episode_id: 'ep1' },
      },
      episodes: [
        { id: 'ep1', name: 'Ep One', audio_url: '/media/ep1.mp3', transcript_segments: [] },
      ],
    })

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="studio_artifact"
        nodeId="a1"
        artifactType="podcast_audio"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
        onOpenSource={vi.fn()}
        onOpenNote={vi.fn()}
      />
    )

    const playButton = screen.getByRole('button', { name: 'Play' })
    fireEvent.click(playButton)

    expect(setEpisodeMock).toHaveBeenCalledWith({
      id: 'ep1',
      title: 'Ep One',
      sourcePath: '/media/ep1.mp3',
      transcriptSegments: [],
    })
  })

  it('shows the unavailable copy for podcast_audio when no episode resolves', () => {
    mockQueries({
      artifact: {
        id: 'a1',
        title: 'Audio Overview',
        artifact_type: 'podcast_audio',
        output_payload: {},
      },
      episodes: [],
    })

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="studio_artifact"
        nodeId="a1"
        artifactType="podcast_audio"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
        onOpenSource={vi.fn()}
        onOpenNote={vi.fn()}
      />
    )

    expect(screen.getByText('Preview unavailable.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Play' })).not.toBeInTheDocument()
  })

  it('renders a <video> for a slide deck with a video_overview', async () => {
    mockQueries({
      artifact: {
        id: 'a2',
        title: 'Deck',
        artifact_type: 'slide_deck',
        output_payload: {
          video_overview: { media_url: '/media/v.mp4', captions_url: '/media/v.vtt' },
        },
      },
    })

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="studio_artifact"
        nodeId="a2"
        artifactType="slide_deck"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
        onOpenSource={vi.fn()}
        onOpenNote={vi.fn()}
      />
    )

    await waitFor(() => {
      expect(document.querySelector('video')).toBeTruthy()
    })
    const source = document.querySelector('video source')
    expect(source).toHaveAttribute('src', 'https://cdn.test/media/v.mp4')
  })

  it('calls onClose on Escape', () => {
    mockQueries({ artifact: { id: 'a3', title: 'Report', artifact_type: 'report', output_payload: {} } })
    const onClose = vi.fn()

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="studio_artifact"
        nodeId="a3"
        artifactType="report"
        anchor={{ x: 10, y: 10 }}
        onClose={onClose}
        onOpenArtifact={vi.fn()}
        onOpenSource={vi.fn()}
        onOpenNote={vi.fn()}
      />
    )

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('calls onOpenArtifact when Open is clicked', () => {
    mockQueries({ artifact: { id: 'a3', title: 'Report', artifact_type: 'report', output_payload: {} } })
    const onOpenArtifact = vi.fn()

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="studio_artifact"
        nodeId="a3"
        artifactType="report"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={onOpenArtifact}
        onOpenSource={vi.fn()}
        onOpenNote={vi.fn()}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open in Studio' }))
    expect(onOpenArtifact).toHaveBeenCalledWith('a3')
  })

  // v0.8.125 — source node preview.
  it('renders a source title + excerpt and calls onOpenSource on Open', () => {
    mockQueries({
      source: {
        id: 's1',
        title: 'A Long Source',
        embedded: true,
        embedded_chunks: 3,
        insights_count: 1,
        full_text: 'x'.repeat(300),
      },
    })
    const onOpenSource = vi.fn()

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="source"
        nodeId="s1"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
        onOpenSource={onOpenSource}
        onOpenNote={vi.fn()}
      />
    )

    expect(screen.getByText('A Long Source')).toBeInTheDocument()
    expect(screen.getByText(`${'x'.repeat(240)}…`)).toBeInTheDocument()
    expect(screen.getByTestId('source-cover')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open source' }))
    expect(onOpenSource).toHaveBeenCalledWith('s1')
  })

  // v0.8.125 — note node preview.
  it('renders a note title + excerpt and calls onOpenNote on Open', () => {
    mockQueries({
      note: {
        id: 'n1',
        title: 'My Note',
        content: 'y'.repeat(300),
      },
    })
    const onOpenNote = vi.fn()

    render(
      <MindMapNodePreview
        notebookId="nb1"
        nodeType="note"
        nodeId="n1"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
        onOpenSource={vi.fn()}
        onOpenNote={onOpenNote}
      />
    )

    expect(screen.getByText('My Note')).toBeInTheDocument()
    expect(screen.getByText(`${'y'.repeat(240)}…`)).toBeInTheDocument()
    expect(screen.queryByTestId('source-cover')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open note' }))
    expect(onOpenNote).toHaveBeenCalledWith('n1')
  })

  // v0.8.126 — popover clipping fix: with an anchor near the container's
  // bottom-right edge, the popover must be shifted left/up to stay inside
  // (12px margin), not rendered off-screen at the raw anchor coordinates.
  it('clamps the popover position inside the container when the anchor is near the bottom-right edge', () => {
    mockQueries({ note: { id: 'n1', title: 'Edge Note', content: 'hi' } })

    const rectSpy = vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({
      width: 800,
      height: 600,
      top: 0,
      left: 0,
      right: 800,
      bottom: 600,
      x: 0,
      y: 0,
      toJSON: () => {},
    } as DOMRect)

    // Mirrors MindMap.tsx's real usage: the canvas wrapper (canvasRef) is
    // already mounted, unconditionally, well before the preview ever
    // appears (it's only rendered after a node click). So mount the
    // container first, then reveal the preview on a follow-up render —
    // matching that timing instead of mounting both in the same commit.
    function Wrapper() {
      const containerRef = useRef<HTMLDivElement>(null)
      const [show, setShow] = useState(false)
      useEffect(() => {
        setShow(true)
      }, [])
      return (
        <div ref={containerRef}>
          {show && (
            <MindMapNodePreview
              notebookId="nb1"
              nodeType="note"
              nodeId="n1"
              anchor={{ x: 790, y: 590 }}
              containerRef={containerRef}
              onClose={vi.fn()}
              onOpenArtifact={vi.fn()}
              onOpenSource={vi.fn()}
              onOpenNote={vi.fn()}
            />
          )}
        </div>
      )
    }

    render(<Wrapper />)

    const dialog = screen.getByRole('dialog')
    const left = parseFloat(dialog.style.left)
    const top = parseFloat(dialog.style.top)

    // Popover is w-64 (256px) with a 320px max-height budget and a 12px
    // margin/offset — see MindMapNodePreview.tsx's clampAnchor().
    expect(left).toBe(660) // 800 - 12 - 128
    expect(top).toBe(256) // 600 - 12 - 320 - 12
    expect(left).toBeLessThan(790)
    expect(top).toBeLessThan(590)
    expect(left).toBeGreaterThanOrEqual(12)
    expect(top).toBeGreaterThanOrEqual(12)

    rectSpy.mockRestore()
  })
})
