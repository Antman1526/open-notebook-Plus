'use client'

/**
 * ChatMessageInterruptedBadge.tsx — v0.8.117
 *
 * Small "Interrupted" chip rendered next to an AI message whose stream
 * stalled mid-generation (v0.8.115 stall guard) but was kept because
 * some partial content had already arrived (v0.8.116 recovery). The
 * `interrupted` flag it reflects is local-only UI state set by
 * useNotebookChat / useSourceChat's stall branch — see NotebookChatMessage
 * / SourceChatMessage in lib/types/api.ts.
 *
 * When an `onRetry` handler is provided, the badge also offers a small
 * ghost button that re-sends the preceding question. Omitted (no
 * preceding human message, or currently streaming) → badge-only.
 */

import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useTranslation } from '@/lib/hooks/use-translation'

export interface ChatMessageInterruptedBadgeProps {
  /** Re-sends the preceding human message. Omitted → no retry button. */
  onRetry?: () => void
}

export function ChatMessageInterruptedBadge({
  onRetry,
}: ChatMessageInterruptedBadgeProps) {
  const { t } = useTranslation()

  return (
    <span className="inline-flex items-center gap-1">
      <Badge
        variant="outline"
        className="text-xs gap-1 font-normal"
        data-testid="interrupted-badge"
      >
        {t('chat.interrupted')}
      </Badge>
      {onRetry && (
        <Button
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs"
          data-testid="interrupted-retry"
          onClick={onRetry}
        >
          {t('common.retry')}
        </Button>
      )}
    </span>
  )
}

export default ChatMessageInterruptedBadge
