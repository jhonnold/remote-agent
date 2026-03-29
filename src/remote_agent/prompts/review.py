from __future__ import annotations


def build_review_system_prompt() -> str:
    return """## Role

You are interpreting a human's comment on a GitHub issue or pull request.

The keywords MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY in this prompt follow RFC 2119.

## Task

Read the comment and classify the human's intent.

### Contexts
You will be classifying comments in one of these contexts: design_review, code_review.

### Intent Categories
- **approve**: The human is satisfied and wants to proceed to the next phase.
  Examples: "looks good", "approved", "LGTM", "ship it", "go ahead"
- **revise**: The human wants changes to the current work.
  Examples: "change X to Y", "this won't work because...", "also handle edge case Z"
- **question**: The human is asking a question and expects an answer, not action.
  Examples: "why did you choose X?", "what happens if Z?", "can you explain this?"
- **back_to_design**: The human wants to rethink the design entirely (only valid during code review).
  Examples: "the design needs to change", "let's rethink the design", "go back to design"

## Format

Call the classify_comment tool with your classification.

## Constraints

- When uncertain, MUST default to "revise" (safer than proceeding on a misread approval).
- For "question" intent, MUST include a helpful response in the response field.
- For "revise" intent, MUST include the revision request summary in the response field.
- SHOULD be conservative with "approve" — only when the intent is clearly positive.
"""


def build_review_user_prompt(
    comment: str,
    context: str,
    issue_title: str,
) -> str:
    if context == "design_review":
        valid_intents = "approve, revise, question"
    elif context == "code_review":
        valid_intents = "approve, revise, question, back_to_design"
    else:
        valid_intents = "approve, revise, question"

    return f"""Classify the following comment for: **{issue_title}**

**Valid intents for this phase ({context}):** {valid_intents}

**Comment:**
{comment}

Call the classify_comment tool with your classification.
"""
