"""LLM cost accounting — Phase S3.

Provides a static rate card (USD per 1 M tokens) and ``record_llm_cost()``,
which increments ``ruhu_llm_cost_usd_total`` and ``ruhu_llm_tokens_total`` for
every instrumented LLM call.

Design constraints
------------------
* **Low cardinality**: only ``provider`` and ``model`` labels are used, both
  from bounded enums.  No org/user/conversation identifiers.
* **Fail-open**: if the model is unknown the cost is recorded under the
  ``"unknown"`` canonical name so the counter increments without raising.
* **Static rate card**: costs are hard-coded and updated here when pricing
  changes.  The alternative (dynamic fetch) adds latency and a remote
  dependency on the hot path.

Rate card
---------
Source: Google AI / Vertex AI pricing page (as of 2026-04).
Units: USD per 1 000 000 tokens.

Model names are canonicalised before lookup — version suffixes like
``-preview``, ``-001``, or date stamps are stripped so the rate card
stays compact.
"""
from __future__ import annotations

import re

# ── Rate card ──────────────────────────────────────────────────────────────────
# USD per 1 000 000 tokens.
# Key: normalised model name (see _canonical_model_name).
_RATE_CARD: dict[str, dict[str, float]] = {
    # Gemini 1.5 family
    "gemini-1.5-flash": {"input": 0.075, "output": 0.30},
    "gemini-1.5-flash-8b": {"input": 0.0375, "output": 0.15},
    "gemini-1.5-pro": {"input": 3.50, "output": 10.50},
    # Gemini 2.0 family
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
    "gemini-2.0-flash-lite": {"input": 0.075, "output": 0.30},
    "gemini-2.0-pro": {"input": 3.50, "output": 10.50},
    # Gemini 2.5 family (experimental pricing)
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro": {"input": 7.00, "output": 21.00},
    # Gemini 3 (placeholder — update when GA pricing is published)
    "gemini-3-flash": {"input": 0.10, "output": 0.40},
    "gemini-3-pro": {"input": 4.00, "output": 12.00},
    # Fallback for any unknown model
    "unknown": {"input": 0.0, "output": 0.0},
}

# Strip trailing version/date/preview suffixes to reach the canonical name.
# Order matters: longer patterns first so "-preview-04-17" loses "-04-17" then "-preview".
_VERSION_SUFFIX_RE = re.compile(
    r"(-\d{4}-\d{2}-\d{2}|-\d{2}-\d{2}|-\d{3,}|-preview|-exp|-latest|-stable)$",
    re.IGNORECASE,
)


def _canonical_model_name(model: str) -> str:
    """Strip version suffixes and return the rate-card key.

    Examples::

        "gemini-1.5-flash-preview"     → "gemini-1.5-flash"
        "gemini-2.0-flash-001"         → "gemini-2.0-flash"
        "gemini-3-flash-preview-04-17" → "gemini-3-flash"
        "totally-unknown-model"        → "unknown"
    """
    name = model.strip().lower()
    # Strip known suffixes iteratively (e.g. "-preview-04-17")
    for _ in range(4):
        stripped = _VERSION_SUFFIX_RE.sub("", name)
        if stripped == name:
            break
        name = stripped

    return name if name in _RATE_CARD else "unknown"


def cost_usd(model: str, *, input_tokens: int, output_tokens: int) -> float:
    """Return the estimated USD cost for a single LLM call.

    The result may be 0.0 for unknown models — this is intentional: the
    counter still increments so we know a call was made even if pricing is
    missing.

    Args:
        model: Raw model name as returned by the API (e.g. ``"gemini-2.0-flash"``).
        input_tokens: Prompt / input token count.
        output_tokens: Completion / output token count.
    """
    canonical = _canonical_model_name(model)
    rates = _RATE_CARD[canonical]  # always present (fallback "unknown" key)
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def record_llm_cost(
    provider: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Emit ``ruhu_llm_tokens_total`` and ``ruhu_llm_cost_usd_total`` metrics.

    Safe to call from any thread.  Silently ignores import / metric errors so
    it never disrupts the hot path.

    Args:
        provider: Bounded provider label — ``"gemini"``, ``"vertex"``, etc.
        model: Raw model name (canonicalised internally for the rate card).
        input_tokens: Prompt tokens for this call.
        output_tokens: Completion tokens for this call.
    """
    try:
        from .metrics import llm_tokens_total, llm_cost_usd_total

        canonical = _canonical_model_name(model)
        llm_tokens_total.labels(provider=provider, model=canonical, direction="input").inc(
            input_tokens
        )
        llm_tokens_total.labels(provider=provider, model=canonical, direction="output").inc(
            output_tokens
        )
        usd = cost_usd(model, input_tokens=input_tokens, output_tokens=output_tokens)
        llm_cost_usd_total.labels(provider=provider, model=canonical).inc(usd)
    except Exception:
        pass
