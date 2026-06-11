"""Capture today's classifier P50/P90/P99 — Stage WI-X.3 baseline.

Without a baseline, "Stage 1–2 made things faster" is a claim with no number
behind it. This script computes per-agent, per-step latency stats for the
existing Vertex Gemini classifier path so the Stage 2.5 decision (Qwen vs
Gemma vs stay) has something to compare against.

Two data sources, in priority order:

1. **Prometheus** (preferred). The kernel records ``ruhu_llm_request_duration_seconds``
   with ``stage="classify"`` for every Vertex Gemini classifier call. Query
   with::

       histogram_quantile(0.5, sum by (le, model) (
           rate(ruhu_llm_request_duration_seconds_bucket{stage="classify"}[7d])
       ))

   Adjust window (7d) to taste. If you have Grafana access, the panel
   already exists in the LLM dashboard.

2. **Database** (fallback, less precise). Some classifier latency is in
   ``turn_traces.latency_breakdown_ms_json`` if the kernel populates it,
   though as of 2026-04-30 most traces show ``{"total": 0}`` — kernel-side
   instrumentation is incomplete. This script's DB path computes whatever
   coarse stats are available so you can sanity-check the Prometheus query.

What this script DOES NOT do:
- Push to a Grafana annotation. Do that manually after.
- Write a markdown decision record. Do that in
  docs/pre-fill-intent-classifier-design/stage-2.5-decision.md after Stage 2.5.

Usage:
    PYTHONPATH=src python scripts/capture_classifier_baseline.py

Set RUHU_DATABASE_URL to point at a database with real classifier traffic
(staging or production). The local dev DB will return near-empty results.
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from statistics import quantiles
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from sqlalchemy import select  # noqa: E402

from ruhu.db import build_session_factory, resolve_database_url  # noqa: E402
from ruhu.db_models import TurnTraceRecord  # noqa: E402


def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m"


def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m"


def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m"


# ─────────────────────────────────────────────────────────────────────────────
# Database-side baseline (coarse — only useful if latency_breakdown_ms_json
# is being populated by the kernel)
# ─────────────────────────────────────────────────────────────────────────────


def db_baseline(database_url: str) -> dict[str, Any]:
    """Compute coarse latency stats from turn_traces.

    Filters to traces whose semantic_events contain at least one event with
    ``source="classifier"`` (i.e., the kernel routed through an LLM
    classifier this turn). Reads ``latency_breakdown_ms_json["total"]`` for
    the per-turn wall-clock time.
    """
    factory = build_session_factory(resolve_database_url(database_url=database_url))
    with factory() as session:
        records = session.execute(
            select(
                TurnTraceRecord.agent_id,
                TurnTraceRecord.step_after,
                TurnTraceRecord.semantic_events_json,
                TurnTraceRecord.latency_breakdown_ms_json,
                TurnTraceRecord.recorded_at,
            )
        ).all()

    rows: list[dict[str, Any]] = []
    for agent_id, step_after, events_json, latency_json, recorded_at in records:
        # We treat "any classifier-source event" as the marker that this turn
        # was classified by the LLM cascade.
        events = events_json or []
        has_classifier = any(
            isinstance(e, dict) and e.get("source") == "classifier"
            for e in events
        )
        if not has_classifier:
            continue
        latency_total = (latency_json or {}).get("total")
        rows.append(
            {
                "agent_id": agent_id,
                "step_id": step_after,
                "latency_total_ms": latency_total,
                "recorded_at": recorded_at,
            }
        )

    return {
        "total_traces_seen": len(records),
        "classifier_turns": len(rows),
        "rows": rows,
    }


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "p99": 0.0, "n": 0}
    if len(values) == 1:
        v = float(values[0])
        return {"p50": v, "p90": v, "p99": v, "n": 1}
    cuts = quantiles(sorted(values), n=100, method="inclusive")
    # quantiles(n=100) returns 99 cut points; index 49 ≈ p50, 89 ≈ p90, 98 ≈ p99
    return {
        "p50": float(cuts[49]),
        "p90": float(cuts[89]),
        "p99": float(cuts[98]),
        "n": len(values),
    }


def summarize(baseline: dict[str, Any]) -> None:
    print(f"\n{_yellow('Database-side baseline:')}")
    print(f"  total turn_traces in window: {baseline['total_traces_seen']}")
    print(f"  classifier turns (≥1 source=classifier event): {baseline['classifier_turns']}")

    if baseline["classifier_turns"] == 0:
        print(_red("\n  ✗ No classifier turns found in this database."))
        print(
            "    Either (a) this DB has no production classifier traffic\n"
            "    (likely the local dev DB), or (b) the kernel isn't routing\n"
            "    through an LLM classifier yet, or (c) latency_breakdown_ms\n"
            "    isn't being populated.\n\n"
            "    For the real WI-X.3 baseline, query Prometheus instead:\n"
            "      histogram_quantile(0.5, sum by (le, model) (\n"
            "          rate(ruhu_llm_request_duration_seconds_bucket{stage=\"classify\"}[7d])\n"
            "      ))\n"
        )
        return

    rows = baseline["rows"]
    latencies = [r["latency_total_ms"] for r in rows if r["latency_total_ms"]]

    if not latencies:
        print(_red("  ✗ classifier turns found but latency_breakdown_ms_json is all zero."))
        print(
            "    Kernel doesn't populate per-turn latency breakdown today;\n"
            "    use Prometheus for the real baseline (WI-X.3).\n"
        )
    else:
        global_pct = _percentiles(latencies)
        print(f"\n{_green('Per-turn total latency (DB-derived, may be coarse):')}")
        print(f"  P50: {global_pct['p50']:.0f}ms")
        print(f"  P90: {global_pct['p90']:.0f}ms")
        print(f"  P99: {global_pct['p99']:.0f}ms")
        print(f"  n:   {global_pct['n']}")

    # Per-agent breakdown.
    by_agent: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        if r["latency_total_ms"]:
            by_agent[r["agent_id"]].append(r["latency_total_ms"])

    if by_agent:
        print(f"\n{_yellow('Per-agent breakdown:')}")
        for agent_id, vals in sorted(
            by_agent.items(), key=lambda x: -len(x[1])
        )[:10]:
            pct = _percentiles(vals)
            print(
                f"  {agent_id[:48]:<48s}  P50={pct['p50']:>4.0f}ms  P99={pct['p99']:>4.0f}ms  n={pct['n']}"
            )


def main() -> None:
    database_url = os.getenv(
        "RUHU_DATABASE_URL",
        "postgresql+psycopg://postgres:postgres@localhost:5432/ruhu_runtime_dev",
    )
    print(f"Database: {database_url.split('@')[-1]}")
    baseline = db_baseline(database_url)
    summarize(baseline)

    print(f"\n{_yellow('Next steps:')}")
    print("  1. Run the Prometheus query above against your production cluster.")
    print("  2. Capture the result in stage-2.5-decision.md as the Vertex baseline.")
    print("  3. Stage 2.5 benchmarks (Qwen3-8B + Gemma 4 E4B) get measured against this.")
    print(
        "\nNote: this script intentionally does not write the decision record.\n"
        "Stage 2.5 is a human gate; the script just produces inputs to it."
    )


if __name__ == "__main__":
    main()
