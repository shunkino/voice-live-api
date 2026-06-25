"""Weather provider: models, Japanese city/day parsing, mock data, optional JMA skeleton.

Supported cities (Japanese and English aliases):
  東京 / Tokyo, 大阪 / Osaka, 京都 / Kyoto, 札幌 / Sapporo, 福岡 / Fukuoka

Mock data is deterministic and clearly identified as demo data.
The JMA provider shape is a skeleton; it falls back to mock on any error.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── City normalisation ────────────────────────────────────────────────────────

_CITY_ALIASES: dict[str, str] = {
    "東京": "東京",
    "tokyo": "東京",
    "大阪": "大阪",
    "osaka": "大阪",
    "京都": "京都",
    "kyoto": "京都",
    "札幌": "札幌",
    "sapporo": "札幌",
    "福岡": "福岡",
    "fukuoka": "福岡",
}

SUPPORTED_CITIES = frozenset(_CITY_ALIASES.values())

_DAY_ALIASES: dict[str, str] = {
    "今日": "今日",
    "本日": "今日",
    "きょう": "今日",
    "today": "今日",
    "明日": "明日",
    "あした": "明日",
    "あす": "明日",
    "tomorrow": "明日",
    "明後日": "明後日",
    "あさって": "明後日",
    "day after tomorrow": "明後日",
    "週末": "週末",
    "weekend": "週末",
}

_DAY_DEFAULT = "今日"

# ── Weather keyword detection ─────────────────────────────────────────────────

_WEATHER_KEYWORDS_JP = frozenset({
    "天気", "気温", "降水", "予報", "晴れ", "くもり", "曇り", "雨", "雪",
    "台風", "気象", "霧", "嵐", "風速", "湿度", "気圧", "紫外線",
})
_WEATHER_KEYWORDS_EN = frozenset({
    "weather", "forecast", "temperature", "rain", "snow", "sunny",
    "cloudy", "storm", "humid", "wind",
})
_OFF_TOPIC_KEYWORDS_JP = frozenset({
    "おすすめ",
    "レストラン",
    "飲食店",
    "ランチ",
    "ディナー",
    "ショッピング",
    "買い物",
    "観光",
    "ホテル",
    "行き方",
    "経路",
    "ニュース",
})
_OFF_TOPIC_KEYWORDS_EN = frozenset({
    "restaurant",
    "restaurants",
    "shopping",
    "hotel",
    "hotels",
    "tourist",
    "tourism",
    "directions",
    "news",
})

# ── Mock forecast data ────────────────────────────────────────────────────────

_MOCK_FORECASTS: dict[str, dict[str, dict]] = {
    "東京": {
        "今日":   {"summary": "晴れ時々くもり", "temp_c": 26, "precip": 10},
        "明日":   {"summary": "くもり一時雨", "temp_c": 23, "precip": 40},
        "明後日": {"summary": "晴れ", "temp_c": 28, "precip": 5},
        "週末":   {"summary": "晴れのち曇り", "temp_c": 25, "precip": 20},
        "_default": {"summary": "おおむね晴れ", "temp_c": 25, "precip": 15},
    },
    "大阪": {
        "今日":   {"summary": "晴れ", "temp_c": 28, "precip": 5},
        "明日":   {"summary": "薄くもり", "temp_c": 27, "precip": 15},
        "明後日": {"summary": "くもり時々雨", "temp_c": 24, "precip": 50},
        "週末":   {"summary": "晴れ時々くもり", "temp_c": 27, "precip": 10},
        "_default": {"summary": "おおむね晴れ", "temp_c": 27, "precip": 10},
    },
    "京都": {
        "今日":   {"summary": "晴れ時々くもり", "temp_c": 29, "precip": 10},
        "明日":   {"summary": "くもり", "temp_c": 26, "precip": 30},
        "明後日": {"summary": "雨", "temp_c": 22, "precip": 70},
        "週末":   {"summary": "晴れ", "temp_c": 28, "precip": 5},
        "_default": {"summary": "おおむね晴れ", "temp_c": 27, "precip": 15},
    },
    "札幌": {
        "今日":   {"summary": "くもり", "temp_c": 18, "precip": 25},
        "明日":   {"summary": "晴れ", "temp_c": 20, "precip": 5},
        "明後日": {"summary": "晴れ時々くもり", "temp_c": 21, "precip": 10},
        "週末":   {"summary": "くもり一時雨", "temp_c": 17, "precip": 35},
        "_default": {"summary": "おおむね曇り", "temp_c": 18, "precip": 20},
    },
    "福岡": {
        "今日":   {"summary": "晴れ", "temp_c": 27, "precip": 10},
        "明日":   {"summary": "晴れ時々くもり", "temp_c": 26, "precip": 15},
        "明後日": {"summary": "くもり", "temp_c": 24, "precip": 30},
        "週末":   {"summary": "晴れのち雨", "temp_c": 25, "precip": 40},
        "_default": {"summary": "おおむね晴れ", "temp_c": 26, "precip": 15},
    },
}

# ── Models ────────────────────────────────────────────────────────────────────


@dataclass
class WeatherRequest:
    location: Optional[str]
    day: str = _DAY_DEFAULT
    language: str = "ja"
    needs_clarification: bool = False
    clarification_prompt: str = "どの地域の天気を知りたいですか？"


@dataclass
class ForecastResponse:
    location: str
    day: str
    summary: str
    temperature_c: Optional[int] = None
    precipitation_chance: Optional[int] = None
    is_demo_data: bool = True
    source: str = "demo"

    def to_spoken_text(self) -> str:
        """Return a natural Japanese sentence suitable for voice output."""
        temp_part = f"最高気温は{self.temperature_c}度前後の見込みです。" if self.temperature_c is not None else ""
        return f"{self.location}の{self.day}の天気は{self.summary}です。{temp_part}"


# ── Parsing ───────────────────────────────────────────────────────────────────


def _normalise_city(text: str) -> Optional[str]:
    """Extract a known city from arbitrary text; returns canonical Japanese name."""
    lower = text.lower()
    for alias, canonical in _CITY_ALIASES.items():
        if alias in lower or alias in text:
            return canonical
    return None


def _normalise_day(text: str) -> str:
    lower = text.lower()
    for alias, canonical in _DAY_ALIASES.items():
        if alias in lower or alias in text:
            return canonical
    return _DAY_DEFAULT


def parse_weather_request(
    text: str,
    session_location: Optional[str] = None,
    session_day: Optional[str] = None,
) -> WeatherRequest:
    """Parse a free-form user text into a WeatherRequest.

    Falls back to session state for location/day when not mentioned explicitly.
    Sets needs_clarification=True when no location can be determined.
    """
    location = _normalise_city(text) or session_location
    day = _normalise_day(text) if _normalise_day(text) != _DAY_DEFAULT or _day_mentioned(text) else (session_day or _DAY_DEFAULT)

    if location is None:
        return WeatherRequest(
            location=None,
            day=day,
            needs_clarification=True,
        )

    return WeatherRequest(location=location, day=day)


def _day_mentioned(text: str) -> bool:
    lower = text.lower()
    return any(alias in lower or alias in text for alias in _DAY_ALIASES)


def is_weather_request(text: str) -> bool:
    """Return True when text appears to be a weather-related request.

    Accepts messages containing any weather keyword or day/time reference
    (the latter covers follow-up turns like '明日は？').  Pure non-weather
    messages that only mention a city (e.g. 'Tokyo のレストランは？') return
    False so FR-007 redirects can be applied.
    """
    lower = text.lower()
    if is_off_topic_request(text):
        return False
    if any(kw in text for kw in _WEATHER_KEYWORDS_JP):
        return True
    if any(kw in lower for kw in _WEATHER_KEYWORDS_EN):
        return True
    if any(alias in lower or alias in text for alias in _DAY_ALIASES):
        return True
    return False


def is_off_topic_request(text: str) -> bool:
    """Return True for known non-weather intents that should be redirected."""
    lower = text.lower()
    return (
        any(kw in text for kw in _OFF_TOPIC_KEYWORDS_JP)
        or any(kw in lower for kw in _OFF_TOPIC_KEYWORDS_EN)
    )


def parse_location_reply(text: str) -> Optional[str]:
    """Try to extract a city from a short follow-up reply like '東京' or 'Osaka'."""
    return _normalise_city(text)


def is_location_only_reply(text: str) -> bool:
    """Return True only for a short clarification answer that is just a location.

    This intentionally rejects off-topic requests that merely contain a city name,
    such as "東京のおすすめレストランは？".
    """
    city = _normalise_city(text)
    if city is None:
        return False

    normalized = text.strip().lower()
    for alias in sorted(_CITY_ALIASES, key=len, reverse=True):
        normalized = normalized.replace(alias.lower(), "")

    for filler in (
        "です",
        "で",
        "を",
        "は",
        "なら",
        "お願いします",
        "おねがいします",
        "ください",
        "頼む",
        "たのむ",
    ):
        normalized = normalized.replace(filler, "")

    normalized = re.sub(r"[\s\u3000、。,.!?！？ー\-]+", "", normalized)
    return normalized == ""


# ── Providers ─────────────────────────────────────────────────────────────────


def get_mock_forecast(request: WeatherRequest) -> ForecastResponse:
    """Return deterministic demo forecast data."""
    city = request.location or "東京"
    city_data = _MOCK_FORECASTS.get(city, _MOCK_FORECASTS["東京"])
    day_data = city_data.get(request.day, city_data["_default"])
    return ForecastResponse(
        location=city,
        day=request.day,
        summary=day_data["summary"],
        temperature_c=day_data.get("temp_c"),
        precipitation_chance=day_data.get("precip"),
        is_demo_data=True,
        source="demo",
    )


async def get_jma_forecast(request: WeatherRequest, timeout: float = 5.0) -> Optional[ForecastResponse]:
    """Skeleton JMA provider — falls back to None (caller uses mock) on any error.

    A real implementation would call the JMA Open Data API here.
    https://www.jma.go.jp/bosai/forecast/
    """
    try:
        # Guard the optional import; aiohttp is in requirements.txt but may be absent locally.
        import aiohttp  # noqa: PLC0415

        # Placeholder: JMA API is free but requires city-code lookup.
        # This is intentionally unimplemented in v1; raise to trigger fallback.
        raise NotImplementedError("JMA provider is not yet implemented")

    except NotImplementedError:
        logger.info("JMA provider not implemented; falling back to mock data")
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("JMA provider error (will use mock): %s", exc)
        return None


async def get_forecast(request: WeatherRequest, provider: str = "mock") -> ForecastResponse:
    """Dispatch to the requested provider; always returns a ForecastResponse."""
    if provider == "jma":
        result = await get_jma_forecast(request)
        if result is not None:
            return result
        # Fallback
    return get_mock_forecast(request)
