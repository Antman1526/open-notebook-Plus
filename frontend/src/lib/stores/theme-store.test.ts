import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { isThemeId } from '@/lib/themes/catalog'
import { THEME_SELECTION_CHANGE_EVENT } from '@/lib/theme-storage'
import { useThemeStore } from './theme-store'

describe('legacy theme-store catalog authority', () => {
  let systemDark = false
  let mediaListeners: Array<(event: MediaQueryListEvent) => void> = []

  beforeEach(() => {
    systemDark = false
    mediaListeners = []
    localStorage.clear()
    document.documentElement.dataset.theme = ''
    document.documentElement.className = ''
    window.matchMedia = vi.fn(() => ({
      get matches() { return systemDark },
      media: '(prefers-color-scheme: dark)',
      onchange: null,
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => mediaListeners.push(listener),
      removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => {
        mediaListeners = mediaListeners.filter(candidate => candidate !== listener)
      },
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }) as MediaQueryList)
    useThemeStore.setState({ theme: 'system', legacyThemeOverride: false, appliedTheme: 'light' })
  })

  afterEach(() => {
    vi.restoreAllMocks()
    useThemeStore.setState({ theme: 'system', legacyThemeOverride: false, appliedTheme: 'light' })
    localStorage.clear()
    document.documentElement.dataset.theme = ''
    document.documentElement.className = ''
  })

  it('normalizes the direct legacy light setter to a semantic catalog theme', () => {
    useThemeStore.getState().setTheme('light')

    expect(document.documentElement.dataset.theme).toBe('light-blue')
    expect(isThemeId(document.documentElement.dataset.theme ?? '')).toBe(true)
    expect(document.documentElement).not.toHaveClass('dark')
  })

  it.each([
    { legacy: 'light' as const, selection: 'light-blue', applied: 'light-blue' },
    { legacy: 'dark' as const, selection: 'dark', applied: 'dark' },
    { legacy: 'system' as const, selection: 'system', applied: 'light-blue' },
  ])('persists the normalized $legacy selection and emits the shared event', ({ legacy, selection, applied }) => {
    const onSelectionChange = vi.fn()
    window.addEventListener(THEME_SELECTION_CHANGE_EVENT, onSelectionChange)

    useThemeStore.getState().setTheme(legacy)

    expect(localStorage.getItem('dn-theme')).toBe(selection)
    expect(localStorage.getItem('onp-theme')).toBe(selection)
    expect(document.documentElement.dataset.theme).toBe(applied)
    expect(useThemeStore.getState().legacyThemeOverride).toBe(false)
    expect(onSelectionChange).toHaveBeenCalledTimes(1)

    window.removeEventListener(THEME_SELECTION_CHANGE_EVENT, onSelectionChange)
  })

  it('maps the direct legacy system setter to the current catalog theme without owning a listener', () => {
    useThemeStore.getState().setTheme('system')
    expect(document.documentElement.dataset.theme).toBe('light-blue')
    expect(document.documentElement).not.toHaveClass('dark')
    expect(mediaListeners).toEqual([])
  })

  it('lets a new legacy setter override stale catalog storage', () => {
    localStorage.setItem('dn-theme', 'archive-paper')

    useThemeStore.getState().setTheme('light')
    expect(document.documentElement.dataset.theme).toBe('light-blue')
    expect(localStorage.getItem('dn-theme')).toBe('light-blue')

    systemDark = true
    useThemeStore.getState().setTheme('system')

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(document.documentElement).toHaveClass('dark')
    expect(localStorage.getItem('dn-theme')).toBe('system')
    expect(mediaListeners).toEqual([])
  })

  it('keeps the live palette update when canonical persistence fails', () => {
    const spy = vi.spyOn(window.localStorage, 'setItem').mockImplementation(() => {
      throw new Error('storage quota exceeded')
    })

    try {
      expect(() => useThemeStore.getState().setTheme('dark')).not.toThrow()
      expect(document.documentElement.dataset.theme).toBe('dark')
      expect(document.documentElement).toHaveClass('dark')
      expect(useThemeStore.getState().appliedTheme).toBe('dark')
      expect(useThemeStore.getState().legacyThemeOverride).toBe(true)
    } finally {
      spy.mockRestore()
    }
  })

})
