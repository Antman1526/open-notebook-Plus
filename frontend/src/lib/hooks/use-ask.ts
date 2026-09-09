'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { getApiErrorMessage } from '@/lib/utils/error-handler'
import { searchApi } from '@/lib/api/search'
import { AskStreamEvent } from '@/lib/types/search'
import {
  isStreamStallError,
  readWithIdleTimeout,
  resolveStreamIdleTimeoutMs,
} from '@/lib/utils/stream-stall'

interface AskModels {
  strategy: string
  answer: string
  finalAnswer: string
}

interface StrategyData {
  reasoning: string
  searches: Array<{ term: string; instructions: string }>
}

interface AskState {
  isStreaming: boolean
  strategy: StrategyData | null
  answers: string[]
  finalAnswer: string | null
  error: string | null
}

export function useAsk() {
  const { t } = useTranslation()
  const [state, setState] = useState<AskState>({
    isStreaming: false,
    strategy: null,
    answers: [],
    finalAnswer: null,
    error: null
  })

  // v0.6.23 — track in-flight controller + mount state so we can cancel
  // the stream on unmount (or on a second sendAsk before the first
  // finishes). Without this, the reader leaks AND every setState fired
  // by the streaming loop hits the unmounted component → React warning.
  const abortRef = useRef<AbortController | null>(null)
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      abortRef.current?.abort()
      abortRef.current = null
    }
  }, [])

  // v0.8.116 — latest sendAsk for the stall toast's "Try again" action.
  const sendAskRef = useRef<((question: string, models: AskModels) => Promise<void>) | null>(null)

  const sendAsk = useCallback(async (question: string, models: AskModels) => {
    // Validate inputs
    if (!question.trim()) {
      toast.error(t('apiErrors.pleaseEnterQuestion'))
      return
    }

    if (!models.strategy || !models.answer || !models.finalAnswer) {
      toast.error(t('apiErrors.pleaseConfigureModels'))
      return
    }

    // Cancel any prior in-flight stream before starting a new one.
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    // Reset state
    setState({
      isStreaming: true,
      strategy: null,
      answers: [],
      finalAnswer: null,
      error: null
    })

    let reader: ReadableStreamDefaultReader<Uint8Array> | null = null
    try {
      const response = await searchApi.askKnowledgeBase({
        question,
        strategy_model: models.strategy,
        answer_model: models.answer,
        final_answer_model: models.finalAnswer
      }, controller.signal)

      if (!response) {
        throw new Error('No response body received from server')
      }

      reader = response.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      // v0.8.34 — defense-in-depth cap. Matches the v0.7.49 buffer
      // cap in useSourceChat; a stream that never emits a newline
      // (server bug, transport corruption) would otherwise grow
      // `buffer` unbounded and exhaust browser memory. 4 MiB is
      // generous for SSE event lines (longest realistic event is
      // the final_answer payload, < 100 KB).
      const BUFFER_MAX = 4 * 1024 * 1024
      // v0.8.115 — idle-timeout guard (see stream-stall.ts). Per-read, so
      // a slow local model that keeps trickling tokens is never cut off.
      const idleTimeoutMs = resolveStreamIdleTimeoutMs()

      // v0.8.70 — batch the per-token `final_answer_delta` into ≤1 setState per
      // paint frame via rAF (was one setState per token → a re-render storm
      // during long synthesis). `finalAccum` is the source of truth; the
      // terminal `final_answer` event still replaces it with the canonical text.
      let finalAccum = ''
      let finalRafId: number | null = null
      const flushFinal = () => {
        finalRafId = null
        if (!mountedRef.current) return
        setState(prev => ({ ...prev, finalAnswer: finalAccum }))
      }
      const scheduleFinalFlush = () => {
        if (finalRafId == null) finalRafId = requestAnimationFrame(flushFinal)
      }

      while (true) {
        // Bail if unmounted between chunks — don't bother reading further.
        if (!mountedRef.current) break
        const { done, value } = await readWithIdleTimeout(reader, idleTimeoutMs)

        if (done) {
          break
        }

        buffer += decoder.decode(value, { stream: true })
        if (buffer.length > BUFFER_MAX) {
          throw new Error('ask stream buffer exceeded 4 MiB')
        }
        const lines = buffer.split('\n')

        // Keep the last incomplete line in buffer
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const jsonStr = line.slice(6).trim()
              if (!jsonStr) continue

              const data: AskStreamEvent = JSON.parse(jsonStr)
              // v0.6.23 — bail if unmounted between the read and the setState
              // (the chunk may straddle the unmount). Errors still re-raise.
              if (!mountedRef.current && data.type !== 'error') continue

              if (data.type === 'strategy') {
                setState(prev => ({
                  ...prev,
                  strategy: {
                    reasoning: data.reasoning || '',
                    searches: data.searches || []
                  }
                }))
              } else if (data.type === 'answer') {
                setState(prev => ({
                  ...prev,
                  answers: [...prev.answers, data.content || '']
                }))
              } else if (data.type === 'final_answer_delta') {
                // v0.7.43 — per-token chunk for the final synthesis.
                // v0.8.70 — accumulate + rAF-flush instead of setState/token.
                finalAccum += data.content || ''
                scheduleFinalFlush()
              } else if (data.type === 'final_answer') {
                // v0.7.43 — canonical terminal event. Replaces the
                // streamed buffer with the server's final string
                // (after any post-processing like clean_thinking_content).
                // v0.8.70 — cancel any pending delta flush first.
                if (finalRafId != null) {
                  cancelAnimationFrame(finalRafId)
                  finalRafId = null
                }
                finalAccum = data.content || finalAccum
                setState(prev => ({
                  ...prev,
                  finalAnswer: finalAccum,
                  isStreaming: false
                }))
              } else if (data.type === 'complete') {
                setState(prev => ({
                  ...prev,
                  isStreaming: false
                }))
              } else if (data.type === 'error') {
                throw new Error(data.message || 'Stream error occurred')
              }
            } catch (e) {
              if (e instanceof SyntaxError) {
                console.error('Error parsing SSE data:', e, 'Line:', line)
                // Don't throw - continue processing other lines
              } else {
                throw e
              }
            }
          }
        }
      }

      // v0.8.70 — drain any buffered final-answer delta before finishing.
      if (finalRafId != null) {
        cancelAnimationFrame(finalRafId)
        finalRafId = null
      }
      flushFinal()

      // Ensure streaming is stopped
      if (mountedRef.current) {
        setState(prev => ({ ...prev, isStreaming: false }))
      }

    } catch (error) {
      // AbortError from controller.abort() is expected — silent.
      if ((error as { name?: string }).name === 'AbortError') {
        return
      }
      const err = error as { message?: string }
      const errorMessage = err.message || 'An unexpected error occurred'
      console.error('Ask error:', error)

      if (mountedRef.current) {
        setState(prev => ({
          ...prev,
          isStreaming: false,
          error: errorMessage
        }))

        if (isStreamStallError(error)) {
          // v0.8.115 — specific copy: this is almost always a local model
          // daemon that died or a post-sleep half-open socket, not a
          // server-side failure. Point the user at the daemon.
          // v0.8.116 — partial strategy/answers stay in state (only
          // isStreaming flips); the action re-runs the same question.
          toast.error(t('apiErrors.streamStalled'), {
            description: t('apiErrors.streamStalledHint', { seconds: error.idleSeconds }),
            action: {
              label: t('common.accessibility.retry'),
              onClick: () => {
                void sendAskRef.current?.(question, models)
              },
            },
          })
        } else {
          toast.error(t('apiErrors.askFailed'), {
            description: getApiErrorMessage(errorMessage, (key) => t(key))
          })
        }
      }
    } finally {
      // v0.7.54 — cancel the reader BEFORE releasing the lock so the
      // underlying HTTP response body is actually torn down. Just
      // releaseLock() leaves the connection open until GC and the
      // backend's `is_disconnected()` doesn't fire — the ask graph
      // (multiple LLM calls) keeps generating answers nobody will see.
      // Mirrors v0.7.50 chat.ts.
      if (reader) {
        try {
          await reader.cancel()
        } catch {
          // cancel can throw if the stream is already errored; ignore.
        }
        try {
          reader.releaseLock()
        } catch {
          // Reader may already be released (cancellation path); ignore.
        }
      }
      // Clear the controller ref if it's still ours (i.e. nobody started
      // a newer ask in the meantime).
      if (abortRef.current === controller) {
        abortRef.current = null
      }
    }
  }, [t])

  useEffect(() => {
    sendAskRef.current = sendAsk
  }, [sendAsk])

  const reset = useCallback(() => {
    setState({
      isStreaming: false,
      strategy: null,
      answers: [],
      finalAnswer: null,
      error: null
    })
  }, [])

  return {
    ...state,
    sendAsk,
    reset
  }
}
