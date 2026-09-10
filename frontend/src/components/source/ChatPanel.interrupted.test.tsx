import React from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeAll, describe, expect, it, vi } from 'vitest'

import { ChatPanel } from './ChatPanel'
import type { SourceChatMessage } from '@/lib/types/api'

// v0.8.117 — the "Interrupted" chip + retry, shown on an AI message kept by
// the stream-stall recovery (v0.8.116). Mocking mirrors ChatPanel.cancel-run.test.tsx.

beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn()
})

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (_key: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? _key,
  }),
}))

vi.mock('@/lib/hooks/use-modal-manager', () => ({
  useModalManager: () => ({ openModal: vi.fn() }),
}))

vi.mock('@/lib/hooks/use-evaluation', () => ({
  useLatestMessageEvaluations: () => ({
    data: {},
    isLoading: false,
    isError: false,
  }),
}))

vi.mock('./ModelSelector', () => ({
  ModelSelector: () => <div data-testid="model-selector" />,
}))

vi.mock('@/components/source/SessionManager', () => ({
  SessionManager: () => <div data-testid="session-manager" />,
}))

vi.mock('@/components/source/MessageActions', () => ({
  MessageActions: () => <div data-testid="message-actions" />,
}))

vi.mock('@/components/common/ContextIndicator', () => ({
  ContextIndicator: () => <div data-testid="context-indicator" />,
}))

vi.mock('@/components/chat/CitationPill', () => ({
  CitationPill: () => <span data-testid="citation-pill" />,
}))

vi.mock('@/components/chat/ChatMessageProviderBadge', () => ({
  ChatMessageProviderBadge: () => <span data-testid="provider-badge" />,
}))

vi.mock('@/components/chat/ChatMessagePrivacyBadge', () => ({
  ChatMessagePrivacyBadge: () => <span data-testid="privacy-badge" />,
}))

vi.mock('@/components/chat/ChatMessageAgentStateBadge', () => ({
  ChatMessageAgentStateBadge: () => <span data-testid="agent-state-badge" />,
}))

vi.mock('@/components/chat/McpToolPicker', () => ({
  McpToolPicker: () => <div data-testid="mcp-tool-picker" />,
}))

vi.mock('@/components/deeper-notebook', async () => {
  const actual = await vi.importActual<Record<string, unknown>>('@/components/deeper-notebook')
  return {
    ...actual,
    RunTimeline: () => <div data-testid="run-timeline" />,
  }
})

const interruptedMessages: SourceChatMessage[] = [
  { id: 'human-1', type: 'human', content: 'q' },
  { id: 'ai-1', type: 'ai', content: 'partial', interrupted: true },
]

describe('ChatPanel interrupted badge', () => {
  it('shows the interrupted badge and retries the preceding human message on click', () => {
    const onSendMessage = vi.fn()

    render(
      <ChatPanel
        messages={interruptedMessages}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={onSendMessage}
      />,
    )

    expect(screen.getByTestId('interrupted-badge')).toBeInTheDocument()

    const retry = screen.getByTestId('interrupted-retry')
    fireEvent.click(retry)

    expect(onSendMessage).toHaveBeenCalledWith('q', undefined)
  })

  it('shows no interrupted badge for a non-interrupted AI message', () => {
    render(
      <ChatPanel
        messages={[
          { id: 'human-1', type: 'human', content: 'q' },
          { id: 'ai-1', type: 'ai', content: 'answer' },
        ]}
        isStreaming={false}
        contextIndicators={null}
        onSendMessage={vi.fn()}
      />,
    )

    expect(screen.queryByTestId('interrupted-badge')).not.toBeInTheDocument()
  })
})
