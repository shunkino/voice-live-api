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

# Japanese display name → English display name (for bilingual output).
_CITY_EN: dict[str, str] = {
    "東京": "Tokyo",
    "大阪": "Osaka",
    "京都": "Kyoto",
    "札幌": "Sapporo",
    "福岡": "Fukuoka",
}

# Japanese day → English day phrase (used in spoken English output).
_DAY_EN: dict[str, str] = {
    "今日": "today",
    "明日": "tomorrow",
    "明後日": "the day after tomorrow",
    "週末": "this weekend",
}

# Mock summary phrase (Japanese) → English equivalent for bilingual output.
_SUMMARY_EN: dict[str, str] = {
    "快晴": "clear",
    "晴れ": "sunny",
    "晴れ時々くもり": "sunny with occasional clouds",
    "晴れのち曇り": "sunny then cloudy",
    "晴れのち雨": "sunny then rainy",
    "くもり": "cloudy",
    "曇り": "cloudy",
    "薄くもり": "partly cloudy",
    "くもり一時雨": "cloudy with brief rain",
    "くもり時々雨": "cloudy with occasional rain",
    "雨": "rainy",
    "おおむね晴れ": "mostly sunny",
    "おおむね曇り": "mostly cloudy",
}


def detect_language(text: str) -> str:
    """Detect the language of *text*: ``"ja"`` or ``"en"`` (Japanese has priority).

    Returns ``"ja"`` if any Japanese character (hiragana/katakana/kanji) is
    present, ``"en"`` if the text is Latin-script only, and ``"ja"`` as the
    default when neither is detectable (Japanese is the priority language).
    """
    for ch in text:
        if (
            "\u3040" <= ch <= "\u30ff"  # hiragana + katakana
            or "\u4e00" <= ch <= "\u9fff"  # CJK unified ideographs
        ):
            return "ja"
    if any("a" <= ch.lower() <= "z" for ch in text):
        return "en"
    return "ja"


def wait_filler_text(language: str) -> str:
    """A short, voice-friendly 'please wait' phrase for slow tool calls."""
    return "One moment, please." if language == "en" else "少々お待ちください。"

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
    language: str = "ja"

    def to_spoken_text(self) -> str:
        """Return a natural sentence (Japanese or English) for voice output."""
        if self.language == "en":
            temp_part = (
                f" The high will be around {self.temperature_c}°C."
                if self.temperature_c is not None
                else ""
            )
            return (
                f"The weather in {self.location} {self.day} is {self.summary}."
                f"{temp_part}"
            )
        temp_part = (
            f"最高気温は{self.temperature_c}度前後の見込みです。"
            if self.temperature_c is not None
            else ""
        )
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

    language = detect_language(text)
    clarification = (
        "Which city's weather would you like to know?"
        if language == "en"
        else "どの地域の天気を知りたいですか？"
    )

    if location is None:
        return WeatherRequest(
            location=None,
            day=day,
            language=language,
            needs_clarification=True,
            clarification_prompt=clarification,
        )

    return WeatherRequest(location=location, day=day, language=language)


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


# ── Live provider (Open-Meteo) data ───────────────────────────────────────────

# Curated coordinates for Japanese cities. Open-Meteo's geocoding is unreliable
# for Japanese script, so we resolve known JP cities locally for reliable
# Japanese support, and fall back to Open-Meteo geocoding for other (English)
# place names. Value: (latitude, longitude, English display name).
_JP_CITY_COORDS: dict[str, tuple[float, float, str]] = {
    "東京": (35.6895, 139.6917, "Tokyo"),
    "大阪": (34.6937, 135.5023, "Osaka"),
    "京都": (35.0116, 135.7681, "Kyoto"),
    "札幌": (43.0618, 141.3545, "Sapporo"),
    "福岡": (33.5904, 130.4017, "Fukuoka"),
    "名古屋": (35.1815, 136.9066, "Nagoya"),
    "横浜": (35.4437, 139.6380, "Yokohama"),
    "神戸": (34.6901, 135.1955, "Kobe"),
    "仙台": (38.2682, 140.8694, "Sendai"),
    "広島": (34.3853, 132.4553, "Hiroshima"),
    "那覇": (26.2124, 127.6809, "Naha"),
}

# WMO weather interpretation codes → (Japanese, English) summary.
# https://open-meteo.com/en/docs (WMO Weather interpretation codes)
_WMO_SUMMARY: dict[int, tuple[str, str]] = {
    0: ("快晴", "clear"),
    1: ("晴れ", "mostly sunny"),
    2: ("晴れ時々くもり", "partly cloudy"),
    3: ("くもり", "cloudy"),
    45: ("霧", "foggy"),
    48: ("霧", "foggy"),
    51: ("霧雨", "light drizzle"),
    53: ("霧雨", "drizzle"),
    55: ("霧雨", "heavy drizzle"),
    56: ("着氷性の霧雨", "freezing drizzle"),
    57: ("着氷性の霧雨", "freezing drizzle"),
    61: ("小雨", "light rain"),
    63: ("雨", "rain"),
    65: ("大雨", "heavy rain"),
    66: ("着氷性の雨", "freezing rain"),
    67: ("着氷性の雨", "freezing rain"),
    71: ("小雪", "light snow"),
    73: ("雪", "snow"),
    75: ("大雪", "heavy snow"),
    77: ("霧雪", "snow grains"),
    80: ("にわか雨", "rain showers"),
    81: ("にわか雨", "rain showers"),
    82: ("激しいにわか雨", "heavy rain showers"),
    85: ("にわか雪", "snow showers"),
    86: ("激しいにわか雪", "heavy snow showers"),
    95: ("雷雨", "thunderstorm"),
    96: ("雷雨（ひょうを伴う）", "thunderstorm with hail"),
    99: ("激しい雷雨（ひょうを伴う）", "severe thunderstorm with hail"),
}

_OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_OPEN_METEO_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def _wmo_summary(code: Optional[int], language: str) -> str:
    ja, en = _WMO_SUMMARY.get(int(code) if code is not None else -1, ("くもり", "cloudy"))
    return en if language == "en" else ja


def _day_index(day: str) -> int:
    """Map a normalized day to an offset within the daily forecast array."""
    import datetime as _dt

    if day == "明日":
        return 1
    if day == "明後日":
        return 2
    if day == "週末":
        # Offset to the next Saturday (0..6); today counts if it's already the weekend.
        weekday = _dt.date.today().weekday()  # Mon=0 .. Sun=6
        if weekday >= 5:
            return 0
        return 5 - weekday
    return 0  # 今日 / default


async def _resolve_coords(
    location: str, language: str, timeout: float
) -> Optional[tuple[float, float, str]]:
    """Resolve a location to (lat, lon, display_name).

    Known Japanese cities are resolved locally; everything else falls back to
    Open-Meteo geocoding (which works well for English/worldwide names).
    """
    if location in _JP_CITY_COORDS:
        lat, lon, en_name = _JP_CITY_COORDS[location]
        return (lat, lon, en_name if language == "en" else location)

    import aiohttp  # noqa: PLC0415

    params = {"name": location, "count": 1, "language": "en" if language == "en" else "ja"}
    async with aiohttp.ClientSession() as session:
        async with session.get(
            _OPEN_METEO_GEOCODE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
    results = data.get("results") or []
    if not results:
        return None
    top = results[0]
    return (top["latitude"], top["longitude"], top.get("name", location))


async def get_live_forecast(
    request: WeatherRequest, timeout: float = 6.0
) -> Optional[ForecastResponse]:
    """Fetch a live forecast from Open-Meteo. Returns ``None`` on any failure.

    Open-Meteo is free and keyless. Japanese cities are resolved via a curated
    coordinate table; other names use Open-Meteo geocoding.
    """
    if not request.location:
        return None
    try:
        import aiohttp  # noqa: PLC0415

        coords = await _resolve_coords(request.location, request.language, timeout)
        if coords is None:
            return None
        lat, lon, display_name = coords

        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": "weather_code,temperature_2m_max,precipitation_probability_max",
            "timezone": "auto",
            "forecast_days": 7,
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _OPEN_METEO_FORECAST_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        daily = data.get("daily") or {}
        codes = daily.get("weather_code") or []
        temps = daily.get("temperature_2m_max") or []
        precips = daily.get("precipitation_probability_max") or []
        idx = min(_day_index(request.day), len(codes) - 1) if codes else 0
        if not codes:
            return None

        summary = _wmo_summary(codes[idx], request.language)
        temp = round(temps[idx]) if idx < len(temps) and temps[idx] is not None else None
        precip = (
            int(precips[idx])
            if idx < len(precips) and precips[idx] is not None
            else None
        )
        day_display = (
            _DAY_EN.get(request.day, request.day)
            if request.language == "en"
            else request.day
        )
        return ForecastResponse(
            location=display_name,
            day=day_display,
            summary=summary,
            temperature_c=temp,
            precipitation_chance=precip,
            is_demo_data=False,
            source="open-meteo",
            language=request.language,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Open-Meteo provider error (will use mock): %s", exc)
        return None


# ── Providers ─────────────────────────────────────────────────────────────────


def get_mock_forecast(request: WeatherRequest) -> ForecastResponse:
    """Return deterministic demo forecast data (bilingual)."""
    city = request.location or "東京"
    city_data = _MOCK_FORECASTS.get(city, _MOCK_FORECASTS["東京"])
    day_data = city_data.get(request.day, city_data["_default"])

    if request.language == "en":
        location = _CITY_EN.get(city, city)
        day = _DAY_EN.get(request.day, request.day)
        summary = _SUMMARY_EN.get(day_data["summary"], day_data["summary"])
    else:
        location = city
        day = request.day
        summary = day_data["summary"]

    return ForecastResponse(
        location=location,
        day=day,
        summary=summary,
        temperature_c=day_data.get("temp_c"),
        precipitation_chance=day_data.get("precip"),
        is_demo_data=True,
        source="demo",
        language=request.language,
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
    """Dispatch to the requested provider; always returns a ForecastResponse.

    Providers:
      * ``mock`` — deterministic demo data (default).
      * ``live`` — live Open-Meteo data, with mock fallback on any error.
      * ``jma``  — skeleton (unimplemented); falls back to mock.
    """
    if provider == "live":
        result = await get_live_forecast(request)
        if result is not None:
            return result
        # Fallback to mock on any failure.
    elif provider == "jma":
        result = await get_jma_forecast(request)
        if result is not None:
            return result
        # Fallback
    return get_mock_forecast(request)
