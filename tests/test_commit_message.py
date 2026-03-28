# tests/test_commit_message.py
from __future__ import annotations
from remote_agent.commit_message import extract_commit_message, build_commit_message

_ISSUE_NUMBER = 42
_ISSUE_TITLE = "Add auth"


def test_extract_valid_tag():
    text = "Some output\n<commit_message>feat: add X</commit_message>\nDone."
    assert extract_commit_message(text) == "feat: add X"


def test_extract_missing_tag():
    assert extract_commit_message("no tag here") is None


def test_extract_empty_tag():
    assert extract_commit_message("<commit_message>  </commit_message>") is None


def test_extract_multiple_tags_returns_last():
    text = (
        "<commit_message>first</commit_message> middle "
        "<commit_message>second</commit_message>"
    )
    assert extract_commit_message(text) == "second"


def test_extract_none_input():
    assert extract_commit_message(None) is None


def test_extract_truncates_long_content():
    long_msg = "a" * 600
    text = f"<commit_message>{long_msg}</commit_message>"
    result = extract_commit_message(text)
    assert result == "a" * 500


def test_extract_replaces_newlines():
    text = "<commit_message>feat: add\nsome thing</commit_message>"
    assert extract_commit_message(text) == "feat: add some thing"


def test_extract_truncates_then_replaces_newlines():
    # Message with newline that exceeds 500 chars when newlines are replaced
    msg_with_newline = "a" * 250 + "\n" + "b" * 250
    text = f"<commit_message>{msg_with_newline}</commit_message>"
    result = extract_commit_message(text)
    # Should be: first 250 'a's, then space, then first 249 'b's (total 500)
    assert len(result) == 500
    assert result.startswith("a" * 250)
    assert result[250] == " "  # newline replaced with space
    assert result[251:] == "b" * 249


def test_build_with_extracted_closes():
    result = build_commit_message("feat: add X", _ISSUE_NUMBER, _ISSUE_TITLE, closes=True)
    assert result == "feat: add X\n\nCloses #42"


def test_build_with_extracted_refs():
    result = build_commit_message("docs: add Y", _ISSUE_NUMBER, _ISSUE_TITLE, closes=False)
    assert result == "docs: add Y\n\nRefs #42"


def test_build_fallback_design_new():
    result = build_commit_message(None, _ISSUE_NUMBER, _ISSUE_TITLE, closes=False)
    assert result == "docs: add design for Add auth (#42)\n\nRefs #42"


def test_build_fallback_design_revision():
    result = build_commit_message(None, _ISSUE_NUMBER, _ISSUE_TITLE, closes=False, is_revision=True)
    assert result == "docs: revise design for Add auth (#42)\n\nRefs #42"


def test_build_fallback_impl_new():
    result = build_commit_message(None, _ISSUE_NUMBER, _ISSUE_TITLE, closes=True)
    assert result == "feat: implement Add auth (#42)\n\nCloses #42"


def test_build_fallback_impl_revision():
    result = build_commit_message(None, _ISSUE_NUMBER, _ISSUE_TITLE, closes=True, is_revision=True)
    assert result == "fix: address review feedback for Add auth (#42)\n\nCloses #42"
