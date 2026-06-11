export type TranscriptSpeaker = 'user' | 'agent'

export const TRANSCRIPT_DEDUPE_WINDOW_MS = 5000
export const TRANSCRIPT_DEDUPE_CACHE_TTL_MS = 30000

export function normalizeTranscriptText(value: string): string {
  return String(value || '')
    .trim()
    .replace(/\s+/g, ' ')
    .toLowerCase()
}

function pruneExpiredFingerprints(
  cache: Map<string, number>,
  nowMs: number,
  ttlMs: number = TRANSCRIPT_DEDUPE_CACHE_TTL_MS,
) {
  for (const [key, seenAt] of cache.entries()) {
    if (nowMs - seenAt > ttlMs) {
      cache.delete(key)
    }
  }
}

export function shouldAppendTranscriptByFingerprint(
  cache: Map<string, number>,
  {
    speaker,
    text,
    nowMs,
    dedupeWindowMs = TRANSCRIPT_DEDUPE_WINDOW_MS,
  }: {
    speaker: TranscriptSpeaker
    text: string
    nowMs: number
    dedupeWindowMs?: number
  },
): boolean {
  const normalized = normalizeTranscriptText(text)
  if (!normalized) return false

  pruneExpiredFingerprints(cache, nowMs)

  const key = `${speaker}:${normalized}`
  const lastSeenAt = cache.get(key)
  if (typeof lastSeenAt === 'number' && nowMs - lastSeenAt <= dedupeWindowMs) {
    return false
  }

  cache.set(key, nowMs)
  return true
}
