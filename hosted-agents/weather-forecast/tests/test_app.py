"""test_app.py – pytest coverage for app-level fixes.

- Session binding: WebSocket session is bound once from agent_session_id query
  param; frame-level session_id must NOT switch the active session.
- Auth rejection: query params carrying bearer tokens are rejected (code 1008)
  for all common token-carrier names, case-insensitively.
- FR-007 via app: a weather.request containing a city name but no weather/day
  keywords returns weather.redirect, not a weather forecast.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from agent.app import create_app
from agent.config import Settings
from agent.session_state import SessionStore


def _app(store: SessionStore | None = None):
    cfg = Settings(weather_provider="mock")
    return create_app(settings=cfg, store=store or SessionStore())


# ── Auth-token query param rejection ─────────────────────────────────────────


class TestAuthQueryParamRejection:
    """WebSocket upgrade with token-bearing query params must be rejected (1008)."""

    @pytest.mark.parametrize("param_name", [
        "authorization",
        "Authorization",
        "AUTHORIZATION",
        "access_token",
        "Access_Token",
        "ACCESS_TOKEN",
        "access-token",
        "Access-Token",
        "token",
        "Token",
        "TOKEN",
        "auth",
        "Auth",
        "AUTH",
        "bearer_token",
        "Bearer_Token",
        "id_token",
        "refresh_token",
        "accessToken",
        "api_token",
    ])
    def test_auth_param_rejected(self, param_name):
        from starlette.websockets import WebSocketDisconnect

        client = TestClient(_app(), raise_server_exceptions=False)
        disconnected = False
        try:
            with client.websocket_connect(
                f"/invocations_ws?{param_name}=some-bearer-token"
            ):
                pass  # Server should not accept the connection
        except WebSocketDisconnect as exc:
            disconnected = True
            assert exc.code == 1008, (
                f"Expected close code 1008 for auth param {param_name!r}, got {exc.code}"
            )
        assert disconnected, (
            f"Expected WebSocketDisconnect(1008) for auth param {param_name!r}"
        )


# ── Session binding ───────────────────────────────────────────────────────────


class TestSessionBinding:
    """Session is bound once from agent_session_id query param."""

    def test_session_id_bound_from_query_param(self):
        store = SessionStore()
        client = TestClient(_app(store))
        session_id = "test-session-bind-001"
        with client.websocket_connect(
            f"/invocations_ws?agent_session_id={session_id}"
        ) as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の東京の天気は？",
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "weather.response"
        # Session must have been stored under the query-param session_id
        assert store.get(session_id) is not None

    def test_frame_session_id_does_not_switch_session(self):
        """Sending a different session_id in the frame must not switch sessions."""
        store = SessionStore()
        client = TestClient(_app(store))
        connection_session = "session-connection"
        rogue_session = "session-rogue"

        with client.websocket_connect(
            f"/invocations_ws?agent_session_id={connection_session}"
        ) as ws:
            # First turn — sets location in connection_session
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の東京の天気は？",
                "session_id": connection_session,
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "weather.response"

            # Second turn — uses a different session_id in the frame
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "明日は？",
                "session_id": rogue_session,
            }))
            resp2 = json.loads(ws.receive_text())
            # The response should still work (session context preserved)
            assert resp2["type"] == "weather.response"

        # The rogue session must NOT have been created in the store
        assert store.get(rogue_session) is None, (
            "Frame-level session_id must not create or switch to a different session"
        )
        # The connection session must have been used throughout
        conn_sess = store.get(connection_session)
        assert conn_sess is not None
        assert conn_sess.turn_count >= 2

    def test_no_session_id_gets_local_uuid(self):
        """No agent_session_id query param → local UUID session is created."""
        store = SessionStore()
        client = TestClient(_app(store))
        with client.websocket_connect("/invocations_ws") as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の東京の天気は？",
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "weather.response"
        # Exactly one session must have been created
        assert store.count() == 1


# ── FR-007 via app: non-weather requests are redirected ──────────────────────


class TestNonWeatherRedirectViaApp:
    """FR-007: weather.request with no weather/day keywords returns weather.redirect."""

    def test_restaurant_query_returns_redirect(self):
        client = TestClient(_app())
        with client.websocket_connect("/invocations_ws") as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "東京のおすすめレストランは？",
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "weather.redirect", (
                "Non-weather request must return weather.redirect, not a forecast"
            )

    def test_redirect_text_is_japanese(self):
        client = TestClient(_app())
        with client.websocket_connect("/invocations_ws") as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "大阪のショッピングモールを教えて",
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "weather.redirect"
            text = resp.get("text", "")
            has_cjk = any(
                "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u309f"
                for ch in text
            )
            assert has_cjk, "Redirect text should be in Japanese"

    def test_valid_weather_request_not_redirected(self):
        """Valid weather requests must still return weather.response."""
        client = TestClient(_app())
        with client.websocket_connect("/invocations_ws") as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の東京の天気は？",
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "weather.response"

    def test_followup_day_keyword_not_redirected(self):
        """'明日は？' contains a day keyword so it is treated as a follow-up."""
        store = SessionStore()
        client = TestClient(_app(store))
        session_id = "fr007-followup"
        with client.websocket_connect(
            f"/invocations_ws?agent_session_id={session_id}"
        ) as ws:
            # Establish session location
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の東京の天気は？",
            }))
            r1 = json.loads(ws.receive_text())
            assert r1["type"] == "weather.response"

            # Follow-up with day keyword only
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "明日は？",
            }))
            r2 = json.loads(ws.receive_text())
            assert r2["type"] == "weather.response", (
                "Follow-up with day keyword must not be redirected"
            )

    def test_location_only_reply_to_clarification_not_redirected(self):
        """A city-only reply after clarification must complete the pending weather request."""
        store = SessionStore()
        client = TestClient(_app(store))
        session_id = "clarification-location-only"

        with client.websocket_connect(
            f"/invocations_ws?agent_session_id={session_id}"
        ) as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の天気は？",
            }))
            r1 = json.loads(ws.receive_text())
            assert r1["type"] == "weather.clarification"

            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "東京",
            }))
            r2 = json.loads(ws.receive_text())
            assert r2["type"] == "weather.response"
            assert r2["location"] == "東京"

    def test_pending_clarification_rejects_off_topic_city_query(self):
        """A pending clarification must not turn off-topic city text into a forecast."""
        store = SessionStore()
        client = TestClient(_app(store))
        session_id = "pending-off-topic"

        with client.websocket_connect(
            f"/invocations_ws?agent_session_id={session_id}"
        ) as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の天気は？",
            }))
            r1 = json.loads(ws.receive_text())
            assert r1["type"] == "weather.clarification"

            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "東京のおすすめレストランは？",
            }))
            r2 = json.loads(ws.receive_text())
            assert r2["type"] == "weather.redirect"

    def test_pending_clarification_rejects_off_topic_city_query_with_day(self):
        """Day words in an off-topic city query must not bypass FR-007."""
        store = SessionStore()
        client = TestClient(_app(store))
        session_id = "pending-off-topic-with-day"

        with client.websocket_connect(
            f"/invocations_ws?agent_session_id={session_id}"
        ) as ws:
            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "今日の天気は？",
            }))
            r1 = json.loads(ws.receive_text())
            assert r1["type"] == "weather.clarification"

            ws.send_text(json.dumps({
                "type": "weather.request",
                "text": "東京の明日のおすすめレストランは？",
            }))
            r2 = json.loads(ws.receive_text())
            assert r2["type"] == "weather.redirect"
