'use client'

import { AppShell } from '@/components/layout/AppShell'
import { SettingsForm } from './components/SettingsForm'
// v0.7.136 — Read-only ObservabilityCard renders the GET /settings/observability
// snapshot below the writable settings form. The two surfaces are
// intentionally separated because the env-derived values aren't
// user-mutable from this UI.
import { ObservabilityCard } from './components/ObservabilityCard'
import { UpdatesCard } from './components/UpdatesCard'
// v0.8.125 — Studio retention status + dry-run visibility card.
import { RetentionCard } from './components/RetentionCard'
import { useSettings } from '@/lib/hooks/use-settings'
import { Button } from '@/components/ui/button'
import { RefreshCw, Sparkles } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
// v0.8.70 — replay the launch "Aurora Reveal" intro on demand.
import { replayIntro } from '@/components/intro/IntroReveal'
import { ThemeGallery } from '@/components/deeper-notebook'
import { DisplayPreferencesPanel } from '@/components/deeper-notebook/DisplayPreferencesPanel'
import { useGuidedTipsStore } from '@/lib/stores/guided-tips-store'
import { SystemRouteFrame } from '@/components/deeper-notebook/route-frames/SystemRouteFrames'
import { RuntimeStatusPanel } from '@/components/deeper-notebook/runtime/RuntimeStatusPanel'
import { BackupProvenancePanel } from '@/components/deeper-notebook/runtime/BackupProvenancePanel'
import { UNKNOWN_RUNTIME_SNAPSHOT } from '@/lib/api/runtime'
import { useRuntimeSnapshot } from '@/lib/hooks/use-runtime-snapshot'

export default function SettingsPage() {
  const { t } = useTranslation()
  const { refetch } = useSettings()
  const tipsEnabled = useGuidedTipsStore((state) => state.enabled)
  const setTipsEnabled = useGuidedTipsStore((state) => state.setEnabled)
  const replayAllTips = useGuidedTipsStore((state) => state.replayAll)
  const runtime = useRuntimeSnapshot()

  // v0.7.153 — Visual rhythm refresh (Settings = roomy treatment).
  // Pain points addressed (per user 2026-05-21):
  //   - Inputs/labels stacked too tightly  → bumped space-y-6 → space-y-10
  //     between top-level sections; page padding p-6 → px-6 py-10 sm:px-8
  //   - Section headings don't separate cleanly → header gets its own
  //     space-y-2 stack with the refresh button moved to a header bar
  //     on the right (visual right-alignment makes the section break
  //     read at a glance)
  //   - Buttons buried → refresh becomes a labeled "Refresh" outline
  //     button (icon + label) instead of an icon-only square
  // Container width: max-w-4xl (896px) → max-w-3xl (768px) feels less
  // sparse on wide monitors while still hitting the "roomy" target.
  return (
    <AppShell>
      <SystemRouteFrame
        route="/settings"
        actions={<Button variant="outline" size="sm" aria-label="Refresh settings" onClick={() => refetch()}><RefreshCw className="h-4 w-4" /></Button>}
      >
        <div data-testid="settings-scroll-viewport" className="mx-auto max-w-3xl space-y-10 rounded-lg bg-[var(--dn-folio-paper)] p-4 sm:p-6">

            <section aria-labelledby="appearance-heading" className="space-y-4">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Appearance</p>
                <h2 id="appearance-heading" className="mt-1 text-xl font-semibold">Choose your research environment</h2>
                <p className="mt-1 text-sm text-muted-foreground">Preview a complete workspace theme, then apply it when it feels right.</p>
              </div>
              <ThemeGallery />
              <DisplayPreferencesPanel />
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3">
                <div>
                  <p className="text-sm font-medium">Guided tips</p>
                  <p className="text-sm text-muted-foreground">Show small contextual messages when you visit a section for the first time.</p>
                </div>
                <div className="flex gap-2">
                  <Button type="button" variant="outline" role="switch" aria-checked={tipsEnabled} onClick={() => setTipsEnabled(!tipsEnabled)}>
                    {tipsEnabled ? 'On' : 'Off'}
                  </Button>
                  <Button type="button" variant="ghost" onClick={replayAllTips}>Replay all tips</Button>
                </div>
              </div>
            </section>

            <SettingsForm />
            <RuntimeStatusPanel
              snapshot={runtime.data ?? UNKNOWN_RUNTIME_SNAPSHOT}
              isLoading={runtime.isLoading}
              onRefresh={() => void runtime.refetch()}
            />
            <BackupProvenancePanel snapshot={runtime.data ?? UNKNOWN_RUNTIME_SNAPSHOT} />
            <UpdatesCard />
            <RetentionCard />
            <div className="flex items-center justify-between gap-4 rounded-lg border bg-card/50 px-4 py-3">
              <div className="space-y-0.5">
                <p className="text-sm font-medium">
                  {t('intro.replayTitle', { defaultValue: 'Welcome intro' })}
                </p>
                <p className="text-sm text-muted-foreground">
                  {t('intro.replayDesc', { defaultValue: 'Play the opening animation again.' })}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={() => replayIntro()}>
                <Sparkles className="h-4 w-4" />
                {t('intro.replay', { defaultValue: 'Replay' })}
              </Button>
            </div>
            <ObservabilityCard />
        </div>
      </SystemRouteFrame>
    </AppShell>
  )
}
