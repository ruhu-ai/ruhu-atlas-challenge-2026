"""
Transcript deduplication for voice sessions.

Prevents the same transcript text from triggering multiple kernel invocations
within a short time window (default 2 s). This handles cases where the STT
engine emits identical or near-identical transcripts for the same utterance.

Usage::

    from ruhu.voice.transcript_dedup import check_and_record

    # One cache dict per voice room, lives for the session lifetime.
    _dedup_cache: dict[str, list[float]] = {}

    # On each transcript event:
    is_dup, fp = check_and_record(_dedup_cache, transcript_text)
    if is_dup:
        voice_transcript_duplicates_suppressed_total.inc()
        return  # skip kernel invocation
"""
from __future__ import annotations

import hashlib
import time
from typing import Optional

_DEFAULT_WINDOW = 2.0    # seconds — duplicates within this window are suppressed
_MAX_CACHE_KEYS = 256    # per-room upper bound; prevents unbounded memory growth


def fingerprint(text: str) -> str:
    """Return a stable fingerprint for a transcript (first 16 hex chars of SHA-256).

    Normalisation: strip whitespace, lower-case, collapse internal whitespace.
    Empty or whitespace-only text returns an empty string.
    """
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def check_and_record(
    cache: dict[str, list[float]],
    text: str,
    *,
    now: Optional[float] = None,
    window_seconds: float = _DEFAULT_WINDOW,
) -> tuple[bool, str]:
    """Check whether ``text`` is a duplicate within the deduplication window.

    Returns ``(is_duplicate, fingerprint)``. The cache is mutated in place.
    Pass a per-session dict so the cache is naturally scoped to one voice room.

    Args:
        cache: Per-session fingerprint → [timestamp, ...] map.
        text: Raw transcript text to check.
        now: Monotonic timestamp override for testing; defaults to
             ``time.monotonic()``.
        window_seconds: Suppress duplicates seen within this many seconds.

    Returns:
        A 2-tuple ``(is_dup, fp)`` where ``is_dup`` is True if the same text
        was seen within ``window_seconds``, and ``fp`` is the fingerprint
        (empty string if ``text`` was empty/whitespace).
    """
    fp = fingerprint(text)
    if not fp:
        return (False, fp)

    t = now if now is not None else time.monotonic()
    cutoff = t - window_seconds

    # Keep only timestamps within the current window
    prior = [ts for ts in cache.get(fp, []) if ts >= cutoff]
    is_dup = len(prior) > 0
    prior.append(t)
    cache[fp] = prior

    # Prune stale keys to keep per-room memory bounded
    if len(cache) > _MAX_CACHE_KEYS:
        stale = [k for k, v in cache.items() if not v or v[-1] < cutoff]
        for k in stale:
            cache.pop(k, None)

    return (is_dup, fp)
