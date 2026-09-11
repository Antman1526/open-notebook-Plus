import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { fireEvent, render, screen } from '@testing-library/react'

import { RetentionCard } from './RetentionCard'

vi.mock('@/components/ui/card', () => ({
  Card: ({ children }: { children: React.ReactNode }) => <section>{children}</section>,
  CardHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  CardContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CardTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  CardDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}))

vi.mock('@/components/ui/button', () => ({
  Button: ({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) => (
    <button {...props}>{children}</button>
  ),
}))

vi.mock('@/components/common/LoadingSpinner', () => ({
  LoadingSpinner: () => <div>loading</div>,
}))

vi.mock('lucide-react', () => ({
  PlayCircle: () => null,
}))

// The real en-US copy for settings.retention.* — kept in sync with
// frontend/src/lib/locales/en-US/index.ts so this test exercises the same
// placeholder substitution the real card performs.
const RETENTION_COPY: Record<string, string> = {
  'settings.retention.title': 'Studio Retention',
  'settings.retention.description':
    'See what the Studio artifact retention job would clean up, without enabling it.',
  'settings.retention.enabled': 'Enabled',
  'settings.retention.disabled': 'Disabled',
  'settings.retention.intervalHours': 'Every {hours} h',
  'settings.retention.keepRevisions': 'Keep {count} revisions per artifact',
  'settings.retention.staleDays': 'Remove stale exports older than {days} days',
  'settings.retention.lastRun': 'Last run {when}',
  'settings.retention.neverRun': 'Never run',
  'settings.retention.dryRun': 'Dry run now',
  'settings.retention.dryRunning': 'Running dry run…',
  'settings.retention.wouldRemoveRevisions': '{count} revisions would be removed',
  'settings.retention.wouldRemoveExports': '{count} export files would be removed ({bytes})',
  'settings.retention.enableHint':
    'Set DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS to enable',
  'settings.observability.loadFailed': 'Could not load observability config',
}

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string) => RETENTION_COPY[key] ?? key,
  }),
}))

let statusData: Record<string, unknown> | undefined
let statusLoading = false
let statusError = false
const dryRunMutate = vi.fn()
let dryRunPending = false

vi.mock('@/lib/hooks/use-studio', () => ({
  useRetentionStatus: () => ({
    data: statusData,
    isLoading: statusLoading,
    isError: statusError,
  }),
  useRetentionDryRun: () => ({
    mutate: dryRunMutate,
    isPending: dryRunPending,
  }),
}))

beforeEach(() => {
  statusData = undefined
  statusLoading = false
  statusError = false
  dryRunPending = false
  dryRunMutate.mockReset()
})

describe('RetentionCard', () => {
  it('renders the disabled state from a mocked status', () => {
    statusData = {
      enabled: false,
      interval_hours: 0,
      revision_keep_per_artifact: 10,
      stale_export_max_age_days: 30,
      dry_run_default: false,
      last_run_at: null,
      last_report: null,
    }

    render(<RetentionCard />)

    expect(screen.getByText('Studio Retention')).toBeInTheDocument()
    expect(screen.getByText('Disabled')).toBeInTheDocument()
    expect(screen.getByText('Every 0 h')).toBeInTheDocument()
    expect(screen.getByText('Keep 10 revisions per artifact')).toBeInTheDocument()
    expect(screen.getByText('Remove stale exports older than 30 days')).toBeInTheDocument()
    expect(screen.getByText('Never run')).toBeInTheDocument()
    expect(
      screen.getByText('Set DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS to enable'),
    ).toBeInTheDocument()
  })

  it('renders enabled status with last run time and no enable hint', () => {
    statusData = {
      enabled: true,
      interval_hours: 6,
      revision_keep_per_artifact: 10,
      stale_export_max_age_days: 30,
      dry_run_default: false,
      last_run_at: '2026-09-10T12:00:00Z',
      last_report: null,
    }

    render(<RetentionCard />)

    expect(screen.getByText('Enabled')).toBeInTheDocument()
    expect(screen.getByText(/^Last run /)).toBeInTheDocument()
    expect(
      screen.queryByText('Set DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS to enable'),
    ).not.toBeInTheDocument()
  })

  it('clicking dry run calls the mutation and renders the returned counters', () => {
    statusData = {
      enabled: false,
      interval_hours: 0,
      revision_keep_per_artifact: 10,
      stale_export_max_age_days: 30,
      dry_run_default: false,
      last_run_at: null,
      last_report: null,
    }
    dryRunMutate.mockImplementation((_vars, options) => {
      options?.onSuccess?.({
        revisions_examined: 12,
        revisions_deleted: 2,
        exports_examined: 5,
        exports_removed: 3,
        bytes_reclaimed: 4096,
        dry_run: true,
        revision_keep_per_artifact: 10,
        stale_export_max_age_days: 30,
      })
    })

    render(<RetentionCard />)
    fireEvent.click(screen.getByText('Dry run now'))

    expect(dryRunMutate).toHaveBeenCalled()
    expect(screen.getByText('2 revisions would be removed')).toBeInTheDocument()
    expect(screen.getByText('3 export files would be removed (4.0 KB)')).toBeInTheDocument()
  })
})
