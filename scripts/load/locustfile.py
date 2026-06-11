"""Turn-path load profile (RP-5.5).

Exercises the hot path — start a conversation, send turns — against a
deployed environment. Run against staging, never production.

Usage:
    pip install locust
    RUHU_LOAD_BASE_URL=https://staging.example.com \
    RUHU_LOAD_BEARER_TOKEN=<access token> \
    RUHU_LOAD_AGENT_ID=<agent id> \
    locust -f scripts/load/locustfile.py --headless \
        -u 50 -r 5 --run-time 5m --host "$RUHU_LOAD_BASE_URL"

SLO reference (docs/remediation-program/plan.md RP-5.5): turn p99 — set
the threshold that matters for your deployment; the kernel itself is
deterministic, so turn latency is dominated by classifier + dialogue LLM
calls and DB writes.
"""
from __future__ import annotations

import os
import uuid

from locust import HttpUser, between, task


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set (see module docstring)")
    return value


class TurnPathUser(HttpUser):
    """One simulated end user holding one conversation."""

    wait_time = between(1.0, 4.0)

    def on_start(self) -> None:
        self.client.headers["Authorization"] = f"Bearer {_require('RUHU_LOAD_BEARER_TOKEN')}"
        self.agent_id = _require("RUHU_LOAD_AGENT_ID")
        response = self.client.post(
            "/conversations",
            json={"agent_id": self.agent_id},
            name="POST /conversations",
        )
        response.raise_for_status()
        self.conversation_id = response.json()["conversation"]["conversation_id"]
        self.turn_counter = 0

    @task(10)
    def send_turn(self) -> None:
        self.turn_counter += 1
        key = f"load-{self.conversation_id}-{self.turn_counter}-{uuid.uuid4().hex[:6]}"
        self.client.post(
            f"/conversations/{self.conversation_id}/turns",
            json={
                "turn_id": key,
                "dedupe_key": key,
                "channel": "web_chat",
                "modality": "text",
                "event_type": "user_message",
                "text": "Tell me about your pricing options.",
            },
            name="POST /conversations/{id}/turns",
        )

    @task(1)
    def read_traces(self) -> None:
        self.client.get(
            f"/conversations/{self.conversation_id}/traces",
            name="GET /conversations/{id}/traces",
        )
