# Data Model: Voice-Live Weather Agent

## VoiceSession

Represents one active conversation session.

**Fields**:

- `session_id`: Stable identifier supplied by Foundry/session query or generated locally
- `latest_location`: Most recent weather location mentioned by the user
- `latest_day`: Most recent forecast day/time period mentioned by the user
- `turn_count`: Number of user-visible weather turns processed
- `created_at`: Session creation timestamp
- `updated_at`: Last activity timestamp

**Validation Rules**:

- `session_id` must be non-empty.
- `latest_location` is optional until the user provides or confirms a location.
- No audio bytes or full transcripts are stored.

## WeatherRequest

Represents a parsed weather intent.

**Fields**:

- `location`: Requested city/location, either explicit or inferred from session state
- `day`: Forecast day or time period, defaulting to "today" when not specified
- `language`: Primary response language, defaulting to Japanese
- `needs_clarification`: Whether the request lacks required information
- `clarification_prompt`: Japanese prompt to ask when required information is missing

**Validation Rules**:

- A forecast request must have a location before a forecast can be returned.
- If no location is available, `needs_clarification` must be true.

## ForecastResponse

Represents the weather answer returned to the user.

**Fields**:

- `location`: Forecast location
- `day`: Forecast day/time period
- `summary`: Short Japanese forecast summary
- `temperature_c`: Optional temperature in Celsius
- `precipitation_chance`: Optional precipitation chance
- `is_demo_data`: Whether the response comes from deterministic demo data
- `source`: Human-readable source label

**Validation Rules**:

- `summary` must be suitable for spoken output.
- Demo responses must explicitly identify themselves as sample/demo data in developer-facing logs or documentation.

## SampleConfiguration

Represents local or hosted runtime configuration.

**Fields**:

- `agent_name`
- `project_name`
- `foundry_endpoint`
- `model_deployment_name`
- `voice_name`
- `weather_provider`
- `weather_api_base`
- `host`
- `port`

**Validation Rules**:

- Required fields must be validated before runtime startup.
- `weather_provider` must be one of `mock` or `jma`.
- No secret values are required for the default mock provider.

## State Transitions

```text
new session
  -> waiting_for_weather_request
  -> waiting_for_location (if location missing)
  -> answering_forecast (when location resolved)
  -> waiting_for_weather_request
  -> closed
```
