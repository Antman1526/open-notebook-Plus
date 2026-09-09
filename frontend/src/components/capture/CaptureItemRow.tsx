import { useState } from 'react'
import { AudioLines, Sparkles } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { SourceCover } from '@/components/deeper-notebook/source-gallery/SourceCover'
import {
  captureApi,
  type CaptureItem,
  type CaptureRoutePreview,
} from '@/lib/api/capture'
import type { SourceListResponse } from '@/lib/types/api'

const stateVariant = (state: CaptureItem['state']) =>
  state === 'failed' ? 'destructive' : state === 'imported' ? 'secondary' : 'outline'

const mediaExtensions = new Set([
  '.aac',
  '.flac',
  '.m4a',
  '.mkv',
  '.mov',
  '.mp3',
  '.mp4',
  '.wav',
  '.webm',
])

function sourceFromLinkedItem(item: CaptureItem): SourceListResponse | null {
  if (!item.linked_source) return null
  const timestamp = item.modified_ns === null ? '' : String(item.modified_ns)
  return {
    id: item.linked_source.id,
    title: item.filename,
    source_type: 'upload',
    asset: null,
    embedded: false,
    embedded_chunks: 0,
    insights_count: 0,
    created: timestamp,
    updated: timestamp,
    visual: item.linked_source.visual,
  }
}

export function CaptureItemRow({ item, showVisualCover = false }: { item: CaptureItem; showVisualCover?: boolean }) {
  const [preview, setPreview] = useState<CaptureRoutePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isRouting, setIsRouting] = useState(false)
  const canPreview =
    mediaExtensions.has(item.extension.toLowerCase()) &&
    (item.state === 'ready' || item.state === 'duplicate')
  const linkedSource = showVisualCover ? sourceFromLinkedItem(item) : null

  async function previewRoute() {
    setIsRouting(true)
    setError(null)
    try {
      setPreview(await captureApi.route(`${item.root_path}/${item.relative_path}`))
    } catch {
      setError(
        'This local file could not be prepared for review. It was not imported or moved.'
      )
    } finally {
      setIsRouting(false)
    }
  }

  return (
    <article className="group relative rounded-xl border border-border/60 bg-card/70 p-3.5 transition-all duration-150 hover:border-primary/40 hover:bg-card/95 hover:shadow-xs">
      {linkedSource ? (
        <div className="mb-3 max-w-xs" data-testid="capture-linked-source-cover">
          <SourceCover source={linkedSource} variant="compact" />
        </div>
      ) : null}
      <div className="grid gap-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{item.filename}</p>
          <p className="truncate text-xs text-muted-foreground">
            {item.relative_path} · {item.extension || 'unknown type'}
            {item.byte_size ? ` · ${(item.byte_size / 1024).toFixed(1)} KB` : ''}
          </p>
          {item.reason ? (
            <p className="mt-1 text-xs text-destructive">{item.reason}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Badge variant={stateVariant(item.state)} className="w-fit rounded-full px-2.5 py-0.5 text-[11px] font-mono font-medium">
            {item.state}
          </Badge>
          {canPreview ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={isRouting}
              onClick={() => void previewRoute()}
              className="rounded-xl active:scale-95 transition-all duration-150 gap-1.5 text-xs"
            >
              <AudioLines className="h-3.5 w-3.5 text-primary" />
              {isRouting ? 'Preparing' : 'Review route'}
            </Button>
          ) : null}
        </div>
      </div>
      {error ? (
        <p role="alert" className="mt-3 text-xs text-destructive">
          {error}
        </p>
      ) : null}
      {preview ? (
        <div className="mt-3 border-l-2 border-primary/40 pl-3 text-sm">
          <p className="font-medium">
            {preview.state === 'ready'
              ? 'Local transcript preview'
              : 'Route unavailable'}
          </p>
          {preview.transcript ? (
            <p className="mt-1 whitespace-pre-wrap text-muted-foreground">
              {preview.transcript}
            </p>
          ) : (
            <p className="mt-1 text-muted-foreground">
              {preview.reason === 'no_default_speech_to_text_model'
                ? 'Choose a local speech-to-text model to generate a transcript.'
                : 'The configured local speech-to-text model is currently unavailable.'}
            </p>
          )}
          {preview.notebook_suggestions.length ? (
            <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
              {preview.notebook_suggestions.map((suggestion) => (
                <li key={suggestion.id} className="flex gap-2">
                  <Sparkles className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" />
                  <span>
                    <span className="font-medium text-foreground">
                      {suggestion.name}
                    </span>{' '}
                    · {suggestion.reason}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          <p className="mt-2 text-xs text-muted-foreground">
            Review only. The original file remains where it is until you
            explicitly import it.
          </p>
        </div>
      ) : null}
    </article>
  )
}
