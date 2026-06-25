"""test_readme_contract.py – pytest coverage for T026.

T026: The README.md at hosted-agents/weather-forecast/README.md must
contain the key quickstart commands and configuration statements required
by User Story 3 (Explain Sample Setup Clearly).

These tests are smoke-tests: they scan the README text for the presence of
specific strings rather than verifying functional behaviour.  They will
fail early if someone accidentally removes a critical setup step.

Covered acceptance criteria:
  - SC-004: All missing-config cases present a clear actionable message.
  - SC-005: Developer can identify demo vs. live data within 30 seconds.
  - FR-008: Clear setup instructions are included.
  - FR-011: README documents whether forecast answers are demo or live data.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# Path to the README under test
_WEATHER_FORECAST_DIR = Path(__file__).parent.parent
README_PATH = _WEATHER_FORECAST_DIR / "README.md"


@pytest.fixture(scope="module")
def readme_text() -> str:
    """Read the README once per module."""
    assert README_PATH.exists(), (
        f"README.md not found at {README_PATH}. "
        "T027/T028/T030 must create it before these tests pass."
    )
    return README_PATH.read_text(encoding="utf-8")


# ── README exists ─────────────────────────────────────────────────────────────


def test_readme_file_exists():
    assert README_PATH.exists(), f"README.md missing at {README_PATH}"


# ── Quickstart commands (FR-008, T030) ───────────────────────────────────────


class TestReadmeQuickstartCommands:
    """Key commands from quickstart.md must appear in the README."""

    def test_readme_has_pytest_command(self, readme_text):
        assert "pytest" in readme_text, "README must mention 'pytest'"

    def test_readme_has_agent_app_command(self, readme_text):
        assert "agent.app" in readme_text, "README must mention 'python -m agent.app'"

    def test_readme_has_pip_install_requirements(self, readme_text):
        assert "pip install" in readme_text and "requirements.txt" in readme_text, (
            "README must include the pip install step"
        )

    def test_readme_has_python_venv_command(self, readme_text):
        assert "venv" in readme_text, "README must include the virtualenv setup step"

    def test_readme_has_text_client_usage(self, readme_text):
        assert "text_client.py" in readme_text, (
            "README must document the text_client.py validation command"
        )

    def test_readme_has_cp_env_example_command(self, readme_text):
        assert ".env.example" in readme_text, (
            "README must show how to create a .env from .env.example"
        )

    def test_readme_has_sample_weather_question_tokyo(self, readme_text):
        assert "東京" in readme_text, (
            "README should include a sample question for Tokyo"
        )

    def test_readme_has_sample_weather_question_no_location(self, readme_text):
        # The clarification scenario must be shown
        assert "天気" in readme_text, "README must include a weather example"


# ── Configuration statements (FR-008, FR-009, T025) ──────────────────────────


class TestReadmeConfigurationStatements:
    """All required Foundry config env var names must appear in the README."""

    def test_readme_has_project_name(self, readme_text):
        assert "PROJECT_NAME" in readme_text, (
            "README must document the PROJECT_NAME configuration variable"
        )

    def test_readme_has_agent_name(self, readme_text):
        assert "AGENT_NAME" in readme_text, (
            "README must document the AGENT_NAME configuration variable"
        )

    def test_readme_has_azure_voicelive_endpoint(self, readme_text):
        assert "AZURE_VOICELIVE_ENDPOINT" in readme_text, (
            "README must document the AZURE_VOICELIVE_ENDPOINT variable"
        )

    def test_readme_has_model_deployment_name(self, readme_text):
        assert "MODEL_DEPLOYMENT_NAME" in readme_text, (
            "README must document the MODEL_DEPLOYMENT_NAME variable"
        )

    def test_readme_has_voice_name(self, readme_text):
        assert "VOICE_NAME" in readme_text, (
            "README must document the VOICE_NAME variable"
        )

    def test_readme_has_weather_provider(self, readme_text):
        assert "WEATHER_PROVIDER" in readme_text, (
            "README must document the WEATHER_PROVIDER variable"
        )

    def test_readme_has_transcription_language(self, readme_text):
        assert "AZURE_VOICELIVE_TRANSCRIPTION_LANGUAGE" in readme_text, (
            "README must document the transcription language variable"
        )


# ── Demo data vs. live data disclosure (FR-011, SC-005) ───────────────────────


class TestReadmeDemoDataDisclosure:
    """SC-005: A developer can identify demo vs. live data from the README
    within 30 seconds — the README must clearly distinguish the two modes."""

    def test_readme_mentions_mock_provider(self, readme_text):
        assert "mock" in readme_text.lower(), (
            "README must explain the mock (demo) weather provider"
        )

    def test_readme_mentions_demo_data(self, readme_text):
        keywords = ["デモ", "demo", "sample", "サンプル"]
        found = any(kw in readme_text.lower() for kw in keywords)
        assert found, "README must mention that v1 uses demo/sample data"

    def test_readme_mentions_jma_or_live_provider(self, readme_text):
        # jma is the live provider; README must document it or reference live data
        assert "jma" in readme_text.lower() or "気象庁" in readme_text, (
            "README must mention the JMA live provider option"
        )

    def test_readme_mentions_demo_data_flag(self, readme_text):
        assert "demo_data" in readme_text, (
            "README must mention the demo_data field in response messages"
        )

    def test_readme_explains_weather_provider_values(self, readme_text):
        assert "WEATHER_PROVIDER" in readme_text, (
            "README must document valid WEATHER_PROVIDER values"
        )


# ── Foundry hosted endpoint (contracts/websocket-protocol.md) ────────────────


class TestReadmeHostedEndpoint:
    """README must include the Foundry WebSocket endpoint pattern."""

    def test_readme_has_invocations_ws_path(self, readme_text):
        assert "invocations_ws" in readme_text, (
            "README must include the invocations_ws endpoint path"
        )

    def test_readme_has_foundry_features_param(self, readme_text):
        assert "HostedAgents=V1Preview" in readme_text or "foundry_features" in readme_text, (
            "README must document the foundry_features query parameter"
        )
