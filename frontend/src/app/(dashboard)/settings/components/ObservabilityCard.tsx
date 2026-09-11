// v0.7.136 — Read-only operator-facing observability card.
//
// Renders the GET /settings/observability snapshot (backend endpoint
// added v0.7.130) so operators can see the effective DEEPER_NOTEBOOK_* env vars
// their running process is using. Pairs with:
//   * /metrics   — Prometheus scrape target
//   * /healthz/deep — per-subsystem deep probe
//   * docs/operator/observability.md — operator handbook (linked
//     at the bottom of the card)
//
// All fields are read-only on this surface by design — operators
// change them via .env + restart, not via the API. A UI form that
// wrote to env would not survive process restart and would lull
// operators into thinking changes had stuck.

'use client'

import { ExternalLink, AlertCircle } from 'lucide-react'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useObservabilitySettings } from '@/lib/hooks/use-settings'
import { useDeepHealth } from '@/lib/hooks/use-deep-health'
import { useTranslation } from '@/lib/hooks/use-translation'
import { cn } from '@/lib/utils'

// Small helper to render an env-derived value with optional unit
// suffix. Centralized so we can keep the visual style consistent
// across the rows (mono font, slate-tone background, etc.).
function ValuePill({
  value,
  unit,
}: {
  value: string | number | boolean | null
  unit?: string
}) {
  let display: string
  if (value === null || value === undefined) {
    display = '—'
  } else if (typeof value === 'boolean') {
    display = value ? 'true' : 'false'
  } else {
    display = String(value)
  }
  if (unit && value !== null && value !== undefined) {
    display = `${display} ${unit}`
  }
  return (
    <code className="px-2 py-1 rounded-md bg-muted text-xs font-mono">
      {display}
    </code>
  )
}

interface RowProps {
  label: string
  description: string
  value?: string | number | boolean | null
  unit?: string
  /** When true, shows a yellow warning badge — used for db_pool_disabled. */
  warningWhenTrue?: boolean
  customValue?: React.ReactNode
  hint?: React.ReactNode
}

function ObservabilityRow({
  label,
  description,
  value,
  unit,
  warningWhenTrue,
  customValue,
  hint,
}: RowProps) {
  const showWarning = warningWhenTrue && value === true
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="font-medium text-sm">{label}</span>
          {showWarning ? (
            <Badge variant="destructive" className="gap-1">
              <AlertCircle className="h-3 w-3" aria-hidden />
              <span>!</span>
            </Badge>
          ) : null}
        </div>
        <p className="text-xs text-muted-foreground mt-1">{description}</p>
        {hint ? <div className="mt-2">{hint}</div> : null}
      </div>
      <div className="shrink-0">
        {customValue !== undefined ? customValue : <ValuePill value={value ?? null} unit={unit} />}
      </div>
    </div>
  )
}

export function ObservabilityCard() {
  const { t } = useTranslation()
  const { data, isLoading, isError, refetch } = useObservabilitySettings()
  const { data: deepHealthData } = useDeepHealth()
  const workerCheck = deepHealthData?.checks?.worker
  const workerStatus = workerCheck?.status ?? 'offline'
  const isWorkerOnline = workerCheck?.ok ?? (workerStatus === 'online')
  const activeWorkersCount = (workerCheck as { active_workers?: number } | undefined)?.active_workers

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.observability.title')}</CardTitle>
        <CardDescription>
          {t('settings.observability.description')}
        </CardDescription>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner />
          </div>
        ) : isError || !data ? (
          <div className="space-y-2 text-sm">
            <p className="text-destructive">
              {t('settings.observability.loadFailed')}
            </p>
            <button
              type="button"
              onClick={() => refetch()}
              className="text-primary underline text-xs"
            >
              {t('common.retry')}
            </button>
          </div>
        ) : (
          <div className="space-y-0">
            <ObservabilityRow
              label={t('setupWizard.subsystems.worker')}
              description={t('settings.observability.workerDesc')}
              customValue={
                <Badge
                  variant={isWorkerOnline ? 'default' : 'destructive'}
                  className={cn(
                    'font-mono text-xs',
                    isWorkerOnline &&
                      'bg-emerald-600 hover:bg-emerald-600 text-white dark:bg-emerald-500 dark:hover:bg-emerald-500'
                  )}
                >
                  {isWorkerOnline
                    ? activeWorkersCount && activeWorkersCount > 1
                      ? `online (${activeWorkersCount} workers)`
                      : 'online'
                    : workerStatus}
                </Badge>
              }
              hint={
                !isWorkerOnline ? (
                  <div className="rounded-md bg-muted/70 p-2 text-xs font-mono text-muted-foreground break-all">
                    {t('setupWizard.fixes.worker')}
                  </div>
                ) : null
              }
            />
            <ObservabilityRow
              label={t('settings.observability.slowQueryLog')}
              description={t('settings.observability.slowQueryLogDesc')}
              value={
                data.slow_query_log_ms === null
                  ? t('settings.observability.disabled')
                  : data.slow_query_log_ms
              }
              unit={data.slow_query_log_ms === null ? undefined : 'ms'}
            />
            <ObservabilityRow
              label={t('settings.observability.encryptionKdf')}
              description={t('settings.observability.encryptionKdfDesc')}
              value={data.encryption_kdf}
            />
            <ObservabilityRow
              label={t('settings.observability.checkpointKeep')}
              description={t('settings.observability.checkpointKeepDesc')}
              value={data.checkpoint_keep_per_thread}
            />
            <ObservabilityRow
              label={t('settings.observability.checkpointInterval')}
              description={t('settings.observability.checkpointIntervalDesc')}
              value={data.checkpoint_prune_interval_hours}
              unit="h"
            />
            <ObservabilityRow
              label={t('settings.observability.dbPoolSize')}
              description={t('settings.observability.dbPoolSizeDesc')}
              value={data.db_pool_size}
            />
            <ObservabilityRow
              label={t('settings.observability.dbPoolDisabled')}
              description={t('settings.observability.dbPoolDisabledDesc')}
              value={data.db_pool_disabled}
              warningWhenTrue
            />
            <ObservabilityRow
              label={t('settings.observability.metricsPath')}
              description={t('settings.observability.metricsPathDesc')}
              value={data.metrics_endpoint_path}
            />
          </div>
        )}

        {/* Doc-link footer. The handbook is the source of truth for what
            each value MEANS; the card just shows what's currently set.
            External-link icon signals "this opens the docs in a new tab"
            convention used elsewhere in the app. */}
        <div className="pt-4 mt-2 border-t">
          <a
            href="https://github.com/Antman1526/Deeper-Notebook/blob/main/docs/operator/observability.md"
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 text-xs text-primary underline"
          >
            <ExternalLink className="h-3 w-3" aria-hidden />
            <span>{t('settings.observability.viewDocs')}</span>
          </a>
        </div>
      </CardContent>
    </Card>
  )
}
