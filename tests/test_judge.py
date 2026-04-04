# tests/test_judge.py
"""Unit tests for the judge module (no LLM calls — mocked)."""
import json
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from tests.evals.judge import (
    judge_output,
    JudgeScore,
    JudgeResult,
    JudgeParseError,
    _build_judge_system_prompt,
    _parse_judge_response,
)
from tests.evals.fixtures.rubrics import Criterion, DESIGNING_RUBRIC


def _make_judge_json(scores: list[dict]) -> str:
    return json.dumps(scores)


def _make_result_message(result_text: str):
    msg = MagicMock()
    msg.result = result_text
    return msg


async def test_judge_happy_path_all_pass():
    response_data = [
        {"criterion": c.name, "score": 5, "reasoning": "Excellent."}
        for c in DESIGNING_RUBRIC
    ]
    mock_msg = _make_result_message(_make_judge_json(response_data))

    async def mock_query(**kwargs):
        yield mock_msg

    with patch("tests.evals.judge.query", mock_query):
        with patch("tests.evals.judge.ClaudeAgentOptions"):
            result = await judge_output("Some artifact", DESIGNING_RUBRIC)

    assert result.passed is True
    assert len(result.scores) == len(DESIGNING_RUBRIC)
    for score in result.scores:
        assert score.score == 5


async def test_judge_failure_path_score_below_threshold():
    response_data = [
        {"criterion": c.name, "score": c.min_score, "reasoning": "Meets minimum."}
        for c in DESIGNING_RUBRIC
    ]
    # Drop one score below threshold
    response_data[0]["score"] = DESIGNING_RUBRIC[0].min_score - 1

    mock_msg = _make_result_message(_make_judge_json(response_data))

    async def mock_query(**kwargs):
        yield mock_msg

    with patch("tests.evals.judge.query", mock_query):
        with patch("tests.evals.judge.ClaudeAgentOptions"):
            result = await judge_output("Some artifact", DESIGNING_RUBRIC)

    assert result.passed is False
    assert result.scores[0].score == DESIGNING_RUBRIC[0].min_score - 1


async def test_judge_parse_error_on_prose_response():
    mock_msg = _make_result_message("I think this design is pretty good overall.")

    async def mock_query(**kwargs):
        yield mock_msg

    with patch("tests.evals.judge.query", mock_query):
        with patch("tests.evals.judge.ClaudeAgentOptions"):
            with pytest.raises(JudgeParseError) as exc_info:
                await judge_output("Some artifact", DESIGNING_RUBRIC)

    assert "I think this design is pretty good overall." in str(exc_info.value)


def test_judge_system_prompt_includes_all_criteria():
    prompt = _build_judge_system_prompt(DESIGNING_RUBRIC)
    for criterion in DESIGNING_RUBRIC:
        assert criterion.name in prompt, (
            f"Criterion '{criterion.name}' missing from judge system prompt"
        )
        assert criterion.description in prompt, (
            f"Description for '{criterion.name}' missing from judge system prompt"
        )


def test_parse_judge_response_strips_markdown_fences():
    rubric = [Criterion(name="Test", description="Test", min_score=3)]
    fenced = '```json\n[{"criterion": "Test", "score": 5, "reasoning": "Good."}]\n```'
    result = _parse_judge_response(fenced, rubric)
    assert result.passed is True
    assert result.scores[0].score == 5


def test_parse_judge_response_rejects_non_list():
    rubric = [Criterion(name="Test", description="Test", min_score=3)]
    with pytest.raises(JudgeParseError):
        _parse_judge_response('{"criterion": "Test", "score": 5}', rubric)


def test_parse_judge_response_rejects_missing_keys():
    rubric = [Criterion(name="Test", description="Test", min_score=3)]
    with pytest.raises(JudgeParseError):
        _parse_judge_response('[{"criterion": "Test"}]', rubric)
