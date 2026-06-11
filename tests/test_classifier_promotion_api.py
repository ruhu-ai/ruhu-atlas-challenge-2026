"""Tests for src/ruhu/classifier/promotion_api.py — WI-6.9 router."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ruhu.classifier.promotion_api import install_promotion_router
from ruhu.classifier.registry import promote_to_production, register_candidate
from ruhu.db_models import AgentRecord, Base, ClassifierLoraRecord


def _make_app() -> tuple[FastAPI, Session]:
    """Build a FastAPI app + a SQLite session backing the registry.

    The TestClient runs requests in a worker thread so the engine needs
    ``check_same_thread=False`` and a single-connection ``StaticPool`` —
    otherwise SQLite raises 'SQLite objects created in a thread can only
    be used in that same thread'.

    The factory returns a *new* Session per call so the router can
    close its own session in its finally-block without invalidating
    the verification session the test holds. ``StaticPool`` ensures
    every Session shares the same underlying connection / in-memory
    DB.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[AgentRecord.__table__, ClassifierLoraRecord.__table__],
    )
    test_session = Session(engine)
    app = FastAPI()
    install_promotion_router(app, session_factory=lambda: Session(engine))
    return app, test_session


def _seed_agent(session: Session, agent_id: str = "agent_a") -> None:
    now = datetime.now(timezone.utc)
    session.add(
        AgentRecord(
            agent_id=agent_id,
            name=agent_id,
            settings_json={},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()


def _candidate(session: Session, lora_name: str = "agent-a-v1") -> str:
    record = register_candidate(
        session,
        agent_id="agent_a",
        lora_name=lora_name,
        model_uri=f"gs://bucket/{lora_name}.safetensors",
        version="v1",
    )
    session.commit()
    return record.lora_id


def _payload_pass_steady_state() -> dict:
    return {
        "eval_report": {
            "macro_f1": 0.90,
            "unknown_rate": 0.05,
            "per_intent": [],
            "latency": {"p50_ms": 80.0, "p99_ms": 200.0},
            "expected_calibration_error": 0.05,
        },
        "baseline": {
            "macro_f1": 0.85,
            "unknown_rate": 0.06,
            "per_intent": [],
            "latency": {"p50_ms": 75.0, "p99_ms": 180.0},
        },
    }


def _payload_pass_cold_start() -> dict:
    return {
        "eval_report": {
            "macro_f1": 0.85,
            "unknown_rate": 0.05,
            "per_intent": [],
            "latency": {"p50_ms": 80.0, "p99_ms": 300.0},
            "expected_calibration_error": 0.05,
        },
        "base_model_macro_f1": 0.78,
    }


# ── happy paths ───────────────────────────────────────────────────────────


def test_post_evaluate_promotes_candidate_when_gates_pass() -> None:
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)

    client = TestClient(app)
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate",
        json=_payload_pass_steady_state(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["promote"] is True
    assert body["promoted"] is True
    assert body["status"] == "production"
    assert body["regime"] == "steady_state"
    assert all(check["outcome"] == "pass" for check in body["checks"])

    # Registry was actually flipped
    record = session.get(ClassifierLoraRecord, lora_id)
    assert record is not None
    assert record.status == "production"
    assert record.published_at is not None


def test_post_evaluate_rejects_when_macro_f1_lift_too_low() -> None:
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)

    client = TestClient(app)
    payload = _payload_pass_steady_state()
    payload["eval_report"]["macro_f1"] = 0.86  # only 1pp lift over 0.85 baseline
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate", json=payload
    )
    assert response.status_code == 200
    body = response.json()
    assert body["promote"] is False
    assert body["promoted"] is False
    assert body["status"] == "candidate"

    record = session.get(ClassifierLoraRecord, lora_id)
    assert record is not None
    assert record.status == "candidate"


def test_post_evaluate_cold_start_promotes_when_lift_over_base_meets_five_pp() -> None:
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)

    client = TestClient(app)
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate",
        json=_payload_pass_cold_start(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["regime"] == "cold_start"
    assert body["promote"] is True
    assert body["status"] == "production"


# ── edge cases ────────────────────────────────────────────────────────────


def test_post_evaluate_returns_404_for_unknown_lora_id() -> None:
    app, _ = _make_app()
    client = TestClient(app)
    response = client.post(
        "/classifier/loras/does-not-exist/evaluate",
        json=_payload_pass_steady_state(),
    )
    assert response.status_code == 404


def test_post_evaluate_returns_400_when_neither_baseline_nor_base_model_provided() -> None:
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)

    client = TestClient(app)
    payload = {
        "eval_report": {
            "macro_f1": 0.9,
            "unknown_rate": 0.05,
            "per_intent": [],
            "latency": {"p50_ms": 80.0, "p99_ms": 200.0},
            "expected_calibration_error": 0.05,
        },
        # neither baseline nor base_model_macro_f1
    }
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate", json=payload
    )
    assert response.status_code == 400


def test_post_evaluate_already_production_does_not_re_promote() -> None:
    """Re-running the gate against an already-production LoRA returns the same
    decision but doesn't flip status (it stays production, doesn't go through
    promote_to_production again)."""
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)
    promote_to_production(session, lora_id=lora_id)
    session.commit()

    client = TestClient(app)
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate",
        json=_payload_pass_steady_state(),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["promote"] is True
    assert body["promoted"] is False  # already production, no re-flip
    assert body["status"] == "production"


def test_post_evaluate_rejects_invalid_eval_report_payload() -> None:
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)

    client = TestClient(app)
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate",
        json={
            "eval_report": {
                "macro_f1": 1.5,  # out of range — Pydantic validation fails
                "unknown_rate": 0.05,
                "per_intent": [],
                "latency": {"p50_ms": 80.0, "p99_ms": 200.0},
                "expected_calibration_error": 0.05,
            },
            "base_model_macro_f1": 0.5,
        },
    )
    assert response.status_code == 422


def test_post_evaluate_returns_per_check_outcomes_in_response_body() -> None:
    app, session = _make_app()
    _seed_agent(session)
    lora_id = _candidate(session)

    client = TestClient(app)
    response = client.post(
        f"/classifier/loras/{lora_id}/evaluate",
        json=_payload_pass_steady_state(),
    )
    assert response.status_code == 200
    body = response.json()
    check_names = {check["name"] for check in body["checks"]}
    assert "macro_f1_lift" in check_names
    assert "calibration_ece" in check_names
    assert all("detail" in check and check["detail"] for check in body["checks"])
