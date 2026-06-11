"""WI-4.6 — vLLM guided-decoding API probe.

Sends a known-good guided_choice request to a deployed vLLM cluster
and reports which API shape works. vLLM's OpenAI-compatible API has
changed the placement of guided_choice across versions:

- v0.4.x:  guided_choice at top level of /v1/completions request
- v0.5.x:  guided_choice inside extra_body of /v1/completions
- v0.6.x:  /v1/chat/completions only with response_format
- v0.7.x:  guided_choice both top-level and extra_body work, but
           guided_decoding_backend may need to be set explicitly

Usage::

    python -m ruhu.classifier.benchmark.probe_vllm_api \\
        --base-url http://vllm.classifier.svc.cluster.local:8000 \\
        --model Qwen/Qwen3-8B \\
        --report-out ./probe-report.json

The probe runs four request variants, scores each by the response
quality (intent in catalog, logprobs returned, no error), and
prints a recommendation. Update
``docs/pre-fill-intent-classifier-design/02-architecture-spec.md``
§vLLM HTTP request shape with the verified shape, and update
``VLLMClassifierBackend`` if the pinned version needs a different
placement than today's defaults.

Critical: this MUST run before shipping VLLMClassifierBackend to
production. Don't encode a guess.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_TOKENS = 8
DEFAULT_PROBE_INTENTS = ["transfer_status", "kyc_help", "card_freeze", "unknown"]
DEFAULT_PROBE_PROMPT = (
    "You classify the user's current turn for a Ruhu step-native assistant.\n\n"
    "Step: Entry\n"
    "Step summary: Triage the user's reason for contacting.\n"
    "Step capabilities: none\n"
    "\n"
    "Valid intents (choose exactly one):\n"
    "- card_freeze: User wants to freeze a card.\n"
    "- kyc_help: User has a KYC question.\n"
    "- transfer_status: User asks about a transfer.\n"
    "- unknown: none of the above match the user's message\n"
    "\n"
    "User message: where is my money?\n"
    "Intent:"
)


@dataclass(slots=True)
class VariantResult:
    name: str
    endpoint: str
    elapsed_ms: int = 0
    http_status: int | None = None
    error: str | None = None
    chosen_text: str | None = None
    intent_in_catalog: bool = False
    logprobs_present: bool = False
    response_keys: list[str] = field(default_factory=list)
    raw_response_excerpt: str = ""


@dataclass(slots=True)
class ProbeReport:
    base_url: str
    model: str
    probed_at: str
    variants: list[VariantResult]
    recommended_variant: str | None
    notes: list[str] = field(default_factory=list)


# ── request builders ────────────────────────────────────────────────────────


def _completions_top_level(model: str, prompt: str, intents: list[str]) -> dict[str, Any]:
    """vLLM v0.4-era: guided_choice at top level of /v1/completions."""
    return {
        "model": model,
        "prompt": prompt,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.0,
        "logprobs": 1,
        "guided_choice": intents,
    }


def _completions_extra_body(model: str, prompt: str, intents: list[str]) -> dict[str, Any]:
    """vLLM v0.5-era: guided_choice inside extra_body of /v1/completions."""
    return {
        "model": model,
        "prompt": prompt,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.0,
        "logprobs": 1,
        "extra_body": {
            "guided_choice": intents,
            "guided_decoding_backend": "outlines",
        },
    }


def _completions_both(model: str, prompt: str, intents: list[str]) -> dict[str, Any]:
    """vLLM v0.7-era hedge: guided_choice at both top-level + extra_body."""
    return {
        "model": model,
        "prompt": prompt,
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.0,
        "logprobs": 1,
        "guided_choice": intents,
        "extra_body": {
            "guided_choice": intents,
            "guided_decoding_backend": "outlines",
        },
    }


def _chat_response_format(model: str, prompt: str, intents: list[str]) -> dict[str, Any]:
    """vLLM v0.6-era /v1/chat/completions with response_format json_schema."""
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": 0.0,
        "logprobs": True,
        "top_logprobs": 1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "intent",
                "schema": {
                    "type": "object",
                    "properties": {
                        "intent": {"type": "string", "enum": intents},
                    },
                    "required": ["intent"],
                },
            },
        },
    }


# ── parsing ────────────────────────────────────────────────────────────────


def _parse_completions(body: dict[str, Any]) -> tuple[str | None, bool]:
    choices = body.get("choices") or []
    if not choices:
        return None, False
    first = choices[0] or {}
    text = str(first.get("text") or "").strip()
    logprobs = first.get("logprobs") or {}
    logprobs_present = bool(logprobs.get("token_logprobs"))
    return text or None, logprobs_present


def _parse_chat(body: dict[str, Any]) -> tuple[str | None, bool]:
    choices = body.get("choices") or []
    if not choices:
        return None, False
    first = choices[0] or {}
    message = first.get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        return None, False
    try:
        parsed = json.loads(content)
        intent = str(parsed.get("intent") or "").strip()
    except json.JSONDecodeError:
        intent = content
    logprobs_present = bool(first.get("logprobs"))
    return intent or None, logprobs_present


# ── probe orchestrator ─────────────────────────────────────────────────────


@dataclass(slots=True)
class _Variant:
    name: str
    endpoint: str
    payload_builder: Callable[[str, str, list[str]], dict[str, Any]]
    parse: Callable[[dict[str, Any]], tuple[str | None, bool]]


def _variants() -> list[_Variant]:
    return [
        _Variant(
            name="completions_top_level",
            endpoint="/v1/completions",
            payload_builder=_completions_top_level,
            parse=_parse_completions,
        ),
        _Variant(
            name="completions_extra_body",
            endpoint="/v1/completions",
            payload_builder=_completions_extra_body,
            parse=_parse_completions,
        ),
        _Variant(
            name="completions_both",
            endpoint="/v1/completions",
            payload_builder=_completions_both,
            parse=_parse_completions,
        ),
        _Variant(
            name="chat_response_format",
            endpoint="/v1/chat/completions",
            payload_builder=_chat_response_format,
            parse=_parse_chat,
        ),
    ]


def probe(
    *,
    base_url: str,
    model: str,
    intents: list[str] | None = None,
    prompt: str = DEFAULT_PROBE_PROMPT,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    http_post: Callable[..., Any] | None = None,
) -> ProbeReport:
    intent_list = intents or DEFAULT_PROBE_INTENTS
    results: list[VariantResult] = []
    for variant in _variants():
        result = _run_variant(
            variant,
            base_url=base_url,
            model=model,
            prompt=prompt,
            intents=intent_list,
            timeout_seconds=timeout_seconds,
            http_post=http_post,
        )
        results.append(result)

    recommended = _recommend(results, intent_list)
    notes = _notes(results, recommended)
    return ProbeReport(
        base_url=base_url,
        model=model,
        probed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        variants=results,
        recommended_variant=recommended,
        notes=notes,
    )


def _run_variant(
    variant: _Variant,
    *,
    base_url: str,
    model: str,
    prompt: str,
    intents: list[str],
    timeout_seconds: float,
    http_post: Callable[..., Any] | None,
) -> VariantResult:
    payload = variant.payload_builder(model, prompt, intents)
    url = base_url.rstrip("/") + variant.endpoint
    started = time.perf_counter()
    try:
        if http_post is not None:
            body = http_post(url=url, json=payload, timeout=timeout_seconds)
            status = 200
        else:
            with httpx.Client(timeout=timeout_seconds) as client:
                response = client.post(
                    url, json=payload, headers={"Content-Type": "application/json"}
                )
                status = response.status_code
                response.raise_for_status()
                body = response.json()
    except Exception as exc:
        return VariantResult(
            name=variant.name,
            endpoint=variant.endpoint,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            http_status=getattr(exc, "response", None) and getattr(exc.response, "status_code", None),
            error=f"{type(exc).__name__}: {exc}",
        )

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    chosen, logprobs_present = variant.parse(body)
    intent_in_catalog = chosen is not None and chosen in intents
    excerpt = json.dumps(body, ensure_ascii=False)[:600]
    return VariantResult(
        name=variant.name,
        endpoint=variant.endpoint,
        elapsed_ms=elapsed_ms,
        http_status=status,
        chosen_text=chosen,
        intent_in_catalog=intent_in_catalog,
        logprobs_present=logprobs_present,
        response_keys=sorted(list(body.keys())) if isinstance(body, dict) else [],
        raw_response_excerpt=excerpt,
    )


def _recommend(results: list[VariantResult], intents: list[str]) -> str | None:
    """Pick the best-scoring variant.

    Scoring (descending priority):
    1. No error AND intent_in_catalog AND logprobs_present
    2. No error AND intent_in_catalog
    3. No error
    4. Anything

    Tie-break by lowest elapsed_ms (cheaper variant wins).
    """
    def score(result: VariantResult) -> tuple[int, int]:
        if result.error:
            return (0, -result.elapsed_ms)
        score = 1
        if result.intent_in_catalog:
            score += 2
        if result.logprobs_present:
            score += 4
        return (score, -result.elapsed_ms)

    ranked = sorted(results, key=score, reverse=True)
    best = ranked[0]
    if best.error or not best.intent_in_catalog:
        return None
    return best.name


def _notes(results: list[VariantResult], recommended: str | None) -> list[str]:
    notes: list[str] = []
    if recommended is None:
        notes.append(
            "no variant succeeded — check vLLM logs, model name, and that the "
            "service is reachable. The cluster may be on a vLLM release with "
            "guided-decoding turned off (--enable-guided-decoding flag may be needed)."
        )
    else:
        notes.append(
            f"VLLMClassifierBackend should be configured to send the {recommended} "
            "variant. If it currently sends a different shape, update vllm_backend.py "
            "to match."
        )
    failures = [r for r in results if r.error]
    if failures:
        notes.append(
            "variants with errors: "
            + ", ".join(f"{r.name} ({r.error})" for r in failures)
        )
    no_logprobs = [r for r in results if not r.error and not r.logprobs_present]
    if no_logprobs:
        notes.append(
            "variants succeeded but returned no logprobs: "
            + ", ".join(r.name for r in no_logprobs)
            + ". Confidence calculation requires logprobs — these variants would "
            "force confidence=0 in the runtime."
        )
    return notes


# ── CLI ────────────────────────────────────────────────────────────────────


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = probe(
        base_url=args.base_url,
        model=args.model,
        intents=args.intents.split(",") if args.intents else None,
        timeout_seconds=args.timeout_seconds,
    )
    output = json.dumps(_to_jsonable(report), indent=2, ensure_ascii=False)
    if args.report_out:
        Path(args.report_out).write_text(output, encoding="utf-8")
    print(output)
    print()
    if report.recommended_variant is None:
        print("No working variant. See `notes` above.", file=sys.stderr)
        return 2
    print(
        f"Recommended variant: {report.recommended_variant}. "
        f"Update spec §vLLM HTTP request shape and VLLMClassifierBackend if needed."
    )
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ruhu.classifier.benchmark.probe_vllm_api",
        description="WI-4.6: probe a vLLM cluster for guided_choice support.",
    )
    parser.add_argument("--base-url", required=True, help="vLLM base URL")
    parser.add_argument("--model", required=True, help="Model name as known to vLLM")
    parser.add_argument(
        "--intents",
        default=None,
        help="Comma-separated guided_choice values (default: probe-friendly catalog)",
    )
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--report-out",
        default=None,
        help="Optional path to write the JSON report",
    )
    return parser.parse_args(argv)


def _to_jsonable(report: ProbeReport) -> dict[str, Any]:
    return {
        "base_url": report.base_url,
        "model": report.model,
        "probed_at": report.probed_at,
        "recommended_variant": report.recommended_variant,
        "notes": list(report.notes),
        "variants": [asdict(v) for v in report.variants],
    }


if __name__ == "__main__":
    sys.exit(main())
