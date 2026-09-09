'use client'

import React, { useState, useRef, useEffect } from 'react'
import { Mic, Square, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { toast } from 'sonner'

interface AudioDictateButtonProps {
  onTranscribed: (text: string) => void
  className?: string
  size?: 'default' | 'sm' | 'lg' | 'icon'
  disabled?: boolean
  title?: string
}

export function AudioDictateButton({
  onTranscribed,
  className = '',
  size = 'icon',
  disabled = false,
  title,
}: AudioDictateButtonProps) {
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const streamRef = useRef<MediaStream | null>(null)

  // Clean up any active stream on unmount
  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((track) => track.stop())
        streamRef.current = null
      }
    }
  }, [])

  const startRecording = async () => {
    if (typeof window === 'undefined' || !navigator?.mediaDevices?.getUserMedia) {
      toast.error('Microphone recording is not supported in this environment')
      return
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream
      audioChunksRef.current = []

      let mimeType = 'audio/webm'
      if (typeof MediaRecorder !== 'undefined') {
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
          mimeType = 'audio/webm;codecs=opus'
        } else if (MediaRecorder.isTypeSupported('audio/webm')) {
          mimeType = 'audio/webm'
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          mimeType = 'audio/mp4'
        }
      }

      const mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      mediaRecorderRef.current = mediaRecorder

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data)
        }
      }

      mediaRecorder.onstop = async () => {
        if (streamRef.current) {
          streamRef.current.getTracks().forEach((track) => track.stop())
          streamRef.current = null
        }

        const resolvedMime = mediaRecorder.mimeType || 'audio/webm'
        const audioBlob = new Blob(audioChunksRef.current, { type: resolvedMime })
        if (audioBlob.size === 0) {
          return
        }

        setIsProcessing(true)
        try {
          const extension = resolvedMime.includes('mp4') ? 'm4a' : 'webm'
          const formData = new FormData()
          formData.append('file', audioBlob, `dictation.${extension}`)

          const response = await fetch('/api/audio/dictate', {
            method: 'POST',
            body: formData,
          })

          if (!response.ok) {
            const errData = await response.json().catch(() => ({}))
            throw new Error(errData.detail || 'Dictation transcription failed')
          }

          const data = await response.json()
          if (data.text && data.text.trim()) {
            onTranscribed(data.text.trim())
            toast.success('Dictation transcribed')
          } else {
            toast.info('No speech detected')
          }
        } catch (error: unknown) {
          console.error('Audio dictation error:', error)
          toast.error(error instanceof Error ? error.message : 'Failed to transcribe audio')
        } finally {
          setIsProcessing(false)
        }
      }

      mediaRecorder.start(250) // collect chunks every 250ms
      setIsRecording(true)
    } catch (err: unknown) {
      console.error('Microphone access error:', err)
      toast.error('Microphone access denied or audio input unavailable')
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
        className={cn('h-8 w-8 text-muted-foreground', className)}
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
      className={cn(
        'h-8 w-8 transition-all duration-200',
        isRecording
          ? 'animate-pulse bg-red-600 text-white hover:bg-red-700 shadow-xs ring-2 ring-red-500/40'
          : 'text-muted-foreground hover:text-foreground hover:bg-muted/70',
        className
      )}
      title={
        title || (isRecording ? 'Click to stop recording' : 'Dictate with local speech-to-text')
      }
      aria-label={isRecording ? 'Stop recording dictation' : 'Start audio dictation'}
    >
      {isRecording ? <Square className="h-3.5 w-3.5 fill-current" /> : <Mic className="h-4 w-4" />}
    </Button>
  )
}
