import React from 'react'
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'

import { ObservabilityCard } from './ObservabilityCard'

vi.mock('@/components/ui/card', () => ({
  Card: ({ children }: { children: React.ReactNode }) => <section>{children}</section>,
  CardHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
  CardContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  CardTitle: ({ children }: { children: React.ReactNode }) => <h2>{children}</h2>,
  CardDescription: ({ children }: { children: React.ReactNode }) => <p>{children}</p>,
}))

vi.mock('@/components/common/LoadingSpinner', () => ({
  LoadingSpinner: () => <div>loading</div>,
}))

let obsData: Record<string, unknown> | undefined
let obsLoading = false
let obsError = false
const refetchMock = vi.fn()

vi.mock('@/lib/hooks/use-settings', () => ({
  useObservabilitySettings: () => ({
    data: obsData,
    isLoading: obsLoading,
    isError: obsError,
    refetch: refetchMock,
  }),
}))

let healthData: Record<string, unknown> | undefined

vi.mock('@/lib/hooks/use-deep-health', () => ({
  useDeepHealth: () => ({
    data: healthData,
  }),
}))

const COPY: Record<string, string> = {
  'settings.observability.title': 'Observability Configuration',
  'settings.observability.description': 'Read-only snapshot of the DN_* environment variables.',
  'settings.observability.slowQueryLog': 'Slow-Query Log Threshold',
  'settings.observability.slowQueryLogDesc': 'Threshold description.',
  'settings.observability.encryptionKdf': 'Encryption Key Derivation',
  'settings.observability.encryptionKdfDesc': 'KDF description.',
  'settings.observability.checkpointKeep': 'Checkpoints Retained per Thread',
  'settings.observability.checkpointKeepDesc': 'Keep description.',
  'settings.observability.checkpointInterval': 'Prune Interval',
  'settings.observability.checkpointIntervalDesc': 'Interval description.',
  'settings.observability.dbPoolSize': 'DB Connection Pool Size',
  'settings.observability.dbPoolSizeDesc': 'Pool size description.',
  'settings.observability.dbPoolDisabled': 'Connection Pool Disabled',
  'settings.observability.dbPoolDisabledDesc': 'Disabled description.',
  'settings.observability.metricsPath': 'Metrics Endpoint',
  'settings.observability.metricsPathDesc': 'Metrics description.',
  'settings.observability.viewDocs': 'View operator handbook',
  'settings.observability.loadFailed': 'Could not load observability config',
  'setupWizard.subsystems.worker': 'Background worker',
  'setupWizard.fixes.worker': 'Start the worker: uv run --env-file .env surreal-commands-worker --import-modules commands',
  'common.retry': 'Retry',
}

vi.mock('@/lib/hooks/use-translation', () => ({
  useTranslation: () => ({
    t: (key: string, fallback?: string) => COPY[key] ?? fallback ?? key,
  }),
}))

describe('ObservabilityCard', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    obsLoading = false
    obsError = false
    obsData = {
      slow_query_log_ms: 500,
      encryption_kdf: 'pbkdf2',
      checkpoint_keep_per_thread: 10,
      checkpoint_prune_interval_hours: 24,
      db_pool_size: 4,
      db_pool_disabled: false,
      metrics_endpoint_path: '/metrics',
    }
    healthData = {
      status: 'healthy',
      checks: {
        worker: {
          status: 'online',
          ok: true,
          error: null,
          active_workers: 1,
        },
      },
    }
  })

  it('renders worker status online when worker check is ok', () => {
    render(<ObservabilityCard />)

    expect(screen.getByText('Background worker')).toBeInTheDocument()
    expect(screen.getByText('online')).toBeInTheDocument()
    expect(screen.getByText('Slow-Query Log Threshold')).toBeInTheDocument()
    expect(screen.getByText('500 ms')).toBeInTheDocument()
  })

  it('renders multi-worker count when active_workers > 1', () => {
    healthData = {
      status: 'healthy',
      checks: {
        worker: {
          status: 'online',
          ok: true,
          error: null,
          active_workers: 3,
        },
      },
    }

    render(<ObservabilityCard />)

    expect(screen.getByText('online (3 workers)')).toBeInTheDocument()
  })

  it('renders worker offline and displays troubleshooting command hint when offline', () => {
    healthData = {
      status: 'degraded',
      checks: {
        worker: {
          status: 'offline',
          ok: false,
          error: 'No heartbeat',
        },
      },
    }

    render(<ObservabilityCard />)

    expect(screen.getByText('offline')).toBeInTheDocument()
    expect(
      screen.getByText(/Start the worker: uv run --env-file .env surreal-commands-worker/),
    ).toBeInTheDocument()
  })

  it('renders loading spinner when observability settings are loading', () => {
    obsLoading = true

    render(<ObservabilityCard />)

    expect(screen.getByText('loading')).toBeInTheDocument()
  })

  it('renders error state and retry button on load failure', () => {
    obsError = true

    render(<ObservabilityCard />)

    expect(screen.getByText('Could not load observability config')).toBeInTheDocument()
    expect(screen.getByText('Retry')).toBeInTheDocument()
  })
})
