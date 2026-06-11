"""Tests for src/ruhu/classifier/training_api.py — WI-6.7 router."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ruhu.classifier.training_api import install_training_router


def _client() -> TestClient:
    app = FastAPI()
    install_training_router(app)
    return TestClient(app)


def _payload(**overrides) -> dict:
    base = {"agent_id": "agent_a"}
    base.update(overrides)
    return {"inputs": base}


# ── manual endpoint ───────────────────────────────────────────────────────


def test_post_train_manual_returns_should_train_true_without_cooldown() -> None:
    client = _client()
    response = client.post(
        "/classifier/agents/agent_a/train",
        json={"inputs": {"agent_id": "agent_a"}, "override_cooldown": False},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "agent_a"
    assert body["should_train"] is True
    assert body["cooldown_active"] is False
    assert any(t["kind"] == "manual" and t["fired"] for t in body["triggers"])


def test_post_train_manual_blocked_by_cooldown() -> None:
    client = _client()
    last = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    response = client.post(
        "/classifier/agents/agent_a/train",
        json={
            "inputs": {"agent_id": "agent_a", "last_trained_at": last},
            "override_cooldown": False,
        },
    )
    body = response.json()
    assert body["cooldown_active"] is True
    assert body["should_train"] is False


def test_post_train_manual_override_cooldown_forces_run() -> None:
    client = _client()
    last = (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat()
    response = client.post(
        "/classifier/agents/agent_a/train",
        json={
            "inputs": {"agent_id": "agent_a", "last_trained_at": last},
            "override_cooldown": True,
        },
    )
    body = response.json()
    assert body["cooldown_active"] is True
    assert body["should_train"] is True


def test_post_train_manual_uses_path_agent_id_over_body() -> None:
    """Path is canonical — body's agent_id is ignored if it disagrees."""
    client = _client()
    response = client.post(
        "/classifier/agents/agent_a/train",
        json={"inputs": {"agent_id": "different_in_body"}},
    )
    body = response.json()
    assert body["agent_id"] == "agent_a"


# ── auto-evaluate endpoint ────────────────────────────────────────────────


def test_post_training_status_returns_no_fire_when_no_predicate() -> None:
    client = _client()
    response = client.post(
        "/classifier/agents/agent_a/training-status",
        json={"inputs": {"agent_id": "agent_a"}},
    )
    body = response.json()
    assert body["should_train"] is False
    fired = [t for t in body["triggers"] if t["fired"]]
    assert fired == []


def test_post_training_status_records_volume_trigger() -> None:
    client = _client()
    response = client.post(
        "/classifier/agents/agent_a/training-status",
        json={"inputs": {"agent_id": "agent_a", "traces_since_last_train": 1500}},
    )
    body = response.json()
    assert body["should_train"] is True
    fired_kinds = {t["kind"] for t in body["triggers"] if t["fired"]}
    assert fired_kinds == {"volume"}


def test_post_training_status_records_quality_drift() -> None:
    client = _client()
    response = client.post(
        "/classifier/agents/agent_a/training-status",
        json={
            "inputs": {
                "agent_id": "agent_a",
                "current_macro_f1": 0.80,
                "prod_lora_macro_f1": 0.85,
            }
        },
    )
    body = response.json()
    fired_kinds = {t["kind"] for t in body["triggers"] if t["fired"]}
    assert "quality_drift" in fired_kinds


def test_post_training_status_returns_cooldown_until_iso_string() -> None:
    client = _client()
    last = datetime(2026, 5, 1, 8, 0, 0, tzinfo=timezone.utc)
    response = client.post(
        "/classifier/agents/agent_a/training-status",
        json={
            "inputs": {
                "agent_id": "agent_a",
                "last_trained_at": last.isoformat(),
            }
        },
    )
    body = response.json()
    assert body["cooldown_until"] is not None
    # Cool-down expires 24 hours after last_trained_at.
    assert body["cooldown_until"].startswith("2026-05-02")


def test_post_training_status_validates_invalid_macro_f1_range() -> None:
    client = _client()
    response = client.post(
        "/classifier/agents/agent_a/training-status",
        json={"inputs": {"agent_id": "agent_a", "current_macro_f1": 1.5}},
    )
    assert response.status_code == 422
