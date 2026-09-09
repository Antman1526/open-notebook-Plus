'use client'

// v0.8.83 — mind-map entry point (improvement roadmap, Batch 3). A header
// button that opens a near-full-screen dialog with the React Flow mind map.
// MindMap is loaded via next/dynamic ssr:false (React Flow needs the DOM, like
// the PDF viewer). Clicking a source node opens the existing SourceDialog.
import { useState } from 'react'
import dynamic from 'next/dynamic'
import { Network } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { SourceDialog } from '@/components/source/SourceDialog'
import { useTranslation } from '@/lib/hooks/use-translation'

const MindMap = dynamic(() => import('./MindMap'), {
  ssr: false,
  loading: () => null,
})

export function MindMapButton({ notebookId }: { notebookId: string }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [sourceId, setSourceId] = useState<string | null>(null)

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        className="rounded-full transition-all duration-200 active:scale-95 shadow-xs"
      >
        <Network className="mr-2 h-4 w-4" />
        {t('mindMap.button', { defaultValue: 'Mind map' })}
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex h-[90vh] max-w-[95vw] flex-col gap-0 p-0 sm:max-w-[95vw]">
          <DialogHeader className="border-b px-4 py-3">
            <DialogTitle>{t('mindMap.title', { defaultValue: 'Mind map' })}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 flex-1">
            <MindMap
              notebookId={notebookId}
              open={open}
              onSelectSource={(id) => setSourceId(id)}
            />
          </div>
        </DialogContent>
      </Dialog>

      <SourceDialog
        open={!!sourceId}
        onOpenChange={(o) => {
          if (!o) setSourceId(null)
        }}
        sourceId={sourceId ?? ''}
      />
    </>
  )
}
