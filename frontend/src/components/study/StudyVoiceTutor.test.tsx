import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { StudyVoiceTutor } from './StudyVoiceTutor'

const transcribe = vi.fn()
const synthesize = vi.fn()

vi.mock('@/lib/api/study-voice', () => ({
  studyVoiceApi: {
    transcribe: (...args: unknown[]) => transcribe(...args),
    synthesize: (...args: unknown[]) => synthesize(...args),
  },
}))

describe('StudyVoiceTutor', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('shows spoken tutoring only when the local capability receipt is ready', () => {
    render(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'unavailable', tts: 'ready' }} />)
    expect(screen.getByRole('button', { name: 'Record question' })).toBeDisabled()
    expect(screen.getByText('Local speech recognition is unavailable.')).toBeVisible()
  })

  it('requests the microphone only after the record gesture and handles denial', async () => {
    const getUserMedia = vi.fn().mockRejectedValue(new DOMException('blocked', 'NotAllowedError'))
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
    render(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'ready', tts: 'unavailable' }} />)
    expect(getUserMedia).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Record question' }))
    await waitFor(() => expect(getUserMedia).toHaveBeenCalledWith({ audio: true }))
    expect(screen.getByRole('alert')).toHaveTextContent('Microphone access was denied.')
  })

  it('keeps the text tutor optional when speech is ready or unavailable', () => {
    const { rerender } = render(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'ready', tts: 'ready' }} />)
    expect(screen.getByRole('button', { name: 'Record question' })).toBeEnabled()
    rerender(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'unavailable', tts: 'unavailable' }} />)
    expect(screen.getByText('Local speech recognition is unavailable.')).toBeVisible()
    expect(screen.getByText('Local speech synthesis is unavailable.')).toBeVisible()
  })

  it('cancels an active recorder and releases the local stream', async () => {
    const tracks = [{ stop: vi.fn() }]
    const getUserMedia = vi.fn().mockResolvedValue({ getTracks: () => tracks })
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: { getUserMedia } })
    class RecorderStub {
      static isTypeSupported = vi.fn(() => true)
      state = 'inactive'
      mimeType = 'audio/webm'
      ondataavailable: ((event: { data: Blob }) => void) | null = null
      onstop: (() => void) | null = null
      onerror: (() => void) | null = null
      constructor() {}
      start() { this.state = 'recording' }
      stop() { this.state = 'inactive'; this.onstop?.() }
    }
    vi.stubGlobal('MediaRecorder', RecorderStub)
    render(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'ready', tts: 'unavailable' }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Record question' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel voice' })).toBeVisible())
    fireEvent.click(screen.getByRole('button', { name: 'Cancel voice' }))
    expect(tracks[0].stop).toHaveBeenCalled()
    expect(transcribe).not.toHaveBeenCalled()
  })

  it('creates and revokes a local audio URL for a tutor response', async () => {
    const createObjectURL = vi.fn(() => 'blob:study-audio')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: revokeObjectURL })
    synthesize.mockResolvedValue(new Blob([new Uint8Array([1, 2])], { type: 'audio/wav' }))
    render(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'unavailable', tts: 'ready' }} assistantText="A local answer." />)
    fireEvent.click(screen.getByRole('button', { name: 'Play tutor response' }))
    await waitFor(() => expect(screen.getByLabelText('Tutor response audio')).toBeVisible())
    expect(createObjectURL).toHaveBeenCalled()
    fireEvent.ended(screen.getByLabelText('Tutor response audio'))
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:study-audio')
  })

  it('ignores a cancelled stale synthesis while allowing the next synthesis to complete', async () => {
    const first = Promise.withResolvers<Blob>()
    const second = Promise.withResolvers<Blob>()
    const createObjectURL = vi.fn(() => 'blob:latest-audio')
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createObjectURL })
    synthesize.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    render(<StudyVoiceTutor planId="study_plan:one" capability={{ stt: 'unavailable', tts: 'ready' }} assistantText="A local answer." />)

    fireEvent.click(screen.getByRole('button', { name: 'Play tutor response' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel voice' })).toBeVisible())
    fireEvent.click(screen.getByRole('button', { name: 'Cancel voice' }))
    fireEvent.click(screen.getByRole('button', { name: 'Play tutor response' }))
    await waitFor(() => expect(synthesize).toHaveBeenCalledTimes(2))

    first.resolve(new Blob([new Uint8Array([1])], { type: 'audio/wav' }))
    await Promise.resolve()
    expect(createObjectURL).not.toHaveBeenCalled()

    second.resolve(new Blob([new Uint8Array([2])], { type: 'audio/wav' }))
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))
  })
})
