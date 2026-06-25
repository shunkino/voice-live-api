"""Settings loading and validation for the weather-forecast hosted agent.

Local mock mode works with no cloud configuration; hosted (Foundry) mode
requires PROJECT_NAME, AGENT_NAME, and AZURE_VOICELIVE_ENDPOINT.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass  # python-dotenv is optional; env vars may be set externally


VALID_WEATHER_PROVIDERS = frozenset({"mock", "jma"})

_FOUNDRY_REQUIRED = ("PROJECT_NAME", "AGENT_NAME", "AZURE_VOICELIVE_ENDPOINT")


@dataclass
class Settings:
    # --- Local sample ---
    agent_name: str = "weather-forecast-agent"
    weather_provider: Literal["mock", "jma"] = "mock"
    host: str = "127.0.0.1"
    port: int = 8080

    # --- Foundry / Voice Live ---
    project_name: str = ""
    foundry_endpoint: str = ""
    model_deployment_name: str = "gpt-realtime"
    voice_name: str = "ja-JP-NanamiNeural"
    transcription_language: str = "ja"

    # --- Validation state (populated by validate_foundry()) ---
    _foundry_missing: list[str] = field(default_factory=list, repr=False)
    # True when agent_name was sourced from the hosted AGENT_NAME env var.
    _agent_name_from_hosted_env: bool = field(default=False, repr=False)
    # True when this instance was created via from_env() — enables strict hosted validation.
    _from_env: bool = field(default=False, repr=False)

    # --- JMA optional base URL (no default — only used when provider=jma) ---
    weather_api_base: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        """Create Settings from environment variables."""
        provider = os.environ.get("WEATHER_PROVIDER", "mock").strip().lower()
        if provider not in VALID_WEATHER_PROVIDERS:
            raise ValueError(
                f"WEATHER_PROVIDER must be one of {sorted(VALID_WEATHER_PROVIDERS)!r}, got {provider!r}"
            )

        port_raw = os.environ.get("WEATHER_AGENT_PORT", "8080")
        try:
            port = int(port_raw)
        except ValueError:
            raise ValueError(f"WEATHER_AGENT_PORT must be an integer, got {port_raw!r}")

        # AGENT_NAME is the Foundry-hosted agent name (contract requirement).
        # WEATHER_AGENT_NAME is a local display/deployment fallback.
        hosted_agent_name = os.environ.get("AGENT_NAME", "").strip()
        local_display_name = os.environ.get("WEATHER_AGENT_NAME", "weather-forecast-agent")
        agent_name = hosted_agent_name or local_display_name

        s = cls(
            agent_name=agent_name,
            weather_provider=provider,  # type: ignore[arg-type]
            host=os.environ.get("WEATHER_AGENT_HOST", "127.0.0.1"),
            port=port,
            project_name=os.environ.get("PROJECT_NAME", ""),
            foundry_endpoint=os.environ.get("AZURE_VOICELIVE_ENDPOINT", ""),
            model_deployment_name=os.environ.get("MODEL_DEPLOYMENT_NAME", "gpt-realtime"),
            voice_name=os.environ.get("VOICE_NAME", "ja-JP-NanamiNeural"),
            transcription_language=os.environ.get(
                "AZURE_VOICELIVE_TRANSCRIPTION_LANGUAGE", "ja"
            ),
            weather_api_base=os.environ.get("WEATHER_API_BASE", ""),
        )
        s._agent_name_from_hosted_env = bool(hosted_agent_name)
        s._from_env = True
        return s

    def validate_foundry(self) -> list[str]:
        """Return names of missing Foundry configuration variables.

        The returned list contains the exact environment-variable names that
        are unset so callers can produce actionable error messages.
        """
        if self._from_env:
            # Strict: only explicit AGENT_NAME env var constitutes a valid hosted agent name.
            # WEATHER_AGENT_NAME is a local display fallback and does not satisfy this.
            agent_name_valid = self._agent_name_from_hosted_env
        else:
            # Direct construction (e.g. Settings(agent_name="my-agent") in tests):
            # any non-default name is treated as a valid hosted agent name.
            agent_name_valid = self.agent_name != "weather-forecast-agent"
        env_map = {
            "PROJECT_NAME": self.project_name,
            "AGENT_NAME": self.agent_name if agent_name_valid else "",
            "AZURE_VOICELIVE_ENDPOINT": self.foundry_endpoint,
        }
        missing = [name for name in _FOUNDRY_REQUIRED if not env_map[name]]
        self._foundry_missing = missing
        return missing

    def is_foundry_ready(self) -> bool:
        """True when all Foundry deployment values are present."""
        return not self.validate_foundry()

    def report_missing_foundry(self) -> str:
        """Return a human-readable message listing missing Foundry variables."""
        missing = self.validate_foundry()
        if not missing:
            return ""
        names = ", ".join(missing)
        return (
            f"Foundry deployment requires the following environment variables: {names}. "
            "Set them in your .env file or shell before starting the hosted agent."
        )
