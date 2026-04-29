# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""Voice Live API with Scenario State Machine control.

Uses a YAML-defined State Machine to control conversation flow,
rather than leaving the entire conversation to the LLM.
The model calls classify_and_act on every turn, the ScenarioEngine
evaluates transitions / safety / slots, and returns response_instructions
that guide the model's response.
"""
from __future__ import annotations

import os
import sys
import argparse
import asyncio
import json
import base64
from datetime import datetime
import logging
import queue
import signal
from typing import Union, Optional, Dict, Any, TYPE_CHECKING

from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import AzureCliCredential

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioNoiseReduction,
    AssistantMessageItem,
    AzureSemanticVadMultilingual,
    AzureStandardVoice,
    InputAudioFormat,
    InterimResponseTrigger,
    ItemType,
    Modality,
    OutputAudioFormat,
    OutputTextContentPart,
    RequestSession,
    ServerEventType,
    StaticInterimResponseConfig,
    FunctionTool,
    FunctionCallOutputItem,
    ResponseCreateParams,
    ToolChoiceLiteral,
    AudioInputTranscriptionOptions,
    Tool,
)
from dotenv import load_dotenv
import pyaudio

from scenario import ScenarioEngine, load_scenario

if TYPE_CHECKING:
    from azure.ai.voicelive.aio import VoiceLiveConnection

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

LEXICON_PATH = os.path.join(SCRIPT_DIR, "lexicon.xml")
LEXICON_URL = f"file://{LEXICON_PATH}"

DEFAULT_SCENARIO_PATH = os.path.join(
    SCRIPT_DIR, "scenario", "scenarios", "interview_practice.yaml"
)

DEFAULT_PHRASE_LIST_JA = [
    "Azure", "Microsoft", "Copilot", "面接", "練習",
    "行動面接", "技術面接", "ケース面接",
    "ジュニア", "ミドル", "シニア",
    "フィードバック", "スキップ",
]

# ---------------------------------------------------------------------------
# Environment & logging
# ---------------------------------------------------------------------------
load_dotenv("./.env", override=True)

if not os.path.exists("logs"):
    os.makedirs("logs")

timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
logging.basicConfig(
    filename=f"logs/{timestamp}_scenario.log",
    filemode="w",
    format="%(asctime)s:%(name)s:%(levelname)s:%(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(
    logging.Formatter("%(asctime)s:%(levelname)s:%(message)s")
)
logger.addHandler(_console_handler)


# ═══════════════════════════════════════════════════════════════════════════
# AudioProcessor  (reused from voice-live-function-call.py)
# ═══════════════════════════════════════════════════════════════════════════
class AudioProcessor:
    """Real-time PCM16 audio capture / playback (24 kHz mono, 50 ms chunks)."""

    loop: asyncio.AbstractEventLoop

    class AudioPlaybackPacket:
        def __init__(self, seq_num: int, data: Optional[bytes]):
            self.seq_num = seq_num
            self.data = data

    _BUILTIN_PATTERNS = ("MacBook", "Built-in", "Internal", "default")
    _VIRTUAL_PATTERNS = (
        "Teams", "Zoom", "Webex", "Meet", "Discord",
        "Virtual", "Aggregate", "Loopback", "BlackHole",
        "Soundflower", "VB-Audio", "CABLE",
    )

    def __init__(self, connection: "VoiceLiveConnection"):
        self.connection = connection
        self.audio = pyaudio.PyAudio()
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 24000
        self.chunk_size = 1200

        self.input_stream: Optional[pyaudio.Stream] = None
        self.playback_queue: queue.Queue[AudioProcessor.AudioPlaybackPacket] = (
            queue.Queue()
        )
        self.playback_base = 0
        self.next_seq_num = 0
        self.output_stream: Optional[pyaudio.Stream] = None
        self._input_device_index, self._output_device_index = self._select_devices()
        logger.info("AudioProcessor initialised")

    # -- device selection (prefer external / headphones) --------------------
    def _select_devices(self) -> tuple[Optional[int], Optional[int]]:
        p = self.audio
        input_candidates: list[tuple[int, str]] = []
        output_candidates: list[tuple[int, str]] = []
        for i in range(p.get_device_count()):
            info = p.get_device_info_by_index(i)
            name = str(info.get("name", ""))
            if int(info.get("maxInputChannels", 0) or 0) > 0:
                input_candidates.append((i, name))
            if int(info.get("maxOutputChannels", 0) or 0) > 0:
                output_candidates.append((i, name))

        def _is_builtin(n: str) -> bool:
            return any(pat in n for pat in self._BUILTIN_PATTERNS)

        def _is_virtual(n: str) -> bool:
            return any(pat.lower() in n.lower() for pat in self._VIRTUAL_PATTERNS)

        ext_in = [(i, n) for i, n in input_candidates if not _is_builtin(n) and not _is_virtual(n)]
        ext_out = [(i, n) for i, n in output_candidates if not _is_builtin(n) and not _is_virtual(n)]
        in_idx: Optional[int] = None
        out_idx: Optional[int] = None

        if ext_in and ext_out:
            for ii, iname in ext_in:
                for oi, oname in ext_out:
                    if iname == oname:
                        in_idx, out_idx = ii, oi
                        break
                if in_idx is not None:
                    break
            if in_idx is None:
                in_idx = ext_in[0][0]
            if out_idx is None:
                out_idx = ext_out[0][0]
        elif ext_in:
            in_idx = ext_in[0][0]
        elif ext_out:
            out_idx = ext_out[0][0]

        if in_idx is not None:
            print(f"🎧 Input: [{in_idx}] {p.get_device_info_by_index(in_idx).get('name')}")
        else:
            print("🎧 Input: system default")
        if out_idx is not None:
            print(f"🔊 Output: [{out_idx}] {p.get_device_info_by_index(out_idx).get('name')}")
        else:
            print("🔊 Output: system default")
        return in_idx, out_idx

    # -- capture ------------------------------------------------------------
    def start_capture(self):
        capture_count = 0

        def _capture_callback(in_data, _frame_count, _time_info, _status_flags):
            nonlocal capture_count
            capture_count += 1
            try:
                audio_base64 = base64.b64encode(in_data).decode("utf-8")
                future = asyncio.run_coroutine_threadsafe(
                    self.connection.input_audio_buffer.append(audio=audio_base64),
                    self.loop,
                )
                if capture_count % 200 == 0:
                    logger.info(
                        "Audio capture heartbeat: %d chunks sent, loop_running=%s",
                        capture_count,
                        self.loop.is_running(),
                    )
                # Check if any previous future failed (sample every 200)
                if capture_count % 200 == 1 and capture_count > 1:
                    try:
                        # Check the last future with a very short timeout
                        future.result(timeout=0.001)
                    except TimeoutError:
                        pass  # Still running, that's OK
                    except Exception as e:
                        logger.error("Audio send failed: %s", e)
            except Exception as e:
                logger.error("Capture callback error: %s", e)
            return (None, pyaudio.paContinue)

        if self.input_stream:
            return
        self.loop = asyncio.get_event_loop()
        logger.info("Capture: event loop id=%s, running=%s", id(self.loop), self.loop.is_running())
        open_kw: Dict[str, Any] = dict(
            format=self.format, channels=self.channels, rate=self.rate,
            input=True, frames_per_buffer=self.chunk_size,
            stream_callback=_capture_callback,
        )
        if self._input_device_index is not None:
            open_kw["input_device_index"] = self._input_device_index
        self.input_stream = self.audio.open(**open_kw)
        logger.info("Audio capture started")

    # -- playback -----------------------------------------------------------
    def start_playback(self):
        if self.output_stream:
            return
        remaining = bytes()

        def _playback_callback(_in_data, frame_count, _time_info, _status_flags):
            nonlocal remaining
            frame_count *= pyaudio.get_sample_size(pyaudio.paInt16)
            out = remaining[:frame_count]
            remaining_local = remaining[frame_count:]

            while len(out) < frame_count:
                try:
                    packet = self.playback_queue.get_nowait()
                except queue.Empty:
                    out += bytes(frame_count - len(out))
                    break
                if not packet or not packet.data:
                    break
                if packet.seq_num < self.playback_base:
                    continue
                need = frame_count - len(out)
                out += packet.data[:need]
                remaining_local = packet.data[need:]

            remaining = remaining_local
            return (out if len(out) >= frame_count else out, pyaudio.paContinue)

        open_kw: Dict[str, Any] = dict(
            format=self.format, channels=self.channels, rate=self.rate,
            output=True, frames_per_buffer=self.chunk_size,
            stream_callback=_playback_callback,
        )
        if self._output_device_index is not None:
            open_kw["output_device_index"] = self._output_device_index
        self.output_stream = self.audio.open(**open_kw)
        logger.info("Audio playback started")

    def _get_and_increase_seq_num(self):
        seq = self.next_seq_num
        self.next_seq_num += 1
        return seq

    def queue_audio(self, audio_data: Optional[bytes]) -> None:
        self.playback_queue.put(
            AudioProcessor.AudioPlaybackPacket(
                seq_num=self._get_and_increase_seq_num(), data=audio_data
            )
        )

    def skip_pending_audio(self):
        self.playback_base = self._get_and_increase_seq_num()

    def shutdown(self):
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None
        if self.output_stream:
            self.skip_pending_audio()
            self.queue_audio(None)
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None
        if self.audio:
            self.audio.terminate()
        logger.info("AudioProcessor shut down")


# ═══════════════════════════════════════════════════════════════════════════
# ScenarioClient — Voice Live + State Machine integration
# ═══════════════════════════════════════════════════════════════════════════
class ScenarioClient:
    """Voice assistant controlled by a YAML scenario State Machine."""

    def __init__(
        self,
        endpoint: str,
        credential: Union[AzureKeyCredential, AsyncTokenCredential],
        model: str,
        voice: str,
        scenario_path: str,
        temperature: float = 0.8,
        rate: str = "0.95",
        phrase_list: Optional[list[str]] = None,
        lexicon_url: Optional[str] = None,
    ):
        self.endpoint = endpoint
        self.credential = credential
        self.model = model
        self.voice = voice
        self.temperature = temperature
        self.rate = rate
        self.phrase_list = phrase_list
        self.lexicon_url = lexicon_url
        self.connection: Optional["VoiceLiveConnection"] = None
        self.audio_processor: Optional[AudioProcessor] = None
        self.session_ready = False
        self.conversation_started = False
        self._active_response = False
        self._response_api_done = False
        self._pending_function_call: Optional[Dict[str, Any]] = None

        # Load scenario & create engine
        self.scenario_config = load_scenario(scenario_path)
        self.engine = ScenarioEngine(self.scenario_config)
        logger.info(
            "Scenario loaded: %s (initial_state=%s)",
            self.scenario_config.name,
            self.scenario_config.initial_state,
        )

    # ------------------------------------------------------------------
    # classify_and_act tool definition
    # ------------------------------------------------------------------
    def _build_classify_tool(self) -> FunctionTool:
        """Build the classify_and_act FunctionTool from engine metadata."""
        all_intents = self.engine.get_all_intents()
        slot_props = self.engine.get_slot_properties()

        # Build intent descriptions for the enum
        intent_descriptions = []
        for intent_name in all_intents:
            idef = self.scenario_config.intents.get(intent_name)
            if idef:
                intent_descriptions.append(f"  - {intent_name}: {idef.description}")
        intent_desc_text = "\n".join(intent_descriptions)

        return FunctionTool(
            name="classify_and_act",
            description=(
                "ユーザーの発言を分析し、意図(intent)とスロット情報を抽出する。"
                "ユーザーが発言するたびに必ずこの関数を呼び出すこと。\n\n"
                "利用可能なintent:\n" + intent_desc_text
            ),
            parameters={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "認識したユーザーの意図",
                        "enum": all_intents,
                    },
                    "slots": {
                        "type": "object",
                        "description": "発言から抽出したスロット値。該当するものだけ含める。",
                        "properties": slot_props,
                    },
                    "user_summary": {
                        "type": "string",
                        "description": "ユーザーの発言内容の簡潔な要約（日本語）",
                    },
                },
                "required": ["intent", "user_summary"],
            },
        )

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        """Build the static system prompt from scenario global instructions."""
        return (
            self.scenario_config.global_instructions.strip()
            + "\n\n"
            + "【重要ルール】\n"
            + "- ユーザーが発言するたびに、必ず classify_and_act 関数を呼び出してください。\n"
            + "- classify_and_act の戻り値に含まれる応答指示に従って、自然な日本語で応答を生成してください。\n"
            + "- 応答指示をそのまま読み上げるのではなく、指示の意図を汲んで自然に話してください。\n"
            + "- 英語で話しかけられた場合は英語で応答してください。\n"
        )

    # ------------------------------------------------------------------
    # Session start
    # ------------------------------------------------------------------
    async def start(self):
        try:
            logger.info("Connecting to VoiceLive API with model %s", self.model)
            async with connect(
                endpoint=self.endpoint,
                credential=self.credential,
                model=self.model,
            ) as connection:
                self.connection = connection

                # --- Instrument append to verify sends actually execute ---
                _original_append = connection.input_audio_buffer.append
                _append_ok = [0]
                _append_fail = [0]

                async def _counted_append(**kwargs):
                    try:
                        await _original_append(**kwargs)
                        _append_ok[0] += 1
                        n = _append_ok[0]
                        if n <= 3 or n % 200 == 0:
                            logger.info(
                                "audio_buffer.append OK #%d (fail=%d)",
                                n, _append_fail[0],
                            )
                    except Exception as e:
                        _append_fail[0] += 1
                        logger.error(
                            "audio_buffer.append FAILED #%d: %s",
                            _append_fail[0], e,
                        )

                connection.input_audio_buffer.append = _counted_append

                self.audio_processor = AudioProcessor(connection)
                await self._setup_session()
                self.audio_processor.start_playback()

                print("\n" + "=" * 60)
                print(f"🎤 SCENARIO VOICE ASSISTANT: {self.scenario_config.name}")
                print(f"   Initial state: {self.engine.current_state}")
                print("   Press Ctrl+C to exit")
                print("=" * 60 + "\n")

                await self._process_events()
        finally:
            if self.audio_processor:
                self.audio_processor.shutdown()

    async def _setup_session(self):
        logger.info("Setting up scenario session...")

        voice_config: Union[AzureStandardVoice, str]
        if "-" in self.voice:
            voice_config = AzureStandardVoice(
                name=self.voice,
                locale="ja-JP",
                temperature=self.temperature,
                rate=self.rate,
                custom_lexicon_url=self.lexicon_url,
            )
        else:
            voice_config = self.voice

        turn_detection = AzureSemanticVadMultilingual(
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=700,
            languages=["ja"],
            remove_filler_words=True,
        )

        classify_tool = self._build_classify_tool()

        interim_response_config = StaticInterimResponseConfig(
            triggers=[InterimResponseTrigger.TOOL, InterimResponseTrigger.LATENCY],
            latency_threshold_ms=500,
            texts=[
                "少々お待ちください。",
                "確認しております。",
            ],
        )

        session_config = RequestSession(
            modalities=[Modality.TEXT, Modality.AUDIO],
            instructions=self._build_system_prompt(),
            voice=voice_config,
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            turn_detection=turn_detection,
            input_audio_echo_cancellation=AudioEchoCancellation(),
            input_audio_noise_reduction=AudioNoiseReduction(
                type="azure_deep_noise_suppression"
            ),
            tools=[classify_tool],
            tool_choice=ToolChoiceLiteral.REQUIRED,
            interim_response=interim_response_config,
            input_audio_transcription=AudioInputTranscriptionOptions(
                model="azure-speech",
                language="ja",
                phrase_list=self.phrase_list,
            ),
        )

        conn = self.connection
        assert conn is not None
        await conn.session.update(session=session_config)
        logger.info("Session configuration sent (tool_choice=required)")

    # ------------------------------------------------------------------
    # Event loop
    # ------------------------------------------------------------------
    async def _heartbeat(self):
        """Periodic diagnostic: log that the event loop is alive."""
        while True:
            await asyncio.sleep(10)
            ap = self.audio_processor
            if ap and ap.input_stream:
                logger.info(
                    "Heartbeat: event_loop alive, capture_active=%s",
                    ap.input_stream.is_active(),
                )

    async def _process_events(self):
        conn = self.connection
        assert conn is not None
        heartbeat = asyncio.create_task(self._heartbeat())
        try:
            async for event in conn:
                await self._handle_event(event)
        except Exception:
            logger.exception("Error processing events")
            raise
        finally:
            heartbeat.cancel()

    async def _handle_event(self, event):
        if event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            logger.debug("audio delta (binary omitted)")
        else:
            try:
                logger.debug("Event: %s | %s", event.type, vars(event))
            except TypeError:
                logger.debug("Event: %s | %s", event.type, event)

        ap = self.audio_processor
        conn = self.connection
        assert ap is not None and conn is not None

        # --- SESSION READY ------------------------------------------------
        if event.type == ServerEventType.SESSION_UPDATED:
            logger.info("Session ready: %s", event.session.id)
            self.session_ready = True

            if not self.conversation_started:
                self.conversation_started = True
                await self._send_greeting()

            ap.start_capture()

        # --- BARGE-IN -----------------------------------------------------
        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            logger.info("User started speaking")
            print("🎤 Listening...")
            ap.skip_pending_audio()
            if self._active_response and not self._response_api_done:
                try:
                    await conn.response.cancel()
                except Exception as e:
                    if "no active response" not in str(e).lower():
                        logger.warning("Cancel failed: %s", e)

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            logger.info("User stopped speaking")
            print("🤔 Processing...")

        # --- RESPONSE LIFECYCLE -------------------------------------------
        elif event.type == ServerEventType.RESPONSE_CREATED:
            self._active_response = True
            self._response_api_done = False

        elif event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            ap.queue_audio(event.delta)

        elif event.type == ServerEventType.RESPONSE_AUDIO_DONE:
            print("🎤 Ready...")

        elif event.type == ServerEventType.RESPONSE_DONE:
            self._active_response = False
            self._response_api_done = True
            logger.info(
                "Response completed. Waiting for user speech. "
                "capture_stream_active=%s, loop_running=%s",
                ap.input_stream is not None and ap.input_stream.is_active() if ap.input_stream else False,
                ap.loop.is_running() if hasattr(ap, 'loop') else "N/A",
            )

            # One-time: verify WebSocket send works from event loop directly
            if not hasattr(self, '_send_verified'):
                self._send_verified = True
                try:
                    test_audio = base64.b64encode(b'\x00' * 2400).decode('utf-8')
                    await conn.input_audio_buffer.append(audio=test_audio)
                    logger.info("Direct audio send test: SUCCESS")
                except Exception as e:
                    logger.error("Direct audio send test: FAILED - %s", e)

            # Execute pending function call
            if self._pending_function_call and "arguments" in self._pending_function_call:
                await self._execute_function_call(self._pending_function_call)
                self._pending_function_call = None

        # --- FUNCTION CALLING ---------------------------------------------
        elif event.type == ServerEventType.CONVERSATION_ITEM_CREATED:
            if event.item.type == ItemType.FUNCTION_CALL:
                self._pending_function_call = {
                    "name": event.item.name,
                    "call_id": event.item.call_id,
                    "previous_item_id": event.item.id,
                }
                logger.info("Function call: %s (call_id=%s)", event.item.name, event.item.call_id)

        elif event.type == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            if (
                self._pending_function_call
                and event.call_id == self._pending_function_call["call_id"]
            ):
                self._pending_function_call["arguments"] = event.arguments
                logger.info("Function args: %s", event.arguments)

        # --- TRANSCRIPTION ------------------------------------------------
        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            logger.info("📝 User: %s", event.transcript)

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            logger.info("🗣️ Assistant: %s", event.transcript)

        # --- ERRORS -------------------------------------------------------
        elif event.type == ServerEventType.ERROR:
            msg = event.error.message
            if "Cancellation failed" in msg:
                logger.debug("Benign cancel error: %s", msg)
            else:
                logger.error("❌ Error: %s", msg)
                print(f"Error: {msg}")

    # ------------------------------------------------------------------
    # Greeting (session start)
    # ------------------------------------------------------------------
    async def _send_greeting(self):
        """Send initial greeting via the engine."""
        conn = self.connection
        assert conn is not None

        result = self.engine.get_initial_response()
        logger.info("Greeting: state=%s", result.state_name)
        print(f"📋 State: {result.state_name}")

        # Use per-response instructions + tool_choice=none so the model
        # generates audio for the greeting without calling a function
        await conn.response.create(
            response=ResponseCreateParams(
                instructions=(
                    self._build_system_prompt()
                    + "\n\n【今回の応答指示】\n"
                    + result.response_instructions
                ),
                tool_choice="none",
            )
        )

    # ------------------------------------------------------------------
    # Function call execution (classify_and_act → engine)
    # ------------------------------------------------------------------
    async def _execute_function_call(self, call_info: Dict[str, Any]):
        conn = self.connection
        assert conn is not None

        function_name = call_info["name"]
        call_id = call_info["call_id"]
        previous_item_id = call_info["previous_item_id"]
        raw_args = call_info["arguments"]

        if function_name != "classify_and_act":
            logger.warning("Unknown function: %s", function_name)
            return

        # Parse arguments
        try:
            if isinstance(raw_args, str):
                args = json.loads(raw_args)
            else:
                args = raw_args
        except json.JSONDecodeError:
            logger.error("Failed to parse function args: %s", raw_args)
            args = {"intent": "off_topic", "slots": {}, "user_summary": str(raw_args)}

        intent = args.get("intent", "off_topic")
        slots = args.get("slots", {})
        user_summary = args.get("user_summary", "")

        logger.info(
            "classify_and_act: intent=%s slots=%s summary=%s",
            intent, slots, user_summary,
        )

        # Run through state machine
        result = self.engine.process(intent=intent, slots=slots, user_summary=user_summary)

        print(f"📋 State: {result.state_name} | intent={intent}")
        if result.missing_slots:
            print(f"   Missing: {result.missing_slots}")
        if result.escalation:
            print("   ⚠️ ESCALATION triggered")
        if result.is_terminal:
            print("   🏁 Terminal state reached")

        # Send engine result back as function output
        output_data = {
            "response_instructions": result.response_instructions,
            "state": result.state_name,
            "collected_slots": result.collected_slots,
            "missing_slots": result.missing_slots,
        }

        function_output = FunctionCallOutputItem(
            call_id=call_id,
            output=json.dumps(output_data, ensure_ascii=False),
        )
        await conn.conversation.item.create(
            previous_item_id=previous_item_id, item=function_output
        )
        # Override tool_choice to "none" so the model generates audio
        # instead of calling classify_and_act again (session default is REQUIRED)
        await conn.response.create(
            response=ResponseCreateParams(tool_choice="none")
        )
        logger.info("Engine result sent, requesting new response (tool_choice=none)")


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scenario-driven Voice Assistant using Azure VoiceLive SDK",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--api-key", type=str,
        default=os.environ.get("AZURE_VOICELIVE_API_KEY"),
        help="Azure VoiceLive API key",
    )
    parser.add_argument(
        "--endpoint", type=str,
        default=os.environ.get("AZURE_VOICELIVE_ENDPOINT", ""),
        help="Azure VoiceLive endpoint",
    )
    parser.add_argument(
        "--model", type=str,
        default=os.environ.get("AZURE_VOICELIVE_MODEL", "gpt-realtime"),
        help="VoiceLive model",
    )
    parser.add_argument(
        "--voice", type=str,
        default=os.environ.get("AZURE_VOICELIVE_VOICE", "ja-jp-Masaru:DragonHDLatestNeural"),
        help="Voice name",
    )
    parser.add_argument(
        "--scenario", type=str,
        default=os.environ.get("SCENARIO_PATH", DEFAULT_SCENARIO_PATH),
        help="Path to scenario YAML file",
    )
    parser.add_argument(
        "--temperature", type=float,
        default=float(os.environ.get("AZURE_VOICELIVE_TEMPERATURE", "0.8")),
        help="Voice temperature (0.0-1.0)",
    )
    parser.add_argument(
        "--rate", type=str,
        default=os.environ.get("AZURE_VOICELIVE_RATE", "0.95"),
        help="Speaking rate (0.5-1.5)",
    )
    parser.add_argument(
        "--lexicon-url", type=str,
        default=os.environ.get("AZURE_VOICELIVE_LEXICON_URL", LEXICON_URL),
        help="Custom pronunciation lexicon URL",
    )
    parser.add_argument(
        "--phrase-list", type=str,
        default=os.environ.get("AZURE_VOICELIVE_PHRASE_LIST", ""),
        help="Comma-separated phrase list for STT",
    )
    parser.add_argument(
        "--use-token-credential", action="store_true", default=False,
        help="Use Azure token credential instead of API key",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    return parser.parse_args()


def main():
    args = parse_arguments()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.api_key and not args.use_token_credential:
        print("❌ Error: No authentication provided.")
        print("Use --api-key or --use-token-credential")
        sys.exit(1)

    credential: Union[AzureKeyCredential, AsyncTokenCredential]
    if args.use_token_credential:
        credential = AzureCliCredential()
    else:
        credential = AzureKeyCredential(args.api_key)

    lexicon_url = None if args.lexicon_url == "none" else args.lexicon_url
    phrase_list = None
    if args.phrase_list:
        phrase_list = [p.strip() for p in args.phrase_list.split(",") if p.strip()]
    elif args.model != "gpt-realtime":
        phrase_list = DEFAULT_PHRASE_LIST_JA

    logger.info(
        "Model: %s | Voice: %s | Scenario: %s",
        args.model, args.voice, args.scenario,
    )

    client = ScenarioClient(
        endpoint=args.endpoint,
        credential=credential,
        model=args.model,
        voice=args.voice,
        scenario_path=args.scenario,
        temperature=args.temperature,
        rate=args.rate,
        phrase_list=phrase_list,
        lexicon_url=lexicon_url,
    )

    def signal_handler(_sig, _frame):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        logger.exception("Fatal error")
        print(f"Fatal Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
