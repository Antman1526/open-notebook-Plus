/**
 * ONP v0.7.0 — Studio page.
 *
 * Single-page workflow: drag-and-drop files → pick mode → click Generate →
 * router pushes to /notebooks/{id} on success.
 *
 * Design choices:
 *  - Inline state, no Zustand store. The workflow is one-shot; no need
 *    for cross-component sharing.
 *  - Drag-and-drop OR file-picker — both go through the same handleFiles
 *    helper to validate + dedupe.
 *  - Per-file validation matches the backend's _ALLOWED_EXTENSIONS list.
 *    We do this client-side too so the user gets immediate feedback
 *    rather than a backend 400 after the upload completes.
 *  - Podcast mode shows the EpisodeProfile + SpeakerProfile dropdowns,
 *    populated from existing /api/episode-profiles + /api/speaker-profiles.
 *  - On success: small toast + router.push(/notebooks/{id}). The user
 *    can then chat-with-sources or wait for podcast generation to finish.
 *  - Warnings from the backend (non-fatal extraction issues) surface as
 *    a yellow banner before navigating.
 */
'use client'

import { useState, useCallback, useRef, useMemo, DragEvent, ChangeEvent, KeyboardEvent } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { Upload, FileText, X, Loader2, AlertCircle, BookOpen, Mic, ArrowLeft, Sparkles, Link2, GraduationCap } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useToast } from '@/lib/hooks/use-toast'
import { useStudioCoursePack, useStudioGenerate } from '@/lib/hooks/use-studio'
import { StudioMode } from '@/lib/api/studio'
import apiClient from '@/lib/api/client'
// v0.7.1 — use existing QUERY_KEYS so Studio's profile fetches share
// cache with use-podcasts.ts (and pick up invalidations from profile
// mutations). Previously this file declared its own raw keys, causing
// duplicate network requests + stale data.
import { QUERY_KEYS } from '@/lib/api/query-client'
// v0.7.196 — sanitize raw `error.message` previously shown as the
// toast description on Studio generation failure. Also routes the
// `mutation.error` chain through the same helper for the inline
// Alert below the form. The Studio page's English-only static
// strings remain — full i18n extraction is deferred (see CHANGELOG).
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { EvidenceStudioFolio } from '@/components/deeper-notebook/studios/EvidenceStudioFolio'

// Must match api/routers/studio.py:_ALLOWED_EXTENSIONS
const ALLOWED_EXTS = new Set([
  '.pdf', '.doc', '.docx', '.txt', '.md', '.markdown',
  '.ppt', '.pptx', '.html', '.htm',
  '.mp3', '.mp4', '.m4a', '.wav', '.mov',
])
const MAX_FILE_MB = 50
type StudioOutputMode = StudioMode | 'course_pack'

interface ProfileSummary {
  name: string
  description?: string | null
}

function fileExt(name: string): string {
  const i = name.lastIndexOf('.')
  return i < 0 ? '' : name.slice(i).toLowerCase()
}

function parseLinks(raw: string): string[] {
  const seen = new Set<string>()
  return raw
    .split(/[\n,]+/)
    .map((value) => value.trim())
    .filter((value) => {
      if (!value || seen.has(value)) return false
      seen.add(value)
      return true
    })
}

// v0.7.203 — isAllowed returns a key + interpolation map so the
// reason can be translated in the caller (which has access to t()
// via the useTranslation hook). Returning a pre-formatted English
// string would leak English into non-English users' toasts.
interface RejectionReason {
  key: 'studio.unsupportedType' | 'studio.fileTooLarge'
  params: Record<string, string>
}
function isAllowed(file: File): { ok: boolean; reason?: RejectionReason } {
  const ext = fileExt(file.name)
  if (!ALLOWED_EXTS.has(ext)) {
    return {
      ok: false,
      reason: {
        key: 'studio.unsupportedType',
        params: { ext: ext || '(no extension)' },
      },
    }
  }
  if (file.size > MAX_FILE_MB * 1024 * 1024) {
    return {
      ok: false,
      reason: {
        key: 'studio.fileTooLarge',
        params: {
          size: String(Math.round(file.size / 1024 / 1024)),
          cap: String(MAX_FILE_MB),
        },
      },
    }
  }
  return { ok: true }
}

export default function StudioPage() {
  const router = useRouter()
  const { toast } = useToast()
  const { t } = useTranslation()
  const mutation = useStudioGenerate()
  const coursePackMutation = useStudioCoursePack()
  const fileInputRef = useRef<HTMLInputElement>(null)

  const [files, setFiles] = useState<File[]>([])
  const [mode, setMode] = useState<StudioOutputMode>('notebook')
  const [title, setTitle] = useState('')
  const [linkText, setLinkText] = useState('')
  const [isDragging, setIsDragging] = useState(false)
  const [episodeProfile, setEpisodeProfile] = useState('')
  const [speakerProfile, setSpeakerProfile] = useState('')

  // Fetch episode + speaker profiles for the podcast mode dropdowns.
  // Cheap — both queries return a small list and TanStack caches them.
  const { data: episodeProfiles = [] } = useQuery<ProfileSummary[]>({
    queryKey: QUERY_KEYS.episodeProfiles,
    queryFn: async () => (await apiClient.get('/episode-profiles')).data,
    enabled: mode === 'podcast' || mode === 'both',
  })
  const { data: speakerProfiles = [] } = useQuery<ProfileSummary[]>({
    queryKey: QUERY_KEYS.speakerProfiles,
    queryFn: async () => (await apiClient.get('/speaker-profiles')).data,
    enabled: mode === 'podcast' || mode === 'both',
  })

  // ----- File handling -----
  const addFiles = useCallback((incoming: FileList | File[]) => {
    const rejected: { name: string; reason: string }[] = []
    const accepted: File[] = []
    for (const f of Array.from(incoming)) {
      const { ok, reason } = isAllowed(f)
      if (!ok) {
        // v0.7.203 — format the rejection reason via t() with the
        // returned key+params, so the user sees a translated string
        // instead of hardcoded English. Fallback string is a last-
        // resort safety in case isAllowed ever returns ok=false with
        // no reason object.
        const reasonText = reason
          ? Object.entries(reason.params).reduce(
              (s, [k, v]) => s.replace(`{${k}}`, v),
              t(reason.key),
            )
          : 'rejected'
        rejected.push({ name: f.name, reason: reasonText })
        continue
      }
      // Dedupe by (name, size) — close enough without computing a hash
      if (files.some((existing) => existing.name === f.name && existing.size === f.size)) {
        continue
      }
      accepted.push(f)
    }
    if (accepted.length > 0) setFiles((prev) => [...prev, ...accepted])
    if (rejected.length > 0) {
      // v0.7.203 — full i18n. The reason strings inside `r.reason`
      // (unsupportedType / fileTooLarge) are now generated via t()
      // in isAllowed below; the heading uses the singular/plural
      // pair from the locale file.
      const headingKey = rejected.length === 1
        ? 'studio.filesRejected'
        : 'studio.filesRejectedPlural'
      toast({
        title: t(headingKey).replace('{count}', String(rejected.length)),
        description: rejected.map((r) => `${r.name}: ${r.reason}`).join('; '),
        variant: 'destructive',
      })
    }
  }, [files, toast, t])

  const removeFile = (index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }

  // ----- DnD handlers -----
  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(true)
  }
  const onDragLeave = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
  }
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files)
  }
  const onFileInputChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) addFiles(e.target.files)
    // Reset input so re-selecting the same file fires onchange again
    if (e.target) e.target.value = ''
  }

  // v0.7.1 — keyboard activation for the drop zone. The element advertises
  // itself as role="button" + tabIndex={0}, so screen readers and
  // keyboard-only users expect Enter/Space to activate. Without this
  // handler, focus lands on the zone but nothing happens — WCAG 2.1.1
  // (keyboard) violation. Standard fix: handle Enter + Space, preventDefault
  // on Space to stop page scroll.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      fileInputRef.current?.click()
    }
  }

  // ----- Submit -----
  const parsedLinks = useMemo(() => parseLinks(linkText), [linkText])
  const hasSourceInputs = files.length > 0 || parsedLinks.length > 0
  const isPending = mutation.isPending || coursePackMutation.isPending
  const canSubmit = hasSourceInputs && !isPending && (
    mode === 'notebook' || mode === 'course_pack' || (episodeProfile && speakerProfile)
  )
  const requiresPodcastProfiles = mode === 'podcast' || mode === 'both'

  const successTitleKey = mode === 'course_pack'
    ? 'studio.coursePackQueued'
    : mode === 'notebook'
      ? 'studio.notebookGenerated'
      : mode === 'both'
        ? 'studio.bothJobStarted'
        : 'studio.podcastJobStarted'
  const successDescriptionKey = mode === 'course_pack'
    ? 'studio.coursePackQueuedDescription'
    : mode === 'notebook'
      ? 'studio.notebookGeneratedDescription'
      : mode === 'both'
        ? 'studio.bothJobStartedDescription'
        : 'studio.podcastJobStartedDescription'
  const generatingText = mode === 'course_pack'
    ? t('studio.queuingCoursePack')
    : mode === 'notebook'
      ? t('studio.generatingNotebook')
      : mode === 'both'
        ? t('studio.generatingBoth')
        : t('studio.generatingPodcast')
  const generateText = mode === 'course_pack'
    ? t('studio.generateCoursePack')
    : mode === 'notebook'
      ? t('studio.generateNotebook')
      : mode === 'both'
        ? t('studio.generateBoth')
        : t('studio.generatePodcast')

  const onGenerate = async () => {
    try {
      if (mode === 'course_pack') {
        const result = await coursePackMutation.mutateAsync({
          files,
          links: parsedLinks,
          title: title.trim() || undefined,
        })
        if (result.warnings.length > 0) {
          toast({
            title: t('studio.generatedWithWarnings'),
            description: result.warnings.join('; '),
          })
        } else {
          toast({
            title: result.generationStatus === 'queued'
              ? t('studio.coursePackQueued')
              : t(successTitleKey),
            description: result.generationStatus === 'queued'
              ? t('studio.coursePackQueuedDescription').replace('{count}', String(result.sources.length))
              : t(successDescriptionKey).replace('{count}', String(result.sources.length)),
          })
        }
        router.push(`/notebooks/${encodeURIComponent(result.notebook.id)}`)
        return
      }

      const result = await mutation.mutateAsync({
        files,
        links: parsedLinks,
        mode,
        title: title.trim() || undefined,
        episode_profile_name: requiresPodcastProfiles ? episodeProfile : undefined,
        speaker_profile_name: requiresPodcastProfiles ? speakerProfile : undefined,
      })
      // Warnings, if any
      if (result.warnings.length > 0) {
        // v0.7.203 — i18n.
        toast({
          title: t('studio.generatedWithWarnings'),
          description: result.warnings.join('; '),
        })
      } else {
        toast({
          title: t(successTitleKey),
          description: t(successDescriptionKey).replace('{jobId}', result.job_id ?? ''),
        })
      }
      router.push(`/notebooks/${encodeURIComponent(result.notebook_id)}`)
    } catch (e) {
      // v0.7.196 — was ad-hoc unwrap of axios `response.data.detail`
      // and bare `(e as Error).message` fallback — both could surface
      // raw stack-trace text. Route through getApiErrorMessage so
      // mapped backend errors (apiErrors.*) are translated and
      // unmapped errors fall back to the backend's user-friendly
      // detail string only.
      toast({
        title: t('studio.generationFailed'),
        description: getApiErrorMessage(e, t, 'apiErrors.genericError'),
        variant: 'destructive',
      })
    }
  }

  // ----- Render -----
  // v0.7.23 — wrap in AppShell so the persistent left sidebar is visible
  // (matches every other dashboard page). Studio previously rendered a
  // bare <div> with no nav — users had no way back to the main page short
  // of the browser back button. Also adds an explicit "Back to Notebooks"
  // header link as a one-click escape hatch independent of the sidebar.
  return (
    <AppShell>
      <div className="flex-1 overflow-y-auto">
        <EvidenceStudioFolio status={<>
          <div className="mb-4">
            <Link href="/notebooks">
              <Button variant="ghost" size="sm">
                <ArrowLeft className="mr-2 h-4 w-4" />
                {t('studio.backToNotebooks')}
              </Button>
            </Link>
          </div>
          {/* v0.7.164 — H1 hierarchy sweep. Was `text-2xl font-bold`
              with `mb-1` between title and description; now matches
              the v0.7.153 dashboard standard
              (`text-3xl font-semibold tracking-tight`) with a
              `space-y-2` stack for proper breathing. Subtitle
              bumped from `text-sm` to default body size — the
              Studio is a flagship feature; the explainer copy
              shouldn't read as a footnote. */}
          <header className="mb-6 space-y-2">
            <h1 className="text-3xl font-semibold tracking-tight">{t('studio.title')}</h1>
            <p className="text-muted-foreground max-w-3xl">
              {t('studio.subtitle')}
            </p>
          </header>
        </>} sourceDesk={<>

      <div className="mb-6 group relative rounded-2xl p-[1.5px] bg-gradient-to-b from-border/80 via-border/40 to-border/20 shadow-sm hover:shadow-md transition-all duration-200">
        <div className="rounded-[14.5px] bg-card/95 backdrop-blur-sm p-6 space-y-6 border border-white/[0.04] dark:border-white/[0.02] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
          <div>
            <h2 className="text-lg font-semibold text-foreground">{t('studio.step1Title')}</h2>
            <p className="text-sm text-muted-foreground mt-1">
              {t('studio.step1Description').replace('{max}', String(MAX_FILE_MB))}
            </p>
          </div>

          <div
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            onClick={() => fileInputRef.current?.click()}
            onKeyDown={onKeyDown}
            className={cn(
              "border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-all duration-200",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              isDragging
                ? "border-primary bg-primary/[0.08] shadow-[0_0_24px_rgba(45,212,191,0.15)]"
                : "border-muted-foreground/25 hover:border-primary/40 hover:bg-muted/20"
            )}
            role="button"
            tabIndex={0}
            aria-label={t('studio.uploadFilesLabel')}
          >
            <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
            <p className="text-sm">
              {t('studio.dropFilesHere')} <span className="text-primary underline">{t('studio.browse')}</span>
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {Array.from(ALLOWED_EXTS).sort().join(', ')}
            </p>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={Array.from(ALLOWED_EXTS).join(',')}
              onChange={onFileInputChange}
              className="hidden"
            />
          </div>

          {files.length > 0 && (
            <ul className="mt-4 space-y-1.5">
              {files.map((f, i) => (
                <li
                  key={`${f.name}-${i}`}
                  className="flex items-center gap-2.5 px-3.5 py-2 rounded-xl border border-border/50 bg-muted/40 text-sm hover:bg-muted/60 transition-colors"
                >
                  <FileText className="h-4 w-4 shrink-0 text-primary" />
                  <span className="flex-1 truncate font-medium">{f.name}</span>
                  <span className="text-xs font-mono text-muted-foreground shrink-0">
                    {(f.size / 1024).toFixed(0)} KB
                  </span>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); removeFile(i) }}
                    className="p-1 hover:bg-muted/80 rounded-lg text-muted-foreground hover:text-foreground transition-colors"
                    aria-label={t('studio.removeFile').replace('{name}', f.name)}
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </li>
              ))}
            </ul>
          )}

          <div className="mt-5 space-y-2">
            <Label htmlFor="studio-links" className="text-sm font-medium">
              {t('studio.linksLabel')}
            </Label>
            <div className="relative">
              <Link2 className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
              <Textarea
                id="studio-links"
                value={linkText}
                onChange={(e) => setLinkText(e.target.value)}
                placeholder={t('studio.linksPlaceholder')}
                className="min-h-24 pl-9 text-sm rounded-xl"
              />
            </div>
            <p className="text-xs text-muted-foreground">
              {t('studio.linksHelp').replace('{count}', String(parsedLinks.length))}
            </p>
          </div>
        </div>
      </div>
      </>} editorialBrief={<>

      <div className="mb-6 group relative rounded-2xl p-[1.5px] bg-gradient-to-b from-border/80 via-border/40 to-border/20 shadow-sm hover:shadow-md transition-all duration-200">
        <div className="rounded-[14.5px] bg-card/95 backdrop-blur-sm p-6 space-y-6 border border-white/[0.04] dark:border-white/[0.02] shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]">
          <div>
            <h2 className="text-lg font-semibold text-foreground">{t('studio.step2Title')}</h2>
          </div>

          <div
            className="grid gap-4"
            style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 12rem), 1fr))' }}
          >
            <button
              type="button"
              onClick={() => setMode('notebook')}
              className={cn(
                "group relative rounded-2xl p-5 text-left transition-all duration-200 active:scale-[0.98]",
                mode === 'notebook'
                  ? "border-2 border-primary bg-primary/[0.06] shadow-[0_0_20px_rgba(45,212,191,0.12),inset_0_1px_0_rgba(255,255,255,0.1)] ring-1 ring-primary/30"
                  : "border border-border/70 bg-card/60 hover:border-primary/40 hover:bg-card/90"
              )}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary mb-3.5 group-hover:scale-105 transition-transform">
                <BookOpen className="h-5 w-5" />
              </div>
              <div className="text-base font-semibold text-foreground">{t('studio.notebookModeTitle')}</div>
              <div className="text-xs leading-relaxed text-muted-foreground mt-1">
                {t('studio.notebookModeDescription')}
              </div>
            </button>

            <button
              type="button"
              onClick={() => setMode('podcast')}
              className={cn(
                "group relative rounded-2xl p-5 text-left transition-all duration-200 active:scale-[0.98]",
                mode === 'podcast'
                  ? "border-2 border-primary bg-primary/[0.06] shadow-[0_0_20px_rgba(45,212,191,0.12),inset_0_1px_0_rgba(255,255,255,0.1)] ring-1 ring-primary/30"
                  : "border border-border/70 bg-card/60 hover:border-primary/40 hover:bg-card/90"
              )}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary mb-3.5 group-hover:scale-105 transition-transform">
                <Mic className="h-5 w-5" />
              </div>
              <div className="text-base font-semibold text-foreground">{t('studio.podcastModeTitle')}</div>
              <div className="text-xs leading-relaxed text-muted-foreground mt-1">
                {t('studio.podcastModeDescription')}
              </div>
            </button>

            <button
              type="button"
              onClick={() => setMode('both')}
              className={cn(
                "group relative rounded-2xl p-5 text-left transition-all duration-200 active:scale-[0.98]",
                mode === 'both'
                  ? "border-2 border-primary bg-primary/[0.06] shadow-[0_0_20px_rgba(45,212,191,0.12),inset_0_1px_0_rgba(255,255,255,0.1)] ring-1 ring-primary/30"
                  : "border border-border/70 bg-card/60 hover:border-primary/40 hover:bg-card/90"
              )}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary mb-3.5 group-hover:scale-105 transition-transform">
                <Sparkles className="h-5 w-5" />
              </div>
              <div className="text-base font-semibold text-foreground">{t('studio.bothModeTitle')}</div>
              <div className="text-xs leading-relaxed text-muted-foreground mt-1">
                {t('studio.bothModeDescription')}
              </div>
            </button>

            <button
              type="button"
              onClick={() => setMode('course_pack')}
              className={cn(
                "group relative rounded-2xl p-5 text-left transition-all duration-200 active:scale-[0.98]",
                mode === 'course_pack'
                  ? "border-2 border-primary bg-primary/[0.06] shadow-[0_0_20px_rgba(45,212,191,0.12),inset_0_1px_0_rgba(255,255,255,0.1)] ring-1 ring-primary/30"
                  : "border border-border/70 bg-card/60 hover:border-primary/40 hover:bg-card/90"
              )}
            >
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary mb-3.5 group-hover:scale-105 transition-transform">
                <GraduationCap className="h-5 w-5" />
              </div>
              <div className="text-base font-semibold text-foreground">{t('studio.coursePackModeTitle')}</div>
              <div className="text-xs leading-relaxed text-muted-foreground mt-1">
                {t('studio.coursePackModeDescription')}
              </div>
            </button>
          </div>

          {requiresPodcastProfiles && (
            <div className="grid grid-cols-2 gap-3 pt-2">
              <div className="space-y-1.5">
                <Label htmlFor="ep-profile" className="text-xs">{t('studio.episodeProfileLabel')}</Label>
                <Select value={episodeProfile} onValueChange={setEpisodeProfile}>
                  <SelectTrigger id="ep-profile" className="h-9 text-sm rounded-xl">
                    <SelectValue placeholder={t('studio.episodeProfilePlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    {episodeProfiles.map((p) => (
                      <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="sp-profile" className="text-xs">{t('studio.speakerProfileLabel')}</Label>
                <Select value={speakerProfile} onValueChange={setSpeakerProfile}>
                  <SelectTrigger id="sp-profile" className="h-9 text-sm rounded-xl">
                    <SelectValue placeholder={t('studio.speakerProfilePlaceholder')} />
                  </SelectTrigger>
                  <SelectContent>
                    {speakerProfiles.map((p) => (
                      <SelectItem key={p.name} value={p.name}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          )}

          <div className="space-y-1.5 pt-2">
            <Label htmlFor="title" className="text-xs">
              {t('studio.titleLabel')}
            </Label>
            <Input
              id="title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder={t('studio.titlePlaceholder')}
              className="h-9 text-sm rounded-xl"
            />
          </div>
        </div>
      </div>
      </>} artifactPages={<>

      {(mutation.isError || coursePackMutation.isError) && (
        <div className="mb-4 p-3 rounded-xl border border-destructive/50 bg-destructive/10 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
          <div className="text-sm text-destructive">
            {/* v0.7.196 — same helper as the catch-branch toast. Was
                an inline unwrap that could surface raw axios + FastAPI
                500-default text. */}
            {getApiErrorMessage(
              mutation.error ?? coursePackMutation.error,
              t,
              'apiErrors.genericError',
            )}
          </div>
        </div>
      )}

          <div className="flex justify-end">
            <Button
              onClick={onGenerate}
              disabled={!canSubmit}
              size="lg"
              className="h-11 px-7 rounded-full font-medium bg-primary text-primary-foreground shadow-[0_4px_16px_rgba(20,184,166,0.35),inset_0_1px_0_rgba(255,255,255,0.2)] hover:bg-primary/95 active:scale-95 transition-all duration-150"
            >
              {isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                  {generatingText}
                </>
              ) : (
                <>{generateText}</>
              )}
            </Button>
          </div>
        </>} trustMargin={<p>Generation remains explicit and reviewable before any output is produced.</p>} />
      </div>
    </AppShell>
  )
}
