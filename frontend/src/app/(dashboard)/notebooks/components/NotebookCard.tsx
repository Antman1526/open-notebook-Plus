'use client'

import { useRouter } from 'next/navigation'
import { NotebookResponse } from '@/lib/types/api'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { MoreHorizontal, Archive, ArchiveRestore, Trash2, FileText, StickyNote } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useUpdateNotebook } from '@/lib/hooks/use-notebooks'
import { NotebookDeleteDialog } from './NotebookDeleteDialog'
import { useState } from 'react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getDateLocale } from '@/lib/utils/date-locale'
import { TurnIntoPodcastAction } from '@/components/podcasts/TurnIntoPodcastAction'
import { usePodcastStudioStore } from '@/lib/stores/podcast-studio-store'
interface NotebookCardProps {
  notebook: NotebookResponse
}

export function NotebookCard({ notebook }: NotebookCardProps) {
  const { t, language } = useTranslation()
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const router = useRouter()
  const updateNotebook = useUpdateNotebook()
  const openPodcastReview = usePodcastStudioStore((state) => state.open)
  const noReadableContent = notebook.source_count + notebook.note_count === 0

  const handleArchiveToggle = (e: React.MouseEvent) => {
    e.stopPropagation()
    updateNotebook.mutate({
      id: notebook.id,
      data: { archived: !notebook.archived }
    })
  }

  const handleCardClick = () => {
    router.push(`/notebooks/${encodeURIComponent(notebook.id)}`)
  }

  return (
    <>
      <div 
        className="group relative rounded-2xl p-1 bg-gradient-to-b from-border/40 via-border/10 to-transparent ring-1 ring-border/30 transition-all duration-300 hover:ring-primary/40 hover:shadow-md active:scale-[0.99] cursor-pointer"
        onClick={handleCardClick}
      >
        <Card 
          className="border-0 rounded-[calc(1rem-2px)] bg-card/95 py-3 transition-colors group-hover:bg-card shadow-[inset_0_1px_1px_rgba(255,255,255,0.06)]"
        >
          <CardHeader className="pb-2">
            <div className="flex min-w-0 items-start justify-between">
              <div className="flex-1 min-w-0">
                <CardTitle className="text-base truncate group-hover:text-primary transition-colors">
                  {notebook.name}
                </CardTitle>
                {notebook.archived && (
                  <Badge variant="secondary" className="mt-1">
                    {t('notebooks.archived')}
                  </Badge>
                )}
              </div>
              
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 w-8 p-0 rounded-full opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-all duration-200 hover:bg-muted/80"
                    aria-label={`Actions for ${notebook.name}`}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <MoreHorizontal className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
                  <DropdownMenuItem onClick={handleArchiveToggle}>
                    {notebook.archived ? (
                      <>
                        <ArchiveRestore className="h-4 w-4 mr-2" />
                        {t('notebooks.unarchive')}
                      </>
                    ) : (
                      <>
                        <Archive className="h-4 w-4 mr-2" />
                        {t('notebooks.archive')}
                      </>
                    )}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      setShowDeleteDialog(true)
                    }}
                    className="text-destructive"
                  >
                    <Trash2 className="h-4 w-4 mr-2" />
                    {t('common.delete')}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </CardHeader>
          
          <CardContent>
            <CardDescription className="line-clamp-2 text-sm">
              {notebook.description || t('chat.noDescription')}
            </CardDescription>

            <div className="mt-2.5 text-xs text-muted-foreground">
              {t('common.updated').replace('{time}', formatDistanceToNow(new Date(notebook.updated), { 
                addSuffix: true,
                locale: getDateLocale(language)
              }))}
            </div>

            <div className="mt-3" onClick={(event) => event.stopPropagation()}>
              <TurnIntoPodcastAction
                selection={{ kind: 'notebook', notebookId: notebook.id }}
                destination="quick"
                disabledReason={noReadableContent ? 'No readable content is available' : undefined}
                onOpen={openPodcastReview}
              />
            </div>

            {/* Item counts footer */}
            <div className="mt-3 flex items-center gap-1.5 border-t pt-3">
              <Badge
                variant="outline"
                className={`text-xs flex items-center gap-1.5 px-2.5 py-0.5 rounded-full transition-all duration-200 ${
                  notebook.source_count > 0
                    ? 'text-foreground bg-muted/40 border-border/80 shadow-xs'
                    : 'text-muted-foreground/70 bg-transparent border-border/40'
                }`}
                title={`${notebook.source_count} sources`}
              >
                <FileText className={`h-3 w-3 ${notebook.source_count > 0 ? 'text-primary' : 'text-muted-foreground/60'}`} />
                <span>{notebook.source_count}</span>
              </Badge>
              <Badge
                variant="outline"
                className={`text-xs flex items-center gap-1.5 px-2.5 py-0.5 rounded-full transition-all duration-200 ${
                  notebook.note_count > 0
                    ? 'text-foreground bg-muted/40 border-border/80 shadow-xs'
                    : 'text-muted-foreground/70 bg-transparent border-border/40'
                }`}
                title={`${notebook.note_count} notes`}
              >
                <StickyNote className={`h-3 w-3 ${notebook.note_count > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-muted-foreground/60'}`} />
                <span>{notebook.note_count}</span>
              </Badge>
            </div>
          </CardContent>
        </Card>
      </div>

      <NotebookDeleteDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        notebookId={notebook.id}
        notebookName={notebook.name}
      />
    </>
  )
}
