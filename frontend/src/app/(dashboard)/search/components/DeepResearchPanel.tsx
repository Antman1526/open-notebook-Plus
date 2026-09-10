// v0.8.117 — extracted from page.tsx (~945 lines) as part of the
// per-mode component split. Carries the Deep Research result Card
// verbatim (page.tsx lines 554-697). State (copiedBrief) and mutations
// (clipboard write, save-dialog open) stay in page.tsx per the task's
// instructions — this component only renders and calls back.

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Save, Sparkles, Layers, Copy, Check } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { searchApi } from '@/lib/api/search'
import type { ModalType } from '@/lib/hooks/use-modal-manager'

export type DeepResearchResult = Awaited<ReturnType<typeof searchApi.deepResearch>>

interface DeepResearchPanelProps {
  result: DeepResearchResult
  openModal: (type: ModalType, id: string) => void
  copiedBrief: boolean
  onCopyBrief: () => void
  onSaveNote: () => void
}

export function DeepResearchPanel({ result, openModal, copiedBrief, onCopyBrief, onSaveNote }: DeepResearchPanelProps) {
  return (
    <Card className="border-primary/40 bg-gradient-to-b from-primary/[0.04] to-transparent shadow-sm">
      <CardHeader className="pb-3 border-b">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-lg bg-primary/10 text-primary">
              <Sparkles className="h-4 w-4" />
            </div>
            <div>
              <CardTitle className="text-base font-semibold">Deep Research Synthesis</CardTitle>
              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">
                {result.objective}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 self-end sm:self-auto shrink-0">
            <Badge variant="secondary" className="text-xs font-mono">
              {result.evidence_count} sources
            </Badge>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={onCopyBrief}
              className="h-8 px-2.5 text-xs text-muted-foreground hover:text-foreground"
            >
              {copiedBrief ? (
                <>
                  <Check className="h-3.5 w-3.5 mr-1 text-primary" />
                  Copied
                </>
              ) : (
                <>
                  <Copy className="h-3.5 w-3.5 mr-1" />
                  Copy Brief
                </>
              )}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onSaveNote}
              className="h-8 px-2.5 text-xs"
            >
              <Save className="h-3.5 w-3.5 mr-1" />
              Save Note
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-5 pt-4">
        {result.plan?.inquiry_paths && result.plan.inquiry_paths.length > 0 && (
          <div className="space-y-2">
            <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Inquiry Paths Explored</Label>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2.5">
              {result.plan.inquiry_paths.map((p, idx) => (
                <div key={idx} className="p-3 rounded-lg border bg-card/60 text-xs space-y-1.5 shadow-xs">
                  <div className="font-medium text-foreground flex items-center justify-between gap-1.5">
                    <span className="flex items-center gap-1.5 min-w-0">
                      <Layers className="h-3.5 w-3.5 text-primary shrink-0" />
                      <span className="truncate">{p.sub_question}</span>
                    </span>
                    {p.facet && (
                      <Badge variant="outline" className="text-[10px] px-1.5 py-0 capitalize shrink-0 font-normal">
                        {p.facet}
                      </Badge>
                    )}
                  </div>
                  <div className="text-muted-foreground font-mono text-[11px] bg-muted/60 px-2 py-1 rounded truncate">
                    &ldquo;{p.search_query}&rdquo;
                  </div>
                  {p.rationale && (
                    <p className="text-[11px] text-muted-foreground leading-normal line-clamp-2">
                      {p.rationale}
                    </p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-2">
          <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Research Brief</Label>
          <div className="prose prose-sm prose-neutral dark:prose-invert max-w-none break-words p-5 rounded-lg border bg-card/40 leading-relaxed shadow-xs prose-headings:font-semibold prose-a:text-primary dark:prose-a:text-blue-400 prose-a:underline prose-code:bg-muted prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-table:my-4">
            <ReactMarkdown
              remarkPlugins={[remarkGfm, remarkMath]}
              rehypePlugins={[rehypeKatex]}
              components={{
                table: ({ children }) => (
                  <div className="my-4 overflow-x-auto">
                    <table className="min-w-full border-collapse border border-border text-xs">
                      {children}
                    </table>
                  </div>
                ),
                thead: ({ children }) => <thead className="bg-muted">{children}</thead>,
                th: ({ children }) => <th className="border border-border px-3 py-2 text-left font-semibold">{children}</th>,
                td: ({ children }) => <td className="border border-border px-3 py-2">{children}</td>,
              }}
            >
              {result.research_brief}
            </ReactMarkdown>
          </div>
        </div>

        {result.citations && result.citations.length > 0 && (
          <div className="space-y-2">
            <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Grounded Citations</Label>
            <div className="flex flex-wrap gap-1.5">
              {result.citations.map((c, idx) => (
                <Badge
                  key={idx}
                  variant="outline"
                  className="text-xs cursor-pointer hover:bg-muted transition-colors py-1 px-2.5 flex items-center gap-1.5"
                  onClick={() => {
                    if (c.id) {
                      openModal('source', c.id)
                    }
                  }}
                  title="Click to open source preview"
                >
                  <span className="font-mono font-semibold text-primary">[{c.ref || idx + 1}]</span>
                  <span>{c.title || c.id}</span>
                </Badge>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
