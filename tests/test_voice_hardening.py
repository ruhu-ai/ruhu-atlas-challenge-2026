"""
Tests for Phase 7: Voice Hardening.

Covers:
  7a — HMAC room metadata signing (sign_room_metadata / verify_room_metadata)
  7b — VoiceConcurrencyLimiter (Redis SADD/SCARD slot reservation)
  7c — Transcript deduplication (fingerprint / check_and_record)
"""
from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from ruhu.livekit_adapter import sign_room_metadata, verify_room_metadata
from ruhu.voice.concurrency import VoiceCapacityExceededError, VoiceConcurrencyLimiter
from ruhu.voice.transcript_dedup import (
    _DEFAULT_WINDOW,
    _MAX_CACHE_KEYS,
    check_and_record,
    fingerprint,
)

_SECRET = "test-hmac-secret-32bytes-long-xx"


# ── 7a: HMAC Room Metadata Signing ────────────────────────────────────────────

class TestSignRoomMetadata:
    def test_produces_valid_json_envelope(self) -> None:
        signed = sign_room_metadata({"organization_id": "org-1"}, _SECRET)
        envelope = json.loads(signed)
        assert "p" in envelope
        assert "s" in envelope

    def test_payload_includes_iat_and_exp(self) -> None:
        before = int(time.time())
        signed = sign_room_metadata({"organization_id": "org-1"}, _SECRET, exp_seconds=300)
        after = int(time.time())
        payload = json.loads(signed)["p"]
        assert before <= payload["iat"] <= after
        assert payload["exp"] == payload["iat"] + 300

    def test_original_payload_fields_preserved(self) -> None:
        signed = sign_room_metadata(
            {"organization_id": "org-1", "agent_id": "agent-x"}, _SECRET
        )
        payload = json.loads(signed)["p"]
        assert payload["organization_id"] == "org-1"
        assert payload["agent_id"] == "agent-x"

    def test_signature_is_64_hex_chars(self) -> None:
        signed = sign_room_metadata({"x": 1}, _SECRET)
        sig = json.loads(signed)["s"]
        assert len(sig) == 64
        assert all(c in "0123456789abcdef" for c in sig)

    def test_different_secrets_produce_different_signatures(self) -> None:
        payload = {"organization_id": "org-1"}
        sig_a = json.loads(sign_room_metadata(payload, "secret-A"))["s"]
        sig_b = json.loads(sign_room_metadata(payload, "secret-B"))["s"]
        assert sig_a != sig_b


class TestVerifyRoomMetadata:
    def test_round_trip_verify(self) -> None:
        signed = sign_room_metadata(
            {"organization_id": "org-1", "agent_id": "agent-x"}, _SECRET
        )
        result = verify_room_metadata(signed, _SECRET)
        assert result["organization_id"] == "org-1"
        assert result["agent_id"] == "agent-x"

    def test_tampered_signature_raises(self) -> None:
        signed = sign_room_metadata({"organization_id": "org-1"}, _SECRET)
        envelope = json.loads(signed)
        envelope["s"] = "a" * 64  # corrupt the signature
        tampered = json.dumps(envelope)
        with pytest.raises(ValueError, match="signature mismatch"):
            verify_room_metadata(tampered, _SECRET)

    def test_tampered_payload_raises(self) -> None:
        signed = sign_room_metadata({"organization_id": "org-1"}, _SECRET)
        envelope = json.loads(signed)
        envelope["p"]["organization_id"] = "evil-org"
        tampered = json.dumps(envelope)
        with pytest.raises(ValueError, match="signature mismatch"):
            verify_room_metadata(tampered, _SECRET)

    def test_wrong_secret_raises(self) -> None:
        signed = sign_room_metadata({"organization_id": "org-1"}, _SECRET)
        with pytest.raises(ValueError, match="signature mismatch"):
            verify_room_metadata(signed, "wrong-secret")

    def test_expired_token_raises(self) -> None:
        signed = sign_room_metadata({"organization_id": "org-1"}, _SECRET, exp_seconds=-1)
        with pytest.raises(ValueError, match="expired"):
            verify_room_metadata(signed, _SECRET)

    def test_malformed_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            verify_room_metadata("not-json", _SECRET)

    def test_missing_envelope_key_raises(self) -> None:
        with pytest.raises(KeyError):
            verify_room_metadata(json.dumps({"p": {"iat": 0, "exp": 99999999999}}), _SECRET)


# ── 7b: VoiceConcurrencyLimiter ────────────────────────────────────────────────

def _make_limiter_with_redis(max_per_org: int = 3) -> tuple[VoiceConcurrencyLimiter, MagicMock]:
    """Return a limiter wired to a mocked aioredis client."""
    limiter = VoiceConcurrencyLimiter("redis://localhost", max_per_org=max_per_org)

    mock_redis = AsyncMock()
    mock_redis.srem = AsyncMock()
    mock_redis.scard = AsyncMock(return_value=2)

    limiter._redis = mock_redis
    return limiter, mock_redis


class TestVoiceConcurrencyLimiterReserve:
    def test_reserve_succeeds_when_slot_available(self) -> None:
        limiter, mock_redis = _make_limiter_with_redis(max_per_org=5)
        mock_redis.eval = AsyncMock(return_value=1)

        async def run():
            await limiter.reserve("org-1", "tok-1")

        anyio.run(run)
        mock_redis.eval.assert_awaited_once()

    def test_reserve_passes_correct_key_and_args(self) -> None:
        limiter, mock_redis = _make_limiter_with_redis(max_per_org=5)
        mock_redis.eval = AsyncMock(return_value=1)

        async def run():
            await limiter.reserve("org-abc", "tok-xyz")

        anyio.run(run)
        call_args = mock_redis.eval.await_args
        # KEYS[1]
        assert call_args.args[2] == "voice_active:org-abc"
        # ARGV[1] — session token
        assert call_args.args[3] == "tok-xyz"
        # ARGV[2] — max
        assert call_args.args[4] == "5"

    def test_reserve_raises_when_at_capacity(self) -> None:
        limiter, mock_redis = _make_limiter_with_redis(max_per_org=3)
        mock_redis.eval = AsyncMock(return_value=0)

        async def run():
            await limiter.reserve("org-full", "tok-new")

        with pytest.raises(VoiceCapacityExceededError):
            anyio.run(run)

    def test_key_is_scoped_per_org(self) -> None:
        limiter = VoiceConcurrencyLimiter("redis://localhost", max_per_org=5)
        assert limiter._key("org-A") == "voice_active:org-A"
        assert limiter._key("org-B") == "voice_active:org-B"
        assert limiter._key("org-A") != limiter._key("org-B")


class TestVoiceConcurrencyLimiterRelease:
    def test_release_calls_srem(self) -> None:
        limiter, mock_redis = _make_limiter_with_redis()

        async def run():
            await limiter.release("org-1", "tok-1")

        anyio.run(run)
        mock_redis.srem.assert_awaited_once_with("voice_active:org-1", "tok-1")

    def test_release_tolerates_redis_error(self) -> None:
        limiter, mock_redis = _make_limiter_with_redis()
        mock_redis.srem = AsyncMock(side_effect=ConnectionError("redis down"))

        async def run():
            # Must not raise
            await limiter.release("org-1", "tok-1")

        anyio.run(run)  # must not raise


class TestVoiceConcurrencyLimiterActiveCount:
    def test_active_count_returns_scard_result(self) -> None:
        limiter, mock_redis = _make_limiter_with_redis()
        mock_redis.scard = AsyncMock(return_value=4)

        async def run():
            return await limiter.active_count("org-1")

        count = anyio.run(run)
        assert count == 4
        mock_redis.scard.assert_awaited_once_with("voice_active:org-1")


# ── 7c: Transcript Deduplication ──────────────────────────────────────────────

class TestFingerprint:
    def test_deterministic(self) -> None:
        assert fingerprint("Hello World") == fingerprint("Hello World")

    def test_case_insensitive(self) -> None:
        assert fingerprint("Hello WORLD") == fingerprint("hello world")

    def test_whitespace_normalised(self) -> None:
        assert fingerprint("  hello   world  ") == fingerprint("hello world")

    def test_empty_returns_empty_string(self) -> None:
        assert fingerprint("") == ""
        assert fingerprint("   ") == ""
        assert fingerprint(None) == ""  # type: ignore[arg-type]

    def test_returns_16_hex_chars(self) -> None:
        fp = fingerprint("some transcript text")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_different_texts_produce_different_fingerprints(self) -> None:
        assert fingerprint("hello") != fingerprint("world")


class TestCheckAndRecord:
    def test_first_occurrence_is_not_duplicate(self) -> None:
        cache: dict = {}
        is_dup, fp = check_and_record(cache, "hello", now=100.0)
        assert is_dup is False
        assert fp != ""

    def test_second_occurrence_within_window_is_duplicate(self) -> None:
        cache: dict = {}
        check_and_record(cache, "hello", now=100.0)
        is_dup, _ = check_and_record(cache, "hello", now=101.0, window_seconds=2.0)
        assert is_dup is True

    def test_occurrence_after_window_is_not_duplicate(self) -> None:
        cache: dict = {}
        check_and_record(cache, "hello", now=100.0)
        is_dup, _ = check_and_record(cache, "hello", now=103.0, window_seconds=2.0)
        assert is_dup is False

    def test_different_texts_are_independent(self) -> None:
        cache: dict = {}
        check_and_record(cache, "hello", now=100.0)
        is_dup, _ = check_and_record(cache, "world", now=100.5)
        assert is_dup is False

    def test_empty_text_never_deduped(self) -> None:
        cache: dict = {}
        is_dup, fp = check_and_record(cache, "", now=100.0)
        assert is_dup is False
        assert fp == ""
        # Second call also not a duplicate
        is_dup2, _ = check_and_record(cache, "", now=100.5)
        assert is_dup2 is False

    def test_cache_fingerprint_entries_cleaned_up_when_over_max(self) -> None:
        cache: dict = {}
        base_time = 1000.0
        # Fill cache beyond MAX_CACHE_KEYS with distinct texts
        for i in range(_MAX_CACHE_KEYS + 10):
            check_and_record(cache, f"text-{i:04d}", now=base_time)
        # Trigger pruning by adding one more entry far in the future
        check_and_record(cache, "new-text", now=base_time + _DEFAULT_WINDOW + 1)
        # Stale entries should have been pruned
        assert len(cache) <= _MAX_CACHE_KEYS + 1

    def test_timestamps_outside_window_pruned_from_cache_entry(self) -> None:
        cache: dict = {}
        # Two occurrences far apart
        check_and_record(cache, "hello", now=100.0)
        check_and_record(cache, "hello", now=200.0, window_seconds=2.0)
        fp = fingerprint("hello")
        # Only the second timestamp should be in the list
        assert cache[fp] == [200.0]

    def test_window_seconds_override(self) -> None:
        cache: dict = {}
        check_and_record(cache, "hi", now=100.0)
        # 0.5 s window — a second occurrence at 100.3 should be a duplicate
        is_dup, _ = check_and_record(cache, "hi", now=100.3, window_seconds=0.5)
        assert is_dup is True
        # At 101.0 both prior timestamps (100.0, 100.3) are outside the 0.5 s window
        is_dup2, _ = check_and_record(cache, "hi", now=101.0, window_seconds=0.5)
        assert is_dup2 is False

    def test_uses_monotonic_clock_by_default(self) -> None:
        """When now is not provided, the function should use time.monotonic."""
        cache: dict = {}
        with patch("ruhu.voice.transcript_dedup.time") as mock_time:
            mock_time.monotonic.return_value = 500.0
            check_and_record(cache, "hello")
        fp = fingerprint("hello")
        assert cache[fp] == [500.0]
