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
    await db.update_issue_phase(id1, "plan_review")
    await db.update_issue_phase(id2, "implementing")
    await db.update_issue_phase(id3, "error")
    review_issues = await db.get_issues_awaiting_comment("o", "r")
    assert len(review_issues) == 2  # plan_review + error
    phases = {i.phase for i in review_issues}
    assert phases == {"plan_review", "error"}


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
    await db.update_issue_phase(issue_id, "plan_review")
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
    await db.update_issue_phase(issue_id, "plan_review")
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
