# Weather Forecast Hosted Agent — container image
#
# Build:  docker build -t weather-forecast-agent .
# Run:    docker run -p 8080:8080 --env-file .env weather-forecast-agent
#
# The container exposes GET /invocations_ws for the hosted-agent WebSocket
# and GET /health for liveness probes.
# Target: at least 1 vCPU / 2 GiB (voice workload sizing recommendation).

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source.
COPY agent/ ./agent/

# Non-root user for security.
RUN useradd --create-home appuser && chown -R appuser /app
USER appuser

ENV WEATHER_AGENT_HOST=0.0.0.0
ENV WEATHER_AGENT_PORT=8080
EXPOSE 8080

CMD ["uvicorn", "agent.app:app", "--host", "0.0.0.0", "--port", "8080"]
