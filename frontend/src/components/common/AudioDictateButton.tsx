'use client'

import React, { useState, useRef } from 'react'
import { Mic, Square, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'

interface AudioDictateButtonProps {
  onTranscribed: (text: string) => void
  className?: string
  size?: 'default' | 'sm' | 'lg' | 'icon'
  disabled?: boolean
}

export function AudioDictateButton({
  onTranscribed,
  className = '',
  size = 'icon',
  disabled = false,
}: AudioDictateButtonProps) {
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioChunksRef.current = []

      const mediaRecorder = new MediaRecorder(stream)
      mediaRecorderRef.current = mediaRecorder

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data)
        }
      }

      mediaRecorder.onstop = async () => {
        // Stop all tracks
        stream.getTracks().forEach((track) => track.stop())

        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' })
        if (audioBlob.size === 0) {
          return
        }

        setIsProcessing(true)
        try {
          const formData = new FormData()
          formData.append('file', audioBlob, 'dictation.webm')

          const response = await fetch('/api/audio/dictate', {
            method: 'POST',
            body: formData,
          })

          if (!response.ok) {
            const errData = await response.json().catch(() => ({}))
            throw new Error(errData.detail || 'Dictation failed')
          }

          const data = await response.json()
          if (data.text) {
            onTranscribed(data.text)
            toast.success('Dictation transcribed')
          } else {
            toast.info('No speech detected')
          }
        } catch (error: any) {
          console.error('Audio dictation error:', error)
          toast.error(error.message || 'Failed to transcribe audio')
        } finally {
          setIsProcessing(false)
        }
      }

      mediaRecorder.start()
      setIsRecording(true)
    } catch (err: any) {
      console.error('Microphone access error:', err)
      toast.error('Microphone access denied or unavailable')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop()
      setIsRecording(false)
    }
  }

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (isRecording) {
      stopRecording()
    } else {
      startRecording()
    }
  }

  if (isProcessing) {
    return (
      <Button
        type="button"
        variant="ghost"
        size={size}
        disabled
        className={`h-8 w-8 text-muted-foreground ${className}`}
        aria-label="Transcribing audio"
      >
        <Loader2 className="h-4 w-4 animate-spin text-primary" />
      </Button>
    )
  }

  return (
    <Button
      type="button"
      variant={isRecording ? 'destructive' : 'ghost'}
      size={size}
      onClick={handleClick}
      disabled={disabled}
      className={`h-8 w-8 transition-colors ${
        isRecording ? 'animate-pulse bg-red-600 text-white hover:bg-red-700' : 'text-muted-foreground hover:text-foreground'
      } ${className}`}
      title={isRecording ? 'Click to stop recording' : 'Dictate with speech-to-text'}
      aria-label={isRecording ? 'Stop recording' : 'Start audio dictation'}
    >
      {isRecording ? <Square className="h-3.5 w-3.5 fill-current" /> : <Mic className="h-4 w-4" />}
    </Button>
  )
}
