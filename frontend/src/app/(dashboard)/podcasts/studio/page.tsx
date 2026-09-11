'use client'

import { useRouter } from 'next/navigation'

import { AppShell } from '@/components/layout/AppShell'
import { PodcastStudio } from '@/components/podcasts/PodcastStudio'
import { Button } from '@/components/ui/button'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'

/** Route counterpart to the Knowledge-pane Studio; selections remain transient. */
export default function PodcastStudioPage() {
  const router = useRouter()
  const selections = usePodcastStudioStore((state) => state.selections)
  // v0.8.127 — carries through to submitStudioPodcast so a Studio session
  // opened from a notebook produces a notebook-scoped episode.
  const notebookId = usePodcastStudioStore((state) => state.notebookId)
  const dismiss = usePodcastStudioStore((state) => state.dismiss)
  const seedDocumentIds = selections.flatMap(selection => (
    selection.kind === 'knowledge_document' ? [selection.documentId]
      : selection.kind === 'graph_selection' ? selection.documentIds
        : []
  ))

  return (
    <AppShell>
      <main className="flex-1 overflow-y-auto px-6 py-10 sm:px-8">
        <div className="mb-4 flex justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              dismiss()
              router.back()
            }}
          >
            Close Studio without producing
          </Button>
        </div>
        <PodcastStudio headingLevel={1} seedDocumentIds={[...new Set(seedDocumentIds)]} selections={selections} notebookId={notebookId} />
      </main>
    </AppShell>
  )
}
