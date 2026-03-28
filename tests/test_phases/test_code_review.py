# tests/test_phases/test_code_review.py
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from remote_agent.phases.code_review import CodeReviewHandler
from remote_agent.models import Issue, Event
from remote_agent.agent import CommentInterpretation


@pytest.fixture
def deps():
    return {"db": AsyncMock(), "github": AsyncMock(), "agent_service": AsyncMock(), "workspace_mgr": AsyncMock()}


@pytest.fixture
def handler(deps):
    return CodeReviewHandler(deps["db"], deps["github"], deps["agent_service"], deps["workspace_mgr"])


@pytest.fixture
def review_issue():
    return Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                 title="Add auth", body="Need OAuth2", phase="code_review",
                 pr_number=10, branch_name="agent/issue-42",
                 design_commit_hash="abc123",
                 workspace_path="/tmp/ws")


async def test_approve_completes(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "completed"
    deps["workspace_mgr"].cleanup.assert_called_once()
    deps["db"].clear_plan_path.assert_called_once_with(1)


async def test_revise_creates_event(handler, deps, review_issue):
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Fix errors"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="revise")
    result = await handler.handle(review_issue, event)
    assert result.next_phase == "implementing"
    deps["db"].create_event.assert_called_once()


async def test_back_to_design_resets_state(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  design_commit_hash="abc123",
                  plan_path="/tmp/.plans/issue-42-plan.md",
                  workspace_path="/tmp/ws")
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "rethink the design"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="back_to_design")

    with patch("remote_agent.phases.code_review.Path") as mock_path_cls:
        mock_design_path = MagicMock()
        mock_design_path.exists.return_value = False
        mock_plan_path = MagicMock()
        mock_plan_path.exists.return_value = True
        mock_plan_path.read_text.return_value = "plan content"

        def path_side_effect(arg):
            if arg == "/tmp/ws":
                # For Path(workspace) / "docs" / ..., we need to return an object whose __truediv__ works
                mock_ws = MagicMock()
                mock_ws.__truediv__ = lambda self, other: mock_ws
                mock_ws.exists.return_value = False
                mock_ws.read_text.return_value = ""
                return mock_ws
            if arg == "/tmp/.plans/issue-42-plan.md":
                return mock_plan_path
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        result = await handler.handle(issue, event)

    assert result.next_phase == "designing"
    deps["db"].set_design_approved.assert_called_once_with(1, False)
    deps["github"].mark_pr_draft.assert_called_once()
    deps["workspace_mgr"].reset_to_commit.assert_called_once()
    deps["db"].clear_plan_path.assert_called_once_with(1)
    # Feedback should be posted on the ISSUE, not the PR
    deps["github"].post_comment.assert_called_once()
    call_args = deps["github"].post_comment.call_args
    assert call_args[0][2] == 42  # issue_number, not pr_number
    deps["db"].create_event.assert_called_once()


async def test_approve_cleans_plan(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  design_commit_hash="abc123",
                  plan_path="/tmp/.plans/issue-42-plan.md",
                  workspace_path="/tmp/ws")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "LGTM"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    with patch("remote_agent.phases.code_review.Path") as mock_path_cls:
        mock_design_path = MagicMock()
        mock_design_path.exists.return_value = False

        mock_plan_path = MagicMock()
        mock_plan_path.exists.return_value = True
        mock_plan_path.read_text.return_value = "plan content"

        def path_side_effect(arg):
            if arg == "/tmp/ws":
                mock_ws = MagicMock()
                mock_ws.__truediv__ = lambda self, other: mock_ws
                mock_ws.exists.return_value = False
                mock_ws.read_text.return_value = ""
                return mock_ws
            if arg == "/tmp/.plans/issue-42-plan.md":
                return mock_plan_path
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        result = await handler.handle(issue, event)

    assert result.next_phase == "completed"
    deps["db"].clear_plan_path.assert_called_once_with(1)
    # Verify the plan file unlink was called
    mock_plan_path.unlink.assert_called_once_with(missing_ok=True)
    deps["workspace_mgr"].cleanup.assert_called_once()


async def test_question_answered_with_context(handler, deps):
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  design_commit_hash="abc123",
                  workspace_path="/tmp/ws")
    event = Event(id=1, issue_id=1, event_type="new_comment",
                  payload={"body": "Why did you use this pattern?"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="question")
    deps["agent_service"].answer_question.return_value = "Great question! Here's why..."

    with patch("remote_agent.phases.code_review.Path") as mock_path_cls:
        mock_design_file = MagicMock()
        mock_design_file.exists.return_value = True
        mock_design_file.read_text.return_value = "design doc content"

        # Path(workspace) / "docs" -> docs_dir / "plans" -> plans_dir / "issue-42-design.md" -> design_file
        mock_plans_dir = MagicMock()
        mock_plans_dir.__truediv__ = lambda self, other: mock_design_file
        mock_docs_dir = MagicMock()
        mock_docs_dir.__truediv__ = lambda self, other: mock_plans_dir
        mock_ws = MagicMock()
        mock_ws.__truediv__ = lambda self, other: mock_docs_dir

        def path_side_effect(arg):
            if arg == "/tmp/ws":
                return mock_ws
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        result = await handler.handle(issue, event)

    assert result.next_phase == "code_review"
    deps["agent_service"].answer_question.assert_called_once()
    call_kwargs = deps["agent_service"].answer_question.call_args[1]
    assert call_kwargs["question"] == "Why did you use this pattern?"
    assert call_kwargs["context"] == "code_review"
    assert call_kwargs["design_content"] == "design doc content"
    # Answer posted on PR
    deps["github"].post_comment.assert_called_once()
    pr_call = deps["github"].post_comment.call_args
    assert pr_call[0][2] == 10  # pr_number


async def test_code_review_approve_audit(deps):
    audit = AsyncMock()
    handler = CodeReviewHandler(deps["db"], deps["github"], deps["agent_service"],
                                 deps["workspace_mgr"], audit=audit)

    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "Ship it"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    result = await handler.handle(issue, event)

    assert result.next_phase == "completed"
    assert audit.log.call_count >= 1
    categories = [c.args[0] for c in audit.log.call_args_list]
    assert "comment_classification" in categories
    assert "phase_transition" in categories


async def test_interpret_comment_receives_context(handler, deps):
    """Verify design_content and plan_content are passed to interpret_comment."""
    issue = Issue(id=1, repo_owner="o", repo_name="r", issue_number=42,
                  title="Add auth", body="Need OAuth2", phase="code_review",
                  pr_number=10, branch_name="agent/issue-42",
                  design_commit_hash="abc123",
                  plan_path="/tmp/.plans/issue-42-plan.md",
                  workspace_path="/tmp/ws")
    event = Event(id=1, issue_id=1, event_type="new_comment", payload={"body": "looks good"})
    deps["agent_service"].interpret_comment.return_value = CommentInterpretation(intent="approve")

    with patch("remote_agent.phases.code_review.Path") as mock_path_cls:
        mock_design_file = MagicMock()
        mock_design_file.exists.return_value = True
        mock_design_file.read_text.return_value = "design doc"

        mock_plans_dir = MagicMock()
        mock_plans_dir.__truediv__ = lambda self, other: mock_design_file
        mock_docs_dir = MagicMock()
        mock_docs_dir.__truediv__ = lambda self, other: mock_plans_dir
        mock_ws = MagicMock()
        mock_ws.__truediv__ = lambda self, other: mock_docs_dir

        mock_plan_path = MagicMock()
        mock_plan_path.exists.return_value = True
        mock_plan_path.read_text.return_value = "plan doc"

        def path_side_effect(arg):
            if arg == "/tmp/ws":
                return mock_ws
            if arg == "/tmp/.plans/issue-42-plan.md":
                return mock_plan_path
            return MagicMock()

        mock_path_cls.side_effect = path_side_effect

        await handler.handle(issue, event)

    call_kwargs = deps["agent_service"].interpret_comment.call_args[1]
    assert call_kwargs["design_content"] == "design doc"
    assert call_kwargs["plan_content"] == "plan doc"
