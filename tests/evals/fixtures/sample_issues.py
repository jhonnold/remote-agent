# tests/evals/fixtures/sample_issues.py
"""Static test data for eval tests."""

CACHING_ISSUE = {
    "issue_number": 99,
    "issue_title": "Add caching layer",
    "issue_body": (
        "We need a caching layer for API responses to reduce latency. "
        "The cache should support TTL-based expiration and work with "
        "our existing async architecture. Consider using an in-memory "
        "cache with optional Redis backend for distributed deployments."
    ),
}

STUB_DESIGN = """\
# Add Caching Layer Design

**Issue:** #99
**Goal:** Add a caching layer for API responses to reduce latency.

## Architecture

Use an in-memory LRU cache with async support. The cache sits between
the API handler and the data layer.

## Components

- `CacheService` — manages cache entries with TTL
- `CacheMiddleware` — intercepts API calls and checks cache

## Data Flow

Request → CacheMiddleware → cache hit? → return cached / call handler → store & return

## Error Handling

Cache failures are non-fatal; on error, bypass cache and call handler directly.

## Testing Strategy

Unit tests for CacheService TTL logic. Integration test for middleware.
"""

STUB_FEEDBACK = (
    "The design is missing details on cache invalidation strategy. "
    "How do we handle stale data when the underlying data changes? "
    "Also, please add concrete file paths for where the new modules will live."
)
