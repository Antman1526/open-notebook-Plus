// v0.8.124 — mind-map node preview popover tests (improvement roadmap).
import React from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

const setEpisodeMock = vi.hoisted(() => vi.fn())
const listArtifactsMock = vi.hoisted(() => vi.fn())
const listEpisodesMock = vi.hoisted(() => vi.fn())
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
}) {
  useQueryMock.mockImplementation((config: { queryKey: unknown[] }) => {
    const [scope] = config.queryKey
    if (scope === 'studio') {
      return { data: opts.artifact ?? null, isLoading: false }
    }
    if (scope === 'podcasts') {
      return { data: opts.episodes ?? [], isLoading: false }
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
        artifactId="a1"
        artifactType="podcast_audio"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
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
        artifactId="a1"
        artifactType="podcast_audio"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
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
        artifactId="a2"
        artifactType="slide_deck"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={vi.fn()}
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
        artifactId="a3"
        artifactType="report"
        anchor={{ x: 10, y: 10 }}
        onClose={onClose}
        onOpenArtifact={vi.fn()}
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
        artifactId="a3"
        artifactType="report"
        anchor={{ x: 10, y: 10 }}
        onClose={vi.fn()}
        onOpenArtifact={onOpenArtifact}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Open in Studio' }))
    expect(onOpenArtifact).toHaveBeenCalledWith('a3')
  })
})
