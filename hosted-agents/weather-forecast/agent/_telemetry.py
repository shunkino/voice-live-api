"""Optional OpenTelemetry instrumentation for outbound aiohttp calls.

The Foundry hosted platform configures the tracer provider and Azure Monitor
exporter when ``APPLICATIONINSIGHTS_CONNECTION_STRING`` is injected. The core
protocol library auto-instruments the OpenAI client and ``requests``, but not
``aiohttp`` — which is what the agent uses to call the weather API (Open-Meteo).

Calling :func:`instrument_aiohttp` registers the aiohttp client instrumentor so
each outbound weather-API request becomes a dependency span (visible in
Application Insights / Log Analytics ``AppDependencies``). It is best-effort and
idempotent: if the instrumentation package isn't installed it is silently
skipped, so local/offline runs are unaffected.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_instrumented = False


def instrument_aiohttp() -> None:
    """Instrument the aiohttp client for OpenTelemetry tracing (best-effort)."""
    global _instrumented
    if _instrumented:
        return
    try:
        from opentelemetry.instrumentation.aiohttp_client import (  # noqa: PLC0415
            AioHttpClientInstrumentor,
        )

        AioHttpClientInstrumentor().instrument()
        _instrumented = True
        logger.info("aiohttp client OpenTelemetry instrumentation enabled")
    except Exception as exc:  # noqa: BLE001
        logger.info("aiohttp instrumentation not enabled: %s", exc)
