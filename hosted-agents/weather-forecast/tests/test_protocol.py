"""test_protocol.py – pytest coverage for T010, T012, and T021.

T010: WebSocket message parsing and 1 MB frame-limit helpers.
T012: Protocol handler contract — weather.request frame produces a
      weather.response shape.
T021: Protocol handler contract — weather.clarification shape is correct
      and an unsupported message type produces an error response.
"""
import json

import pytest

from agent.protocol import (
    MAX_FRAME_BYTES,
    ClarificationMessage,
    ErrorMessage,
    WeatherRequestMessage,
    WeatherResponseMessage,
    check_frame_size,
    oversized_frame_error,
    parse_text_frame,
    unsupported_type_error,
)


# ── T010: MAX_FRAME_BYTES constant ────────────────────────────────────────────


class TestMaxFrameBytes:
    def test_max_frame_bytes_is_one_mebibyte(self):
        assert MAX_FRAME_BYTES == 1_048_576

    def test_max_frame_bytes_is_positive(self):
        assert MAX_FRAME_BYTES > 0


# ── T010: parse_text_frame ────────────────────────────────────────────────────


class TestParseTextFrame:
    def test_valid_json_object_returns_dict(self):
        raw = '{"type": "weather.request", "text": "今日の東京の天気は？"}'
        result = parse_text_frame(raw)
        assert isinstance(result, dict)
        assert result["type"] == "weather.request"

    def test_invalid_json_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_text_frame("{bad json}")

    def test_json_array_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_text_frame('["not", "an", "object"]')

    def test_json_string_raises_value_error(self):
        with pytest.raises(ValueError):
            parse_text_frame('"just a string"')

    def test_empty_object_is_valid(self):
        result = parse_text_frame("{}")
        assert result == {}

    def test_unicode_text_preserved(self):
        raw = '{"type": "weather.request", "text": "今日の東京の天気は？", "session_id": "s1"}'
        result = parse_text_frame(raw)
        assert result["text"] == "今日の東京の天気は？"


# ── T010: check_frame_size ────────────────────────────────────────────────────


class TestCheckFrameSize:
    def test_small_bytes_within_limit(self):
        data = b"small frame"
        check_frame_size(data)  # must not raise

    def test_small_string_within_limit(self):
        data = "small string"
        check_frame_size(data)  # must not raise

    def test_exactly_one_mebibyte_not_rejected(self):
        # Exactly at the limit must pass
        data = b"x" * MAX_FRAME_BYTES
        check_frame_size(data)  # must not raise

    def test_one_byte_over_limit_raises(self):
        data = b"x" * (MAX_FRAME_BYTES + 1)
        with pytest.raises(ValueError):
            check_frame_size(data)

    def test_oversized_string_raises(self):
        data = "a" * (MAX_FRAME_BYTES + 1)
        with pytest.raises(ValueError):
            check_frame_size(data)

    def test_large_binary_frame_raises(self):
        data = b"\x00" * (MAX_FRAME_BYTES * 2)
        with pytest.raises(ValueError):
            check_frame_size(data)

    def test_error_message_mentions_size(self):
        big_frame = b"x" * (MAX_FRAME_BYTES + 100)
        with pytest.raises(ValueError) as exc_info:
            check_frame_size(big_frame)
        assert str(MAX_FRAME_BYTES + 100) in str(exc_info.value) or str(MAX_FRAME_BYTES) in str(exc_info.value)


# ── T010: helper error constructors ──────────────────────────────────────────


class TestErrorHelpers:
    def test_unsupported_type_error_returns_error_message(self):
        err = unsupported_type_error("example.type")
        assert isinstance(err, ErrorMessage)

    def test_unsupported_type_error_message_contains_type(self):
        err = unsupported_type_error("example.type")
        assert "example.type" in err.message

    def test_unsupported_type_error_type_field(self):
        err = unsupported_type_error("example.type")
        assert err.type == "error"

    def test_unsupported_type_error_none_input(self):
        err = unsupported_type_error(None)
        assert isinstance(err, ErrorMessage)
        assert len(err.message) > 0

    def test_oversized_frame_error_returns_error_message(self):
        err = oversized_frame_error(2_000_000)
        assert isinstance(err, ErrorMessage)

    def test_oversized_frame_error_message_contains_size(self):
        err = oversized_frame_error(2_000_000)
        assert "2000000" in err.message

    def test_oversized_frame_error_message_contains_limit(self):
        err = oversized_frame_error(2_000_000)
        assert str(MAX_FRAME_BYTES) in err.message


# ── T010 + T012: WeatherRequestMessage ───────────────────────────────────────


class TestWeatherRequestMessage:
    """T012: weather.request text frame produces correct message object."""

    def test_from_dict_sets_type(self):
        data = {"type": "weather.request", "text": "今日の東京の天気は？", "session_id": "s1"}
        msg = WeatherRequestMessage.from_dict(data)
        assert msg.type == "weather.request"

    def test_from_dict_sets_text(self):
        data = {"type": "weather.request", "text": "今日の東京の天気は？", "session_id": "s1"}
        msg = WeatherRequestMessage.from_dict(data)
        assert msg.text == "今日の東京の天気は？"

    def test_from_dict_sets_session_id(self):
        data = {"type": "weather.request", "text": "今日の東京の天気は？", "session_id": "demo-1"}
        msg = WeatherRequestMessage.from_dict(data)
        assert msg.session_id == "demo-1"

    def test_from_dict_missing_session_id_defaults_empty(self):
        data = {"type": "weather.request", "text": "今日の天気は？"}
        msg = WeatherRequestMessage.from_dict(data)
        assert msg.session_id == ""

    def test_roundtrip_parse_then_message(self):
        """T012: parse JSON frame → WeatherRequestMessage."""
        raw = '{"type": "weather.request", "text": "今日の東京の天気は？", "session_id": "sess-1"}'
        data = parse_text_frame(raw)
        msg = WeatherRequestMessage.from_dict(data)
        assert msg.type == "weather.request"
        assert msg.text == "今日の東京の天気は？"
        assert msg.session_id == "sess-1"


# ── T012: WeatherResponseMessage ─────────────────────────────────────────────


class TestWeatherResponseMessage:
    """T012: weather.response has correct shape including demo_data flag."""

    def test_type_field_is_weather_response(self):
        msg = WeatherResponseMessage(
            text="東京の今日の天気は晴れです。",
            location="東京",
            day="今日",
        )
        assert msg.type == "weather.response"

    def test_to_dict_contains_required_keys(self):
        msg = WeatherResponseMessage(
            text="東京の今日の天気は晴れです。",
            location="東京",
            day="今日",
            demo_data=True,
        )
        d = msg.to_dict()
        assert "type" in d
        assert "text" in d
        assert "location" in d
        assert "day" in d
        assert "demo_data" in d

    def test_to_dict_type_value(self):
        msg = WeatherResponseMessage(text="晴れ", location="大阪", day="今日")
        assert msg.to_dict()["type"] == "weather.response"

    def test_to_dict_demo_data_true_by_default(self):
        msg = WeatherResponseMessage(text="晴れ", location="大阪", day="今日")
        assert msg.to_dict()["demo_data"] is True

    def test_to_json_returns_string(self):
        msg = WeatherResponseMessage(text="晴れ", location="東京", day="今日")
        result = msg.to_json()
        assert isinstance(result, str)

    def test_to_json_is_valid_json(self):
        msg = WeatherResponseMessage(text="晴れ", location="東京", day="今日")
        parsed = json.loads(msg.to_json())
        assert parsed["type"] == "weather.response"

    def test_to_json_preserves_japanese_text(self):
        msg = WeatherResponseMessage(
            text="東京の今日の天気は晴れです。",
            location="東京",
            day="今日",
        )
        raw = msg.to_json()
        assert "東京" in raw


# ── T021: ClarificationMessage ────────────────────────────────────────────────


class TestClarificationMessage:
    """T021: weather.clarification has correct type, text, and missing fields."""

    def test_type_field_is_weather_clarification(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        assert msg.type == "weather.clarification"

    def test_missing_defaults_to_location(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        assert "location" in msg.missing

    def test_to_dict_contains_required_keys(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        d = msg.to_dict()
        assert "type" in d
        assert "text" in d
        assert "missing" in d

    def test_to_dict_type_is_weather_clarification(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        assert msg.to_dict()["type"] == "weather.clarification"

    def test_to_json_returns_string(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        assert isinstance(msg.to_json(), str)

    def test_to_json_is_valid_json(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        parsed = json.loads(msg.to_json())
        assert parsed["type"] == "weather.clarification"

    def test_to_json_missing_field_is_list(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        parsed = json.loads(msg.to_json())
        assert isinstance(parsed["missing"], list)

    def test_to_json_preserves_japanese_text(self):
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？")
        raw = msg.to_json()
        assert "どの地域" in raw


# ── T021: ErrorMessage + unsupported type ────────────────────────────────────


class TestErrorMessage:
    """T021: error message shape and unsupported-type handling."""

    def test_error_message_type_field(self):
        err = ErrorMessage(message="Unsupported message type: foo.bar")
        assert err.type == "error"

    def test_error_message_to_dict_has_type_and_message(self):
        err = ErrorMessage(message="Something went wrong")
        d = err.to_dict()
        assert "type" in d
        assert "message" in d

    def test_error_message_to_json_valid(self):
        err = ErrorMessage(message="Unsupported message type: foo.bar")
        parsed = json.loads(err.to_json())
        assert parsed["type"] == "error"

    def test_unsupported_type_produces_error_in_message(self):
        """T021: sending an unknown message type must yield an error."""
        unknown_type = "audio.stream"
        err = unsupported_type_error(unknown_type)
        assert unknown_type in err.message

    def test_unsupported_type_json_has_error_type(self):
        err = unsupported_type_error("binary.blob")
        parsed = json.loads(err.to_json())
        assert parsed["type"] == "error"

    def test_unsupported_type_json_message_not_empty(self):
        err = unsupported_type_error("some.unknown")
        parsed = json.loads(err.to_json())
        assert len(parsed["message"]) > 0


# ── T021: Clarification → follow-up protocol flow ────────────────────────────


class TestClarificationProtocolFlow:
    """T021: clarification frame is emitted when location is missing;
    the follow-up response resumes the forecast."""

    def test_clarification_frame_missing_contains_location(self):
        """Clarification response always reports 'location' as missing."""
        msg = ClarificationMessage(text="どの地域の天気を知りたいですか？", missing=["location"])
        parsed = json.loads(msg.to_json())
        assert "location" in parsed["missing"]

    def test_follow_up_response_has_weather_response_type(self):
        """After user provides a location, the response type is weather.response."""
        msg = WeatherResponseMessage(
            text="大阪の今日の天気は曇りです。",
            location="大阪",
            day="今日",
        )
        assert msg.type == "weather.response"

    def test_clarification_and_response_are_distinct_types(self):
        clarification = ClarificationMessage(text="どの地域ですか？")
        response = WeatherResponseMessage(text="晴れ", location="京都", day="今日")
        assert clarification.type != response.type
