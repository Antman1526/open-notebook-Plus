'use client'

import React, { useState, useEffect, useCallback } from 'react'
import { GitBranch, GitCommit, RefreshCw, Camera, Clock, User, Copy, Check, CloudUpload, CloudDownload, Globe } from 'lucide-react'
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

interface GitRemoteItem {
  name: string
  url: string
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

  // Remote Git Sync state
  const [remotes, setRemotes] = useState<GitRemoteItem[]>([])
  const [remoteInput, setRemoteInput] = useState('')
  const [isEditingRemote, setIsEditingRemote] = useState(false)
  const [isPushing, setIsPushing] = useState(false)
  const [isPulling, setIsPulling] = useState(false)

  const fetchHistory = useCallback(async () => {
    if (!vaultId) return
    setLoading(true)
    try {
      const res = await apiClient.get<GitCommitItem[]>(`/vaults/${vaultId}/git/history`)
      setHistory(res.data || [])
    } catch (err: unknown) {
      console.error('Failed to load vault git history:', err)
      toast.error('Failed to load version history')
    } finally {
      setLoading(false)
    }
  }, [vaultId])

  const fetchRemotes = useCallback(async () => {
    if (!vaultId) return
    try {
      const res = await apiClient.get<GitRemoteItem[]>(`/vaults/${vaultId}/git/remote`)
      setRemotes(res.data || [])
      if (res.data?.length > 0) {
        setRemoteInput(res.data[0].url)
      }
    } catch (err: unknown) {
      console.error('Failed to load vault remotes:', err)
    }
  }, [vaultId])

  useEffect(() => {
    if (open) {
      void fetchHistory()
      void fetchRemotes()
    }
  }, [open, fetchHistory, fetchRemotes])

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
        void fetchHistory()
      } else {
        toast.info(res.data.message || 'No changes to snapshot')
      }
    } catch (err: unknown) {
      console.error('Failed to take snapshot:', err)
      toast.error('Failed to record snapshot')
    } finally {
      setTakingSnapshot(false)
    }
  }

  const handleSaveRemote = async () => {
    if (!vaultId || !remoteInput.trim()) return
    try {
      const res = await apiClient.post(`/vaults/${vaultId}/git/remote`, {
        remote_name: 'origin',
        url: remoteInput.trim(),
      })
      if (res.data.ok) {
        toast.success('Remote origin repository configured')
        setIsEditingRemote(false)
        void fetchRemotes()
      } else {
        toast.error(res.data.error || 'Failed to set remote')
      }
    } catch {
      toast.error('Failed to set remote repository')
    }
  }

  const handlePush = async () => {
    if (!vaultId) return
    setIsPushing(true)
    try {
      const res = await apiClient.post(`/vaults/${vaultId}/git/push`, {
        remote: 'origin',
      })
      if (res.data.ok) {
        toast.success(res.data.message || 'Successfully pushed to remote')
      } else {
        toast.error(res.data.error || 'Push failed')
      }
    } catch {
      toast.error('Failed to push to remote repository')
    } finally {
      setIsPushing(false)
    }
  }

  const handlePull = async () => {
    if (!vaultId) return
    setIsPulling(true)
    try {
      const res = await apiClient.post(`/vaults/${vaultId}/git/pull`, {
        remote: 'origin',
      })
      if (res.data.ok) {
        toast.success(res.data.message || 'Successfully pulled latest changes')
        void fetchHistory()
      } else {
        toast.error(res.data.error || 'Pull failed')
      }
    } catch {
      toast.error('Failed to pull from remote repository')
    } finally {
      setIsPulling(false)
    }
  }

  const handleCopyHash = (hash: string) => {
    void navigator.clipboard.writeText(hash)
    setCopiedHash(hash)
    toast.success('Commit hash copied')
    setTimeout(() => setCopiedHash(null), 2000)
  }

  const activeRemote = remotes.find((r) => r.name === 'origin') || remotes[0]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-primary" />
            Vault Version History: {vaultName}
          </DialogTitle>
          <DialogDescription>
            Git-backed version history, manual snapshot controls, and cloud remote sync.
          </DialogDescription>
        </DialogHeader>

        {/* Remote Sync Bar */}
        <div className="rounded-lg border bg-muted/40 p-2.5 text-xs space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 font-medium text-foreground">
              <Globe className="h-3.5 w-3.5 text-primary" />
              <span>Remote: {activeRemote ? activeRemote.url : 'No remote configured'}</span>
            </div>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setIsEditingRemote(!isEditingRemote)}
                className="h-6 px-2 text-[11px]"
              >
                {isEditingRemote ? 'Cancel' : activeRemote ? 'Change' : 'Configure'}
              </Button>
              {activeRemote && (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handlePush}
                    disabled={isPushing}
                    className="h-6 px-2 text-[11px] gap-1"
                    title="Push commits to remote"
                  >
                    {isPushing ? <LoadingSpinner size="sm" /> : <CloudUpload className="h-3 w-3" />}
                    Push
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handlePull}
                    disabled={isPulling}
                    className="h-6 px-2 text-[11px] gap-1"
                    title="Pull latest changes from remote"
                  >
                    {isPulling ? <LoadingSpinner size="sm" /> : <CloudDownload className="h-3 w-3" />}
                    Pull
                  </Button>
                </>
              )}
            </div>
          </div>

          {isEditingRemote && (
            <div className="flex gap-1.5 pt-1">
              <Input
                placeholder="git@github.com:user/repo.git or https://..."
                value={remoteInput}
                onChange={(e) => setRemoteInput(e.target.value)}
                className="h-7 text-xs"
              />
              <Button size="sm" className="h-7 text-xs px-2.5" onClick={handleSaveRemote}>
                Save
              </Button>
            </div>
          )}
        </div>

        {/* Snapshot Input */}
        <div className="flex gap-2 pt-1">
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
