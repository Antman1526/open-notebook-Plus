// v0.8.119 — Placeholder parity across locales.
//
// The app uses two interpolation styles side by side: i18next's `{{name}}`
// (resolved by t(key, { name })) and a single-brace `{name}` that callers
// substitute with .replace(). Both are established across many namespaces,
// so neither is being migrated. What can go wrong in a mixed convention is
// a translation dropping, renaming, or re-bracing a placeholder, which the
// key-parity test cannot see. This test requires every locale string to
// carry exactly the placeholders its en-US counterpart carries, in the same
// brace style.

import { describe, expect, it } from 'vitest'
import { resources } from './index'
import { enUS } from './en-US'

type Tree = Record<string, unknown>

function flatten(obj: Tree, prefix = '', out: Record<string, string> = {}): Record<string, string> {
  for (const [key, value] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (typeof value === 'string') out[path] = value
    else if (typeof value === 'object' && value !== null && !Array.isArray(value)) flatten(value as Tree, path, out)
  }
  return out
}

/** Placeholders as they appear, e.g. "{{count}}" or "{count}", sorted. */
function placeholders(text: string): string[] {
  const found = text.match(/\{\{[A-Za-z0-9_]+\}\}|(?<!\{)\{[A-Za-z0-9_]+\}(?!\})/g) ?? []
  return [...found].sort()
}

describe('Locale placeholder parity', () => {
  const en = flatten(enUS as Tree)
  const keysWithPlaceholders = Object.entries(en).filter(([, text]) => placeholders(text).length > 0)

  it('en-US has placeholder-bearing strings to compare against', () => {
    expect(keysWithPlaceholders.length).toBeGreaterThan(20)
  })

  const locales = Object.entries(resources).filter(([code]) => code !== 'en-US')

  it.each(locales.map(([code, resource]) => [code, resource] as const))(
    '%s carries the same placeholders as en-US, in the same brace style',
    (code, resource) => {
      const local = flatten(resource.translation as Tree)
      const drift: string[] = []
      for (const [key, text] of keysWithPlaceholders) {
        const expected = placeholders(text)
        const actual = placeholders(local[key] ?? '')
        if (expected.join('|') !== actual.join('|')) {
          drift.push(`${key}: expected ${expected.join(' ')} got ${actual.join(' ') || '(none)'}`)
        }
      }
      expect(drift, `Placeholder drift in ${code}:\n${drift.join('\n')}`).toEqual([])
    },
  )
})
