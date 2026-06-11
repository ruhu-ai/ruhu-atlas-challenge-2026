from __future__ import annotations

import os
import subprocess
import json
import socket
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import httpx
import uvicorn

from ruhu.api import build_default_app
from ruhu.livekit_worker import main
from ruhu.runtime_config import RuntimeSettings
from tests.conftest import make_widget_publishable_key


TEST_HS256_SECRET = "0123456789abcdef0123456789abcdef"


def _unused_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass
class _LocalWorkerHarness:
    base_url: str
    provider_secret: str
    publishable_key: str


@contextmanager
def _run_local_worker_control_plane(
    *,
    runtime_database_url: str,
    auth_database_url: str,
) -> Iterator[_LocalWorkerHarness]:
    agent_root_path = Path(__file__).resolve().parent / "_fixtures" / "data" / "agents"
    provider_secret = "widget-provider-secret"
    app = build_default_app(
        agent_root=agent_root_path,
        database_url=runtime_database_url,
        auth_database_url=auth_database_url,
        auth_jwt_secret=TEST_HS256_SECRET,
        interpreter_name="sales",
        bootstrap_organization_id="test-org",
        runtime_settings=RuntimeSettings(
            database_url=runtime_database_url,
            auth_database_url=auth_database_url,
            auth_jwt_secret=TEST_HS256_SECRET,
            provider_shared_secret=provider_secret,
            livekit_server_url="ws://127.0.0.1:7880",
            livekit_api_key="devkey",
            livekit_api_secret=TEST_HS256_SECRET,
            livekit_agent_name="ruhu-voice",
            livekit_room_prefix="widget",
            livekit_dispatch_strategy="room_config",
        ),
    )
    pk = make_widget_publishable_key(
        runtime_database_url,
        agent_id="sales",
        organization_id="test-org",
    )

    port = _unused_tcp_port()
    base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="error",
            ws="none",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base_url}/health", timeout=0.25)
            if response.status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        raise RuntimeError("timed out waiting for local LiveKit worker test server to start")

    try:
        yield _LocalWorkerHarness(base_url=base_url, provider_secret=provider_secret, publishable_key=pk)
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def _start_widget_voice(base_url: str, publishable_key: str) -> tuple[str, str]:
    with httpx.Client(base_url=base_url, follow_redirects=False) as client:
        session_response = client.post(
            "/public/widget/sessions",
            json={"agent_id": "sales", "publishable_key": publishable_key},
        )
        assert session_response.status_code == 200
        session_payload = session_response.json()
        conversation_id = str(session_payload["conversation_id"])
        session_token = str(session_payload["session_token"])

        voice_response = client.post(
            f"/public/widget/sessions/{conversation_id}/voice",
            headers={"X-Ruhu-Widget-Session-Token": session_token},
            json={"participant_name": "CLI Test"},
        )
        assert voice_response.status_code == 200
        voice_payload = voice_response.json()
        return conversation_id, str(voice_payload["realtime_session_id"])


def _disconnect_voice(*, base_url: str, provider_secret: str, realtime_session_id: str) -> None:
    with httpx.Client(base_url=base_url, follow_redirects=False) as client:
        response = client.post(
            f"/providers/livekit/voice/sessions/{realtime_session_id}/disconnect",
            headers={"X-Ruhu-Provider-Secret": provider_secret},
            json={"reason": "test_disconnect"},
        )
    assert response.status_code == 200


def _configure_worker_env(monkeypatch) -> None:
    monkeypatch.setenv("RUHU_LIVEKIT_SERVER_URL", "ws://127.0.0.1:7880")
    monkeypatch.setenv("RUHU_LIVEKIT_API_KEY", "devkey")
    monkeypatch.setenv("RUHU_LIVEKIT_API_SECRET", TEST_HS256_SECRET)
    monkeypatch.setenv("RUHU_LIVEKIT_AGENT_NAME", "ruhu-voice")
    monkeypatch.delenv("RUHU_LIVEKIT_CONTROL_PLANE_BASE_URL", raising=False)
    monkeypatch.delenv("RUHU_PROVIDER_SHARED_SECRET", raising=False)


def _build_worker_env(base_url: str, provider_secret: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    env["RUHU_LIVEKIT_SERVER_URL"] = "ws://127.0.0.1:7880"
    env["RUHU_LIVEKIT_API_KEY"] = "devkey"
    env["RUHU_LIVEKIT_API_SECRET"] = TEST_HS256_SECRET
    env["LIVEKIT_URL"] = "ws://127.0.0.1:7880"
    env["LIVEKIT_API_KEY"] = "devkey"
    env["LIVEKIT_API_SECRET"] = TEST_HS256_SECRET
    env["RUHU_LIVEKIT_AGENT_NAME"] = "ruhu-voice"
    env["RUHU_LIVEKIT_ROOM_PREFIX"] = "widget"
    env["RUHU_LIVEKIT_DISPATCH_STRATEGY"] = "room_config"
    env["RUHU_LIVEKIT_CONTROL_PLANE_BASE_URL"] = base_url
    env["RUHU_PROVIDER_SHARED_SECRET"] = provider_secret
    return env


def test_livekit_worker_bridge_final_transcript_cli_round_trips_to_local_control_plane(
    postgres_database_url_factory,
    monkeypatch,
    capsys,
) -> None:
    runtime_database_url = postgres_database_url_factory()
    auth_database_url = runtime_database_url
    with _run_local_worker_control_plane(
        runtime_database_url=runtime_database_url,
        auth_database_url=auth_database_url,
    ) as harness:
        conversation_id, realtime_session_id = _start_widget_voice(harness.base_url, harness.publishable_key)
        _configure_worker_env(monkeypatch)

        exit_code = main(
            [
                "bridge-final-transcript",
                "--control-plane-base-url",
                harness.base_url,
                "--provider-secret",
                harness.provider_secret,
                "--realtime-session-id",
                realtime_session_id,
                "--idempotency-key",
                "cli-seg-1",
                "--text",
                "Tell me about pricing.",
                "--participant-identity",
                "visitor-1",
                "--provider-session-id",
                f"room-{realtime_session_id}",
                "--json",
            ]
        )
        assert exit_code == 0
        first_payload = json.loads(capsys.readouterr().out)
        assert first_payload["conversation_id"] == conversation_id
        assert first_payload["trace_id"]

        exit_code = main(
            [
                "bridge-final-transcript",
                "--control-plane-base-url",
                harness.base_url,
                "--provider-secret",
                harness.provider_secret,
                "--realtime-session-id",
                realtime_session_id,
                "--idempotency-key",
                "cli-seg-1",
                "--text",
                "Tell me about pricing.",
                "--participant-identity",
                "visitor-1",
                "--provider-session-id",
                f"room-{realtime_session_id}",
                "--json",
            ]
        )
        assert exit_code == 0
        repeated_payload = json.loads(capsys.readouterr().out)
        assert repeated_payload["trace_id"] == first_payload["trace_id"]


def test_livekit_worker_bridge_final_transcript_cli_reports_stale_session_conflict_cleanly(
    postgres_database_url_factory,
    monkeypatch,
    capsys,
) -> None:
    runtime_database_url = postgres_database_url_factory()
    auth_database_url = runtime_database_url
    with _run_local_worker_control_plane(
        runtime_database_url=runtime_database_url,
        auth_database_url=auth_database_url,
    ) as harness:
        _, realtime_session_id = _start_widget_voice(harness.base_url, harness.publishable_key)
        _disconnect_voice(
            base_url=harness.base_url,
            provider_secret=harness.provider_secret,
            realtime_session_id=realtime_session_id,
        )
        _configure_worker_env(monkeypatch)

        exit_code = main(
            [
                "bridge-final-transcript",
                "--control-plane-base-url",
                harness.base_url,
                "--provider-secret",
                harness.provider_secret,
                "--realtime-session-id",
                realtime_session_id,
                "--idempotency-key",
                "cli-seg-stale",
                "--text",
                "Can you still hear me?",
                "--json",
            ]
        )
        assert exit_code == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["status_code"] == 409
        assert "HTTP 409" in payload["reason"]
        assert "/providers/livekit/voice/sessions/" in payload["request_url"]
        assert payload["response_text"]


def test_livekit_worker_serve_process_starts_against_local_control_plane(
    postgres_database_url_factory,
) -> None:
    runtime_database_url = postgres_database_url_factory()
    auth_database_url = runtime_database_url
    with _run_local_worker_control_plane(
        runtime_database_url=runtime_database_url,
        auth_database_url=auth_database_url,
    ) as harness:
        worker_port = _unused_tcp_port()
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ruhu.livekit_worker",
                "serve",
                "--control-plane-base-url",
                harness.base_url,
                "--provider-secret",
                harness.provider_secret,
                "--host",
                "127.0.0.1",
                "--port",
                str(worker_port),
                "--log-level",
                "error",
                "--runtime-mode",
                "worker_options",
            ],
            env=_build_worker_env(
                base_url=harness.base_url,
                provider_secret=harness.provider_secret,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            deadline = time.time() + 8
            while time.time() < deadline:
                if proc.poll() is not None:
                    _, proc_stderr = proc.communicate()
                    raise AssertionError(
                        f"worker exited before ready: code {proc.returncode}\n{proc_stderr or ''}"
                    )
                time.sleep(0.2)
            assert proc.poll() is None
            proc.terminate()
            try:
                _, proc_stderr = proc.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                _, proc_stderr = proc.communicate(timeout=8)
            assert proc.returncode is not None
            if proc.returncode not in {0, -15}:
                raise AssertionError(f"worker did not shut down cleanly: {proc.returncode}\n{proc_stderr or ''}")
            assert "error initializing process" not in (proc_stderr or "")
            assert "cannot pickle" not in (proc_stderr or "").lower()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=8)
