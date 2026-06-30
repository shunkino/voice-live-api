"""Hosted-agent WebSocket endpoint: /invocations_ws

Prefers `azure-ai-agentserver-invocations` when available; falls back to a
FastAPI/uvicorn implementation so local tests and the quickstart work without
the preview SDK installed.

Routes:
  GET /invocations_ws  — WebSocket upgrade (text + binary frames)
  GET /health          — simple liveness probe
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .config import Settings
from .protocol import (
    ClarificationMessage,
    ErrorMessage,
    MAX_FRAME_BYTES,
    RedirectMessage,
    WeatherRequestMessage,
    WeatherResponseMessage,
    check_frame_size,
    parse_text_frame,
    unsupported_type_error,
    oversized_frame_error,
)
from .session_state import SessionStore, default_store
from .weather import (
    ForecastResponse,
    WeatherRequest,
    detect_language,
    get_forecast,
    is_weather_request,
    is_location_only_reply,
    parse_location_reply,
    parse_weather_request,
)

logger = logging.getLogger(__name__)


def _redirect_text(language: str) -> str:
    """Polite non-weather decline in the user's language (FR-007)."""
    if language == "en":
        return (
            "Sorry, I can only help with weather forecasts. "
            "Please feel free to ask about the weather."
        )
    return (
        "申し訳ありませんが、天気予報に関するご質問のみお答えできます。"
        "天気についてお気軽にお尋ねください。"
    )


def _clarify_text(language: str) -> str:
    """Location clarification prompt in the user's language."""
    if language == "en":
        return "Which city's weather would you like to know?"
    return "どの地域の天気を知りたいですか？"

# Query parameter names that indicate a bearer token — reject them all.
# Foundry validates auth before proxying; the container must not accept tokens.
_AUTH_PARAM_NAMES = frozenset({
    "authorization", "access_token", "access-token", "token", "auth",
})


def _has_auth_query_param(ws: WebSocket) -> bool:
    """Return True if any query param name looks like an auth token carrier."""
    for key in ws.query_params.keys():
        normalized = key.lower().replace("-", "_")
        if normalized in _AUTH_PARAM_NAMES or "token" in normalized or "auth" in normalized:
            return True
    return False

# ── Application factory ───────────────────────────────────────────────────────


def create_app(settings: Settings | None = None, store: SessionStore | None = None) -> FastAPI:
    cfg = settings or Settings.from_env()
    sessions = store or default_store

    # Optional LLM responder (RESPONSE_MODE=llm); None keeps template behavior.
    from .llm import maybe_build_responder

    responder = maybe_build_responder(cfg)

    # Startup: surface missing config
    missing_msg = cfg.report_missing_foundry()
    if missing_msg:
        logger.warning(missing_msg)

    app = FastAPI(title="Weather Forecast Hosted Agent")

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "provider": cfg.weather_provider})

    # Foundry Hosted Agent platform health probe — must return 200 when ready.
    @app.get("/readiness")
    async def readiness() -> JSONResponse:
        return JSONResponse({"status": "ready"})

    @app.websocket("/invocations_ws")
    async def invocations_ws(ws: WebSocket) -> None:
        # Reject any query param that looks like a bearer token.
        # Foundry validates Entra auth before proxying; containers must not accept tokens.
        if _has_auth_query_param(ws):
            await ws.close(code=1008)
            return

        await ws.accept()
        session_id = ws.query_params.get("agent_session_id", "")
        if not session_id:
            # Generate a local session id for non-hosted use
            import uuid
            session_id = str(uuid.uuid4())

        session = sessions.get_or_create(session_id)
        logger.info("Session %s connected", session_id)

        try:
            while True:
                # Receive next frame (text or bytes)
                message: dict[str, Any] = await ws.receive()

                if "bytes" in message and message["bytes"] is not None:
                    raw_bytes: bytes = message["bytes"]
                    if len(raw_bytes) > MAX_FRAME_BYTES:
                        await ws.send_text(oversized_frame_error(len(raw_bytes)).to_json())
                    # Binary (audio) frames are acknowledged silently — no audio storage.
                    continue

                raw_text: str = message.get("text", "")
                if not raw_text:
                    continue

                try:
                    check_frame_size(raw_text)
                except ValueError:
                    await ws.send_text(oversized_frame_error(len(raw_text.encode())).to_json())
                    continue

                try:
                    data = parse_text_frame(raw_text)
                except ValueError as exc:
                    await ws.send_text(ErrorMessage(message=str(exc)).to_json())
                    continue

                msg_type = data.get("type", "")

                if msg_type == "weather.request":
                    req_msg = WeatherRequestMessage.from_dict(data)
                    # Session is bound at connection time; ignore frame-level
                    # session_id to prevent mid-connection session hijacking.

                    response_json = await _handle_weather_request(
                        req_msg.text, session, cfg.weather_provider, responder
                    )
                    await ws.send_text(response_json)

                elif msg_type in ("", None):
                    # Could be a follow-up location reply (plain text or typed)
                    # Check if session has a pending request
                    if session.pending_weather_text:
                        location = parse_location_reply(data.get("text", raw_text))
                        if location:
                            session.update_location(location)
                            pending_text = session.pending_weather_text
                            session.clear_pending_request()
                            response_json = await _handle_weather_request(
                                pending_text or location, session, cfg.weather_provider, responder
                            )
                            await ws.send_text(response_json)
                        else:
                            await ws.send_text(
                                ClarificationMessage(
                                    text=_clarify_text(
                                        detect_language(data.get("text", raw_text))
                                    )
                                ).to_json()
                            )
                    else:
                        await ws.send_text(unsupported_type_error(msg_type or None).to_json())

                elif msg_type == "location.reply":
                    # Explicit location follow-up type
                    location = parse_location_reply(data.get("text", ""))
                    if location and session.pending_weather_text is not None:
                        session.update_location(location)
                        pending = session.pending_weather_text
                        session.clear_pending_request()
                        response_json = await _handle_weather_request(
                            pending or location, session, cfg.weather_provider, responder
                        )
                        await ws.send_text(response_json)
                    else:
                        await ws.send_text(unsupported_type_error(msg_type).to_json())

                else:
                    await ws.send_text(unsupported_type_error(msg_type).to_json())

        except WebSocketDisconnect:
            logger.info("Session %s disconnected", session_id)
        except Exception as exc:
            logger.exception("Unhandled error in session %s: %s", session_id, exc)
            try:
                await ws.send_text(ErrorMessage(message="Internal server error").to_json())
            except Exception:
                pass

    return app


async def _handle_weather_request(
    text: str, session: Any, provider: str, responder: Any = None
) -> str:
    """Parse text into a weather request, update session state, return JSON response.

    When *responder* (an :class:`agent.llm.LLMResponder`) is provided, the LLM
    path is tried first; on any failure it transparently falls back to the
    deterministic template logic below.
    """
    if responder is not None:
        llm_out = await responder.respond(text, session)
        if llm_out is not None:
            return llm_out
        # Fall through to the template path on LLM failure.

    language = detect_language(text)

    if session.pending_weather_text:
        mentioned_location = parse_location_reply(text)
        location = mentioned_location if is_location_only_reply(text) else None
        if location:
            session.update_location(location)
            text = session.pending_weather_text
        elif mentioned_location and not is_weather_request(text):
            return RedirectMessage(text=_redirect_text(language)).to_json()
        elif not is_weather_request(text):
            return ClarificationMessage(text=_clarify_text(language)).to_json()

    # FR-007: politely decline non-weather requests before any location parsing.
    if not is_weather_request(text):
        return RedirectMessage(text=_redirect_text(language)).to_json()

    req = parse_weather_request(
        text,
        session_location=session.latest_location,
        session_day=session.latest_day,
    )
    session.increment_turn()

    if req.needs_clarification:
        session.set_pending_request(text)
        return ClarificationMessage(text=req.clarification_prompt).to_json()

    # Location resolved — update session
    if session.pending_weather_text:
        session.clear_pending_request()
    if req.location:
        session.update_location(req.location)
    session.update_day(req.day)

    forecast: ForecastResponse = await get_forecast(req, provider=provider)
    spoken = forecast.to_spoken_text()

    return WeatherResponseMessage(
        text=spoken,
        location=forecast.location,
        day=forecast.day,
        demo_data=forecast.is_demo_data,
    ).to_json()


# ── Entry point ───────────────────────────────────────────────────────────────

# Module-level app instance for uvicorn / hosted-agent runner
_settings = Settings.from_env()
app = create_app(settings=_settings)


def main() -> None:
    """Run the agent locally with uvicorn."""
    import uvicorn

    uvicorn.run(
        "agent.app:app",
        host=_settings.host,
        port=_settings.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
