from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .schemas import InteractionPacingPolicy

_PHRASE_ROOT = Path(__file__).resolve().parent / "templates" / "phrases"

_CHANNEL_DEFAULTS: dict[str, dict[str, Any]] = {
    "phone": {
        "slow_threshold_ms": 1000,
        "soft_timeout_ms": 800,
        "endpointing_ms": 650,
        "filler_repeat_gap_ms": 3500,
        "turn_eagerness": "normal",
        "interruptibility_policy": "interruptible_except_policy",
        "allow_filler": True,
        "filter_backchannels": True,
        "max_fillers_per_pending_action": 3,
    },
    "voice": {
        "slow_threshold_ms": 1000,
        "soft_timeout_ms": 800,
        "endpointing_ms": 650,
        "filler_repeat_gap_ms": 3500,
        "turn_eagerness": "normal",
        "interruptibility_policy": "interruptible_except_policy",
        "allow_filler": True,
        "filter_backchannels": True,
        "max_fillers_per_pending_action": 3,
    },
    "web_widget": {
        "slow_threshold_ms": 1200,
        "soft_timeout_ms": 800,
        "endpointing_ms": 650,
        "filler_repeat_gap_ms": 3500,
        "turn_eagerness": "normal",
        "interruptibility_policy": "interruptible_except_policy",
        "allow_filler": True,
        "filter_backchannels": True,
        "max_fillers_per_pending_action": 2,
    },
    "web_chat": {
        "slow_threshold_ms": 1500,
        "soft_timeout_ms": 1500,
        "endpointing_ms": 650,
        "filler_repeat_gap_ms": 3500,
        "turn_eagerness": "low",
        "interruptibility_policy": "always_interruptible",
        "allow_filler": False,
        "filter_backchannels": False,
        "max_fillers_per_pending_action": 0,
    },
    "whatsapp": {
        "slow_threshold_ms": 2000,
        "soft_timeout_ms": 1800,
        "endpointing_ms": 650,
        "filler_repeat_gap_ms": 3500,
        "turn_eagerness": "low",
        "interruptibility_policy": "always_interruptible",
        "allow_filler": False,
        "filter_backchannels": False,
        "max_fillers_per_pending_action": 0,
    },
    "browser": {
        "slow_threshold_ms": 1500,
        "soft_timeout_ms": 1500,
        "endpointing_ms": 650,
        "filler_repeat_gap_ms": 3500,
        "turn_eagerness": "normal",
        "interruptibility_policy": "always_interruptible",
        "allow_filler": False,
        "filter_backchannels": False,
        "max_fillers_per_pending_action": 0,
    },
}

_VOICE_BACKCHANNELS = {
    "mm-hm",
    "mmhmm",
    "mhm",
    "uh-huh",
    "uh huh",
    "hmm",
    "hm",
}


def pacing_policy_for_channel(
    channel: str | None,
    *,
    locale: str = "en",
    overrides: dict[str, Any] | None = None,
) -> InteractionPacingPolicy:
    channel_name = str(channel or "web_chat")
    payload = dict(_CHANNEL_DEFAULTS.get(channel_name, _CHANNEL_DEFAULTS["web_chat"]))
    payload.update({"channel": channel_name, "locale": locale})
    if overrides:
        payload.update(overrides)
    return InteractionPacingPolicy.model_validate(payload)


def is_voice_backchannel(text: str | None, *, locale: str = "en") -> bool:
    if locale != "en":
        return False
    normalized = " ".join(str(text or "").strip().lower().split())
    if not normalized:
        return False
    return normalized in _VOICE_BACKCHANNELS


def phrase_for(
    category: str,
    *,
    channel: str,
    locale: str = "en",
    seed: str | None = None,
) -> str | None:
    phrases = load_phrase_bank(locale=locale, channel=channel).get(category) or []
    if not phrases:
        phrases = load_phrase_bank(locale="en", channel="web_chat").get(category) or []
    if not phrases:
        return None
    if seed:
        digest = hashlib.sha256(f"{category}:{seed}".encode()).hexdigest()
        index = int(digest[:8], 16) % len(phrases)
    else:
        index = 0
    return str(phrases[index])


@lru_cache(maxsize=32)
def load_phrase_bank(*, locale: str, channel: str) -> dict[str, list[str]]:
    candidates = [
        _PHRASE_ROOT / locale / f"{channel}.json",
        _PHRASE_ROOT / locale / "web_chat.json",
        _PHRASE_ROOT / "en" / f"{channel}.json",
        _PHRASE_ROOT / "en" / "web_chat.json",
    ]
    for path in candidates:
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            return {
                str(key): [str(item) for item in value]
                for key, value in dict(payload).items()
                if isinstance(value, list)
            }
    return {}
