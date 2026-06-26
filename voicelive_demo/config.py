# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""Configuration for the integrated Voice Live experiments.

Everything the three features need (custom voice, avatar, MAI-Transcribe) is
expressed through a single :class:`ExperimentConfig`. Both the CLI and the web
front-ends build their session from the very same object, so the features stay
integrated rather than isolated.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field
from typing import List, Optional

# ---------------------------------------------------------------------------
# Allowed value sets (kept in sync with the Voice Live API reference)
# ---------------------------------------------------------------------------
VOICE_TYPES = ("standard", "personal", "custom", "avatar-voice-sync")
"""Supported ``--voice-type`` values.

* ``standard``           – an Azure standard / HD voice (e.g. ``en-US-AvaNeural``,
  ``en-US-Ava:DragonHDLatestNeural``).
* ``personal``           – a *personal voice* cloned from a short sample of your
  own speech. This is the option to "sound like me".
* ``custom``             – a *professional custom voice* trained for a brand /
  character (requires an ``endpoint_id``).
* ``avatar-voice-sync``  – the voice that was synced while training a custom
  video avatar; tied to the avatar character.
"""

PERSONAL_VOICE_BASE_MODELS = (
    "DragonLatestNeural",
    "DragonHDOmniLatestNeural",
    "PhoenixLatestNeural",
    "PhoenixV2Neural",
    "MAI-Voice-1",
)

TRANSCRIPTION_MODELS = (
    "azure-speech",
    "mai-transcribe-1",
    "mai-transcribe-1.5",
    "whisper-1",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
    "gpt-4o-transcribe-diarize",
)

AVATAR_TYPES = ("video-avatar", "photo-avatar")


@dataclass
class VoiceSpec:
    """Describes the synthesis voice (feature #1: custom voice)."""

    voice_type: str = "standard"
    name: str = "en-US-Ava:DragonHDLatestNeural"
    # Base neural model for personal / avatar-voice-sync voices.
    base_model: str = "DragonLatestNeural"
    # Endpoint id (GUID) for a professional custom voice.
    endpoint_id: Optional[str] = None
    temperature: float = 0.8
    rate: Optional[str] = None
    locale: Optional[str] = None
    style: Optional[str] = None
    custom_lexicon_url: Optional[str] = None

    def validate(self) -> None:
        if self.voice_type not in VOICE_TYPES:
            raise ValueError(
                f"voice_type must be one of {VOICE_TYPES}, got {self.voice_type!r}"
            )
        if self.voice_type == "custom" and not self.endpoint_id:
            raise ValueError(
                "voice_type 'custom' requires --voice-endpoint-id (the custom "
                "voice deployment GUID)."
            )
        if self.voice_type in ("personal", "avatar-voice-sync"):
            if self.base_model not in PERSONAL_VOICE_BASE_MODELS:
                raise ValueError(
                    f"voice base model must be one of {PERSONAL_VOICE_BASE_MODELS}, "
                    f"got {self.base_model!r}"
                )
        if self.voice_type == "personal" and not self.name:
            raise ValueError(
                "voice_type 'personal' requires --voice (the personal voice name)."
            )


@dataclass
class AvatarSpec:
    """Describes the talking-head avatar (feature #2: face avatar)."""

    enabled: bool = False
    avatar_type: str = "video-avatar"
    character: str = "lisa"
    style: Optional[str] = "casual-sitting"
    customized: bool = False
    # Base model required for photo avatars (currently only "vasa-1").
    model: Optional[str] = None
    video_width: int = 1920
    video_height: int = 1080
    video_bitrate: int = 2_000_000
    video_codec: str = "h264"
    background_color: Optional[str] = "#FFFFFFFF"
    background_image_url: Optional[str] = None

    def validate(self) -> None:
        if not self.enabled:
            return
        if self.avatar_type not in AVATAR_TYPES:
            raise ValueError(
                f"avatar_type must be one of {AVATAR_TYPES}, got {self.avatar_type!r}"
            )
        if self.avatar_type == "photo-avatar" and not self.model:
            # vasa-1 is the only base model today; default it for convenience.
            self.model = "vasa-1"


@dataclass
class ExperimentConfig:
    """Top-level config that ties all three features together."""

    # --- Connection ---
    endpoint: str = "https://your-resource-name.services.ai.azure.com/"
    model: str = "gpt-realtime"
    api_key: Optional[str] = None
    use_token_credential: bool = False

    # --- Hosted agent (optional) ---
    # When both are set, Voice Live connects to a Foundry *hosted agent*
    # (agent_config) instead of a bare model. The agent then owns the
    # conversation logic; Voice Live only does STT/TTS.
    agent_name: Optional[str] = None
    agent_project_name: Optional[str] = None

    # --- Assistant behaviour ---
    instructions: str = (
        "You are a helpful AI assistant. Respond naturally and conversationally. "
        "Keep your responses concise but engaging."
    )

    # --- Feature #1: voice ---
    voice: VoiceSpec = field(default_factory=VoiceSpec)

    # --- Feature #2: avatar ---
    avatar: AvatarSpec = field(default_factory=AvatarSpec)

    # --- Feature #3: transcription ---
    transcription_model: str = "azure-speech"
    transcription_language: Optional[str] = "auto"
    phrase_list: List[str] = field(default_factory=list)

    # Emit visemes alongside audio (useful with avatars / lip-sync UIs).
    enable_viseme: bool = False
    # Emit 3D blendshape frames (richer facial expression for a local model rig).
    enable_blendshapes: bool = False

    @property
    def local_face_model(self) -> bool:
        """True when the browser should render its own face model.

        We drive a locally rendered 2D/3D face from the animation stream
        (visemes and/or blendshapes) whenever the server-side video avatar is
        *off* and at least one animation output is requested. Azure then only
        supplies voice + animation cues; the model is rendered in the browser.
        """
        return (not self.avatar.enabled) and (
            self.enable_viseme or self.enable_blendshapes
        )

    @property
    def use_agent(self) -> bool:
        """True when a Foundry hosted agent should drive the conversation."""
        return bool(self.agent_name and self.agent_project_name)

    def validate(self) -> None:
        self.voice.validate()
        self.avatar.validate()
        if bool(self.agent_name) != bool(self.agent_project_name):
            raise ValueError(
                "Hosted agent mode requires BOTH --agent-name and "
                "--agent-project-name (or neither)."
            )
        if self.transcription_model not in TRANSCRIPTION_MODELS:
            raise ValueError(
                f"transcription_model must be one of {TRANSCRIPTION_MODELS}, "
                f"got {self.transcription_model!r}"
            )
        # MAI-Transcribe-1 and azure-speech work with text chat models like
        # gpt-realtime; the OpenAI transcribers require gpt-realtime(-mini).
        openai_transcribers = (
            "whisper-1",
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "gpt-4o-transcribe-diarize",
        )
        if (
            self.transcription_model in openai_transcribers
            and "realtime" not in self.model
        ):
            raise ValueError(
                f"transcription_model {self.transcription_model!r} is only "
                "supported with gpt-realtime / gpt-realtime-mini chat models."
            )

    def summary(self) -> str:
        """A short human-readable summary of the active experiment."""
        v = self.voice
        if v.voice_type == "standard":
            voice_desc = f"standard voice '{v.name}'"
        elif v.voice_type == "personal":
            voice_desc = f"personal voice '{v.name}' (base {v.base_model})"
        elif v.voice_type == "custom":
            voice_desc = f"custom voice '{v.name}' (endpoint {v.endpoint_id})"
        else:
            voice_desc = f"avatar-synced voice (base {v.base_model})"

        avatar_desc = (
            f"{self.avatar.avatar_type} '{self.avatar.character}'"
            if self.avatar.enabled
            else "off"
        )
        anim = []
        if self.enable_viseme:
            anim.append("viseme")
        if self.enable_blendshapes:
            anim.append("blendshapes")
        if self.local_face_model:
            face_desc = f"local face model ({'+'.join(anim)})"
        elif anim:
            face_desc = "+".join(anim)
        else:
            face_desc = "off"
        target = (
            f"agent '{self.agent_name}' (project '{self.agent_project_name}')"
            if self.use_agent
            else f"model={self.model}"
        )
        return (
            f"{target} | voice={voice_desc} | "
            f"transcription={self.transcription_model} | avatar={avatar_desc} | "
            f"face={face_desc}"
        )


# ---------------------------------------------------------------------------
# argparse integration
# ---------------------------------------------------------------------------
def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the shared arguments for every feature on ``parser``."""
    conn = parser.add_argument_group("connection")
    conn.add_argument(
        "--endpoint",
        default=os.environ.get(
            "AZURE_VOICELIVE_ENDPOINT",
            "https://your-resource-name.services.ai.azure.com/",
        ),
        help="Voice Live endpoint.",
    )
    conn.add_argument(
        "--model",
        default=os.environ.get("AZURE_VOICELIVE_MODEL", "gpt-realtime"),
        help="Chat model / agent to use (e.g. gpt-realtime, gpt-4.1).",
    )
    conn.add_argument(
        "--agent-name",
        default=os.environ.get("AZURE_VOICELIVE_AGENT_NAME"),
        help="Foundry hosted agent name. When set together with "
        "--agent-project-name, Voice Live connects to the hosted agent "
        "instead of --model.",
    )
    conn.add_argument(
        "--agent-project-name",
        default=os.environ.get("AZURE_VOICELIVE_AGENT_PROJECT"),
        help="Foundry project that contains the hosted agent (required with "
        "--agent-name).",
    )
    conn.add_argument(
        "--api-key",
        default=os.environ.get("AZURE_VOICELIVE_API_KEY"),
        help="API key. Omit and pass --use-token-credential for keyless auth.",
    )
    conn.add_argument(
        "--use-token-credential",
        action="store_true",
        default=_env_bool("AZURE_VOICELIVE_USE_TOKEN", False),
        help="Use Azure AD (AzureCliCredential) instead of an API key.",
    )
    conn.add_argument(
        "--instructions",
        default=os.environ.get(
            "AZURE_VOICELIVE_INSTRUCTIONS",
            "You are a helpful AI assistant. Respond naturally and "
            "conversationally. Keep your responses concise but engaging.",
        ),
        help="System instructions for the assistant.",
    )

    # ----- Feature #1: voice -----
    voice = parser.add_argument_group("voice (feature: custom voice)")
    voice.add_argument(
        "--voice-type",
        choices=VOICE_TYPES,
        default=os.environ.get("AZURE_VOICELIVE_VOICE_TYPE", "standard"),
        help="Kind of synthesis voice.",
    )
    voice.add_argument(
        "--voice",
        default=os.environ.get(
            "AZURE_VOICELIVE_VOICE", "en-US-Ava:DragonHDLatestNeural"
        ),
        help="Voice name (standard/custom/personal voice name).",
    )
    voice.add_argument(
        "--voice-base-model",
        choices=PERSONAL_VOICE_BASE_MODELS,
        default=os.environ.get("AZURE_VOICELIVE_VOICE_BASE_MODEL", "DragonLatestNeural"),
        help="Base neural model for personal / avatar-voice-sync voices.",
    )
    voice.add_argument(
        "--voice-endpoint-id",
        default=os.environ.get("AZURE_VOICELIVE_VOICE_ENDPOINT_ID"),
        help="Deployment GUID for a professional custom voice.",
    )
    voice.add_argument(
        "--voice-temperature",
        type=float,
        default=float(os.environ.get("AZURE_VOICELIVE_VOICE_TEMPERATURE", "0.8")),
        help="HD-voice temperature (0.0-1.0).",
    )
    voice.add_argument(
        "--voice-rate",
        default=os.environ.get("AZURE_VOICELIVE_VOICE_RATE"),
        help="Speaking rate (0.5-1.5, e.g. '1.1').",
    )
    voice.add_argument(
        "--voice-locale",
        default=os.environ.get("AZURE_VOICELIVE_VOICE_LOCALE"),
        help="Enforced output locale (e.g. en-US, ja-JP).",
    )
    voice.add_argument(
        "--voice-style",
        default=os.environ.get("AZURE_VOICELIVE_VOICE_STYLE"),
        help="Speaking style (e.g. cheerful, sad).",
    )
    voice.add_argument(
        "--lexicon-url",
        default=os.environ.get("AZURE_VOICELIVE_LEXICON_URL"),
        help="Custom lexicon URL for pronunciation tweaks.",
    )

    # ----- Feature #3: transcription -----
    trans = parser.add_argument_group("transcription (feature: MAI-Transcribe)")
    trans.add_argument(
        "--transcription-model",
        choices=TRANSCRIPTION_MODELS,
        default=os.environ.get("AZURE_VOICELIVE_TRANSCRIPTION_MODEL", "azure-speech"),
        help="Input audio transcription model. Use mai-transcribe-1 or "
        "mai-transcribe-1.5 to try MAI (multilingual, incl. Japanese).",
    )
    trans.add_argument(
        "--transcription-language",
        default=os.environ.get("AZURE_VOICELIVE_TRANSCRIPTION_LANGUAGE", "auto"),
        help="Transcription language hint (BCP-47/ISO-639-1, e.g. ja, en). "
        "Use 'auto' (default) for multilingual auto-detection.",
    )
    trans.add_argument(
        "--phrase",
        action="append",
        dest="phrase_list",
        default=None,
        help="Phrase-list hint (azure-speech only). Repeatable.",
    )

    # ----- Feature #2: avatar -----
    av = parser.add_argument_group("avatar (feature: face avatar, web mode)")
    av.add_argument(
        "--avatar",
        action="store_true",
        default=_env_bool("AZURE_VOICELIVE_AVATAR", False),
        help="Enable the talking-head avatar (web mode only).",
    )
    av.add_argument(
        "--avatar-type",
        choices=AVATAR_TYPES,
        default=os.environ.get("AZURE_VOICELIVE_AVATAR_TYPE", "video-avatar"),
        help="Avatar kind.",
    )
    av.add_argument(
        "--avatar-character",
        default=os.environ.get("AZURE_VOICELIVE_AVATAR_CHARACTER", "lisa"),
        help="Avatar character name.",
    )
    av.add_argument(
        "--avatar-style",
        default=os.environ.get("AZURE_VOICELIVE_AVATAR_STYLE", "casual-sitting"),
        help="Avatar style (video avatars).",
    )
    av.add_argument(
        "--avatar-customized",
        action="store_true",
        default=_env_bool("AZURE_VOICELIVE_AVATAR_CUSTOMIZED", False),
        help="The character is a custom avatar you trained.",
    )
    av.add_argument(
        "--avatar-model",
        default=os.environ.get("AZURE_VOICELIVE_AVATAR_MODEL"),
        help="Base model for photo avatars (e.g. vasa-1).",
    )
    av.add_argument(
        "--viseme",
        action="store_true",
        default=_env_bool("AZURE_VOICELIVE_VISEME", False),
        help="Request viseme animation output alongside audio.",
    )
    av.add_argument(
        "--blendshapes",
        action="store_true",
        default=_env_bool("AZURE_VOICELIVE_BLENDSHAPES", False),
        help="Request 3D blendshape animation frames (richer local face rig).",
    )


def config_from_args(args: argparse.Namespace) -> ExperimentConfig:
    """Build a validated :class:`ExperimentConfig` from parsed CLI args."""
    voice = VoiceSpec(
        voice_type=args.voice_type,
        name=args.voice,
        base_model=args.voice_base_model,
        endpoint_id=args.voice_endpoint_id,
        temperature=args.voice_temperature,
        rate=args.voice_rate,
        locale=args.voice_locale,
        style=args.voice_style,
        custom_lexicon_url=args.lexicon_url,
    )
    avatar = AvatarSpec(
        enabled=args.avatar,
        avatar_type=args.avatar_type,
        character=args.avatar_character,
        style=args.avatar_style,
        customized=args.avatar_customized,
        model=args.avatar_model,
    )
    cfg = ExperimentConfig(
        endpoint=args.endpoint,
        model=args.model,
        agent_name=args.agent_name,
        agent_project_name=args.agent_project_name,
        api_key=args.api_key,
        use_token_credential=args.use_token_credential,
        instructions=args.instructions,
        voice=voice,
        avatar=avatar,
        transcription_model=args.transcription_model,
        transcription_language=args.transcription_language,
        phrase_list=list(args.phrase_list or []),
        enable_viseme=args.viseme,
        enable_blendshapes=args.blendshapes,
    )
    cfg.validate()
    return cfg


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")
