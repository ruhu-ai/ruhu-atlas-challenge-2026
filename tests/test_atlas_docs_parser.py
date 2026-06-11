"""Tests for the LLM-driven HTML API docs parser used by Atlas.

These tests exercise ``AtlasDocsPageParser.parse(...)`` against a mocked
Anthropic endpoint. They also exercise the dispatcher in
``atlas_provisioning._docs_page_result`` to confirm:

* When the parser is configured AND succeeds → ``spec_type="llm_parsed"``
* When the parser fails (network / 4xx / malformed JSON / missing api_key)
  → falls back to the regex heuristic, returning ``spec_type="heuristic"``
"""
from __future__ import annotations

import json

import httpx
import pytest

from ruhu.atlas_docs_parser import (
    AtlasDocsPageEndpoint,
    AtlasDocsPageExtraction,
    AtlasDocsPageParser,
    _extract_json_object,
    _normalize_extraction,
    _strip_html_for_llm,
)
from ruhu.atlas_protocol import AtlasAPIDiscoveryRequest
from ruhu.atlas_provisioning import _docs_page_result, discovery_result_for_request
from ruhu.observability.metrics import (
    atlas_docs_parser_fallback_total,
    atlas_docs_parser_requests_total,
)


# ── Helpers ──────────────────────────────────────────────────────────


def _requests_count(outcome: str) -> float:
    return atlas_docs_parser_requests_total.labels(
        provider="anthropic", model="claude-test", outcome=outcome
    )._value.get()


def _fallback_count(reason: str) -> float:
    return atlas_docs_parser_fallback_total.labels(reason=reason)._value.get()


_REQUEST_OUTCOMES = ("ok", "parse_error", "empty", "http_4xx", "http_5xx", "network", "error")
_FALLBACK_REASONS = (
    "missing_api_key",
    "empty_body",
    "empty_after_cleaning",
    "http_error",
    "network_error",
    "empty_result",
    "unexpected_error",
    "parse_error",
)


def _metric_snapshot() -> dict[str, float]:
    snapshot = {f"requests:{outcome}": _requests_count(outcome) for outcome in _REQUEST_OUTCOMES}
    snapshot.update({f"fallback:{reason}": _fallback_count(reason) for reason in _FALLBACK_REASONS})
    return snapshot


def _metric_deltas(before: dict[str, float]) -> dict[str, float]:
    """Non-zero metric increments since ``before``, keyed like the snapshot."""
    after = _metric_snapshot()
    return {key: after[key] - before[key] for key in before if after[key] != before[key]}


def _make_anthropic_response(payload: dict) -> httpx.Response:
    """Build an Anthropic-shaped /v1/messages response with a JSON payload
    in the assistant text block."""
    body = {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": json.dumps(payload)}],
        "model": "claude-test",
        "stop_reason": "end_turn",
    }
    return httpx.Response(200, json=body)


def _build_parser(handler) -> AtlasDocsPageParser:
    """Construct a parser with the Anthropic httpx call routed to ``handler``.

    Uses the parser's ``transport`` test seam, so the REAL
    ``_call_anthropic`` runs (including its metric/outcome contract)
    against an ``httpx.MockTransport``.
    """
    return AtlasDocsPageParser(
        api_key="sk-test-anthropic",
        model="claude-test",
        timeout_seconds=2.0,
        max_retries=0,
        retry_backoff_seconds=0.0,
        transport=httpx.MockTransport(handler),
    )


_SAMPLE_HTML = """
<html><head><title>Acme API Docs</title></head>
<body>
<h1>Acme API</h1>
<p>Authentication: provide your API Key in the X-API-Key header.</p>
<h2>GET /users</h2><p>List all users.</p>
<h2>POST /users</h2><p>Create a user.</p>
</body></html>
"""


# ── Pure-function tests ──────────────────────────────────────────────


def test_strip_html_drops_script_and_collapses_whitespace() -> None:
    html = "<html><script>alert('x')</script><body>Hello   <b>world</b>\n\n</body></html>"
    cleaned = _strip_html_for_llm(html)
    assert "alert" not in cleaned
    assert "Hello world" in cleaned


def test_strip_html_truncates_huge_input() -> None:
    huge = "<body>" + ("a" * 200_000) + "</body>"
    cleaned = _strip_html_for_llm(huge)
    assert len(cleaned) <= 60_000


def test_extract_json_object_finds_balanced_braces_in_noisy_text() -> None:
    text = 'Here is the JSON:\n{"foo": 1, "bar": {"nested": true}}\nThanks!'
    extracted = _extract_json_object(text)
    assert extracted == '{"foo": 1, "bar": {"nested": true}}'


def test_extract_json_object_returns_none_when_no_object() -> None:
    assert _extract_json_object("just plain text, no braces") is None


def test_normalize_drops_endpoints_with_invalid_method() -> None:
    raw = {
        "provider_name": "Acme",
        "endpoints": [
            {"method": "GET", "path": "/users"},
            {"method": "TRACE", "path": "/legacy"},  # not in allowed set
            {"method": "POST", "path": "missing-leading-slash"},  # rejected
        ],
    }
    extraction = _normalize_extraction(raw)
    assert len(extraction.endpoints) == 1
    assert extraction.endpoints[0].method == "GET"


def test_normalize_filters_unknown_auth_field_values() -> None:
    raw = {
        "endpoints": [{"method": "GET", "path": "/x"}],
        "missing_auth_fields": ["api_key", "Bearer-Token", "fictional_auth"],
    }
    extraction = _normalize_extraction(raw)
    # "Bearer-Token" → normalized lowercase + dash-to-underscore → "bearer_token"
    assert extraction.missing_auth_fields == ["api_key", "bearer_token"]


def test_normalize_caps_endpoints_at_50() -> None:
    raw = {
        "endpoints": [
            {"method": "GET", "path": f"/r/{i}"} for i in range(80)
        ]
    }
    extraction = _normalize_extraction(raw)
    assert len(extraction.endpoints) == 50


# ── Parser end-to-end ────────────────────────────────────────────────


def test_parser_extracts_endpoints_from_anthropic_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _make_anthropic_response({
            "provider_name": "Acme API",
            "base_url": "https://api.acme.com/v1",
            "endpoints": [
                {"method": "GET", "path": "/users", "operation_id": "listUsers", "summary": "List users", "requires_auth": True},
                {"method": "POST", "path": "/users", "operation_id": "createUser", "summary": "Create a user", "requires_auth": True},
            ],
            "missing_auth_fields": ["api_key"],
        })

    parser = _build_parser(handler)
    extraction = parser.parse(html_body=_SAMPLE_HTML, source_url="https://docs.acme.com")

    assert extraction is not None
    assert extraction.provider_name == "Acme API"
    assert extraction.base_url == "https://api.acme.com/v1"
    assert len(extraction.endpoints) == 2
    assert extraction.endpoints[0].operation_id == "listUsers"
    assert extraction.missing_auth_fields == ["api_key"]


def test_parser_returns_none_when_no_api_key_configured() -> None:
    parser = AtlasDocsPageParser(api_key=None)
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None


def test_parser_returns_none_when_html_is_empty() -> None:
    parser = AtlasDocsPageParser(api_key="sk-test")
    assert parser.parse(html_body="", source_url=None) is None
    assert parser.parse(html_body="   \n\t  ", source_url=None) is None


def test_parser_returns_none_when_anthropic_returns_5xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    parser = _build_parser(handler)
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None


def test_parser_returns_none_when_anthropic_returns_4xx() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    parser = _build_parser(handler)
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None


def test_docs_parser_from_env_survives_malformed_numeric_env(monkeypatch) -> None:
    """AR-3.7: malformed numeric env warns-and-defaults instead of crashing
    app construction (parity with the generator's from_env)."""
    monkeypatch.setenv("RUHU_ATLAS_DOCS_PARSER_API_KEY", "sk-test")
    monkeypatch.setenv("RUHU_ATLAS_DOCS_PARSER_TIMEOUT_SECONDS", "not-a-number")
    monkeypatch.setenv("RUHU_ATLAS_DOCS_PARSER_MAX_RETRIES", "two")
    monkeypatch.setenv("RUHU_ATLAS_DOCS_PARSER_RETRY_BACKOFF_SECONDS", "")

    parser = AtlasDocsPageParser.from_env()

    assert parser.timeout_seconds == 12.0
    assert parser.max_retries == 2
    assert parser.retry_backoff_seconds == 0.25


def test_parser_returns_none_when_response_is_not_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Sure! I'd be happy to help, but I have no JSON for you."}],
            "model": "claude-test",
        }
        return httpx.Response(200, json=body)

    parser = _build_parser(handler)
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None


def test_parser_returns_none_when_json_block_is_malformed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "{not really json}"}],
            "model": "claude-test",
        }
        return httpx.Response(200, json=body)

    parser = _build_parser(handler)
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None


def test_parser_returns_none_when_network_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    parser = _build_parser(handler)
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None


# ── Dispatcher integration: _docs_page_result ────────────────────────


def test_dispatcher_returns_llm_parsed_when_parser_succeeds() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _make_anthropic_response({
            "provider_name": "Acme API",
            "base_url": "https://api.acme.com/v1",
            "endpoints": [
                {"method": "GET", "path": "/things", "operation_id": "listThings"},
            ],
            "missing_auth_fields": ["api_key"],
        })

    parser = _build_parser(handler)
    request = AtlasAPIDiscoveryRequest(
        request_id="r1",
        source_type="website_url",
        source_value="https://docs.acme.com",
    )
    result = _docs_page_result(request, _SAMPLE_HTML, docs_parser=parser)

    assert result.status == "discovered"
    assert result.spec_type == "llm_parsed"
    assert result.provider_name == "Acme API"
    assert result.base_url == "https://api.acme.com/v1"
    assert result.candidate_tool_refs == ["listThings"]


def test_dispatcher_falls_back_to_heuristic_when_parser_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    parser = _build_parser(handler)
    request = AtlasAPIDiscoveryRequest(
        request_id="r1",
        source_type="website_url",
        source_value="https://docs.acme.com",
    )
    result = _docs_page_result(request, _SAMPLE_HTML, docs_parser=parser)

    assert result.spec_type == "heuristic"
    # Heuristic still finds the GET/POST patterns in the sample HTML.
    assert result.status == "discovered"


def test_dispatcher_uses_heuristic_when_parser_not_configured() -> None:
    parser = AtlasDocsPageParser(api_key=None)  # no api key
    request = AtlasAPIDiscoveryRequest(
        request_id="r1",
        source_type="website_url",
        source_value="https://docs.acme.com",
    )
    result = _docs_page_result(request, _SAMPLE_HTML, docs_parser=parser)

    assert result.spec_type == "heuristic"


def test_dispatcher_uses_heuristic_when_no_parser_passed() -> None:
    request = AtlasAPIDiscoveryRequest(
        request_id="r1",
        source_type="website_url",
        source_value="https://docs.acme.com",
    )
    result = _docs_page_result(request, _SAMPLE_HTML, docs_parser=None)

    assert result.spec_type == "heuristic"


def test_dispatcher_falls_back_when_llm_returns_zero_endpoints() -> None:
    """LLM might confidently say "no endpoints found." We still want
    to try the heuristic instead of failing immediately."""
    def handler(request: httpx.Request) -> httpx.Response:
        return _make_anthropic_response({
            "provider_name": "Acme API",
            "endpoints": [],
            "missing_auth_fields": [],
        })

    parser = _build_parser(handler)
    request = AtlasAPIDiscoveryRequest(
        request_id="r1",
        source_type="website_url",
        source_value="https://docs.acme.com",
    )
    result = _docs_page_result(request, _SAMPLE_HTML, docs_parser=parser)

    # The heuristic should pick up the GET /users + POST /users in the
    # sample HTML. Label is "heuristic" because the LLM returned nothing
    # useful and we fell back.
    assert result.spec_type == "heuristic"
    assert result.status == "discovered"


# ── discovery_result_for_request integration ────────────────────────


def test_discovery_for_pasted_openapi_does_not_call_llm_parser() -> None:
    """Structural OpenAPI parsing must NEVER hit the LLM, even when a
    parser is configured. Only ``website_url`` / HTML content paths
    use the LLM."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("LLM was called for an OpenAPI input — should not happen")

    parser = _build_parser(handler)
    request = AtlasAPIDiscoveryRequest(
        request_id="r1",
        source_type="pasted_schema",
        source_value=json.dumps({
            "openapi": "3.0.0",
            "info": {"title": "X"},
            "paths": {"/y": {"get": {"operationId": "getY"}}},
        }),
    )
    result = discovery_result_for_request(request, docs_parser=parser)
    assert result.spec_type == "openapi"
    assert "getY" in result.candidate_tool_refs


# ── String-aware JSON extraction ─────────────────────────────────────


def test_extract_json_object_ignores_closing_brace_inside_string() -> None:
    text = '{"summary": "ends with :}"}'
    assert _extract_json_object(text) == text


def test_extract_json_object_ignores_braces_and_escapes_inside_strings() -> None:
    text = '{"a": "{", "b": "say \\" }", "c": {"d": "}}}"}}'
    extracted = _extract_json_object(text)
    assert extracted == text
    assert json.loads(extracted) == {"a": "{", "b": 'say " }', "c": {"d": "}}}"}}


def test_extract_json_object_handles_quotes_in_surrounding_commentary() -> None:
    text = 'Here is "the" JSON: {"foo": 1} done'
    assert _extract_json_object(text) == '{"foo": 1}'


def test_extract_json_object_ignores_stray_closing_brace_before_object() -> None:
    assert _extract_json_object('} noise {"foo": 1}') == '{"foo": 1}'


# ── operation_id type handling ───────────────────────────────────────


def test_normalize_rejects_boolean_operation_id() -> None:
    raw = {
        "endpoints": [
            {"method": "GET", "path": "/a", "operation_id": True},
            {"method": "GET", "path": "/b", "operation_id": False},
        ]
    }
    extraction = _normalize_extraction(raw)
    assert [endpoint.operation_id for endpoint in extraction.endpoints] == [None, None]


def test_normalize_still_accepts_numeric_operation_id() -> None:
    raw = {"endpoints": [{"method": "GET", "path": "/a", "operation_id": 42}]}
    extraction = _normalize_extraction(raw)
    assert extraction.endpoints[0].operation_id == "42"


# ── Metric recording: exactly-once requests counter + fallback reasons ──


def test_metrics_success_records_single_ok_request_and_no_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _make_anthropic_response({"endpoints": [{"method": "GET", "path": "/x"}]})

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is not None
    assert _metric_deltas(before) == {"requests:ok": 1.0}


def test_metrics_parse_failure_records_parse_error_exactly_once() -> None:
    """A 2xx HTTP call followed by a JSON-extraction failure must record
    the requests counter ONCE (outcome=parse_error), not ok + parse_error."""
    def handler(request: httpx.Request) -> httpx.Response:
        body = {
            "content": [{"type": "text", "text": "no JSON here at all"}],
        }
        return httpx.Response(200, json=body)

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {
        "requests:parse_error": 1.0,
        "fallback:parse_error": 1.0,
    }


def test_metrics_json_decode_failure_records_parse_error_exactly_once() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"content": [{"type": "text", "text": "{not really json}"}]}
        return httpx.Response(200, json=body)

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {
        "requests:parse_error": 1.0,
        "fallback:parse_error": 1.0,
    }


def test_metrics_http_4xx_records_request_and_http_error_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="invalid api key")

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {
        "requests:http_4xx": 1.0,
        "fallback:http_error": 1.0,
    }


def test_metrics_http_5xx_records_request_and_http_error_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream unavailable")

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {
        "requests:http_5xx": 1.0,
        "fallback:http_error": 1.0,
    }


def test_metrics_network_failure_records_request_and_network_error_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {
        "requests:network": 1.0,
        "fallback:network_error": 1.0,
    }


def test_metrics_empty_llm_response_records_empty_result_fallback() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": []})

    parser = _build_parser(handler)
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {
        "requests:empty": 1.0,
        "fallback:empty_result": 1.0,
    }


def test_metrics_missing_api_key_records_fallback_without_request() -> None:
    parser = AtlasDocsPageParser(api_key=None, model="claude-test")
    before = _metric_snapshot()
    assert parser.parse(html_body=_SAMPLE_HTML, source_url=None) is None
    assert _metric_deltas(before) == {"fallback:missing_api_key": 1.0}


def test_metrics_empty_body_records_fallback_without_request() -> None:
    parser = AtlasDocsPageParser(api_key="sk-test", model="claude-test")
    before = _metric_snapshot()
    assert parser.parse(html_body="   ", source_url=None) is None
    assert _metric_deltas(before) == {"fallback:empty_body": 1.0}
