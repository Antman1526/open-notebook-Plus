'use client'

import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Folder, FolderPlus, RefreshCw } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCaptureActions, useCaptureItems, useCaptureRoots } from '@/lib/hooks/use-capture'
import { isVisualSystemV2Enabled } from '@/lib/features'
import { useSourceVisualsEnabled } from '@/lib/features-client'
import { CaptureItemRow } from './CaptureItemRow'

export function CaptureInbox() {
  const roots = useCaptureRoots()
  const items = useCaptureItems()
  const actions = useCaptureActions()
  const sourceVisualsEnabled = useSourceVisualsEnabled()
  const showVisualCover = isVisualSystemV2Enabled() && sourceVisualsEnabled
  const [path, setPath] = useState('')

  const addRoot = async () => {
    if (!path.trim()) return
    await actions.addRoot.mutateAsync(path.trim())
    setPath('')
  }

  const scan = () => void actions.scan.mutateAsync(undefined)

  return (
    <div className="space-y-6 max-w-4xl">
      <Card className="border">
        <CardContent className="p-5 space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold text-foreground">Approved folders</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                Only folders you add here are scanned. Originals remain in place.
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={scan}
              disabled={actions.scan.isPending}
            >
              <RefreshCw
                className={`mr-2 h-4 w-4 ${actions.scan.isPending ? 'animate-spin' : ''}`}
              />
              Scan now
            </Button>
          </div>

          {roots.data && roots.data.length > 0 ? (
            <ul className="space-y-1.5 rounded-md bg-muted/40 p-3 text-sm">
              {roots.data.map((root) => (
                <li key={root.path} className="flex items-center gap-2 truncate text-muted-foreground font-mono text-xs">
                  <Folder className="h-3.5 w-3.5 shrink-0 text-primary" />
                  <span className="truncate">{root.path}</span>
                </li>
              ))}
            </ul>
          ) : null}

          <div className="flex gap-2">
            <Input
              aria-label="Capture folder path"
              value={path}
              onChange={(event) => setPath(event.target.value)}
              placeholder="Add a local or Google Drive Desktop folder"
              className="font-mono text-xs"
            />
            <Button
              type="button"
              variant="secondary"
              disabled={!path.trim() || actions.addRoot.isPending}
              onClick={() => void addRoot()}
              className="shrink-0"
            >
              <FolderPlus className="mr-2 h-4 w-4" />
              Add
            </Button>
          </div>
        </CardContent>
      </Card>

      <section className="space-y-4">
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-base font-semibold text-foreground">Inbox</h2>
          <span className="text-xs text-muted-foreground font-medium">
            {items.data?.length ?? 0} items
          </span>
        </div>

        {items.isLoading ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Loading local intake…</p>
        ) : items.isError ? (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 p-4 text-sm text-destructive">
            The capture inbox could not be loaded.
          </p>
        ) : items.data?.length ? (
          <Card className="divide-y border">
            <div className="p-4 space-y-3">
              {items.data.map((item) => (
                <CaptureItemRow
                  key={item.id ?? `${item.root_path}:${item.relative_path}`}
                  item={item}
                  showVisualCover={showVisualCover}
                />
              ))}
            </div>
          </Card>
        ) : (
          <div className="rounded-lg border border-dashed border-border p-8 text-center">
            <p className="text-sm font-medium text-foreground">No supported files in inbox</p>
            <p className="mt-1 text-xs text-muted-foreground">
              Add a folder above and click Scan now after copying documents, audio, or video into it.
            </p>
          </div>
        )}
      </section>
    </div>
  )
}
