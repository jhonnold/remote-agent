# Remote Agent

An autonomous AI agent that turns GitHub issues into implemented pull requests. It watches your repositories, creates detailed plans, gets your feedback, writes the code, and opens PRs for review — all through natural conversation in GitHub comments.

## How It Works

```
You open an issue          Agent creates a plan         You review and comment
with the "agent" label --> as a draft PR            --> on the plan
                                                         |
                     You review the code <-- Agent    <--+-- You approve
                     and comment on PR       implements       the plan
                           |                 the plan
                           |
                           +--> You approve --> You merge
```

1. **You open an issue** in a tracked repo with the `agent` label
2. **The agent creates a plan** — a detailed markdown document committed to the repo and opened as a draft PR
3. **You review the plan** by commenting on the PR. The agent interprets your feedback naturally — no special commands needed. Say "looks good" to approve, or describe what to change
4. **The agent implements the plan** using AI subagents for coding, with automated spec compliance and code quality reviews between each task
5. **The PR is published** for your review. Comment with feedback, approve to finish, or ask it to go back to planning
6. **You merge** when satisfied

The agent understands natural language comments. "LGTM", "ship it", "change the approach to X", "why did you choose Y?", and "go back to planning" all work as you'd expect.

## Requirements

- Python 3.11+
- [GitHub CLI](https://cli.github.com/) (`gh`) installed and authenticated
- An [Anthropic API key](https://console.anthropic.com/)

## Setup

```bash
# Clone and install
git clone <repo-url> remote-agent
cd remote-agent
pip install -e ".[dev]"

# Set your API key
export ANTHROPIC_API_KEY=your-key-here
```

Edit `config.yaml` with your repositories and GitHub username:

```yaml
repos:
  - owner: "your-github-username"
    name: "your-repo"

users:
  - "your-github-username"

polling:
  interval_seconds: 60

trigger:
  label: "agent"

workspace:
  base_dir: "/home/you/workspaces"

database:
  path: "data/agent.db"

agent:
  default_model: "sonnet"
  planning_model: "opus"
  implementation_model: "sonnet"
  orchestrator_model: "haiku"
  review_model: "sonnet"
  max_turns: 200
  max_budget_usd: 10.0
  daily_budget_usd: 50.0
```

## Running

```bash
python3 -m remote_agent.main
```

The agent starts polling your configured repos every 60 seconds (configurable). It runs until you stop it with Ctrl+C. Logs are written to both stdout and `remote-agent.log` (rotating, 10MB, 3 backups).

On startup, it recovers any issues that were interrupted by a previous shutdown.

### Running as a systemd Service

For persistent operation, run the agent as a systemd user service so it starts on boot and restarts on failure.

**1. Create the service file:**

```bash
mkdir -p ~/.config/systemd/user

cat > ~/.config/systemd/user/remote-agent.service << 'EOF'
[Unit]
Description=Remote Agent - Autonomous GitHub Issue Handler
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/path/to/remote-agent
ExecStart=/path/to/python3 -m remote_agent.main
Restart=on-failure
RestartSec=10

# Hardening
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/path/to/workspaces /path/to/remote-agent

[Install]
WantedBy=default.target
EOF
```

**2. Enable and start the service:**

```bash
systemctl --user daemon-reload
systemctl --user enable remote-agent.service
systemctl --user start remote-agent.service
```

**3. Enable lingering** so the service runs even when you're not logged in:

```bash
sudo loginctl enable-linger $USER
```

**4. Check status and logs:**

```bash
systemctl --user status remote-agent.service
journalctl --user -u remote-agent.service -f
```

Logs go to both journald and the rotating `remote-agent.log` file in the working directory.

**5. Restart after config changes:**

```bash
systemctl --user restart remote-agent.service
```

## Configuration Reference

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `repos` | `owner`, `name` | — | GitHub repositories to watch (at least one required) |
| `users` | — | — | GitHub usernames allowed to trigger the agent (at least one required) |
| `polling` | `interval_seconds` | `60` | How often to check GitHub for new issues/comments |
| `trigger` | `label` | `"agent"` | Issue label that activates the agent |
| `workspace` | `base_dir` | `/home/claude/workspaces` | Where target repos are checked out |
| `database` | `path` | `data/agent.db` | SQLite database location (relative to config file) |
| `agent.default_model` | — | `"sonnet"` | Default model for agent work |
| `agent.planning_model` | — | `"opus"` | Model for creating plans (benefits from strong reasoning) |
| `agent.implementation_model` | — | `"sonnet"` | Model for code implementation subagents |
| `agent.orchestrator_model` | — | `"haiku"` | Model for the implementation orchestrator (delegates, doesn't code) |
| `agent.review_model` | — | `"sonnet"` | Model for interpreting PR comments |
| `agent.max_turns` | — | `200` | Max agent turns per invocation |
| `agent.max_budget_usd` | — | `10.0` | Max spend per single agent invocation |
| `agent.daily_budget_usd` | — | `50.0` | Aggregate daily spend cap across all invocations |

## Issue Lifecycle

Each issue moves through these phases:

| Phase | What's happening | Your role |
|-------|-----------------|-----------|
| **Planning** | Agent explores the codebase and writes a plan | Wait |
| **Plan Review** | Draft PR open with the plan document | Comment with feedback or approve |
| **Implementing** | Agent codes the solution using subagents with automated reviews | Wait |
| **Code Review** | PR published with code changes | Comment with feedback, approve, or send back to planning |
| **Completed** | You approved the code | Merge the PR |

If anything goes wrong, the agent posts the error to the PR and waits for you to comment "retry".

## Architecture

```
Poller --> Events DB --> Dispatcher --> Phase Handlers --> Agent Service --> Claude SDK
  |                        |               |                    |
  +-- gh CLI          error handling   planning.py         query()
  +-- comment          budget gate     plan_review.py      subagents
      detection        phase routing   implementation.py   custom tools
                                       code_review.py
```

The system is built as a polling loop with an event-driven state machine:

- **Poller** checks GitHub for new issues and PR comments, creates events in SQLite
- **Dispatcher** reads events and routes them to the correct phase handler, with budget gating and error recovery
- **Phase Handlers** execute the business logic for each phase (plan, review, implement, code review)
- **Agent Service** wraps the Claude Agent SDK, managing sessions, subagents, and cost tracking
- **GitHub Service** wraps the `gh` CLI for all GitHub API operations
- **Workspace Manager** handles repo checkouts, branching, and git operations

### How Implementation Works

During implementation, the agent uses a multi-agent architecture:

1. An **orchestrator** (haiku) reads the plan and dispatches tasks sequentially
2. For each task, an **implementer** subagent (sonnet) writes code following TDD
3. A **spec reviewer** subagent verifies the implementation matches the plan exactly
4. A **code quality reviewer** subagent checks for clean code, good tests, and correct patterns
5. Issues found in review are sent back to the implementer (max 3 iterations)

## Project Structure

```
src/remote_agent/
  main.py           Entry point and polling loop
  config.py         YAML config loading with validation
  models.py         Data models (Issue, Event, AgentRun, PhaseResult)
  exceptions.py     Exception hierarchy (GitHubError, GitError, AgentError, etc.)
  db.py             SQLite persistence layer
  github.py         GitHub CLI wrapper
  workspace.py      Repo checkout management
  agent.py          Claude Agent SDK integration
  poller.py         GitHub polling for issues/comments
  dispatcher.py     Event routing and error handling
  phases/
    base.py         PhaseHandler protocol
    planning.py     Creates plan documents
    plan_review.py  Interprets plan feedback
    implementation.py  Orchestrates code implementation
    code_review.py  Interprets code feedback
  prompts/
    planning.py     System prompts for planning
    implementation.py  System prompts for implementation
    review.py       System prompts for comment interpretation
```

## Testing

```bash
# Run all tests
pytest

# Run with verbose output
pytest -v

# Run a specific test file
pytest tests/test_poller.py -v
```

Tests cover all modules, including a full lifecycle integration test.

## Design Documents

- [System Design Spec](docs/superpowers/specs/2026-03-26-github-agent-design.md) — full architecture, state machine, database schema, and component designs
- [Implementation Plan](docs/superpowers/plans/2026-03-26-github-agent-implementation.md) — task-by-task build plan with TDD steps

## Limitations

- Processes one issue at a time across all repos (sequential, not parallel)
- Polling pauses during active agent work (no new issues detected while implementing)
- Comment polling uses GitHub's issues API (regular PR comments only, not formal review comments)
- Daily budget tracking may undercount on errored runs
- No web UI — all interaction happens through GitHub issues and PR comments

## License

This is a personal project. No license file has been added yet.
