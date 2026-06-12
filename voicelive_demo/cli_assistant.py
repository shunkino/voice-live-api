# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""CLI front-end for the integrated Voice Live experiments.

Mic + speaker conversation through the terminal. Exercises feature #1
(custom voice) and feature #3 (transcription model, incl. MAI-Transcribe-1),
and prints the live input transcription so you can judge recognition quality.

The avatar (feature #2) needs a display surface, so it runs in the web
front-end instead — but it is configured through the *same* ExperimentConfig.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Union

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import ServerEventType
from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential

from .audio import AudioProcessor
from .config import ExperimentConfig
from .session_factory import build_session

logger = logging.getLogger(__name__)


class CliVoiceAssistant:
    """Terminal voice assistant driven by an :class:`ExperimentConfig`."""

    def __init__(
        self,
        config: ExperimentConfig,
        credential: Union[AzureKeyCredential, AsyncTokenCredential],
    ):
        self.config = config
        self.credential = credential
        self.connection = None
        self.audio_processor: Optional[AudioProcessor] = None
        self.session_ready = False
        self._input_transcript = ""

    async def start(self) -> None:
        if self.config.avatar.enabled:
            print(
                "ℹ️  Avatar is enabled but the CLI has no display surface. "
                "Run in web mode (--mode web) to see the avatar. "
                "Continuing with audio only.\n"
            )

        logger.info("Connecting to Voice Live (%s)", self.config.summary())
        try:
            async with connect(
                endpoint=self.config.endpoint,
                credential=self.credential,
                model=self.config.model,
            ) as connection:
                self.connection = connection
                self.audio_processor = AudioProcessor(connection)

                await self._setup_session()
                self.audio_processor.start_playback()

                print("\n" + "=" * 60)
                print("🎤 VOICE LIVE EXPERIMENTS — CLI")
                print(f"   {self.config.summary()}")
                print("   Start speaking. Press Ctrl+C to exit.")
                print("=" * 60 + "\n")

                await self._process_events()
        finally:
            if self.audio_processor:
                self.audio_processor.shutdown()

    async def _setup_session(self) -> None:
        session = build_session(self.config)
        assert self.connection is not None
        await self.connection.session.update(session=session)
        logger.info("Session configuration sent")

    async def _process_events(self) -> None:
        assert self.connection is not None
        async for event in self.connection:
            await self._handle_event(event)

    async def _handle_event(self, event) -> None:
        ap = self.audio_processor
        assert ap is not None

        etype = event.type
        if etype == ServerEventType.SESSION_UPDATED:
            logger.info("Session ready: %s", event.session.id)
            self.session_ready = True
            ap.start_capture()

        elif etype == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            print("🎤 Listening...")
            ap.skip_pending_audio()

        elif etype == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            print("🤔 Processing...")

        # ---- Feature #3: surface the transcription so models can be compared ----
        elif etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_DELTA:
            delta = getattr(event, "delta", "") or ""
            self._input_transcript += delta
            print(f"\r📝 [{self.config.transcription_model}] {self._input_transcript}",
                  end="", flush=True)

        elif etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            transcript = getattr(event, "transcript", "") or self._input_transcript
            print(f"\r📝 [{self.config.transcription_model}] you said: {transcript}")
            self._input_transcript = ""

        elif etype == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_FAILED:
            err = getattr(event, "error", None)
            msg = getattr(err, "message", err)
            print(f"\n⚠️  Transcription failed: {msg}")
            self._input_transcript = ""

        # ---- Assistant audio out ----
        elif etype == ServerEventType.RESPONSE_AUDIO_DELTA:
            ap.queue_audio(event.delta)

        elif etype == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            transcript = getattr(event, "transcript", "")
            if transcript:
                print(f"🤖 {transcript}")

        elif etype == ServerEventType.RESPONSE_AUDIO_DONE:
            print("🎤 Ready for next input...")

        elif etype == ServerEventType.ERROR:
            msg = event.error.message
            if "Cancellation failed: no active response" in msg:
                logger.debug("Benign cancellation error: %s", msg)
            else:
                logger.error("Voice Live error: %s", msg)
                print(f"\n❌ Error: {msg}")
        else:
            logger.debug("Unhandled event type: %s", etype)
