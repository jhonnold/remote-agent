# tests/test_models.py
from remote_agent.models import Issue, Event, AgentRun, PhaseResult


def test_issue_creation():
    issue = Issue(
        id=1,
        repo_owner="owner",
        repo_name="repo",
        issue_number=42,
        title="Test issue",
        body="Issue body",
        phase="new",
    )
    assert issue.repo_owner == "owner"
    assert issue.phase == "new"
    assert issue.design_approved is False
    assert issue.pr_number is None


def test_issue_design_approved_default():
    """design_approved defaults to False."""
    issue = Issue(
        id=1, repo_owner="o", repo_name="r",
        issue_number=1, title="t", body=None, phase="new",
    )
    assert issue.design_approved is False


def test_issue_design_approved_accepts_value():
    """design_approved can be set to True."""
    issue = Issue(
        id=1, repo_owner="o", repo_name="r",
        issue_number=1, title="t", body=None, phase="new",
        design_approved=True,
    )
    assert issue.design_approved is True


def test_issue_design_commit_hash_default():
    """design_commit_hash defaults to None."""
    issue = Issue(
        id=1, repo_owner="o", repo_name="r",
        issue_number=1, title="t", body=None, phase="new",
    )
    assert issue.design_commit_hash is None


def test_issue_design_commit_hash_accepts_value():
    """design_commit_hash can be set to a string."""
    issue = Issue(
        id=1, repo_owner="o", repo_name="r",
        issue_number=1, title="t", body=None, phase="new",
        design_commit_hash="abc123",
    )
    assert issue.design_commit_hash == "abc123"


def test_issue_plan_path_default():
    """plan_path defaults to None."""
    issue = Issue(
        id=1, repo_owner="o", repo_name="r",
        issue_number=1, title="t", body=None, phase="new",
    )
    assert issue.plan_path is None


def test_issue_plan_path_accepts_value():
    """plan_path can be set to a string."""
    issue = Issue(
        id=1, repo_owner="o", repo_name="r",
        issue_number=1, title="t", body=None, phase="new",
        plan_path="docs/plan.md",
    )
    assert issue.plan_path == "docs/plan.md"


def test_event_creation():
    event = Event(
        id=1,
        issue_id=1,
        event_type="new_issue",
        payload={"number": 42, "title": "Test"},
    )
    assert event.event_type == "new_issue"
    assert event.processed is False


def test_phase_result():
    result = PhaseResult(next_phase="design_review")
    assert result.next_phase == "design_review"
    assert result.error_message is None


def test_agent_run_creation():
    run = AgentRun(id=1, issue_id=1, phase="planning")
    assert run.result is None
    assert run.cost_usd == 0.0
