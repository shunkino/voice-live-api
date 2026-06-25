# Research: Voice-Live Weather Agent

## Decision: Use hosted-agent `invocations_ws` as the deployment-facing protocol

**Rationale**: Microsoft Foundry hosted voice agents expose a single WebSocket route, `GET /invocations_ws`, through the public endpoint `/api/projects/agents/endpoint/protocols/invocations_ws`. The platform validates the Microsoft Entra bearer token during upgrade and relays raw text/binary frames to the container.

**Alternatives considered**:

- HTTP `/invocations`: rejected because voice requires bidirectional streaming.
- Existing local Voice Live bridge only: useful for reference, but it does not demonstrate the new hosted-agent protocol.

## Decision: Keep v1 on direct WebSocket frames, not WebRTC/avatar

**Rationale**: The weather sample only needs a simple voice conversation. Direct WebSocket audio/control frames are easier to test and explain than WebRTC signaling, TURN/SFU behavior, avatar SDP negotiation, and browser media routing.

**Alternatives considered**:

- WebRTC media over WebSocket signaling: rejected for v1 because it adds operational complexity unrelated to the weather-forecasting user story.
- Avatar mode: rejected for v1 because it distracts from hosted voice-agent basics.

## Decision: Default to mock weather data with optional JMA provider

**Rationale**: The spec requires the sample to be demonstrable without production weather-data setup. Mock data keeps local tests deterministic and makes the sample runnable without API keys. A Japan Meteorological Agency provider is a good optional live-data direction because the topic is Japanese weather and JMA data is Japan-specific.

**Alternatives considered**:

- Production paid weather API: rejected because credentials and billing would complicate the sample.
- LLM-generated weather only: rejected because the sample should clearly distinguish demo data from live forecasts.

## Decision: Use Python 3.10+ and a split-ready `hosted-agents/weather-forecast` package

**Rationale**: The repository already uses Python, Azure SDKs, pytest, and Voice Live examples. A self-contained directory keeps the feature easy to branch into another repository while preserving existing sample scripts.

**Alternatives considered**:

- Refactor the whole repository immediately: rejected because unrelated existing demos remain useful and a targeted sample directory is safer.
- Create a separate repo now: rejected because the current task is to implement here first.

## Decision: Use in-memory session state only

**Rationale**: The spec forbids long-term audio/transcript/personal-data storage. The hosted preview can reconnect with the same `agent_session_id`; v1 only needs minimal active-session context such as latest location and latest day.

**Alternatives considered**:

- Persistent database/session store: rejected as unnecessary for a sample.
- No state at all: rejected because follow-up questions such as "明日は？" require the previous location.

## Decision: Configuration must fail fast with actionable messages

**Rationale**: The sample's third user story requires a developer to understand setup quickly. Required values should be validated at startup or client launch, with exact missing names listed.

**Alternatives considered**:

- Lazy failure from Azure SDK calls: rejected because it produces slower, less clear setup feedback.
