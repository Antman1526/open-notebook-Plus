// v0.8.115 — Contract test for the streaming idle-timeout guard.
//
// Every consumer that reads a streaming response body must go through
// `readWithIdleTimeout` (src/lib/utils/stream-stall.ts) instead of a bare
// `reader.read()`. A bare read has no upper bound: when a local model
// daemon dies mid-generation, or the machine sleeps and wakes with a
// half-open socket, the read never settles and the UI stays in
// "streaming" forever. The hooks must also recognise `StreamStallError`
// so the user sees copy that names the cause rather than a generic
// "failed to send message".
//
// Source-text assertion in the same style as chat-race-guard.test.ts: a
// refactor that reintroduces a bare read, or drops the stall branch from
// a catch block, fails here loudly. The behavioural half lives in
// src/lib/utils/stream-stall.test.ts and src/lib/api/chat.stream-stall.test.ts.

import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { join } from 'path'

const READERS = [
  ['use-ask.ts', join(__dirname, 'use-ask.ts')],
  ['useSourceChat.ts', join(__dirname, 'useSourceChat.ts')],
  ['api/chat.ts', join(__dirname, '..', 'api', 'chat.ts')],
] as const

const TOASTERS = [
  ['use-ask.ts', join(__dirname, 'use-ask.ts')],
  ['useSourceChat.ts', join(__dirname, 'useSourceChat.ts')],
  ['useNotebookChat.ts', join(__dirname, 'useNotebookChat.ts')],
] as const

describe('v0.8.115 stream stall guard', () => {
  for (const [label, file] of READERS) {
    describe(label, () => {
      const src = readFileSync(file, 'utf-8')

      it('reads the body through readWithIdleTimeout', () => {
        expect(
          src,
          `${label}: must call readWithIdleTimeout(reader, …) so a silent ` +
          `stream is detected instead of hanging the UI forever.`,
        ).toMatch(/readWithIdleTimeout\(\s*reader\s*,/)
      })

      it('has no bare reader.read() left', () => {
        expect(
          src,
          `${label}: a bare reader.read() bypasses the idle-timeout guard.`,
        ).not.toMatch(/await\s+reader\.read\(\)/)
      })

      it('resolves the timeout from the environment helper', () => {
        expect(src).toMatch(/resolveStreamIdleTimeoutMs\(\)/)
      })
    })
  }

  for (const [label, file] of TOASTERS) {
    describe(`${label} error surface`, () => {
      const src = readFileSync(file, 'utf-8')

      it('branches on isStreamStallError before the generic toast', () => {
        expect(src).toMatch(/isStreamStallError\(/)
      })

      it('uses the dedicated stall copy with the seconds interpolation', () => {
        expect(src).toMatch(/apiErrors\.streamStalled'/)
        expect(src).toMatch(/apiErrors\.streamStalledHint'.*seconds/)
      })

      it('offers a retry action on the stall toast (v0.8.116)', () => {
        expect(
          src,
          `${label}: the stall toast must carry an action that re-sends via ` +
          `a ref to the latest send function.`,
        ).toMatch(/label:\s*t\('common\.accessibility\.retry'\)/)
      })
    })
  }
})
