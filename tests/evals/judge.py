# tests/evals/judge.py
"""LLM-as-judge module for evaluating agent output quality."""
from __future__ import annotations

import json
from dataclasses import dataclass

from claude_agent_sdk import query, ClaudeAgentOptions

from tests.evals.fixtures.rubrics import Criterion


@dataclass
class JudgeScore:
    criterion: str
    score: int  # 1-5
    reasoning: str


@dataclass
class JudgeResult:
    scores: list[JudgeScore]
    passed: bool  # True if all scores >= their min thresholds


class JudgeParseError(Exception):
    """Raised when the judge LLM returns non-JSON or malformed JSON."""

    def __init__(self, raw_response: str):
        super().__init__(
            f"Judge returned invalid JSON. Raw response:\n{raw_response}"
        )


def _build_judge_system_prompt(rubric: list[Criterion]) -> str:
    criteria_text = "\n".join(
        f"- **{c.name}**: {c.description}" for c in rubric
    )
    return f"""\
You are an expert evaluator of software design documents. You will be given
an artifact (a design document) and must score it on the following criteria,
each on a 1-5 scale where 1 is very poor and 5 is excellent.

Criteria:
{criteria_text}

You MUST respond with ONLY a JSON array of objects. Each object must have
exactly these keys: "criterion" (string, the criterion name), "score" (integer, 1-5),
"reasoning" (string, brief explanation for the score).

Example response format:
[
  {{"criterion": "Section completeness", "score": 5, "reasoning": "All 5 sections present."}},
  {{"criterion": "Format adherence", "score": 4, "reasoning": "Has correct headers."}}
]

Do NOT include any text before or after the JSON array. Output ONLY valid JSON."""


def _build_judge_user_prompt(artifact: str, context: str = "") -> str:
    parts = []
    if context:
        parts.append(f"Context: {context}\n")
    parts.append(f"Artifact to evaluate:\n\n{artifact}")
    return "\n".join(parts)


def _parse_judge_response(
    raw: str, rubric: list[Criterion]
) -> JudgeResult:
    # Strip markdown code fences the LLM may wrap around the JSON
    stripped = raw.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1]  # drop opening ```json line
        stripped = stripped.rsplit("```", 1)[0]  # drop closing ```

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        raise JudgeParseError(raw)

    if not isinstance(data, list):
        raise JudgeParseError(raw)

    min_scores = {c.name: c.min_score for c in rubric}
    scores = []
    for item in data:
        if not isinstance(item, dict):
            raise JudgeParseError(raw)
        try:
            score = JudgeScore(
                criterion=item["criterion"],
                score=int(item["score"]),
                reasoning=item["reasoning"],
            )
            scores.append(score)
        except (KeyError, ValueError, TypeError):
            raise JudgeParseError(raw)

    passed = all(
        s.score >= min_scores.get(s.criterion, 1) for s in scores
    )
    return JudgeResult(scores=scores, passed=passed)


async def judge_output(
    artifact: str,
    rubric: list[Criterion],
    context: str = "",
) -> JudgeResult:
    """Send artifact to judge LLM, return structured scores."""
    system_prompt = _build_judge_system_prompt(rubric)
    user_prompt = _build_judge_user_prompt(artifact, context)

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model="haiku",
        max_turns=1,
        allowed_tools=[],
        permission_mode="bypassPermissions",
    )

    raw_response = ""
    async for message in query(prompt=user_prompt, options=options):
        if hasattr(message, "result"):
            raw_response = message.result

    if not raw_response:
        raise JudgeParseError("")

    return _parse_judge_response(raw_response, rubric)
