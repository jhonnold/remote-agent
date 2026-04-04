from __future__ import annotations
from unittest.mock import patch, AsyncMock, MagicMock
import pytest
from remote_agent.config import TelemetryConfig
import remote_agent.telemetry as telemetry_module


@pytest.fixture(autouse=True)
def reset_telemetry_state():
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None
    yield
    telemetry_module._initialized = False
    telemetry_module._server_runner = None
    telemetry_module._server_site = None


def test_record_query_metrics_disabled_is_noop():
    """record_query_metrics should silently do nothing when telemetry is not initialized."""
    telemetry_module.record_query_metrics(
        repo="owner/repo", phase="designing",
        model_usage={"claude-sonnet-4-6": {"inputTokens": 100, "outputTokens": 50, "costUSD": 0.01}},
        duration_ms=1000, duration_api_ms=800,
    )


def test_record_query_metrics_increments_counters():
    """record_query_metrics should increment all metric counters when enabled."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        model_usage = {
            "claude-sonnet-4-6": {
                "inputTokens": 1500,
                "outputTokens": 800,
                "cacheReadInputTokens": 100,
                "cacheCreationInputTokens": 200,
                "costUSD": 0.015,
            },
        }

        telemetry_module.record_query_metrics(
            repo="owner/repo", phase="designing",
            model_usage=model_usage,
            duration_ms=5000, duration_api_ms=4000,
        )

        val = telemetry_module.SESSION_COUNT.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )._value.get()
        assert val == 1.0

        val = telemetry_module.TOKEN_USAGE.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6", type="input",
        )._value.get()
        assert val == 1500.0

        val = telemetry_module.TOKEN_USAGE.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6", type="cacheRead",
        )._value.get()
        assert val == 100.0

        val = telemetry_module.COST_USAGE.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )._value.get()
        assert val == 0.015

        val = telemetry_module.ACTIVE_TIME.labels(
            repo="owner/repo", phase="designing", type="agent",
        )._value.get()
        assert val == 5.0

        val = telemetry_module.ACTIVE_TIME.labels(
            repo="owner/repo", phase="designing", type="api",
        )._value.get()
        assert val == 4.0


def test_record_query_metrics_multiple_models():
    """record_query_metrics should handle multi-model usage (subagents)."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        model_usage = {
            "claude-sonnet-4-6": {
                "inputTokens": 1000, "outputTokens": 500,
                "cacheReadInputTokens": 0, "cacheCreationInputTokens": 0,
                "costUSD": 0.01,
            },
            "claude-opus-4-6[1m]": {
                "inputTokens": 2000, "outputTokens": 1000,
                "cacheReadInputTokens": 500, "cacheCreationInputTokens": 100,
                "costUSD": 0.05,
            },
        }

        telemetry_module.record_query_metrics(
            repo="owner/repo", phase="implementing",
            model_usage=model_usage,
            duration_ms=10000, duration_api_ms=8000,
        )

        val_sonnet = telemetry_module.SESSION_COUNT.labels(
            repo="owner/repo", phase="implementing", model="claude-sonnet-4-6",
        )._value.get()
        val_opus = telemetry_module.SESSION_COUNT.labels(
            repo="owner/repo", phase="implementing", model="claude-opus-4-6[1m]",
        )._value.get()
        assert val_sonnet == 1.0
        assert val_opus == 1.0


def test_record_query_error_increments_counter():
    """record_query_error should increment the error counter."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        telemetry_module.record_query_error(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )

        val = telemetry_module.QUERY_ERRORS.labels(
            repo="owner/repo", phase="designing", model="claude-sonnet-4-6",
        )._value.get()
        assert val == 1.0


def test_record_pr_created_increments_counter():
    """record_pr_created should increment the PR counter."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        telemetry_module.record_pr_created(repo="owner/repo")

        val = telemetry_module.PR_COUNT.labels(repo="owner/repo")._value.get()
        assert val == 1.0


def test_record_phase_transition_increments_counter():
    """record_phase_transition should increment the transition counter."""
    with patch("remote_agent.telemetry.HAS_PROMETHEUS", True):
        telemetry_module._initialized = True

        telemetry_module.record_phase_transition(
            repo="owner/repo", from_phase="designing", to_phase="design_review",
        )

        val = telemetry_module.PHASE_TRANSITIONS.labels(
            repo="owner/repo", from_phase="designing", to_phase="design_review",
        )._value.get()
        assert val == 1.0


def test_setup_telemetry_disabled_is_noop():
    """setup_telemetry should be a no-op when disabled."""
    config = TelemetryConfig(enabled=False)
    telemetry_module.setup_telemetry(config)
    assert not telemetry_module._initialized


def test_setup_telemetry_missing_deps_logs_warning():
    """setup_telemetry should warn when prometheus_client is not installed."""
    config = TelemetryConfig(enabled=True)
    with (
        patch("remote_agent.telemetry.HAS_PROMETHEUS", False),
        patch("remote_agent.telemetry.logger") as mock_logger,
    ):
        telemetry_module.setup_telemetry(config)
        mock_logger.warning.assert_called_once()
        assert not telemetry_module._initialized
