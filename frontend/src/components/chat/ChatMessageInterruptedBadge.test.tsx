import React from 'react'
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { ChatMessageInterruptedBadge } from './ChatMessageInterruptedBadge'

// v0.8.117 — tests for the "Interrupted" chip shown on a stall-recovered
// partial AI message.

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (_k: string, opts?: { defaultValue?: string }) => opts?.defaultValue ?? _k,
  }),
}))

describe('ChatMessageInterruptedBadge', () => {
  it('renders the interrupted label', () => {
    render(<ChatMessageInterruptedBadge />)
    expect(screen.getByTestId('interrupted-badge')).toHaveTextContent('chat.interrupted')
  })

  it('renders a retry button that calls onRetry when clicked', () => {
    const onRetry = vi.fn()
    render(<ChatMessageInterruptedBadge onRetry={onRetry} />)

    const retry = screen.getByTestId('interrupted-retry')
    expect(retry).toBeInTheDocument()

    fireEvent.click(retry)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })

  it('renders no retry button when onRetry is not provided', () => {
    render(<ChatMessageInterruptedBadge />)
    expect(screen.queryByTestId('interrupted-retry')).not.toBeInTheDocument()
  })
})
