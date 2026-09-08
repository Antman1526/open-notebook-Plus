'use client'

import { useState } from 'react'
import { NotebookResponse } from '@/lib/types/api'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Archive, ArchiveRestore, Download, Trash2 } from 'lucide-react'
import { useUpdateNotebook } from '@/lib/hooks/use-notebooks'
import { NotebookDeleteDialog } from './NotebookDeleteDialog'
import { ExportNotebookDialog } from './ExportNotebookDialog'
import { formatDistanceToNow } from 'date-fns'
import { getDateLocale } from '@/lib/utils/date-locale'
import { InlineEdit } from '@/components/common/InlineEdit'
import { MindMapButton } from '@/components/notebooks/MindMapButton'
import { useTranslation } from '@/lib/hooks/use-translation'

interface NotebookHeaderProps {
  notebook: NotebookResponse
}

export function NotebookHeader({ notebook }: NotebookHeaderProps) {
  const { t, language } = useTranslation()
  const dfLocale = getDateLocale(language)
  const [showDeleteDialog, setShowDeleteDialog] = useState(false)
  const [showExportDialog, setShowExportDialog] = useState(false)
  
  const updateNotebook = useUpdateNotebook()

  const handleUpdateName = async (name: string) => {
    if (!name || name === notebook.name) return
    
    await updateNotebook.mutateAsync({
      id: notebook.id,
      data: { name }
    })
  }

  const handleUpdateDescription = async (description: string) => {
    if (description === notebook.description) return
    
    await updateNotebook.mutateAsync({
      id: notebook.id,
      data: { description: description || undefined }
    })
  }

  const handleArchiveToggle = () => {
    updateNotebook.mutate({
      id: notebook.id,
      data: { archived: !notebook.archived }
    })
  }

  return (
    <>
      <div className="pb-1">
        <div className="space-y-2">
          <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <InlineEdit
                id="notebook-name"
                name="notebook-name"
                value={notebook.name}
                onSave={handleUpdateName}
                // v0.7.180 — font-bold → font-semibold so the editable
                // notebook title matches the v0.7.153 H1 weight standard
                // and doesn't outweigh the dashboard H1s above it.
                className="min-w-0 break-words text-2xl font-semibold"
                inputClassName="text-2xl font-semibold"
                placeholder={t('notebooks.namePlaceholder')}
              />
              {notebook.archived && (
                <Badge variant="secondary">{t('notebooks.archived')}</Badge>
              )}
            </div>
            <div className="flex w-full flex-wrap gap-2 sm:w-auto sm:justify-end">
              <MindMapButton notebookId={notebook.id} />
              <Button
                variant="outline"
                size="sm"
                onClick={handleArchiveToggle}
              >
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
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowExportDialog(true)}
              >
                <Download className="h-4 w-4 mr-2" />
                {t('notebooks.export.button')}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setShowDeleteDialog(true)}
                className="text-destructive hover:text-destructive/90"
              >
                <Trash2 className="h-4 w-4 mr-2" />
                {t('common.delete')}
              </Button>
            </div>
          </div>
          
          <InlineEdit
            id="notebook-description"
            name="notebook-description"
            value={notebook.description || ''}
            onSave={handleUpdateDescription}
            className="text-muted-foreground"
            inputClassName="text-muted-foreground"
            placeholder={t('notebooks.addDescription')}
            multiline
            emptyText={t('notebooks.addDescription')}
          />
          
          <div className="text-sm text-muted-foreground">
            {t('common.created').replace('{time}', formatDistanceToNow(new Date(notebook.created), { addSuffix: true, locale: dfLocale }))} • 
            {t('common.updated').replace('{time}', formatDistanceToNow(new Date(notebook.updated), { addSuffix: true, locale: dfLocale }))}
          </div>
        </div>
      </div>

      <NotebookDeleteDialog
        open={showDeleteDialog}
        onOpenChange={setShowDeleteDialog}
        notebookId={notebook.id}
        notebookName={notebook.name}
        redirectAfterDelete
      />

      <ExportNotebookDialog
        open={showExportDialog}
        onOpenChange={setShowExportDialog}
        notebookId={notebook.id}
        notebookName={notebook.name}
      />
    </>
  )
}
