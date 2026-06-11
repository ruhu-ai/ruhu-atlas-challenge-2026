#!/usr/bin/env python3
"""Run an operator smoke check for the Atlas readiness pipeline.

This script verifies the real SQLAlchemy readiness stores and service wiring:

* a deterministic validate run with a voice-case subset
* a deterministic fix run that produces reviewable Atlas deltas
* a rerun that reuses the original case set
* an optional google_only run with voice_case_count > 0 when Google providers
  are configured

It is intentionally service-level instead of browser-driven so it remains
stable in CI and in local Codex sessions where browser automation is not
available.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import Decimal
from typing import Any

from ruhu.agent_document import AgentDocument, Scenario, Step, StepCompletion, StepTransition
from ruhu.atlas_readiness_models import AtlasReadinessProviderPolicy, AtlasReadinessRunRequest
from ruhu.atlas_readiness_service import AtlasReadinessService
from ruhu.atlas_readiness_store import SQLAlchemyAtlasReadinessStore
from ruhu.atlas_store import SQLAlchemyAtlasStore
from ruhu.blob_store import build_blob_store_from_settings
from ruhu.db import build_session_factory, resolve_database_url
from ruhu.heuristics import KeywordInterpreter
from ruhu.registry import SQLAlchemyAgentRegistry
from ruhu.runtime_config import RuntimeSettings


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test Atlas readiness against the configured database.")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("RUHU_DATABASE_URL") or os.environ.get("DATABASE_URL") or "",
        help="Runtime DB URL. Defaults to RUHU_DATABASE_URL or DATABASE_URL.",
    )
    parser.add_argument(
        "--organization-id",
        default=os.environ.get("RUHU_ATLAS_SMOKE_ORGANIZATION_ID", "public"),
        help="Tenant id used for the smoke agent and readiness runs.",
    )
    parser.add_argument(
        "--user-id",
        default=os.environ.get("RUHU_ATLAS_SMOKE_USER_ID", "atlas-readiness-smoke"),
        help="User id stored as the run creator.",
    )
    parser.add_argument(
        "--agent-id",
        default=os.environ.get("RUHU_ATLAS_SMOKE_AGENT_ID", "atlas_readiness_smoke"),
        help="Agent id used for the reusable smoke draft.",
    )
    parser.add_argument(
        "--require-google",
        action="store_true",
        default=os.environ.get("RUHU_ATLAS_REQUIRE_GOOGLE", "").strip().lower() in {"1", "true", "yes"},
        help="Fail if google_only providers are not configured or the google_only smoke is skipped.",
    )
    parser.add_argument(
        "--skip-google",
        action="store_true",
        help="Only run deterministic validate/fix/rerun checks.",
    )
    parser.add_argument(
        "--voice-audio-uri",
        default=os.environ.get("RUHU_ATLAS_SMOKE_VOICE_AUDIO_URI") or "",
        help="Optional gs:// audio fixture URI for strict Google STT verification.",
    )
    parser.add_argument(
        "--voice-language",
        default=os.environ.get("RUHU_ATLAS_SMOKE_VOICE_LANGUAGE") or "en-NG",
        help="Language code for the Google STT audio fixture.",
    )
    parser.add_argument(
        "--require-real-voice",
        action="store_true",
        default=os.environ.get("RUHU_ATLAS_REQUIRE_REAL_VOICE", "").strip().lower() in {"1", "true", "yes"},
        help="Fail the Google smoke unless a real audio URI, real STT, TTS, and artifact export all succeed.",
    )
    return parser.parse_args()


def _handoff_demo_document() -> AgentDocument:
    return AgentDocument(
        start_scenario_id="support",
        scenarios=[
            Scenario(
                id="support",
                name="Support",
                start_step_id="entry",
                steps=[
                    Step(
                        id="entry",
                        name="Entry",
                        say="How can I help with your repayment?",
                        transitions=[
                            StepTransition(
                                id="t_payment_dispute",
                                when={
                                    "kind": "outcome",
                                    "event": "payment_dispute",
                                    "description": "The customer says a repayment was made but did not reflect.",
                                },
                                to_step_id="payment_dispute",
                                priority=10,
                            ),
                            StepTransition(
                                id="t_otherwise",
                                when={"kind": "otherwise"},
                                to_step_id="entry",
                                priority=100,
                            ),
                        ],
                    ),
                    Step(
                        id="payment_dispute",
                        name="Payment dispute",
                        completion=StepCompletion(disposition="ticket_created", summary="Payment dispute captured."),
                    ),
                    Step(
                        id="handoff",
                        name="Human handoff",
                        completion=StepCompletion(disposition="handoff", summary="Customer requested a human."),
                    ),
                ],
            )
        ],
    )


def _ensure_smoke_agent(
    registry: SQLAlchemyAgentRegistry,
    *,
    agent_id: str,
    organization_id: str,
) -> None:
    document = _handoff_demo_document()
    try:
        registry.create_agent_document(
            agent_id=agent_id,
            agent_name="Atlas Readiness Smoke",
            organization_id=organization_id,
            document=document,
        )
    except ValueError:
        registry.update_draft_agent_document(agent_id, document, organization_id=organization_id)


def _build_service(database_url: str) -> tuple[AtlasReadinessService, SQLAlchemyAtlasReadinessStore, SQLAlchemyAtlasStore, SQLAlchemyAgentRegistry]:
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    artifact_store = build_blob_store_from_settings(RuntimeSettings.from_env())
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
        interpreter=KeywordInterpreter(
            rules={
                "payment_dispute": ("paid", "repayment", "reflect"),
                "handoff_request": ("person", "human", "transfer"),
            }
        ),
        artifact_store=artifact_store,
    )
    return service, readiness_store, atlas_store, registry


def _event_types(readiness_store: SQLAlchemyAtlasReadinessStore, run_id: str, *, organization_id: str) -> list[str]:
    events, _total_count, _has_more = readiness_store.list_events(run_id, organization_id=organization_id, limit=200)
    return [event.type for event in events]


def _run_summary_payload(summary, readiness_store: SQLAlchemyAtlasReadinessStore, *, organization_id: str) -> dict[str, Any]:
    report = summary.report
    if report is None:
        raise RuntimeError(f"readiness run {summary.run.run_id} did not write a report")
    case_set = summary.case_set or readiness_store.get_case_set(summary.run.case_set_id or "", organization_id=organization_id)
    if case_set is None:
        raise RuntimeError(f"readiness run {summary.run.run_id} did not persist a case set")
    events = _event_types(readiness_store, summary.run.run_id, organization_id=organization_id)
    required_events = {"run_created", "case_generated", "report_written"}
    missing = sorted(required_events - set(events))
    if missing:
        raise RuntimeError(f"readiness run {summary.run.run_id} missed events: {missing}")
    return {
        "run_id": summary.run.run_id,
        "state": summary.run.state,
        "case_set_id": case_set.case_set_id,
        "case_count": len(case_set.cases),
        "voice_case_count": sum(1 for case in case_set.cases if case.voice_input is not None),
        "publish_recommendation": report.publish_recommendation,
        "before_pass_rate": report.before_pass_rate,
        "artifact_uri": report.score_breakdown.get("artifact_uri"),
        "event_count": len(events),
        "provider": report.provider_invocations[0].provider if report.provider_invocations else None,
    }


def _cancel_existing_smoke_runs(
    service: AtlasReadinessService,
    readiness_store: SQLAlchemyAtlasReadinessStore,
    *,
    agent_id: str,
    organization_id: str,
) -> list[str]:
    runs, _total_count, _has_more = readiness_store.list_runs(organization_id=organization_id, agent_id=agent_id, limit=100)
    cancelled_run_ids: list[str] = []
    for run in runs:
        if run.state in {"completed", "failed", "cancelled"}:
            continue
        try:
            service.cancel_run(run.run_id, organization_id=organization_id, reason="smoke_cleanup")
            cancelled_run_ids.append(run.run_id)
        except Exception:
            continue
    return cancelled_run_ids


def _start_run(
    service: AtlasReadinessService,
    request: AtlasReadinessRunRequest,
    *,
    organization_id: str,
    user_id: str,
) -> Any:
    return service.start_run(request, organization_id=organization_id, user_id=user_id)


def _run_deterministic_smoke(
    service: AtlasReadinessService,
    readiness_store: SQLAlchemyAtlasReadinessStore,
    atlas_store: SQLAlchemyAtlasStore,
    *,
    agent_id: str,
    organization_id: str,
    user_id: str,
) -> dict[str, Any]:
    validate = _start_run(
        service,
        AtlasReadinessRunRequest(
            agent_id=agent_id,
            scope="validate",
            provider_policy="deterministic",
            case_limit=2,
            voice_case_count=1,
            seed=7,
            max_estimated_cost_usd=Decimal("0"),
        ),
        organization_id=organization_id,
        user_id=user_id,
    )
    if validate.run.state != "completed":
        raise RuntimeError(f"validate smoke expected completed, got {validate.run.state}")

    fix = _start_run(
        service,
        AtlasReadinessRunRequest(
            agent_id=agent_id,
            scope="fix",
            provider_policy="deterministic",
            case_limit=4,
            voice_case_count=0,
            seed=3,
            max_estimated_cost_usd=Decimal("0"),
        ),
        organization_id=organization_id,
        user_id=user_id,
    )
    if fix.run.state != "awaiting_review":
        raise RuntimeError(f"fix smoke expected awaiting_review, got {fix.run.state}")
    if fix.run.atlas_session_id is None:
        raise RuntimeError("fix smoke did not create an Atlas review session")
    proposed = atlas_store.load_proposed_changes(fix.run.atlas_session_id, organization_id=organization_id)
    if not proposed.step_deltas:
        raise RuntimeError("fix smoke did not persist reviewable step deltas")
    fix_payload = {
        **_run_summary_payload(fix, readiness_store, organization_id=organization_id),
        "atlas_session_id": fix.run.atlas_session_id,
        "proposed_step_delta_count": len(proposed.step_deltas),
    }
    cancelled_fix = service.cancel_run(fix.run.run_id, organization_id=organization_id, reason="smoke_cleanup")
    fix_payload["cleanup_state"] = cancelled_fix.run.state

    rerun = service.rerun(validate.run.run_id, organization_id=organization_id, user_id=user_id)
    if rerun.case_set is None or validate.case_set is None or rerun.case_set.case_set_id != validate.case_set.case_set_id:
        raise RuntimeError("rerun smoke did not reuse the original case set")

    runs, total_count, _has_more = readiness_store.list_runs(organization_id=organization_id, agent_id=agent_id)
    if not any(run.run_id == validate.run.run_id for run in runs):
        raise RuntimeError("run history did not include the validate smoke run")

    return {
        "validate": _run_summary_payload(validate, readiness_store, organization_id=organization_id),
        "fix": fix_payload,
        "rerun": _run_summary_payload(rerun, readiness_store, organization_id=organization_id),
        "history_total_count": total_count,
    }


def _run_google_smoke(
    service: AtlasReadinessService,
    readiness_store: SQLAlchemyAtlasReadinessStore,
    *,
    agent_id: str,
    organization_id: str,
    user_id: str,
    require_google: bool,
    skip_google: bool,
    voice_audio_uri: str | None,
    voice_language: str | None,
    require_real_voice: bool,
) -> dict[str, Any]:
    if skip_google:
        if require_google:
            raise RuntimeError("--skip-google cannot be combined with --require-google")
        return {"status": "skipped", "reason": "skip_google_requested"}

    health = service.provider_health(provider_policy="google_only")
    health_payload = health.model_dump(mode="json")
    if not health.gemini_configured:
        if require_google:
            raise RuntimeError(f"google_only smoke required but Gemini is not configured: {health.warnings}")
        return {"status": "skipped", "reason": "gemini_not_configured", "provider_health": health_payload}
    if require_real_voice and not voice_audio_uri:
        raise RuntimeError("real Google voice smoke requires --voice-audio-uri or RUHU_ATLAS_SMOKE_VOICE_AUDIO_URI")

    google = _start_run(
        service,
        AtlasReadinessRunRequest(
            agent_id=agent_id,
            scope="validate",
            provider_policy="google_only",
            case_limit=1,
            voice_case_count=1,
            voice_audio_uri=voice_audio_uri,
            voice_language=voice_language,
            require_real_voice_io=require_real_voice,
            seed=11,
            max_estimated_cost_usd=Decimal("1.00"),
        ),
        organization_id=organization_id,
        user_id=user_id,
    )
    if google.run.state != "completed":
        raise RuntimeError(f"google_only smoke expected completed, got {google.run.state}: {google.run.error}")
    if google.report is None:
        raise RuntimeError(f"google_only smoke run {google.run.run_id} did not write a report")
    blocked_invocations = [
        {
            "provider": item.provider,
            "model": item.model,
            "role": item.role,
            "validation_outcome": item.validation_outcome,
            "fallback_reason": item.fallback_reason,
        }
        for item in google.report.provider_invocations
        if item.validation_outcome in {"blocked", "invalid"} or item.fallback_reason
    ]
    if blocked_invocations:
        raise RuntimeError(f"google_only provider invocation did not complete cleanly: {blocked_invocations}")
    payload = _run_summary_payload(google, readiness_store, organization_id=organization_id)
    payload["status"] = "completed"
    payload["provider_health"] = health_payload
    payload["real_voice_required"] = require_real_voice
    payload["voice_audio_uri_configured"] = bool(voice_audio_uri)
    return payload


def main() -> int:
    args = _parse_args()
    if not args.database_url.strip():
        print("error: DB URL not set. Pass --database-url or set RUHU_DATABASE_URL.", file=sys.stderr)
        return 2

    try:
        database_url = resolve_database_url(database_url=args.database_url)
        service, readiness_store, atlas_store, registry = _build_service(database_url)
        _ensure_smoke_agent(registry, agent_id=args.agent_id, organization_id=args.organization_id)
        cleaned_up_run_ids = _cancel_existing_smoke_runs(
            service,
            readiness_store,
            agent_id=args.agent_id,
            organization_id=args.organization_id,
        )
        deterministic = _run_deterministic_smoke(
            service,
            readiness_store,
            atlas_store,
            agent_id=args.agent_id,
            organization_id=args.organization_id,
            user_id=args.user_id,
        )
        google = _run_google_smoke(
            service,
            readiness_store,
            agent_id=args.agent_id,
            organization_id=args.organization_id,
            user_id=args.user_id,
            require_google=bool(args.require_google),
            skip_google=bool(args.skip_google),
            voice_audio_uri=str(args.voice_audio_uri or "").strip() or None,
            voice_language=str(args.voice_language or "").strip() or None,
            require_real_voice=bool(args.require_real_voice),
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "agent_id": args.agent_id,
                "organization_id": args.organization_id,
                "cleaned_up_run_ids": cleaned_up_run_ids,
                "deterministic": deterministic,
                "google_only": google,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
