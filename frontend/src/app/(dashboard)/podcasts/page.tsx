'use client'

import { useMemo, useState } from 'react'
import { AlertTriangle } from 'lucide-react'

import { AppShell } from '@/components/layout/AppShell'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { EpisodesTab } from '@/components/podcasts/EpisodesTab'
import { TemplatesTab } from '@/components/podcasts/TemplatesTab'
import { Mic, LayoutTemplate } from 'lucide-react'
import { useTranslation } from '@/lib/hooks/use-translation'
import { useEpisodeProfiles, useSpeakerProfiles } from '@/lib/hooks/use-podcasts'
import { needsModelSetup } from '@/lib/types/podcasts'
import { SystemRouteFrame } from '@/components/deeper-notebook/route-frames/SystemRouteFrames'

export default function PodcastsPage() {
  const { t } = useTranslation()
  const [activeTab, setActiveTab] = useState<'episodes' | 'templates'>('episodes')

  const { episodeProfiles } = useEpisodeProfiles()
  const { speakerProfiles } = useSpeakerProfiles(episodeProfiles)

  const hasUnconfiguredProfiles = useMemo(() => {
    return episodeProfiles.some(needsModelSetup) || speakerProfiles.some(needsModelSetup)
  }, [episodeProfiles, speakerProfiles])

  // v0.7.153 — Visual rhythm refresh (Podcasts = edge-to-edge wide).
  // Changes:
  //   - Page padding px-6 py-6 → px-6 py-10 sm:px-8 (more vertical breathing
  //     room; horizontal stays edge-to-edge for the wide grid layout)
  //   - Top-level space-y-6 → space-y-10 so the header / alert / tabs
  //     sections have a real visual break between them
  //   - Title text-2xl → text-3xl, header gap space-y-1 → space-y-2
  //     (title and subtitle stop touching)
  //   - Removed the "CHOOSE A VIEW" all-caps caption above the tab list:
  //     two-tab toggles are self-explanatory; the label was visual noise
  //     and added one of the cramped-stacking pain points
  //   - Tabs inner space-y-6 → space-y-8 (more room between the tab
  //     toggle and the active panel)
  return (
    <AppShell>
      <SystemRouteFrame route="/podcasts" title={t('podcasts.listTitle')} description={t('podcasts.listDesc')}>
        <div className="space-y-10">

          {hasUnconfiguredProfiles ? (
            <Alert className="bg-amber-50 dark:bg-amber-950/30 text-amber-900 dark:text-amber-200 border-amber-200 dark:border-amber-800/50">
              <AlertTriangle className="h-4 w-4 text-amber-700 dark:text-amber-400" />
              <AlertTitle>{t('podcasts.setupRequired')}</AlertTitle>
              <AlertDescription>
                {t('podcasts.setupRequiredDesc')}
              </AlertDescription>
            </Alert>
          ) : null}

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as 'episodes' | 'templates')}
            className="space-y-8"
          >
            <TabsList aria-label={t('common.accessibility.podcastViews')} className="w-full max-w-md">
              <TabsTrigger value="episodes">
                <Mic className="h-4 w-4" />
                {t('podcasts.episodesTab')}
              </TabsTrigger>
              <TabsTrigger value="templates">
                <LayoutTemplate className="h-4 w-4" />
                {t('podcasts.templatesTab')}
              </TabsTrigger>
            </TabsList>

            <TabsContent value="episodes">
              <EpisodesTab />
            </TabsContent>

            <TabsContent value="templates">
              <TemplatesTab />
            </TabsContent>
          </Tabs>
        </div>
      </SystemRouteFrame>
    </AppShell>
  )
}
