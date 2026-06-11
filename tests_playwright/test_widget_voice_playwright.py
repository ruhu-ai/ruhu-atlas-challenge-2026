from __future__ import annotations

from playwright.sync_api import Page, expect


_FAKE_LIVEKIT_MODULE = """
const rooms = [];

class Room {
  constructor() {
    this._events = new Map();
    this.localParticipant = {
      setMicrophoneEnabled: async () => {},
    };
    rooms.push(this);
    window.__ruhuTestLiveKitRooms = rooms;
  }

  on(event, callback) {
    const callbacks = this._events.get(event) || [];
    callbacks.push(callback);
    this._events.set(event, callbacks);
    return callback;
  }

  async connect(url, token) {
    this.url = url;
    this.token = token;
    return undefined;
  }

  disconnect() {
    this.emit("disconnected");
  }

  emit(event, ...args) {
    const callbacks = this._events.get(event) || [];
    for (const callback of callbacks) {
      callback(...args);
    }
  }
}

window.__ruhuTestLiveKitRooms = rooms;
window.__ruhuEmitLiveKitEvent = (event, ...args) => {
  const room = rooms.at(-1);
  if (room) {
    room.emit(event, ...args);
  }
};

export { Room };
"""


def _read_widget_state(page: Page) -> dict[str, object]:
    return page.evaluate(
        """() => {
            const raw = window.localStorage.getItem('ruhu:widget:sales_agent');
            return raw ? JSON.parse(raw) : {};
        }"""
    )


def test_widget_voice_browser_lifecycle_supports_interruption_and_refresh_resume(
    page: Page,
    widget_browser_harness,
) -> None:
    harness = widget_browser_harness
    page.route(
        f"{harness.base_url}/widget-livekit-client.js",
        lambda route: route.fulfill(
            status=200,
            body=_FAKE_LIVEKIT_MODULE,
            content_type="application/javascript",
        ),
    )

    page.goto(f"{harness.base_url}/widget-preview?agent_id=sales_agent")
    page.click(".widget-fab")
    expect(page.locator(".widget-header-title")).to_have_text("Ruhu")
    expect(page.locator(".chat-bubble.assistant")).to_contain_text("learn about Ruhu")

    page.click('[data-action="voice"]')
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Voice connected")

    widget_state = _read_widget_state(page)
    conversation_id = str(widget_state["sessionId"])
    voice_session = dict(widget_state["voiceSession"])
    realtime_session_id = str(voice_session["realtimeSessionId"])
    assert voice_session["status"] == "connected"

    with harness.provider_client() as client:
        assistant_started = client.post(
            f"/providers/livekit/voice/sessions/{realtime_session_id}/signals",
            json={"signal": "assistant_speaking_started", "provider_session_id": "room-rs-widget-1"},
        )
        assert assistant_started.status_code == 200

        barged_in = client.post(
            f"/providers/livekit/voice/sessions/{realtime_session_id}/signals",
            json={"signal": "user_barged_in", "reason": "browser_test_overlap"},
        )
        assert barged_in.status_code == 200
        assert barged_in.json()["recorded_names"] == [
            "user_barged_in",
            "interruption_detected",
            "assistant_interrupted",
        ]

        voice_events = client.get(
            f"/providers/livekit/conversations/{conversation_id}/events",
            params={"family": "voice"},
        )
        assert voice_events.status_code == 200

    voice_event_names = {
        (event["family"], event["name"])
        for event in voice_events.json()
    }
    assert ("voice", "assistant_speaking_started") in voice_event_names
    assert ("voice", "user_barged_in") in voice_event_names
    assert ("voice", "interruption_detected") in voice_event_names
    assert ("voice", "assistant_interrupted") in voice_event_names

    page.evaluate("() => window.__ruhuEmitLiveKitEvent('reconnecting')")
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Reconnecting voice")
    page.evaluate("() => window.__ruhuEmitLiveKitEvent('reconnected')")
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Voice connected")

    page.reload()
    page.click(".widget-fab")
    expect(page.locator(".widget-header-title")).to_have_text("Ruhu")
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Voice connected")

    resumed_state = _read_widget_state(page)
    resumed_voice_session = dict(resumed_state["voiceSession"])
    assert str(resumed_state["sessionId"]) == conversation_id
    assert str(resumed_voice_session["realtimeSessionId"]) == realtime_session_id
    assert resumed_voice_session["status"] == "connected"


def test_widget_voice_browser_minimize_and_close_create_child_sessions(
    page: Page,
    widget_browser_harness,
) -> None:
    harness = widget_browser_harness
    page.route(
        f"{harness.base_url}/widget-livekit-client.js",
        lambda route: route.fulfill(
            status=200,
            body=_FAKE_LIVEKIT_MODULE,
            content_type="application/javascript",
        ),
    )

    page.goto(f"{harness.base_url}/widget-preview?agent_id=sales_agent")
    page.click(".widget-fab")
    page.click('[data-action="voice"]')
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Voice connected")

    first_state = _read_widget_state(page)
    conversation_id = str(first_state["sessionId"])
    first_session_id = str(dict(first_state["voiceSession"])["realtimeSessionId"])
    control_plane = harness.app.state.realtime_control_plane

    page.click('[data-action="minimize"]')
    expect(page.locator(".widget-fab")).to_be_visible()
    first_session = control_plane.sessions.load(first_session_id)
    assert first_session is not None
    assert first_session.status == "disconnected"

    page.click(".widget-fab")
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Voice connected")
    second_state = _read_widget_state(page)
    second_session_id = str(dict(second_state["voiceSession"])["realtimeSessionId"])
    assert second_session_id != first_session_id
    second_session = control_plane.sessions.load(second_session_id)
    assert second_session is not None
    assert second_session.parent_realtime_session_id == first_session_id

    with harness.provider_client() as client:
        stale_transcript = client.post(
            f"/providers/livekit/voice/sessions/{first_session_id}/transcripts",
            json={
                "text": "Can you still hear me?",
                "is_final": True,
                "idempotency_key": "stale-minimize-seg-1",
            },
        )
        assert stale_transcript.status_code == 409

    page.click('[data-action="close"]')
    expect(page.locator(".widget-fab")).to_be_visible()
    second_session = control_plane.sessions.load(second_session_id)
    assert second_session is not None
    assert second_session.status == "disconnected"

    page.click(".widget-fab")
    expect(page.locator(".widget-header-subtitle")).to_contain_text("Voice connected")
    third_state = _read_widget_state(page)
    assert str(third_state["sessionId"]) == conversation_id
    third_session_id = str(dict(third_state["voiceSession"])["realtimeSessionId"])
    assert third_session_id != second_session_id
    third_session = control_plane.sessions.load(third_session_id)
    assert third_session is not None
    assert third_session.parent_realtime_session_id == second_session_id

    voice_event_names = {
        (event.family, event.name)
        for event in control_plane.events.replay(conversation_id=conversation_id)
    }
    assert ("voice", "disconnected") in voice_event_names
    assert ("voice", "resumed") in voice_event_names
