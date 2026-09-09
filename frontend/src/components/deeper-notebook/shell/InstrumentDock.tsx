'use client'

import { Book, FileText, LogOut, Mic, Plus } from 'lucide-react'

import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { GmailSidebarButton } from '@/components/deeper-notebook/GmailSidebarButton'
import { ThemeSwitcher } from '@/components/deeper-notebook/ThemeSwitcher'
import { LocalModelHealthBadges } from '@/components/chat/LocalModelHealthBadges'
import { LanguageToggle } from '@/components/common/LanguageToggle'
import { useAuth } from '@/lib/hooks/use-auth'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import { CREATE_TARGETS, type CreateTarget } from '@/components/layout/AppSidebar'
import { readDesktopVersion } from '@/lib/desktop-version'
import { useTranslation } from '@/lib/hooks/use-translation'

export function InstrumentDock() {
  const { t } = useTranslation()
  const { logout } = useAuth()
  const { openSourceDialog, openNotebookDialog, openPodcastDialog } = useCreateDialogs()

  const handleCreateSelection = (target: CreateTarget) => {
    if (target === 'source') openSourceDialog()
    if (target === 'notebook') openNotebookDialog()
    if (target === 'podcast') openPodcastDialog()
  }

  return (
    <nav
      aria-label="Primary tools"
      className="dn-instrument-dock"
      data-mobile-mode="bottom-tool-row"
    >
      <div className="dn-dock-brand" data-guided-tip-anchor="/">
        <span className="dn-dock-brand-mark shadow-[0_0_14px_rgba(45,212,191,0.25),inset_0_1px_0_rgba(255,255,255,0.2)] ring-1 ring-primary/30 transition-all duration-200" aria-hidden="true">DN</span>
        <span className="dn-dock-brand-name">Deeper Notebook</span>
      </div>

      <div className="dn-dock-create">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              aria-label={t('common.create')}
              className="w-full justify-center p-0 h-10 rounded-xl group relative overflow-hidden bg-primary text-primary-foreground shadow-[0_2px_10px_rgba(20,184,166,0.3),inset_0_1px_0_rgba(255,255,255,0.2)] hover:bg-primary/95 active:scale-95 transition-all duration-150"
            >
              <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-black/10 dark:bg-white/10 group-hover:scale-110 group-hover:bg-black/15 transition-all duration-150">
                <Plus className="h-4 w-4" aria-hidden="true" />
              </span>
              <span>{t('common.create')}</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" side="top" className="w-48">
            {CREATE_TARGETS.map((target) => {
              const label = target === 'source'
                ? t('common.source')
                : target === 'notebook'
                  ? t('common.notebook')
                  : t('common.podcast')
              const Icon = target === 'source'
                ? FileText
                : target === 'notebook'
                  ? Book
                  : Mic
              return (
                <DropdownMenuItem
                  key={target}
                  onSelect={() => handleCreateSelection(target)}
                  className="gap-2 cursor-pointer"
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {label}
                </DropdownMenuItem>
              )
            })}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div className="dn-dock-utilities" data-mobile-mode="utility-row">
        <div className="dn-dock-utility-row">
          <ThemeSwitcher iconOnly />
          <LanguageToggle iconOnly />
          <GmailSidebarButton iconOnly />
        </div>

        <Button
          type="button"
          variant="outline"
          className="w-full justify-start gap-3 rounded-xl hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive active:scale-95 transition-all duration-150"
          onClick={logout}
          aria-label={t('common.signOut')}
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
          <span>{t('common.signOut')}</span>
        </Button>

        <div className="dn-dock-health" data-guided-tip-anchor="/settings/local-models">
          <LocalModelHealthBadges />
        </div>

        <div className="dn-dock-version" suppressHydrationWarning>
          v{typeof window !== 'undefined' ? (readDesktopVersion(window) || '—') : '—'}
        </div>
      </div>
    </nav>
  )
}
