"""test_session_state.py – pytest coverage for T009 and T020.

T009: VoiceSession stores latest_location, latest_day, and pending_weather_text
      but does NOT store transcript or audio fields.
T020: Follow-up location resolution — pending request is stored and can be
      resolved when the user later provides a location.
"""
import time

import pytest

from agent.session_state import SessionStore, VoiceSession


# ── T009: VoiceSession field existence and defaults ───────────────────────────


class TestVoiceSessionFields:
    """Session tracks weather context; no transcript/audio."""

    def test_session_id_stored(self):
        s = VoiceSession(session_id="sess-001")
        assert s.session_id == "sess-001"

    def test_latest_location_defaults_none(self):
        s = VoiceSession(session_id="sess-001")
        assert s.latest_location is None

    def test_latest_day_defaults_none(self):
        s = VoiceSession(session_id="sess-001")
        assert s.latest_day is None

    def test_turn_count_defaults_zero(self):
        s = VoiceSession(session_id="sess-001")
        assert s.turn_count == 0

    def test_pending_weather_text_defaults_none(self):
        s = VoiceSession(session_id="sess-001")
        assert s.pending_weather_text is None

    def test_created_at_set(self):
        s = VoiceSession(session_id="sess-001")
        assert s.created_at is not None

    def test_updated_at_set(self):
        s = VoiceSession(session_id="sess-001")
        assert s.updated_at is not None

    # FR-012 / data-model.md: no audio bytes or transcripts stored
    def test_no_transcript_field(self):
        s = VoiceSession(session_id="sess-001")
        assert not hasattr(s, "transcript"), (
            "VoiceSession must NOT store transcripts (FR-012)"
        )

    def test_no_audio_field(self):
        s = VoiceSession(session_id="sess-001")
        assert not hasattr(s, "audio"), (
            "VoiceSession must NOT store audio bytes (FR-012)"
        )

    def test_no_audio_bytes_field(self):
        s = VoiceSession(session_id="sess-001")
        assert not hasattr(s, "audio_bytes"), (
            "VoiceSession must NOT store audio bytes (FR-012)"
        )

    def test_no_full_transcript_field(self):
        s = VoiceSession(session_id="sess-001")
        assert not hasattr(s, "full_transcript"), (
            "VoiceSession must NOT store full transcripts (FR-012)"
        )


# ── T009: VoiceSession mutator methods ───────────────────────────────────────


class TestVoiceSessionMutators:
    def test_update_location_sets_field(self):
        s = VoiceSession(session_id="sess-loc")
        s.update_location("東京")
        assert s.latest_location == "東京"

    def test_update_day_sets_field(self):
        s = VoiceSession(session_id="sess-day")
        s.update_day("明日")
        assert s.latest_day == "明日"

    def test_increment_turn_increases_count(self):
        s = VoiceSession(session_id="sess-turn")
        s.increment_turn()
        assert s.turn_count == 1

    def test_increment_turn_accumulates(self):
        s = VoiceSession(session_id="sess-turn2")
        for _ in range(3):
            s.increment_turn()
        assert s.turn_count == 3

    def test_touch_updates_updated_at(self):
        s = VoiceSession(session_id="sess-touch")
        before = s.updated_at
        time.sleep(0.01)
        s.touch()
        assert s.updated_at >= before

    def test_set_pending_request_stores_text(self):
        s = VoiceSession(session_id="sess-pend")
        s.set_pending_request("今日の天気は？")
        assert s.pending_weather_text == "今日の天気は？"

    def test_clear_pending_request_sets_none(self):
        s = VoiceSession(session_id="sess-clear")
        s.set_pending_request("今日の天気は？")
        s.clear_pending_request()
        assert s.pending_weather_text is None

    def test_update_location_does_not_set_transcript(self):
        s = VoiceSession(session_id="sess-no-transcript")
        s.update_location("大阪")
        assert not hasattr(s, "transcript")


# ── T009: SessionStore CRUD ───────────────────────────────────────────────────


class TestSessionStore:
    def test_get_or_create_returns_voice_session(self):
        store = SessionStore()
        session = store.get_or_create("sess-new")
        assert isinstance(session, VoiceSession)

    def test_get_or_create_idempotent(self):
        store = SessionStore()
        s1 = store.get_or_create("sess-idem")
        s2 = store.get_or_create("sess-idem")
        assert s1 is s2

    def test_get_returns_none_for_unknown_id(self):
        store = SessionStore()
        assert store.get("does-not-exist") is None

    def test_get_returns_session_after_create(self):
        store = SessionStore()
        store.get_or_create("sess-get")
        assert store.get("sess-get") is not None

    def test_empty_session_id_raises(self):
        store = SessionStore()
        with pytest.raises(ValueError):
            store.get_or_create("")

    def test_delete_removes_session(self):
        store = SessionStore()
        store.get_or_create("sess-del")
        store.delete("sess-del")
        assert store.get("sess-del") is None

    def test_delete_nonexistent_does_not_raise(self):
        store = SessionStore()
        store.delete("never-existed")  # should not raise

    def test_count_empty_store(self):
        store = SessionStore()
        assert store.count() == 0

    def test_count_after_create(self):
        store = SessionStore()
        store.get_or_create("sess-a")
        store.get_or_create("sess-b")
        assert store.count() == 2

    def test_count_after_delete(self):
        store = SessionStore()
        store.get_or_create("sess-a")
        store.get_or_create("sess-b")
        store.delete("sess-a")
        assert store.count() == 1


# ── T020: Follow-up location resolution ──────────────────────────────────────


class TestFollowUpLocationResolution:
    """When user provides location after a clarification, session resolves it.

    Scenario:
        1. User sends "今日の天気は？" (no location).
        2. Agent stores the raw text as pending_weather_text.
        3. Agent asks for location clarification.
        4. User replies with "東京".
        5. Session stores 東京 in latest_location, resolves pending request.
    """

    def test_pending_request_stored_after_missing_location(self):
        """Step 2: raw text is stored while waiting for location."""
        store = SessionStore()
        session = store.get_or_create("clarify-session")

        session.set_pending_request("今日の天気は？")

        assert session.pending_weather_text == "今日の天気は？"

    def test_location_update_clears_pending_request(self):
        """Step 5: after user provides location the pending text should be
        clearable so the next turn starts fresh."""
        store = SessionStore()
        session = store.get_or_create("clarify-resolve")

        session.set_pending_request("今日の天気は？")
        session.update_location("東京")
        session.clear_pending_request()

        assert session.latest_location == "東京"
        assert session.pending_weather_text is None

    def test_latest_location_is_preserved_for_follow_up(self):
        """FR-005: location from turn N is available in turn N+1."""
        store = SessionStore()
        session = store.get_or_create("follow-up")

        session.update_location("大阪")
        session.increment_turn()
        # Re-fetch from store simulates next request
        retrieved = store.get("follow-up")
        assert retrieved is not None
        assert retrieved.latest_location == "大阪"

    def test_multiple_location_updates_keep_latest(self):
        """Session keeps only the latest location mentioned."""
        store = SessionStore()
        session = store.get_or_create("multi-loc")

        session.update_location("東京")
        session.update_location("大阪")

        assert session.latest_location == "大阪"

    def test_pending_request_session_has_no_transcript(self):
        """Even during clarification flow, no transcript is stored."""
        store = SessionStore()
        session = store.get_or_create("pending-no-transcript")
        session.set_pending_request("今日の天気は？")
        assert not hasattr(session, "transcript")

    def test_day_preserved_from_pending_to_resolved(self):
        """When user says '今日の天気は？', day context should survive."""
        store = SessionStore()
        session = store.get_or_create("day-preserve")
        session.update_day("今日")
        session.set_pending_request("今日の天気は？")
        # After providing location
        session.update_location("京都")
        session.clear_pending_request()
        assert session.latest_day == "今日"
        assert session.latest_location == "京都"
