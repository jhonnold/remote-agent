import pytest


@pytest.fixture
def sample_issue_data():
    return {
        "number": 42,
        "title": "Add user authentication",
        "body": "We need OAuth2 support for the API.",
        "author": {"login": "myuser"},
    }
