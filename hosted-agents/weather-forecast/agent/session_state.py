"""In-memory session state for the weather-forecast hosted agent.

No audio bytes, full transcripts, or personal data are stored.
State is keyed by session_id and lives only for the process lifetime.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class VoiceSession:
    """Active conversation state — no audio/transcript storage."""

    session_id: str
    latest_location: Optional[str] = None
    latest_day: Optional[str] = None
    # Pending weather request info preserved across a clarification turn.
    # Stores the raw user text so we can retry after location is supplied.
    pending_weather_text: Optional[str] = None
    turn_count: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)

    def update_location(self, location: str) -> None:
        self.latest_location = location
        self.touch()

    def update_day(self, day: str) -> None:
        self.latest_day = day
        self.touch()

    def set_pending_request(self, text: str) -> None:
        """Store the raw text of a weather request that needs clarification."""
        self.pending_weather_text = text
        self.touch()

    def clear_pending_request(self) -> None:
        self.pending_weather_text = None
        self.touch()

    def increment_turn(self) -> None:
        self.turn_count += 1
        self.touch()


class SessionStore:
    """Thread-safe in-memory store for VoiceSession objects."""

    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str) -> VoiceSession:
        if not session_id:
            raise ValueError("session_id must be non-empty")
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = VoiceSession(session_id=session_id)
            return self._sessions[session_id]

    def get(self, session_id: str) -> Optional[VoiceSession]:
        with self._lock:
            return self._sessions.get(session_id)

    def delete(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


# Module-level default store used by app.py.
default_store = SessionStore()
