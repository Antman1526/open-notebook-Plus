// v0.7.105 — Export a notebook to disk. Lets the user pick folder-vs-zip,
// destination path, whether to include source documents, and whether
// to overwrite. Defaults destination to
// `{default_exports}/{notebook-slug}` or `.zip` based on format.
//
// v0.7.119 — Expanded to surface all six backend formats
// (folder / zip / html_folder / html_zip / combined_md / combined_html),
// the zip compression knob, and tightened the include_sources / overwrite
// visibility to only the formats where they're meaningful.
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
import { useExportNotebook } from '@/lib/hooks/use-export'
import { useFsHome, useFsMkdir } from '@/lib/hooks/use-fs'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { DirectoryPicker } from '@/components/notebooks/DirectoryPicker'
import { ExportCompression, ExportFormat } from '@/lib/types/api'

interface ExportNotebookDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notebookId: string
  notebookName: string
}

function slugify(text: string, fallback = 'notebook'): string {
  const normalized = text
    .normalize('NFKD')
    .replace(/[̀-ͯ]/g, '')
    .toLowerCase()
  const slug = normalized.replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
  return slug.slice(0, 80) || fallback
}

function joinPath(dir: string, leaf: string): string {
  return `${dir.replace(/\/+$/, '')}/${leaf}`
}

// v0.8.126 — found live: the default destination folder doesn't exist on a
// fresh machine and the folder/zip export routes deliberately refuse to
// create parents, so the first export failed with a 400 the dialog never
// showed. Mirrors the fix already applied to ExportAllArtifactsDialog.tsx
// (v0.8.125): create the destination directory (folder formats) or its
// parent (zip/single-file formats) before submitting, and surface any API
// error inline.
function parentDir(path: string): string {
  const i = path.lastIndexOf('/')
  return i > 0 ? path.slice(0, i) : path
}

function isDirectoryFormat(format: ExportFormat): boolean {
  return format === 'folder' || format === 'html_folder' || format === 'obsidian_folder'
}

// v0.7.119 — Match the destination shape to the chosen format:
//   folder / html_folder       → directory
//   zip / html_zip             → .zip file
//   combined_md                → .md file
//   combined_html              → .html file
function leafFor(format: ExportFormat, slug: string): string {
  switch (format) {
    case 'zip':
    case 'html_zip':
    case 'obsidian_zip':
      return `${slug}.zip`
    case 'combined_md':
      return `${slug}.md`
    case 'combined_html':
      return `${slug}.html`
    case 'folder':
    case 'html_folder':
    case 'obsidian_folder':
    default:
      return slug
  }
}

const ALL_FORMATS: ExportFormat[] = [
  'folder',
  'zip',
  'obsidian_folder',
  'obsidian_zip',
  'html_folder',
  'html_zip',
  'combined_md',
  'combined_html',
]

const ALL_COMPRESSIONS: ExportCompression[] = [
  'deflated',
  'stored',
  'bzip2',
  'lzma',
]

// v0.7.119 — Explicit literal map so the locale-parity / unused-key test
// (lib/locales/index.test.ts) can detect each key in the source corpus.
// A dynamic `t(\`notebooks.exportFormat.${fmt}\`)` would leave the keys
// orphaned and trip the "unused key" detection.
const FORMAT_LABEL_KEYS: Record<ExportFormat, string> = {
  folder: 'notebooks.exportFormat.folder',
  zip: 'notebooks.exportFormat.zip',
  obsidian_folder: 'notebooks.export.format.obsidian_folder',
  obsidian_zip: 'notebooks.export.format.obsidian_zip',
  html_folder: 'notebooks.export.format.html_folder',
  html_zip: 'notebooks.export.format.html_zip',
  combined_md: 'notebooks.export.format.combined_md',
  combined_html: 'notebooks.export.format.combined_html',
}

const COMPRESSION_LABEL_KEYS: Record<ExportCompression, string> = {
  deflated: 'notebooks.export.compression.deflated',
  stored: 'notebooks.export.compression.stored',
  bzip2: 'notebooks.export.compression.bzip2',
  lzma: 'notebooks.export.compression.lzma',
}

// v0.7.119 — Which formats accept include_sources? combined_html flat-out
// ignores it on the backend (single HTML doc doesn't carry binary sources);
// everything else passes through.
function supportsIncludeSources(format: ExportFormat): boolean {
  return format !== 'combined_html'
}

function isZipFormat(format: ExportFormat): boolean {
  return format === 'zip' || format === 'html_zip' || format === 'obsidian_zip'
}

export function ExportNotebookDialog({
  open,
  onOpenChange,
  notebookId,
  notebookName,
}: ExportNotebookDialogProps) {
  const { t } = useTranslation()
  const exportNotebook = useExportNotebook()
  const mkdir = useFsMkdir()
  const [submitError, setSubmitError] = useState<string | null>(null)
  const homeQuery = useFsHome(open)

  const [format, setFormat] = useState<ExportFormat>('folder')
  const [destination, setDestination] = useState('')
  const [destinationTouched, setDestinationTouched] = useState(false)
  const [includeSources, setIncludeSources] = useState(false)
  const [overwrite, setOverwrite] = useState(false)
  const [compression, setCompression] = useState<ExportCompression>('deflated')
  const [pickerOpen, setPickerOpen] = useState(false)

  const slug = useMemo(() => slugify(notebookName), [notebookName])

  // Auto-populate destination from home + slug + format when the user
  // hasn't customized it. Re-derive whenever format flips.
  useEffect(() => {
    if (!open) return
    if (destinationTouched) return
    if (!homeQuery.data) return
    const base = homeQuery.data.default_exports
    setDestination(joinPath(base, leafFor(format, slug)))
  }, [open, destinationTouched, homeQuery.data, format, slug])

  // Reset state when dialog closes.
  useEffect(() => {
    if (!open) {
      setFormat('folder')
      setDestinationTouched(false)
      setIncludeSources(false)
      setOverwrite(false)
      setCompression('deflated')
      setPickerOpen(false)
      setSubmitError(null)
    }
  }, [open])

  const handlePickerSelect = (path: string) => {
    setDestination(joinPath(path, leafFor(format, slug)))
    setDestinationTouched(true)
  }

  const handleSubmit = async () => {
    const target = destination.trim()
    if (!target) return
    setSubmitError(null)
    try {
      // v0.8.126 — see parentDir()/isDirectoryFormat() above: create the
      // destination first so a fresh machine doesn't 400 on a missing
      // parent directory.
      await mkdir.mutateAsync(isDirectoryFormat(format) ? target : parentDir(target))
      await exportNotebook.mutateAsync({
        id: notebookId,
        data: {
          destination: target,
          format,
          // v0.7.119 — only send include_sources when the format honors
          // it; otherwise the backend silently ignores it but we'd
          // rather not lie in the request body.
          include_sources: supportsIncludeSources(format)
            ? includeSources
            : false,
          overwrite,
          // Only send compression when the destination is a zip.
          ...(isZipFormat(format) ? { compression } : {}),
        },
      })
      onOpenChange(false)
    } catch (error) {
      // The mutation hook also toasts; keep the reason visible in the dialog.
      setSubmitError(getApiErrorMessage(error, (key) => t(key), 'apiErrors.genericError'))
    }
  }

  const isPending = exportNotebook.isPending || mkdir.isPending
  const showIncludeSources = supportsIncludeSources(format)
  const showCompression = isZipFormat(format)

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>{t('notebooks.exportNotebook')}</DialogTitle>
            <DialogDescription>
              {t('notebooks.exportNotebookDesc').replace('{name}', notebookName)}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label htmlFor="export-format-select">
                {t('notebooks.exportFormatLabel')}
              </Label>
              <Select
                value={format}
                onValueChange={(value) => setFormat(value as ExportFormat)}
                disabled={isPending}
              >
                <SelectTrigger
                  id="export-format-select"
                  className="w-full"
                  aria-label={t('notebooks.exportFormatLabel')}
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {ALL_FORMATS.map((fmt) => (
                    <SelectItem key={fmt} value={fmt}>
                      {t(FORMAT_LABEL_KEYS[fmt])}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label htmlFor="export-destination">
                {t('notebooks.exportDestination')}
              </Label>
              <div className="flex gap-2">
                <Input
                  id="export-destination"
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

            {showIncludeSources && (
              <div className="flex items-start gap-3">
                <Checkbox
                  id="export-include-sources"
                  checked={includeSources}
                  onCheckedChange={(v) => setIncludeSources(v === true)}
                  disabled={isPending}
                />
                <Label
                  htmlFor="export-include-sources"
                  className="text-sm leading-tight cursor-pointer"
                >
                  {t('notebooks.export.includeSources')}
                </Label>
              </div>
            )}

            {showCompression && (
              <div className="space-y-2">
                <Label htmlFor="export-compression-select">
                  {t('notebooks.export.compressionLabel')}
                </Label>
                <Select
                  value={compression}
                  onValueChange={(value) =>
                    setCompression(value as ExportCompression)
                  }
                  disabled={isPending}
                >
                  <SelectTrigger
                    id="export-compression-select"
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
            )}

            <div className="flex items-start gap-3">
              <Checkbox
                id="export-overwrite"
                checked={overwrite}
                onCheckedChange={(v) => setOverwrite(v === true)}
                disabled={isPending}
              />
              <Label
                htmlFor="export-overwrite"
                className="text-sm leading-tight cursor-pointer"
              >
                {t('notebooks.exportOverwrite')}
              </Label>
            </div>
          </div>

          {submitError ? (
            <p role="alert" className="text-sm text-destructive" data-testid="export-error">
              {submitError}
            </p>
          ) : null}
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
