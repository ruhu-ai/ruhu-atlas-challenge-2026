import { webcrypto } from 'node:crypto'

const mockPost = jest.fn()

jest.mock('@/api/client', () => ({
  apiClient: {
    get: jest.fn(),
    post: (...args: unknown[]) => mockPost(...args),
    patch: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
  },
}))

jest.mock('@/api/services/auth.service', () => ({
  meToUser: jest.fn(),
}))

import { settingsService } from '@/api/services/settings.service'

describe('settingsService.createApiKey', () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, 'crypto', {
      value: webcrypto,
      configurable: true,
    })
  })

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('generates the plaintext locally and posts only hash and prefix metadata', async () => {
    mockPost.mockResolvedValue({
      key_id: 'key-1',
      name: 'CI key',
      key_prefix: 'sk_live_01234567',
      is_active: true,
      created_at: '2026-04-17T12:00:00Z',
      last_used_at: null,
    })

    const created = await settingsService.createApiKey('CI key')

    expect(mockPost).toHaveBeenCalledTimes(1)
    const [endpoint, payload] = mockPost.mock.calls[0]
    expect(endpoint).toBe('/api-keys')
    expect(payload).toMatchObject({
      name: 'CI key',
      key_prefix: expect.stringMatching(/^sk_live_[0-9a-f]{8}$/),
      key_hash: expect.stringMatching(/^[0-9a-f]{64}$/),
    })
    expect(created).toMatchObject({
      key_id: 'key-1',
      name: 'CI key',
      is_active: true,
      key_prefix: 'sk_live_01234567',
    })
    expect(created.key).toMatch(/^sk_live_[0-9a-f]{64}$/)
  })
})
