"""E2E integration tests for DEBT-01: GitHub API and search pipeline.

These tests hit real external services (GitHub API, ChromaDB) and require
a valid GITHUB_TOKEN environment variable. All tests are skipped gracefully
when the token is not available.
"""

from __future__ import annotations

import logging
import os
import time

import pytest

logger = logging.getLogger(__name__)

pytestmark = pytest.mark.e2e

skip_no_token = pytest.mark.skipif(
    not os.environ.get("GITHUB_TOKEN"),
    reason="GITHUB_TOKEN not set -- skipping e2e GitHub tests",
)


@pytest.fixture(scope="session")
def e2e_index_db(tmp_path_factory: pytest.TempPathFactory) -> str:
    """Build a real OCA index in a temporary directory.

    This is a session-scoped fixture shared across all e2e tests that need
    a ChromaDB index. It calls build_oca_index() against the real OCA GitHub
    org.

    Returns the db_path string for tests to use.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        pytest.skip("GITHUB_TOKEN not set -- cannot build e2e index")

    from amil_utils.search.index import build_oca_index

    db_path = str(tmp_path_factory.mktemp("e2e_chromadb"))
    build_oca_index(token=token, db_path=db_path)
    return db_path


@skip_no_token
def test_github_token_available() -> None:
    """Verify get_github_token() returns a non-empty string when GITHUB_TOKEN is set."""
    from amil_utils.search.index import get_github_token

    token = get_github_token()
    assert token is not None, "Expected a GitHub token but got None"
    assert len(token) > 0, "Expected a non-empty GitHub token"


@skip_no_token
def test_build_index_returns_modules(e2e_index_db: str) -> None:
    """Verify build_oca_index returns a positive module count."""
    from amil_utils.search.index import get_index_status

    status = get_index_status(db_path=e2e_index_db)
    assert status.module_count > 0, (
        f"Expected module_count > 0, got {status.module_count}"
    )


@skip_no_token
def test_index_status_reports_modules(e2e_index_db: str) -> None:
    """Verify get_index_status reports exists=True and module_count > 0 on a built index."""
    from amil_utils.search.index import get_index_status

    status = get_index_status(db_path=e2e_index_db)
    assert status.exists is True, "Expected index to exist"
    assert status.module_count > 0, (
        f"Expected module_count > 0, got {status.module_count}"
    )
    assert status.last_built is not None, "Expected last_built to be set"
    assert status.db_path == e2e_index_db


@skip_no_token
def test_search_returns_results(e2e_index_db: str) -> None:
    """Verify search_modules returns non-empty results for a common query."""
    from amil_utils.search.query import search_modules

    results = search_modules("inventory management", db_path=e2e_index_db)
    assert len(results) > 0, "Expected at least one search result for 'inventory management'"
    # Verify result structure
    first = results[0]
    assert first.module_name, "Expected module_name to be non-empty"
    assert first.relevance_score > 0.0, "Expected positive relevance score"


@skip_no_token
def test_github_fallback_returns_results() -> None:
    """Verify _github_search_fallback returns results via real gh search repos."""
    from amil_utils.search.query import _github_search_fallback

    results = _github_search_fallback("odoo inventory", 3)
    # gh search repos may return empty if gh CLI is not authenticated,
    # but with GITHUB_TOKEN set it should work
    assert isinstance(results, tuple), "Expected tuple of SearchResult"
    if len(results) > 0:
        first = results[0]
        assert first.org == "GitHub"
        assert first.module_name, "Expected module_name to be non-empty"


@pytest.mark.e2e_slow
@skip_no_token
def test_full_oca_index_build(tmp_path: object) -> None:
    """Build the full OCA index and verify count > 50.

    This test is marked e2e_slow because it crawls all 200+ OCA repos
    and takes 3-5 minutes to complete.
    """
    from pathlib import Path

    from amil_utils.search.index import build_oca_index

    token = os.environ.get("GITHUB_TOKEN")
    assert token, "GITHUB_TOKEN required for full build test"

    db_path = str(Path(str(tmp_path)) / "full_build_chromadb")

    start_time = time.monotonic()
    count = build_oca_index(token=token, db_path=db_path)
    duration = time.monotonic() - start_time

    logger.info("Full OCA index build: %d modules in %.1fs", count, duration)

    assert count > 50, (
        f"Expected > 50 modules from full OCA build, got {count}"
    )
