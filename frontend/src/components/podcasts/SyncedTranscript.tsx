'use client'

import { useState, useEffect, useRef } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { ArrowDownCircle, Download, Copy, Check, Sparkles } from 'lucide-react'
import { toast } from 'sonner'
import type { TranscriptSegment } from '@/lib/types/podcasts'

interface SyncedTranscriptProps {
  segments: TranscriptSegment[]
  currentTime: number
  onSeek: (seconds: number) => void
  onCitationClick?: (citationId: string) => void
}

function formatTimestamp(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds))
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, '0')}`
}

function formatVTTTimestamp(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  const ms = Math.floor((seconds % 1) * 1000)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(3, '0')}`
}

export function SyncedTranscript({
  segments,
  currentTime,
  onSeek,
  onCitationClick,
}: SyncedTranscriptProps) {
  const [autoScroll, setAutoScroll] = useState(true)
  const [copied, setCopied] = useState(false)
  const segmentRefs = useRef<(HTMLElement | null)[]>([])

  const activeIndex = segments.findIndex(
    (s) => currentTime >= s.start_seconds && currentTime < s.end_seconds,
  )

  useEffect(() => {
    if (
      autoScroll &&
      activeIndex >= 0 &&
      typeof segmentRefs.current[activeIndex]?.scrollIntoView === 'function'
    ) {
      segmentRefs.current[activeIndex]?.scrollIntoView({
        behavior: 'smooth',
        block: 'nearest',
      })
    }
  }, [activeIndex, autoScroll])

  if (segments.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Transcript timing is not available for this overview.
      </p>
    )
  }

  const handleCopyMarkdown = async () => {
    const md = segments
      .map(
        (s) =>
          `**[${formatTimestamp(s.start_seconds)}] ${s.speaker}:** ${s.text}`,
      )
      .join('\n\n')
    try {
      await navigator.clipboard.writeText(md)
      setCopied(true)
      toast.success('Transcript copied as Markdown')
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error('Failed to copy transcript')
    }
  }

  const handleDownloadVTT = () => {
    let vtt = 'WEBVTT\n\n'
    segments.forEach((s, i) => {
      vtt += `${i + 1}\n${formatVTTTimestamp(s.start_seconds)} --> ${formatVTTTimestamp(s.end_seconds)}\n<v ${s.speaker}>${s.text}\n\n`
    })
    const blob = new Blob([vtt], { type: 'text/vtt;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'transcript.vtt'
    a.click()
    URL.revokeObjectURL(url)
    toast.success('Downloaded transcript as WebVTT (.vtt)')
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 border-b border-border/40 pb-1.5 text-xs text-muted-foreground">
        <div className="flex items-center gap-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setAutoScroll(!autoScroll)}
            className={cn(
              'h-6 px-2 text-xs gap-1',
              autoScroll ? 'text-primary font-medium' : 'text-muted-foreground',
            )}
            title="Toggle automatic scrolling to current playback position"
          >
            <ArrowDownCircle className="h-3.5 w-3.5" />
            Auto-scroll: {autoScroll ? 'On' : 'Off'}
          </Button>
          {activeIndex >= 0 && (
            <Badge variant="secondary" className="text-[10px] px-1.5 py-0 h-4">
              {segments[activeIndex]?.speaker}
            </Badge>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleCopyMarkdown}
            className="h-6 px-2 text-xs gap-1"
            title="Copy full transcript with timestamps"
          >
            {copied ? <Check className="h-3 w-3 text-emerald-500" /> : <Copy className="h-3 w-3" />}
            Copy
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={handleDownloadVTT}
            className="h-6 px-2 text-xs gap-1"
            title="Download transcript as WebVTT for media players"
          >
            <Download className="h-3 w-3" />
            VTT
          </Button>
        </div>
      </div>

      <div
        aria-label="Synced transcript"
        className="max-h-56 space-y-2 overflow-y-auto pr-1"
      >
        {segments.map((segment, index) => {
          const active = index === activeIndex
          return (
            <article
              key={`${segment.start_seconds}-${index}`}
              ref={(el) => {
                segmentRefs.current[index] = el
              }}
              className={cn(
                'rounded-md border p-3 text-sm transition-all duration-200',
                active
                  ? 'border-primary bg-primary/8 shadow-xs ring-1 ring-primary/20'
                  : 'bg-background hover:bg-muted/30',
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className={cn(
                    'h-auto px-0 py-0 font-medium',
                    active ? 'text-primary font-semibold' : 'text-foreground',
                  )}
                  onClick={() => onSeek(segment.start_seconds)}
                >
                  {formatTimestamp(segment.start_seconds)} {segment.speaker}
                </Button>
                {segment.citation_ids.map((citationId) => (
                  <Button
                    key={citationId}
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-5 text-xs px-1.5"
                    onClick={() => onCitationClick?.(citationId)}
                  >
                    {citationId}
                  </Button>
                ))}
              </div>
              <p
                className={cn(
                  'mt-1 whitespace-pre-wrap text-sm leading-relaxed',
                  active ? 'text-foreground font-normal' : 'text-muted-foreground',
                )}
              >
                {segment.text}
              </p>
            </article>
          )
        })}
      </div>
    </div>
  )
}

