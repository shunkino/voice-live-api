"""Unit tests for the integrated Voice Live experiment session factory.

These cover the pure configuration logic that turns an ExperimentConfig into
Voice Live SDK objects for all three features (custom voice, avatar,
MAI-Transcribe), without needing any Azure connectivity.
"""
from __future__ import annotations

import os
import sys

import pytest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, ROOT_DIR)

from voicelive_demo.config import AvatarSpec, ExperimentConfig, VoiceSpec
from voicelive_demo.session_factory import (
    build_avatar,
    build_session,
    build_transcription,
    build_voice,
)


# ═══════════════════════════════════════════════════════════════════════════
# Feature #1 — custom voice
# ═══════════════════════════════════════════════════════════════════════════
class TestVoice:
    def test_standard_voice(self):
        v = build_voice(VoiceSpec(voice_type="standard", name="en-US-AvaNeural"))
        d = v.as_dict()
        assert d["type"] == "azure-standard"
        assert d["name"] == "en-US-AvaNeural"

    def test_personal_voice_sounds_like_me(self):
        v = build_voice(
            VoiceSpec(
                voice_type="personal",
                name="my-cloned-voice",
                base_model="DragonLatestNeural",
            )
        )
        d = v.as_dict()
        assert d["type"] == "azure-personal"
        assert d["name"] == "my-cloned-voice"
        assert d["model"] == "DragonLatestNeural"

    def test_custom_voice_requires_endpoint(self):
        spec = VoiceSpec(voice_type="custom", name="en-US-BrandNeural")
        with pytest.raises(ValueError):
            build_voice(spec)

    def test_custom_voice(self):
        v = build_voice(
            VoiceSpec(
                voice_type="custom",
                name="en-US-BrandNeural",
                endpoint_id="11111111-2222-3333-4444-555555555555",
            )
        )
        d = v.as_dict()
        assert d["type"] == "azure-custom"
        assert d["endpoint_id"] == "11111111-2222-3333-4444-555555555555"

    def test_avatar_voice_sync_has_no_name(self):
        v = build_voice(
            VoiceSpec(voice_type="avatar-voice-sync", base_model="DragonHDOmniLatestNeural")
        )
        d = v.as_dict()
        assert d["type"] == "avatar-voice-sync"
        assert d["model"] == "DragonHDOmniLatestNeural"
        assert "name" not in d

    def test_bad_personal_base_model(self):
        with pytest.raises(ValueError):
            VoiceSpec(voice_type="personal", name="x", base_model="NopeNeural").validate()


# ═══════════════════════════════════════════════════════════════════════════
# Feature #3 — transcription model selection
# ═══════════════════════════════════════════════════════════════════════════
class TestTranscription:
    def test_mai_transcribe(self):
        t = build_transcription("mai-transcribe-1", "en")
        assert t.as_dict()["model"] == "mai-transcribe-1"

    def test_azure_speech_keeps_phrase_list(self):
        t = build_transcription("azure-speech", "en", ["Azure", "Foundry"])
        assert t.as_dict()["phrase_list"] == ["Azure", "Foundry"]

    def test_phrase_list_dropped_for_mai(self):
        # phrase_list isn't supported by mai-transcribe-1; it must be omitted.
        t = build_transcription("mai-transcribe-1", "en", ["Azure"])
        assert "phrase_list" not in t.as_dict()

    def test_openai_transcriber_requires_realtime_model(self):
        cfg = ExperimentConfig(model="gpt-4.1", transcription_model="gpt-4o-transcribe")
        with pytest.raises(ValueError):
            cfg.validate()

    def test_mai_transcribe_works_with_text_model(self):
        cfg = ExperimentConfig(model="gpt-4.1", transcription_model="mai-transcribe-1")
        cfg.validate()  # should not raise


# ═══════════════════════════════════════════════════════════════════════════
# Feature #2 — avatar
# ═══════════════════════════════════════════════════════════════════════════
class TestAvatar:
    def test_disabled_returns_none(self):
        assert build_avatar(AvatarSpec(enabled=False)) is None

    def test_video_avatar(self):
        a = build_avatar(
            AvatarSpec(enabled=True, character="lisa", style="casual-sitting")
        )
        d = a.as_dict()
        assert d["type"] == "video-avatar"
        assert d["character"] == "lisa"
        assert d["style"] == "casual-sitting"
        assert d["video"]["resolution"]["width"] == 1080

    def test_photo_avatar_defaults_model(self):
        a = build_avatar(
            AvatarSpec(enabled=True, avatar_type="photo-avatar", character="anika")
        )
        d = a.as_dict()
        assert d["type"] == "photo-avatar"
        assert d["model"] == "vasa-1"
        # photo avatars don't carry a video-avatar style
        assert "style" not in d


# ═══════════════════════════════════════════════════════════════════════════
# Integration — all three features in one session
# ═══════════════════════════════════════════════════════════════════════════
class TestSession:
    def test_all_three_features_compose(self):
        cfg = ExperimentConfig(
            model="gpt-realtime",
            voice=VoiceSpec(voice_type="personal", name="me", base_model="MAI-Voice-1"),
            avatar=AvatarSpec(enabled=True, character="lisa"),
            transcription_model="mai-transcribe-1",
            enable_viseme=True,
        )
        session = build_session(cfg)
        d = session.as_dict()
        assert d["voice"]["type"] == "azure-personal"
        assert d["avatar"]["character"] == "lisa"
        assert d["input_audio_transcription"]["model"] == "mai-transcribe-1"
        assert d["animation"]["outputs"] == ["viseme_id"]

    def test_session_without_avatar_has_no_avatar_key(self):
        cfg = ExperimentConfig(model="gpt-realtime")
        d = build_session(cfg).as_dict()
        assert "avatar" not in d

    def test_blendshapes_animation_output(self):
        cfg = ExperimentConfig(model="gpt-realtime", enable_blendshapes=True)
        d = build_session(cfg).as_dict()
        assert d["animation"]["outputs"] == ["blendshapes"]

    def test_viseme_and_blendshapes_compose(self):
        cfg = ExperimentConfig(
            model="gpt-realtime", enable_viseme=True, enable_blendshapes=True
        )
        d = build_session(cfg).as_dict()
        assert d["animation"]["outputs"] == ["viseme_id", "blendshapes"]

    def test_no_animation_without_request(self):
        cfg = ExperimentConfig(model="gpt-realtime")
        assert "animation" not in build_session(cfg).as_dict()

    def test_local_face_model_when_avatar_off_and_animation_on(self):
        assert ExperimentConfig(enable_viseme=True).local_face_model is True
        assert ExperimentConfig(enable_blendshapes=True).local_face_model is True
        # No animation requested -> no local face model.
        assert ExperimentConfig().local_face_model is False
        # Avatar on -> the avatar's own face is used, not a local model.
        assert (
            ExperimentConfig(
                avatar=AvatarSpec(enabled=True), enable_viseme=True
            ).local_face_model
            is False
        )

    def test_summary_mentions_each_feature(self):
        cfg = ExperimentConfig(
            voice=VoiceSpec(voice_type="custom", name="b", endpoint_id="e"),
            avatar=AvatarSpec(enabled=True, character="lisa"),
            transcription_model="mai-transcribe-1",
        )
        s = cfg.summary()
        assert "custom voice" in s
        assert "mai-transcribe-1" in s
        assert "lisa" in s
