"""test_config.py – pytest coverage for T008 and T025.

T008: Settings defaults and invalid WEATHER_PROVIDER handling.
T025: Missing Foundry configuration (PROJECT_NAME, AGENT_NAME,
      AZURE_VOICELIVE_ENDPOINT) is reported by its environment-variable name.
"""
import os

import pytest

from agent.config import Settings, VALID_WEATHER_PROVIDERS


# ── T008: Default values ──────────────────────────────────────────────────────


class TestSettingsDefaults:
    """Settings() constructor returns spec-correct defaults."""

    def test_default_agent_name(self):
        s = Settings()
        assert s.agent_name == "weather-forecast-agent"

    def test_default_weather_provider(self):
        s = Settings()
        assert s.weather_provider == "mock"

    def test_default_host(self):
        s = Settings()
        assert s.host == "127.0.0.1"

    def test_default_port(self):
        s = Settings()
        assert s.port == 8080

    def test_default_model_deployment_name(self):
        s = Settings()
        assert s.model_deployment_name == "gpt-realtime"

    def test_default_voice_name(self):
        s = Settings()
        assert s.voice_name == "ja-JP-NanamiNeural"

    def test_default_transcription_language(self):
        s = Settings()
        assert s.transcription_language == "ja"

    def test_default_project_name_empty(self):
        s = Settings()
        assert s.project_name == ""

    def test_default_foundry_endpoint_empty(self):
        s = Settings()
        assert s.foundry_endpoint == ""

    def test_valid_weather_providers_contains_mock(self):
        assert "mock" in VALID_WEATHER_PROVIDERS

    def test_valid_weather_providers_contains_jma(self):
        assert "jma" in VALID_WEATHER_PROVIDERS


# ── T008: from_env() with invalid provider ────────────────────────────────────


class TestFromEnvInvalidProvider:
    """Settings.from_env() raises ValueError for unsupported WEATHER_PROVIDER."""

    def test_invalid_provider_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("WEATHER_PROVIDER", "openweathermap")
        with pytest.raises(ValueError, match="WEATHER_PROVIDER"):
            Settings.from_env()

    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setenv("WEATHER_PROVIDER", "invalid")
        with pytest.raises(ValueError):
            Settings.from_env()

    def test_empty_provider_raises(self, monkeypatch):
        monkeypatch.setenv("WEATHER_PROVIDER", "")
        with pytest.raises(ValueError):
            Settings.from_env()

    def test_valid_mock_provider(self, monkeypatch):
        monkeypatch.setenv("WEATHER_PROVIDER", "mock")
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        monkeypatch.delenv("AZURE_VOICELIVE_ENDPOINT", raising=False)
        s = Settings.from_env()
        assert s.weather_provider == "mock"

    def test_valid_jma_provider(self, monkeypatch):
        monkeypatch.setenv("WEATHER_PROVIDER", "jma")
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        monkeypatch.delenv("AZURE_VOICELIVE_ENDPOINT", raising=False)
        s = Settings.from_env()
        assert s.weather_provider == "jma"

    def test_invalid_port_raises_value_error(self, monkeypatch):
        monkeypatch.setenv("WEATHER_AGENT_PORT", "not-a-number")
        with pytest.raises(ValueError, match="WEATHER_AGENT_PORT"):
            Settings.from_env()

    def test_from_env_picks_up_agent_name(self, monkeypatch):
        monkeypatch.setenv("WEATHER_AGENT_NAME", "my-custom-agent")
        monkeypatch.delenv("AGENT_NAME", raising=False)
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        monkeypatch.delenv("AZURE_VOICELIVE_ENDPOINT", raising=False)
        s = Settings.from_env()
        assert s.agent_name == "my-custom-agent"

    def test_from_env_agent_name_env_var_takes_precedence(self, monkeypatch):
        """AGENT_NAME env var populates agent_name when set."""
        monkeypatch.setenv("AGENT_NAME", "hosted-agent-prod")
        monkeypatch.setenv("WEATHER_AGENT_NAME", "local-display-name")
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        monkeypatch.delenv("AZURE_VOICELIVE_ENDPOINT", raising=False)
        s = Settings.from_env()
        assert s.agent_name == "hosted-agent-prod"

    def test_from_env_agent_name_fallback_to_weather_agent_name(self, monkeypatch):
        """When AGENT_NAME is absent, WEATHER_AGENT_NAME is used as the display name."""
        monkeypatch.delenv("AGENT_NAME", raising=False)
        monkeypatch.setenv("WEATHER_AGENT_NAME", "local-only-agent")
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        monkeypatch.delenv("AZURE_VOICELIVE_ENDPOINT", raising=False)
        s = Settings.from_env()
        assert s.agent_name == "local-only-agent"

    def test_from_env_agent_name_default_when_neither_set(self, monkeypatch):
        monkeypatch.delenv("AGENT_NAME", raising=False)
        monkeypatch.delenv("WEATHER_AGENT_NAME", raising=False)
        monkeypatch.delenv("PROJECT_NAME", raising=False)
        monkeypatch.delenv("AZURE_VOICELIVE_ENDPOINT", raising=False)
        s = Settings.from_env()
        assert s.agent_name == "weather-forecast-agent"


# ── T025: Missing Foundry config names are reported correctly ─────────────────


class TestValidateFoundry:
    """validate_foundry() / report_missing_foundry() surface env var names."""

    def test_validate_foundry_returns_list(self):
        s = Settings()
        result = s.validate_foundry()
        assert isinstance(result, list)

    def test_all_three_missing_by_default(self):
        s = Settings()
        missing = s.validate_foundry()
        # All three required env vars must be reported
        assert "PROJECT_NAME" in missing
        assert "AGENT_NAME" in missing
        assert "AZURE_VOICELIVE_ENDPOINT" in missing

    def test_project_name_missing_when_not_set(self):
        s = Settings(project_name="")
        missing = s.validate_foundry()
        assert "PROJECT_NAME" in missing

    def test_project_name_present_when_set(self):
        s = Settings(
            project_name="my-project",
            agent_name="my-agent",
            foundry_endpoint="https://example.services.ai.azure.com/",
        )
        missing = s.validate_foundry()
        assert "PROJECT_NAME" not in missing

    def test_agent_name_missing_when_default(self):
        """Default agent_name is NOT a valid hosted-agent name."""
        s = Settings(agent_name="weather-forecast-agent")
        missing = s.validate_foundry()
        assert "AGENT_NAME" in missing

    def test_agent_name_present_when_custom(self):
        s = Settings(
            project_name="my-project",
            agent_name="my-custom-agent",
            foundry_endpoint="https://example.services.ai.azure.com/",
        )
        missing = s.validate_foundry()
        assert "AGENT_NAME" not in missing

    def test_azure_voicelive_endpoint_missing_when_not_set(self):
        s = Settings(foundry_endpoint="")
        missing = s.validate_foundry()
        assert "AZURE_VOICELIVE_ENDPOINT" in missing

    def test_azure_voicelive_endpoint_present_when_set(self):
        s = Settings(
            project_name="my-project",
            agent_name="my-agent",
            foundry_endpoint="https://example.services.ai.azure.com/",
        )
        missing = s.validate_foundry()
        assert "AZURE_VOICELIVE_ENDPOINT" not in missing

    def test_validate_foundry_empty_when_all_present(self):
        s = Settings(
            project_name="my-project",
            agent_name="my-agent",
            foundry_endpoint="https://example.services.ai.azure.com/",
        )
        assert s.validate_foundry() == []

    def test_is_foundry_ready_false_by_default(self):
        s = Settings()
        assert s.is_foundry_ready() is False

    def test_is_foundry_ready_true_when_complete(self):
        s = Settings(
            project_name="my-project",
            agent_name="my-agent",
            foundry_endpoint="https://example.services.ai.azure.com/",
        )
        assert s.is_foundry_ready() is True

    def test_report_missing_foundry_nonempty_by_default(self):
        s = Settings()
        report = s.report_missing_foundry()
        assert len(report) > 0

    def test_report_missing_foundry_contains_project_name(self):
        s = Settings()
        report = s.report_missing_foundry()
        assert "PROJECT_NAME" in report

    def test_report_missing_foundry_contains_agent_name(self):
        s = Settings()
        report = s.report_missing_foundry()
        assert "AGENT_NAME" in report

    def test_report_missing_foundry_contains_endpoint(self):
        s = Settings()
        report = s.report_missing_foundry()
        assert "AZURE_VOICELIVE_ENDPOINT" in report

    def test_report_missing_foundry_empty_when_all_present(self):
        s = Settings(
            project_name="my-project",
            agent_name="my-agent",
            foundry_endpoint="https://example.services.ai.azure.com/",
        )
        assert s.report_missing_foundry() == ""

    def test_agent_name_env_var_not_reported_missing(self, monkeypatch):
        """AGENT_NAME set in env → validate_foundry() must not list AGENT_NAME as missing."""
        monkeypatch.setenv("AGENT_NAME", "hosted-agent-prod")
        monkeypatch.setenv("PROJECT_NAME", "my-project")
        monkeypatch.setenv("AZURE_VOICELIVE_ENDPOINT", "https://example.services.ai.azure.com/")
        monkeypatch.delenv("WEATHER_AGENT_NAME", raising=False)
        s = Settings.from_env()
        missing = s.validate_foundry()
        assert "AGENT_NAME" not in missing

    def test_only_weather_agent_name_set_reports_agent_name_missing(self, monkeypatch):
        """WEATHER_AGENT_NAME alone is a display name; AGENT_NAME is still missing."""
        monkeypatch.delenv("AGENT_NAME", raising=False)
        monkeypatch.setenv("WEATHER_AGENT_NAME", "local-only-agent")
        monkeypatch.setenv("PROJECT_NAME", "my-project")
        monkeypatch.setenv("AZURE_VOICELIVE_ENDPOINT", "https://example.services.ai.azure.com/")
        s = Settings.from_env()
        missing = s.validate_foundry()
        assert "AGENT_NAME" in missing

    def test_agent_name_env_var_is_foundry_ready(self, monkeypatch):
        """All three Foundry vars set → is_foundry_ready() returns True."""
        monkeypatch.setenv("AGENT_NAME", "my-hosted-agent")
        monkeypatch.setenv("PROJECT_NAME", "my-project")
        monkeypatch.setenv("AZURE_VOICELIVE_ENDPOINT", "https://example.services.ai.azure.com/")
        s = Settings.from_env()
        assert s.is_foundry_ready() is True
