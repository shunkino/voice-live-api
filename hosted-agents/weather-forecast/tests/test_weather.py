"""test_weather.py – pytest coverage for T011 and T019.

T011: Mock weather provider returns valid ForecastResponse for the five
      supported Japanese cities (Tokyo, Osaka, Kyoto, Sapporo, Fukuoka)
      using both kanji and romanized city names.
T019: Missing-location detection — a weather request without a location sets
      needs_clarification=True and provides a Japanese clarification prompt.
      A request WITH a location does not need clarification.

Implementation uses a function-based API:
  get_mock_forecast(request: WeatherRequest) -> ForecastResponse
  parse_weather_request(text, session_location=None, session_day=None) -> WeatherRequest
"""
from __future__ import annotations

import pytest

from agent.weather import (
    ForecastResponse,
    WeatherRequest,
    get_mock_forecast,
    is_location_only_reply,
    is_weather_request,
    parse_weather_request,
)


def _forecast(location: str, day: str = "今日") -> ForecastResponse:
    """Helper: build a mock forecast for a given location and day."""
    return get_mock_forecast(WeatherRequest(location=location, day=day))


# ── T011: Mock weather provider – one test per supported city ─────────────────


SUPPORTED_CITIES_JP = ["東京", "大阪", "京都", "札幌", "福岡"]
SUPPORTED_CITIES_EN = ["Tokyo", "Osaka", "Kyoto", "Sapporo", "Fukuoka"]


class TestMockWeatherProviderSupportedCities:
    """FR-004: System MUST support at least Tokyo, Osaka, Kyoto, Sapporo, Fukuoka."""

    # ── Kanji city names ──────────────────────────────────────────────────────

    def test_forecast_tokyo_kanji(self):
        result = _forecast("東京")
        assert isinstance(result, ForecastResponse)
        assert result.location == "東京"

    def test_forecast_osaka_kanji(self):
        result = _forecast("大阪")
        assert isinstance(result, ForecastResponse)
        assert result.location == "大阪"

    def test_forecast_kyoto_kanji(self):
        result = _forecast("京都")
        assert isinstance(result, ForecastResponse)
        assert result.location == "京都"

    def test_forecast_sapporo_kanji(self):
        result = _forecast("札幌")
        assert isinstance(result, ForecastResponse)
        assert result.location == "札幌"

    def test_forecast_fukuoka_kanji(self):
        result = _forecast("福岡")
        assert isinstance(result, ForecastResponse)
        assert result.location == "福岡"

    # ── Romanized city names ──────────────────────────────────────────────────

    @pytest.mark.parametrize("city_en", SUPPORTED_CITIES_EN)
    def test_forecast_romanized_city_name(self, city_en):
        """Romanized names should also resolve to a valid ForecastResponse."""
        result = get_mock_forecast(WeatherRequest(location=city_en))
        assert isinstance(result, ForecastResponse)


class TestForecastResponseFields:
    """All ForecastResponse fields from data-model.md must be present."""

    def test_summary_not_empty(self):
        result = _forecast("東京")
        assert result.summary
        assert len(result.summary) > 0

    def test_summary_suitable_for_spoken_output(self):
        """Summary must be a non-empty string (suitable for TTS)."""
        result = _forecast("大阪")
        assert isinstance(result.summary, str)
        assert len(result.summary.strip()) > 0

    def test_day_field_present(self):
        result = _forecast("京都", day="今日")
        assert result.day == "今日"

    def test_day_defaults_to_today(self):
        result = _forecast("東京")
        assert result.day is not None
        assert len(result.day) > 0

    def test_is_demo_data_true_for_mock(self):
        """FR-011 / data-model.md: mock responses MUST set is_demo_data=True."""
        result = _forecast("東京")
        assert result.is_demo_data is True

    def test_source_field_present(self):
        result = _forecast("福岡")
        assert result.source is not None

    def test_temperature_c_is_optional(self):
        result = _forecast("東京")
        # temperature_c may be None or a numeric value — both are valid
        assert result.temperature_c is None or isinstance(result.temperature_c, (int, float))

    def test_precipitation_chance_is_optional(self):
        result = _forecast("大阪")
        assert result.precipitation_chance is None or isinstance(result.precipitation_chance, (int, float))

    @pytest.mark.parametrize("city", SUPPORTED_CITIES_JP)
    def test_all_cities_return_demo_data(self, city):
        result = _forecast(city)
        assert result.is_demo_data is True, (
            f"Mock provider must flag is_demo_data=True for {city}"
        )

    @pytest.mark.parametrize("city", SUPPORTED_CITIES_JP)
    def test_all_cities_have_nonempty_summary(self, city):
        result = _forecast(city)
        assert len(result.summary.strip()) > 0, (
            f"Mock provider must return a non-empty summary for {city}"
        )


# ── T019: Missing-location detection and clarification prompt ─────────────────


class TestParseWeatherRequestMissingLocation:
    """FR-006: System MUST ask for location when it is absent."""

    def test_no_location_sets_needs_clarification(self):
        req = parse_weather_request("今日の天気は？")
        assert req.needs_clarification is True

    def test_no_location_clarification_prompt_nonempty(self):
        req = parse_weather_request("今日の天気は？")
        assert req.clarification_prompt
        assert len(req.clarification_prompt) > 0

    def test_clarification_prompt_in_japanese(self):
        req = parse_weather_request("今日の天気は？")
        # A Japanese clarification must contain at least one CJK character
        has_cjk = any("\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u309f" or "\u30a0" <= ch <= "\u30ff" for ch in req.clarification_prompt)
        assert has_cjk, "Clarification prompt should be in Japanese"

    def test_no_location_location_field_is_none_or_empty(self):
        req = parse_weather_request("今日の天気は？")
        assert not req.location

    def test_no_location_no_session_context(self):
        req = parse_weather_request("天気教えて", session_location=None)
        assert req.needs_clarification is True

    def test_session_location_satisfies_missing_location(self):
        """If session has a prior location, clarification is not needed."""
        req = parse_weather_request("今日の天気は？", session_location="東京")
        assert req.needs_clarification is False

    def test_session_location_is_used_in_request(self):
        req = parse_weather_request("今日の天気は？", session_location="大阪")
        assert req.location == "大阪"


class TestParseWeatherRequestWithLocation:
    """FR-004/FR-005: Explicit location sets needs_clarification=False."""

    def test_tokyo_explicit_no_clarification(self):
        req = parse_weather_request("今日の東京の天気は？")
        assert req.needs_clarification is False

    def test_tokyo_explicit_location_field(self):
        req = parse_weather_request("今日の東京の天気は？")
        assert req.location == "東京"

    def test_osaka_explicit_no_clarification(self):
        req = parse_weather_request("大阪の天気を教えてください")
        assert req.needs_clarification is False

    def test_osaka_location_field(self):
        req = parse_weather_request("大阪の天気を教えてください")
        assert req.location == "大阪"

    def test_kyoto_explicit(self):
        req = parse_weather_request("京都の今日の天気は？")
        assert req.location == "京都"
        assert req.needs_clarification is False

    def test_sapporo_explicit(self):
        req = parse_weather_request("札幌の天気は？")
        assert req.location == "札幌"
        assert req.needs_clarification is False

    def test_fukuoka_explicit(self):
        req = parse_weather_request("福岡の天気を教えて")
        assert req.location == "福岡"
        assert req.needs_clarification is False

    def test_with_location_no_clarification_needed(self):
        req = parse_weather_request("今日の東京の天気は？")
        # When a location is available, needs_clarification must be False
        assert req.needs_clarification is False


class TestWeatherRequestDefaults:
    """WeatherRequest field defaults as per data-model.md."""

    def test_day_defaults_to_today_when_unspecified(self):
        req = parse_weather_request("東京の天気は？")
        assert req.day is not None
        assert len(req.day) > 0

    def test_language_defaults_to_japanese(self):
        req = parse_weather_request("東京の天気は？")
        assert req.language == "ja"

    def test_tomorrow_day_parsed(self):
        req = parse_weather_request("東京の明日の天気は？")
        assert "明日" in req.day or "tomorrow" in req.day.lower()


# ── FR-007: is_weather_request() — non-weather detection ─────────────────────


class TestIsWeatherRequest:
    """FR-007: Non-weather requests must be identified so they can be redirected."""

    # ── Positive (weather) ────────────────────────────────────────────────────

    def test_explicit_weather_keyword_returns_true(self):
        assert is_weather_request("今日の東京の天気は？") is True

    def test_forecast_keyword_returns_true(self):
        assert is_weather_request("大阪の天気予報を教えてください") is True

    def test_rain_keyword_returns_true(self):
        assert is_weather_request("明日は雨ですか？") is True

    def test_temperature_keyword_returns_true(self):
        assert is_weather_request("東京の気温は何度ですか？") is True

    def test_day_keyword_only_returns_true(self):
        """Follow-up like '明日は？' has only a day keyword — still weather context."""
        assert is_weather_request("明日は？") is True

    def test_today_keyword_returns_true(self):
        assert is_weather_request("今日は？") is True

    def test_english_weather_keyword_returns_true(self):
        assert is_weather_request("What's the weather in Tokyo today?") is True

    def test_english_forecast_keyword_returns_true(self):
        assert is_weather_request("Give me a forecast for Osaka") is True

    def test_snow_keyword_returns_true(self):
        assert is_weather_request("札幌は雪が降りますか？") is True

    # ── Negative (non-weather) ────────────────────────────────────────────────

    def test_restaurant_query_returns_false(self):
        """FR-007: '東京のおすすめレストランは？' must NOT be treated as weather."""
        assert is_weather_request("東京のおすすめレストランは？") is False

    def test_shopping_query_returns_false(self):
        assert is_weather_request("大阪のショッピングモールを教えて") is False

    def test_sightseeing_query_returns_false(self):
        assert is_weather_request("京都の観光スポットはどこですか？") is False

    def test_generic_question_returns_false(self):
        assert is_weather_request("こんにちは") is False

    def test_city_name_alone_returns_false(self):
        """A bare city name with no weather/day context is not a weather request."""
        assert is_weather_request("東京") is False

    def test_english_non_weather_returns_false(self):
        assert is_weather_request("What are the best restaurants in Tokyo?") is False

    def test_non_weather_restaurant_query_with_day_false(self):
        assert is_weather_request("東京の明日のおすすめレストランは？") is False

    def test_english_non_weather_restaurant_query_with_day_false(self):
        assert is_weather_request("What are the best restaurants in Tokyo tomorrow?") is False


class TestIsLocationOnlyReply:
    """Clarification replies should be accepted only when they are just locations."""

    @pytest.mark.parametrize("text", ["東京", "東京です", "東京でお願いします", "Osaka", "京都ください"])
    def test_location_only_reply_true(self, text):
        assert is_location_only_reply(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "東京のおすすめレストランは？",
            "東京の明日のおすすめレストランは？",
            "大阪のショッピングモールを教えて",
            "What are the best restaurants in Tokyo?",
            "こんにちは",
        ],
    )
    def test_off_topic_or_non_location_reply_false(self, text):
        assert is_location_only_reply(text) is False
