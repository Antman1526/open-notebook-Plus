'use client'

// v0.8.87 — Discover sources (improvement roadmap, Batch 3). A deliberate,
// user-driven web search: type a topic → see candidate results → pick which to
// add as link sources (via the existing create-source pipeline). Privacy:
// nothing leaves the machine until the user runs a search. v0.8.82 — the
// provider chain ends in a keyless Wikipedia tail, so search works with no key
// configured; the dialog names the active provider, and the setup hint appears
// only when the operator restored key-only gating
// (DEEPER_NOTEBOOK_WEB_SEARCH_KEYLESS=0) with no key set. Search is
// server-side via POST /notebooks/{id}/discover-sources.
import { useState } from 'react'
import { toast } from 'sonner'
import { Compass, Loader2, Search } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Checkbox } from '@/components/ui/checkbox'
import { notebooksApi, type DiscoverResult } from '@/lib/api/notebooks'
import { useCreateSource } from '@/lib/hooks/use-sources'
import { useTranslation } from '@/lib/hooks/use-translation'

interface DiscoverSourcesDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notebookId: string
}

export function DiscoverSourcesDialog({
  open,
  onOpenChange,
  notebookId,
}: DiscoverSourcesDialogProps) {
  const { t } = useTranslation()
  const createSource = useCreateSource()

  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searched, setSearched] = useState(false)
  const [enabled, setEnabled] = useState(true)
  const [provider, setProvider] = useState<string | null>(null)
  const [results, setResults] = useState<DiscoverResult[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [adding, setAdding] = useState(false)

  const reset = () => {
    setQuery('')
    setSearching(false)
    setSearched(false)
    setEnabled(true)
    setProvider(null)
    setResults([])
    setSelected(new Set())
    setAdding(false)
  }

  const handleOpenChange = (next: boolean) => {
    if (!next) reset()
    onOpenChange(next)
  }

  const runSearch = async () => {
    const q = query.trim()
    if (!q || searching) return
    setSearching(true)
    setSearched(true)
    setSelected(new Set())
    try {
      const data = await notebooksApi.discoverSources(notebookId, q, 8)
      setEnabled(data.enabled)
      setProvider(data.provider)
      setResults(data.results)
    } catch {
      setResults([])
      toast.error(t('sources.discoverError', { defaultValue: 'Search failed. Please try again.' }))
    } finally {
      setSearching(false)
    }
  }

  const toggle = (url: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(url)) next.delete(url)
      else next.add(url)
      return next
    })
  }

  const addSelected = async () => {
    const chosen = results.filter((r) => selected.has(r.url))
    if (chosen.length === 0 || adding) return
    setAdding(true)
    let ok = 0
    for (const r of chosen) {
      try {
        await createSource.mutateAsync({
          type: 'link',
          url: r.url,
          title: r.title || undefined,
          notebooks: [notebookId],
          async_processing: true,
        })
        ok += 1
      } catch {
        /* per-source failure surfaces its own toast; keep going */
      }
    }
    setAdding(false)
    if (ok > 0) {
      toast.success(
        t('sources.discoverAdded', {
          defaultValue: `Added ${ok} source${ok === 1 ? '' : 's'}. Processing…`,
          count: ok,
        })
      )
      handleOpenChange(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Compass className="h-5 w-5" />
            {t('sources.discover', { defaultValue: 'Discover sources' })}
          </DialogTitle>
          <DialogDescription>
            {t('sources.discoverDesc', {
              defaultValue:
                'Search the web for a topic and add results as sources. The query is sent to your configured search provider only when you search.',
            })}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') runSearch()
            }}
            placeholder={t('sources.discoverPlaceholder', {
              defaultValue: 'e.g. recent advances in retrieval-augmented generation',
            })}
            autoFocus
          />
          <Button onClick={runSearch} disabled={!query.trim() || searching}>
            {searching ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Search className="h-4 w-4" />
            )}
          </Button>
        </div>

        {provider && enabled && (
          <p className="text-xs text-muted-foreground">
            {t('sources.discoverProvider', {
              defaultValue: `Searching via ${provider}.`,
              provider,
            })}
          </p>
        )}

        <div className="max-h-[45vh] min-h-[80px] overflow-y-auto">
          {!enabled && searched ? (
            <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              {t('sources.discoverNotConfigured', {
                defaultValue:
                  'Web search isn’t configured. Set SERPER_API_KEY, TAVILY_API_KEY, or SEARXNG_BASE_URL in your environment to enable Discover. Nothing is sent anywhere until a provider is set.',
              })}
            </div>
          ) : searched && !searching && results.length === 0 ? (
            <p className="p-4 text-center text-sm text-muted-foreground">
              {t('sources.discoverNoResults', { defaultValue: 'No results found.' })}
            </p>
          ) : (
            <ul className="space-y-2">
              {results.map((r) => (
                <li
                  key={r.url}
                  className="flex items-start gap-3 rounded-md border p-3 hover:bg-muted/40"
                >
                  <Checkbox
                    checked={selected.has(r.url)}
                    onCheckedChange={() => toggle(r.url)}
                    className="mt-1"
                    id={`discover-${r.url}`}
                  />
                  <label htmlFor={`discover-${r.url}`} className="min-w-0 flex-1 cursor-pointer">
                    <span className="block truncate text-sm font-medium">
                      {r.title || r.url}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">{r.url}</span>
                    {r.snippet && (
                      <span className="mt-1 block text-xs text-muted-foreground line-clamp-2">
                        {r.snippet}
                      </span>
                    )}
                  </label>
                </li>
              ))}
            </ul>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            {t('common.cancel', { defaultValue: 'Cancel' })}
          </Button>
          <Button onClick={addSelected} disabled={selected.size === 0 || adding}>
            {adding && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {selected.size > 0
              ? t('sources.discoverAddSelectedCount', {
                  defaultValue: `Add selected (${selected.size})`,
                  count: selected.size,
                })
              : t('sources.discoverAddSelected', {
                  defaultValue: 'Add selected',
                })}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
