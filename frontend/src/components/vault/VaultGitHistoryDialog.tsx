'use client'

import React, { useState, useEffect } from 'react'
import { GitBranch, GitCommit, RefreshCw, Camera, Clock, User } from 'lucide-react'
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

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <GitBranch className="h-5 w-5 text-primary" />
            Vault Version History: {vaultName}
          </DialogTitle>
          <DialogDescription>
            View Git-backed version history and capture manual snapshots for this vault.
          </DialogDescription>
        </DialogHeader>

        {/* Snapshot Input */}
        <div className="flex gap-2 pt-2">
          <Input
            placeholder="Snapshot message (optional)"
            value={snapshotMsg}
            onChange={(e) => setSnapshotMsg(e.target.value)}
            disabled={takingSnapshot}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleTakeSnapshot()
            }}
          />
          <Button
            onClick={handleTakeSnapshot}
            disabled={takingSnapshot}
            className="shrink-0 gap-1"
          >
            <Camera className="h-4 w-4" />
            Snapshot
          </Button>
          <Button
            variant="outline"
            size="icon"
            onClick={fetchHistory}
            disabled={loading}
            title="Refresh history"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
          </Button>
        </div>

        {/* Commit List */}
        <ScrollArea className="h-72 w-full rounded-md border p-3">
          {history.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-48 text-muted-foreground text-sm">
              <GitCommit className="h-8 w-8 mb-2 opacity-50" />
              <span>No snapshots recorded yet</span>
            </div>
          ) : (
            <div className="space-y-3">
              {history.map((commit) => (
                <div
                  key={commit.hash}
                  className="flex flex-col gap-1 p-2.5 rounded-lg bg-muted/40 hover:bg-muted/70 transition-colors border"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-sm text-foreground">
                      {commit.message}
                    </span>
                    <Badge variant="outline" className="font-mono text-xs">
                      {commit.hash.slice(0, 7)}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground mt-1">
                    <span className="flex items-center gap-1">
                      <User className="h-3 w-3" />
                      {commit.author}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock className="h-3 w-3" />
                      {commit.date}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}
