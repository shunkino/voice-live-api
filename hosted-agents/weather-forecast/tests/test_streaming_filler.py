"""Tests for the tool-latency "please wait" filler in the invocations SSE stream.

The filler is emitted by ``agent.server._stream_invocation`` when the (possibly
slow) per-turn work doesn't finish within ``tool_wait_seconds``. These tests
drive that generator directly with a fast/slow stand-in for the weather work, so
no model or network is involved.
"""
from __future__ import annotations

import asyncio
import json

import agent.server as server_mod
from agent.protocol import WeatherResponseMessage
from agent.session_state import SessionStore


def _make_work(delay: float, text: str):
    async def fake_handle(user_text, session, provider, responder):
        if delay:
            await asyncio.sleep(delay)
        return WeatherResponseMessage(
            text=text, location="東京", day="今日", demo_data=False
        ).to_json()

    return fake_handle


def _collect(**kwargs):
    async def run():
        return [chunk async for chunk in server_mod._stream_invocation(**kwargs)]

    lines = asyncio.run(run())
    return [json.loads(line[len("data: "):]) for line in lines]


def _session(name):
    return SessionStore().get_or_create(name)


def test_filler_emitted_when_slow(monkeypatch):
    monkeypatch.setattr(server_mod, "_handle_weather_request", _make_work(0.3, "東京は晴れです。"))
    events = _collect(
        user_text="今日の東京の天気は？", session=_session("s1"),
        provider="mock", responder=None, language="ja", wait_seconds=0.05,
    )
    # First spoken chunk is the filler, before the real answer.
    assert events[0]["type"] == "output_audio_transcription.delta"
    assert events[0]["delta"] == "少々お待ちください。"
    assert any(e.get("delta") == "東京は晴れです。" for e in events)
    done = next(e for e in events if e["type"] == "output_audio_transcription.done")
    assert "少々お待ちください" in done["text"] and "東京は晴れです" in done["text"]
    assert events[-1] == {"type": "done"}


def test_no_filler_when_fast(monkeypatch):
    monkeypatch.setattr(server_mod, "_handle_weather_request", _make_work(0.0, "東京は晴れです。"))
    events = _collect(
        user_text="今日の東京の天気は？", session=_session("s2"),
        provider="mock", responder=None, language="ja", wait_seconds=0.5,
    )
    assert all("お待ち" not in e.get("delta", "") for e in events)
    assert events[0]["delta"] == "東京は晴れです。"


def test_filler_disabled_when_wait_zero(monkeypatch):
    monkeypatch.setattr(server_mod, "_handle_weather_request", _make_work(0.2, "東京は晴れです。"))
    events = _collect(
        user_text="今日の東京の天気は？", session=_session("s3"),
        provider="mock", responder=None, language="ja", wait_seconds=0.0,
    )
    assert all("お待ち" not in e.get("delta", "") for e in events)


def test_english_filler(monkeypatch):
    monkeypatch.setattr(server_mod, "_handle_weather_request", _make_work(0.3, "It's sunny in Tokyo."))
    events = _collect(
        user_text="what's the weather in Tokyo?", session=_session("s4"),
        provider="mock", responder=None, language="en", wait_seconds=0.05,
    )
    assert events[0]["delta"] == "One moment, please."


def test_filler_config_default():
    from agent.config import Settings
    assert Settings().tool_wait_seconds == 1.2

    s = Settings.from_env  # ensure attribute exists / parses
    import os
    os.environ["TOOL_WAIT_SECONDS"] = "2.5"
    try:
        assert Settings.from_env().tool_wait_seconds == 2.5
    finally:
        del os.environ["TOOL_WAIT_SECONDS"]
