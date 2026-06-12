# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""Web front-end for the integrated Voice Live experiments.

A small FastAPI app that:

* serves the single-page browser client (``static/index.html``), and
* bridges each browser over a WebSocket (``/ws``) to a Voice Live session.

The browser is a thin I/O surface:

* it captures the microphone and streams PCM16/24k up over ``/ws``;
* for the **avatar** (feature #2) it negotiates a WebRTC peer connection with
  Azure (the SDP offer/answer are relayed through this server) and plays the
  avatar's audio+video track directly;
* with the avatar off, the server forwards the model's audio deltas and the
  browser plays them through the Web Audio API.

All three features are configured server-side from the shared
:class:`~voicelive_demo.config.ExperimentConfig`, so the web client never needs
Azure credentials.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional, Union

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import (
    ClientEventSessionAvatarConnect,
    ServerEventType,
)
from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import ExperimentConfig
from ..session_factory import build_session

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    config: ExperimentConfig,
    credential_factory,
):
    """Build the FastAPI application.

    :param config: shared experiment configuration.
    :param credential_factory: zero-arg callable returning a fresh async
        credential (or :class:`AzureKeyCredential`) per browser session.
    """
    app = FastAPI(title="Voice Live Experiments")

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/config")
    async def client_config() -> JSONResponse:
        """Feature flags the browser needs to know about up front."""
        return JSONResponse(
            {
                "avatar": config.avatar.enabled,
                "avatarType": config.avatar.avatar_type,
                "transcriptionModel": config.transcription_model,
                "summary": config.summary(),
            }
        )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        bridge = VoiceLiveBridge(config, credential_factory, websocket)
        try:
            await bridge.run()
        except WebSocketDisconnect:
            logger.info("Browser disconnected")
        except Exception:  # pragma: no cover - defensive
            logger.exception("Bridge error")
        finally:
            await bridge.close()

    return app


class VoiceLiveBridge:
    """Relays one browser WebSocket to one Voice Live session."""

    def __init__(self, config: ExperimentConfig, credential_factory, websocket):
        self.config = config
        self.credential_factory = credential_factory
        self.ws = websocket
        self.connection = None
        self._credential: Optional[
            Union[AzureKeyCredential, AsyncTokenCredential]
        ] = None
        self._closed = False

    async def run(self) -> None:
        self._credential = self.credential_factory()
        async with connect(
            endpoint=self.config.endpoint,
            credential=self._credential,
            model=self.config.model,
        ) as connection:
            self.connection = connection
            await connection.session.update(session=build_session(self.config))
            logger.info("Voice Live session opened (%s)", self.config.summary())

            # Pump browser->service and service->browser concurrently. When
            # either side finishes (browser disconnect / stop, or the service
            # connection closing), cancel the other so the session is torn down
            # promptly instead of leaking until Azure's idle timeout.
            tasks = [
                asyncio.create_task(self._pump_browser_to_service()),
                asyncio.create_task(self._pump_service_to_browser()),
            ]
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                self._closed = True
                for task in tasks:
                    task.cancel()
                # Drain cancellations / surface any non-cancel exception.
                for task in tasks:
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

    # ------------------------------------------------------------------
    # browser -> service
    # ------------------------------------------------------------------
    async def _pump_browser_to_service(self) -> None:
        while not self._closed:
            raw = await self.ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            mtype = msg.get("type")
            if mtype == "input_audio":
                # base64 PCM16/24k chunk captured in the browser.
                await self.connection.input_audio_buffer.append(audio=msg["audio"])
            elif mtype == "avatar_offer":
                # Relay the browser's WebRTC SDP offer to Azure.
                await self.connection.send(
                    ClientEventSessionAvatarConnect(client_sdp=msg["sdp"])
                )
            elif mtype == "stop":
                self._closed = True
                break

    # ------------------------------------------------------------------
    # service -> browser
    # ------------------------------------------------------------------
    async def _pump_service_to_browser(self) -> None:
        assert self.connection is not None
        async for event in self.connection:
            await self._forward_event(event)

    async def _forward_event(self, event) -> None:
        etype = event.type

        if etype == ServerEventType.SESSION_UPDATED:
            ice_servers = _extract_ice_servers(event)
            await self._send(
                {
                    "type": "session_ready",
                    "avatar": self.config.avatar.enabled,
                    "iceServers": ice_servers,
                }
            )

        elif etype == ServerEventType.SESSION_AVATAR_CONNECTING:
            # Azure's SDP answer for the avatar peer connection.
            await self._send({"type": "avatar_answer", "sdp": event.server_sdp})

        elif etype == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            await self._send({"type": "speech_started"})

        elif etype == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            await self._send({"type": "speech_stopped"})

        # ---- Feature #3: transcription for side-by-side comparison ----
        elif etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_DELTA:
            await self._send(
                {
                    "type": "transcript_delta",
                    "role": "user",
                    "delta": getattr(event, "delta", "") or "",
                    "model": self.config.transcription_model,
                }
            )
        elif (
            etype
            == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED
        ):
            await self._send(
                {
                    "type": "transcript_final",
                    "role": "user",
                    "text": getattr(event, "transcript", "") or "",
                    "model": self.config.transcription_model,
                }
            )

        # ---- Assistant text ----
        elif etype == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            await self._send(
                {
                    "type": "transcript_delta",
                    "role": "assistant",
                    "delta": getattr(event, "delta", "") or "",
                }
            )
        elif etype == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            await self._send(
                {
                    "type": "transcript_final",
                    "role": "assistant",
                    "text": getattr(event, "transcript", "") or "",
                }
            )

        # ---- Assistant audio (only when avatar is OFF; with the avatar the
        #      audio is carried on the WebRTC track instead) ----
        elif etype == ServerEventType.RESPONSE_AUDIO_DELTA:
            if not self.config.avatar.enabled:
                await self._send(
                    {"type": "audio_delta", "audio": _delta_b64(event.delta)}
                )

        elif etype == ServerEventType.SESSION_AVATAR_SWITCH_TO_SPEAKING:
            await self._send({"type": "avatar_state", "state": "speaking"})
        elif etype == ServerEventType.SESSION_AVATAR_SWITCH_TO_IDLE:
            await self._send({"type": "avatar_state", "state": "idle"})

        elif etype == ServerEventType.ERROR:
            msg = getattr(event.error, "message", str(event.error))
            if "Cancellation failed: no active response" not in msg:
                await self._send({"type": "error", "message": msg})

    async def _send(self, payload: dict) -> None:
        if self._closed:
            return
        try:
            await self.ws.send_text(json.dumps(payload))
        except Exception:
            self._closed = True

    async def close(self) -> None:
        self._closed = True
        cred = self._credential
        if cred is not None and hasattr(cred, "close"):
            try:
                await cred.close()
            except Exception:  # pragma: no cover
                pass


def _extract_ice_servers(session_updated_event) -> list:
    """Pull avatar ICE servers out of a session.updated event, if present."""
    try:
        session = session_updated_event.session.as_dict()
    except Exception:  # pragma: no cover - defensive
        return []
    avatar = session.get("avatar") or {}
    servers = avatar.get("ice_servers") or []
    out = []
    for s in servers:
        entry = {"urls": s.get("urls")}
        if s.get("username"):
            entry["username"] = s["username"]
        if s.get("credential"):
            entry["credential"] = s["credential"]
        out.append(entry)
    return out


def _delta_b64(delta) -> str:
    """Audio deltas arrive base64-encoded (str) or as raw bytes."""
    if isinstance(delta, (bytes, bytearray)):
        import base64

        return base64.b64encode(bytes(delta)).decode("ascii")
    return delta
