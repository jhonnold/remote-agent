# src/remote_agent/telemetry.py
from __future__ import annotations

import logging

from remote_agent.config import TelemetryConfig

logger = logging.getLogger(__name__)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor

    HAS_OTEL = True
except ImportError:
    HAS_OTEL = False


_initialized = False


def setup_telemetry(config: TelemetryConfig) -> None:
    global _initialized

    if not config.enabled:
        return

    if _initialized:
        return

    if not HAS_OTEL:
        logger.warning(
            "Telemetry enabled but opentelemetry packages not installed. "
            "Install with: pip install -e '.[telemetry]'"
        )
        return

    resource = Resource.create({"service.name": config.service_name})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=config.otlp_endpoint)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    AnthropicInstrumentor().instrument()
    _initialized = True

    logger.info(
        "Telemetry enabled: exporting to %s as %s",
        config.otlp_endpoint,
        config.service_name,
    )
