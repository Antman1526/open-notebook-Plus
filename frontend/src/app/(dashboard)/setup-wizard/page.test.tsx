// v0.7.117 — covers the three terminal states of the Setup Wizard:
//   healthy   → Continue button is enabled, no Fix buttons rendered
//   degraded  → Continue button enabled, Fix buttons surface
//                   per-subsystem errors
//   not_ready → Continue button disabled
//
// The wizard renders inside AppShell, which mounts SetupBanner and the
// AppSidebar with their own hook dependencies — we stub the whole
// AppShell so this test focuses on the wizard's own logic.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import React from 'react'

import { useNotebooks } from '@/lib/hooks/use-notebooks'

vi.mock('@/components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) =>
    React.createElement('div', { 'data-testid': 'app-shell' }, children),
}))

vi.mock('@/lib/hooks/use-deep-health', () => ({
  useDeepHealth: vi.fn(),
  DEEP_HEALTH_QUERY_KEY: ['system', 'healthz', 'deep'],
}))

// v0.8.70 — the wizard now reads notebooks to detect a returning user.
// Mutable holder (mock-prefixed so vitest's hoist allows the factory ref).
const mockNotebooks: { value: unknown[] } = { value: [] }
vi.mock('@/lib/hooks/use-notebooks', () => ({
  useNotebooks: vi.fn(() => ({ data: mockNotebooks.value })),
}))

vi.mock('@/lib/features', () => ({
  isVisualSystemV2Enabled: () => process.env.NEXT_PUBLIC_DN_VISUAL_SYSTEM_V2 === '1',
}))

const pushMock = vi.fn()
const replaceMock = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: pushMock, replace: replaceMock, prefetch: vi.fn() }),
  usePathname: () => '/setup-wizard',
}))

import { useDeepHealth } from '@/lib/hooks/use-deep-health'
import SetupWizardPage from './page'

const WIZARD_COMPLETED_KEY = 'wizard_completed'

const HEALTHY = {
  status: 'healthy' as const,
  checks: {
    database: { status: 'online', ok: true, error: null },
    migrations: { status: 'applied', ok: true, error: null },
    embedding_model: { status: 'configured', ok: true, error: null },
    chat_model: { status: 'configured', ok: true, error: null },
    command_registry: { status: 'loaded', ok: true, error: null },
    worker: { status: 'online', ok: true, error: null },
  },
}

const DEGRADED = {
  status: 'degraded' as const,
  checks: {
    database: { status: 'online', ok: true, error: null },
    migrations: { status: 'applied', ok: true, error: null },
    embedding_model: {
      status: 'missing',
      ok: false,
      error: 'No default embedding model assigned. Configure one in Settings.',
    },
    chat_model: { status: 'configured', ok: true, error: null },
    command_registry: { status: 'loaded', ok: true, error: null },
    worker: { status: 'online', ok: true, error: null },
  },
}

const NOT_READY = {
  status: 'not_ready' as const,
  checks: {
    database: {
      status: 'offline',
      ok: false,
      error: 'SurrealDB unreachable at localhost:8000',
    },
    migrations: { status: 'error', ok: false, error: 'migration 14 failed' },
    embedding_model: { status: 'missing', ok: false, error: null },
    chat_model: { status: 'missing', ok: false, error: null },
    command_registry: { status: 'error', ok: false, error: null },
    worker: { status: 'offline', ok: false, error: 'No background worker heartbeat' },
  },
}

function mockDeepHealth(data: unknown, overrides: Record<string, unknown> = {}) {
  vi.mocked(useDeepHealth).mockReturnValue({
    data,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
    ...overrides,
  } as any)
}

describe('SetupWizardPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    pushMock.mockClear()
    replaceMock.mockClear()
    mockNotebooks.value = []  // default: fresh install (no notebooks)
    if (typeof window !== 'undefined') {
      window.localStorage.clear()
      document.cookie = `${WIZARD_COMPLETED_KEY}=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/`
    }
    delete process.env.NEXT_PUBLIC_DN_VISUAL_SYSTEM_V2
  })

  it('renders the healthy state with Continue enabled and no fix buttons', () => {
    mockDeepHealth(HEALTHY)
    render(<SetupWizardPage />)

    expect(screen.getByText('setupWizard.title')).toBeInTheDocument()
    expect(screen.getByText('setupWizard.statusHealthy')).toBeInTheDocument()

    const continueBtn = screen.getByTestId('continue-button')
    expect(continueBtn).not.toBeDisabled()

    expect(screen.queryAllByText('setupWizard.fixButton')).toHaveLength(0)
  })

  it('renders the degraded state with a Fix button surfacing the actionable error', () => {
    mockDeepHealth(DEGRADED)
    render(<SetupWizardPage />)

    expect(screen.getByText('setupWizard.statusDegraded')).toBeInTheDocument()
    expect(
      screen.getByTestId('subsystem-error-embedding_model'),
    ).toHaveTextContent('No default embedding model assigned')

    const fixButtons = screen.getAllByText('setupWizard.fixButton')
    expect(fixButtons.length).toBeGreaterThanOrEqual(1)

    // Embedding fix link should deep-link to the live model settings route.
    const fixLink = fixButtons[0].closest('a')
    expect(fixLink).toHaveAttribute('href', '/settings/api-keys')

    expect(screen.getByTestId('continue-button')).not.toBeDisabled()
  })

  it('renders the worker row with a copy-paste hint when the worker is offline (v0.8.127)', () => {
    vi.mocked(useDeepHealth).mockReturnValue({
      data: {
        status: 'degraded',
        checks: {
          database: { status: 'online', ok: true, error: null },
          migrations: { status: 'applied', ok: true, error: null },
          embedding_model: { status: 'configured', ok: true, error: null },
          chat_model: { status: 'configured', ok: true, error: null },
          command_registry: { status: 'loaded', ok: true, error: null },
          worker: { status: 'offline', ok: false, error: 'No background worker heartbeat in the last 180 s' },
        },
      },
      isLoading: false,
      refetch: vi.fn(),
    } as never)
    render(<SetupWizardPage />)
    expect(screen.getByTestId('subsystem-error-worker')).toHaveTextContent('No background worker heartbeat')
    // useTranslation is mocked to echo keys in this file.
    expect(screen.getByTestId('subsystem-hint-worker')).toHaveTextContent('setupWizard.fixes.worker')
  })

  it('disables Continue when status is not_ready', () => {
    mockDeepHealth(NOT_READY)
    render(<SetupWizardPage />)

    expect(screen.getByText('setupWizard.statusNotReady')).toBeInTheDocument()
    expect(screen.getByTestId('continue-button')).toBeDisabled()
  })

  it('triggers refetch when Re-check is clicked', () => {
    const refetch = vi.fn()
    mockDeepHealth(HEALTHY, { refetch })
    render(<SetupWizardPage />)

    fireEvent.click(screen.getByText('setupWizard.recheckButton'))
    expect(refetch).toHaveBeenCalled()
  })

  it('marks wizard completed and navigates home on Continue', () => {
    mockDeepHealth(HEALTHY)
    render(<SetupWizardPage />)

    fireEvent.click(screen.getByTestId('continue-button'))

    expect(window.localStorage.getItem(WIZARD_COMPLETED_KEY)).toBe('1')
    expect(document.cookie).toContain(`${WIZARD_COMPLETED_KEY}=1`)
    expect(pushMock).toHaveBeenCalledWith('/')
  })

  // v0.7.119 — When the first deep-health response comes back healthy,
  // the wizard self-dismisses via router.replace('/') and sets the
  // wizard-completed cookie / localStorage flag so middleware stops
  // redirecting on subsequent loads.
  it('auto-advances to / when status is healthy', async () => {
    mockDeepHealth(HEALTHY)
    render(<SetupWizardPage />)

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(replaceMock).toHaveBeenCalledWith('/')
    expect(window.localStorage.getItem(WIZARD_COMPLETED_KEY)).toBe('1')
    expect(document.cookie).toContain(`${WIZARD_COMPLETED_KEY}=1`)
  })

  it('does not auto-advance when status is degraded or not_ready', async () => {
    mockDeepHealth(DEGRADED)
    render(<SetupWizardPage />)

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(replaceMock).not.toHaveBeenCalled()
    expect(window.localStorage.getItem(WIZARD_COMPLETED_KEY)).toBeNull()
  })

  // v0.8.70 — a returning user (existing notebooks) whose wizard_completed
  // cookie was lost (e.g. a rebuild reset the webview cookie store) must skip
  // the first-launch wizard even when a subsystem is transiently degraded.
  it('auto-advances for a returning user (existing notebooks) when degraded', async () => {
    mockNotebooks.value = [{ id: 'notebook:abc', name: 'Existing' }]
    mockDeepHealth(DEGRADED)
    render(<SetupWizardPage />)

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(replaceMock).toHaveBeenCalledWith('/')
    expect(document.cookie).toContain(`${WIZARD_COMPLETED_KEY}=1`)
  })

  it('still shows the wizard (no auto-advance) for a fresh install when not_ready', async () => {
    mockNotebooks.value = []
    mockDeepHealth(NOT_READY)
    render(<SetupWizardPage />)

    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(replaceMock).not.toHaveBeenCalled()
  })

  it('uses the V2 setup frame without duplicating health or notebook hooks', () => {
    process.env.NEXT_PUBLIC_DN_VISUAL_SYSTEM_V2 = '1'
    mockDeepHealth(DEGRADED)
    render(<SetupWizardPage />)

    expect(screen.getByRole('main')).toHaveAttribute('data-dn-visual-system', 'v2')
    expect(screen.getAllByRole('main')).toHaveLength(1)
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(screen.getByTestId('continue-button')).toBeEnabled()
    expect(useDeepHealth).toHaveBeenCalledTimes(1)
    expect(useNotebooks).toHaveBeenCalledTimes(1)
  })

  it('marks only the V2 setup content for async geometry reservation', () => {
    process.env.NEXT_PUBLIC_DN_VISUAL_SYSTEM_V2 = '1'
    mockDeepHealth(NOT_READY)
    render(<SetupWizardPage />)

    expect(document.querySelector('.dn-workspace-setup-card-content')).toBeInTheDocument()
  })

  it('retains the SystemRouteFrame marker and setup behavior when V2 is explicitly off', () => {
    process.env.NEXT_PUBLIC_DN_VISUAL_SYSTEM_V2 = '0'
    mockDeepHealth(DEGRADED)
    render(<SetupWizardPage />)

    expect(screen.getByRole('main')).toHaveAttribute('data-dn-folio-route-frame', 'true')
    expect(screen.queryByTestId('visual-system-v2-setup')).toBeNull()
    expect(screen.getByTestId('continue-button')).toBeEnabled()
    expect(useDeepHealth).toHaveBeenCalledTimes(1)
    expect(useNotebooks).toHaveBeenCalledTimes(1)
  })
})
