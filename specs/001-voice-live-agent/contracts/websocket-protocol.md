# Contract: Hosted Weather Voice WebSocket

## Public Hosted Endpoint

When deployed as a Microsoft Foundry hosted agent, callers connect through:

```text
wss://<account>.services.ai.azure.com/api/projects/agents/endpoint/protocols/invocations_ws
  ?project_name=<project>
  &agent_name=<agent>
  &agent_session_id=<session-id>
  &foundry_features=HostedAgents=V1Preview
```

The container receives the proxied upgrade on:

```text
GET /invocations_ws
```

## Authentication Boundary

- The caller sends a Microsoft Entra bearer token on the WebSocket upgrade.
- Foundry validates the token before proxying to the container.
- The container must not depend on receiving the `Authorization` header.
- The container must not accept bearer tokens through query parameters.

## Text Frames

Text frames are UTF-8 JSON control messages.

### Client → Agent: weather text request

```json
{
  "type": "weather.request",
  "text": "今日の東京の天気は？",
  "session_id": "demo-session-1"
}
```

### Agent → Client: weather text response

```json
{
  "type": "weather.response",
  "text": "東京の今日の天気は晴れ時々くもりです。最高気温は26度前後の見込みです。",
  "location": "東京",
  "day": "今日",
  "demo_data": true
}
```

### Agent → Client: clarification

```json
{
  "type": "weather.clarification",
  "text": "どの地域の天気を知りたいですか？",
  "missing": ["location"]
}
```

### Agent → Client: error

```json
{
  "type": "error",
  "message": "Unsupported message type: example.type"
}
```

## Binary Frames

Binary frames represent voice/media data for hosted voice workloads. v1 accepts binary frames and keeps them under the platform 1 MB limit. The sample's automated local tests validate frame-limit and routing behavior without requiring microphone hardware.

## Close Behavior

- Normal shutdown: close code `1000`.
- Unsupported payload: close or error response with policy-safe message.
- Oversized frame: platform may close with `1009`; local implementation should reject frames over 1 MB.
- Preview platform drain may close with `1001`; clients should reconnect with the same `agent_session_id`.
