'use client'

import { useState, useEffect } from 'react'
import Link from 'next/link'
import Image from 'next/image'
import { usePathname } from 'next/navigation'
import { motion } from 'framer-motion'

import { cn } from '@/lib/utils'
import { readDesktopVersion } from '@/lib/desktop-version'
import { isStudyWorkbenchEnabled } from '@/lib/features'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/lib/hooks/use-auth'
import { useIsDesktop } from '@/lib/hooks/use-media-query'
import { useSidebarStore } from '@/lib/stores/sidebar-store'
import { useCreateDialogs } from '@/lib/hooks/use-create-dialogs'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
// ONP v0.5.7 — replaced upstream ThemeToggle (light/dark/system) with our
// shadow-layer ThemeSwitcher that supports all ONP themes + live-switch.
// import { ThemeToggle } from '@/components/common/ThemeToggle'
import { ThemeSwitcher as ThemeToggle, GmailSidebarButton } from '@/components/deeper-notebook'
import { LocalModelHealthBadges } from '@/components/chat/LocalModelHealthBadges'
import { LanguageToggle } from '@/components/common/LanguageToggle'
import type { TFunction } from 'i18next'
import { useTranslation } from '@/lib/hooks/use-translation'
// v0.7.167 — Separator import no longer needed; section gaps use
// margin instead of visible dividers. Kept as a comment so a future
// "what happened to it?" grep finds the rationale.
import {
  Book,
  Search,
  Mic,
  Bot,
  Plug,
  Shuffle,
  Settings,
  LogOut,
  ChevronLeft,
  Menu,
  FileText,
  Plus,
  Wrench,
  Command,
  Sparkles,
  GraduationCap,
  Inbox,
  Network,
  Sliders,  // v0.8.6 Item D — Launch preferences nav icon
} from 'lucide-react'

export const getNavigation = (t: TFunction) => [
  {
    title: t('navigation.collect'),
    items: [
      { name: t('navigation.sources'), href: '/sources', icon: FileText },
      { name: 'Capture', href: '/capture', icon: Inbox },
    ],
  },
  {
    title: t('navigation.process'),
    items: [
      { name: t('navigation.notebooks'), href: '/notebooks', icon: Book },
      { name: t('navigation.knowledge'), href: '/knowledge', icon: Network },
      { name: t('navigation.askAndSearch'), href: '/search', icon: Search },
    ],
  },
  {
    title: t('navigation.create'),
    items: [
      // ONP v0.7.0 — Studio: one-shot upload + mode → output. Lives in
      // the Create group because that's its conceptual home (it produces
      // a new notebook or podcast from uploaded docs).
      { name: 'Studio', href: '/studio', icon: Sparkles },
      { name: t('navigation.podcasts'), href: '/podcasts', icon: Mic },
      ...(isStudyWorkbenchEnabled()
        ? [{ name: t('navigation.study'), href: '/study', icon: GraduationCap }]
        : []),
    ],
  },
  {
    title: t('navigation.manage'),
    items: [
      { name: t('navigation.models'), href: '/settings/api-keys', icon: Bot },
      { name: t('navigation.transformations'), href: '/transformations', icon: Shuffle },
      { name: t('navigation.settings'), href: '/settings', icon: Settings },
      // v0.8.0 Phase 2 Task 10 — MCP Servers settings page
      { name: t('settings.mcp.navTitle'), href: '/settings/mcp', icon: Plug },
      // v0.8.6 Item D — Launch preferences (env-var knobs for local LLM)
      { name: t('settings.launcherPrefs.navTitle'), href: '/settings/launcher-prefs', icon: Sliders },
      { name: t('navigation.advanced'), href: '/advanced', icon: Wrench },
    ],
  },
] as const

export const CREATE_TARGETS = ['source', 'notebook', 'podcast'] as const
export type CreateTarget = (typeof CREATE_TARGETS)[number]

export function AppSidebar() {
  const { t } = useTranslation()
  const navigation = getNavigation(t)
  const pathname = usePathname()
  const { logout } = useAuth()
  const { isCollapsed: storeCollapsed, toggleCollapse } = useSidebarStore()
  const { openSourceDialog, openNotebookDialog, openPodcastDialog } = useCreateDialogs()

  // v0.7.41 — responsive collapse. On `<lg` viewports the sidebar is
  // forced into mini-rail mode regardless of the persisted store state.
  // The full 256px sidebar dominates phone screens (320-414px wide) and
  // pushes the content area off-screen. At lg (1024px+), the user's
  // saved preference (expanded or collapsed) applies normally.
  const isDesktop = useIsDesktop()
  const isCollapsed = isDesktop ? storeCollapsed : true

  const [createMenuOpen, setCreateMenuOpen] = useState(false)
  // v0.7.28 — `null` until the effect resolves. The previous `true`
  // default caused every Windows/Linux user to see "⌘K" on first paint
  // then flicker to "Ctrl+K" after hydration. With null we render the
  // kbd hint only after the effect runs, eliminating the flash.
  const [isMac, setIsMac] = useState<boolean | null>(null)

  // Detect platform for keyboard shortcut display
  useEffect(() => {
    setIsMac(navigator.platform.toLowerCase().includes('mac'))
  }, [])

  const handleCreateSelection = (target: CreateTarget) => {
    setCreateMenuOpen(false)

    if (target === 'source') {
      openSourceDialog()
    } else if (target === 'notebook') {
      openNotebookDialog()
    } else if (target === 'podcast') {
      openPodcastDialog()
    }
  }

  return (
    <TooltipProvider delayDuration={0}>
      <div
        className={cn(
          'app-sidebar flex h-full flex-col bg-sidebar border-sidebar-border border-r transition-all duration-300',
          isCollapsed ? 'w-16' : 'w-64'
        )}
      >
        <div
          data-guided-tip-anchor="/"
          className={cn(
            'flex h-16 items-center group',
            isCollapsed ? 'justify-center px-2' : 'justify-between px-4'
          )}
        >
          {isCollapsed ? (
            <div className="relative flex items-center justify-center w-full">
              <Image
                src="/logo.svg"
                alt="Deeper Notebook"
                width={32}
                height={32}
                className="transition-opacity group-hover:opacity-0"
              />
              {/* v0.7.197 — `[@media(hover:none)]:opacity-100` makes
                  the collapsed-sidebar expand button visible on touch
                  devices (iPad, touch laptops). The `opacity-0 group-
                  hover:opacity-100` pair only works on devices that
                  fire a real `hover` — on a touch screen the button
                  was permanently invisible, so users had no way to
                  expand the sidebar. Repeated across NotebookCard,
                  NotesColumn, SourceCard (same hover-only trap). */}
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleCollapse}
                className="absolute text-sidebar-foreground hover:bg-sidebar-accent opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity"
              >
                <Menu className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2">
                <Image src="/logo.svg" alt={t('common.appName')} width={32} height={32} />
                <span className="text-base font-medium text-sidebar-foreground">
                  {t('common.appName')}
                </span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={toggleCollapse}
                className="text-sidebar-foreground hover:bg-sidebar-accent"
                data-testid="sidebar-toggle"
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
            </>
          )}
        </div>

        <nav
          className={cn(
            'flex-1 space-y-1 py-4',
            isCollapsed ? 'px-2' : 'px-3'
          )}
        >
          <div
            className={cn(
              'mb-4',
              isCollapsed ? 'px-0' : 'px-3'
            )}
          >
            <DropdownMenu open={createMenuOpen} onOpenChange={setCreateMenuOpen}>
              {isCollapsed ? (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <DropdownMenuTrigger asChild>
                      <Button
                        onClick={() => setCreateMenuOpen(true)}
                        variant="default"
                        size="sm"
                        className="w-full justify-center px-2 h-10 rounded-xl group relative overflow-hidden bg-primary hover:bg-primary/90 text-primary-foreground border-0 shadow-[0_2px_10px_rgba(20,184,166,0.25),inset_0_1px_0_rgba(255,255,255,0.2)] active:scale-95 transition-all duration-150"
                        aria-label={t('common.create')}
                      >
                        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-black/10 dark:bg-white/10 group-hover:scale-110 transition-all duration-150">
                          <Plus className="h-4 w-4" />
                        </span>
                      </Button>
                    </DropdownMenuTrigger>
                  </TooltipTrigger>
                   <TooltipContent side="right">{t('common.create')}</TooltipContent>
                </Tooltip>
              ) : (
                <DropdownMenuTrigger asChild>
                  <Button
                    onClick={() => setCreateMenuOpen(true)}
                    variant="default"
                    size="sm"
                    className="w-full justify-start h-10 px-3 rounded-xl group relative overflow-hidden bg-primary hover:bg-primary/90 text-primary-foreground border-0 shadow-[0_2px_10px_rgba(20,184,166,0.25),inset_0_1px_0_rgba(255,255,255,0.2)] active:scale-[0.98] transition-all duration-150"
                   >
                    <span className="flex h-6 w-6 items-center justify-center rounded-lg bg-black/10 dark:bg-white/10 mr-2 group-hover:scale-110 group-hover:bg-black/15 transition-all duration-150">
                      <Plus className="h-3.5 w-3.5" />
                    </span>
                    <span className="font-medium">{t('common.create')}</span>
                  </Button>
                </DropdownMenuTrigger>
              )}

              <DropdownMenuContent
                align={isCollapsed ? 'end' : 'start'}
                side={isCollapsed ? 'right' : 'bottom'}
                className="w-48"
              >
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault()
                    handleCreateSelection('source')
                  }}
                  className="gap-2"
                >
                   <FileText className="h-4 w-4" />
                  {t('common.source')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault()
                    handleCreateSelection('notebook')
                  }}
                  className="gap-2"
                >
                   <Book className="h-4 w-4" />
                  {t('common.notebook')}
                </DropdownMenuItem>
                <DropdownMenuItem
                  onSelect={(event) => {
                    event.preventDefault()
                    handleCreateSelection('podcast')
                  }}
                  className="gap-2"
                >
                   <Mic className="h-4 w-4" />
                  {t('common.podcast')}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          {navigation.map((section, index) => (
            // v0.7.167 — Sidebar section separators downgraded from
            // visible `<Separator />` lines to plain vertical rhythm.
            // 4 sections × 1 separator each = 4 horizontal lines in the
            // sidebar — visually noisy on what should be a quiet rail.
            // The uppercase section labels (COLLECT, PROCESS, CREATE,
            // MANAGE) already provide enough delineation; just give
            // them breathing room via `mt-6` and the labels do the
            // work. Saves ~3px of visual chrome per gap.
            <div key={section.title} className={index > 0 ? "mt-6" : ""}>
              <div className="space-y-1">
                {!isCollapsed && (
                  <h3 className="mb-2 px-3 text-xs font-semibold uppercase tracking-wider text-sidebar-foreground/60">
                    {section.title}
                  </h3>
                )}

                {section.items.map((item) => {
                  // v0.7.28 — exact-or-child match. The previous
                  // `startsWith` made /sources highlight when on
                  // /sources/{id}, which is correct, BUT also caused
                  // false matches for unrelated routes that shared a
                  // common prefix. Keep startsWith but force a path-
                  // boundary check.
                  const isActive = !!pathname && (
                    pathname === item.href ||
                    pathname.startsWith(item.href + '/')
                  )
                  const button = (
                    <Button
                      data-guided-tip-anchor={item.href}
                      variant={isActive ? 'secondary' : 'ghost'}
                      className={cn(
                        // v0.8.70 — active state is now a Framer Motion
                        // `layoutId` accent pill that SLIDES between items on
                        // route change (replacing the per-button before: bar).
                        // No scale (the v0.7.25 overflow lesson) and the pill's
                        // position uses top offset instead of a transform so it
                        // can't conflict with Framer's layout transform.
                        'relative w-full gap-3 text-sidebar-foreground sidebar-menu-item rounded-xl transition-all duration-150',
                        isActive && 'bg-sidebar-accent/90 text-sidebar-accent-foreground font-medium shadow-[inset_0_1px_0_rgba(255,255,255,0.06),inset_0_0_12px_rgba(45,212,191,0.04)] border border-primary/20',
                        isCollapsed ? 'justify-center px-2' : 'justify-start'
                      )}
                    >
                      {isActive && (
                        <motion.span
                          layoutId="onp-sidebar-active"
                          className="absolute left-0 h-6 w-[3px] rounded-r bg-primary shadow-[0_0_8px_rgba(45,212,191,0.4)]"
                          style={{ top: 'calc(50% - 0.75rem)' }}
                          transition={{ type: 'spring', stiffness: 520, damping: 40 }}
                        />
                      )}
                      <item.icon className={cn('h-4 w-4', isActive && 'text-primary')} />
                      {!isCollapsed && <span>{item.name}</span>}
                    </Button>
                  )

                  if (isCollapsed) {
                    return (
                      <Tooltip key={item.name}>
                        <TooltipTrigger asChild>
                          <Link href={item.href}>
                            {button}
                          </Link>
                        </TooltipTrigger>
                        <TooltipContent side="right">{item.name}</TooltipContent>
                      </Tooltip>
                    )
                  }

                  return (
                    <Link key={item.name} href={item.href}>
                      {button}
                    </Link>
                  )
                })}
              </div>
            </div>
          ))}
        </nav>

        <div
          className={cn(
            'border-t border-sidebar-border p-3 space-y-2',
            isCollapsed && 'px-2'
          )}
        >
          {/* Command Palette hint */}
          {!isCollapsed && (
            <div className="px-3 py-1.5 text-xs text-sidebar-foreground/60">
              <div className="flex items-center justify-between">
                 <span className="flex items-center gap-1.5">
                  <Command className="h-3 w-3" />
                  {t('common.quickActions')}
                </span>
                {/* v0.7.28 — only render after platform detection
                    completes (isMac !== null). Avoids a flash of the
                    wrong key on SSR/hydration. */}
                {isMac !== null && (
                  <kbd className="pointer-events-none inline-flex h-5 select-none items-center gap-1 rounded border bg-muted px-1.5 font-mono text-[10px] font-medium text-muted-foreground">
                    {isMac ? <span className="text-xs">⌘</span> : <span>Ctrl+</span>}K
                  </kbd>
                )}
              </div>
               <p className="mt-1 text-[10px] text-sidebar-foreground/40">
                {t('common.quickActionsDesc')}
              </p>
            </div>
          )}

           <div
            className={cn(
              'flex flex-col gap-2',
              isCollapsed ? 'items-center' : 'items-stretch'
            )}
          >
            {isCollapsed ? (
              <>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="w-full">
                      <ThemeToggle iconOnly />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">{t('common.theme')}</TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="w-full">
                      <LanguageToggle iconOnly />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">{t('common.language')}</TooltipContent>
                </Tooltip>
                {/* ONP v0.6 — Gmail sign-in / status (icon-only when collapsed) */}
                <Tooltip>
                  <TooltipTrigger asChild>
                    <div className="w-full">
                      <GmailSidebarButton iconOnly />
                    </div>
                  </TooltipTrigger>
                  <TooltipContent side="right">Email Digests</TooltipContent>
                </Tooltip>
              </>
            ) : (
              <>
                <ThemeToggle />
                <LanguageToggle />
                {/* ONP v0.6 — Gmail sign-in / status */}
                <GmailSidebarButton />
              </>
            )}
          </div>

          {isCollapsed ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  className="w-full justify-center sidebar-menu-item"
                  onClick={logout}
                  aria-label={t('common.signOut')}
                >
                  <LogOut className="h-4 w-4" />
                </Button>
              </TooltipTrigger>
               <TooltipContent side="right">{t('common.signOut')}</TooltipContent>
            </Tooltip>
          ) : (
            <Button
              variant="outline"
              className="w-full justify-start gap-3 sidebar-menu-item"
              onClick={logout}
              aria-label={t('common.signOut')}
             >
              <LogOut className="h-4 w-4" />
              {t('common.signOut')}
            </Button>
          )}

          {/* v0.8.0 Phase 1 — local-model health badges; hidden when
              sidebar is collapsed and during the initial fetch. */}
          {!isCollapsed && (
            <div className="mt-2">
              <LocalModelHealthBadges />
            </div>
          )}

          {/* v0.7.210 — Version badge. Source: the desktop version bridge
              injected by desktop/window.py at page load (read from
              desktop/__init__.py:__version__). Falls back to the
              /api/version endpoint when running in dev mode outside
              the bundled .app. Read-only display — helps users tell
              support which build they're running, and confirms a
              fresh rebuild actually picked up new code.
              `[suppressHydrationWarning]` because the value is set
              client-side via window injection and may differ from
              the SSR fallback. */}
          {!isCollapsed && (
            <div
              className="mt-1 text-center text-[10px] text-sidebar-foreground/40 font-mono"
              suppressHydrationWarning
            >
              v{
                typeof window !== 'undefined'
                  ? (readDesktopVersion(window) || '—')
                  : '—'
              }
            </div>
          )}
        </div>
      </div>
    </TooltipProvider>
  )
}
