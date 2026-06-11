from __future__ import annotations

from ruhu.interaction_pacing import (
    is_voice_backchannel,
    load_phrase_bank,
    pacing_policy_for_channel,
    phrase_for,
)


def test_pacing_policy_for_phone_uses_voice_defaults() -> None:
    policy = pacing_policy_for_channel("phone")
    assert policy.slow_threshold_ms == 1000
    assert policy.soft_timeout_ms == 800
    assert policy.filter_backchannels is True


def test_phrase_for_loads_channel_specific_bank() -> None:
    phrase = phrase_for("interrupt_ack", channel="phone", seed="abc")
    assert phrase in {
        "Okay, I stopped that.",
        "Understood, I’ve stopped that.",
    }


def test_load_phrase_bank_falls_back_to_web_chat() -> None:
    bank = load_phrase_bank(locale="en", channel="web_widget")
    assert "interrupt_ack" in bank


def test_is_voice_backchannel_detects_narrow_acknowledgements() -> None:
    assert is_voice_backchannel("mm-hm") is True
    assert is_voice_backchannel("uh huh") is True
    assert is_voice_backchannel("yes") is False
    assert is_voice_backchannel("cancel it") is False
