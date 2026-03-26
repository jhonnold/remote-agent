# src/remote_agent/main.py
from __future__ import annotations
import asyncio
import logging
import logging.handlers
from dataclasses import dataclass

from remote_agent.config import load_config, Config
from remote_agent.db import Database
from remote_agent.github import GitHubService
from remote_agent.workspace import WorkspaceManager
from remote_agent.agent import AgentService
from remote_agent.poller import Poller
from remote_agent.dispatcher import Dispatcher

logger = logging.getLogger("remote_agent")


@dataclass
class App:
    config: Config
    db: Database
    poller: Poller
    dispatcher: Dispatcher


async def create_app(config_path: str = "config.yaml") -> App:
    config = load_config(config_path)

    db = await Database.initialize(config.database.path)
    github = GitHubService()
    workspace_mgr = WorkspaceManager(config, github)
    agent_service = AgentService(config, db)
    poller = Poller(config, db, github)
    dispatcher = Dispatcher(config, db, github, agent_service, workspace_mgr)

    return App(config=config, db=db, poller=poller, dispatcher=dispatcher)


async def run(config_path: str = "config.yaml"):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                "remote-agent.log", maxBytes=10_000_000, backupCount=3,
            ),
        ],
    )

    app = await create_app(config_path)

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
            await asyncio.sleep(app.config.polling.interval_seconds)
    finally:
        await app.db.close()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Shutting down.")


if __name__ == "__main__":
    main()
