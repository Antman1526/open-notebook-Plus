import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { VaultGitHistoryDialog } from './VaultGitHistoryDialog'
import apiClient from '@/lib/api/client'

vi.mock('@/lib/api/client', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}))

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}))

describe('VaultGitHistoryDialog', () => {
  const baseProps = {
    vaultId: 'vault-456',
    vaultName: 'Obsidian Main',
    open: true,
    onOpenChange: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(apiClient.get).mockImplementation((url: string) => {
      if (url.includes('/git/history')) {
        return Promise.resolve({
          data: [
            {
              hash: 'abcdef1234567890',
              author: 'Researcher',
              date: new Date().toISOString(),
              message: 'Initial research vault snapshot',
            },
          ],
        }) as any
      }
      if (url.includes('/git/remote')) {
        return Promise.resolve({
          data: [{ name: 'origin', url: 'https://github.com/user/obsidian-vault.git' }],
        }) as any
      }
      return Promise.resolve({ data: [] }) as any
    })
  })

  it('renders commit history and remote repository status', async () => {
    render(<VaultGitHistoryDialog {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByText('Initial research vault snapshot')).toBeDefined()
      expect(screen.getByText(/Remote: https:\/\/github\.com\/user\/obsidian-vault\.git/i)).toBeDefined()
    })

    expect(screen.getByRole('button', { name: /Push/i })).toBeDefined()
    expect(screen.getByRole('button', { name: /Pull/i })).toBeDefined()
  })

  it('handles push action', async () => {
    vi.mocked(apiClient.post).mockResolvedValueOnce({
      data: { ok: true, message: 'Successfully pushed to remote' },
    } as any)

    render(<VaultGitHistoryDialog {...baseProps} />)

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Push/i })).toBeDefined()
    })

    const pushBtn = screen.getByRole('button', { name: /Push/i })
    fireEvent.click(pushBtn)

    await waitFor(() => {
      expect(apiClient.post).toHaveBeenCalledWith('/vaults/vault-456/git/push', {
        remote: 'origin',
      })
    })
  })
})
