import { describe, expect, it } from 'vitest'
import { formatModelProviderLabel } from './models'

describe('formatModelProviderLabel', () => {
  it('returns empty string when model is null or undefined', () => {
    expect(formatModelProviderLabel(null)).toBe('')
    expect(formatModelProviderLabel(undefined)).toBe('')
  })

  it('translates LM Studio credential', () => {
    expect(formatModelProviderLabel({
      provider: 'openai_compatible',
      credential: 'LM Studio (local)',
    })).toBe('LM Studio')
  })

  it('translates MLX credential', () => {
    expect(formatModelProviderLabel({
      provider: 'openai_compatible',
      credential: 'MLX (local)',
    })).toBe('MLX')
  })

  it('translates llama.cpp credential', () => {
    expect(formatModelProviderLabel({
      provider: 'openai_compatible',
      credential: 'llama.cpp (local)',
    })).toBe('llama.cpp')
  })

  it('translates Ollama credential', () => {
    expect(formatModelProviderLabel({
      provider: 'openai_compatible',
      credential: 'Ollama (local)',
    })).toBe('Ollama')
  })

  it('formats standard cloud providers', () => {
    expect(formatModelProviderLabel({ provider: 'openai' })).toBe('OpenAI')
    expect(formatModelProviderLabel({ provider: 'anthropic' })).toBe('Anthropic')
    expect(formatModelProviderLabel({ provider: 'google' })).toBe('Google AI')
    expect(formatModelProviderLabel({ provider: 'gemini' })).toBe('Google AI')
    expect(formatModelProviderLabel({ provider: 'groq' })).toBe('Groq')
    expect(formatModelProviderLabel({ provider: 'deepseek' })).toBe('DeepSeek')
    expect(formatModelProviderLabel({ provider: 'mistral' })).toBe('Mistral AI')
    expect(formatModelProviderLabel({ provider: 'openrouter' })).toBe('OpenRouter')
    expect(formatModelProviderLabel({ provider: 'ollama' })).toBe('Ollama')
  })

  it('falls back to OpenAI Compatible when no credential hint matches', () => {
    expect(formatModelProviderLabel({ provider: 'openai_compatible' })).toBe('OpenAI Compatible')
  })
})
