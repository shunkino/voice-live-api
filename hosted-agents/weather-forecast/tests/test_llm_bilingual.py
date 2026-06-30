"""Tests for the bilingual (JA/EN) + LLM/tool features.

Covers:
  * language detection and English spoken output,
  * live-provider helper functions (WMO mapping, day index, no-location guard),
  * RESPONSE_MODE / provider configuration,
  * the LLMResponder tool-call loop and template fallback, using a fake
    Responses client (no network, no model calls).
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from agent.config import Settings, VALID_RESPONSE_MODES, VALID_WEATHER_PROVIDERS
from agent.llm import LLMResponder, maybe_build_responder
from agent.session_state import SessionStore
from agent.weather import (
    ForecastResponse,
    WeatherRequest,
    _day_index,
    _wmo_summary,
    detect_language,
    get_live_forecast,
    get_mock_forecast,
    parse_weather_request,
)


# ── Language detection ────────────────────────────────────────────────────────


class TestLanguageDetection:
    def test_japanese_text(self):
        assert detect_language("今日の東京の天気は？") == "ja"

    def test_english_text(self):
        assert detect_language("what's the weather in Tokyo today?") == "en"

    def test_default_is_japanese(self):
        # No Latin letters and no Japanese → default to Japanese (priority).
        assert detect_language("123 ???") == "ja"

    def test_mixed_prefers_japanese(self):
        assert detect_language("Tokyo の天気") == "ja"

    def test_parse_sets_language(self):
        assert parse_weather_request("weather in Osaka tomorrow").language == "en"
        assert parse_weather_request("大阪の明日の天気").language == "ja"


# ── Bilingual output ──────────────────────────────────────────────────────────


class TestBilingualOutput:
    def test_japanese_spoken_text(self):
        f = get_mock_forecast(WeatherRequest(location="東京", day="今日", language="ja"))
        text = f.to_spoken_text()
        assert "東京" in text and "天気" in text

    def test_english_spoken_text(self):
        f = get_mock_forecast(WeatherRequest(location="東京", day="今日", language="en"))
        text = f.to_spoken_text()
        assert text.startswith("The weather in Tokyo today is")
        assert "°C" in text

    def test_english_clarification_prompt(self):
        req = parse_weather_request("what's the weather?")
        assert req.needs_clarification is True
        assert req.clarification_prompt == "Which city's weather would you like to know?"

    def test_japanese_clarification_prompt(self):
        req = parse_weather_request("天気を教えて")
        assert req.needs_clarification is True
        assert "地域" in req.clarification_prompt


# ── Live-provider helpers ─────────────────────────────────────────────────────


class TestLiveProviderHelpers:
    def test_wmo_summary_japanese(self):
        assert _wmo_summary(0, "ja") == "快晴"
        assert _wmo_summary(95, "ja") == "雷雨"

    def test_wmo_summary_english(self):
        assert _wmo_summary(0, "en") == "clear"
        assert _wmo_summary(95, "en") == "thunderstorm"

    def test_wmo_unknown_code_falls_back(self):
        assert _wmo_summary(None, "en") == "cloudy"
        assert _wmo_summary(-1, "ja") == "くもり"

    def test_day_index(self):
        assert _day_index("今日") == 0
        assert _day_index("明日") == 1
        assert _day_index("明後日") == 2
        assert 0 <= _day_index("週末") <= 6

    def test_live_forecast_no_location_returns_none(self):
        # Guard path: never touches the network when there is no location.
        result = asyncio.run(get_live_forecast(WeatherRequest(location=None)))
        assert result is None


# ── Configuration ─────────────────────────────────────────────────────────────


class TestResponseModeConfig:
    def test_live_is_a_valid_provider(self):
        assert "live" in VALID_WEATHER_PROVIDERS

    def test_response_modes(self):
        assert VALID_RESPONSE_MODES == frozenset({"template", "llm"})

    def test_default_response_mode_is_template(self):
        assert Settings().response_mode == "template"

    def test_from_env_rejects_bad_response_mode(self, monkeypatch):
        monkeypatch.setenv("RESPONSE_MODE", "bogus")
        with pytest.raises(ValueError):
            Settings.from_env()

    def test_from_env_reads_llm_mode(self, monkeypatch):
        monkeypatch.setenv("RESPONSE_MODE", "llm")
        monkeypatch.setenv("WEATHER_PROVIDER", "live")
        monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://x/api/projects/p")
        monkeypatch.setenv("LLM_MODEL_DEPLOYMENT", "gpt-4.1-mini")
        s = Settings.from_env()
        assert s.response_mode == "llm"
        assert s.weather_provider == "live"
        assert s.project_endpoint == "https://x/api/projects/p"
        assert s.llm_model_deployment == "gpt-4.1-mini"


class TestMaybeBuildResponder:
    def test_template_mode_returns_none(self):
        assert maybe_build_responder(Settings(response_mode="template")) is None

    def test_llm_mode_without_endpoint_returns_none(self):
        assert maybe_build_responder(Settings(response_mode="llm", project_endpoint="")) is None

    def test_llm_mode_with_endpoint_builds_responder(self):
        r = maybe_build_responder(
            Settings(response_mode="llm", project_endpoint="https://x/api/projects/p")
        )
        assert isinstance(r, LLMResponder)


# ── LLM responder (fake Responses client, no network) ─────────────────────────


class _FakeFunctionCall:
    type = "function_call"

    def __init__(self, call_id: str, name: str, arguments: str):
        self.call_id = call_id
        self.name = name
        self.arguments = arguments


class _FakeTextItem:
    type = "message"


class _FakeResponse:
    def __init__(self, output, output_text=""):
        self.output = output
        self.output_text = output_text


class _FakeResponses:
    """Returns scripted responses in order; records each create() call."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._scripted.pop(0)


class _RaisingResponses:
    def create(self, **kwargs):
        raise RuntimeError("model unavailable")


def _responder_with(fake, provider="mock"):
    r = LLMResponder(project_endpoint="https://x/api/projects/p",
                     model_deployment="gpt-4.1-mini", provider=provider)
    r._responses = fake  # inject fake; skips real client creation
    return r


class TestLLMResponder:
    def test_tool_loop_returns_final_text(self):
        fake = _FakeResponses([
            _FakeResponse(output=[_FakeFunctionCall("c1", "get_weather",
                          json.dumps({"location": "東京", "day": "今日"}))]),
            _FakeResponse(output=[_FakeTextItem()], output_text="東京の今日は晴れです。"),
        ])
        responder = _responder_with(fake, provider="mock")
        session = SessionStore().get_or_create("s1")

        out = asyncio.run(responder.respond("今日の東京の天気は？", session))
        assert out is not None
        msg = json.loads(out)
        assert msg["type"] == "weather.response"
        assert msg["text"] == "東京の今日は晴れです。"
        # The session was updated from the tool call (enables follow-ups).
        assert session.latest_location == "東京"
        # The second model call received the tool output.
        second_input = fake.calls[1]["input"]
        assert any(
            isinstance(i, dict) and i.get("type") == "function_call_output"
            for i in second_input
        )

    def test_no_tool_call_returns_text_directly(self):
        fake = _FakeResponses([
            _FakeResponse(output=[_FakeTextItem()],
                          output_text="すみません、天気のことならお答えできます。"),
        ])
        responder = _responder_with(fake)
        session = SessionStore().get_or_create("s2")
        out = asyncio.run(responder.respond("おすすめのレストランは？", session))
        assert json.loads(out)["text"].startswith("すみません")

    def test_model_error_returns_none_for_fallback(self):
        responder = _responder_with(_RaisingResponses())
        session = SessionStore().get_or_create("s3")
        assert asyncio.run(responder.respond("今日の東京の天気は？", session)) is None

    def test_missing_endpoint_returns_none(self):
        responder = LLMResponder(project_endpoint="", model_deployment="m", provider="mock")
        session = SessionStore().get_or_create("s4")
        assert asyncio.run(responder.respond("今日の東京の天気は？", session)) is None
