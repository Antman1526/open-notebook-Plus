'use client'

// v0.8.125 — Settings card for the Studio artifact retention job
// (deeper_notebook/studio/retention.py). Read-only status + a zero-mutation
// "Dry run now" action so an operator can see what a real run would touch
// before opting in via DEEPER_NOTEBOOK_STUDIO_RETENTION_INTERVAL_HOURS. The
// job itself stays env-gated — nothing on this card can enable it.
import { useState } from 'react'
import { PlayCircle } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useRetentionDryRun, useRetentionStatus } from '@/lib/hooks/use-studio'
import type { StudioRetentionDryRunResponse } from '@/lib/api/studio'

function formatBytes(bytes: number): string {
  if (bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const exponent = Math.min(
    units.length - 1,
    Math.floor(Math.log(bytes) / Math.log(1024)),
  )
  const value = bytes / 1024 ** exponent
  return `${exponent === 0 ? value : value.toFixed(1)} ${units[exponent]}`
}

export function RetentionCard() {
  const { t } = useTranslation()
  const { data, isLoading, isError } = useRetentionStatus()
  const dryRun = useRetentionDryRun()
  const [lastDryRunResult, setLastDryRunResult] =
    useState<StudioRetentionDryRunResponse | null>(null)

  const handleDryRun = () => {
    dryRun.mutate(undefined, {
      onSuccess: (result) => setLastDryRunResult(result),
    })
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('settings.retention.title')}</CardTitle>
        <CardDescription>{t('settings.retention.description')}</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex justify-center py-6">
            <LoadingSpinner />
          </div>
        ) : isError || !data ? (
          <p className="text-sm text-destructive">
            {t('settings.observability.loadFailed')}
          </p>
        ) : (
          <div className="space-y-2 text-sm">
            <p className="font-medium">
              {data.enabled
                ? t('settings.retention.enabled')
                : t('settings.retention.disabled')}
            </p>
            <p className="text-muted-foreground">
              {t('settings.retention.intervalHours').replace(
                '{hours}',
                String(data.interval_hours),
              )}
            </p>
            <p className="text-muted-foreground">
              {t('settings.retention.keepRevisions').replace(
                '{count}',
                String(data.revision_keep_per_artifact),
              )}
            </p>
            <p className="text-muted-foreground">
              {t('settings.retention.staleDays').replace(
                '{days}',
                String(data.stale_export_max_age_days),
              )}
            </p>
            <p className="text-muted-foreground">
              {data.last_run_at
                ? t('settings.retention.lastRun').replace(
                    '{when}',
                    new Date(data.last_run_at).toLocaleString(),
                  )
                : t('settings.retention.neverRun')}
            </p>
            {!data.enabled ? (
              <p className="text-xs text-muted-foreground">
                {t('settings.retention.enableHint')}
              </p>
            ) : null}
          </div>
        )}

        <div className="flex flex-col gap-3">
          <div>
            <Button
              variant="outline"
              size="sm"
              onClick={handleDryRun}
              disabled={dryRun.isPending}
            >
              <PlayCircle className="h-4 w-4" />
              {dryRun.isPending
                ? t('settings.retention.dryRunning')
                : t('settings.retention.dryRun')}
            </Button>
          </div>
          {lastDryRunResult ? (
            <div className="space-y-1 text-sm text-primary">
              <p>
                {t('settings.retention.wouldRemoveRevisions').replace(
                  '{count}',
                  String(lastDryRunResult.revisions_deleted),
                )}
              </p>
              <p>
                {t('settings.retention.wouldRemoveExports')
                  .replace('{count}', String(lastDryRunResult.exports_removed))
                  .replace('{bytes}', formatBytes(lastDryRunResult.bytes_reclaimed))}
              </p>
            </div>
          ) : null}
        </div>
      </CardContent>
    </Card>
  )
}
