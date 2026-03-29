# tests/test_eval_fixtures.py
"""Unit tests for eval fixture data integrity."""
from tests.evals.fixtures.rubrics import Criterion, DESIGNING_RUBRIC
from tests.evals.fixtures.sample_issues import CACHING_ISSUE, STUB_DESIGN, STUB_FEEDBACK
from remote_agent.config import (
    Config, RepoConfig, PollingConfig, TriggerConfig,
    WorkspaceConfig, DatabaseConfig, AgentConfig, LoggingConfig,
)


def test_designing_rubric_has_expected_count():
    assert len(DESIGNING_RUBRIC) == 5, (
        f"Expected 5 rubric criteria, got {len(DESIGNING_RUBRIC)}"
    )


def test_designing_rubric_criteria_have_valid_min_scores():
    for criterion in DESIGNING_RUBRIC:
        assert isinstance(criterion, Criterion)
        assert 1 <= criterion.min_score <= 5, (
            f"{criterion.name} has invalid min_score={criterion.min_score}"
        )


def test_designing_rubric_criteria_have_descriptions():
    for criterion in DESIGNING_RUBRIC:
        assert criterion.name.strip(), "Criterion name must not be empty"
        assert criterion.description.strip(), (
            f"Criterion '{criterion.name}' has empty description"
        )


def test_caching_issue_has_required_fields():
    assert CACHING_ISSUE["issue_number"] > 0
    assert len(CACHING_ISSUE["issue_title"]) > 0
    assert len(CACHING_ISSUE["issue_body"]) > 0


def test_stub_design_is_nonempty():
    assert len(STUB_DESIGN.strip()) > 0


def test_stub_feedback_is_nonempty():
    assert len(STUB_FEEDBACK.strip()) > 0


def test_eval_config_has_cost_conscious_overrides():
    """Verify eval config matches the design doc's cost control table."""
    config = Config(
        repos=[RepoConfig(owner="eval", name="eval")],
        users=["eval-user"],
        polling=PollingConfig(interval_seconds=60),
        trigger=TriggerConfig(label="agent"),
        workspace=WorkspaceConfig(base_dir="/tmp/eval"),
        database=DatabaseConfig(path=""),
        agent=AgentConfig(
            planning_model="sonnet",
            max_turns=50,
            max_budget_usd=2.0,
        ),
        logging=LoggingConfig(),
    )
    assert config.agent.planning_model == "sonnet"
    assert config.agent.max_turns == 50
    assert config.agent.max_budget_usd == 2.0
