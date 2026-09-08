// v0.8.6 Item D — TanStack Query hooks for /api/launcher-prefs.
// Mirrors the pattern established by use-mcp-servers.ts:
// - one query hook (GET)
// - one mutation hook (PUT) with toast feedback + invalidation
// - response type exported so the page can type the diff computation

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { apiClient } from '@/lib/api/client'
import { useTranslation } from '@/lib/hooks/use-translation'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LauncherPrefs {
  [key: string]: string
}

export interface LauncherPrefsResponse {
  prefs: LauncherPrefs
}

/**
 * Update payload — values may be strings (set) or null (remove the key).
 * This is what the PUT body accepts.
 */
export interface LauncherPrefsUpdate {
  prefs: { [key: string]: string | null }
}

// ---------------------------------------------------------------------------
// Query key
// ---------------------------------------------------------------------------

export const LAUNCHER_PREFS_QUERY_KEY = ['launcher-prefs'] as const

// ---------------------------------------------------------------------------
// Query hook — GET /api/launcher-prefs
// ---------------------------------------------------------------------------

export function useLauncherPrefs() {
  return useQuery<LauncherPrefsResponse>({
    queryKey: LAUNCHER_PREFS_QUERY_KEY,
    queryFn: async () => {
      const res = await apiClient.get<LauncherPrefsResponse>('/launcher-prefs')
      return res.data
    },
  })
}

// ---------------------------------------------------------------------------
// Mutation hook — PUT /api/launcher-prefs
// Accepts a diff payload (only changed fields); returns updated prefs.
// ---------------------------------------------------------------------------

export function useUpdateLauncherPrefs() {
  const queryClient = useQueryClient()
  const { t } = useTranslation()

  return useMutation<LauncherPrefsResponse, Error, LauncherPrefsUpdate>({
    mutationFn: async (payload) => {
      const res = await apiClient.put<LauncherPrefsResponse>(
        '/launcher-prefs',
        payload,
      )
      return res.data
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: LAUNCHER_PREFS_QUERY_KEY })
      toast.success(t('settings.launcherPrefs.saveSuccess'))
    },
    onError: (error: unknown) => {
      const message =
        (error as { response?: { data?: { detail?: string } } })
          ?.response?.data?.detail ??
        t('settings.launcherPrefs.saveFailed')
      toast.error(message)
    },
  })
}

export interface HardwareProfile {
  system: string
  machine: string
  chip_name: string
  is_apple_silicon: boolean
  total_ram_bytes: number
  total_ram_gb: number
  tier_name: string
  guidance: string
  recommended_context: number
  recommended_quant: string
  recommended_flash_attn: boolean
  recommended_kv_quant: string
}

export const HARDWARE_PROFILE_QUERY_KEY = ['hardware-profile'] as const

export function useHardwareProfile() {
  return useQuery<HardwareProfile>({
    queryKey: HARDWARE_PROFILE_QUERY_KEY,
    queryFn: async () => {
      const res = await apiClient.get<HardwareProfile>(
        '/launcher-prefs/hardware-profile',
      )
      return res.data
    },
    staleTime: 60_000,
  })
}

