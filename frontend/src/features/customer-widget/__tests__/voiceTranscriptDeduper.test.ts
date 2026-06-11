import {
  normalizeTranscriptText,
  shouldAppendTranscriptByFingerprint,
  TRANSCRIPT_DEDUPE_WINDOW_MS,
} from '../voiceTranscriptDeduper'

describe('voiceTranscriptDeduper', () => {
  it('normalizes whitespace and case consistently', () => {
    expect(normalizeTranscriptText('  Hello   WORLD  ')).toBe('hello world')
  })

  it('drops duplicate transcript within dedupe window', () => {
    const cache = new Map<string, number>()
    const t0 = 1_000

    expect(
      shouldAppendTranscriptByFingerprint(cache, {
        speaker: 'user',
        text: 'Hello, can you hear me?',
        nowMs: t0,
      }),
    ).toBe(true)

    expect(
      shouldAppendTranscriptByFingerprint(cache, {
        speaker: 'user',
        text: 'hello,   can you hear me?',
        nowMs: t0 + 500,
      }),
    ).toBe(false)
  })

  it('allows same text after dedupe window', () => {
    const cache = new Map<string, number>()
    const t0 = 2_000

    expect(
      shouldAppendTranscriptByFingerprint(cache, {
        speaker: 'user',
        text: 'hello there',
        nowMs: t0,
      }),
    ).toBe(true)

    expect(
      shouldAppendTranscriptByFingerprint(cache, {
        speaker: 'user',
        text: 'hello there',
        nowMs: t0 + TRANSCRIPT_DEDUPE_WINDOW_MS + 1,
      }),
    ).toBe(true)
  })

  it('does not dedupe across speakers', () => {
    const cache = new Map<string, number>()
    const t0 = 3_000

    expect(
      shouldAppendTranscriptByFingerprint(cache, {
        speaker: 'user',
        text: 'hello',
        nowMs: t0,
      }),
    ).toBe(true)

    expect(
      shouldAppendTranscriptByFingerprint(cache, {
        speaker: 'agent',
        text: 'hello',
        nowMs: t0 + 200,
      }),
    ).toBe(true)
  })
})

