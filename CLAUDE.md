# Remote Agent - Claude Code Context

## Commands
- **Requires**: `ANTHROPIC_API_KEY` env var and a configured `config.yaml`
- `python3 -m remote_agent.main` - run the agent (not `python`)
- `pytest` or `pytest -v` - run all tests
- `pytest tests/test_integration.py -v` - full lifecycle integration test
- `pip install -e ".[dev]"` - install with dev dependencies

## Architecture
- **Entry**: `src/remote_agent/main.py` -> poll loop -> dispatch events -> phase handlers
- **State machine**: new -> designing -> design_review -> planning -> implementing -> code_review -> completed
- **All GitHub ops**: via `gh` CLI subprocess (`github.py`), never httpx/REST
- **All AI ops**: via `claude-agent-sdk` query() in `agent.py` - sole module importing the SDK
- **Prompts**: centralized in `prompts/` — `subagents.py` has all 8 sub-agent role prompts
- To add a new phase: extend `PhaseHandler` in `phases/base.py`, add to dispatcher's `__init__` and `_get_handler`

## Code Patterns
- `from __future__ import annotations` in all modules for `str | None` syntax
- aiosqlite transactions: explicit `BEGIN`/`COMMIT`/`ROLLBACK` in try/except, not context manager
- SDK imports deferred inside methods in `agent.py` to allow testing without claude-agent-sdk installed
- `WorkspaceManager.cleanup()` is intentionally synchronous (shutil.rmtree) - causes benign AsyncMock warning in tests

## Style
- Commits: conventional format — `feat:`, `fix:`, `docs:`, `chore:`
- Async tests: pytest-asyncio `asyncio_mode = "auto"` — no `@pytest.mark.asyncio` markers needed
- Build backend: `setuptools.build_meta` (not `setuptools.backends._legacy:_Backend`)

## Testing
- All external I/O mocked: `asyncio.create_subprocess_exec` for gh/git, `claude_agent_sdk.query` for SDK
- DB tests use real SQLite via `tmp_path` fixture
- Phase handler tests use `AsyncMock` for all dependencies
- Integration test wires real DB + mocked GitHub/Agent services for full lifecycle
