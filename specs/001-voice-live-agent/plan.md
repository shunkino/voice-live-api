# Implementation Plan: Voice-Live Weather Agent

**Branch**: `[001-voice-live-agent]` | **Date**: 2026-06-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-voice-live-agent/spec.md`

## Summary

Build a self-contained Microsoft Foundry hosted voice-agent sample for Japanese weather forecasting (`天気予報`). The sample will live under `hosted-agents/weather-forecast/`, expose a hosted-agent WebSocket contract for `invocations_ws`, provide a browser/client validation path, and keep weather behavior simple through a mock provider with an optional Japan Meteorological Agency-backed provider.

## Technical Context

**Language/Version**: Python 3.10+

**Primary Dependencies**: `azure-ai-agentserver-invocations` for hosted-agent WebSocket hosting, `azure-ai-voicelive` for Voice Live integration patterns, `azure-ai-projects` and `azure-identity` for Foundry deployment/client auth, `aiohttp` for optional weather provider calls, `python-dotenv` for local configuration

**Storage**: In-memory per-session state only; no persistent storage for audio, transcripts, or personal data

**Testing**: `pytest` with async unit tests for weather provider behavior, configuration validation, protocol message handling, and session-state edge cases

**Target Platform**: Local Linux/macOS/Windows developer environment plus Microsoft Foundry hosted-agent container deployment; hosted `invocations_ws` preview currently requires North Central US

**Project Type**: Self-contained hosted-agent sample with a small web/client validation surface

**Performance Goals**: Keep individual WebSocket frames under 1 MB; support at least one active demo session locally; hosted sizing target is at least 1 vCPU / 2 GiB for voice workloads

**Constraints**: Do not store long-lived audio/transcripts; do not expose bearer tokens to the container; handle missing configuration with actionable errors; keep v1 weather behavior simple and demonstrable without paid weather-data setup

**Scale/Scope**: Sample/demo scope; supports Japanese weather questions for common Japanese cities with mock data by default and optional live provider behavior

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

The project constitution file is still the generated template and does not define enforceable gates. This feature follows the implied Spec Kit quality bar: simple scope, independently testable user stories, explicit configuration, and no unnecessary persistent data storage.

**Gate Status**: PASS — no enforceable constitution violations.

## Project Structure

### Documentation (this feature)

```text
specs/001-voice-live-agent/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── websocket-protocol.md
│   └── configuration.md
└── tasks.md
```

### Source Code (repository root)

```text
hosted-agents/
└── weather-forecast/
    ├── README.md
    ├── requirements.txt
    ├── .env.example
    ├── agent/
    │   ├── __init__.py
    │   ├── app.py
    │   ├── config.py
    │   ├── protocol.py
    │   ├── session_state.py
    │   └── weather.py
    ├── client/
    │   └── text_client.py
    └── tests/
        ├── test_config.py
        ├── test_protocol.py
        ├── test_session_state.py
        └── test_weather.py
```

**Structure Decision**: Create `hosted-agents/weather-forecast/` as a split-ready sample so this feature can become a separate repository later without carrying unrelated experiments. Keep existing root scripts intact for reference, but do not couple new sample code to the current root-level quickstarts.

## Complexity Tracking

No constitution violations or complexity exceptions.

## Phase 0: Research Summary

Research is captured in [research.md](./research.md). Key decisions:

- Use the hosted-agent `invocations_ws` WebSocket protocol as the public hosted interface.
- Use direct WebSocket frames for v1 rather than WebRTC/avatar.
- Use mock weather data by default, with optional JMA live provider shape.
- Keep session state in memory and keyed by session id only.

## Phase 1: Design Summary

Design artifacts:

- [data-model.md](./data-model.md)
- [contracts/websocket-protocol.md](./contracts/websocket-protocol.md)
- [contracts/configuration.md](./contracts/configuration.md)
- [quickstart.md](./quickstart.md)

## Post-Design Constitution Check

**Gate Status**: PASS — the design remains simple, stores no long-lived user data, and keeps external dependencies optional for a runnable demo.
