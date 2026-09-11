// v0.7.117 — wrapper around the v0.7.112 /healthz/deep endpoint.
//
// The endpoint lives at the root of the FastAPI app (auth-exempt), not
// under /api. apiClient's baseURL is `${apiUrl}/api`, so we override
// baseURL on the request to hit the root path directly.
//
// The backend returns the same JSON shape on 200 (healthy / degraded)
// and 503 (not_ready), so we ask axios to tolerate any status under
// 600 and read the body either way. On a network failure we synthesize
// a "not_ready" response so the wizard has something concrete to show
// instead of an unbounded error state.

import apiClient from './client'
import { getApiUrl } from '@/lib/config'

export type SubsystemKey =
  | 'database'
  | 'migrations'
  | 'embedding_model'
  | 'chat_model'
  | 'command_registry'
  // v0.8.127 — background worker heartbeat (degraded when absent, never not_ready)
  | 'worker'

export interface SubsystemCheck {
  status: string
  ok: boolean
  error: string | null
}

export type DeepHealthStatus = 'healthy' | 'degraded' | 'not_ready'

export interface DeepHealthResponse {
  status: DeepHealthStatus
  checks: Record<SubsystemKey, SubsystemCheck>
}

const SYNTHESIZED_NOT_READY: DeepHealthResponse = {
  status: 'not_ready',
  checks: {
    database: { status: 'offline', ok: false, error: 'API unreachable' },
    migrations: { status: 'error', ok: false, error: 'API unreachable' },
    embedding_model: { status: 'missing', ok: false, error: 'API unreachable' },
    chat_model: { status: 'missing', ok: false, error: 'API unreachable' },
    command_registry: { status: 'error', ok: false, error: 'API unreachable' },
    worker: { status: 'offline', ok: false, error: 'API unreachable' },
  },
}

export const healthApi = {
  getDeepHealth: async (): Promise<DeepHealthResponse> => {
    try {
      const apiUrl = await getApiUrl()
      const res = await apiClient.get<DeepHealthResponse>('/healthz/deep', {
        baseURL: apiUrl,
        validateStatus: (s) => s < 600,
        headers: { 'x-skip-error-toast': '1' },
      })
      return res.data
    } catch {
      return SYNTHESIZED_NOT_READY
    }
  },
}
