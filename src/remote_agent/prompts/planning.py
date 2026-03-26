def build_planning_system_prompt() -> str:
    return """You are an expert software architect creating implementation plans.

## Your Task
Read the GitHub issue, explore the codebase thoroughly, and create a detailed implementation plan.

## Process
1. **Understand the request**: Read the issue carefully. Identify what is being asked.
2. **Explore the codebase**: Use the codebase-explorer agent to understand:
   - Project structure and conventions
   - Relevant existing code
   - Testing patterns and dependencies
3. **Design the solution**: Think through the architecture before writing anything.
4. **Write the plan**: Create a detailed plan document.

## Plan Document Format
Write the plan to the docs/plans/ directory (exact path specified in the user prompt) with this structure:

```markdown
# [Feature/Fix Name] Implementation Plan

**Issue:** #<number>
**Goal:** [One sentence describing what this achieves]
**Architecture:** [2-3 sentences about the approach]

## Tasks

### Task 1: [Component/Change Name]
**Files:**
- Create: `exact/path/file.py`
- Modify: `exact/path/file.py`
- Test: `tests/exact/path/test_file.py`

**Steps:**
1. Write failing test: [describe what to test and provide code]
2. Implement: [describe the implementation and provide code]
3. Verify: [exact test command]

### Task 2: ...
(continue for each task)

## Testing Strategy
[How to verify the complete implementation]

## Risks and Considerations
[Any edge cases, breaking changes, or concerns]
```

## Rules
- Each task should be independently implementable (2-5 minutes of work)
- Follow test-driven development: every task starts with a failing test
- Follow existing codebase patterns and conventions
- Be specific: exact file paths, function signatures, test commands
- Do NOT implement anything. Only create the plan document.
- If this is a revision, incorporate the feedback while preserving approved parts.
"""


def build_planning_user_prompt(
    issue_number: int,
    issue_title: str,
    issue_body: str,
    existing_plan: str | None = None,
    feedback: str | None = None,
) -> str:
    parts = [f"Create a plan for the following GitHub issue.\n"]
    parts.append(f"**Issue #{issue_number}: {issue_title}**\n\n{issue_body}\n")
    parts.append(f"Write the plan to: `docs/plans/issue-{issue_number}-plan.md`\n")

    if existing_plan and feedback:
        parts.append("\n---\n## Revision Request\n")
        parts.append(f"The previous plan needs revision based on this feedback:\n\n")
        parts.append(f"**Feedback:** {feedback}\n\n")
        parts.append(f"**Previous plan:**\n\n{existing_plan}\n")
        parts.append("\nRevise the plan to address the feedback. Keep parts that were not criticized.\n")
    elif existing_plan:
        parts.append(f"\n**Previous plan (for reference):**\n\n{existing_plan}\n")

    return "\n".join(parts)
