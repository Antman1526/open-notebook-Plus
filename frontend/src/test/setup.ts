import '@testing-library/jest-dom'
import { vi } from 'vitest'

// Mock next/navigation
vi.mock('next/navigation', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    prefetch: vi.fn(),
  }),
  usePathname: () => '',
  useSearchParams: () => new URLSearchParams(),
}))

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // Deprecated
    removeListener: vi.fn(), // Deprecated
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// Mock localStorage for environments like Node 22 where built-in localStorage is incomplete/unconfigured in tests
const createStorageMock = () => {
  let store: Record<string, string> = {}
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value ? value.toString() : ''
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key]
    }),
    clear: vi.fn(() => {
      store = {}
    }),
    key: vi.fn((index: number) => Object.keys(store)[index] ?? null),
    get length() {
      return Object.keys(store).length
    },
  }
}

const mockStorage = createStorageMock()
Object.defineProperty(window, 'localStorage', {
  value: mockStorage,
  configurable: true,
  writable: true,
})
try {
  Object.defineProperty(globalThis, 'localStorage', {
    value: mockStorage,
    configurable: true,
    writable: true,
  })
} catch {
  // Ignore if globalThis.localStorage is not configurable
}

// React Flow observes its viewport before laying out a mind map. JSDOM does
// not implement this browser API, so provide the smallest no-op test double.
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverMock)

// Mock @/lib/hooks/use-translation with standard t() function
vi.mock('../lib/hooks/use-translation', () => {
  return {
    useTranslation: () => ({
      t: (key: string) => key,
      language: 'en-US',
      setLanguage: vi.fn(),
    }),
  }
})

// Mock @/lib/hooks/use-auth
vi.mock('@/lib/hooks/use-auth', () => ({
  useAuth: vi.fn(() => ({
    user: { id: '1', email: 'test@example.com' },
    logout: vi.fn(),
    isLoading: false,
  })),
}))

// Mock @/lib/stores/sidebar-store
vi.mock('@/lib/stores/sidebar-store', () => ({
  useSidebarStore: vi.fn(() => ({
    isCollapsed: false,
    toggleCollapse: vi.fn(),
  })),
}))

// Mock @/lib/hooks/use-create-dialogs
vi.mock('@/lib/hooks/use-create-dialogs', () => ({
  useCreateDialogs: vi.fn(() => ({
    openSourceDialog: vi.fn(),
    openNotebookDialog: vi.fn(),
    openPodcastDialog: vi.fn(),
  })),
}))
