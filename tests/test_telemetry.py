from __future__ import annotations
from unittest.mock import patch, MagicMock
import pytest
from remote_agent.config import TelemetryConfig
from remote_agent.telemetry import setup_telemetry
import remote_agent.telemetry as telemetry_module


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    telemetry_module._initialized = False
    yield
    telemetry_module._initialized = False


def test_setup_telemetry_disabled_is_noop():
    config = TelemetryConfig(enabled=False)
    with patch("remote_agent.telemetry.HAS_OTEL", True), \
         patch("remote_agent.telemetry.TracerProvider") as mock_tp:
        setup_telemetry(config)
        mock_tp.assert_not_called()


def test_setup_telemetry_enabled_configures_provider():
    config = TelemetryConfig(
        enabled=True,
        otlp_endpoint="http://localhost:4317",
        service_name="test-agent",
    )
    mock_instrumentor = MagicMock()
    with (
        patch("remote_agent.telemetry.TracerProvider") as mock_tp_cls,
        patch("remote_agent.telemetry.OTLPSpanExporter") as mock_exporter_cls,
        patch("remote_agent.telemetry.BatchSpanProcessor") as mock_bsp_cls,
        patch("remote_agent.telemetry.trace") as mock_trace,
        patch("remote_agent.telemetry.AnthropicInstrumentor", return_value=mock_instrumentor) as mock_instr_cls,
        patch("remote_agent.telemetry.Resource") as mock_resource_cls,
    ):
        setup_telemetry(config)

        # Verify provider was created and set
        mock_tp_cls.assert_called_once()
        mock_trace.set_tracer_provider.assert_called_once()

        # Verify exporter uses configured endpoint
        mock_exporter_cls.assert_called_once_with(endpoint="http://localhost:4317")

        # Verify instrumentor was called
        mock_instrumentor.instrument.assert_called_once()


def test_setup_telemetry_missing_deps_logs_warning():
    config = TelemetryConfig(enabled=True)
    with (
        patch("remote_agent.telemetry.HAS_OTEL", False),
        patch("remote_agent.telemetry.logger") as mock_logger,
    ):
        setup_telemetry(config)
        mock_logger.warning.assert_called_once()
