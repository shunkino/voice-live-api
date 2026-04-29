# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
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
from typing import Union, Optional, Dict, Any, Mapping, Callable, TYPE_CHECKING, cast

from azure.core.credentials import AzureKeyCredential
from azure.core.credentials_async import AsyncTokenCredential
from azure.identity.aio import AzureCliCredential, DefaultAzureCredential

from azure.ai.voicelive.aio import connect
from azure.ai.voicelive.models import (
    AudioEchoCancellation,
    AudioNoiseReduction,
    AzureSemanticVadMultilingual,
    AzureStandardVoice,
    AssistantMessageItem,
    InputAudioFormat,
    InterimResponseTrigger,
    ItemType,
    LlmInterimResponseConfig,
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

if TYPE_CHECKING:
    from azure.ai.voicelive.aio import VoiceLiveConnection

## Change to the directory where this script is located
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(SCRIPT_DIR)

# Lexicon file path (file URI for local use)
LEXICON_PATH = os.path.join(SCRIPT_DIR, "lexicon.xml")
LEXICON_URL = f"file://{LEXICON_PATH}"

# Default Japanese phrase list for improved STT recognition
DEFAULT_PHRASE_LIST_JA = [
    "Azure", "Microsoft", "Copilot", "VoiceLive", "Teams",
    "OneDrive", "SharePoint", "Contoso",
    "NVIDIA", "Google", "Apple",
    "天気", "気温", "天候", "湿度", "風速",
    "今何時", "時間", "時刻",
]

# Environment variable loading
load_dotenv('./.env', override=True)

# Set up logging
## Add folder for logging
if not os.path.exists('logs'):
    os.makedirs('logs')

## Add timestamp for logfiles
timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

## Set up logging
logging.basicConfig(
    filename=f'logs/{timestamp}_voicelive.log',
    filemode="w",
    format='%(asctime)s:%(name)s:%(levelname)s:%(message)s',
    level=logging.DEBUG
)
logger = logging.getLogger(__name__)

## Also log to console
_console_handler = logging.StreamHandler()
_console_handler.setLevel(logging.DEBUG)
_console_handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(message)s'))
logger.addHandler(_console_handler)


class AudioProcessor:
    """
    Handles real-time audio capture and playback for the voice assistant.

    Threading Architecture:
    - Main thread: Event loop and UI
    - Capture thread: PyAudio input stream reading
    - Send thread: Async audio data transmission to VoiceLive
    - Playback thread: PyAudio output stream writing
    """
    
    loop: asyncio.AbstractEventLoop
    
    class AudioPlaybackPacket:
        """Represents a packet that can be sent to the audio playback queue."""
        def __init__(self, seq_num: int, data: Optional[bytes]):
            self.seq_num = seq_num
            self.data = data

    # Built-in device name patterns to deprioritize (prefer external/headphone devices)
    _BUILTIN_PATTERNS = ("MacBook", "Built-in", "Internal", "default")
    _VIRTUAL_PATTERNS = (
        "Teams", "Zoom", "Webex", "Meet", "Discord",
        "Virtual", "Aggregate", "Loopback", "BlackHole",
        "Soundflower", "VB-Audio", "CABLE",
    )

    def __init__(self, connection):
        self.connection = connection
        self.audio = pyaudio.PyAudio()

        # Audio configuration - PCM16, 24kHz, mono as specified
        self.format = pyaudio.paInt16
        self.channels = 1
        self.rate = 24000
        self.chunk_size = 1200 # 50ms

        # Capture and playback state
        self.input_stream = None

        self.playback_queue: queue.Queue[AudioProcessor.AudioPlaybackPacket] = queue.Queue()
        self.playback_base = 0
        self.next_seq_num = 0
        self.output_stream: Optional[pyaudio.Stream] = None

        # Select preferred audio devices (headphones/external over built-in)
        self._input_device_index, self._output_device_index = self._select_devices()

        logger.info("AudioProcessor initialized with 24kHz PCM16 mono audio")

    def _select_devices(self) -> tuple[Optional[int], Optional[int]]:
        """Select best input/output devices, preferring external (headphones/USB) over built-in."""
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

        def _is_builtin(name: str) -> bool:
            return any(p in name for p in self._BUILTIN_PATTERNS)

        def _is_virtual(name: str) -> bool:
            return any(p.lower() in name.lower() for p in self._VIRTUAL_PATTERNS)

        # Prefer external devices; fall back to built-in
        ext_inputs = [(i, n) for i, n in input_candidates if not _is_builtin(n) and not _is_virtual(n)]
        ext_outputs = [(i, n) for i, n in output_candidates if not _is_builtin(n) and not _is_virtual(n)]

        in_idx: Optional[int] = None
        out_idx: Optional[int] = None

        # Try to pair input/output from the same external device name
        if ext_inputs and ext_outputs:
            for ii, iname in ext_inputs:
                for oi, oname in ext_outputs:
                    if iname == oname:
                        in_idx, out_idx = ii, oi
                        break
                if in_idx is not None:
                    break
            # If no exact pair, use first external for each
            if in_idx is None:
                in_idx = ext_inputs[0][0]
            if out_idx is None:
                out_idx = ext_outputs[0][0]
        elif ext_inputs:
            in_idx = ext_inputs[0][0]
        elif ext_outputs:
            out_idx = ext_outputs[0][0]

        # Log selections
        if in_idx is not None:
            in_name = p.get_device_info_by_index(in_idx).get("name", "?")
            logger.info("Selected INPUT device: [%d] %s", in_idx, in_name)
            print(f"🎧 Input: [{in_idx}] {in_name}")
        else:
            logger.info("Using system default INPUT device")
            print("🎧 Input: system default")

        if out_idx is not None:
            out_name = p.get_device_info_by_index(out_idx).get("name", "?")
            logger.info("Selected OUTPUT device: [%d] %s", out_idx, out_name)
            print(f"🔊 Output: [{out_idx}] {out_name}")
        else:
            logger.info("Using system default OUTPUT device")
            print("🔊 Output: system default")

        return in_idx, out_idx

    def start_capture(self):
        """Start capturing audio from microphone."""
        def _capture_callback(
            in_data,      # data
            _frame_count,  # number of frames
            _time_info,    # dictionary
            _status_flags):
            """Audio capture thread - runs in background."""
            audio_base64 = base64.b64encode(in_data).decode("utf-8")
            asyncio.run_coroutine_threadsafe(
                self.connection.input_audio_buffer.append(audio=audio_base64), self.loop
            )
            return (None, pyaudio.paContinue)

        if self.input_stream:
            return

        # Store the current event loop for use in threads
        self.loop = asyncio.get_event_loop()

        try:
            open_kwargs: Dict[str, Any] = dict(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                input=True,
                frames_per_buffer=self.chunk_size,
                stream_callback=_capture_callback,
            )
            if self._input_device_index is not None:
                open_kwargs["input_device_index"] = self._input_device_index
            self.input_stream = self.audio.open(**open_kwargs)
            logger.info("Started audio capture")

        except Exception:
            logger.exception("Failed to start audio capture")
            raise

    def start_playback(self):
        """Initialize audio playback system."""
        if self.output_stream:
            return

        remaining = bytes()
        def _playback_callback(
            _in_data,
            frame_count,  # number of frames
            _time_info,
            _status_flags):

            nonlocal remaining
            frame_count *= pyaudio.get_sample_size(pyaudio.paInt16)

            out = remaining[:frame_count]
            remaining = remaining[frame_count:]

            while len(out) < frame_count:
                try:
                    packet = self.playback_queue.get_nowait()
                except queue.Empty:
                    out = out + bytes(frame_count - len(out))
                    continue
                except Exception:
                    logger.exception("Error in audio playback")
                    raise

                if not packet or not packet.data:
                    # None packet indicates end of stream
                    logger.info("End of playback queue.")
                    break

                if packet.seq_num < self.playback_base:
                    # skip requested
                    # ignore skipped packet and clear remaining
                    if len(remaining) > 0:
                        remaining = bytes()
                    continue

                num_to_take = frame_count - len(out)
                out = out + packet.data[:num_to_take]
                remaining = packet.data[num_to_take:]

            if len(out) >= frame_count:
                return (out, pyaudio.paContinue)
            else:
                return (out, pyaudio.paComplete)

        try:
            open_kwargs: Dict[str, Any] = dict(
                format=self.format,
                channels=self.channels,
                rate=self.rate,
                output=True,
                frames_per_buffer=self.chunk_size,
                stream_callback=_playback_callback,
            )
            if self._output_device_index is not None:
                open_kwargs["output_device_index"] = self._output_device_index
            self.output_stream = self.audio.open(**open_kwargs)
            logger.info("Audio playback system ready")
        except Exception:
            logger.exception("Failed to initialize audio playback")
            raise

    def _get_and_increase_seq_num(self):
        seq = self.next_seq_num
        self.next_seq_num += 1
        return seq

    def queue_audio(self, audio_data: Optional[bytes]) -> None:
        """Queue audio data for playback."""
        self.playback_queue.put(
            AudioProcessor.AudioPlaybackPacket(
                seq_num=self._get_and_increase_seq_num(),
                data=audio_data))

    def skip_pending_audio(self):
        """Skip current audio in playback queue."""
        self.playback_base = self._get_and_increase_seq_num()

    def shutdown(self):
        """Clean up audio resources."""
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
            self.input_stream = None

        logger.info("Stopped audio capture")

        # Inform thread to complete
        if self.output_stream:
            self.skip_pending_audio()
            self.queue_audio(None)
            self.output_stream.stop_stream()
            self.output_stream.close()
            self.output_stream = None

        logger.info("Stopped audio playback")

        if self.audio:
            self.audio.terminate()

        logger.info("Audio processor cleaned up")



class AsyncFunctionCallingClient:
    """Voice assistant with function calling capabilities using VoiceLive SDK patterns."""

    def __init__(
        self,
        endpoint: str,
        credential: Union[AzureKeyCredential, AsyncTokenCredential],
        model: str,
        voice: str,
        instructions: str,
        temperature: float = 0.8,
        rate: str = "1.0",
        phrase_list: Optional[list[str]] = None,
        lexicon_url: Optional[str] = None,
    ):
        self.endpoint = endpoint
        self.credential = credential
        self.model = model
        self.voice = voice
        self.instructions = instructions
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

        # Define available functions
        self.available_functions: Dict[str, Callable[[Union[str, Mapping[str, Any]]], Mapping[str, Any]]] = {
            "get_current_time": self.get_current_time,
            "get_current_weather": self.get_current_weather,
        }

    async def start(self):
        """Start the voice assistant session."""
        try:
            logger.info("Connecting to VoiceLive API with model %s", self.model)

            # Connect to VoiceLive WebSocket API
            async with connect(
                endpoint=self.endpoint,
                credential=self.credential,
                model=self.model,
            ) as connection:
                conn = connection
                self.connection = conn

                # Initialize audio processor
                ap = AudioProcessor(conn)
                self.audio_processor = ap

                # Configure session for voice conversation
                await self._setup_session()

                # Start audio systems
                ap.start_playback()

                logger.info("Voice assistant with function calling ready! Start speaking...")
                print("\n" + "=" * 60)
                print("🎤 VOICE ASSISTANT WITH FUNCTION CALLING READY")
                print("Try saying:")
                print("  • 'What's the current time?'")
                print("  • 'What's the weather in Seattle?'")
                print("Press Ctrl+C to exit")
                print("=" * 60 + "\n")

                # Process events
                await self._process_events()
        finally:
            if self.audio_processor:
                self.audio_processor.shutdown()

    async def _setup_session(self):
        """Configure the VoiceLive session for audio conversation with function tools."""
        logger.info("Setting up voice conversation session with function tools...")

        # Create voice configuration
        voice_config: Union[AzureStandardVoice, str]
        if self.voice.startswith("en-US-") or self.voice.startswith("en-CA-") or "-" in self.voice:
            # Azure voice with locale hint, rate, temperature, and custom lexicon
            voice_config = AzureStandardVoice(
                name=self.voice,
                locale="ja-JP",
                temperature=self.temperature,
                rate=self.rate,
                custom_lexicon_url=self.lexicon_url,
            )
            logger.info("Voice config: name=%s, temperature=%.1f, rate=%s, lexicon=%s",
                        self.voice, self.temperature, self.rate, self.lexicon_url)
        else:
            # OpenAI voice (alloy, echo, fable, onyx, nova, shimmer)
            voice_config = self.voice

        # Create turn detection configuration - use semantic VAD for better Japanese support
        turn_detection_config = AzureSemanticVadMultilingual(
            threshold=0.5,
            prefix_padding_ms=300,
            silence_duration_ms=700,
            languages=["ja"],
            remove_filler_words=True,
        )

        # Define function tools (bilingual descriptions for better Japanese tool call accuracy)
        function_tools: list[Tool] = [
            FunctionTool(
                name="get_current_time",
                description="現在の時刻を取得する (Get the current time). ユーザーが「今何時」「時間を教えて」「時刻」などと聞いた場合に呼び出す。",
                parameters={
                    "type": "object",
                    "properties": {
                        "timezone": {
                            "type": "string",
                            "description": "タイムゾーン (timezone), e.g., 'UTC', 'local', 'JST'",
                        }
                    },
                    "required": [],
                },
            ),
            FunctionTool(
                name="get_current_weather",
                description="指定された場所の現在の天気を取得する (Get the current weather in a given location). ユーザーが「天気」「気温」「天候」などについて聞いた場合に呼び出す。",
                parameters={
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "都市名 (city name), e.g., '東京', 'San Francisco, CA', '大阪'",
                        },
                        "unit": {
                            "type": "string",
                            "enum": ["celsius", "fahrenheit"],
                            "description": "温度の単位 (temperature unit): celsius（摂氏）or fahrenheit（華氏）",
                        },
                    },
                    "required": ["location"],
                },
            ),
        ]

        # --- Interim Response Configuration ---
        # Option A: LLM-based interim response (context-aware, dynamic)
        #   Uses gpt-4.1-mini (default) to generate contextual interim responses
        #   while waiting for tool results or when latency exceeds threshold.
        interim_response_config = LlmInterimResponseConfig(
            triggers=[InterimResponseTrigger.TOOL, InterimResponseTrigger.LATENCY],
            latency_threshold_ms=100,
            instructions="""ツール呼び出しや処理の待ち時間中に、短く自然な日本語の中間応答を生成してください。
例:「少々お待ちください」「確認しております」など。
ツール呼び出し時に「リアルタイムの情報にアクセスできません」とは言わないでください。""",
            max_completion_tokens=50,
        )

        # Option B: Static interim response (no extra LLM call, lower latency)
        #   Uncomment below and comment out Option A to use static texts instead.
        # interim_response_config = StaticInterimResponseConfig(
        #     triggers=[InterimResponseTrigger.TOOL, InterimResponseTrigger.LATENCY],
        #     latency_threshold_ms=1000,
        #     texts=[
        #         "少々お待ちください。",
        #         "ただいま確認しております。",
        #         "お調べしますので、少しお待ちくださいね。",
        #         "確認中です。もう少々お待ちください。",
        #     ],
        # )

        # Create session configuration with function tools
        session_config = RequestSession(
            modalities=[Modality.TEXT, Modality.AUDIO],
            instructions=self.instructions,
            voice=voice_config,
            input_audio_format=InputAudioFormat.PCM16,
            output_audio_format=OutputAudioFormat.PCM16,
            turn_detection=turn_detection_config,
            input_audio_echo_cancellation=AudioEchoCancellation(),
            input_audio_noise_reduction=AudioNoiseReduction(type="azure_deep_noise_suppression"),
            tools=function_tools,
            tool_choice=ToolChoiceLiteral.AUTO,
            interim_response=interim_response_config,
            input_audio_transcription=AudioInputTranscriptionOptions(
                model="azure-speech",
                language="ja",
                phrase_list=self.phrase_list,
            ),
        )

        conn = self.connection
        assert conn is not None, "Connection must be established before setting up session"
        await conn.session.update(session=session_config)

        logger.info("Session configuration with function tools sent")

    async def _process_events(self):
        """Process events from the VoiceLive connection."""
        try:
            conn = self.connection
            assert conn is not None, "Connection must be established before processing events"
            async for event in conn:
                await self._handle_event(event)
        except Exception:
            logger.exception("Error processing events")
            raise

    async def _handle_event(self, event):
        """Handle different types of events from VoiceLive."""
        # Log full event object for debugging (exclude raw audio binary for readability)
        if event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            logger.debug("Received event: %s (audio delta, binary omitted)", event.type)
        else:
            try:
                logger.debug("Received event: %s | %s", event.type, vars(event))
            except TypeError:
                logger.debug("Received event: %s | %s", event.type, event)
        ap = self.audio_processor
        conn = self.connection
        assert ap is not None, "AudioProcessor must be initialized"
        assert conn is not None, "Connection must be established"

        if event.type == ServerEventType.SESSION_UPDATED:
            logger.info("Session ready: %s", event.session.id)
            self.session_ready = True

            # Proactive greeting
            if not self.conversation_started:
                self.conversation_started = True
                logger.info("Sending proactive greeting request")
                try:
                    await conn.response.create(
                        response=ResponseCreateParams(
                            pre_generated_assistant_message=AssistantMessageItem(
                                content=[OutputTextContentPart(text="こんにちは。今日はどうされましたか？")]
                            )
                        )
                    )

                except Exception:
                    logger.exception("Failed to send proactive greeting request")

            # Start audio capture once session is ready
            ap.start_capture()

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STARTED:
            logger.info("User started speaking - stopping playback")
            print("🎤 Listening...")

            ap.skip_pending_audio()

            # Only cancel if response is active and not already done
            if self._active_response and not self._response_api_done:
                try:
                    await conn.response.cancel()
                    logger.debug("Cancelled in-progress response due to barge-in")
                except Exception as e:
                    if "no active response" in str(e).lower():
                        logger.debug("Cancel ignored - response already completed")
                    else:
                        logger.warning("Cancel failed: %s", e)

        elif event.type == ServerEventType.INPUT_AUDIO_BUFFER_SPEECH_STOPPED:
            logger.info("🎤 User stopped speaking")
            print("🤔 Processing...")

        elif event.type == ServerEventType.RESPONSE_CREATED:
            logger.info("🤖 Assistant response created")
            self._active_response = True
            self._response_api_done = False

        elif event.type == ServerEventType.RESPONSE_AUDIO_DELTA:
            logger.debug("Received audio delta")
            ap.queue_audio(event.delta)

        elif event.type == ServerEventType.RESPONSE_AUDIO_DONE:
            logger.info("🤖 Assistant finished speaking")
            print("🎤 Ready for next input...")

        elif event.type == ServerEventType.RESPONSE_DONE:
            logger.info("✅ Response complete")
            self._active_response = False
            self._response_api_done = True

            # Execute pending function call if arguments are ready
            if self._pending_function_call and "arguments" in self._pending_function_call:
                await self._execute_function_call(self._pending_function_call)
                self._pending_function_call = None

        elif event.type == ServerEventType.ERROR:
            msg = event.error.message
            if "Cancellation failed: no active response" in msg:
                logger.debug("Benign cancellation error: %s", msg)
            else:
                logger.error("❌ VoiceLive error: %s", msg)
                print(f"Error: {msg}")

        elif event.type == ServerEventType.CONVERSATION_ITEM_CREATED:
            logger.debug("Conversation item created: %s", event.item.id)

            if event.item.type == ItemType.FUNCTION_CALL:
                function_call_item = event.item
                self._pending_function_call = {
                    "name": function_call_item.name,
                    "call_id": function_call_item.call_id,
                    "previous_item_id": function_call_item.id
                }
                print(f"🔧 Calling function: {function_call_item.name}")
                logger.info(f"Function call detected: {function_call_item.name} with call_id: {function_call_item.call_id}")

        elif event.type == ServerEventType.RESPONSE_FUNCTION_CALL_ARGUMENTS_DONE:
            if self._pending_function_call and event.call_id == self._pending_function_call["call_id"]:
                logger.info(f"Function arguments received: {event.arguments}")
                self._pending_function_call["arguments"] = event.arguments

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_COMPLETED:
            logger.info("📝 User transcription: %s", event.transcript)

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_DELTA:
            logger.debug("📝 User transcription delta: %s", event.delta)

        elif event.type == ServerEventType.CONVERSATION_ITEM_INPUT_AUDIO_TRANSCRIPTION_FAILED:
            logger.error("❌ User transcription failed: %s", event.error)

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DELTA:
            logger.debug("🗣️ Assistant transcript delta: %s", event.delta)

        elif event.type == ServerEventType.RESPONSE_AUDIO_TRANSCRIPT_DONE:
            logger.info("🗣️ Assistant transcript: %s", event.transcript)

        elif event.type == ServerEventType.RESPONSE_TEXT_DELTA:
            logger.debug("📄 Response text delta: %s", event.delta)

        elif event.type == ServerEventType.RESPONSE_TEXT_DONE:
            logger.info("📄 Response text: %s", event.text)

        else:
            logger.debug("Unhandled event: %s", event.type)

    async def _execute_function_call(self, function_call_info):
        """Execute a function call and send the result back to the conversation."""
        conn = self.connection
        assert conn is not None, "Connection must be established"
        
        function_name = function_call_info["name"]
        call_id = function_call_info["call_id"]
        previous_item_id = function_call_info["previous_item_id"]
        arguments = function_call_info["arguments"]

        try:
            if function_name in self.available_functions:
                logger.info(f"Executing function: {function_name}")
                result = self.available_functions[function_name](arguments)

                function_output = FunctionCallOutputItem(call_id=call_id, output=json.dumps(result))

                # Send result back to conversation
                await conn.conversation.item.create(previous_item_id=previous_item_id, item=function_output)
                logger.info(f"Function result sent: {result}")
                print(f"✅ Function {function_name} completed")

                # Request new response to process the function result
                await conn.response.create()
                logger.info("Requested new response with function result")

            else:
                logger.error(f"Unknown function: {function_name}")

        except Exception as e:
            logger.error(f"Error executing function {function_name}: {e}")

    def get_current_time(self, arguments: Optional[Union[str, Mapping[str, Any]]] = None) -> Dict[str, Any]:
        """Get the current time."""
        from datetime import datetime, timezone
        
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                args = {}
        else:
            args = arguments if isinstance(arguments, dict) else {}

        timezone_arg = args.get("timezone", "local")
        now = datetime.now()

        if timezone_arg.lower() == "utc":
            now = datetime.now(timezone.utc)
            timezone_name = "UTC"
        else:
            timezone_name = "local"

        formatted_time = now.strftime("%I:%M:%S %p")
        formatted_date = now.strftime("%A, %B %d, %Y")

        return {"time": formatted_time, "date": formatted_date, "timezone": timezone_name}

    def get_current_weather(self, arguments: Union[str, Mapping[str, Any]]):
        """Get the current weather for a location."""
        if isinstance(arguments, str):
            try:
                args = json.loads(arguments)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse weather arguments: {arguments}")
                return {"error": "Invalid arguments"}
        else:
            args = arguments if isinstance(arguments, dict) else {}

        location = args.get("location", "Unknown")
        unit = args.get("unit", "celsius")

        # Simulated weather response
        try:
            return {
                "location": location,
                "temperature": 22 if unit == "celsius" else 72,
                "unit": unit,
                "condition": "Partly Cloudy",
                "humidity": 65,
                "wind_speed": 10,
            }
        except Exception as e:
            logger.error(f"Error getting weather: {e}")
            return {"error": str(e)}


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Voice Assistant with Function Calling using Azure VoiceLive SDK",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--api-key",
        help="Azure VoiceLive API key. If not provided, will use AZURE_VOICELIVE_API_KEY environment variable.",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_API_KEY"),
    )

    parser.add_argument(
        "--endpoint",
        help="Azure VoiceLive endpoint",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_ENDPOINT", "https://your-resource-name.services.ai.azure.com/"),
    )

    parser.add_argument(
        "--model",
        help="VoiceLive model to use",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_MODEL", "gpt-realtime"),
    )

    parser.add_argument(
        "--voice",
        help="Voice to use for the assistant. E.g. alloy, echo, fable, en-US-AvaNeural, en-US-GuyNeural",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_VOICE", "ja-jp-Masaru:DragonHDLatestNeural"),
    )

    parser.add_argument(
        "--instructions",
        help="System instructions for the AI assistant",
        type=str,
        default=os.environ.get(
            "AZURE_VOICELIVE_INSTRUCTIONS",
            "あなたは関数呼び出し機能を持つ親切なAIアシスタントです。日本語で応答してください。"
            "回答は1〜2文ずつ、自然な口語体で生成してください。"
            "漢字の読みが曖昧な固有名詞はカタカナで出力してください。"
            "長い説明が必要な場合は、短い文に区切って順番に話してください。"
            "ユーザーが天気について質問した場合（例：「天気は？」「気温は？」「今日の天候」）、get_current_weather関数を呼び出してください。"
            "ユーザーが時刻について質問した場合（例：「今何時？」「時間を教えて」）、get_current_time関数を呼び出してください。"
            "関数を使用する際は、自然な日本語で結果を伝えてください。"
            "英語で話しかけられた場合は英語で応答してください。",
        ),
    )

    parser.add_argument(
        "--temperature",
        help="Voice temperature (0.0-1.0). Higher = more expressive intonation for HD voices.",
        type=float,
        default=float(os.environ.get("AZURE_VOICELIVE_TEMPERATURE", "0.8")),
    )

    parser.add_argument(
        "--rate",
        help="Speaking rate (0.5-1.5). Japanese often sounds more natural at 0.9-0.95.",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_RATE", "0.95"),
    )

    parser.add_argument(
        "--lexicon-url",
        help="URL to custom pronunciation lexicon XML. Use 'none' to disable.",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_LEXICON_URL", LEXICON_URL),
    )

    parser.add_argument(
        "--phrase-list",
        help="Comma-separated phrase list for STT recognition improvement. Only works with non-realtime models.",
        type=str,
        default=os.environ.get("AZURE_VOICELIVE_PHRASE_LIST", ""),
    )

    parser.add_argument(
        "--use-token-credential", help="Use Azure token credential instead of API key", action="store_true", default=False
    )

    parser.add_argument("--verbose", help="Enable verbose logging", action="store_true")

    return parser.parse_args()

def main():
    """Main function."""
    args = parse_arguments()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Validate credentials
    if not args.api_key and not args.use_token_credential:
        print("❌ Error: No authentication provided")
        print("Please provide an API key using --api-key or set AZURE_VOICELIVE_API_KEY environment variable,")
        print("or use --use-token-credential for Azure authentication.")
        sys.exit(1)

    # Create client with appropriate credential
    credential: Union[AzureKeyCredential, AsyncTokenCredential]
    if args.use_token_credential:
        credential = AzureCliCredential()
        logger.info("Using Azure token credential")
    else:
        credential = AzureKeyCredential(args.api_key)
        logger.info("Using API key credential")

    # Resolve lexicon URL
    lexicon_url = None if args.lexicon_url == "none" else args.lexicon_url

    # Resolve phrase list
    phrase_list = None
    if args.phrase_list:
        phrase_list = [p.strip() for p in args.phrase_list.split(",") if p.strip()]
    elif args.model != "gpt-realtime":
        # Use default Japanese phrase list for non-realtime models (which support phrase_list)
        phrase_list = DEFAULT_PHRASE_LIST_JA

    logger.info("Model: %s | Voice: %s | Rate: %s | Temperature: %.1f",
                args.model, args.voice, args.rate, args.temperature)

    # Create and start voice assistant with function calling
    client = AsyncFunctionCallingClient(
        endpoint=args.endpoint,
        credential=credential,
        model=args.model,
        voice=args.voice,
        instructions=args.instructions,
        temperature=args.temperature,
        rate=args.rate,
        phrase_list=phrase_list,
        lexicon_url=lexicon_url,
    )

    # Signal handlers for graceful shutdown
    def signal_handler(_sig, _frame):
        logger.info("Received shutdown signal")
        raise KeyboardInterrupt()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(client.start())
    except KeyboardInterrupt:
        print("\n👋 Voice assistant shut down. Goodbye!")
    except Exception as e:
        logger.exception("Fatal error")
        print(f"Fatal Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    # Check for required dependencies
    dependencies = {
        "pyaudio": "Audio processing",
        "azure.ai.voicelive": "Azure VoiceLive SDK",
        "azure.core": "Azure Core libraries",
    }

    missing_deps = []
    for dep, description in dependencies.items():
        try:
            __import__(dep.replace("-", "_"))
        except ImportError:
            missing_deps.append(f"{dep} ({description})")

    if missing_deps:
        print("❌ Missing required dependencies:")
        for dep in missing_deps:
            print(f"  - {dep}")
        print("\nInstall with: pip install azure-ai-voicelive pyaudio python-dotenv")
        sys.exit(1)

    # Check audio system
    try:
        p = pyaudio.PyAudio()
        # Check for input devices
        input_devices = [
            i
            for i in range(p.get_device_count())
            if cast(Union[int, float], p.get_device_info_by_index(i).get("maxInputChannels", 0) or 0) > 0
        ]
        # Check for output devices
        output_devices = [
            i
            for i in range(p.get_device_count())
            if cast(Union[int, float], p.get_device_info_by_index(i).get("maxOutputChannels", 0) or 0) > 0
        ]
        p.terminate()

        if not input_devices:
            print("❌ No audio input devices found. Please check your microphone.")
            sys.exit(1)
        if not output_devices:
            print("❌ No audio output devices found. Please check your speakers.")
            sys.exit(1)

    except Exception as e:
        print(f"❌ Audio system check failed: {e}")
        sys.exit(1)

    print("🎙️  Voice Assistant with Function Calling - Azure VoiceLive SDK")
    print("=" * 65)

    # Run the assistant
    main()