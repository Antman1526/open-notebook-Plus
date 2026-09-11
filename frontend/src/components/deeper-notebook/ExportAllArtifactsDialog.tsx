// v0.8.124 — One-click export of every completed Studio artifact in a
// notebook as a single zip. Trimmed clone of
// frontend/src/app/(dashboard)/notebooks/components/ExportNotebookDialog.tsx:
// same destination-prefill + DirectoryPicker + overwrite/compression
// pattern, minus the format select (bundles are always a zip) and
// include-sources checkbox (Studio artifacts, not notebook notes/sources),
// plus a "regenerate stale exports first" checkbox.
'use client'

import { useEffect, useMemo, useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { FolderOpen } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useExportNotebookArtifactBundle } from '@/lib/hooks/use-studio'
import { useFsHome } from '@/lib/hooks/use-fs'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { DirectoryPicker } from '@/components/notebooks/DirectoryPicker'
import { StudioBundleCompression } from '@/lib/api/studio'

interface ExportAllArtifactsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notebookId: string
}

// v0.8.124 — ArtifactRail only has notebookId (no notebook name prop), so
// the slug is derived from the record-id suffix, same style as the
// backend's `_notebook_record_id_part` helper (api/routers/exports.py).
function slugFromNotebookId(notebookId: string): string {
  const raw = notebookId.includes(':') ? notebookId.split(':', 2)[1] : notebookId
  const normalized = raw.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase()
  const slug = normalized.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return slug.slice(0, 80) || 'notebook'
}

function joinPath(dir: string, leaf: string): string {
  return `${dir.replace(/\/+$/, '')}/${leaf}`
}

const ALL_COMPRESSIONS: StudioBundleCompression[] = [
  'deflated',
  'stored',
  'bzip2',
  'lzma',
]

const COMPRESSION_LABEL_KEYS: Record<StudioBundleCompression, string> = {
  deflated: 'notebooks.export.compression.deflated',
  stored: 'notebooks.export.compression.stored',
  bzip2: 'notebooks.export.compression.bzip2',
  lzma: 'notebooks.export.compression.lzma',
}

export function ExportAllArtifactsDialog({
  open,
  onOpenChange,
  notebookId,
}: ExportAllArtifactsDialogProps) {
  const { t } = useTranslation()
  const exportBundle = useExportNotebookArtifactBundle()
  const homeQuery = useFsHome(open)

  const [destination, setDestination] = useState('')
  const [destinationTouched, setDestinationTouched] = useState(false)
  const [overwrite, setOverwrite] = useState(false)
  const [compression, setCompression] = useState<StudioBundleCompression>('deflated')
  const [regenerateStale, setRegenerateStale] = useState(true)
  // v0.8.125 — include podcast episode audio/transcript in the bundle.
  const [includeMedia, setIncludeMedia] = useState(true)
  const [pickerOpen, setPickerOpen] = useState(false)

  const slug = useMemo(() => slugFromNotebookId(notebookId), [notebookId])
  const leaf = `${slug}-artifacts.zip`

  // Auto-populate destination from home + slug when the user hasn't
  // customized it.
  useEffect(() => {
    if (!open) return
    if (destinationTouched) return
    if (!homeQuery.data) return
    setDestination(joinPath(homeQuery.data.default_exports, leaf))
  }, [open, destinationTouched, homeQuery.data, leaf])

  // Reset state when dialog closes.
  useEffect(() => {
    if (!open) {
      setDestinationTouched(false)
      setOverwrite(false)
      setCompression('deflated')
      setRegenerateStale(true)
      setIncludeMedia(true)
      setPickerOpen(false)
    }
  }, [open])

  const handlePickerSelect = (path: string) => {
    setDestination(joinPath(path, leaf))
    setDestinationTouched(true)
  }

  const handleSubmit = async () => {
    if (!destination.trim()) return
    try {
      await exportBundle.mutateAsync({
        notebookId,
        data: {
          destination: destination.trim(),
          overwrite,
          compression,
          regenerate_stale: regenerateStale,
          include_media: includeMedia,
        },
      })
      onOpenChange(false)
    } catch {
      // Toast is handled by the mutation hook.
    }
  }

  const isPending = exportBundle.isPending

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('studio.export.bundleTitle')}</DialogTitle>
            <DialogDescription>{t('studio.export.bundleDescription')}</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="export-bundle-destination">
                {t('notebooks.exportDestination')}
              </Label>
              <div className="flex gap-2">
                <Input
                  id="export-bundle-destination"
                  value={destination}
                  onChange={(e) => {
                    setDestination(e.target.value)
                    setDestinationTouched(true)
                  }}
                  disabled={isPending}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => setPickerOpen(true)}
                  disabled={isPending}
                  aria-label={t('filesystem.pickDirectory')}
                >
                  <FolderOpen className="h-4 w-4" />
                </Button>
              </div>
            </div>

            <div className="space-y-2">
              <Label htmlFor="export-bundle-compression-select">
                {t('notebooks.export.compressionLabel')}
              </Label>
              <Select
                value={compression}
                onValueChange={(value) => setCompression(value as StudioBundleCompression)}
                disabled={isPending}
              >
                <SelectTrigger
                  id="export-bundle-compression-select"
                  className="w-full"
                  aria-label={t('notebooks.export.compressionLabel')}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ALL_COMPRESSIONS.map((c) => (
                    <SelectItem key={c} value={c}>
                      {t(COMPRESSION_LABEL_KEYS[c])}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex items-start gap-3">
              <Checkbox
                id="export-bundle-regenerate-stale"
                checked={regenerateStale}
                onCheckedChange={(v) => setRegenerateStale(v === true)}
                disabled={isPending}
              />
              <Label
                htmlFor="export-bundle-regenerate-stale"
                className="text-sm leading-tight cursor-pointer"
              >
                {t('studio.export.regenerateStale')}
              </Label>
            </div>

            <div className="flex items-start gap-3">
              <Checkbox
                id="export-bundle-include-media"
                checked={includeMedia}
                onCheckedChange={(v) => setIncludeMedia(v === true)}
                disabled={isPending}
              />
              <Label
                htmlFor="export-bundle-include-media"
                className="text-sm leading-tight cursor-pointer"
              >
                {t('studio.export.includeMedia')}
              </Label>
            </div>

            <div className="flex items-start gap-3">
              <Checkbox
                id="export-bundle-overwrite"
                checked={overwrite}
                onCheckedChange={(v) => setOverwrite(v === true)}
                disabled={isPending}
              />
              <Label
                htmlFor="export-bundle-overwrite"
                className="text-sm leading-tight cursor-pointer"
              >
                {t('notebooks.exportOverwrite')}
              </Label>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isPending}>
              {t('filesystem.cancel')}
            </Button>
            <Button onClick={handleSubmit} disabled={isPending || !destination.trim()}>
              {isPending ? (
                <>
                  <LoadingSpinner size="sm" className="mr-2" />
                  {t('notebooks.exporting')}
                </>
              ) : (
                t('notebooks.export.button')
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <DirectoryPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        initialPath={homeQuery.data?.default_exports}
        onSelect={handlePickerSelect}
      />
    </>
  )
}
