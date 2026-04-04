# OTel Metrics Export via Claude Agent SDK Instrumentor

**Date:** 2026-04-03
**Status:** Approved

## Goal

Add OpenTelemetry metrics export to the remote-agent service, focused on Claude Agent SDK call telemetry (token usage, model, tool calls, durations). Metrics are exported via OTLP to an external OTel Collector that Prometheus can scrape.

## Decisions

- **Metrics only** (no tracing or log export through OTel for now)
- **OTLP export to external collector** (no in-process HTTP metrics endpoint)
- **Community instrumentor** (`opentelemetry-instrumentation-claude-agent-sdk`) for automatic `query()` instrumentation
- **Config docs only** for collector setup (no docker-compose included)
- **Opt-in** via config toggle (disabled by default)

## Dependencies

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-grpc
opentelemetry-instrumentation-claude-agent-sdk
```

## Architecture

```
main.py startup
  └── setup_telemetry(config)
        ├── if disabled: no-op return
        ├── configure TracerProvider with OTLP exporter
        └── ClaudeAgentSdkInstrumentor().instrument()

agent.py query() calls  ──(auto-patched)──>  OTel spans  ──>  OTLP gRPC  ──>  Collector  ──>  Prometheus
```

### New Module: `src/remote_agent/telemetry.py`

Responsibilities:
1. Read telemetry config (endpoint, service name, enabled flag)
2. Configure `TracerProvider` with `OTLPSpanExporter`
3. Call `ClaudeAgentSdkInstrumentor().instrument()`
4. No-op when telemetry is disabled

### Config Addition

```yaml
telemetry:
  enabled: false                         # opt-in
  otlp_endpoint: "http://localhost:4317" # gRPC OTLP endpoint
  service_name: "remote-agent"
```

New `TelemetryConfig` dataclass in `config.py`.

### Changes to Existing Files

| File | Change |
|------|--------|
| `main.py` | Call `setup_telemetry(config)` after config loads, before poll loop |
| `config.py` | Add `TelemetryConfig` dataclass and `telemetry` field |
| `pyproject.toml` | Add OTel dependencies |
| `agent.py` | No changes (instrumentor patches `query()` automatically) |

### What Gets Emitted

The community instrumentor captures per `query()` call:
- `invoke_agent` spans: model name, input/output token counts, finish reason, duration
- `execute_tool` child spans: tool name, duration, success/failure
- Follows GenAI semantic conventions

## Testing

- Unit test `telemetry.py`: verify instrumentor is called when enabled, no-op when disabled
- Existing tests unaffected since `agent.py` is not modified

## Future Work

- Add custom operational metrics (poll latency, queue depth, phase durations, budget tracking)
- Add distributed tracing with span propagation across phases
- Export structured logs through OTel
