# src/remote_agent/commit_message.py
from __future__ import annotations
import re

# This module is integrated into phase handlers in Tasks 3-5 (designing.py, implementation.py)

_TAG_RE = re.compile(r"<commit_message>(.*?)</commit_message>", re.DOTALL)
_MAX_LEN = 500


def extract_commit_message(result_text: str | None) -> str | None:
    """Parse the last <commit_message>...</commit_message> from LLM output.

    Returns None if result_text is None, no tag found, or tag content is
    empty after stripping. Truncates to 500 chars. Replaces newlines with spaces.
    """
    if result_text is None:
        return None
    matches = _TAG_RE.findall(result_text)
    if not matches:
        return None
    content = matches[-1].strip()
    if not content:
        return None
    if len(content) > _MAX_LEN:
        content = content[:_MAX_LEN]
    content = content.replace("\n", " ")
    return content


def build_commit_message(
    extracted: str | None,
    issue_number: int,
    issue_title: str,
    *,
    closes: bool,
    is_revision: bool = False,
) -> str:
    """Assemble the final commit message with trailer.

    Uses extracted text as subject if available, otherwise falls back to a
    template based on closes/is_revision context. Appends 'Closes #N' or
    'Refs #N' trailer.
    """
    trailer = f"Closes #{issue_number}" if closes else f"Refs #{issue_number}"

    if extracted is not None:
        # Defensive: ensure no embedded newlines even if caller bypasses extract_commit_message()
        extracted = extracted.replace("\n", " ")
        return f"{extracted}\n\n{trailer}"

    if closes:
        if is_revision:
            subject = f"fix: address review feedback for {issue_title} (#{issue_number})"
        else:
            subject = f"feat: implement {issue_title} (#{issue_number})"
    else:
        if is_revision:
            subject = f"docs: revise design for {issue_title} (#{issue_number})"
        else:
            subject = f"docs: add design for {issue_title} (#{issue_number})"

    return f"{subject}\n\n{trailer}"
