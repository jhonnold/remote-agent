# src/remote_agent/telemetry.py
from __future__ import annotations

import logging

from remote_agent.config import TelemetryConfig

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, CollectorRegistry, generate_latest
    from aiohttp import web

    HAS_PROMETHEUS = True
except ImportError:
    HAS_PROMETHEUS = False


_initialized = False
_server_runner: object | None = None
_server_site: object | None = None

# -- Metric definitions (safe to define even if prometheus_client missing) --

if HAS_PROMETHEUS:
    REGISTRY = CollectorRegistry()

    SESSION_COUNT = Counter(
        "remote_agent_session_count_total",
        "Count of agent query sessions",
        ["repo", "phase", "model"],
        registry=REGISTRY,
    )

    TOKEN_USAGE = Counter(
        "remote_agent_token_usage_total",
        "Number of tokens used",
        ["repo", "phase", "model", "type"],
        registry=REGISTRY,
    )

    COST_USAGE = Counter(
        "remote_agent_cost_usage_total",
        "Cost of agent queries in USD",
        ["repo", "phase", "model"],
        registry=REGISTRY,
    )

    ACTIVE_TIME = Counter(
        "remote_agent_active_time_total",
        "Total active time in seconds",
        ["repo", "phase", "type"],
        registry=REGISTRY,
    )

    QUERY_ERRORS = Counter(
        "remote_agent_query_errors_total",
        "Count of failed agent queries",
        ["repo", "phase", "model"],
        registry=REGISTRY,
    )

    PR_COUNT = Counter(
        "remote_agent_pull_request_count_total",
        "Number of pull requests created",
        ["repo"],
        registry=REGISTRY,
    )

    PHASE_TRANSITIONS = Counter(
        "remote_agent_phase_transitions_total",
        "Count of issue phase transitions",
        ["repo", "from_phase", "to_phase"],
        registry=REGISTRY,
    )


# -- Record functions (no-op when not initialized) --


def record_query_metrics(
    *, repo: str, phase: str, model_usage: dict | None,
    duration_ms: int, duration_api_ms: int,
) -> None:
    if not _initialized or not model_usage:
        return

    for model, usage in model_usage.items():
        SESSION_COUNT.labels(repo=repo, phase=phase, model=model).inc()
        COST_USAGE.labels(repo=repo, phase=phase, model=model).inc(
            usage.get("costUSD", 0),
        )
        for token_type, key in [
            ("input", "inputTokens"),
            ("output", "outputTokens"),
            ("cacheRead", "cacheReadInputTokens"),
            ("cacheCreation", "cacheCreationInputTokens"),
        ]:
            TOKEN_USAGE.labels(
                repo=repo, phase=phase, model=model, type=token_type,
            ).inc(usage.get(key, 0))

    ACTIVE_TIME.labels(repo=repo, phase=phase, type="agent").inc(duration_ms / 1000)
    ACTIVE_TIME.labels(repo=repo, phase=phase, type="api").inc(duration_api_ms / 1000)


def record_query_error(*, repo: str, phase: str, model: str) -> None:
    if not _initialized:
        return
    QUERY_ERRORS.labels(repo=repo, phase=phase, model=model).inc()


def record_pr_created(*, repo: str) -> None:
    if not _initialized:
        return
    PR_COUNT.labels(repo=repo).inc()


def record_phase_transition(*, repo: str, from_phase: str, to_phase: str) -> None:
    if not _initialized:
        return
    PHASE_TRANSITIONS.labels(repo=repo, from_phase=from_phase, to_phase=to_phase).inc()


# -- Server lifecycle --


async def _metrics_handler(request: web.Request) -> web.Response:
    body = generate_latest(REGISTRY)
    return web.Response(
        body=body,
        content_type="text/plain; version=0.0.4",
        charset="utf-8",
    )


def setup_telemetry(config: TelemetryConfig) -> None:
    global _initialized

    if not config.enabled:
        return

    if _initialized:
        return

    if not HAS_PROMETHEUS:
        logger.warning(
            "Telemetry enabled but prometheus_client/aiohttp not installed. "
            "Install with: pip install -e '.[telemetry]'"
        )
        return

    _initialized = True
    logger.info(
        "Telemetry enabled: /metrics on port %d as %s",
        config.metrics_port, config.service_name,
    )


async def start_metrics_server(config: TelemetryConfig) -> None:
    global _server_runner, _server_site

    if not _initialized:
        return

    app = web.Application()
    app.router.add_get("/metrics", _metrics_handler)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.metrics_port)
    await site.start()

    _server_runner = runner
    _server_site = site

    logger.info("Metrics server listening on port %d", config.metrics_port)


async def shutdown_telemetry() -> None:
    global _server_runner, _server_site, _initialized

    if _server_runner:
        await _server_runner.cleanup()
        _server_runner = None
        _server_site = None
        logger.info("Metrics server stopped")
