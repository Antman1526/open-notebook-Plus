// v0.8.118 — Every literal translation key used in the app must exist in
// en-US. A key that does not resolve renders as the raw key string at
// runtime (react-i18next returns the key on a miss), which is exactly how
// the v0.8.116 retry button briefly shipped as "common.accessibility.retry".
// Locale parity (index.test.ts) already guarantees the other 13 locales
// carry whatever en-US carries, so checking en-US is sufficient.

import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'fs'
import { join, relative } from 'path'
import { enUS } from './en-US'

const SRC_ROOT = join(__dirname, '..', '..')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      if (entry === 'node_modules' || entry === 'locales' || entry === 'test') continue
      walk(full, out)
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry) && !/\.d\.ts$/.test(entry)) {
      out.push(full)
    }
  }
  return out
}

function resolveKey(key: string): boolean {
  let node: unknown = enUS
  for (const part of key.split('.')) {
    if (typeof node !== 'object' || node === null || !(part in (node as Record<string, unknown>))) {
      return false
    }
    node = (node as Record<string, unknown>)[part]
  }
  return typeof node === 'string'
}

// Matches t('a.b.c') and t("a.b.c"). Only dotted literals are checked:
// single-segment keys and dynamic keys (template strings, variables) are out
// of scope for a static check. Calls that supply a fallback — a string
// second argument or an options object with `defaultValue` — are accepted:
// a missing key there renders the fallback (English only, but never the raw
// key), which is a translation gap for locale parity to worry about, not a
// runtime bug.
const LITERAL_CALL = /\bt\(\s*(['"])([A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+)\1\s*([,)])/g

function hasFallback(text: string, afterIndex: number): boolean {
  const tail = text.slice(afterIndex, afterIndex + 400)
  if (/^\s*['"`]/.test(tail)) return true
  // Look for defaultValue before the call's closing paren (approximate:
  // stop at the first `)` that is not inside braces).
  let depth = 0
  for (let i = 0; i < tail.length; i += 1) {
    const ch = tail[i]
    if (ch === '{' || ch === '(') depth += 1
    else if (ch === '}' ) depth -= 1
    else if (ch === ')') {
      if (depth === 0) return tail.slice(0, i).includes('defaultValue')
      depth -= 1
    }
  }
  return tail.includes('defaultValue')
}

describe('translation keys referenced in source exist in en-US', () => {
  const files = walk(SRC_ROOT)

  it('scans a meaningful number of source files', () => {
    expect(files.length).toBeGreaterThan(200)
  })

  it('finds no literal t() key missing from en-US without a fallback', () => {
    const missing: string[] = []
    for (const file of files) {
      const text = readFileSync(file, 'utf-8')
      for (const match of text.matchAll(LITERAL_CALL)) {
        const key = match[2]
        if (resolveKey(key)) continue
        const afterKey = (match.index ?? 0) + match[0].length
        if (match[3] === ',' && hasFallback(text, afterKey)) continue
        missing.push(`${relative(SRC_ROOT, file)} → ${key}`)
      }
    }
    expect(missing, `Missing translation keys:\n${missing.join('\n')}`).toEqual([])
  })
})
