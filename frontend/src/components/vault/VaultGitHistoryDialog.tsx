'use client'

import React, { useState, useEffect } from 'react'
import { GitBranch, GitCommit, RefreshCw, Camera, Clock, User, Copy, Check } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { LoadingSpinner } from '@/components/common/LoadingSpinner'
import { toast } from 'sonner'
import apiClient from '@/lib/api/client'

interface GitCommitItem {
  hash: string
  author: string
  date: string
  message: string
}

interface VaultGitHistoryDialogProps {
  vaultId: string
  vaultName?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

function formatCommitDate(dateStr: string): string {
  if (!dateStr) return '—'
  const parsed = new Date(dateStr)
  if (isNaN(parsed.getTime())) return dateStr
  const diff = Date.now() - parsed.getTime()
  if (diff < 60_000) return 'just now'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`
  if (diff < 7 * 86_400_000) return `${Math.floor(diff / 86_400_000)}d ago`
  return parsed.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
}

export function VaultGitHistoryDialog({
  vaultId,
  vaultName = 'Vault',
  open,
  onOpenChange,
}: VaultGitHistoryDialogProps) {
  const [history, setHistory] = useState<GitCommitItem[]>([])
  const [loading, setLoading] = useState(false)
  const [snapshotMsg, setSnapshotMsg] = useState('')
  const [takingSnapshot, setTakingSnapshot] = useState(false)
  const [copiedHash, setCopiedHash] = useState<string | null>(null)

  const fetchHistory = async () => {
    if (!vaultId) return
    setLoading(true)
    try {
      const res = await apiClient.get<GitCommitItem[]>(`/vaults/${vaultId}/git/history`)
      setHistory(res.data || [])
    } catch (err: any) {
      console.error('Failed to load vault git history:', err)
      toast.error('Failed to load version history')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) {
      fetchHistory()
    }
  }, [open, vaultId])

  const handleTakeSnapshot = async () => {
    if (!vaultId) return
    setTakingSnapshot(true)
    try {
      const res = await apiClient.post(`/vaults/${vaultId}/git/snapshot`, {
        message: snapshotMsg.trim() || undefined,
      })
      if (res.data.committed) {
        toast.success(`Snapshot recorded: ${res.data.commit?.slice(0, 8)}`)
        setSnapshotMsg('')
        fetchHistory()
      } else {
        toast.info(res.data.message || 'No changes to snapshot')
      }
    } catch (err: any) {
      console.error('Failed to take snapshot:', err)
      toast.error('Failed to record snapshot')
    } finally {
      setTakingSnapshot(false)
    }
  }

  const handleCopyHash = (hash: string) => {
    void navigator.clipboard.writeText(hash)
    setCopiedHash(hash)
    toast.success('Commit hash copied')
    setTimeout(() => setCopiedHash(null), 2000)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-primary" />
            Vault Version History: {vaultName}
          </DialogTitle>
          <DialogDescription>
            Git-backed version history and manual snapshot controls for this knowledge vault.
          </DialogDescription>
        </DialogHeader>

        {/* Snapshot Input */}
        <div className="flex gap-2 pt-2">
          <Input
            placeholder="Snapshot commit message (optional)"
            value={snapshotMsg}
            onChange={(e) => setSnapshotMsg(e.target.value)}
            disabled={takingSnapshot}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleTakeSnapshot()
            }}
            className="text-sm"
            aria-label="Snapshot message"
          />
          <Button
            onClick={handleTakeSnapshot}
            disabled={takingSnapshot}
            className="shrink-0 gap-1.5"
          >
            {takingSnapshot ? (
              <LoadingSpinner size="sm" />
            ) : (
              <Camera className="h-4 w-4" />
            )}
            Snapshot
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={fetchHistory}
            disabled={loading}
            title="Refresh history"
            aria-label="Refresh history"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {/* Commit List */}
        <ScrollArea className="h-72 w-full rounded-md border p-3 bg-card/30">
          {loading && history.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground text-sm gap-2">
              <LoadingSpinner size="md" />
              <span>Loading version history...</span>
            </div>
          ) : history.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground text-sm gap-1.5">
              <GitCommit className="h-8 w-8 mb-1 opacity-40 text-primary" />
              <span className="font-medium text-foreground">No snapshots recorded yet</span>
              <p className="text-xs text-muted-foreground text-center max-w-xs">
                Take a snapshot above or wait for auto-sync to record changes automatically.
              </p>
            </div>
          ) : (
            <div className="space-y-2.5">
              {history.map((commit) => {
                const isCopied = copiedHash === commit.hash
                return (
                  <div
                    key={commit.hash}
                    className="flex flex-col gap-1 p-3 rounded-lg bg-card/80 hover:bg-muted/60 transition-colors border shadow-xs"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <span className="font-medium text-sm text-foreground leading-snug">
                        {commit.message}
                      </span>
                      <Badge
                        variant="outline"
                        className="font-mono text-xs shrink-0 cursor-pointer hover:bg-muted transition-colors py-0.5 px-1.5 flex items-center gap-1"
                        onClick={() => handleCopyHash(commit.hash)}
                        title="Click to copy full commit hash"
                      >
                        {isCopied ? (
                          <Check className="h-3 w-3 text-primary" />
                        ) : (
                          <Copy className="h-3 w-3 opacity-60" />
                        )}
                        {commit.hash.slice(0, 7)}
                      </Badge>
                    </div>
                    <div className="flex items-center gap-3 text-xs text-muted-foreground mt-1">
                      <span className="flex items-center gap-1">
                        <User className="h-3 w-3 opacity-70" />
                        {commit.author}
                      </span>
                      <span className="flex items-center gap-1" title={commit.date}>
                        <Clock className="h-3 w-3 opacity-70" />
                        {formatCommitDate(commit.date)}
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
