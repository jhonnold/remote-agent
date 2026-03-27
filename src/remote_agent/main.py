# src/remote_agent/main.py
from __future__ import annotations
import asyncio
import logging
import sys
from dataclasses import dataclass

from remote_agent.config import load_config, Config
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.workspace import WorkspaceManager
from remote_agent.agent import AgentService
from remote_agent.poller import Poller
from remote_agent.dispatcher import Dispatcher
from remote_agent.audit import AuditLogger
from remote_agent.updater import AutoUpdater

logger = logging.getLogger("remote_agent")


@dataclass
class App:
    config: Config
    db: Database
    poller: Poller
    dispatcher: Dispatcher
    audit: AuditLogger | None = None
    updater: AutoUpdater | None = None


async def create_app(config_path: str = "config.yaml") -> App:
    config = load_config(config_path)

    db = await Database.initialize(config.database.path)
    audit = AuditLogger(db, config.logging.audit_file)
    github = GitHubService()
    workspace_mgr = WorkspaceManager(config, github)
    agent_service = AgentService(config, db)
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr, audit=audit)

    updater = AutoUpdater() if config.auto_update.enabled else None

    return App(config=config, db=db, poller=poller, dispatcher=dispatcher, audit=audit, updater=updater)


async def run(config_path: str = "config.yaml"):
    # Phase 1: minimal console logging until config is available
    logging.basicConfig(level=logging.INFO)

    app = await create_app(config_path)

    # Phase 2: reconfigure with structured JSON logging
    from remote_agent.logging_config import setup_logging
    setup_logging(app.config)

    logger.info("Remote agent started. Polling %d repos every %ds.",
                len(app.config.repos), app.config.polling.interval_seconds)

    await app.dispatcher.recover_interrupted_issues()

    try:
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
    finally:
        if app.audit:
            app.audit.close()
        await app.db.close()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
