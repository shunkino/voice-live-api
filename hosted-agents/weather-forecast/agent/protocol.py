"""WebSocket message dataclasses and JSON parsing helpers.

All frames are UTF-8 JSON text messages. Binary frames represent voice/media
and are accepted up to MAX_FRAME_BYTES; oversized frames are rejected.

Message types
-------------
Client → Agent: weather.request
Agent → Client: weather.response, weather.clarification, error
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

MAX_FRAME_BYTES = 1_048_576  # 1 MB

# ---- inbound ------------------------------------------------------------------


@dataclass
class WeatherRequestMessage:
    type: str  # "weather.request"
    text: str
    session_id: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WeatherRequestMessage":
        return cls(
            type=data.get("type", ""),
            text=data.get("text", ""),
            session_id=data.get("session_id", ""),
        )


# ---- outbound -----------------------------------------------------------------


@dataclass
class WeatherResponseMessage:
    text: str
    location: str
    day: str
    demo_data: bool = True
    type: str = "weather.response"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "location": self.location,
            "day": self.day,
            "demo_data": self.demo_data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ClarificationMessage:
    text: str
    missing: list[str] = field(default_factory=lambda: ["location"])
    type: str = "weather.clarification"

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "missing": self.missing,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class ErrorMessage:
    message: str
    type: str = "error"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "message": self.message}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class RedirectMessage:
    """Polite decline for non-weather requests (FR-007)."""

    text: str
    type: str = "weather.redirect"

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "text": self.text}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ---- parsing helpers ----------------------------------------------------------


def parse_text_frame(raw: str) -> dict[str, Any]:
    """Parse a UTF-8 JSON text frame; raise ValueError on bad JSON."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON frame: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Frame payload must be a JSON object")
    return data


def unsupported_type_error(msg_type: Optional[str]) -> ErrorMessage:
    label = msg_type or "<missing type>"
    return ErrorMessage(message=f"Unsupported message type: {label}")


def oversized_frame_error(size: int) -> ErrorMessage:
    return ErrorMessage(
        message=f"Frame size {size} exceeds maximum allowed {MAX_FRAME_BYTES} bytes"
    )


def check_frame_size(data: bytes | str) -> None:
    """Raise ValueError when frame exceeds MAX_FRAME_BYTES."""
    size = len(data) if isinstance(data, bytes) else len(data.encode("utf-8"))
    if size > MAX_FRAME_BYTES:
        raise ValueError(
            f"Frame size {size} exceeds maximum allowed {MAX_FRAME_BYTES} bytes"
        )
