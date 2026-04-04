from __future__ import annotations
import pytest
import aiohttp
import remote_agent.telemetry as telemetry_module
from remote_agent.config import TelemetryConfig


@pytest.fixture(autouse=True)
def reset_telemetry():
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None
    yield
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None


async def test_metrics_endpoint_serves_prometheus_format():
    """The /metrics endpoint should return Prometheus text format with recorded metrics."""
    config = TelemetryConfig(enabled=True, metrics_port=0, service_name="test-agent")
    telemetry_module.setup_telemetry(config)

    # Record some metrics before starting server
    telemetry_module.record_query_metrics(
        repo="owner/repo", phase="designing",
        model_usage={
            "claude-sonnet-4-6": {
                "inputTokens": 100, "outputTokens": 50,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "costUSD": 0.01,
            },
        },
        duration_ms=1000, duration_api_ms=800,
    )

    await telemetry_module.start_metrics_server(config)

    try:
        # Get the actual port assigned by OS (port=0 means ephemeral)
        site = telemetry_module._server_site
        sock = site._server.sockets[0]
        port = sock.getsockname()[1]

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://localhost:{port}/metrics") as resp:
                assert resp.status == 200
                body = await resp.text()

                # Verify metric names present
                assert "remote_agent_session_count_total" in body
                assert "remote_agent_token_usage_total" in body
                assert "remote_agent_cost_usage_total" in body
                assert "remote_agent_active_time_total" in body

                # Verify labels present
                assert 'repo="owner/repo"' in body
                assert 'model="claude-sonnet-4-6"' in body
                assert 'phase="designing"' in body
    finally:
        await telemetry_module.shutdown_telemetry()


async def test_metrics_endpoint_404_on_other_paths():
    """Non-/metrics paths should return 404."""
    config = TelemetryConfig(enabled=True, metrics_port=0, service_name="test-agent")
    telemetry_module.setup_telemetry(config)
    await telemetry_module.start_metrics_server(config)

    try:
        site = telemetry_module._server_site
        sock = site._server.sockets[0]
        port = sock.getsockname()[1]

        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://localhost:{port}/") as resp:
                assert resp.status == 404
    finally:
        await telemetry_module.shutdown_telemetry()
