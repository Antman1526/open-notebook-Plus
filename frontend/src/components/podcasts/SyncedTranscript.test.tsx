import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { SyncedTranscript } from './SyncedTranscript'

describe('SyncedTranscript', () => {
  it('seeks from a segment and keeps citation handling explicit', () => {
    const onSeek = vi.fn()
    const onCitationClick = vi.fn()

    render(
      <SyncedTranscript
        currentTime={4}
        onSeek={onSeek}
        onCitationClick={onCitationClick}
        segments={[{ start_seconds: 3, end_seconds: 8, speaker: 'Alex', text: 'Grounded finding.', citation_ids: ['source:1'] }]}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '0:03 Alex' }))
    fireEvent.click(screen.getByRole('button', { name: 'source:1' }))

    expect(onSeek).toHaveBeenCalledWith(3)
    expect(onCitationClick).toHaveBeenCalledWith('source:1')
  })

  it('toggles auto-scroll mode and copies markdown', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    render(
      <SyncedTranscript
        currentTime={4}
        onSeek={vi.fn()}
        segments={[
          { start_seconds: 3, end_seconds: 8, speaker: 'Alex', text: 'Grounded finding.', citation_ids: [] },
        ]}
      />,
    )

    const toggleBtn = screen.getByRole('button', { name: /Auto-scroll: On/i })
    fireEvent.click(toggleBtn)
    expect(screen.getByRole('button', { name: /Auto-scroll: Off/i })).toBeDefined()

    const copyBtn = screen.getByRole('button', { name: /Copy/i })
    fireEvent.click(copyBtn)
    expect(writeText).toHaveBeenCalledWith('**[0:03] Alex:** Grounded finding.')
  })
})
