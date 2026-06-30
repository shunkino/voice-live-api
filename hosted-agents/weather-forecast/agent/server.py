"""Foundry Hosted Agent entry point using InvocationAgentServerHost.

This module is the container entry point for Microsoft Foundry deployments.
It uses the official `azure-ai-agentserver-invocations` SDK so the platform
can detect the invocations_ws protocol and route WebSocket connections
correctly.

The weather handling logic lives in app.py / protocol.py / weather.py and is
reused here unchanged.

Local testing still uses `app.py` (FastAPI + TestClient).
Foundry deployment uses this file (InvocationAgentServerHost, port 8088).
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.websockets import WebSocket
from azure.ai.agentserver.invocations import InvocationAgentServerHost

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
    get_forecast,
    is_weather_request,
    is_location_only_reply,
    parse_location_reply,
    parse_weather_request,
)
from .app import _handle_weather_request  # reuse the shared logic
from .llm import maybe_build_responder

logger = logging.getLogger(__name__)

cfg = Settings.from_env()
sessions = default_store

# Optional LLM responder (RESPONSE_MODE=llm); None keeps template behavior.
responder = maybe_build_responder(cfg)

missing = cfg.report_missing_foundry()
if missing:
    logger.warning(missing)

app = InvocationAgentServerHost()


def _extract_user_text(body: bytes) -> str:
    """Extract the user's text from a Voice Live / Invocations request body.

    Voice Live sends ``{"type": "input_audio.transcription", "input": "..."}``.
    We also accept ``{"message": "..."}``, ``{"text": "..."}`` and plain-text
    bodies so the same endpoint works from the Foundry portal chat UI.
    """
    if not body:
        return ""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return body.decode("utf-8", errors="replace").strip()
    if isinstance(data, dict):
        for key in ("input", "message", "text"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""
    if isinstance(data, str):
        return data.strip()
    return ""


@app.invoke_handler
async def handle_invoke(request: Request):
    """Voice Live-compatible Invocations (HTTP/SSE) handler.

    Accepts a transcribed user utterance, runs the shared weather logic, and
    streams the spoken reply back as ``output_audio_transcription`` SSE events
    so the Voice Live service can synthesize speech. Requires the agent to be
    deployed with the ``invocations`` protocol and the version metadata
    ``voiceLiveCompatible: "true"``.
    """
    body = await request.body()
    user_text = _extract_user_text(body)
    if not user_text:
        return JSONResponse(
            status_code=400,
            content={
                "error": "invalid_request",
                "message": (
                    'Request body must contain user text, e.g. '
                    '{"type": "input_audio.transcription", "input": "今日の東京の天気は？"}'
                ),
            },
        )

    session_id = getattr(request.state, "session_id", "") or str(uuid.uuid4())
    session = sessions.get_or_create(session_id)

    response_json = await _handle_weather_request(
        user_text, session, cfg.weather_provider, responder
    )
    try:
        spoken = json.loads(response_json).get("text", "")
    except (json.JSONDecodeError, AttributeError):
        spoken = ""

    async def event_generator():
        if spoken:
            yield (
                "data: "
                + json.dumps(
                    {"type": "output_audio_transcription.delta", "delta": spoken},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        yield (
            "data: "
            + json.dumps(
                {"type": "output_audio_transcription.done", "text": spoken},
                ensure_ascii=False,
            )
            + "\n\n"
        )
        yield "data: " + json.dumps({"type": "done"}) + "\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.ws_handler
async def handle_ws(ws: WebSocket) -> None:
    """Handle invocations_ws connections from the Foundry gateway.

    The SDK has already called ws.accept() before invoking this handler.
    Auth is validated by the Foundry gateway before the connection reaches us.
    """
    session_id = ws.query_params.get("agent_session_id", "") or str(uuid.uuid4())
    session = sessions.get_or_create(session_id)
    logger.info("Session %s connected", session_id)

    try:
        while True:
            message: dict[str, Any] = await ws.receive()

            if "bytes" in message and message["bytes"] is not None:
                raw_bytes: bytes = message["bytes"]
                if len(raw_bytes) > MAX_FRAME_BYTES:
                    await ws.send_text(oversized_frame_error(len(raw_bytes)).to_json())
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
                response_json = await _handle_weather_request(
                    req_msg.text, session, cfg.weather_provider, responder
                )
                await ws.send_text(response_json)

            elif msg_type in ("", None):
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
                            ClarificationMessage(text="どの地域の天気を知りたいですか？").to_json()
                        )
                else:
                    await ws.send_text(unsupported_type_error(msg_type or None).to_json())

            elif msg_type == "location.reply":
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

    except Exception as exc:
        if "disconnect" in type(exc).__name__.lower() or "websocket" in type(exc).__name__.lower():
            logger.info("Session %s disconnected", session_id)
        else:
            logger.exception("Unhandled error in session %s: %s", session_id, exc)
            try:
                await ws.send_text(ErrorMessage(message="Internal server error").to_json())
            except Exception:
                pass


def main() -> None:
    # Bind host from config, but let the InvocationAgentServerHost resolve the
    # port from the platform-injected ``PORT`` env var (falling back to 8088).
    # Passing an explicit port here would override the Foundry-provided ``PORT``
    # and make the container unreachable behind the hosted gateway.
    app.run(host=cfg.host)


if __name__ == "__main__":
    main()
