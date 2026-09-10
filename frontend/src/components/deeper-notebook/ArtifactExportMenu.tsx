// v0.8.118 — localized the export menu. Previously "Saved exports",
// "Download"/"Open"/"Copy"/"Folder", and the group headings
// (Editable/Visual/Data/Source/Bundle) were hardcoded English while every
// other surface went through t(). Format labels (EPUB, PDF, DOCX, JSON,
// Markdown, ...) remain literal — they are proper nouns, not UI copy.
'use client'

import { useEffect, useRef, useState } from 'react'
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileCode2,
  FilePenLine,
  FolderOpen,
  Image,
  Package,
  TableProperties,
} from 'lucide-react'

import { Button } from '@/components/ui/button'
import { useRegenerateStudioExport } from '@/lib/hooks/use-studio'
import { useTranslation } from '@/lib/hooks/use-translation'
import type { StudioArtifact, StudioArtifactExportFormat } from '@/lib/api/studio'

// v0.8.119 — course-pack artifacts can be missing EPUB/PDF (e.g. persisted
// before v0.8.117, or a prior export attempt failed) without needing the
// whole artifact regenerated. Only these two formats are offered here since
// they're the only course-pack exports the single-format endpoint targets.
const COURSE_PACK_ARTIFACT_TYPES = new Set(['course_pack', 'training_guide'])
const GENERATABLE_COURSE_PACK_FORMATS: StudioArtifactExportFormat[] = ['epub', 'pdf']

type ExportGroup = 'editable' | 'visual' | 'data' | 'source' | 'bundle'
type Translate = (key: string) => string

type ExportItem = {
  format: StudioArtifactExportFormat
  path?: string
  href: string
  label: string
  metadata: string
  group: ExportGroup
  isBrowserDownload?: boolean
}

const EXPORT_GROUP_META: Array<{
  id: ExportGroup
  labelKey: string
  Icon: typeof FilePenLine
}> = [
  { id: 'editable', labelKey: 'studio.export.groupEditable', Icon: FilePenLine },
  { id: 'visual', labelKey: 'studio.export.groupVisual', Icon: Image },
  { id: 'data', labelKey: 'studio.export.groupData', Icon: TableProperties },
  { id: 'source', labelKey: 'studio.export.groupSource', Icon: FileCode2 },
  { id: 'bundle', labelKey: 'studio.export.groupBundle', Icon: Package },
]

function exportGroup(format: string): ExportGroup {
  const normalized = format.toLowerCase()
  if (['docx', 'xlsx'].includes(normalized)) return 'editable'
  if (['pptx', 'pdf', 'png', 'svg'].includes(normalized)) return 'visual'
  if (['csv'].includes(normalized)) return 'data'
  if (
    ['zip', 'research_bundle', 'scorm_package', 'xapi_package', 'epub'].includes(normalized)
  ) {
    return 'bundle'
  }
  return 'source'
}

function exportLabel(format: string): string {
  const normalized = format.toLowerCase()
  if (
    ['json', 'csv', 'docx', 'xlsx', 'pptx', 'pdf', 'png', 'svg', 'zip', 'epub'].includes(
      normalized,
    )
  ) {
    return normalized.toUpperCase()
  }
  if (normalized === 'md') return 'Markdown'
  return format
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}

function filePathHref(path: string): string {
  const normalized = path.replace(/\\/g, '/')
  if (normalized.startsWith('file://')) return encodeURI(normalized)
  const withPrefix = normalized.startsWith('/') ? `file://${normalized}` : `file:///${normalized}`
  return encodeURI(withPrefix)
}

function parentFilePath(path: string): string | null {
  const normalized = path.replace(/\\/g, '/')
  const lastSlash = normalized.lastIndexOf('/')
  if (lastSlash <= 0) return null
  return normalized.slice(0, lastSlash)
}

function artifactFileName(artifact: StudioArtifact): string {
  const slug = artifact.title
    .trim()
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  return `${slug || 'artifact'}.md`
}

function browserExports(artifact: StudioArtifact, markdown: string, t: Translate): ExportItem[] {
  const browserDownloadPrefix = t('studio.export.browserDownloadPrefix')
  const exportPaths = artifact.export_paths ?? {}
  const exports: ExportItem[] = []
  if (!exportPaths.markdown && !exportPaths.md && markdown) {
    exports.push({
      format: 'markdown',
      href: `data:text/markdown;charset=utf-8,${encodeURIComponent(markdown)}`,
      label: 'Markdown',
      metadata: `${browserDownloadPrefix}${artifactFileName(artifact)}`,
      group: 'source',
      isBrowserDownload: true,
    })
  }
  if (!exportPaths.json) {
    exports.push({
      format: 'json',
      href: `data:application/json;charset=utf-8,${encodeURIComponent(JSON.stringify(artifact, null, 2))}`,
      label: 'JSON',
      metadata: `${browserDownloadPrefix}${artifactFileName(artifact).replace(/\.md$/, '.json')}`,
      group: 'source',
      isBrowserDownload: true,
    })
  }
  return exports
}

function artifactExports(artifact: StudioArtifact, markdown: string, t: Translate): ExportItem[] {
  const saved = Object.entries(artifact.export_paths ?? {})
    .filter((entry): entry is [StudioArtifactExportFormat, string] => {
      return typeof entry[1] === 'string' && entry[1].trim().length > 0
    })
    .map(([format, path]) => ({
      format,
      path,
      href: filePathHref(path),
      label: exportLabel(format),
      metadata: path,
      group: exportGroup(format),
    }))

  return [...saved, ...browserExports(artifact, markdown, t)].sort((left, right) => {
    const groupOrder = EXPORT_GROUP_META.findIndex((group) => group.id === left.group)
      - EXPORT_GROUP_META.findIndex((group) => group.id === right.group)
    if (groupOrder !== 0) return groupOrder
    const formatOrder: Record<string, number> = {
      pptx: 0,
      pdf: 1,
      png: 2,
      svg: 3,
      docx: 0,
      xlsx: 1,
      csv: 0,
      markdown: 0,
      md: 0,
      json: 1,
      research_bundle: 0,
      scorm_package: 1,
      xapi_package: 2,
      epub: 3,
    }
    const leftOrder = formatOrder[left.format.toLowerCase()] ?? 10
    const rightOrder = formatOrder[right.format.toLowerCase()] ?? 10
    if (leftOrder !== rightOrder) return leftOrder - rightOrder
    return left.label.localeCompare(right.label)
  })
}

function exportDownloadName(item: ExportItem, browserDownloadPrefix: string): string | undefined {
  if (!item.isBrowserDownload) return undefined
  return item.metadata.replace(browserDownloadPrefix, '')
}

function IconAction({
  label,
  tooltip = label,
  children,
  ...props
}: React.ComponentProps<typeof Button> & { label: string; tooltip?: string }) {
  return (
    <Button
      {...props}
      size="icon"
      variant="ghost"
      aria-label={label}
      title={tooltip}
      className="min-h-11 min-w-11"
    >
      {children}
    </Button>
  )
}

export function ArtifactExportMenu({
  artifact,
  markdown,
}: {
  artifact: StudioArtifact
  markdown: string
}) {
  const { t } = useTranslation()
  const [copiedPath, setCopiedPath] = useState<string | null>(null)
  const copyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const exports = artifactExports(artifact, markdown, t)
  const browserDownloadPrefix = t('studio.export.browserDownloadPrefix')
  const regenerateExport = useRegenerateStudioExport()
  const exportPaths = artifact.export_paths ?? {}
  const missingCoursePackFormats = COURSE_PACK_ARTIFACT_TYPES.has(artifact.artifact_type)
    ? GENERATABLE_COURSE_PACK_FORMATS.filter((format) => !exportPaths[format])
    : []

  useEffect(() => () => {
    if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
  }, [])

  async function copyPath(path: string) {
    try {
      await navigator.clipboard?.writeText(path)
      setCopiedPath(path)
      if (copyTimerRef.current) clearTimeout(copyTimerRef.current)
      copyTimerRef.current = setTimeout(() => {
        copyTimerRef.current = null
        setCopiedPath(null)
      }, 1600)
    } catch {
      setCopiedPath(null)
    }
  }

  return (
    <section aria-label={t('studio.export.regionLabel')} className="mb-4 border-t pt-3">
        <div className="flex items-center justify-between gap-2">
          <div className="text-sm font-medium">{t('studio.export.savedExports')}</div>
          <span className="text-xs text-muted-foreground">
            {t('studio.export.availableCount').replace('{count}', String(exports.length))}
          </span>
        </div>
        {missingCoursePackFormats.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {missingCoursePackFormats.map((format) => {
              const label = exportLabel(format)
              const isPending = regenerateExport.isPending
                && regenerateExport.variables?.format === format
              return (
                <Button
                  key={format}
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={isPending}
                  onClick={() => regenerateExport.mutate({ artifactId: artifact.id, format })}
                >
                  {isPending
                    ? t('studio.export.generating')
                    : t('studio.export.generate').replace('{label}', label)}
                </Button>
              )
            })}
          </div>
        )}
        <div className="mt-2 divide-y">
          {EXPORT_GROUP_META.map(({ id, labelKey, Icon }) => {
            const groupExports = exports.filter((item) => item.group === id)
            if (groupExports.length === 0) return null
            return (
              <div key={id} className="py-2 first:pt-0 last:pb-0">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                  {t(labelKey)}
                </div>
                <div className="mt-1.5 space-y-1.5">
                  {groupExports.map((item) => {
                    const folderPath = item.path ? parentFilePath(item.path) : null
                    const key = `${item.format}-${item.path ?? item.href}`
                    return (
                      <div key={key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
                        <div className="min-w-0">
                          <div className="text-xs font-medium">{item.label}</div>
                          <div title={item.path} className="truncate text-[0.68rem] text-muted-foreground">
                            {item.metadata}
                          </div>
                        </div>
                        <div className="flex items-center gap-0.5">
                          {item.isBrowserDownload ? (
                            <IconAction
                              asChild
                              label={t('studio.export.download').replace('{label}', item.label)}
                              tooltip={t('studio.export.download').replace('{label}', item.label)}
                            >
                              <a href={item.href} download={exportDownloadName(item, browserDownloadPrefix)}>
                                <Download className="h-4 w-4" aria-hidden="true" />
                              </a>
                            </IconAction>
                          ) : (
                            <IconAction
                              asChild
                              label={t('studio.export.open')}
                              tooltip={t('studio.export.openTooltip').replace('{label}', item.label)}
                            >
                              <a href={item.href} target="_blank" rel="noreferrer">
                                <ExternalLink className="h-4 w-4" aria-hidden="true" />
                              </a>
                            </IconAction>
                          )}
                          {item.path && (
                            <IconAction
                              label={copiedPath === item.path ? t('studio.export.copied') : t('studio.export.copy')}
                              tooltip={
                                copiedPath === item.path
                                  ? t('studio.export.copiedPath')
                                  : t('studio.export.copyPath').replace('{label}', item.label)
                              }
                              onClick={() => void copyPath(item.path!)}
                            >
                              {copiedPath === item.path ? (
                                <Check className="h-4 w-4 text-[var(--dn-success)]" aria-hidden="true" />
                              ) : (
                                <Copy className="h-4 w-4" aria-hidden="true" />
                              )}
                            </IconAction>
                          )}
                          {folderPath && (
                            <IconAction
                              asChild
                              label={t('studio.export.folder')}
                              tooltip={t('studio.export.openFolder').replace('{label}', item.label)}
                            >
                              <a href={filePathHref(folderPath)} target="_blank" rel="noreferrer">
                                <FolderOpen className="h-4 w-4" aria-hidden="true" />
                              </a>
                            </IconAction>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
        </div>
    </section>
  )
}
