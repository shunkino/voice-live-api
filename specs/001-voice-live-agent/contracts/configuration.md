# Contract: Configuration

## Required for local sample

| Name | Required | Default | Description |
|------|----------|---------|-------------|
| `WEATHER_AGENT_NAME` | No | `weather-forecast-agent` | Local display/deployment name |
| `WEATHER_PROVIDER` | No | `mock` | `mock` for deterministic demo data or `jma` for live provider path |
| `WEATHER_AGENT_HOST` | No | `127.0.0.1` | Local server host |
| `WEATHER_AGENT_PORT` | No | `8080` | Local server port |

## Required for Foundry/Voice Live deployment

| Name | Required | Default | Description |
|------|----------|---------|-------------|
| `PROJECT_NAME` | Yes | none | Foundry project name used by hosted endpoint query |
| `AGENT_NAME` | Yes | none | Hosted agent name |
| `AZURE_VOICELIVE_ENDPOINT` | Yes | none | Voice Live / Foundry resource endpoint |
| `MODEL_DEPLOYMENT_NAME` | Yes | `gpt-realtime` | Model deployment used by the voice agent |
| `VOICE_NAME` | No | `ja-JP-NanamiNeural` | Japanese voice used for spoken responses |
| `AZURE_VOICELIVE_TRANSCRIPTION_LANGUAGE` | No | `ja` | Primary speech recognition language |

## Validation Expectations

- Missing required Foundry values must be reported by name before deployment/client connection.
- `WEATHER_PROVIDER` must be rejected unless it is `mock` or `jma`.
- Secret-bearing values must not be printed in full.
