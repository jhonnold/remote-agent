# Self-Updating Agent Design

## Summary

Add an opt-in auto-update mechanism to the remote agent so that, when running as a systemd service, it detects new commits on `main`, pulls them, and restarts itself — without interrupting in-progress work.

## Decisions

- **Update timing**: finish the current poll iteration (poll + dispatch), then check for updates before sleeping. Never interrupts mid-phase work.
- **Restart mechanism**: exit with code 42 after pulling. systemd's `RestartForceExitStatus=42` triggers the restart. Distinguishes update restarts from crashes in journald.
- **Check cadence**: every poll cycle (default 60s). `git fetch` is cheap.
- **Opt-in**: disabled by default. Enabled via `auto_update.enabled: true` in `config.yaml`.
- **CLI preference**: `gh` where possible, `git` for fetch/pull (no `gh` equivalent).

## Config

New optional section in `config.yaml`:

```yaml
auto_update:
  enabled: true
```

Defaults to `enabled: false` when absent. Parsed as `AutoUpdateConfig` dataclass in `config.py`.

## Updater Module — `src/remote_agent/updater.py`

A single class, `AutoUpdater`, operating on the agent's own repo (CWD).

### `async check_for_update() -> bool`

1. `git fetch origin main`
2. Compare `git rev-parse HEAD` vs `git rev-parse origin/main`
3. Return `True` if they differ

### `async pull_update()`

1. `git pull origin main`
2. `pip install -e .` (picks up dependency changes in `pyproject.toml`)

Both use `asyncio.create_subprocess_exec`, matching existing project patterns.

## Main Loop Integration

```python
while True:
    try:
        await app.poller.poll_once()
        await app.dispatcher.process_events()
    except Exception:
        logger.exception("Unexpected error in main loop")

    if app.updater:
        try:
            if await app.updater.check_for_update():
                await app.updater.pull_update()
                logger.info("Update applied, restarting...")
                sys.exit(42)
        except SystemExit:
            raise
        except Exception:
            logger.exception("Update check failed, continuing...")

    await asyncio.sleep(app.config.polling.interval_seconds)
```

- Update check is error-isolated — fetch/pull failures log and continue.
- `SystemExit` re-raised so `sys.exit(42)` isn't swallowed.
- If pull fails, no exit — next cycle retries.
- `app.updater` is `None` when disabled.

## systemd Changes

Add to the service file:

```ini
RestartForceExitStatus=42
```

This ensures exit code 42 triggers a restart even with `Restart=on-failure`.

## Testing

- **Unit tests for `AutoUpdater`**: mock `asyncio.create_subprocess_exec`. Cases: no update, update available, fetch failure, pull failure.
- **Main loop integration**: verify `check_for_update() -> True` triggers `pull_update()` + exit 42. Verify skipped when config disabled.
- Same mock patterns as `github.py` and `workspace.py` tests.

## Not In Scope

- **DB schema migrations**: handled by existing `db.py` init logic, not the updater.
- **Rollback**: if new code crashes, systemd keeps restarting. Manual intervention expected (same as any deployment).
- **Branch selection**: always tracks `main`.
