// v0.7.117 — first-launch Setup Wizard.
//
// Renders a traffic-light view of /healthz/deep so the user can spot
// the broken subsystem and jump straight to the page that fixes it.
// "Continue anyway" is enabled when overall status is healthy or
// degraded (the API can still serve a notebook UI), and disabled on
// not_ready (DB/migrations down — nothing useful renders past this
// gate). Continuing sets a localStorage + cookie flag so the
// middleware stops redirecting on subsequent loads.

'use client'

import { useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import Link from 'next/link'
import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  RefreshCw,
  ArrowRight,
  Loader2,
} from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useDeepHealth } from '@/lib/hooks/use-deep-health'
import { useNotebooks } from '@/lib/hooks/use-notebooks'
import type { SubsystemKey, SubsystemCheck } from '@/lib/api/health'
import { SystemRouteFrame } from '@/components/deeper-notebook/route-frames/SystemRouteFrames'
import { WorkspacePage } from '@/components/deeper-notebook/workspace/WorkspacePage'
import { isVisualSystemV2Enabled } from '@/lib/features'

const SUBSYSTEM_ORDER: SubsystemKey[] = [
  'database',
  'migrations',
  'embedding_model',
  'chat_model',
  'command_registry',
  'worker',
]

// Where each fixable subsystem should send the user. database +
// migrations have no in-app fix — they're shown with copy-paste
// instructions instead of a deep-link button.
const FIX_PATHS: Partial<Record<SubsystemKey, string>> = {
  embedding_model: '/settings/api-keys',
  chat_model: '/settings/api-keys',
  command_registry: '/advanced',
}

// v0.7.117 — explicit map of subsystem → i18n key. We can't compute
// the key dynamically (`t(\`setupWizard.subsystems.${name}\`)`)
// because the locale-parity test scans the source for literal key
// references; the dynamic form would leave the keys orphaned and the
// "unused key" detection would scream.
//
const SUBSYSTEM_LABEL_KEYS: Record<SubsystemKey, string> = {
  database: 'setupWizard.subsystems.database',
  migrations: 'setupWizard.subsystems.migrations',
  embedding_model: 'setupWizard.subsystems.embedding_model',
  chat_model: 'setupWizard.subsystems.chat_model',
  command_registry: 'setupWizard.subsystems.command_registry',
  worker: 'setupWizard.subsystems.worker',
}

// v0.8.127 — subsystems with no in-app fix page get a copy-paste hint
// rendered under the row instead of a deep-link button.
const FIX_HINT_KEYS: Partial<Record<SubsystemKey, string>> = {
  worker: 'setupWizard.fixes.worker',
}

const WIZARD_COMPLETED_KEY = 'wizard_completed'

function markWizardCompleted() {
  if (typeof window === 'undefined') return
  try {
    window.localStorage.setItem(WIZARD_COMPLETED_KEY, '1')
  } catch {
    // ignore quota / private-mode errors
  }
  // Cookie is what middleware reads (localStorage isn't visible on the
  // edge). 1-year expiry; cleared if the user wipes site data.
  // v0.7.121 — Cookie security flags:
  //   SameSite=Strict — the cookie carries no security-sensitive
  //     value (it's just "user has finished the wizard"), but Strict
  //     prevents cross-site requests from triggering the redirect-
  //     loop bypass any future feature might add to the wizard path.
  //   Secure — only sent over HTTPS. Skipped on localhost / dev
  //     because browsers reject Secure cookies on http://localhost
  //     and the wizard would loop forever in dev. Detect via
  //     window.location.protocol.
  //   HttpOnly is NOT set — can't be, because we set the cookie from
  //     JS (document.cookie doesn't support HttpOnly).
  const secureFlag = window.location.protocol === 'https:' ? '; Secure' : ''
  document.cookie = (
    `${WIZARD_COMPLETED_KEY}=1; path=/; ` +
    `max-age=${60 * 60 * 24 * 365}; ` +
    `SameSite=Strict${secureFlag}`
  )
}

function StatusIcon({ ok, status }: { ok: boolean; status: string }) {
  if (ok) return <CheckCircle2 className="h-5 w-5 text-success" aria-hidden />
  if (status === 'pending' || status === 'missing')
    return <AlertTriangle className="h-5 w-5 text-warning" aria-hidden />
  return <XCircle className="h-5 w-5 text-destructive" aria-hidden />
}

function SubsystemRow({
  name,
  check,
  label,
  fixPath,
  fixLabel,
  fixHint,
}: {
  name: SubsystemKey
  check: SubsystemCheck
  label: string
  fixPath?: string
  fixLabel: string
  fixHint?: string
}) {
  return (
    <div className="flex items-start justify-between gap-4 py-3 border-b last:border-b-0">
      <div className="flex items-start gap-3 min-w-0 flex-1">
        <StatusIcon ok={check.ok} status={check.status} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium">{label}</span>
            <Badge variant={check.ok ? 'secondary' : 'destructive'}>
              {check.status}
            </Badge>
          </div>
          {check.error ? (
            <p
              className="text-sm text-muted-foreground mt-1 break-words"
              data-testid={`subsystem-error-${name}`}
            >
              {check.error}
            </p>
          ) : null}
          {!check.ok && fixHint ? (
            <p
              className="text-xs text-muted-foreground mt-1 font-mono break-all"
              data-testid={`subsystem-hint-${name}`}
            >
              {fixHint}
            </p>
          ) : null}
        </div>
      </div>
      {!check.ok && fixPath ? (
        <Button variant="outline" size="sm" asChild className="shrink-0">
          <Link href={fixPath}>
            {fixLabel}
            <ArrowRight className="ml-2 h-4 w-4" />
          </Link>
        </Button>
      ) : null}
    </div>
  )
}

export default function SetupWizardPage() {
  const { t } = useTranslation()
  const router = useRouter()
  const { data, isLoading, refetch, isFetching } = useDeepHealth()

  const overallStatus = data?.status ?? 'not_ready'
  const canContinue = overallStatus === 'healthy' || overallStatus === 'degraded'

  // v0.8.70 — returning-user signal. Existing notebooks mean this is NOT a
  // fresh install, so the first-launch wizard should be skipped even when the
  // wizard_completed cookie is missing (see the auto-advance effect below).
  const { data: notebooks } = useNotebooks()
  const hasExistingNotebooks = (notebooks?.length ?? 0) > 0

  // v0.7.119 — Auto-advance: when the first /healthz/deep response comes
  // back healthy, skip the wizard entirely. Guard with a ref so we only
  // fire once per mount — otherwise a user who manually navigates back
  // to /setup-wizard would be redirected away again before they could
  // re-check anything.
  //
  // v0.8.70 — ALSO auto-skip for a returning user (existing notebooks) whenever
  // the backend is reachable (healthy OR degraded), not only on a perfectly
  // healthy first launch. The wizard_completed cookie lives in the webview's
  // cookie store, which macOS recreates from scratch whenever the app's ad-hoc
  // code-signing identity changes — which happens on every rebuild. Gating
  // solely on the cookie therefore forced returning users back through the
  // wizard after each new build, often stuck behind a transiently `degraded`
  // subsystem during startup. Genuine first-launch users (no notebooks) still
  // see the guided wizard.
  const autoAdvancedRef = useRef(false)
  useEffect(() => {
    if (autoAdvancedRef.current) return
    if (!data) return
    const reachable = data.status === 'healthy' || data.status === 'degraded'
    const shouldSkip =
      data.status === 'healthy' || (reachable && hasExistingNotebooks)
    if (!shouldSkip) return
    autoAdvancedRef.current = true
    markWizardCompleted()
    router.replace('/')
  }, [data, hasExistingNotebooks, router])

  const handleContinue = () => {
    markWizardCompleted()
    router.push('/')
  }

  const overallLabel =
    overallStatus === 'healthy'
      ? t('setupWizard.statusHealthy')
      : overallStatus === 'degraded'
        ? t('setupWizard.statusDegraded')
        : t('setupWizard.statusNotReady')

  const wizardContent = (
        <div className="mx-auto max-w-3xl space-y-6 rounded-lg bg-[var(--dn-folio-paper)] p-4 sm:p-6">

          <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div>
                <CardTitle className="flex items-center gap-2">
                  {overallStatus === 'healthy' ? (
                    <CheckCircle2 className="h-5 w-5 text-success" aria-hidden />
                  ) : overallStatus === 'degraded' ? (
                    <AlertTriangle className="h-5 w-5 text-warning" aria-hidden />
                  ) : (
                    <XCircle className="h-5 w-5 text-destructive" aria-hidden />
                  )}
                  {overallLabel}
                </CardTitle>
                <CardDescription>
                  {t('setupWizard.subtitle')}
                </CardDescription>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => refetch()}
                disabled={isFetching}
              >
                {isFetching ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                <span className="ml-2">{t('setupWizard.recheckButton')}</span>
              </Button>
            </CardHeader>
            <CardContent
              className={
                isVisualSystemV2Enabled()
                  ? 'dn-workspace-setup-card-content'
                  : undefined
              }
            >
              {isLoading || !data ? (
                <div className="py-6 text-center text-muted-foreground">
                  {t('common.loading')}
                </div>
              ) : (
                <div data-testid="subsystem-list">
                  {SUBSYSTEM_ORDER.map((name) => (
                    <SubsystemRow
                      key={name}
                      name={name}
                      check={data.checks[name]}
                      label={t(SUBSYSTEM_LABEL_KEYS[name])}
                      fixPath={FIX_PATHS[name]}
                      fixHint={FIX_HINT_KEYS[name] ? t(FIX_HINT_KEYS[name]) : undefined}
                      fixLabel={t('setupWizard.fixButton')}
                    />
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* v0.7.164 — Setup Wizard primary CTA prominence (visual
              audit item #6). The wizard is the highest-stakes first-
              launch screen in the app — the Continue button used to
              be a default-size right-aligned button with no visible
              border separation from the subsystem card above, fighting
              for attention with secondary actions. Promoted to
              `size="lg"` with a hairline top divider + pt-4 so the
              action bar reads as a footer (clear "I'm done here →
              advance" affordance). */}
          <div className="flex items-center justify-end gap-3 border-t pt-4">
            <Button
              size="lg"
              onClick={handleContinue}
              disabled={!canContinue}
              data-testid="continue-button"
            >
              {t('setupWizard.continueButton')}
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          </div>
        </div>
  )

  return (
    <AppShell>
      {isVisualSystemV2Enabled() ? (
        <WorkspacePage
          title={t('setupWizard.title')}
          eyebrow="Setup"
          description={t('setupWizard.subtitle')}
          data-testid="visual-system-v2-setup"
          data-dn-visual-system="v2"
        >
          {wizardContent}
        </WorkspacePage>
      ) : (
        <SystemRouteFrame
          route="/setup-wizard"
          title={t('setupWizard.title')}
          description={t('setupWizard.subtitle')}
        >
          {wizardContent}
        </SystemRouteFrame>
      )}
    </AppShell>
  )
}
