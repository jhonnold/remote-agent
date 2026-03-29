import pytest


def pytest_addoption(parser):
    """Register the --run-evals CLI flag for eval tests."""
    parser.addoption(
        "--run-evals",
        action="store_true",
        default=False,
        help="Run LLM-as-judge eval tests (slow, costs money)",
    )


@pytest.fixture
def sample_issue_data():
    return {
        "number": 42,
        "title": "Add user authentication",
        "body": "We need OAuth2 support for the API.",
        "author": {"login": "myuser"},
    }
