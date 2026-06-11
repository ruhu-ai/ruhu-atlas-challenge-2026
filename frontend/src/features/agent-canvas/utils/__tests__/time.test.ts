import { formatTimeAgo } from '../time'

describe('formatTimeAgo', () => {
  beforeAll(() => {
    jest.useFakeTimers({ now: new Date('2026-02-27T12:00:00Z') })
  })

  afterAll(() => {
    jest.useRealTimers()
  })

  it('shows just now for recent timestamps', () => {
    expect(formatTimeAgo(new Date('2026-02-27T11:59:40Z'))).toBe('just now')
  })

  it('shows minutes and hours correctly', () => {
    expect(formatTimeAgo(new Date('2026-02-27T11:45:00Z'))).toBe('15 min ago')
    expect(formatTimeAgo(new Date('2026-02-27T09:00:00Z'))).toBe('3 hours ago')
  })

  it('falls back to date string for older timestamps', () => {
    const date = new Date('2026-02-20T12:00:00Z')
    expect(formatTimeAgo(date)).toBe(date.toLocaleDateString('en-US'))
  })
})
