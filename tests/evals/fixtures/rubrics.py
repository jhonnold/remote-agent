# tests/evals/fixtures/rubrics.py
"""Rubric definitions for eval judging."""
from dataclasses import dataclass


@dataclass
class Criterion:
    name: str
    description: str
    min_score: int  # 1-5, minimum passing score


DESIGNING_RUBRIC: list[Criterion] = [
    Criterion(
        name="Section completeness",
        description=(
            "Contains all 5 required sections: Architecture, Components, "
            "Data Flow, Error Handling, Testing Strategy"
        ),
        min_score=5,
    ),
    Criterion(
        name="Format adherence",
        description=(
            "Follows the markdown template: has '# [Name] Design' header, "
            "'**Issue:**' and '**Goal:**' fields, '##' section headers"
        ),
        min_score=4,
    ),
    Criterion(
        name="Specificity",
        description=(
            "References concrete file paths, function names, and patterns "
            "from the codebase rather than staying abstract"
        ),
        min_score=3,
    ),
    Criterion(
        name="No implementation code",
        description=(
            "Does not contain actual implementation code — only design "
            "description, architecture decisions, and interface sketches"
        ),
        min_score=4,
    ),
    Criterion(
        name="Coherence",
        description=(
            "Sections are internally consistent; the architecture logically "
            "supports the components described"
        ),
        min_score=3,
    ),
]
