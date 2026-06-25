# Quickstart: Voice-Live Weather Agent

## Prerequisites

- Python 3.10+
- Azure CLI login for hosted/Voice Live validation
- Microsoft Foundry project in a region that supports hosted `invocations_ws` preview
- Microphone and speaker for end-to-end voice validation

## 1. Install dependencies

```bash
cd hosted-agents/weather-forecast
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 2. Run local mock validation

```bash
pytest
python -m agent.app
```

Expected result:

- The local hosted-agent-compatible server starts.
- Missing/invalid configuration is reported clearly.
- Mock weather requests return Japanese weather responses.

## 3. Try text WebSocket validation

In another terminal:

```bash
python client/text_client.py --text "今日の東京の天気は？"
python client/text_client.py --text "今日の天気は？"
```

Expected result:

- The first request receives a Japanese weather response for Tokyo.
- The second request receives a Japanese clarification asking for the location.

## 4. Configure Foundry/Voice Live deployment values

Copy the sample environment file:

```bash
cp .env.example .env
```

Set at least:

```env
PROJECT_NAME=<your-foundry-project-name>
AGENT_NAME=<your-hosted-agent-name>
AZURE_VOICELIVE_ENDPOINT=https://<resource>.services.ai.azure.com/
MODEL_DEPLOYMENT_NAME=gpt-realtime
VOICE_NAME=ja-JP-NanamiNeural
AZURE_VOICELIVE_TRANSCRIPTION_LANGUAGE=ja
```

## 5. Hosted endpoint validation

After the container is deployed as a hosted agent with the `invocations_ws` protocol, connect with:

```text
wss://<account>.services.ai.azure.com/api/projects/agents/endpoint/protocols/invocations_ws?project_name=<project>&agent_name=<agent>&agent_session_id=demo-session-1&foundry_features=HostedAgents=V1Preview
```

Expected result:

- The WebSocket upgrade succeeds with Microsoft Entra authentication.
- Japanese weather conversation works for supported city questions.
- Reconnecting with the same `agent_session_id` preserves minimal weather context during the active session.

## 6. Scope notes

- v1 uses mock weather data by default.
- Live weather can be enabled later through the `jma` provider path.
- Avatar/WebRTC support is intentionally out of scope for v1.
