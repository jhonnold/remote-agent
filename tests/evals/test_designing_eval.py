# tests/evals/test_designing_eval.py
"""LLM-as-judge eval tests for the designing phase."""
from __future__ import annotations

from pathlib import Path

import pytest

from remote_agent.exceptions import AgentError
from tests.evals.fixtures.rubrics import DESIGNING_RUBRIC
from tests.evals.fixtures.sample_issues import (
    CACHING_ISSUE,
    STUB_DESIGN,
    STUB_FEEDBACK,
)
from tests.evals.judge import judge_output


def _format_failures(judge_result) -> str:
    """Format failing criteria for assertion message."""
    min_scores = {c.name: c.min_score for c in DESIGNING_RUBRIC}
    lines = ["Quality criteria failures:"]
    for score in judge_result.scores:
        min_req = min_scores.get(score.criterion, 1)
        if score.score < min_req:
            lines.append(
                f"  - {score.criterion}: got {score.score}/{min_req} "
                f"— {score.reasoning}"
            )
    return "\n".join(lines)


@pytest.mark.eval
async def test_designing_produces_quality_design(
    eval_agent_service,
    eval_db,
    eval_workspace,
):
    try:
        result = await eval_agent_service.run_designing(
            issue_number=CACHING_ISSUE["issue_number"],
            issue_title=CACHING_ISSUE["issue_title"],
            issue_body=CACHING_ISSUE["issue_body"],
            cwd=eval_workspace,
            issue_id=1,
        )
    except AgentError as e:
        pytest.fail(f"Agent execution failed: {e}")

    design_path = (
        Path(eval_workspace)
        / "docs"
        / "plans"
        / f"issue-{CACHING_ISSUE['issue_number']}-design.md"
    )
    if not design_path.exists():
        pytest.fail(f"Agent did not produce design file at {design_path}")

    artifact = design_path.read_text()
    judge_result = await judge_output(
        artifact,
        DESIGNING_RUBRIC,
        context=f"Design for: {CACHING_ISSUE['issue_title']}",
    )
    assert judge_result.passed, _format_failures(judge_result)


@pytest.mark.eval
async def test_designing_revision_produces_quality_design(
    eval_agent_service,
    eval_db,
    eval_workspace,
):
    try:
        result = await eval_agent_service.run_designing(
            issue_number=CACHING_ISSUE["issue_number"],
            issue_title=CACHING_ISSUE["issue_title"],
            issue_body=CACHING_ISSUE["issue_body"],
            cwd=eval_workspace,
            issue_id=1,
            existing_design=STUB_DESIGN,
            feedback=STUB_FEEDBACK,
        )
    except AgentError as e:
        pytest.fail(f"Agent execution failed: {e}")

    design_path = (
        Path(eval_workspace)
        / "docs"
        / "plans"
        / f"issue-{CACHING_ISSUE['issue_number']}-design.md"
    )
    if not design_path.exists():
        pytest.fail(f"Agent did not produce design file at {design_path}")

    artifact = design_path.read_text()
    judge_result = await judge_output(
        artifact,
        DESIGNING_RUBRIC,
        context=(
            f"Revised design for: {CACHING_ISSUE['issue_title']}. "
            f"Feedback addressed: {STUB_FEEDBACK}"
        ),
    )
    assert judge_result.passed, _format_failures(judge_result)
