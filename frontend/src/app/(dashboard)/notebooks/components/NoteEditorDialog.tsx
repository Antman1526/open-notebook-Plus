'use client'

import { Controller, useForm, useWatch } from 'react-hook-form'
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { zodResolver } from '@hookform/resolvers/zod'
import { z } from 'zod'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { useCreateNote, useUpdateNote, useNote } from '@/lib/hooks/use-notes'
import { QUERY_KEYS } from '@/lib/api/query-client'
import { MarkdownEditor } from '@/components/ui/markdown-editor'
import { InlineEdit } from '@/components/common/InlineEdit'
import { AudioDictateButton } from '@/components/common/AudioDictateButton'
import { cn } from "@/lib/utils";
import { useTranslation } from '@/lib/hooks/use-translation'
import { useToast } from '@/lib/hooks/use-toast'

// v0.7.199 — factory pattern so the validation message is
// translated. Was hardcoded English "Content is required" — non-
// English users saw English text in the field-error.
const makeCreateNoteSchema = (t: (key: string) => string) =>
  z.object({
    title: z.string().optional(),
    content: z.string().min(1, t('common.contentRequired')),
  })

type CreateNoteFormData = z.infer<ReturnType<typeof makeCreateNoteSchema>>

interface NoteEditorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  notebookId: string
  note?: { id: string; title: string | null; content: string | null }
}

export function NoteEditorDialog({ open, onOpenChange, notebookId, note }: NoteEditorDialogProps) {
  const { t } = useTranslation()
  const { toast } = useToast()
  const createNote = useCreateNote()
  const updateNote = useUpdateNote()
  const queryClient = useQueryClient()
  const isEditing = Boolean(note)

  // Ensure note ID has 'note:' prefix for API calls
  const noteIdWithPrefix = note?.id
    ? (note.id.includes(':') ? note.id : `note:${note.id}`)
    : ''

  const { data: fetchedNote, isLoading: noteLoading } = useNote(noteIdWithPrefix, { enabled: open && !!note?.id })
  const isSaving = isEditing ? updateNote.isPending : createNote.isPending
  const {
    handleSubmit,
    control,
    formState: { errors },
    reset,
    setValue,
    getValues,
  } = useForm<CreateNoteFormData>({
    resolver: zodResolver(makeCreateNoteSchema(t)),
    defaultValues: {
      title: '',
      content: '',
    },
  })
  const watchTitle = useWatch({ control, name: 'title' })
  const [isEditorFullscreen, setIsEditorFullscreen] = useState(false)

  useEffect(() => {
    if (!open) {
      reset({ title: '', content: '' })
      return
    }

    const source = fetchedNote ?? note
    const title = source?.title ?? ''
    const content = source?.content ?? ''

    reset({ title, content })
  }, [open, note, fetchedNote, reset])

  // v0.7.59 — scope the fullscreen-class observer to the editor wrapper
  // instead of `document.body`. The previous version fired on every
  // class mutation anywhere in the document: Radix tooltips opening,
  // dropdown menus toggling, Sonner toasts appearing/dismissing — all
  // of those mutate class lists deep in the tree and woke up the
  // observer just so we could re-query for `.w-md-editor-fullscreen`.
  // While the dialog was open that was a continuous low-grade CPU
  // burn. Observing the editor wrapper alone gives us the same
  // signal — `.w-md-editor-fullscreen` lives inside the editor's own
  // DOM — without the noise.
  const editorContainerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    // Editor renders behind `noteLoading` for the edit-existing case,
    // so re-check after loading completes — include `noteLoading` in
    // deps so the effect re-runs once the wrapper actually mounts.
    const target = editorContainerRef.current
    if (!target) return

    const observer = new MutationObserver(() => {
      setIsEditorFullscreen(!!target.querySelector('.w-md-editor-fullscreen'))
    })
    observer.observe(target, { subtree: true, attributes: true, attributeFilter: ['class'] })
    return () => observer.disconnect()
  }, [open, noteLoading])

  const onSubmit = async (data: CreateNoteFormData) => {
    if (note) {
      await updateNote.mutateAsync({
        id: noteIdWithPrefix,
        data: {
          title: data.title || undefined,
          content: data.content,
        },
      })
      // Only invalidate notebook-specific queries if we have a notebookId
      if (notebookId) {
        queryClient.invalidateQueries({ queryKey: QUERY_KEYS.notes(notebookId) })
      }
    } else {
      // Creating a note requires a notebookId
      if (!notebookId) {
        // v0.7.202 — was silent `console.error + return`. The
        // user clicked Save, watched the dialog do nothing, and
        // had no feedback at all. Surface a toast so the failure
        // is visible. (In normal flows this branch is unreachable
        // because the dialog opens with a parent-provided
        // notebookId; the toast is a safety net for a
        // misconfigured caller.)
        console.error('Cannot create note without notebook_id')
        toast({
          title: t('common.error'),
          description: t('notebooks.failedToCreateNote'),
          variant: 'destructive',
        })
        return
      }
      await createNote.mutateAsync({
        title: data.title || undefined,
        content: data.content,
        note_type: 'human',
        notebook_id: notebookId,
      })
    }
    reset()
    onOpenChange(false)
  }

  const handleClose = () => {
    reset()
    setIsEditorFullscreen(false)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className={cn(
          "sm:max-w-3xl w-full max-h-[90vh] overflow-hidden p-0",
          isEditorFullscreen && "!max-w-screen !max-h-screen border-none w-screen h-screen"
      )}>
        <DialogTitle className="sr-only">
          {isEditing ? t('sources.editNote') : t('sources.createNote')}
        </DialogTitle>
        <form onSubmit={handleSubmit(onSubmit)} className="flex h-full flex-col min-w-0">
          {isEditing && noteLoading ? (
            <div className="flex-1 flex items-center justify-center py-10">
              <span className="text-sm text-muted-foreground">{t('common.loading')}</span>
            </div>
          ) : (
            <>
              <div className="border-b px-6 py-4 flex items-center justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <InlineEdit
                    id="note-title"
                    name="title"
                    value={watchTitle ?? ''}
                    onSave={(value) => setValue('title', value || '')}
                    placeholder={t('sources.addTitle')}
                    emptyText={t('sources.untitledNote')}
                    className="text-xl font-semibold"
                    inputClassName="text-xl font-semibold"
                  />
                </div>
                <AudioDictateButton
                  onTranscribed={(text) => {
                    const current = getValues('content') || ''
                    setValue('content', current ? `${current}\n\n${text}` : text, { shouldDirty: true })
                  }}
                  disabled={isSaving}
                />
              </div>

              <div className={cn(
                  "flex-1 overflow-y-auto",
                  !isEditorFullscreen && "px-6 py-4")
              }>
                <Controller
                  control={control}
                  name="content"
                  render={({ field }) => (
                    <div ref={editorContainerRef} className="w-full h-full">
                      <MarkdownEditor
                        key={note?.id ?? 'new'}
                        textareaId="note-content"
                        value={field.value}
                        onChange={field.onChange}
                        height={420}
                        placeholder={t('sources.writeNotePlaceholder')}
                        className={cn(
                            "w-full h-full min-h-[420px] max-h-[500px] overflow-hidden [&_.w-md-editor]:!static [&_.w-md-editor]:!w-full [&_.w-md-editor]:!h-full [&_.w-md-editor-content]:overflow-y-auto",
                            !isEditorFullscreen && "rounded-md border"
                        )}
                      />
                    </div>
                  )}
                />
                {errors.content && (
                  <p className="text-sm text-destructive mt-1">{errors.content.message}</p>
                )}
              </div>
            </>
          )}

          <div className="border-t px-6 py-4 flex justify-end gap-2">
            <Button type="button" variant="outline" onClick={handleClose}>
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              disabled={isSaving || (isEditing && noteLoading)}
            >
              {isSaving
                ? isEditing ? `${t('common.saving')}...` : `${t('common.creating')}...`
                : isEditing
                  ? t('sources.saveNote')
                  : t('sources.createNoteBtn')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
