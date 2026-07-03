"""LLM-backed response generation with a ``get_weather`` tool (function calling).

In ``RESPONSE_MODE=llm`` the agent uses a Foundry chat model (via the Responses
API) instead of the rule-based template path. The model is given a single
``get_weather`` tool; it decides when to call it, we execute the tool against the
configured weather provider (``live`` Open-Meteo data by default, or ``mock``),
feed the result back, and the model composes a natural spoken reply in the
user's language (Japanese is prioritized).

The agent stays voice-friendly: the final text is what Voice Live speaks. On any
failure (missing config, model/tool error) ``respond()`` returns ``None`` so the
caller can fall back to the deterministic template path.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional

from .protocol import WeatherResponseMessage
from .weather import (
    WeatherRequest,
    detect_language,
    get_forecast,
    is_weather_request,
    _normalise_city,
    _normalise_day,
)

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 4

_SYSTEM_PROMPT = (
    "あなたは親切な天気予報アシスタントです。"
    "ユーザーと同じ言語で、簡潔で自然な話し言葉で応答してください（日本語を優先）。"
    "天気に関する質問には必ず get_weather ツールを使って予報を取得し、その結果だけに基づいて答えてください。"
    "ツール結果には最高気温(temperature_max_c)・最低気温(temperature_min_c)・現在の気温(temperature_current_c、今日のみ)・"
    "降水確率(precipitation_chance)が含まれます。ユーザーの質問に合う情報を選んで自然に伝えてください"
    "（例:「今何度?」には現在の気温）。値が無い(null)項目は述べないでください。"
    "地名が不明な場合は、どの地域か一度だけ聞き返してください。"
    "天気以外の質問には丁寧にお断りし、天気についてお尋ねくださいと促してください。"
    "音声で読み上げられるため、1〜2文の短い文章にし、箇条書きや記号は使わないでください。\n\n"
    "You are a helpful weather assistant. Reply in the user's language "
    "(prefer Japanese), concise and natural for speech. For weather questions, "
    "always call the get_weather tool and answer only from its result. The result "
    "includes temperature_max_c, temperature_min_c, temperature_current_c (today "
    "only), and precipitation_chance — pick what fits the question (e.g. 'what's it "
    "now?' -> current temperature) and don't mention null fields. If the "
    "location is unknown, ask once which city. Politely decline non-weather "
    "questions and invite a weather question instead. Keep replies to 1-2 short "
    "spoken sentences with no lists or markup."
)

# Appended to the instructions for Japanese turns when hiragana_output is on.
# The TTS engine mispronounces kanji because their readings are context-
# dependent; forcing hiragana makes the reading unambiguous.
_HIRAGANA_DIRECTIVE = (
    "\n\n【重要・日本語の表記ルール】日本語で答えるときは、返答の文をすべてひらがなで"
    "書いてください。漢字やカタカナは使わないでください。これは音声読み上げ（TTS）が"
    "漢字の読みをまちがえるのを防ぐためです。たとえば「東京の天気は晴れです」ではなく"
    "「とうきょうのてんきははれです」と書きます。気温などの数字は算用数字のままで"
    "かまいません（例:「25ど」）。"
)

_GET_WEATHER_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "get_weather",
    "description": (
        "Get the weather forecast for a city and day. Use for any weather "
        "question, including follow-ups that omit the city/day."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "location": {
                "type": "string",
                "description": "City name in Japanese or English, e.g. 東京 or Tokyo.",
            },
            "day": {
                "type": "string",
                "description": (
                    "Day to forecast: 今日/明日/明後日/週末 or "
                    "today/tomorrow/day after tomorrow/weekend."
                ),
            },
        },
        "required": ["location"],
    },
}


class LLMResponder:
    """Generate weather replies with a Foundry chat model + get_weather tool."""

    def __init__(
        self,
        project_endpoint: str,
        model_deployment: str,
        provider: str = "live",
        hiragana_output: bool = True,
    ) -> None:
        self._endpoint = project_endpoint
        self._model = model_deployment
        self._provider = provider
        self._hiragana_output = hiragana_output
        self._responses = None  # lazily created OpenAI Responses client

    def _get_responses_client(self):
        if self._responses is None:
            from azure.ai.projects import AIProjectClient
            from azure.identity import DefaultAzureCredential

            project = AIProjectClient(
                endpoint=self._endpoint,
                credential=DefaultAzureCredential(),
            )
            self._responses = project.get_openai_client().responses
        return self._responses

    def _instructions(self, session: Any, language: str) -> str:
        ctx_parts = []
        if getattr(session, "latest_location", None):
            ctx_parts.append(f"last location={session.latest_location}")
        if getattr(session, "latest_day", None):
            ctx_parts.append(f"last day={session.latest_day}")
        context = (
            "\n\nKnown session context (use for follow-ups that omit details): "
            + ", ".join(ctx_parts)
            if ctx_parts
            else ""
        )
        lang_hint = (
            f"\n\nThe user's latest message is in {'Japanese' if language == 'ja' else 'English'}; "
            "respond in that language."
        )
        hiragana = (
            _HIRAGANA_DIRECTIVE
            if language == "ja" and self._hiragana_output
            else ""
        )
        return _SYSTEM_PROMPT + context + lang_hint + hiragana

    async def _run_get_weather(
        self, args: dict[str, Any], language: str, session: Any
    ) -> dict[str, Any]:
        """Execute the get_weather tool against the configured provider."""
        raw_location = (args.get("location") or "").strip()
        raw_day = (args.get("day") or "").strip()

        city = _normalise_city(raw_location) or getattr(session, "latest_location", None)
        day = _normalise_day(raw_day) if raw_day else (getattr(session, "latest_day", None) or "今日")

        request = WeatherRequest(location=city or raw_location or None, day=day, language=language)
        forecast = await get_forecast(request, provider=self._provider)

        # Persist resolved context for follow-up turns.
        if city:
            session.update_location(city)
        session.update_day(day)

        return {
            "location": forecast.location,
            "day": forecast.day,
            "summary": forecast.summary,
            "temperature_max_c": forecast.temperature_c,
            "temperature_min_c": forecast.temperature_min_c,
            "temperature_current_c": forecast.temperature_current_c,
            "precipitation_chance": forecast.precipitation_chance,
            "is_demo_data": forecast.is_demo_data,
            "source": forecast.source,
        }

    async def respond(self, text: str, session: Any) -> Optional[str]:
        """Return a WeatherResponseMessage JSON string, or None on failure."""
        if not self._endpoint:
            logger.warning("LLM mode requested but no project endpoint configured")
            return None

        language = detect_language(text)
        try:
            client = self._get_responses_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not create Foundry Responses client: %s", exc)
            return None

        instructions = self._instructions(session, language)
        input_items: list[dict[str, Any]] = [{"role": "user", "content": text}]

        # Weather questions MUST be grounded on the tool. Forcing tool_choice on
        # the first turn stops a small model from answering weather questions
        # from its own (hallucinated) knowledge. Non-weather turns stay "auto"
        # so the model can decline without calling the tool.
        force_tool = is_weather_request(text)

        last_location: Optional[str] = None
        last_day: Optional[str] = None
        last_demo = self._provider != "live"
        tool_used = False

        try:
            for _ in range(_MAX_TOOL_ROUNDS):
                if force_tool and not tool_used:
                    tool_choice: Any = {"type": "function", "name": "get_weather"}
                else:
                    tool_choice = "auto"
                response = await asyncio.to_thread(
                    client.create,
                    model=self._model,
                    instructions=instructions,
                    input=input_items,
                    tools=[_GET_WEATHER_TOOL],
                    tool_choice=tool_choice,
                    store=False,
                )

                tool_calls = [
                    item for item in response.output if getattr(item, "type", "") == "function_call"
                ]
                if not tool_calls:
                    final_text = (response.output_text or "").strip()
                    if not final_text:
                        return None
                    return WeatherResponseMessage(
                        text=final_text,
                        location=last_location or getattr(session, "latest_location", "") or "",
                        day=last_day or getattr(session, "latest_day", "") or "",
                        demo_data=last_demo,
                    ).to_json()

                for call in tool_calls:
                    tool_used = True
                    try:
                        args = json.loads(call.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    result = await self._run_get_weather(args, language, session)
                    last_location = result.get("location") or last_location
                    last_day = result.get("day") or last_day
                    last_demo = bool(result.get("is_demo_data", last_demo))
                    logger.info(
                        "get_weather tool: location=%s day=%s -> source=%s "
                        "max=%s min=%s current=%s precip=%s",
                        result.get("location"),
                        result.get("day"),
                        result.get("source"),
                        result.get("temperature_max_c"),
                        result.get("temperature_min_c"),
                        result.get("temperature_current_c"),
                        result.get("precipitation_chance"),
                    )

                    input_items.append(
                        {
                            "type": "function_call",
                            "call_id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                    )
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.call_id,
                            "output": json.dumps(result, ensure_ascii=False),
                        }
                    )

            logger.warning("LLM tool loop exceeded %d rounds", _MAX_TOOL_ROUNDS)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM responder error (falling back to template): %s", exc)
            return None


def maybe_build_responder(cfg: Any) -> Optional["LLMResponder"]:
    """Build an LLMResponder when RESPONSE_MODE=llm and config is present.

    Returns ``None`` for template mode or when the project endpoint is missing,
    so callers transparently keep the deterministic template behavior.
    """
    if getattr(cfg, "response_mode", "template") != "llm":
        return None
    endpoint = getattr(cfg, "project_endpoint", "")
    if not endpoint:
        logger.warning(
            "RESPONSE_MODE=llm but no project endpoint "
            "(FOUNDRY_PROJECT_ENDPOINT / AZURE_AI_PROJECT_ENDPOINT); using template mode"
        )
        return None
    return LLMResponder(
        project_endpoint=endpoint,
        model_deployment=getattr(cfg, "llm_model_deployment", "gpt-4.1-mini"),
        provider=getattr(cfg, "weather_provider", "live"),
        hiragana_output=getattr(cfg, "hiragana_output", True),
    )
