export interface Model {
  id: string
  name: string
  provider: string
  type: 'language' | 'embedding' | 'text_to_speech' | 'speech_to_text'
  credential?: string | null
  created: string
  updated: string
}

export interface CreateModelRequest {
  name: string
  provider: string
  type: 'language' | 'embedding' | 'text_to_speech' | 'speech_to_text'
  credential?: string
}

export interface ModelDefaults {
  default_chat_model?: string | null
  default_transformation_model?: string | null
  large_context_model?: string | null
  default_text_to_speech_model?: string | null
  default_speech_to_text_model?: string | null
  default_embedding_model?: string | null
  default_tools_model?: string | null
  // ONP v0.5 — dedicated slot for slow-but-deep reasoning models
  default_reasoning_model?: string | null
  // v0.8.1 — dedicated cloud slot for the smart router (DEEPER_NOTEBOOK_AUTO_ROUTE_CHAT).
  // Separate from default_chat_model so the router doesn't silently fall
  // back to a local model. Migration 18.
  auto_route_cloud?: string | null
  // v0.8.37 — UI-controllable smart routing. Pre-v0.8.37 the only way
  // to enable smart routing was DEEPER_NOTEBOOK_AUTO_ROUTE_CHAT=1; these
  // two fields make the toggle live in Settings. Env var still wins
  // when set (back-compat); otherwise auto_route_enabled drives.
  auto_route_enabled?: boolean | null
  auto_route_provider_pref?: 'auto' | 'local' | 'cloud' | null
}

export interface ProviderAvailability {
  available: string[]
  unavailable: string[]
  supported_types: Record<string, string[]>
}

// Model Discovery Types
export interface DiscoveredModel {
  name: string
  provider: string
  model_type: 'language' | 'embedding' | 'text_to_speech' | 'speech_to_text'
  description?: string
}

export interface ProviderSyncResult {
  provider: string
  discovered: number
  new: number
  existing: number
}

export interface AllProvidersSyncResult {
  results: Record<string, ProviderSyncResult>
  total_discovered: number
  total_new: number
}

export interface ProviderModelCount {
  provider: string
  counts: Record<string, number>
  total: number
}

export interface AutoAssignResult {
  assigned: Record<string, string>  // slot_name -> model_id
  skipped: string[]  // slots already assigned
  missing: string[]  // slots with no available models
}

export interface ModelTestResult {
  success: boolean
  message: string
  details?: string
}

/**
 * Format a human-friendly provider badge label for a model.
 * Translates generic "openai_compatible" using credential hints like
 * "LM Studio (local)", "MLX (local)", "llama.cpp (local)", "Ollama (local)".
 */
export function formatModelProviderLabel(
  model?: { provider?: string | null; credential?: string | null } | null
): string {
  if (!model) return ''
  if (model.credential) {
    const cred = model.credential.toLowerCase()
    if (cred.includes('lm studio')) return 'LM Studio'
    if (cred.includes('mlx')) return 'MLX'
    if (cred.includes('llama.cpp') || cred.includes('llamacpp')) return 'llama.cpp'
    if (cred.includes('ollama')) return 'Ollama'
  }
  const prov = (model.provider || '').toLowerCase()
  if (prov === 'openai_compatible') return 'OpenAI Compatible'
  if (prov === 'openai') return 'OpenAI'
  if (prov === 'anthropic') return 'Anthropic'
  if (prov === 'google' || prov === 'gemini') return 'Google AI'
  if (prov === 'groq') return 'Groq'
  if (prov === 'deepseek') return 'DeepSeek'
  if (prov === 'mistral') return 'Mistral AI'
  if (prov === 'openrouter') return 'OpenRouter'
  if (prov === 'ollama') return 'Ollama'
  return model.provider || ''
}