import { beforeEach, describe, expect, it, vi } from 'vitest'

const apiPost = vi.fn()
const apiGet = vi.fn()

vi.mock('@/lib/api/client', () => ({
  default: {
    post: (...args: unknown[]) => apiPost(...args),
    get: (...args: unknown[]) => apiGet(...args),
  },
}))

import { sourcesApi } from './sources'

function sourceResponse() {
  return {
    id: 'source:1',
    title: 'paper.pdf',
    asset: null,
    full_text: '',
    embedded: false,
    embedded_chunks: 0,
    insights_count: 0,
    created: '2026-06-23T00:00:00Z',
    updated: '2026-06-23T00:00:00Z',
  }
}

describe('sourcesApi source creation helpers', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('strictly decodes list and detail visuals but fails soft only for visual fields', async () => {
    const base = sourceResponse()
    const listBase = { ...base }
    delete (listBase as { full_text?: unknown }).full_text
    const visual = {
      source_id: 'source:1', content_sha256: 'a'.repeat(64), asset_sha256: 'b'.repeat(64),
      origin: 'embedded', source_locator: { page: 1 }, alt_text: 'A useful figure',
      width: 640, height: 360, mime_type: 'image/webp',
      asset_url: `/api/sources/source%3A1/visual?v=${'c'.repeat(64)}`,
      created_at: base.created, updated_at: base.updated,
    }
    apiGet.mockResolvedValueOnce({ data: [{ ...listBase, visual, visual_status: null }] })
    await expect(sourcesApi.list()).resolves.toEqual([expect.objectContaining({ visual })])

    apiGet.mockResolvedValueOnce({ data: { ...base, visual: { ...visual, mime_type: 'image/svg+xml' }, visual_status: { state: 'bad' } } })
    await expect(sourcesApi.get('source:1')).resolves.toEqual(expect.objectContaining({ id: 'source:1', visual: null, visual_status: null }))

    apiGet.mockResolvedValueOnce({ data: [{ ...listBase, private_path: '/tmp/source' }] })
    await expect(sourcesApi.list()).rejects.toThrow()
  })

  it('uses the multi-notebook upload contract and keeps embedding enabled', async () => {
    apiPost.mockResolvedValue({
      data: sourceResponse(),
    })

    const file = new File(['test'], 'paper.pdf', { type: 'application/pdf' })

    await sourcesApi.upload(file, 'notebook:alpha')

    expect(apiPost).toHaveBeenCalledOnce()
    const [path, body, config] = apiPost.mock.calls[0]
    expect(path).toBe('/sources')
    expect(config).toEqual({
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    })

    const formData = body as FormData
    expect(formData.get('file')).toBe(file)
    expect(formData.get('notebooks')).toBe(JSON.stringify(['notebook:alpha']))
    expect(formData.get('notebook_id')).toBe('notebook:alpha')
    expect(formData.get('type')).toBe('upload')
    expect(formData.get('embed')).toBe('true')
    expect(formData.get('delete_source')).toBe('false')
    expect(formData.get('async_processing')).toBe('true')
  })

  it('defaults general source creation to searchable queued processing', async () => {
    apiPost.mockResolvedValue({ data: sourceResponse() })

    await sourcesApi.create({
      type: 'link',
      url: 'https://example.com/research',
      notebooks: ['notebook:alpha'],
    })

    expect(apiPost).toHaveBeenCalledOnce()
    const [path, body] = apiPost.mock.calls[0]
    expect(path).toBe('/sources')
    const formData = body as FormData
    expect(formData.get('type')).toBe('link')
    expect(formData.get('url')).toBe('https://example.com/research')
    expect(formData.get('notebooks')).toBe(JSON.stringify(['notebook:alpha']))
    expect(formData.get('embed')).toBe('true')
    expect(formData.get('async_processing')).toBe('true')
  })

  it('keeps explicit embed=false and async_processing=false for ingest-only source creation', async () => {
    apiPost.mockResolvedValue({ data: sourceResponse() })

    await sourcesApi.create({
      type: 'text',
      title: 'Archive only',
      content: 'Do not embed this note',
      notebooks: ['notebook:alpha'],
      embed: false,
      async_processing: false,
    })

    const [, body] = apiPost.mock.calls[0]
    const formData = body as FormData
    expect(formData.get('embed')).toBe('false')
    expect(formData.get('async_processing')).toBe('false')
  })
})
