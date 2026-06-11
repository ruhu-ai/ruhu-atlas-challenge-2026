"""Tests for src/ruhu/classifier/benchmark/probe_vllm_api.py — WI-4.6."""
from __future__ import annotations

import json
from typing import Any

import pytest

from ruhu.classifier.benchmark.probe_vllm_api import (
    DEFAULT_PROBE_INTENTS,
    ProbeReport,
    probe,
)


def _completions_response(
    *,
    text: str = "transfer_status",
    logprobs: list[float] | None = None,
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "text": text,
                "logprobs": {"token_logprobs": logprobs if logprobs is not None else [-0.05]},
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 2},
    }


def _chat_response(*, intent: str = "transfer_status") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"content": json.dumps({"intent": intent})},
                "logprobs": {"content": [{"token": intent, "logprob": -0.05}]},
            }
        ]
    }


# ── recommendation logic ──────────────────────────────────────────────────


def test_probe_recommends_variant_with_intent_in_catalog_and_logprobs() -> None:
    """All four variants succeed with logprobs → first listed wins on tiebreak."""

    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return _chat_response()
        return _completions_response()

    report = probe(
        base_url="http://vllm:8000",
        model="Qwen/Qwen3-8B",
        http_post=fake_post,
    )
    assert report.recommended_variant in {
        "completions_top_level",
        "completions_extra_body",
        "completions_both",
        "chat_response_format",
    }
    assert all(v.intent_in_catalog for v in report.variants)


def test_probe_recommends_extra_body_when_top_level_returns_unknown() -> None:
    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return _chat_response()
        # Top-level guided_choice ignored — returns 'unknown' (out of catalog
        # for our probe). Extra-body works.
        if "guided_choice" in json and "extra_body" in json:
            # both — pretend backend honours top-level only when intent is also
            # in extra_body
            return _completions_response(text="transfer_status")
        if "extra_body" in json and "guided_choice" in json["extra_body"]:
            return _completions_response(text="transfer_status")
        return _completions_response(text="some_garbage_not_in_catalog")

    report = probe(
        base_url="http://vllm:8000",
        model="Qwen/Qwen3-8B",
        http_post=fake_post,
    )
    top_level = next(v for v in report.variants if v.name == "completions_top_level")
    extra_body = next(v for v in report.variants if v.name == "completions_extra_body")
    assert top_level.intent_in_catalog is False
    assert extra_body.intent_in_catalog is True
    assert report.recommended_variant != "completions_top_level"


def test_probe_returns_no_recommendation_when_all_variants_fail() -> None:
    def fake_post(**_):
        raise ConnectionError("vllm pod unreachable")

    report = probe(
        base_url="http://vllm:8000",
        model="Qwen/Qwen3-8B",
        http_post=fake_post,
    )
    assert report.recommended_variant is None
    assert all(v.error and "ConnectionError" in v.error for v in report.variants)
    assert any("no variant succeeded" in note for note in report.notes)


def test_probe_returns_no_recommendation_when_intent_outside_catalog() -> None:
    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return _chat_response(intent="bogus_intent")
        return _completions_response(text="bogus_intent")

    report = probe(
        base_url="http://vllm:8000",
        model="Qwen/Qwen3-8B",
        http_post=fake_post,
    )
    assert report.recommended_variant is None


# ── variant scoring details ──────────────────────────────────────────────


def test_probe_prefers_variant_with_logprobs_over_no_logprobs() -> None:
    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return _chat_response()
        # Top-level: no logprobs in response
        if "extra_body" not in json:
            return {
                "choices": [{"text": "transfer_status", "logprobs": {"token_logprobs": []}}]
            }
        # Extra-body: full logprobs
        return _completions_response()

    report = probe(
        base_url="http://vllm:8000",
        model="Qwen/Qwen3-8B",
        http_post=fake_post,
    )
    # extra_body wins because it has logprobs
    assert report.recommended_variant != "completions_top_level"
    no_lp = next(v for v in report.variants if v.name == "completions_top_level")
    assert no_lp.logprobs_present is False


def test_probe_runs_all_four_variants() -> None:
    seen: list[str] = []

    def fake_post(*, url, json, timeout):
        seen.append(url)
        if url.endswith("/v1/chat/completions"):
            return _chat_response()
        return _completions_response()

    probe(
        base_url="http://vllm:8000",
        model="m",
        http_post=fake_post,
    )
    assert seen.count("http://vllm:8000/v1/completions") == 3
    assert seen.count("http://vllm:8000/v1/chat/completions") == 1


def test_probe_uses_supplied_intents() -> None:
    captured: list[dict[str, Any]] = []

    def fake_post(*, url, json, timeout):
        captured.append(json)
        if url.endswith("/v1/chat/completions"):
            return _chat_response(intent="custom_b")
        return _completions_response(text="custom_b")

    probe(
        base_url="http://vllm:8000",
        model="m",
        intents=["custom_a", "custom_b", "custom_c"],
        http_post=fake_post,
    )
    # First two completion variants put guided_choice somewhere
    found = False
    for payload in captured:
        if payload.get("guided_choice") == ["custom_a", "custom_b", "custom_c"]:
            found = True
        elif (payload.get("extra_body") or {}).get("guided_choice") == [
            "custom_a", "custom_b", "custom_c"
        ]:
            found = True
    assert found


def test_probe_records_elapsed_ms_per_variant() -> None:
    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return _chat_response()
        return _completions_response()

    report = probe(
        base_url="http://vllm:8000",
        model="m",
        http_post=fake_post,
    )
    assert all(v.elapsed_ms >= 0 for v in report.variants)


def test_probe_includes_no_logprobs_note_when_relevant() -> None:
    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return {"choices": [{"message": {"content": json_dumps_intent("transfer_status")}}]}
        return _completions_response()

    report = probe(
        base_url="http://vllm:8000",
        model="m",
        http_post=fake_post,
    )
    notes = " ".join(report.notes).lower()
    assert "logprobs" in notes


def json_dumps_intent(name: str) -> str:
    return json.dumps({"intent": name})


# ── default catalog sanity ────────────────────────────────────────────────


def test_default_probe_intents_includes_unknown_sentinel() -> None:
    assert "unknown" in DEFAULT_PROBE_INTENTS


# ── jsonable serialisation ───────────────────────────────────────────────


def test_probe_report_serialises_to_json_safely() -> None:
    def fake_post(*, url, json, timeout):
        if url.endswith("/v1/chat/completions"):
            return _chat_response()
        return _completions_response()

    report = probe(
        base_url="http://vllm:8000",
        model="m",
        http_post=fake_post,
    )
    from ruhu.classifier.benchmark.probe_vllm_api import _to_jsonable

    payload = _to_jsonable(report)
    encoded = json.dumps(payload)
    assert "completions_top_level" in encoded
    assert "recommended_variant" in encoded
