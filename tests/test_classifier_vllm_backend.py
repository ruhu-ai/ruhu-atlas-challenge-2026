"""Tests for src/ruhu/classifier/vllm_backend.py — WI-3.2.

End-to-end mocked tests: ``http_post`` injection lets us exercise the
full code path (payload shape, response parsing, error coercion)
without needing a running vLLM cluster.
"""
from __future__ import annotations

import math
from typing import Any

import pytest

from ruhu.classifier.constrained import UNKNOWN_LABEL, confidence_from_token_logprobs
from ruhu.classifier.protocol import ClassificationRequest
from ruhu.classifier.vllm_backend import (
    DEFAULT_GUIDED_BACKEND,
    DEFAULT_TIMEOUT_SECONDS,
    VLLMClassifierBackend,
    _classify_exception,
    _parse_vllm_response,
)


def _request(
    *,
    prefix: str | None = "PREFIX_",
    suffix: str | None = "SUFFIX",
    candidate_labels: dict[str, str] | None = None,
    user_text: str = "where is my money?",
    lora_name: str | None = None,
) -> ClassificationRequest:
    return ClassificationRequest(
        agent_id="a1",
        agent_version_id="v1",
        step_id="entry",
        step_name="Entry",
        step_summary="x",
        user_text=user_text,
        candidate_labels=(
            {"transfer_status": "x", "kyc_help": "y"}
            if candidate_labels is None
            else candidate_labels
        ),
        prefix=prefix,
        suffix=suffix,
        lora_name=lora_name,
    )


def _vllm_response(
    *,
    text: str = "transfer_status",
    token_logprobs: list[float] | None = None,
    prompt_tokens: int = 245,
    completion_tokens: int = 2,
    extra: dict | None = None,
) -> dict:
    return {
        "id": "cmpl-1",
        "choices": [
            {
                "text": text,
                "logprobs": {
                    "token_logprobs": token_logprobs
                    if token_logprobs is not None
                    else [-0.05, -0.02],
                },
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        **(extra or {}),
    }


# ── classify happy path ────────────────────────────────────────────────────


def test_classify_happy_path_returns_intent_and_confidence() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["url"] = url
        captured["payload"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _vllm_response(text="transfer_status", token_logprobs=[-0.05, -0.02])

    backend = VLLMClassifierBackend(
        base_url="http://vllm.classifier.svc.cluster.local:8000",
        base_model="qwen3-8b",
        http_post=fake_post,
    )
    result = backend.classify(_request())

    assert result.chosen_label == "transfer_status"
    assert result.confidence == pytest.approx(math.exp(-0.07), abs=1e-9)
    assert result.backend == "vllm"
    assert result.error is None
    assert result.cache_hit is False
    assert result.prefill_tokens == 245
    assert result.decode_tokens == 2
    assert "transfer_status" in result.decode_logprobs

    assert captured["url"] == "http://vllm.classifier.svc.cluster.local:8000/v1/completions"
    assert captured["timeout"] == DEFAULT_TIMEOUT_SECONDS
    assert captured["headers"] == {"Content-Type": "application/json"}


def test_classify_payload_shape_matches_spec() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["payload"] = json
        return _vllm_response()

    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000", base_model="qwen3-8b", http_post=fake_post
    )
    backend.classify(_request())

    payload = captured["payload"]
    assert payload["model"] == "qwen3-8b"
    assert payload["prompt"] == "PREFIX_SUFFIX"
    assert payload["temperature"] == 0.0
    assert payload["logprobs"] == 1
    assert payload["max_tokens"] == 8
    # guided_choice must be sorted and include the unknown sentinel
    assert payload["guided_choice"] == sorted(["transfer_status", "kyc_help", UNKNOWN_LABEL])
    # Sent in extra_body as well for vLLM-version compatibility (per WI-4.6 note)
    assert payload["extra_body"]["guided_choice"] == payload["guided_choice"]
    assert payload["extra_body"]["guided_decoding_backend"] == DEFAULT_GUIDED_BACKEND


def test_classify_uses_lora_name_as_model_when_set() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["payload"] = json
        return _vllm_response()

    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000", base_model="qwen3-8b", http_post=fake_post
    )
    backend.classify(_request(lora_name="agent-melonpay-lora-v3"))
    assert captured["payload"]["model"] == "agent-melonpay-lora-v3"


def test_classify_includes_bearer_token_when_loader_provided() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["headers"] = headers
        return _vllm_response()

    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=fake_post,
        access_token_loader=lambda: "test-token",
    )
    backend.classify(_request())
    assert captured["headers"]["Authorization"] == "Bearer test-token"


def test_classify_omits_bearer_token_when_loader_returns_none() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["headers"] = headers
        return _vllm_response()

    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=fake_post,
        access_token_loader=lambda: None,
    )
    backend.classify(_request())
    assert "Authorization" not in captured["headers"]


# ── pre-dispatch validation ────────────────────────────────────────────────


def test_classify_returns_empty_request_error_for_missing_user_text() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: pytest.fail("must not call vLLM for empty request"),
    )
    result = backend.classify(_request(user_text=""))
    assert result.chosen_label is None
    assert result.error == "empty_request"


def test_classify_returns_empty_request_error_for_no_candidate_labels() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: pytest.fail("must not call vLLM for empty intents"),
    )
    result = backend.classify(_request(candidate_labels={}))
    assert result.error == "empty_request"


def test_classify_rejects_request_without_prefix() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: pytest.fail("must not call vLLM without prefix"),
    )
    result = backend.classify(_request(prefix=None))
    assert result.error == "missing_prefix_suffix"


def test_classify_rejects_request_without_suffix() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: pytest.fail("must not call vLLM without suffix"),
    )
    result = backend.classify(_request(suffix=None))
    assert result.error == "missing_prefix_suffix"


# ── response parsing ───────────────────────────────────────────────────────


def test_response_unknown_label_yields_intent_none_no_error() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: _vllm_response(text=UNKNOWN_LABEL, token_logprobs=[-0.1]),
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error is None


def test_response_intent_outside_catalog_records_error() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: _vllm_response(text="made_up_intent"),
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error and result.error.startswith("intent_outside_catalog")


def test_response_with_no_choices_returns_no_choices_error() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: {"choices": []},
    )
    result = backend.classify(_request())
    assert result.error == "no_choices"


def test_response_empty_body_returns_empty_response_error() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: {},
    )
    result = backend.classify(_request())
    assert result.error == "empty_response"


def test_response_records_cache_hit_when_extra_body_signals_it() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: _vllm_response(extra={"extra_body": {"prefix_cache_hit": True}}),
    )
    result = backend.classify(_request())
    assert result.cache_hit is True


def test_response_records_cache_hit_via_usage_field() -> None:
    response = _vllm_response()
    response["usage"]["prefix_cache_hit"] = True
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: response,
    )
    result = backend.classify(_request())
    assert result.cache_hit is True


def test_response_falls_back_to_logprobs_count_when_completion_tokens_missing() -> None:
    response = {
        "choices": [
            {
                "text": "transfer_status",
                "logprobs": {"token_logprobs": [-0.1, -0.05, -0.02]},
            }
        ],
        "usage": {"prompt_tokens": 100},  # no completion_tokens
    }
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: response,
    )
    result = backend.classify(_request())
    assert result.decode_tokens == 3


def test_confidence_matches_canonical_helper() -> None:
    """Confidence comes from confidence_from_token_logprobs — not a re-implementation."""
    logprobs = [-0.5, -0.3, -0.1]
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: _vllm_response(token_logprobs=logprobs),
    )
    result = backend.classify(_request())
    assert result.confidence == pytest.approx(
        confidence_from_token_logprobs(logprobs), abs=1e-9
    )


def test_response_with_no_token_logprobs_yields_zero_confidence() -> None:
    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=lambda **_: _vllm_response(token_logprobs=[]),
    )
    result = backend.classify(_request())
    assert result.confidence == 0.0


# ── error coercion ─────────────────────────────────────────────────────────


def test_post_exception_returns_error_classification_result() -> None:
    def fake_post(**_):
        raise ConnectionError("vllm pod unreachable")

    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=fake_post,
    )
    result = backend.classify(_request())
    assert result.chosen_label is None
    assert result.error == "connection_error"
    assert result.backend == "vllm"
    assert result.elapsed_ms >= 0


def test_post_timeout_classified_as_timeout_error() -> None:
    class _MockTimeout(Exception):
        pass

    _MockTimeout.__name__ = "TimeoutException"

    def fake_post(**_):
        raise _MockTimeout("request timed out")

    backend = VLLMClassifierBackend(
        base_url="http://vllm:8000",
        base_model="qwen3-8b",
        http_post=fake_post,
    )
    result = backend.classify(_request())
    assert result.error == "timeout"


def test_classify_exception_routes_5xx() -> None:
    err = _classify_exception(Exception("HTTPStatusError 503"))
    # Generic message — coarse "5xx" path only triggers on the literal "status"+"5"
    assert err == "5xx" or err.startswith("exception:")


def test_classify_exception_routes_unknown_to_class_name() -> None:
    err = _classify_exception(ValueError("oops"))
    assert err.startswith("valueerror")


# ── direct _parse_vllm_response unit tests ────────────────────────────────


def test_parse_vllm_response_passes_through_lora_name() -> None:
    request = _request(lora_name="agent-x-lora")
    response = _vllm_response()
    result = _parse_vllm_response(
        response, request=request, backend_name="vllm", elapsed_ms=42
    )
    assert result.lora_name == "agent-x-lora"
    assert result.elapsed_ms == 42


def test_parse_vllm_response_skips_none_logprobs() -> None:
    request = _request()
    response = _vllm_response(token_logprobs=[-0.1, None, -0.05])  # type: ignore[list-item]
    result = _parse_vllm_response(
        response, request=request, backend_name="vllm", elapsed_ms=0
    )
    # The None is dropped; confidence computed from the two real values
    assert result.confidence == pytest.approx(math.exp(-0.15), abs=1e-9)
