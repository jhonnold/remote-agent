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
    assert issue.plan_approved is False
    assert issue.pr_number is None


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
    result = PhaseResult(next_phase="plan_review")
    assert result.next_phase == "plan_review"
    assert result.error_message is None


def test_agent_run_creation():
    run = AgentRun(id=1, issue_id=1, phase="planning")
    assert run.result is None
    assert run.cost_usd == 0.0
