# tests/test_db.py
import pytest
from remote_agent.db import Database


@pytest.fixture
async def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = await Database.initialize(db_path)
    yield database
    await database.close()


async def test_create_and_get_issue(db):
    issue_id = await db.create_issue(
        repo_owner="owner", repo_name="repo",
        issue_data={"number": 42, "title": "Test", "body": "Body"}
    )
    issue = await db.get_issue("owner", "repo", 42)
    assert issue is not None
    assert issue.id == issue_id
    assert issue.title == "Test"
    assert issue.phase == "new"


async def test_create_duplicate_issue_ignored(db):
    await db.create_issue("owner", "repo", {"number": 1, "title": "A", "body": ""})
    # Second create with same repo/issue should return None (already exists)
    result = await db.create_issue("owner", "repo", {"number": 1, "title": "A", "body": ""})
    assert result is None


async def test_create_and_get_event(db):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.create_event(issue_id, "new_issue", {"number": 1})
    events = await db.get_unprocessed_events()
    assert len(events) == 1
    assert events[0].event_type == "new_issue"


async def test_mark_event_processed(db):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.create_event(issue_id, "new_issue", {})
    events = await db.get_unprocessed_events()
    await db.mark_event_processed(events[0].id)
    events = await db.get_unprocessed_events()
    assert len(events) == 0


async def test_update_issue_phase(db):
    issue_id = await db.create_issue("owner", "repo", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "planning")
    issue = await db.get_issue("owner", "repo", 1)
    assert issue.phase == "planning"


async def test_get_issues_awaiting_comment(db):
    id1 = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    id2 = await db.create_issue("o", "r", {"number": 2, "title": "T2", "body": ""})
    id3 = await db.create_issue("o", "r", {"number": 3, "title": "T3", "body": ""})
    await db.update_issue_phase(id1, "design_review")
    await db.update_issue_phase(id2, "implementing")
    await db.update_issue_phase(id3, "error")
    review_issues = await db.get_issues_awaiting_comment("o", "r")
    assert len(review_issues) == 2  # design_review + error
    phases = {i.phase for i in review_issues}
    assert phases == {"design_review", "error"}


async def test_create_and_complete_agent_run(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    run_id = await db.create_agent_run(issue_id, "planning")
    await db.complete_agent_run(run_id, session_id="sess-123", result="success", cost_usd=1.5)
    run = await db.get_agent_run(run_id)
    assert run.session_id == "sess-123"
    assert run.cost_usd == 1.5


async def test_get_daily_spend(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    run_id = await db.create_agent_run(issue_id, "planning")
    await db.complete_agent_run(run_id, result="success", cost_usd=5.0)
    daily = await db.get_daily_spend()
    assert daily == 5.0


async def test_transaction_for_comments(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "design_review")
    await db.update_issue_pr(issue_id, 10)
    comments = [{"id": 100, "body": "LGTM"}, {"id": 101, "body": "Change X"}]
    await db.create_comment_events(issue_id, comments)
    events = await db.get_unprocessed_events()
    assert len(events) == 2
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_comment_id == 101


async def test_issue_has_last_review_id(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_review_id == 0


async def test_create_review_events(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "design_review")
    await db.update_issue_pr(issue_id, 10)
    reviews = [
        {"id": 500, "body": "Change X"},
        {"id": 501, "body": "Also fix Y"},
    ]
    await db.create_review_events(issue_id, reviews)
    events = await db.get_unprocessed_events()
    assert len(events) == 2
    assert all(e.event_type == "new_comment" for e in events)
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_review_id == 501


async def test_issue_has_closed_seen_and_issue_comment_fields(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    issue = await db.get_issue("o", "r", 1)
    assert issue.issue_closed_seen is False
    assert issue.last_issue_comment_id == 0


async def test_mark_issue_closed(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.mark_issue_closed(issue_id, last_issue_comment_id=500)
    issue = await db.get_issue("o", "r", 1)
    assert issue.issue_closed_seen is True
    assert issue.last_issue_comment_id == 500


async def test_clear_issue_for_reopen(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_issue_phase(issue_id, "completed")
    await db.update_issue_pr(issue_id, 10)
    await db.update_issue_branch(issue_id, "agent/issue-1")
    await db.set_design_commit_hash(issue_id, "abc123")
    await db.update_issue_workspace(issue_id, "/tmp/ws")
    await db.set_plan_path(issue_id, "docs/plan.md")
    await db.update_last_comment_id(issue_id, 100)
    await db.mark_issue_closed(issue_id, last_issue_comment_id=500)

    await db.clear_issue_for_reopen(issue_id)

    issue = await db.get_issue("o", "r", 1)
    assert issue.pr_number is None
    assert issue.branch_name is None
    assert issue.design_commit_hash is None
    assert issue.plan_path is None
    assert issue.workspace_path is None
    assert issue.last_comment_id == 0
    assert issue.last_review_id == 0
    assert issue.issue_closed_seen is False
    assert issue.last_issue_comment_id == 0


async def test_get_completed_or_error_issues(db):
    id1 = await db.create_issue("o", "r", {"number": 1, "title": "T1", "body": ""})
    id2 = await db.create_issue("o", "r", {"number": 2, "title": "T2", "body": ""})
    id3 = await db.create_issue("o", "r", {"number": 3, "title": "T3", "body": ""})
    id4 = await db.create_issue("o", "r", {"number": 4, "title": "T4", "body": ""})
    await db.update_issue_phase(id1, "completed")
    await db.update_issue_phase(id2, "error")
    await db.update_issue_phase(id3, "planning")
    await db.update_issue_phase(id4, "completed")
    issues = await db.get_completed_or_error_issues("o", "r")
    assert len(issues) == 3
    phases = {i.phase for i in issues}
    assert phases == {"completed", "error"}


async def test_update_last_issue_comment_id(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.update_last_issue_comment_id(issue_id, 999)
    issue = await db.get_issue("o", "r", 1)
    assert issue.last_issue_comment_id == 999


async def test_set_design_approved(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.set_design_approved(issue_id, True)
    issue = await db.get_issue("o", "r", 1)
    assert issue.design_approved is True


async def test_set_design_commit_hash(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.set_design_commit_hash(issue_id, "abc123")
    issue = await db.get_issue("o", "r", 1)
    assert issue.design_commit_hash == "abc123"


async def test_set_plan_path(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.set_plan_path(issue_id, "docs/plan.md")
    issue = await db.get_issue("o", "r", 1)
    assert issue.plan_path == "docs/plan.md"


async def test_clear_plan_path(db):
    issue_id = await db.create_issue("o", "r", {"number": 1, "title": "T", "body": ""})
    await db.set_plan_path(issue_id, "docs/plan.md")
    issue = await db.get_issue("o", "r", 1)
    assert issue.plan_path == "docs/plan.md"
    await db.clear_plan_path(issue_id)
    issue = await db.get_issue("o", "r", 1)
    assert issue.plan_path is None


async def test_get_issues_awaiting_comment_includes_design_review(db):
    id1 = await db.create_issue("o", "r", {"number": 1, "title": "T1", "body": ""})
    id2 = await db.create_issue("o", "r", {"number": 2, "title": "T2", "body": ""})
    id3 = await db.create_issue("o", "r", {"number": 3, "title": "T3", "body": ""})
    await db.update_issue_phase(id1, "design_review")
    await db.update_issue_phase(id2, "code_review")
    await db.update_issue_phase(id3, "implementing")
    review_issues = await db.get_issues_awaiting_comment("o", "r")
    assert len(review_issues) == 2
    phases = {i.phase for i in review_issues}
    assert phases == {"design_review", "code_review"}
