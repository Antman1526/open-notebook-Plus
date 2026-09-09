'use client'

// v0.8.85 — shadcn "resizable" primitive (improvement roadmap, Batch 3),
// wrapping react-resizable-panels. Used by the notebook workspace for
// draggable, width-remembered sources | notes | chat panes.
import { GripVertical } from 'lucide-react'
import * as ResizablePrimitive from 'react-resizable-panels'

import { cn } from '@/lib/utils'

function ResizablePanelGroup({
  className,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.PanelGroup>) {
  return (
    <ResizablePrimitive.PanelGroup
      className={cn(
        'flex h-full w-full data-[panel-group-direction=vertical]:flex-col',
        className
      )}
      {...props}
    />
  )
}

const ResizablePanel = ResizablePrimitive.Panel

function ResizableHandle({
  withHandle,
  className,
  ...props
}: React.ComponentProps<typeof ResizablePrimitive.PanelResizeHandle> & {
  withHandle?: boolean
}) {
  return (
    <ResizablePrimitive.PanelResizeHandle
      className={cn(
        'group relative flex w-px items-center justify-center bg-border/80 transition-all duration-200',
        'after:absolute after:inset-y-0 after:left-1/2 after:w-3.5 after:-translate-x-1/2',
        'focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring',
        'data-[resize-handle-state=hover]:bg-primary/70 data-[resize-handle-state=hover]:shadow-[0_0_8px_rgba(45,212,191,0.3)]',
        'data-[resize-handle-state=drag]:bg-primary data-[resize-handle-state=drag]:shadow-[0_0_14px_rgba(45,212,191,0.5)]',
        'data-[panel-group-direction=vertical]:h-px data-[panel-group-direction=vertical]:w-full',
        'data-[panel-group-direction=vertical]:after:left-0 data-[panel-group-direction=vertical]:after:h-3.5 data-[panel-group-direction=vertical]:after:w-full data-[panel-group-direction=vertical]:after:-translate-y-1/2 data-[panel-group-direction=vertical]:after:translate-x-0',
        className
      )}
      {...props}
    >
      {withHandle && (
        <div className="z-10 flex h-7 w-4 items-center justify-center rounded-full border border-border/70 bg-card/90 backdrop-blur-md shadow-xs transition-all duration-200 group-hover:scale-110 group-hover:border-primary/50 group-hover:bg-card active:scale-95">
          <GripVertical className="h-3 w-3 text-muted-foreground/60 transition-colors group-hover:text-primary" />
        </div>
      )}
    </ResizablePrimitive.PanelResizeHandle>
  )
}

export { ResizablePanelGroup, ResizablePanel, ResizableHandle }
