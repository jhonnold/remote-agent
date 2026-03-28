# tests/test_prompts.py
from remote_agent.prompts.designing import build_designing_system_prompt, build_designing_user_prompt
from remote_agent.prompts.planning import build_planning_system_prompt, build_planning_user_prompt
from remote_agent.prompts.implementation import build_implementation_system_prompt, build_implementation_user_prompt
from remote_agent.prompts.review import build_review_system_prompt, build_review_user_prompt


def test_designing_system_prompt_contains_key_instructions():
    prompt = build_designing_system_prompt()
    assert "architect" in prompt.lower()
    assert "codebase-explorer" in prompt or "codebase_explorer" in prompt
    assert "issue-advocate" in prompt or "issue_advocate" in prompt
    assert "design-critic" in prompt or "design_critic" in prompt
    assert "2-3 approaches" in prompt or "two to three" in prompt.lower()


def test_designing_user_prompt_new_issue():
    prompt = build_designing_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
    )
    assert "#42" in prompt
    assert "Add auth" in prompt
    assert "Need OAuth2" in prompt
    assert "issue-42-design.md" in prompt


def test_designing_user_prompt_revision():
    prompt = build_designing_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
        existing_design="## Old design", feedback="Change the approach",
    )
    assert "Change the approach" in prompt
    assert "Old design" in prompt
    assert "Revision" in prompt or "revision" in prompt


def test_planning_system_prompt_contains_key_instructions():
    prompt = build_planning_system_prompt()
    assert "plan" in prompt.lower()
    assert "docs/plans/" in prompt
    assert "codebase-explorer" in prompt


def test_planning_system_prompt_references_design_doc():
    prompt = build_planning_system_prompt()
    assert "design" in prompt.lower()
    assert "plan-reviewer" in prompt or "plan_reviewer" in prompt
    assert "bite-sized" in prompt.lower() or "single action" in prompt.lower()


def test_planning_user_prompt_includes_design_content():
    prompt = build_planning_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
        design_content="## Design\nUse token-based auth",
    )
    assert "Use token-based auth" in prompt
    assert "#42" in prompt


def test_planning_user_prompt_includes_issue_details():
    prompt = build_planning_user_prompt(
        issue_number=42, issue_title="Add auth", issue_body="Need OAuth2",
        design_content="## Design\nSome design",
    )
    assert "42" in prompt
    assert "Add auth" in prompt
    assert "OAuth2" in prompt
    assert "issue-42-plan.md" in prompt


def test_implementation_system_prompt_contains_key_instructions():
    prompt = build_implementation_system_prompt()
    assert "implementer" in prompt
    assert "spec-reviewer" in prompt
    assert "code-reviewer" in prompt
    assert "do not write code yourself" in prompt.lower() or "do NOT write code" in prompt


def test_implementation_system_prompt_references_issue_advocate():
    prompt = build_implementation_system_prompt()
    assert "issue-advocate" in prompt or "issue_advocate" in prompt
    assert "final-reviewer" in prompt or "final_reviewer" in prompt
    assert "scene-setting" in prompt.lower() or "scene setting" in prompt.lower()


def test_implementation_system_prompt_red_flags():
    prompt = build_implementation_system_prompt()
    assert "never parallelize" in prompt.lower() or "do not parallelize" in prompt.lower()
    assert "never skip review" in prompt.lower() or "do not skip review" in prompt.lower()
    assert "full task text" in prompt.lower()
    assert "3 iteration" in prompt.lower() or "three iteration" in prompt.lower()


def test_implementation_system_prompt_verification():
    prompt = build_implementation_system_prompt()
    assert "full test suite" in prompt.lower()
    assert "verification" in prompt.lower() or "verify" in prompt.lower()


def test_implementation_user_prompt():
    prompt = build_implementation_user_prompt(
        plan_content="## Task 1\nDo stuff",
        issue_title="Add auth",
        issue_body="Need OAuth2",
        design_content="## Design\nUse tokens",
    )
    assert "Task 1" in prompt
    assert "Add auth" in prompt
    assert "Need OAuth2" in prompt
    assert "## Design" in prompt or "Use tokens" in prompt


def test_implementation_user_prompt_with_feedback():
    prompt = build_implementation_user_prompt(
        plan_content="## Task 1", issue_title="X",
        issue_body="Body", design_content="Design",
        feedback="Fix the error handling",
    )
    assert "Fix the error handling" in prompt


def test_implementation_user_prompt_includes_design_and_issue():
    prompt = build_implementation_user_prompt(
        plan_content="## Plan", issue_title="Add auth",
        issue_body="Need OAuth2", design_content="## Design",
    )
    assert "## Plan" in prompt
    assert "## Design" in prompt
    assert "Need OAuth2" in prompt


def test_review_system_prompt():
    prompt = build_review_system_prompt()
    assert "classify_comment" in prompt


def test_review_user_prompt_plan_review():
    prompt = build_review_user_prompt(
        comment="Looks good!", context="plan_review", issue_title="Add auth",
    )
    assert "Looks good!" in prompt
    assert "back_to_planning" not in prompt  # Not valid for plan_review


def test_review_user_prompt_code_review():
    prompt = build_review_user_prompt(
        comment="Go back to planning", context="code_review", issue_title="Add auth",
    )
    assert "back_to_planning" in prompt  # Valid for code_review


# ── Sub-agent prompts ──────────────────────────────────────────────────

from remote_agent.prompts.subagents import (
    codebase_explorer_prompt, issue_advocate_prompt, design_critic_prompt,
    plan_reviewer_prompt, implementer_prompt, spec_reviewer_prompt,
    code_quality_reviewer_prompt, final_reviewer_prompt,
)


def test_codebase_explorer_prompt():
    prompt = codebase_explorer_prompt()
    assert "codebase" in prompt.lower()
    assert "structure" in prompt.lower()


def test_issue_advocate_prompt_includes_issue_body():
    prompt = issue_advocate_prompt("We need OAuth2 support")
    assert "We need OAuth2 support" in prompt
    assert "issue" in prompt.lower()
    assert "codebase" in prompt.lower()


def test_issue_advocate_prompt_flags_inferences():
    prompt = issue_advocate_prompt("Add auth")
    assert "infer" in prompt.lower() or "flag" in prompt.lower()


def test_design_critic_prompt():
    prompt = design_critic_prompt()
    assert "design" in prompt.lower()
    assert "YAGNI" in prompt or "yagni" in prompt.lower()


def test_plan_reviewer_prompt():
    prompt = plan_reviewer_prompt()
    assert "plan" in prompt.lower()
    assert "design" in prompt.lower()


def test_implementer_prompt_has_before_you_begin():
    prompt = implementer_prompt()
    assert "Before You Begin" in prompt or "before you begin" in prompt.lower()


def test_implementer_prompt_has_self_review():
    prompt = implementer_prompt()
    assert "self-review" in prompt.lower() or "Self-Review" in prompt
    assert "Completeness" in prompt
    assert "Quality" in prompt
    assert "Discipline" in prompt
    assert "Testing" in prompt


def test_spec_reviewer_prompt_adversarial():
    prompt = spec_reviewer_prompt()
    assert "Do NOT trust" in prompt or "do not trust" in prompt.lower()


def test_code_quality_reviewer_prompt():
    prompt = code_quality_reviewer_prompt()
    assert "Critical" in prompt
    assert "Important" in prompt
    assert "Minor" in prompt


def test_final_reviewer_prompt():
    prompt = final_reviewer_prompt()
    assert "holistic" in prompt.lower() or "entire" in prompt.lower()
