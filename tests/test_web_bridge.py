"""Regression test for the web bridge teardown.

Ensures that when the browser sends a ``stop`` message (or disconnects), the
``VoiceLiveBridge`` tears down promptly instead of hanging on the still-open
service event stream (which previously leaked the Azure session).
"""
from __future__ import annotations

import asyncio
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from voicelive_demo.config import ExperimentConfig
from voicelive_demo.web import server as server_mod
from voicelive_demo.web.server import VoiceLiveBridge, create_app


class _Resource:
    def __init__(self):
        self.calls = []

    async def update(self, session=None):
        self.calls.append(("update", session))

    async def append(self, audio=None):
        self.calls.append(("append", audio))


class _FakeConnection:
    """Service connection whose event stream stays open until cancelled."""

    def __init__(self):
        self.session = _Resource()
        self.input_audio_buffer = _Resource()
        self.closed = False

    async def send(self, event):
        pass

    async def __aiter__(self):
        # Never yields; simulates an idle-but-open service stream.
        await asyncio.Event().wait()
        yield  # pragma: no cover

    async def close(self):
        self.closed = True


class _FakeConnectCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        await self.conn.close()
        return False


class _FakeWebSocket:
    def __init__(self, messages):
        self._messages = list(messages)
        self.sent = []

    async def receive_text(self):
        if self._messages:
            return self._messages.pop(0)
        # After scripted messages, block like a quiet-but-open socket.
        await asyncio.Event().wait()

    async def send_text(self, text):
        self.sent.append(text)


def test_stop_message_tears_down_bridge():
    # Self-contained runner so the suite works without pytest-asyncio.
    asyncio.run(_run_stop_case())


async def _run_stop_case():
    conn = _FakeConnection()
    orig = server_mod.connect
    server_mod.connect = lambda **kw: _FakeConnectCtx(conn)
    try:
        ws = _FakeWebSocket(['{"type": "stop"}'])
        bridge = VoiceLiveBridge(ExperimentConfig(), lambda: object(), ws)
        await asyncio.wait_for(bridge.run(), timeout=5)
        assert conn.closed is True
    finally:
        server_mod.connect = orig


def test_websocket_route_does_not_require_fake_websocket_param():
    """Guard against the FastAPI import-scope bug that caused /ws 403s.

    With ``from __future__ import annotations`` active, importing ``WebSocket``
    inside ``create_app`` left FastAPI unable to resolve the ``websocket``
    annotation, so it treated it as an extra required query param and rejected
    the handshake with 403. The route's dependant must expose no such params.
    """
    app = create_app(ExperimentConfig(), lambda: object())
    ws_route = next(r for r in app.routes if getattr(r, "path", None) == "/ws")
    dependant = ws_route.dependant
    assert dependant.query_params == []
    assert dependant.body_params == []


def test_agent_mode_connects_with_agent_config():
    """In hosted-agent mode the bridge connects via agent_config, not model."""
    asyncio.run(_run_agent_mode_case())


async def _run_agent_mode_case():
    conn = _FakeConnection()
    captured = {}
    orig = server_mod.connect

    def _fake_connect(**kw):
        captured.update(kw)
        return _FakeConnectCtx(conn)

    server_mod.connect = _fake_connect
    try:
        cfg = ExperimentConfig(
            endpoint="https://acct.services.ai.azure.com",
            agent_name="weather-forecast-agent",
            agent_project_name="weather-agent-proj",
        )
        assert cfg.use_agent is True
        ws = _FakeWebSocket(['{"type": "stop"}'])
        bridge = VoiceLiveBridge(cfg, lambda: object(), ws)
        await asyncio.wait_for(bridge.run(), timeout=5)
    finally:
        server_mod.connect = orig

    assert "model" not in captured
    expected = server_mod.agent_connect_kwargs(cfg)
    for key, value in expected.items():
        assert captured.get(key) == value
    # Either form must carry the agent + project identifiers.
    flat = {**captured, **captured.get("agent_config", {})}
    assert flat.get("agent_name") == "weather-forecast-agent"
    assert flat.get("project_name") == "weather-agent-proj"


def test_model_mode_connects_with_model():
    """Without agent config the bridge connects with a bare model."""
    asyncio.run(_run_model_mode_case())


async def _run_model_mode_case():
    conn = _FakeConnection()
    captured = {}
    orig = server_mod.connect

    def _fake_connect(**kw):
        captured.update(kw)
        return _FakeConnectCtx(conn)

    server_mod.connect = _fake_connect
    try:
        cfg = ExperimentConfig(model="gpt-realtime")
        assert cfg.use_agent is False
        ws = _FakeWebSocket(['{"type": "stop"}'])
        bridge = VoiceLiveBridge(cfg, lambda: object(), ws)
        await asyncio.wait_for(bridge.run(), timeout=5)
    finally:
        server_mod.connect = orig

    assert "agent_config" not in captured
    assert captured.get("model") == "gpt-realtime"
