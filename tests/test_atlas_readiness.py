from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from ruhu.agent_document import AgentDocument, Scenario, Step, StepCompletion, StepTransition
from ruhu.atlas_readiness import (
    AtlasReadinessCase,
    AtlasReadinessPatchProposal,
    run_atlas_readiness_loop,
)
from ruhu.heuristics import KeywordInterpreter
from ruhu.api import build_default_app
from ruhu.atlas_readiness_models import AtlasReadinessRun, AtlasReadinessRunRequest
from ruhu.atlas_readiness_service import AtlasReadinessService
from ruhu.atlas_readiness_store import SQLAlchemyAtlasReadinessStore
from ruhu.atlas_store import SQLAlchemyAtlasStore
from ruhu.db import build_session_factory
from ruhu.db_models import AtlasModelInvocationRecord, AtlasReadinessApplyLockRecord, AtlasReadinessRunRecord, AtlasVoiceArtifactRecord
from ruhu.blob_store import InMemoryBlobStore
from ruhu.registry import SQLAlchemyAgentRegistry
from ruhu.voice.protocol import VoiceSynthesisResult


def _handoff_demo_document(*, with_handoff_transition: bool = False) -> AgentDocument:
    transitions = [
        StepTransition(
            id="t_payment_dispute",
            when={
                "kind": "outcome",
                "event": "payment_dispute",
                "description": "The customer says a repayment was made but did not reflect.",
            },
            to_step_id="payment_dispute",
            priority=10,
        )
    ]
    if with_handoff_transition:
        transitions.insert(
            0,
            StepTransition(
                id="t_handoff_request",
                when={
                    "kind": "outcome",
                    "event": "handoff_request",
                    "description": "The customer explicitly asks to speak with a human support officer.",
                },
                to_step_id="handoff",
                priority=1,
            ),
        )
    transitions.append(
        StepTransition(
            id="t_otherwise",
            when={"kind": "otherwise"},
            to_step_id="entry",
            priority=100,
        )
    )
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
                        transitions=transitions,
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


@pytest.mark.parametrize(
    "audio_uri",
    ["http://evil.example/clip.wav", "file:///etc/passwd", "gs://", "", "s3://bucket/clip"],
)
def test_voice_harness_rejects_non_gcs_audio_uri(audio_uri) -> None:
    """AR-2.6: caller-supplied audio_uri must be a gs:// object reference."""
    from ruhu.atlas_voice_harness import GoogleAtlasVoiceHarness

    harness = GoogleAtlasVoiceHarness()
    transcript, confidence, error = harness._transcribe_google_audio_uri(audio_uri, language="en-US")
    assert transcript == ""
    assert confidence == 0.0
    assert error == "rejected_non_gcs_audio_uri"


def _scoring_service() -> "AtlasReadinessService":
    # _score_case / _fact_failures / _trace_events touch no stores.
    return AtlasReadinessService(agent_registry=None, atlas_store=None, readiness_store=None)


def _case(**overrides):
    from ruhu.atlas_readiness_models import AtlasReadinessCase, AtlasSyntheticTestProfile

    base = dict(
        case_id="c1",
        test_profile=AtlasSyntheticTestProfile(
            profile_id="p1", locale="en-US", channel="chat",
            language_style="plain", emotional_state="neutral", goal="g",
        ),
        scenario_summary="s",
        utterances=["hi"],
    )
    base.update(overrides)
    return AtlasReadinessCase(**base)


def _trace(**overrides):
    from ruhu.atlas_readiness_models import AtlasReadinessTrace

    base = dict(case_id="c1", conversation_id="conv1", final_step_id="done")
    base.update(overrides)
    return AtlasReadinessTrace(**base)


def test_propose_deltas_validation_drops_dangling_transition_targets() -> None:
    """AR-4.7: a generated delta whose transition target isn't a real step is
    dropped before it can enter the review set."""
    from ruhu.atlas_protocol import StepDelta

    svc = _scoring_service()
    doc = AgentDocument(
        start_scenario_id="s",
        scenarios=[
            Scenario(
                id="s",
                name="S",
                start_step_id="start",
                steps=[
                    Step(
                        id="start",
                        name="Start",
                        transitions=[
                            StepTransition(id="t", when={"kind": "otherwise"}, to_step_id="done", priority=100)
                        ],
                    ),
                    Step(id="done", name="Done", completion=StepCompletion(disposition="resolved")),
                ],
            )
        ],
    )

    def _delta(to_step_id: str, delta_id: str) -> StepDelta:
        return StepDelta(
            agent_id="a",
            scenario_id="s",
            step_id="start",
            delta_id=delta_id,
            operation="update",
            change_type="add_step_transition",
            payload={"transition": {"id": "x", "to_step_id": to_step_id, "when": {"kind": "otherwise"}}},
            summary="route",
        )

    valid, dropped = svc._validate_step_deltas(
        doc, [_delta("done", "ok"), _delta("ghost_step", "bad")]
    )
    assert dropped == 1
    assert [d.delta_id for d in valid] == ["ok"]


def _readiness_score(case_id: str, *, case_score: float, blockers=None):
    from ruhu.atlas_readiness_models import AtlasReadinessScore

    return AtlasReadinessScore(
        case_id=case_id,
        passed=not blockers,
        score_source="deterministic",
        containment_score=1.0,
        safety_score=1.0,
        traceability_score=1.0,
        operational_readiness_score=1.0,
        improvement_potential_score=1.0,
        trajectory_score=1.0,
        case_score=case_score,
        failures=[],
        blockers=list(blockers or []),
    )


@pytest.mark.parametrize(
    "scores,expected",
    [
        ([("c", 0.95, [])], "publish"),  # >=0.90, no blockers
        ([("c", 0.80, ["trajectory"])], "needs_review"),  # 0.75-0.90, non-critical blocker
        ([("c", 0.80, ["safety"])], "do_not_publish"),  # critical blocker forces down
        ([("c", 0.50, [])], "do_not_publish"),  # below 0.75
    ],
)
def test_build_report_recommendation_boundaries(scores, expected) -> None:
    """AR-5.2: the 0.90 / 0.75 publish thresholds + critical-blocker override."""
    from ruhu.atlas_model_gateway import AtlasModelGateway
    from ruhu.atlas_protocol import AtlasProposedChanges

    svc = _scoring_service()
    report = svc._build_report(
        run_id="r1",
        agent_id="a1",
        scores=[_readiness_score(cid, case_score=cs, blockers=bl) for cid, cs, bl in scores],
        proposed_changes=AtlasProposedChanges(),
        provider_policy="deterministic",
        gateway=AtlasModelGateway(provider_policy="deterministic"),
    )
    assert report.publish_recommendation == expected


def test_resolve_document_raises_when_requested_version_has_no_document() -> None:
    """AR-4.5 (F7): evaluating a specific version with no stored document must
    not silently fall back to draft/published."""
    from types import SimpleNamespace

    class _Registry:
        def get_version_snapshot(self, version_id, *, organization_id=None):
            return SimpleNamespace(agent_id="a1", version_id=version_id, agent_document=None)

    svc = AtlasReadinessService(
        agent_registry=_Registry(), atlas_store=None, readiness_store=None
    )
    with pytest.raises(ValueError, match="has no stored document"):
        svc._resolve_document(
            AtlasReadinessRunRequest(agent_id="a1", agent_version_id="v1", scope="validate"),
            organization_id="org-a",
        )


def test_capture_normalized_fact_comparison_matches_typed_capture() -> None:
    """AR-4.3: the default capture_normalized policy normalizes both sides."""
    svc = _scoring_service()
    case = _case(
        expected_facts={"amount": "5000"},
        fact_comparison_policy="capture_normalized",
    )
    # Captured as a typed money structure — should match the scalar expectation.
    trace_ok = _trace(extracted_facts={"amount": {"value": 5000, "currency": "NGN"}})
    assert svc._fact_failures(case, trace_ok) == []
    # A genuinely different value still fails.
    trace_bad = _trace(extracted_facts={"amount": {"value": 9999, "currency": "NGN"}})
    assert svc._fact_failures(case, trace_bad)


def test_required_trace_events_missing_fails_traceability() -> None:
    """AR-4.3: a trace lacking required events can't score traceability 1.0."""
    svc = _scoring_service()
    case = _case(required_trace_events=["start", "complete"], expected_final_step_ids=["done"])
    # No completion_status and empty step_path → neither event present.
    trace = _trace(step_path=[], completion_status=None)
    score = svc._score_case(case, trace)
    assert score.traceability_score <= 0.5
    assert "traceability" in score.blockers
    assert not score.passed


def test_improvement_potential_below_threshold_blocks() -> None:
    """AR-4.3: improvement_potential < 0.70 is a category blocker."""
    svc = _scoring_service()
    # A missing-required-trace-event failure can't be mapped to a fixable delta
    # → improvement_score drops to 0.4.
    case = _case(expected_final_step_ids=["done"], required_trace_events=["start", "complete"])
    trace = _trace(final_step_id="done", step_path=[], completion_status=None)
    score = svc._score_case(case, trace)
    assert score.improvement_potential_score < 0.70
    assert "improvement_potential" in score.blockers


def test_privacy_scrubber_redacts_secrets_without_eating_identifiers() -> None:
    """AR-2.5: secret keys are redacted; identifier keys (…_id, author) survive."""
    from ruhu.atlas_readiness_privacy import AtlasReadinessPrivacyScrubber

    scrubber = AtlasReadinessPrivacyScrubber()
    out = scrubber.scrub(
        {
            "atlas_session_id": "sess_123",
            "author": "jane",
            "agent_id": "sales",
            "delta_count": 3,
            "access_token": "tok_secret",
            "x_api_key": "key_secret",
            "client_secret": "shh",
            "authorization": "Bearer abc",
            "password": "hunter2",
            "nested": {"refresh_token": "r", "note": "ok"},
        }
    )
    # Identifiers and ordinary fields preserved (audit linkage intact).
    assert out["atlas_session_id"] == "sess_123"
    assert out["author"] == "jane"
    assert out["agent_id"] == "sales"
    assert out["delta_count"] == 3
    assert out["nested"]["note"] == "ok"
    # Secrets redacted, including nested and split api-key forms.
    for key in ["access_token", "x_api_key", "client_secret", "authorization", "password"]:
        assert out[key] == "[REDACTED]"
    assert out["nested"]["refresh_token"] == "[REDACTED]"


def test_atlas_readiness_loop_proposes_patch_for_failed_handoff() -> None:
    interpreter = KeywordInterpreter(
        rules={
            "payment_dispute": ("paid", "repayment", "reflect"),
            "handoff_request": ("person", "human", "transfer"),
        }
    )
    cases = [
        AtlasReadinessCase(
            case_id="payment_reflected",
            persona="Borrower",
            description="Payment did not reflect.",
            utterances=["I paid but my repayment no reflect"],
            expected_final_step_ids=["payment_dispute"],
        ),
        AtlasReadinessCase(
            case_id="human_request",
            persona="Angry borrower",
            description="Customer asks for human support.",
            utterances=["Transfer me to a human person"],
            expected_final_step_ids=["handoff"],
        ),
    ]

    report = run_atlas_readiness_loop(
        _handoff_demo_document(),
        cases=cases,
        interpreter=interpreter,
        agent_id="microfinance_demo",
        agent_name="Microfinance Demo",
    )

    assert report.publish_recommendation == "do_not_publish"
    assert report.before_pass_rate == 0.5
    assert [score.case_id for score in report.before_scores if not score.passed] == ["human_request"]
    assert report.patch_proposals
    assert report.patch_proposals[0].target == "transition"


def test_atlas_readiness_loop_reruns_after_approved_patch() -> None:
    interpreter = KeywordInterpreter(
        rules={
            "payment_dispute": ("paid", "repayment", "reflect"),
            "handoff_request": ("person", "human", "transfer"),
        }
    )
    cases = [
        AtlasReadinessCase(
            case_id="human_request",
            persona="Angry borrower",
            description="Customer asks for human support.",
            utterances=["Transfer me to a human person"],
            expected_final_step_ids=["handoff"],
        )
    ]

    def apply_demo_patch(
        document: AgentDocument,
        proposals: list[AtlasReadinessPatchProposal],
    ) -> AgentDocument:
        assert proposals
        patched = deepcopy(document)
        return _handoff_demo_document(with_handoff_transition=True).model_copy(
            update={"metadata": patched.metadata}
        )

    report = run_atlas_readiness_loop(
        _handoff_demo_document(),
        cases=cases,
        interpreter=interpreter,
        agent_id="microfinance_demo",
        agent_name="Microfinance Demo",
        apply_recommended_patches=apply_demo_patch,
    )

    # AR-4.5 (F19): patches are validated against a discarded copy, never
    # applied to the real agent — so the existing (still-failing) agent stays
    # do_not_publish, and proposals remain 'proposed'. after_pass_rate shows the
    # patches would resolve the failures.
    assert report.before_pass_rate == 0.0
    assert report.after_pass_rate == 1.0
    assert report.publish_recommendation == "do_not_publish"
    assert all(proposal.status == "proposed" for proposal in report.patch_proposals)


@pytest.mark.asyncio
async def test_atlas_readiness_api_creates_run_report_events_and_rerun(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    monkeypatch.delenv("RUHU_ATLAS_GOOGLE_VERTEX_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("RUHU_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # This test exercises the microfinance demo agent end-to-end (2 demo cases).
    monkeypatch.setenv("RUHU_ATLAS_READINESS_DEMO_CASES", "1")

    database_url = postgres_database_url_factory()
    app = build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/atlas/readiness/runs",
            json={
                "workflow_brief": "A repayment support workflow for ada@example.com at +234 801 234 5678 that handles disputes and handoff.",
                "scope": "validate",
                "provider_policy": "deterministic",
                "case_limit": 2,
                "voice_case_count": 1,
                "seed": 7,
                "max_estimated_cost_usd": "0",
            },
        )
        assert created.status_code == 200, created.text
        payload = created.json()
        run_id = payload["run"]["run_id"]
        assert payload["run"]["state"] == "completed"
        assert payload["case_set"]["case_set_id"]
        assert len(payload["case_set"]["cases"]) == 2
        assert payload["report"]["publish_recommendation"] in {"publish", "needs_review", "do_not_publish"}
        assert payload["report"]["provider_invocations"][0]["provider"] == "deterministic"
        assert payload["report"]["score_breakdown"]["artifact_uri"].startswith("in_memory://")

        events = await client.get(f"/atlas/readiness/runs/{run_id}/events")
        assert events.status_code == 200
        event_types = [item["type"] for item in events.json()["events"]]
        assert "run_created" in event_types
        assert "report_artifact_written" in event_types
        assert "report_written" in event_types

        report = await client.get(f"/atlas/readiness/runs/{run_id}/report")
        assert report.status_code == 200
        assert report.json()["run_id"] == run_id

        listed = await client.get("/atlas/readiness/runs")
        assert listed.status_code == 200
        assert listed.json()["total_count"] == 1
        assert listed.json()["runs"][0]["run_id"] == run_id

        health = await client.get("/atlas/readiness/provider-health?provider_policy=google_only")
        assert health.status_code == 200
        assert health.json()["provider_policy"] == "google_only"
        assert "gemini_provider_not_configured" in health.json()["warnings"]

        rerun = await client.post(f"/atlas/readiness/runs/{run_id}/rerun")
        assert rerun.status_code == 200
        assert rerun.json()["case_set"]["case_set_id"] == payload["case_set"]["case_set_id"]

    session_factory = build_session_factory(database_url)
    with session_factory() as session:
        invocation_count = session.execute(
            select(func.count()).select_from(AtlasModelInvocationRecord).where(AtlasModelInvocationRecord.run_id == run_id)
        ).scalar_one()
        voice_artifact_count = session.execute(
            select(func.count()).select_from(AtlasVoiceArtifactRecord).where(AtlasVoiceArtifactRecord.run_id == run_id)
        ).scalar_one()
        stored_run = session.get(AtlasReadinessRunRecord, run_id)
        stored_workflow_brief = stored_run.request_json["workflow_brief"] if stored_run is not None else None
    assert invocation_count == 1
    assert voice_artifact_count == 1
    assert stored_run is not None
    assert stored_workflow_brief is not None
    assert "ada@example.com" not in stored_workflow_brief
    assert "+234 801 234 5678" not in stored_workflow_brief
    assert "[REDACTED]" in stored_workflow_brief


def test_atlas_readiness_fix_run_saves_reviewable_deltas_and_is_tenant_scoped(
    postgres_database_url_factory,
) -> None:
    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo",
        agent_name="Readiness Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
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
        artifact_store=InMemoryBlobStore(),
        demo_case_set=True,  # the demo agent IS microfinance-shaped (AR-4.2)
    )

    summary = service.start_run(
        AtlasReadinessRunRequest(
            agent_id="readiness_demo",
            scope="fix",
            provider_policy="deterministic",
            case_limit=4,
            seed=3,
            max_estimated_cost_usd=0,
        ),
        organization_id="org-a",
        user_id="author",
    )

    assert summary.run.state == "awaiting_review"
    assert summary.report is not None
    assert str(summary.report.score_breakdown["artifact_uri"]).startswith("in_memory://")
    assert summary.run.atlas_session_id is not None
    proposed = atlas_store.load_proposed_changes(summary.run.atlas_session_id, organization_id="org-a")
    assert proposed.step_deltas
    assert all(delta.payload["source_run_id"] == summary.run.run_id for delta in proposed.step_deltas)
    with session_factory() as session:
        lock_count = session.execute(
            select(func.count()).select_from(AtlasReadinessApplyLockRecord).where(AtlasReadinessApplyLockRecord.run_id == summary.run.run_id)
        ).scalar_one()
    assert lock_count == 1
    assert readiness_store.get_run(summary.run.run_id, organization_id="org-b") is None
    assert readiness_store.get_report(summary.run.run_id, organization_id="org-b") is None

    # AR-4.6: publish review reads the latest verdict + held fix-run lock.
    latest = readiness_store.latest_report_for_agent("readiness_demo", organization_id="org-a")
    assert latest is not None
    assert latest.publish_recommendation == summary.report.publish_recommendation
    assert readiness_store.latest_report_for_agent("readiness_demo", organization_id="org-b") is None
    assert readiness_store.has_active_apply_lock(
        "readiness_demo", summary.run.agent_version_id, organization_id="org-a"
    )
    assert not readiness_store.has_active_apply_lock(
        "readiness_demo", "nonexistent_version", organization_id="org-a"
    )

    # AR-4.4: a second fix run while the lock is held fails fast (before
    # burning a full simulation suite) with the lock-conflict error.
    with pytest.raises(ValueError, match="readiness_apply_lock_conflict"):
        service.start_run(
            AtlasReadinessRunRequest(
                agent_id="readiness_demo",
                scope="fix",
                provider_policy="deterministic",
                case_limit=4,
                seed=3,
                max_estimated_cost_usd=0,
            ),
            organization_id="org-a",
            user_id="author",
        )

    cancelled = service.cancel_run(summary.run.run_id, organization_id="org-a")
    assert cancelled.run.state == "cancelled"
    assert "operator_cancelled" in cancelled.run.blocker_codes
    with session_factory() as session:
        lock_count = session.execute(
            select(func.count()).select_from(AtlasReadinessApplyLockRecord).where(AtlasReadinessApplyLockRecord.run_id == summary.run.run_id)
        ).scalar_one()
    assert lock_count == 0
    with pytest.raises(ValueError):
        service.cancel_run(summary.run.run_id, organization_id="org-a")


def test_atlas_readiness_default_cases_are_document_derived(
    postgres_database_url_factory,
) -> None:
    """AR-4.2: with the demo set off (default), cases come from the agent's own
    document — expected steps are the document's real step IDs, not microfinance
    payment_dispute/handoff."""
    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)

    # A non-microfinance agent: a simple booking flow.
    doc = AgentDocument(
        start_scenario_id="booking",
        scenarios=[
            Scenario(
                id="booking",
                name="Booking",
                start_step_id="greet",
                steps=[
                    Step(
                        id="greet",
                        name="Greet",
                        say="Hi, want to book?",
                        transitions=[
                            StepTransition(
                                id="t_book",
                                when={"kind": "otherwise"},
                                to_step_id="booked",
                                priority=100,
                            )
                        ],
                    ),
                    Step(
                        id="booked",
                        name="Booked",
                        completion=StepCompletion(disposition="resolved", summary="Booked."),
                    ),
                ],
            )
        ],
    )
    registry.create_agent_document(
        agent_id="booking_demo", agent_name="Booking Demo", organization_id="org-a", document=doc
    )
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
        artifact_store=InMemoryBlobStore(),
    )  # demo_case_set defaults False

    summary = service.start_run(
        AtlasReadinessRunRequest(
            agent_id="booking_demo",
            scope="validate",
            provider_policy="deterministic",
            case_limit=4,
            seed=1,
            max_estimated_cost_usd=0,
        ),
        organization_id="org-a",
        user_id="author",
    )

    assert summary.case_set is not None
    expected_union: set[str] = set()
    for case in summary.case_set.cases:
        expected_union.update(case.expected_final_step_ids)
    # Derived from the booking document, never the microfinance demo steps.
    assert expected_union <= {"greet", "booked"}
    assert "payment_dispute" not in expected_union
    assert "handoff" not in expected_union


def test_atlas_readiness_google_policy_records_competition_provider_roles(
    postgres_database_url_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RUHU_ATLAS_GOOGLE_VERTEX_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("RUHU_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    service = AtlasReadinessService(
        agent_registry=None,
        atlas_store=None,
        readiness_store=SQLAlchemyAtlasReadinessStore(session_factory),
        artifact_store=InMemoryBlobStore(),
    )

    summary = service.start_run(
        AtlasReadinessRunRequest(
            workflow_brief="Nigerian microfinance repayment support for failed wallet transfers and human handoff.",
            scope="validate",
            provider_policy="google_only",
            demo_case_set=True,
            case_limit=2,
            seed=4,
            max_estimated_cost_usd="1.00",
        ),
        organization_id="org-a",
        user_id="author",
    )

    assert summary.report is not None
    roles = {item.role for item in summary.report.provider_invocations}
    assert {
        "orchestrator",
        "workflow_understanding",
        "draft_generator",
        "case_generator",
        "report_writer",
    } <= roles
    assert all(item.provider == "gemini" for item in summary.report.provider_invocations)
    assert any(item.fallback_reason == "google_provider_not_configured" for item in summary.report.provider_invocations)
    assert summary.report.narrative["executive_summary"]
    assert summary.report.evidence["hosting_target"] == "cloud_run"
    assert summary.report.score_breakdown["adk_bounded_plan"]["tool_calls"]


def test_atlas_provider_prompts_are_redacted_before_external_egress(
    postgres_database_url_factory,
) -> None:
    from decimal import Decimal

    from ruhu.atlas_readiness_models import AtlasProviderInvocationMetadata

    class CapturingGateway:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def generate_structured(
            self,
            *,
            role,
            schema_name,
            prompt,
            response_model,
            trace_context,
            temperature_policy,
            cancellation_token=None,
        ):
            self.prompts.append(prompt)
            payload = trace_context.get("deterministic_response")
            result = response_model.model_validate(payload if isinstance(payload, dict) else {})
            return result, AtlasProviderInvocationMetadata(
                provider="captured",
                model="test",
                role=role,
                latency_ms=0,
                prompt_tokens=0,
                completion_tokens=0,
                estimated_cost_usd=Decimal("0"),
                validation_outcome="valid",
                timeout_seconds=1,
            )

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    gateway = CapturingGateway()
    service = AtlasReadinessService(
        agent_registry=None,
        atlas_store=None,
        readiness_store=SQLAlchemyAtlasReadinessStore(session_factory),
        artifact_store=InMemoryBlobStore(),
        model_gateway=gateway,  # type: ignore[arg-type]
    )

    service.start_run(
        AtlasReadinessRunRequest(
            workflow_brief=(
                "Repayment support for ada@example.com at +234 801 234 5678. "
                "If stuck, call Jane on 08012345678."
            ),
            scope="validate",
            provider_policy="google_only",
            case_limit=2,
            max_estimated_cost_usd=Decimal("1.00"),
        ),
        organization_id="org-a",
        user_id="author",
    )

    assert gateway.prompts
    joined = "\n".join(gateway.prompts)
    assert "ada@example.com" not in joined
    assert "+234 801 234 5678" not in joined
    assert "08012345678" not in joined


def test_atlas_non_deterministic_runs_require_explicit_budget() -> None:
    svc = _scoring_service()
    with pytest.raises(ValueError, match="require max_estimated_cost_usd"):
        svc._check_budget(
            AtlasReadinessRunRequest(
                workflow_brief="Support workflow",
                scope="validate",
                provider_policy="google_only",
                case_limit=1,
            ),
            "google_only",
        )


def test_atlas_readiness_enforces_wall_clock_budget(
    postgres_database_url_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AR-4.1: a run that exceeds max_wall_clock_seconds fails with a
    timeout_exceeded blocker instead of running indefinitely."""
    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo",
        agent_name="Readiness Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
        interpreter=KeywordInterpreter(rules={"payment_dispute": ("paid", "repayment", "reflect")}),
        artifact_store=InMemoryBlobStore(),
    )

    # Clock: deadline is captured on the first monotonic() call (=0.0); every
    # later call returns a time well past the 1s ceiling so the first per-case
    # deadline check trips.
    # Each call advances the clock by a full hour, so whichever call captures
    # the deadline, the next monotonic() read (the first per-case deadline
    # check) is already far past it — independent of how many internal callers
    # read the clock first.
    clock = {"t": 0.0}

    def fake_monotonic() -> float:
        clock["t"] += 3600.0
        return clock["t"]

    monkeypatch.setattr("ruhu.atlas_readiness_service.time.monotonic", fake_monotonic)

    with pytest.raises(ValueError, match="timeout_exceeded"):
        service.start_run(
            AtlasReadinessRunRequest(
                agent_id="readiness_demo",
                scope="validate",
                provider_policy="deterministic",
                case_limit=4,
                max_wall_clock_seconds=1,
                seed=1,
                max_estimated_cost_usd=0,
            ),
            organization_id="org-a",
            user_id="author",
        )

    runs, _total, _more = readiness_store.list_runs(organization_id="org-a")
    run = runs[0]
    assert run.state == "failed"
    assert "timeout_exceeded" in run.blocker_codes


def test_atlas_readiness_update_run_rejects_un_terminating_a_cancelled_run(
    postgres_database_url_factory,
) -> None:
    """AR-1.4: a terminal run cannot be transitioned back to a running state.

    This is the store-level guard that stops an in-flight worker which raced
    past cancellation from silently un-cancelling the run.
    """
    from ruhu.atlas_readiness_models import AtlasReadinessRunTerminal

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo",
        agent_name="Readiness Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )

    now = datetime.now(timezone.utc)
    run = AtlasReadinessRun(
        run_id="run_terminal_guard",
        organization_id="org-a",
        agent_id="readiness_demo",
        agent_version_id=None,
        scope="validate",
        state="running_simulations",
        provider_policy="deterministic",
        request=AtlasReadinessRunRequest(agent_id="readiness_demo", scope="validate"),
        created_by_user_id="author",
        created_at=now,
        updated_at=now,
    )
    readiness_store.create_run(run)

    cancelled = readiness_store.update_run(
        run.run_id, organization_id="org-a", state="cancelled", completed_at=datetime.now(timezone.utc)
    )
    assert cancelled.state == "cancelled"

    with pytest.raises(AtlasReadinessRunTerminal):
        readiness_store.update_run(
            run.run_id, organization_id="org-a", state="running_simulations"
        )
    # The run is still cancelled — the racing transition did not take effect.
    assert readiness_store.get_run(run.run_id, organization_id="org-a").state == "cancelled"


def test_atlas_readiness_cancel_run_signals_registered_in_flight_token(
    postgres_database_url_factory,
) -> None:
    """AR-1.4: cancel_run signals the in-flight worker's token so it stops."""
    from ruhu.atlas_readiness_models import SimpleAtlasCancellationToken

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo",
        agent_name="Readiness Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
        artifact_store=InMemoryBlobStore(),
    )

    now = datetime.now(timezone.utc)
    run = AtlasReadinessRun(
        run_id="run_token_signal",
        organization_id="org-a",
        agent_id="readiness_demo",
        agent_version_id=None,
        scope="validate",
        state="running_simulations",
        provider_policy="deterministic",
        request=AtlasReadinessRunRequest(agent_id="readiness_demo", scope="validate"),
        created_by_user_id="author",
        created_at=now,
        updated_at=now,
    )
    readiness_store.create_run(run)
    token = SimpleAtlasCancellationToken()
    service._register_cancellation_token(run.run_id, token)

    service.cancel_run(run.run_id, organization_id="org-a")

    # The worker's token is now signalled; its next throw_if_cancelled fires.
    assert token.is_cancelled() is True
    with pytest.raises(Exception):
        token.throw_if_cancelled()
    assert readiness_store.get_run(run.run_id, organization_id="org-a").state == "cancelled"


def test_atlas_readiness_list_events_paginates(postgres_database_url_factory) -> None:
    """AR-5.2: list_events honors limit/after_sequence and reports has_more."""
    from ruhu.atlas_readiness_models import AtlasReadinessEvent, new_atlas_readiness_event_id

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents", database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo", agent_name="Readiness Demo", organization_id="org-a",
        document=_handoff_demo_document(),
    )
    now = datetime.now(timezone.utc)
    readiness_store.create_run(
        AtlasReadinessRun(
            run_id="run_events", organization_id="org-a", agent_id="readiness_demo",
            agent_version_id=None, scope="validate", state="running_simulations",
            provider_policy="deterministic",
            request=AtlasReadinessRunRequest(agent_id="readiness_demo", scope="validate"),
            created_by_user_id="author", created_at=now, updated_at=now,
        )
    )
    for i in range(5):
        readiness_store.append_event(
            AtlasReadinessEvent(
                event_id=new_atlas_readiness_event_id(), run_id="run_events",
                sequence_number=0, type="node_started", payload={"i": i}, created_at=now,
            ),
            organization_id="org-a",
        )

    page1, total, has_more = readiness_store.list_events("run_events", organization_id="org-a", limit=2)
    assert total == 5 and has_more is True and len(page1) == 2
    last_seq = page1[-1].sequence_number
    page2, _total, has_more2 = readiness_store.list_events(
        "run_events", organization_id="org-a", after_sequence=last_seq, limit=10
    )
    assert has_more2 is False
    assert [e.sequence_number for e in page2] == [last_seq + 1, last_seq + 2, last_seq + 3]


def test_atlas_readiness_store_requires_explicit_org_scope(
    postgres_database_url_factory,
) -> None:
    """F17: organization_id is required on every scoped store method — None or
    omission is a hard error; deliberate cross-tenant access must use
    ATLAS_SYSTEM_SCOPE."""
    from ruhu.atlas_readiness_store import ATLAS_SYSTEM_SCOPE

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents", database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo", agent_name="Readiness Demo", organization_id="org-a",
        document=_handoff_demo_document(),
    )
    now = datetime.now(timezone.utc)
    readiness_store.create_run(
        AtlasReadinessRun(
            run_id="run_scope_test", organization_id="org-a", agent_id="readiness_demo",
            agent_version_id=None, scope="validate", state="running_simulations",
            provider_policy="deterministic",
            request=AtlasReadinessRunRequest(agent_id="readiness_demo", scope="validate"),
            created_by_user_id="author", created_at=now, updated_at=now,
        )
    )

    # Omitting or passing None is a hard error — never silently unscoped.
    with pytest.raises(ValueError, match="requires organization_id"):
        readiness_store.get_run("run_scope_test")
    with pytest.raises(ValueError, match="requires organization_id"):
        readiness_store.get_run("run_scope_test", organization_id=None)
    with pytest.raises(ValueError, match="requires organization_id"):
        readiness_store.release_apply_lock("run_scope_test")

    # Tenant scoping behaves as before.
    assert readiness_store.get_run("run_scope_test", organization_id="org-a") is not None
    assert readiness_store.get_run("run_scope_test", organization_id="org-b") is None
    # The sentinel grants deliberate cross-tenant access.
    assert readiness_store.get_run("run_scope_test", organization_id=ATLAS_SYSTEM_SCOPE) is not None


def test_atlas_readiness_sweep_recovers_stuck_and_expired_runs(
    postgres_database_url_factory,
) -> None:
    """AR-4.4: the sweep fails crashed in-flight runs and cancels paused runs
    whose TTL elapsed."""
    from ruhu.db_models import AtlasReadinessRunRecord

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="readiness_demo", agent_name="Readiness Demo", organization_id="org-a",
        document=_handoff_demo_document(),
    )
    service = AtlasReadinessService(
        agent_registry=registry, atlas_store=atlas_store, readiness_store=readiness_store,
        artifact_store=InMemoryBlobStore(),
    )

    now = datetime.now(timezone.utc)
    for run_id, state, version in [
        ("run_stuck", "running_simulations", None),
        ("run_paused", "awaiting_review", None),  # no active lock → TTL expired
    ]:
        readiness_store.create_run(
            AtlasReadinessRun(
                run_id=run_id, organization_id="org-a", agent_id="readiness_demo",
                agent_version_id=version, scope="fix", state=state,
                provider_policy="deterministic",
                request=AtlasReadinessRunRequest(agent_id="readiness_demo", scope="fix"),
                created_by_user_id="author", created_at=now, updated_at=now,
            )
        )
    # Backdate updated_at so the stuck run is past the cutoff.
    with session_factory.begin() as session:
        rec = session.get(AtlasReadinessRunRecord, "run_stuck")
        rec.updated_at = now - timedelta(hours=2)

    result = service.sweep_stale_runs(stuck_after_seconds=60, organization_id="org-a")
    assert result["stuck_failed"] == 1
    assert result["paused_expired"] == 1

    stuck = readiness_store.get_run("run_stuck", organization_id="org-a")
    assert stuck.state == "failed" and "run_stuck" in stuck.blocker_codes
    paused = readiness_store.get_run("run_paused", organization_id="org-a")
    assert paused.state == "cancelled" and "pause_ttl_expired" in paused.blocker_codes


def test_atlas_readiness_strict_google_voice_requires_real_audio_uri(
    postgres_database_url_factory,
) -> None:
    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="strict_voice_demo",
        agent_name="Strict Voice Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
        interpreter=KeywordInterpreter(rules={"payment_dispute": ("paid", "repayment", "reflect")}),
        artifact_store=InMemoryBlobStore(),
    )

    with pytest.raises(ValueError, match="real_voice_io_required"):
        service.start_run(
            AtlasReadinessRunRequest(
                agent_id="strict_voice_demo",
                scope="validate",
                provider_policy="deterministic",
                case_limit=1,
                voice_case_count=1,
                require_real_voice_io=True,
                seed=1,
                max_estimated_cost_usd=0,
            ),
            organization_id="org-a",
            user_id="author",
        )


def test_atlas_readiness_exports_google_tts_artifact_for_strict_voice(
    postgres_database_url_factory,
) -> None:
    from ruhu.atlas_voice_harness import GoogleAtlasVoiceHarness

    class _FakeVoiceProvider:
        name = "fake_google_tts"

        def synthesize(self, text: str, *, voice_id: str, language: str) -> VoiceSynthesisResult:
            assert voice_id == "en-US-Chirp3-HD-Kore"
            return VoiceSynthesisResult(
                audio_bytes=b"fake-audio",
                audio_mime_type="audio/mpeg",
                character_count=len(text),
                estimated_cost_usd=0.001,
                provider_metadata={"voice_id": voice_id, "language": language},
            )

    class _FakeGoogleVoiceHarness(GoogleAtlasVoiceHarness):
        def _transcribe_google_audio_uri(self, audio_uri: str, *, language: str) -> tuple[str, float, str | None]:
            assert audio_uri == "gs://ruhu-readiness-fixtures/payment.wav"
            assert language == "en-NG"
            return "I paid but my repayment did not reflect", 0.96, None

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="strict_voice_export_demo",
        agent_name="Strict Voice Export Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
    service = AtlasReadinessService(
        agent_registry=registry,
        atlas_store=atlas_store,
        readiness_store=readiness_store,
        interpreter=KeywordInterpreter(rules={"payment_dispute": ("paid", "repayment", "reflect")}),
        voice_harness=_FakeGoogleVoiceHarness(voice_provider=_FakeVoiceProvider()),
        artifact_store=InMemoryBlobStore(),
    )

    summary = service.start_run(
        AtlasReadinessRunRequest(
            agent_id="strict_voice_export_demo",
            scope="validate",
            provider_policy="deterministic",
            case_limit=1,
            voice_case_count=1,
            voice_audio_uri="gs://ruhu-readiness-fixtures/payment.wav",
            voice_language="en-NG",
            require_real_voice_io=True,
            seed=1,
            max_estimated_cost_usd=0,
        ),
        organization_id="org-a",
        user_id="author",
    )

    assert summary.run.state == "completed"
    with session_factory() as session:
        tts_artifact = session.execute(
            select(AtlasVoiceArtifactRecord).where(
                AtlasVoiceArtifactRecord.run_id == summary.run.run_id,
                AtlasVoiceArtifactRecord.artifact_type == "tts_audio",
            )
        ).scalar_one()
    assert tts_artifact.uri is not None
    assert tts_artifact.uri.startswith("in_memory://")
    assert tts_artifact.metadata_json["artifact_size_bytes"] == len(b"fake-audio")


def test_atlas_model_gateway_provider_policy_records_role_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    from ruhu.atlas_model_gateway import AtlasModelGateway
    from pydantic import BaseModel

    class _Payload(BaseModel):
        pass

    monkeypatch.delenv("RUHU_ATLAS_GOOGLE_VERTEX_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("RUHU_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("RUHU_ATLAS_GENERATOR_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _payload, google_metadata = AtlasModelGateway(provider_policy="google_only").generate_structured(
        role="report_writer",
        schema_name="TestPayload",
        prompt="test",
        response_model=_Payload,
        trace_context={"deterministic_response": {}},
        temperature_policy="deterministic",
    )
    assert google_metadata.provider == "gemini"
    assert google_metadata.fallback_reason == "google_provider_not_configured"

    _payload, anthropic_metadata = AtlasModelGateway(provider_policy="anthropic_only").generate_structured(
        role="report_writer",
        schema_name="TestPayload",
        prompt="test",
        response_model=_Payload,
        trace_context={"deterministic_response": {}},
        temperature_policy="deterministic",
    )
    assert anthropic_metadata.provider == "anthropic"
    assert anthropic_metadata.fallback_reason == "anthropic_provider_not_configured"


def test_atlas_gemini_adapter_inherits_workflow_vertex_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from ruhu.atlas_model_gateway import GeminiAtlasModelAdapter

    monkeypatch.delenv("RUHU_ATLAS_GOOGLE_VERTEX_PROJECT", raising=False)
    monkeypatch.delenv("RUHU_ATLAS_GOOGLE_VERTEX_LOCATION", raising=False)
    monkeypatch.delenv("RUHU_ATLAS_GEMINI_ORCHESTRATOR_MODEL", raising=False)
    monkeypatch.delenv("RUHU_ATLAS_GEMINI_FAST_MODEL", raising=False)
    monkeypatch.delenv("RUHU_GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    monkeypatch.setenv("VERTEX_AI_PROJECT", "workflow-project")
    monkeypatch.setenv("VERTEX_AI_LOCATION", "europe-west2")

    adapter = GeminiAtlasModelAdapter.from_env()

    assert adapter.configured is True
    assert adapter.model == "gemini-3.1-pro-preview"
    assert adapter.vertex_project == "workflow-project"
    assert adapter.vertex_location == "europe-west2"


def test_atlas_model_adapters_parse_configured_provider_json_without_network() -> None:
    from pydantic import BaseModel
    from ruhu.atlas_model_gateway import ClaudeAtlasModelAdapter, GeminiAtlasModelAdapter

    class _Payload(BaseModel):
        decision: str

    def fake_gemini_post(**kwargs):
        assert "generateContent" in kwargs["url"]
        # AR-1.3: the key travels as a header, never a query param (which
        # would leak into logged request URLs).
        assert kwargs["headers"]["x-goog-api-key"] == "test-key"
        assert "key" not in kwargs.get("params", {})
        assert "key=" not in kwargs["url"]
        return {
            "candidates": [{"content": {"parts": [{"text": "{\"decision\":\"publish\"}"}]}}],
            "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 4},
        }

    gemini_payload, gemini_metadata = GeminiAtlasModelAdapter(
        provider="gemini",
        model="gemini-test",
        configured=True,
        fallback_reason="not_configured",
        api_key="test-key",
        http_post=fake_gemini_post,
    ).generate_structured(
        role="report_writer",
        schema_name="Payload",
        prompt="Return a decision.",
        response_model=_Payload,
        trace_context={},
        timeout_seconds=1.0,
        temperature_policy="deterministic",
    )
    assert isinstance(gemini_payload, _Payload)
    assert gemini_payload.decision == "publish"
    assert gemini_metadata.validation_outcome == "valid"
    assert gemini_metadata.prompt_tokens == 12

    def fake_anthropic_post(**kwargs):
        assert kwargs["headers"]["x-api-key"] == "test-key"
        return {
            "content": [{"type": "text", "text": "{\"decision\":\"needs_review\"}"}],
            "usage": {"input_tokens": 9, "output_tokens": 3},
        }

    anthropic_payload, anthropic_metadata = ClaudeAtlasModelAdapter(
        provider="anthropic",
        model="claude-test",
        configured=True,
        fallback_reason="not_configured",
        api_key="test-key",
        http_post=fake_anthropic_post,
    ).generate_structured(
        role="trace_repair_planner",
        schema_name="Payload",
        prompt="Return a decision.",
        response_model=_Payload,
        trace_context={},
        timeout_seconds=1.0,
        temperature_policy="deterministic",
    )
    assert isinstance(anthropic_payload, _Payload)
    assert anthropic_payload.decision == "needs_review"
    assert anthropic_metadata.validation_outcome == "valid"
    assert anthropic_metadata.completion_tokens == 3


def test_atlas_gemini_adapter_does_not_leak_api_key_on_http_error() -> None:
    """AR-1.3: a provider HTTP error must not surface the key.

    An httpx error embeds the request URL in its message; the key must be a
    header (absent from the URL) and the logged/recorded reason must be the
    classified code, never raw str(exc).
    """
    import httpx
    from pydantic import BaseModel
    from ruhu.atlas_model_gateway import GeminiAtlasModelAdapter

    class _Payload(BaseModel):
        decision: str

    def exploding_post(**kwargs):
        # Mirror what httpx would build: the URL carries no secret because the
        # key is a header. Raise a status error whose message contains the URL.
        assert "key=" not in kwargs["url"]
        assert kwargs["headers"]["x-goog-api-key"] == "super-secret-key"
        request = httpx.Request("POST", kwargs["url"])
        response = httpx.Response(403, request=request, json={"error": "forbidden"})
        raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

    payload, metadata = GeminiAtlasModelAdapter(
        provider="gemini",
        model="gemini-test",
        configured=True,
        fallback_reason="not_configured",
        api_key="super-secret-key",
        http_post=exploding_post,
    ).generate_structured(
        role="report_writer",
        schema_name="Payload",
        prompt="Return a decision.",
        response_model=_Payload,
        trace_context={},
        timeout_seconds=1.0,
        temperature_policy="deterministic",
    )

    assert isinstance(payload, _Payload)  # fell back deterministically
    assert metadata.validation_outcome == "blocked"
    assert metadata.fallback_reason == "provider_http_403"
    assert "super-secret-key" not in (metadata.fallback_reason or "")
    assert metadata.retry_count == 0  # 403 is non-transient, never retried


def test_atlas_gemini_adapter_retries_transient_then_reports_unavailable() -> None:
    """AR-3.5: a transient 5xx is retried up to max_retries, then surfaces as a
    provider outage (not a plain 'blocked' result) with retry_count populated."""
    import httpx
    from pydantic import BaseModel
    from ruhu.atlas_model_gateway import GeminiAtlasModelAdapter

    class _Payload(BaseModel):
        decision: str

    calls = {"n": 0}

    def flaky_post(**kwargs):
        calls["n"] += 1
        request = httpx.Request("POST", kwargs["url"])
        response = httpx.Response(503, request=request, json={"error": "unavailable"})
        raise httpx.HTTPStatusError("503", request=request, response=response)

    adapter = GeminiAtlasModelAdapter(
        provider="gemini",
        model="gemini-test",
        configured=True,
        fallback_reason="not_configured",
        api_key="k",
        http_post=flaky_post,
        max_retries=2,
    )
    adapter._sleep = lambda _seconds: None  # don't actually back off in the test

    payload, metadata = adapter.generate_structured(
        role="report_writer",
        schema_name="Payload",
        prompt="x",
        response_model=_Payload,
        trace_context={},
        timeout_seconds=1.0,
        temperature_policy="deterministic",
    )
    assert isinstance(payload, _Payload)
    assert calls["n"] == 3  # initial try + 2 retries
    assert metadata.retry_count == 2
    assert metadata.validation_outcome == "blocked"
    assert (metadata.fallback_reason or "").startswith("provider_unavailable:")


def test_atlas_gemini_adapter_retries_then_succeeds() -> None:
    """AR-3.5: a transient failure that clears on retry yields a valid result."""
    import httpx
    from pydantic import BaseModel
    from ruhu.atlas_model_gateway import GeminiAtlasModelAdapter

    class _Payload(BaseModel):
        decision: str

    calls = {"n": 0}

    def recovering_post(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            request = httpx.Request("POST", kwargs["url"])
            raise httpx.HTTPStatusError(
                "503", request=request, response=httpx.Response(503, request=request)
            )
        return {
            "candidates": [{"content": {"parts": [{"text": "{\"decision\":\"publish\"}"}]}}],
            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
        }

    adapter = GeminiAtlasModelAdapter(
        provider="gemini",
        model="gemini-test",
        configured=True,
        fallback_reason="not_configured",
        api_key="k",
        http_post=recovering_post,
        max_retries=2,
    )
    adapter._sleep = lambda _seconds: None

    payload, metadata = adapter.generate_structured(
        role="report_writer",
        schema_name="Payload",
        prompt="x",
        response_model=_Payload,
        trace_context={},
        timeout_seconds=1.0,
        temperature_policy="deterministic",
    )
    assert payload.decision == "publish"
    assert metadata.validation_outcome == "valid"
    assert metadata.retry_count == 1


def test_atlas_readiness_mcp_adapter_requires_context_and_blocks_ungranted_side_effects(
    postgres_database_url_factory,
) -> None:
    from ruhu.atlas_readiness_mcp import AtlasReadinessMCPAdapter, AtlasReadinessMCPContext

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="mcp_readiness_demo",
        agent_name="MCP Readiness Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
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
    )
    adapter = AtlasReadinessMCPAdapter(
        service=service,
        agent_registry=registry,
        context=AtlasReadinessMCPContext(tenant_id="org-a", user_id="author"),
    )

    document_payload = adapter.call_tool(
        "ruhu_atlas_readiness",
        "get_agent_document",
        {"agent_id": "mcp_readiness_demo", "version_target": "draft"},
    )
    assert document_payload["agent_document"]["start_scenario_id"] == "support"

    generated = adapter.call_tool(
        "ruhu_atlas_readiness",
        "generate_evaluation_cases",
        {"agent_id": "mcp_readiness_demo", "count": 1, "seed": 1},
    )
    assert len(generated["cases"]) == 1

    blocked = adapter.call_tool(
        "ruhu_atlas_readiness",
        "propose_agent_document_deltas",
        {"agent_id": "mcp_readiness_demo", "case_limit": 1},
    )
    assert blocked == {"status": "blocked", "error": "permission_grant_required"}

    _profile = {
        "profile_id": "p1",
        "locale": "en-US",
        "channel": "voice",
        "language_style": "plain",
        "emotional_state": "neutral",
        "goal": "test",
    }

    # AR-2.6: live (non-deterministic) voice without a grant is blocked before
    # any case parsing or provider call.
    voice_blocked = adapter.call_tool(
        "ruhu_atlas_readiness",
        "run_voice_simulation",
        {
            "agent_id": "mcp_readiness_demo",
            "provider_policy": "google_only",
            "case": {
                "case_id": "c1",
                "test_profile": _profile,
                "scenario_summary": "s",
                "utterances": ["hello"],
            },
        },
    )
    assert voice_blocked == {"status": "blocked", "error": "permission_grant_required_for_live_voice"}

    # AR-2.6: an oversized utterance set is rejected at the MCP boundary.
    with pytest.raises(Exception, match="at most|50"):
        adapter.call_tool(
            "ruhu_atlas_readiness",
            "run_simulation",
            {
                "agent_id": "mcp_readiness_demo",
                "case": {
                    "case_id": "c_big",
                    "test_profile": {**_profile, "channel": "chat"},
                    "scenario_summary": "s",
                    "utterances": ["x"] * 999,
                },
            },
        )

    tenant_b_adapter = AtlasReadinessMCPAdapter(
        service=service,
        agent_registry=registry,
        context=AtlasReadinessMCPContext(tenant_id="org-b", user_id="author"),
    )
    with pytest.raises(KeyError):
        tenant_b_adapter.call_tool(
            "ruhu_atlas_readiness",
            "get_agent_document",
            {"agent_id": "mcp_readiness_demo", "version_target": "draft"},
        )


def test_atlas_adk_runner_uses_mcp_boundary_and_enforces_limits(
    postgres_database_url_factory,
) -> None:
    from ruhu.atlas_adk_runner import AtlasADKReadinessRunner, AtlasADKRunnerLimits, AtlasADKToolCall
    from ruhu.atlas_readiness_mcp import AtlasReadinessMCPAdapter, AtlasReadinessMCPContext

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    atlas_store = SQLAlchemyAtlasStore(session_factory)
    readiness_store = SQLAlchemyAtlasReadinessStore(session_factory)
    registry.create_agent_document(
        agent_id="adk_readiness_demo",
        agent_name="ADK Readiness Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )
    service = AtlasReadinessService(agent_registry=registry, atlas_store=atlas_store, readiness_store=readiness_store)
    adapter = AtlasReadinessMCPAdapter(
        service=service,
        agent_registry=registry,
        context=AtlasReadinessMCPContext(tenant_id="org-a", user_id="author"),
    )
    runner = AtlasADKReadinessRunner(mcp_adapter=adapter, limits=AtlasADKRunnerLimits(max_tool_calls=2))

    runaway = runner.run_tool_plan(
        [
            AtlasADKToolCall(tool_name="get_agent_document", arguments={"agent_id": "adk_readiness_demo"}),
            AtlasADKToolCall(tool_name="get_agent_document", arguments={"agent_id": "adk_readiness_demo"}),
            AtlasADKToolCall(tool_name="get_agent_document", arguments={"agent_id": "adk_readiness_demo"}),
        ]
    )
    assert runaway.status == "failed"
    assert runaway.blocker == "runaway_orchestrator"

    blocked = runner.run_tool_plan(
        [
            AtlasADKToolCall(
                tool_name="propose_agent_document_deltas",
                arguments={"agent_id": "adk_readiness_demo", "case_limit": 1},
            )
        ]
    )
    assert blocked.status == "blocked"
    assert blocked.blocker == "permission_grant_required"


def test_atlas_adk_runner_executes_through_external_mcp_server(
    postgres_database_url_factory,
) -> None:
    import os
    import sys
    from pathlib import Path

    from ruhu.atlas_adk_runner import AtlasADKReadinessRunner, AtlasADKRunnerLimits, AtlasADKToolCall
    from ruhu.tools.mcp_client import MCPServerConfig, mcp_manager

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )
    session_factory = build_session_factory(database_url)
    registry = SQLAlchemyAgentRegistry(session_factory)
    registry.create_agent_document(
        agent_id="external_mcp_adk_demo",
        agent_name="External MCP ADK Demo",
        organization_id="org-a",
        document=_handoff_demo_document(),
    )

    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(Path.cwd() / "src"),
            "RUHU_DATABASE_URL": database_url,
            "RUHU_ATLAS_MCP_TENANT_ID": "org-a",
            "RUHU_ATLAS_MCP_USER_ID": "author",
        }
    )
    config = MCPServerConfig(
        name="ruhu_atlas_readiness",
        transport="stdio",
        command=sys.executable,
        args=["-m", "ruhu.atlas_mcp_server"],
        cwd=str(Path.cwd()),
        env=env,
    )
    try:
        listed = mcp_manager.list_tools(config)
        assert {tool["name"] for tool in listed} >= {
            "get_agent_document",
            "generate_evaluation_cases",
            "propose_agent_document_deltas",
            "create_publish_report",
        }

        runner = AtlasADKReadinessRunner(
            mcp_server_config=config,
            limits=AtlasADKRunnerLimits(max_tool_calls=3),
        )
        result = runner.run_tool_plan(
            [
                AtlasADKToolCall(
                    tool_name="get_agent_document",
                    arguments={"agent_id": "external_mcp_adk_demo", "version_target": "draft"},
                ),
                AtlasADKToolCall(
                    tool_name="propose_agent_document_deltas",
                    arguments={"agent_id": "external_mcp_adk_demo", "case_limit": 1},
                ),
            ]
        )
    finally:
        mcp_manager.close_all()

    assert result.status == "blocked"
    assert result.blocker == "permission_grant_required"
    assert result.tool_results[0]["result"]["agent_document"]["start_scenario_id"] == "support"


@pytest.mark.asyncio
async def test_google_adk_discovers_atlas_tools_through_mcp_server(
    postgres_database_url_factory,
) -> None:
    import asyncio
    import os
    import sys
    from pathlib import Path

    from ruhu.atlas_google_adk import AtlasGoogleADKUnavailable, build_atlas_google_adk_agent
    from ruhu.tools.mcp_client import MCPServerConfig

    database_url = postgres_database_url_factory()
    build_default_app(
        agent_root="tests/_fixtures/data/agents",
        database_url=database_url,
        bootstrap_organization_id="public",
    )

    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(Path.cwd() / "src"),
            "RUHU_DATABASE_URL": database_url,
            "RUHU_ATLAS_MCP_TENANT_ID": "org-a",
            "RUHU_ATLAS_MCP_USER_ID": "author",
        }
    )
    config = MCPServerConfig(
        name="ruhu_atlas_readiness",
        transport="stdio",
        command=sys.executable,
        args=["-m", "ruhu.atlas_mcp_server"],
        cwd=str(Path.cwd()),
        env=env,
        timeout=10.0,
    )

    try:
        bundle = build_atlas_google_adk_agent(mcp_server_config=config)
    except AtlasGoogleADKUnavailable as exc:
        pytest.skip(str(exc))

    try:
        tools = await asyncio.wait_for(bundle.toolset.get_tools(), timeout=10.0)
    finally:
        await bundle.toolset.close()

    tool_names = {tool.name for tool in tools}
    assert "get_agent_document" in tool_names
    assert "run_simulation" in tool_names
    assert "propose_agent_document_deltas" in tool_names
