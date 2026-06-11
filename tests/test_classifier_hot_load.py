"""Tests for src/ruhu/classifier/hot_load.py and hot_load_api.py — WI-6.6."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from ruhu.classifier.hot_load import (
    HotLoadResult,
    HotUnloadResult,
    VLLMHotLoadClient,
)
from ruhu.classifier.hot_load_api import install_hot_load_router
from ruhu.classifier.registry import register_candidate
from ruhu.db_models import AgentRecord, Base, ClassifierLoraRecord


# ── client (load) ─────────────────────────────────────────────────────────


def test_client_load_sends_lora_name_and_path() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return {"status": "ok"}

    client = VLLMHotLoadClient(base_url="http://vllm:8000", http_post=fake_post)
    result = client.load(lora_name="agent-a-v1", model_uri="gs://bucket/a-v1.safetensors")

    assert result.outcome == "loaded"
    assert result.lora_name == "agent-a-v1"
    assert captured["url"] == "http://vllm:8000/v1/load_lora_adapter"
    assert captured["json"] == {
        "lora_name": "agent-a-v1",
        "lora_path": "gs://bucket/a-v1.safetensors",
    }


def test_client_load_returns_already_loaded_when_status_says_so() -> None:
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: {"status": "already_loaded"},
    )
    result = client.load(lora_name="x", model_uri="gs://x")
    assert result.outcome == "already_loaded"


def test_client_load_recognises_already_loaded_via_message() -> None:
    """Some vLLM versions report via message rather than status."""
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: {"message": "lora already loaded"},
    )
    result = client.load(lora_name="x", model_uri="gs://x")
    assert result.outcome == "already_loaded"


def test_client_load_treats_empty_response_as_loaded() -> None:
    """200 + empty body — assume vLLM accepted the load (older versions do this)."""
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: {},
    )
    result = client.load(lora_name="x", model_uri="gs://x")
    assert result.outcome == "loaded"


def test_client_load_classifies_connection_errors() -> None:
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: (_ for _ in ()).throw(ConnectionError("vllm down")),
    )
    result = client.load(lora_name="x", model_uri="gs://x")
    assert result.outcome == "error"
    assert result.detail == "connection_error"


def test_client_load_classifies_timeouts() -> None:
    class _MockTimeout(Exception):
        pass

    _MockTimeout.__name__ = "TimeoutException"

    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: (_ for _ in ()).throw(_MockTimeout("deadline exceeded")),
    )
    result = client.load(lora_name="x", model_uri="gs://x")
    assert result.outcome == "error"
    assert result.detail == "timeout"


def test_client_load_records_elapsed_ms() -> None:
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: {"status": "ok"},
    )
    result = client.load(lora_name="x", model_uri="gs://x")
    assert result.elapsed_ms >= 0


def test_client_load_includes_bearer_token_when_loader_provided() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["headers"] = headers
        return {"status": "ok"}

    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=fake_post,
        access_token_loader=lambda: "admin-token",
    )
    client.load(lora_name="x", model_uri="gs://x")
    assert captured["headers"]["Authorization"] == "Bearer admin-token"


def test_client_load_omits_bearer_token_when_loader_returns_none() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["headers"] = headers
        return {"status": "ok"}

    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=fake_post,
        access_token_loader=lambda: None,
    )
    client.load(lora_name="x", model_uri="gs://x")
    assert "Authorization" not in captured["headers"]


# ── client (unload) ──────────────────────────────────────────────────────


def test_client_unload_sends_lora_name() -> None:
    captured: dict[str, Any] = {}

    def fake_post(*, url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        return {"status": "ok"}

    client = VLLMHotLoadClient(base_url="http://vllm:8000", http_post=fake_post)
    result = client.unload(lora_name="agent-a-v1")

    assert result.outcome == "unloaded"
    assert captured["url"] == "http://vllm:8000/v1/unload_lora_adapter"
    assert captured["json"] == {"lora_name": "agent-a-v1"}


def test_client_unload_recognises_not_loaded_response() -> None:
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: {"message": "lora is not loaded"},
    )
    result = client.unload(lora_name="x")
    assert result.outcome == "not_loaded"


def test_client_unload_classifies_errors() -> None:
    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: (_ for _ in ()).throw(ConnectionError("down")),
    )
    result = client.unload(lora_name="x")
    assert result.outcome == "error"


# ── API endpoints ─────────────────────────────────────────────────────────


def _make_app() -> tuple[FastAPI, Session, list[dict[str, Any]]]:
    """Build a FastAPI app + session + a captured-call list for the fake vLLM."""
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

    captured: list[dict[str, Any]] = []

    def fake_post(*, url, json, headers, timeout):
        captured.append({"url": url, "json": json})
        if "/load_lora_adapter" in url:
            return {"status": "ok"}
        return {"status": "ok"}

    client = VLLMHotLoadClient(base_url="http://vllm:8000", http_post=fake_post)
    app = FastAPI()
    install_hot_load_router(
        app,
        hot_load_client=client,
        session_factory=lambda: Session(engine),
    )
    return app, test_session, captured


def _seed_agent_and_lora(session: Session) -> str:
    now = datetime.now(timezone.utc)
    session.add(
        AgentRecord(
            agent_id="agent_a",
            name="agent_a",
            settings_json={},
            created_at=now,
            updated_at=now,
        )
    )
    session.flush()
    record = register_candidate(
        session,
        agent_id="agent_a",
        lora_name="agent-a-v1",
        model_uri="gs://bucket/a-v1.safetensors",
        version="v1",
    )
    session.commit()
    return record.lora_id


def test_post_hot_load_calls_vllm_with_registry_lora_name_and_uri() -> None:
    app, session, captured = _make_app()
    lora_id = _seed_agent_and_lora(session)

    client = TestClient(app)
    response = client.post(f"/classifier/loras/{lora_id}/hot-load")
    assert response.status_code == 200
    body = response.json()
    assert body["lora_name"] == "agent-a-v1"
    assert body["outcome"] == "loaded"

    assert len(captured) == 1
    assert captured[0]["json"] == {
        "lora_name": "agent-a-v1",
        "lora_path": "gs://bucket/a-v1.safetensors",
    }


def test_post_hot_load_returns_404_for_unknown_lora_id() -> None:
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.post("/classifier/loras/does-not-exist/hot-load")
    assert response.status_code == 404


def test_post_hot_unload_calls_vllm_with_lora_name() -> None:
    app, session, captured = _make_app()
    lora_id = _seed_agent_and_lora(session)

    client = TestClient(app)
    response = client.post(f"/classifier/loras/{lora_id}/hot-unload")
    assert response.status_code == 200
    body = response.json()
    assert body["lora_name"] == "agent-a-v1"
    assert body["outcome"] == "unloaded"

    assert len(captured) == 1
    assert captured[0]["url"].endswith("/v1/unload_lora_adapter")
    assert captured[0]["json"] == {"lora_name": "agent-a-v1"}


def test_post_hot_unload_returns_404_for_unknown_lora_id() -> None:
    app, _, _ = _make_app()
    client = TestClient(app)
    response = client.post("/classifier/loras/missing/hot-unload")
    assert response.status_code == 404


def test_post_hot_load_returns_already_loaded_when_vllm_says_so() -> None:
    """If vLLM already has the LoRA, the API surfaces that explicitly."""
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

    client = VLLMHotLoadClient(
        base_url="http://vllm:8000",
        http_post=lambda **_: {"status": "already_loaded"},
    )
    app = FastAPI()
    install_hot_load_router(
        app, hot_load_client=client, session_factory=lambda: Session(engine)
    )
    lora_id = _seed_agent_and_lora(test_session)

    test_client = TestClient(app)
    response = test_client.post(f"/classifier/loras/{lora_id}/hot-load")
    assert response.status_code == 200
    assert response.json()["outcome"] == "already_loaded"
