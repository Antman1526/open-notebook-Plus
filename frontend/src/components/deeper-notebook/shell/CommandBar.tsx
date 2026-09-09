'use client'

import { useEffect, useState } from 'react'
import { Command, Search } from 'lucide-react'
import { usePathname } from 'next/navigation'

import { Button } from '@/components/ui/button'
import { requestCommandSurface } from '@/lib/commands/command-surface-store'
import { useTranslation } from '@/lib/hooks/use-translation'
import { FocusModeControl } from './FocusModeControl'

export function CommandBar() {
  const pathname = usePathname()
  const { t } = useTranslation()
  const [isMac, setIsMac] = useState<boolean | null>(null)

  useEffect(() => {
    setIsMac(navigator.platform.toLowerCase().includes('mac'))
  }, [])

  const routeLabel = pathname && pathname !== '/'
    ? pathname.split('/').filter(Boolean)[0]
    : 'notebook'

  return (
    <header className="dn-command-bar" aria-label="Command bar">
      <div className="dn-command-breadcrumb flex items-center gap-2.5">
        <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wider bg-primary/10 text-primary border border-primary/20 shadow-[0_0_8px_rgba(45,212,191,0.12)]">
          <span className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
          <span className="dn-command-kicker m-0 leading-none">{routeLabel}</span>
        </span>
        <p className="dn-command-title font-semibold tracking-tight text-foreground">Deeper Notebook</p>
      </div>
      {/* v0.8.96 — the Focus control lives HERE, in flow, not floated over the
          bar. It used to be a shell-level sibling with position:absolute at the
          top-right, which put it directly on top of this trigger at every width.
          Flex layout removes the class of bug: no width reservation to keep in
          sync, nothing to drift when a shell's DOM changes. The legacy shell has
          no command bar and still renders it as a floated sibling. */}
      <div className="dn-command-actions">
        <Button
          type="button"
          variant="outline"
          className="dn-command-trigger group h-9 px-3 gap-2.5 rounded-xl border-border/80 bg-background/80 hover:bg-background/95 hover:border-primary/40 active:scale-[0.98] shadow-[inset_0_1px_0_rgba(255,255,255,0.06),0_1px_3px_rgba(0,0,0,0.04)] transition-all duration-150"
          aria-label="Open command palette"
          onClick={(event) => requestCommandSurface('global', '', event.currentTarget)}
        >
          <Search className="h-3.5 w-3.5 text-muted-foreground group-hover:text-primary transition-colors" aria-hidden="true" />
          <span className="text-sm font-medium text-muted-foreground group-hover:text-foreground transition-colors">{t('common.quickActions')}</span>
          <span className="dn-command-shortcut ml-auto inline-flex items-center gap-0.5 rounded-md border border-border/80 bg-muted/60 px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]" data-testid="command-shortcut">
            <Command className="h-2.5 w-2.5" aria-hidden="true" />
            {isMac !== null ? (isMac ? '⌘K' : 'Ctrl+K') : 'K'}
          </span>
        </Button>
        <FocusModeControl />
      </div>
    </header>
  )
}
