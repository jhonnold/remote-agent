# src/remote_agent/exceptions.py


class RemoteAgentError(Exception):
    """Base exception for the remote agent system."""
    pass


class GitHubError(RemoteAgentError):
    """GitHub CLI operation failed."""
    pass


class GitError(RemoteAgentError):
    """Git operation failed."""
    pass


class AgentError(RemoteAgentError):
    """Claude Agent SDK operation failed."""
    pass


class BudgetExceededError(RemoteAgentError):
    """Daily budget limit reached."""
    pass
