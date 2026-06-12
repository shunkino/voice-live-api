# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
# -------------------------------------------------------------------------
"""Turn an :class:`ExperimentConfig` into Voice Live SDK objects.

This is the single place where the three features are assembled into one
``RequestSession``:

* :func:`build_voice` -> custom / personal / standard voice
* :func:`build_transcription` -> azure-speech / mai-transcribe-1 / ...
* :func:`build_avatar` -> talking-head avatar config
* :func:`build_session` -> the combined ``RequestSession``
"""
from __future__ import annotations

from typing import List, Optional, Union

from azure.ai.voicelive.models import (
    Animation,
    AnimationOutputType,
    AudioEchoCancellation,
    AudioInputTranscriptionOptions,
    AudioNoiseReduction,
    AvatarConfig,
    AzureAvatarVoiceSyncVoice,
    AzureCustomVoice,
    AzurePersonalVoice,
    AzureSemanticVadMultilingual,
    AzureStandardVoice,
    Background,
    InputAudioFormat,
    Modality,
    OutputAudioFormat,
    RequestSession,
    VideoParams,
    VideoResolution,
)

from .config import AvatarSpec, ExperimentConfig, VoiceSpec

VoiceConfig = Union[
    AzureStandardVoice,
    AzureCustomVoice,
    AzurePersonalVoice,
    AzureAvatarVoiceSyncVoice,
    str,
]


def build_voice(spec: VoiceSpec) -> VoiceConfig:
    """Build the SDK voice object for the configured voice type."""
    spec.validate()

    if spec.voice_type == "custom":
        return AzureCustomVoice(
            name=spec.name,
            endpoint_id=spec.endpoint_id,
            temperature=spec.temperature,
            custom_lexicon_url=spec.custom_lexicon_url,
            locale=spec.locale,
            style=spec.style,
            rate=spec.rate,
        )

    if spec.voice_type == "personal":
        return AzurePersonalVoice(
            name=spec.name,
            model=spec.base_model,
            temperature=spec.temperature,
            custom_lexicon_url=spec.custom_lexicon_url,
            locale=spec.locale,
            style=spec.style,
            rate=spec.rate,
        )

    if spec.voice_type == "avatar-voice-sync":
        # The voice is tied to the custom avatar; no name required.
        return AzureAvatarVoiceSyncVoice(
            model=spec.base_model,
            temperature=spec.temperature,
            custom_lexicon_url=spec.custom_lexicon_url,
            locale=spec.locale,
            style=spec.style,
            rate=spec.rate,
        )

    # standard / HD voice
    return AzureStandardVoice(
        name=spec.name,
        temperature=spec.temperature,
        custom_lexicon_url=spec.custom_lexicon_url,
        locale=spec.locale,
        style=spec.style,
        rate=spec.rate,
    )


def build_transcription(
    model: str,
    language: Optional[str] = None,
    phrase_list: Optional[List[str]] = None,
) -> AudioInputTranscriptionOptions:
    """Build the input-audio transcription options.

    Phrase lists are only honoured by ``azure-speech``; they're dropped for
    other models (including ``mai-transcribe-1``) so the comparison stays fair.
    """
    kwargs: dict = {"model": model}
    if language:
        kwargs["language"] = language
    if phrase_list and model == "azure-speech":
        kwargs["phrase_list"] = list(phrase_list)
    return AudioInputTranscriptionOptions(**kwargs)


def build_avatar(spec: AvatarSpec) -> Optional[AvatarConfig]:
    """Build the avatar config, or ``None`` when the avatar is disabled."""
    if not spec.enabled:
        return None
    spec.validate()

    background: Optional[Background] = None
    if spec.background_image_url:
        background = Background(image_url=spec.background_image_url)
    elif spec.background_color:
        background = Background(color=spec.background_color)

    video = VideoParams(
        bitrate=spec.video_bitrate,
        codec=spec.video_codec,
        resolution=VideoResolution(width=spec.video_width, height=spec.video_height),
        background=background,
    )

    avatar_kwargs: dict = {
        "avatar_type": spec.avatar_type,
        "character": spec.character,
        "customized": spec.customized,
        "video": video,
    }
    if spec.style and spec.avatar_type == "video-avatar":
        avatar_kwargs["style"] = spec.style
    if spec.model:
        avatar_kwargs["model"] = spec.model
    return AvatarConfig(**avatar_kwargs)


def build_turn_detection(language: Optional[str]) -> AzureSemanticVadMultilingual:
    """Multilingual semantic VAD with barge-in, biased to the active language."""
    langs = [language.split("-")[0]] if language else None
    return AzureSemanticVadMultilingual(
        threshold=0.5,
        prefix_padding_ms=300,
        silence_duration_ms=500,
        languages=langs,
        remove_filler_words=False,
    )


def build_session(cfg: ExperimentConfig) -> RequestSession:
    """Assemble the combined :class:`RequestSession` for all three features."""
    cfg.validate()

    animation: Optional[Animation] = None
    outputs = []
    if cfg.enable_viseme:
        outputs.append(AnimationOutputType.VISEME_ID)
    if cfg.enable_blendshapes:
        outputs.append(AnimationOutputType.BLENDSHAPES)
    if outputs:
        # Visemes/blendshapes are handy both for the server-side avatar's
        # lip-sync and for driving our own locally rendered face model.
        animation = Animation(outputs=outputs)

    session = RequestSession(
        modalities=[Modality.TEXT, Modality.AUDIO],
        instructions=cfg.instructions,
        voice=build_voice(cfg.voice),
        input_audio_format=InputAudioFormat.PCM16,
        output_audio_format=OutputAudioFormat.PCM16,
        turn_detection=build_turn_detection(cfg.transcription_language),
        input_audio_echo_cancellation=AudioEchoCancellation(),
        input_audio_noise_reduction=AudioNoiseReduction(
            type="azure_deep_noise_suppression"
        ),
        input_audio_transcription=build_transcription(
            cfg.transcription_model,
            cfg.transcription_language,
            cfg.phrase_list,
        ),
    )

    avatar = build_avatar(cfg.avatar)
    if avatar is not None:
        session.avatar = avatar
    if animation is not None:
        session.animation = animation

    return session
