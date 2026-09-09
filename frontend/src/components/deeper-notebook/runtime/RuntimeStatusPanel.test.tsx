import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { RuntimeStatusPanel } from './RuntimeStatusPanel'

const readySnapshot = {
  schema_version: 'runtime-snapshot-v1',
  status: 'ready',
  reasons: [],
  readiness: { state: 'ready', database: 'online', migrations: 'applied' },
  startup: { state: 'ready', stages: [] },
  updates: { state: 'ready', enabled: true, update_available: false, current_version: '0.8.70' },
  vault: { state: 'ready', ready: 2, degraded: 0, unavailable: 0 },
  knowledge: { state: 'ready', projected: 2, unchanged: 1, failed: 0 },
  backup: { state: 'ready', file_count: 1, newest_age_seconds: 30 },
}

describe('RuntimeStatusPanel', () => {
  it.each([
    ['ready', 'Ready'],
    ['degraded', 'Degraded'],
    ['unknown', 'Unknown'],
  ] as const)('renders the %s overall state', (status, label) => {
    render(
      <RuntimeStatusPanel
        snapshot={{ ...readySnapshot, status }}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByRole(status === 'degraded' ? 'alert' : 'status')).toHaveTextContent(label)
  })

  it('uses redacted reason labels and never renders raw provider details', () => {
    render(
      <RuntimeStatusPanel
        snapshot={{
          ...readySnapshot,
          status: 'degraded',
          reasons: ['database_offline', 'migrations_pending', 'database_check_failed'],
          readiness: { state: 'degraded', database: 'offline', migrations: 'pending' },
        }}
      />,
    )

    expect(screen.getByText('Database is offline')).toBeInTheDocument()
    expect(screen.getByText('Migrations are pending')).toBeInTheDocument()
    expect(screen.getByText('Database status is unavailable')).toBeInTheDocument()
    expect(screen.queryByText('database_offline')).not.toBeInTheDocument()
    expect(screen.queryByText(/Users|private|canary|exception|token/i)).not.toBeInTheDocument()
  })

  it('provides a keyboard-reachable manual refresh without creating actions on mount', () => {
    const onRefresh = vi.fn()
    render(<RuntimeStatusPanel snapshot={readySnapshot} onRefresh={onRefresh} />)

    const refresh = screen.getByRole('button', { name: 'Refresh runtime status' })
    expect(refresh).toBeEnabled()
    expect(onRefresh).not.toHaveBeenCalled()
    fireEvent.click(refresh)
    expect(onRefresh).toHaveBeenCalledOnce()
  })
})

// v0.8.86 — Phase 2B startup measurement: stage timings render in the panel.
describe('startup stage timings', () => {
  it('shows slow stages with human-readable durations', () => {
    render(
      <RuntimeStatusPanel
        snapshot={{
          ...readySnapshot,
          startup: {
            state: 'ready',
            stages: [
              { stage: 'chat_model_scan', elapsed_ms: 2195 },
              { stage: 'core_ready', elapsed_ms: 97398 },
              { stage: 'launcher_start', elapsed_ms: 0 },
            ],
          },
        }}
        onRefresh={vi.fn()}
      />,
    )

    expect(screen.getByText('chat model scan')).toBeInTheDocument()
    expect(screen.getByText('2.2s')).toBeInTheDocument()
    expect(screen.getByText('core ready')).toBeInTheDocument()
    expect(screen.getByText('97.4s')).toBeInTheDocument()
    // sub-100ms noise stays hidden
    expect(screen.queryByText('launcher start')).toBeNull()
  })

  it('renders no stage rows when the receipt has none', () => {
    render(<RuntimeStatusPanel snapshot={readySnapshot} onRefresh={vi.fn()} />)
    expect(screen.queryByText(/core ready/)).toBeNull()
  })
})

// v0.8.104 — a broken model configuration has to reach the person who can fix
// it. get_default_model already logged "the configured model_id may have been
// deleted or misconfigured" long before this existed; that log went to a file
// nobody opens while the UI just failed to answer. These assert the detail and
// the remedy render verbatim, not merely that some warning appeared.
describe('RuntimeStatusPanel model configuration issues', () => {
  const issueSnapshot = {
    ...readySnapshot,
    status: 'degraded' as const,
    reasons: ['model_config_degraded'] as const,
    model_config_health: {
      state: 'degraded' as const,
      issues: [
        {
          code: 'chat_default_dangling',
          detail: 'The configured chat model (model:gone) no longer exists.',
          remedy: 'Settings → Models → pick a different default chat model.',
        },
      ],
    },
  }

  it('renders the specific detail and its remedy', () => {
    render(<RuntimeStatusPanel snapshot={issueSnapshot} />)

    expect(
      screen.getByText('The configured chat model (model:gone) no longer exists.'),
    ).toBeInTheDocument()
    expect(
      screen.getByText('Settings → Models → pick a different default chat model.'),
    ).toBeInTheDocument()
  })

  it('omits the block entirely when configuration is healthy', () => {
    render(<RuntimeStatusPanel snapshot={readySnapshot} />)

    expect(screen.queryByTestId('runtime-model-config-issues')).toBeNull()
  })

  it('parses a pre-v0.8.104 backend that omits the section', () => {
    // The field is optional on purpose: an older backend must still yield a
    // usable snapshot rather than failing validation into UNKNOWN.
    const withoutSection = { ...issueSnapshot }
    delete (withoutSection as { model_config_health?: unknown }).model_config_health
    render(<RuntimeStatusPanel snapshot={withoutSection} />)

    expect(screen.queryByTestId('runtime-model-config-issues')).toBeNull()
    expect(screen.getByTestId('runtime-status-panel')).toBeInTheDocument()
  })
})
