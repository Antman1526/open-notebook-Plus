'use client'

import { useEffect, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'

import { podcastsApi } from '@/lib/api/podcasts'
import type { PodcastReadiness } from '@/lib/types/podcasts'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

/**
 * The first Quick Podcast gate. It is read-only: rendering and cancellation
 * only request readiness and clear transient state. Production confirmation is
 * deliberately withheld until the fingerprint-checked submit API is present.
 */
export function QuickPodcastDialog() {
  const isOpen = usePodcastStudioStore((state) => state.isOpen)
  const destination = usePodcastStudioStore((state) => state.destination)
  const selections = usePodcastStudioStore((state) => state.selections)
  const notebookId = usePodcastStudioStore((state) => state.notebookId)
  const handoffToStudio = usePodcastStudioStore((state) => state.handoffToStudio)
  const dismiss = usePodcastStudioStore((state) => state.dismiss)
  const router = useRouter()
  const [readiness, setReadiness] = useState<PodcastReadiness | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [episodeProfiles, setEpisodeProfiles] = useState<string[]>([])
  const [speakerProfiles, setSpeakerProfiles] = useState<string[]>([])
  const [episodeProfile, setEpisodeProfile] = useState('')
  const [speakerProfile, setSpeakerProfile] = useState('')
  const [phase, setPhase] = useState<'review' | 'confirm'>('review')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const submissionKey = useRef<string | null>(null)
  const studioHandoffPending = useRef(false)
  const open = isOpen && destination === 'quick'

  useEffect(() => {
    if (!open || selections.length === 0) {
      setReadiness(null)
      setError(null)
      setPhase('review')
      setSubmitError(null)
      submissionKey.current = null
      return
    }
    let current = true
    setReadiness(null)
    setError(null)
    void podcastsApi.getPodcastReadiness(selections).then(
      (result) => current && setReadiness(result),
      () => current && setError('Podcast readiness is unavailable. No production was started.'),
    )
    return () => { current = false }
  }, [open, selections])

  useEffect(() => {
    if (!open) return
    let current = true
    void Promise.all([
      podcastsApi.listEpisodeProfiles(),
      podcastsApi.listSpeakerProfiles(),
    ]).then(
      ([episodes, speakers]) => {
        if (!current) return
        const episodeNames = episodes.map((profile) => profile.name)
        const speakerNames = speakers.map((profile) => profile.name)
        setEpisodeProfiles(episodeNames)
        setSpeakerProfiles(speakerNames)
        setEpisodeProfile((currentValue) => currentValue || episodeNames[0] || '')
        setSpeakerProfile((currentValue) => currentValue || speakerNames[0] || '')
      },
      () => current && setError('Podcast profiles are unavailable. No production was started.'),
    )
    return () => { current = false }
  }, [open])

  const canConfirm = Boolean(
    readiness?.ready && episodeProfile && speakerProfile && readiness.preview.selectionFingerprint,
  )

  const confirmProduction = async () => {
    if (!readiness || !canConfirm || submitting) return
    setSubmitting(true)
    setSubmitError(null)
    submissionKey.current ??= `podcast-${crypto.randomUUID()}`
    try {
      await podcastsApi.submitStudioPodcast({
        selections,
        selectionFingerprint: readiness.preview.selectionFingerprint,
        idempotencyKey: submissionKey.current,
        episodeProfile,
        speakerProfile,
        episodeName: readiness.preview.entries[0]?.title ?? 'Deeper Notebook podcast',
        reviewOutline: true,
        notebookId: notebookId ?? undefined,
      })
      dismiss()
    } catch {
      setSubmitError('Production could not be submitted. Review your readiness and try again.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (nextOpen) return
        if (!studioHandoffPending.current) {
          dismiss()
          return
        }
        handoffToStudio()
        // Let Radix finish releasing its modal pointer lock before the route
        // replaces this dialog. Same-task navigation can strand the lock.
        window.setTimeout(() => {
          router.push('/podcasts/studio')
          studioHandoffPending.current = false
        }, 250)
      }}
    >
      <DialogContent
        className="max-w-xl"
        onCloseAutoFocus={() => {
          if (studioHandoffPending.current) document.body.style.pointerEvents = ''
        }}
      >
        <DialogHeader>
          <DialogTitle>Review selection</DialogTitle>
          <DialogDescription>
            Podcast creation is optional. This step reads a temporary preview and does not start a model or create an episode.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4" aria-busy={readiness === null && error === null}>
          <section aria-labelledby="quick-podcast-research-set">
            <h2 id="quick-podcast-research-set" className="text-sm font-semibold">Research set</h2>
            {readiness ? (
              <ul className="mt-2 space-y-2" aria-label="Selected sources">
                {readiness.preview.entries.map((entry) => (
                  <li key={`${entry.stableId}:${entry.revisionId ?? 'current'}`} className="flex justify-between gap-3 text-sm">
                    <span>{entry.title}</span>
                    <span className="text-muted-foreground">{entry.state}</span>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-2 text-sm text-muted-foreground">{error ?? 'Checking local readiness…'}</p>}
          </section>
          <section aria-labelledby="quick-podcast-policy">
            <h2 id="quick-podcast-policy" className="text-sm font-semibold">Production policy</h2>
            <p className="mt-1 text-sm text-muted-foreground">Strict local evidence policy · Outline storyboard review</p>
          </section>
          {readiness?.blockedReasons.length ? (
            <p role="status" className="text-sm text-destructive">
              {readiness.blockedReasons.join(', ')}
            </p>
          ) : null}
          {phase === 'review' ? (
            <section aria-labelledby="quick-podcast-profiles">
              <h2 id="quick-podcast-profiles" className="text-sm font-semibold">Production profiles</h2>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                <label className="grid gap-1 text-sm">
                  Episode profile
                  <select
                    value={episodeProfile}
                    onChange={(event) => setEpisodeProfile(event.target.value)}
                    className="h-9 rounded-md border bg-background px-3 text-sm"
                  >
                    <option value="">Choose a profile</option>
                    {episodeProfiles.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
                <label className="grid gap-1 text-sm">
                  Voice profile
                  <select
                    value={speakerProfile}
                    onChange={(event) => setSpeakerProfile(event.target.value)}
                    className="h-9 rounded-md border bg-background px-3 text-sm"
                  >
                    <option value="">Choose a profile</option>
                    {speakerProfiles.map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
              </div>
            </section>
          ) : (
            <section aria-labelledby="quick-podcast-confirmation">
              <h2 id="quick-podcast-confirmation" className="text-sm font-semibold">Confirm production</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                This sends one confirmed, fingerprint-checked local production job.
              </p>
            </section>
          )}
          {submitError ? <p role="alert" className="text-sm text-destructive">{submitError}</p> : null}
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={dismiss}>Cancel</Button>
          {phase === 'review' ? (
            <DialogClose asChild>
              <Button
                type="button"
                variant="outline"
                onClick={() => { studioHandoffPending.current = true }}
              >
                Customize in Studio
              </Button>
            </DialogClose>
          ) : null}
          {phase === 'review' ? (
            <Button type="button" disabled={!canConfirm} onClick={() => setPhase('confirm')}>
              Continue to confirmation
            </Button>
          ) : (
            <Button type="button" disabled={!canConfirm || submitting} onClick={confirmProduction}>
              {submitting ? 'Submitting…' : 'Confirm production'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
