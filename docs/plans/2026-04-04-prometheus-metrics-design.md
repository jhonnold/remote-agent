# Prometheus Metrics via In-Process /metrics Endpoint

**Date:** 2026-04-04
**Status:** Approved
**Supersedes:** 2026-04-03-otel-metrics-design.md (OTLP push model)

## Goal

Replace the OTLP gRPC push telemetry (PR #13) with an in-process `/metrics` HTTP endpoint that Prometheus can scrape directly. The service runs on an isolated VM with no OTel Collector, making push-based export unworkable.

## Decisions

- **Drop entire OTel stack** — TracerProvider, OTLP exporter, BatchSpanProcessor, AnthropicInstrumentor all removed
- **prometheus_client + aiohttp** for metric definitions and HTTP serving
- **Explicit instrumentation** in `agent.py._run_query()` using `ResultMessage.model_usage` for per-model token/cost breakdown
- **Metric names follow `remote_agent_*` convention**, aligned with Claude Code's `claude_code_*` metric schema (same counter types, same token type labels, same model labels)
- **Telemetry module owns the HTTP server lifecycle** — `setup_telemetry()` starts it, `shutdown_telemetry()` stops it
- **No high-cardinality labels** — no `session_id`, `issue_number`, or user identity labels in Prometheus

## Metrics

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `remote_agent_session_count_total` | Counter | `repo`, `phase`, `model` | Each `_run_query` completion (model = orchestrator model) |
| `remote_agent_token_usage_total` | Counter | `repo`, `phase`, `model`, `type` | `model_usage` from `ResultMessage`; `type` ∈ {`input`, `output`, `cacheRead`, `cacheCreation`} |
| `remote_agent_cost_usage_total` | Counter | `repo`, `phase`, `model` | `model_usage[model]["costUSD"]` per model (unit: USD) |
| `remote_agent_active_time_total` | Counter | `repo`, `phase`, `type` | `ResultMessage.duration_ms` / 1000 (`type=agent`), `duration_api_ms` / 1000 (`type=api`) |
| `remote_agent_query_errors_total` | Counter | `repo`, `phase`, `model` | `_run_query` exception path |
| `remote_agent_pull_request_count_total` | Counter | `repo` | `github.create_pr` calls |
| `remote_agent_phase_transitions_total` | Counter | `repo`, `from_phase`, `to_phase` | Dispatcher phase changes |

### Label values

- **`repo`**: `"owner/name"` from config
- **`phase`**: `designing`, `planning`, `implementing`, `design_review`, `code_review`, `{context}_question`
- **`model`**: model name string from `model_usage` keys (e.g. `claude-sonnet-4-6`, `claude-opus-4-6[1m]`)
- **`type`** (token_usage): `input`, `output`, `cacheRead`, `cacheCreation` — matches Claude Code's label values
- **`type`** (active_time): `agent`, `api` — derived from `duration_ms` and `duration_api_ms`

### Data source: `ResultMessage.model_usage`

The SDK's `model_usage` dict is keyed by model name with camelCase inner keys:

```python
{
    "claude-sonnet-4-6": {
        "inputTokens": 1500,
        "outputTokens": 800,
        "cacheReadInputTokens": 100,
        "cacheCreationInputTokens": 200,
        "costUSD": 0.015,
    }
}
```

This gives per-model breakdown including cache token types, matching Claude Code's format.

## Architecture

```
telemetry.py
├── Module-level prometheus_client metric objects
├── record_query_metrics(repo, phase, model_usage, duration_ms, duration_api_ms)
├── record_query_error(repo, phase, model)
├── record_pr_created(repo)
├── record_phase_transition(repo, from_phase, to_phase)
├── setup_telemetry(config) → starts aiohttp server on config.metrics_port
└── shutdown_telemetry() → stops the aiohttp server

agent.py._run_query()
├── Extract model_usage from ResultMessage (new field access)
├── Extract duration_ms, duration_api_ms from ResultMessage (new)
└── Call telemetry.record_query_metrics() on success
└── Call telemetry.record_query_error() on exception

github.py.create_pr()
└── Call telemetry.record_pr_created()

dispatcher.py (phase transitions)
└── Call telemetry.record_phase_transition()

main.py
├── setup_telemetry(config.telemetry) at startup (existing call site)
└── shutdown_telemetry() in finally block (new)
```

## Config Changes

### Before (PR #13)

```python
@dataclass
class TelemetryConfig:
    enabled: bool = False
    otlp_endpoint: str = "http://localhost:4317"
    service_name: str = "remote-agent"
```

### After

```python
@dataclass
class TelemetryConfig:
    enabled: bool = False
    metrics_port: int = 9090
    service_name: str = "remote-agent"
```

```yaml
telemetry:
  enabled: false
  metrics_port: 9090
  service_name: "remote-agent"
```

## Dependency Changes

### Remove (pyproject.toml `[telemetry]` extra)

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-grpc
opentelemetry-instrumentation-anthropic
```

### Add

```
prometheus_client>=0.20.0
aiohttp>=3.9.0
```

## File Changes Summary

| File | Change |
|------|--------|
| `telemetry.py` | Complete rewrite: OTel → prometheus_client + aiohttp server |
| `config.py` | `TelemetryConfig`: `otlp_endpoint` → `metrics_port: int = 9090` |
| `agent.py` | `_run_query`: extract `model_usage`, `duration_ms`, `duration_api_ms`; call `record_query_metrics`/`record_query_error` |
| `github.py` | `create_pr`: call `record_pr_created` |
| `dispatcher.py` | Phase transitions: call `record_phase_transition` |
| `main.py` | Add `shutdown_telemetry()` in finally block |
| `pyproject.toml` | Swap OTel deps for prometheus_client + aiohttp |
| `tests/test_telemetry.py` | Rewrite to test new metrics + HTTP server |

## Testing

- **telemetry.py**: verify metrics are registered, `record_*` functions increment counters, aiohttp server starts/stops
- **agent.py**: verify `record_query_metrics` called with correct model_usage data after query
- **Existing tests**: mock `telemetry.record_*` calls (they're no-ops when telemetry disabled anyway)
- Integration: hit `/metrics` endpoint, verify Prometheus text format output

## Not In Scope

- `lines_of_code_count_total` — edits happen inside SDK sessions, we don't see diffs
- `commit_count_total` — commits happen inside agent sessions opaquely
- `code_edit_tool_decision_total` — no interactive permission decisions (headless agent)
- `num_turns` tracking — available on ResultMessage but not in Claude Code's metric set
