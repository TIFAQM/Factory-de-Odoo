"""OCA crawl, manifest parse, ChromaDB upsert pipeline for the search index.

Crawls OCA GitHub repositories via PyGithub, parses __manifest__.py files
using ast.literal_eval (never eval), and stores module metadata in a local
ChromaDB vector database for semantic search.
"""

from __future__ import annotations

import ast
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

try:
    import chromadb
except ImportError:  # pragma: no cover
    chromadb = None  # type: ignore[assignment]

try:
    from github import Github, GithubException, RateLimitExceededException
except ImportError:  # pragma: no cover
    Github = None  # type: ignore[assignment,misc]
    GithubException = Exception  # type: ignore[assignment,misc]
    RateLimitExceededException = Exception  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from amil_utils.search.types import IndexStatus

DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "amil" / "chromadb"


def _check_rate_limit(gh: object, min_remaining: int = 10) -> None:
    """Check GitHub API rate limit and sleep until reset if remaining is low.

    Calls ``gh.get_rate_limit().core`` to inspect the current rate limit.
    If ``remaining < min_remaining``, sleeps until the reset timestamp plus
    one second of buffer.

    Args:
        gh: Authenticated PyGithub Github instance.
        min_remaining: Minimum remaining requests before sleeping. Default 10.
    """
    rate = gh.get_rate_limit().core  # type: ignore[union-attr]
    if rate.remaining < min_remaining:
        reset_timestamp = rate.reset.timestamp()
        now = time.time()
        sleep_seconds = max(reset_timestamp - now + 1, 1)
        logger.info(
            "Rate limit low (%d/%d remaining). Sleeping %.0f seconds until reset.",
            rate.remaining,
            rate.limit,
            sleep_seconds,
        )
        time.sleep(sleep_seconds)


def _retry_on_rate_limit(func: Callable, *args: object, max_retries: int = 3, **kwargs: object) -> object:
    """Retry a function call on RateLimitExceededException with exponential backoff.

    Attempts ``func(*args, **kwargs)`` up to ``max_retries + 1`` times. On each
    RateLimitExceededException, sleeps for ``2 ** attempt`` seconds before retrying.
    Re-raises the exception if all retries are exhausted.

    Args:
        func: Callable to invoke.
        *args: Positional arguments passed to func.
        max_retries: Maximum number of retry attempts. Default 3.
        **kwargs: Keyword arguments passed to func.

    Returns:
        The return value of func on success.

    Raises:
        RateLimitExceededException: If all retries are exhausted.
    """
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except RateLimitExceededException as exc:
            if attempt >= max_retries:
                raise
            # Use reset timestamp from exception headers when available,
            # otherwise fall back to exponential backoff with longer base.
            reset_ts = getattr(exc, "headers", {}).get("X-RateLimit-Reset") if hasattr(exc, "headers") else None
            if reset_ts:
                try:
                    sleep_seconds = max(float(reset_ts) - time.time() + 1, 1)
                except (ValueError, TypeError):
                    sleep_seconds = 60 * (2 ** attempt)
            else:
                sleep_seconds = 60 * (2 ** attempt)
            logger.warning(
                "Rate limit exceeded (attempt %d/%d). Retrying in %.0f seconds.",
                attempt + 1,
                max_retries,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)
    return None  # pragma: no cover — unreachable


def get_github_token() -> str | None:
    """Retrieve a GitHub token from environment or gh CLI.

    Checks GITHUB_TOKEN env var first, then tries ``gh auth token`` via
    subprocess. Returns None if both fail.
    """
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        logger.debug("Using GITHUB_TOKEN from environment")
        return token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.debug("Using token from 'gh auth token'")
            return result.stdout.strip()
        logger.warning(
            "gh auth token returned rc=%d; stderr: %s",
            result.returncode, result.stderr.strip(),
        )
    except FileNotFoundError:
        logger.warning("gh CLI not found — install GitHub CLI or set GITHUB_TOKEN env var")
    except subprocess.TimeoutExpired:
        logger.warning("gh auth token timed out after 10s")

    logger.warning("No GitHub token available — set GITHUB_TOKEN or run 'gh auth login'")
    return None


def _parse_manifest_safe(content: str) -> dict | None:
    """Parse an Odoo ``__manifest__.py`` file content safely.

    Uses ``ast.literal_eval`` to evaluate the manifest dictionary.
    NEVER uses ``eval()``. Returns None for invalid or non-dict content.

    Args:
        content: Raw string content of a __manifest__.py file.

    Returns:
        Parsed manifest dict, or None if parsing fails or result is not a dict.
    """
    try:
        result = ast.literal_eval(content)
    except (ValueError, SyntaxError, RecursionError):
        return None

    if not isinstance(result, dict):
        return None

    return result


def _build_document_text(manifest: dict, module_name: str) -> str:
    """Build the document text for ChromaDB embedding.

    Concatenates display_name, summary, description (first 500 chars),
    category, and depends list, joined with `` | ``.

    Args:
        manifest: Parsed manifest dictionary.
        module_name: Technical module name.

    Returns:
        Concatenated text string for embedding.
    """
    display_name = manifest.get("name", module_name)
    summary = manifest.get("summary", "")
    description = manifest.get("description", "")[:500]
    category = manifest.get("category", "")
    depends = manifest.get("depends", [])
    depends_str = ", ".join(depends) if isinstance(depends, list) else str(depends)

    parts = [display_name, summary, description, category, depends_str]
    return " | ".join(part for part in parts if part)


def build_oca_index(
    token: str,
    db_path: str,
    incremental: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> int:
    """Crawl OCA GitHub repos and upsert module metadata into ChromaDB.

    Creates a ChromaDB persistent collection, iterates over all OCA
    organization repos, checks for a 19.0 branch, parses module manifests,
    and stores entries with metadata.

    Args:
        token: GitHub personal access token. Empty string raises SystemExit.
        db_path: Path to ChromaDB storage directory.
        incremental: If True, skip repos where pushed_at <= stored last_pushed.
        progress_callback: Optional callback(repos_done, total_repos).

    Returns:
        Count of modules indexed.

    Raises:
        SystemExit: If token is empty or falsy.
    """
    if not token:
        raise SystemExit("GitHub token required. Run: gh auth login")

    client = chromadb.PersistentClient(path=db_path)
    collection = client.get_or_create_collection(
        name="odoo_modules",
        metadata={"hnsw:space": "cosine"},
    )

    gh = Github(token)
    org = gh.get_organization("OCA")
    repos = list(org.get_repos())
    total_repos = len(repos)

    module_count = 0
    seen_ids: set[str] = set()

    for idx, repo in enumerate(repos):
        # Check rate limit every 10 repos
        if idx % 10 == 0 and idx > 0:
            _check_rate_limit(gh)

        # Try to get 19.0 branch; skip repo if not found
        try:
            repo.get_branch("19.0")
        except RateLimitExceededException:
            # Rate limit hit on get_branch -- wait and retry once
            _check_rate_limit(gh)
            try:
                repo.get_branch("19.0")
            except (GithubException, Exception):
                if progress_callback:
                    progress_callback(idx + 1, total_repos)
                continue
        except (GithubException, Exception):
            if progress_callback:
                progress_callback(idx + 1, total_repos)
            continue

        # Incremental: skip repos not pushed since last build
        if incremental:
            existing_meta = collection.metadata or {}
            last_pushed_stored = existing_meta.get(f"repo_pushed_{repo.name}", "")
            repo_pushed = str(repo.pushed_at) if repo.pushed_at else ""
            if repo_pushed and last_pushed_stored and repo_pushed <= last_pushed_stored:
                if progress_callback:
                    progress_callback(idx + 1, total_repos)
                continue

        # Get root contents of 19.0 branch
        try:
            contents = repo.get_contents("", ref="19.0")
        except (GithubException, OSError):
            if progress_callback:
                progress_callback(idx + 1, total_repos)
            continue

        if not isinstance(contents, list):
            contents = [contents]

        for item in contents:
            if item.type != "dir":
                continue

            module_name = item.name

            # Check for __manifest__.py in the module directory
            try:
                manifest_file = repo.get_contents(f"{module_name}/__manifest__.py", ref="19.0")
            except (GithubException, OSError):
                continue

            # Parse manifest
            manifest_content = manifest_file.decoded_content.decode("utf-8", errors="replace")
            manifest = _parse_manifest_safe(manifest_content)
            if manifest is None:
                continue

            # Skip non-installable modules
            if not manifest.get("installable", True):
                continue

            # Build document text and metadata
            document = _build_document_text(manifest, module_name)
            depends_list = manifest.get("depends", [])
            depends_str = ", ".join(depends_list) if isinstance(depends_list, list) else str(depends_list)

            entry_id = f"oca/{repo.name}/{module_name}"
            metadata = {
                "module_name": module_name,
                "oca_repo": repo.name,
                "org": "OCA",
                "category": manifest.get("category", ""),
                "depends": depends_str,
                "version": manifest.get("version", ""),
                "license": manifest.get("license", ""),
                "summary": manifest.get("summary", ""),
                "url": repo.html_url,
                "stars": repo.stargazers_count,
                "last_pushed": str(repo.pushed_at) if repo.pushed_at else "",
            }

            collection.upsert(
                ids=[entry_id],
                documents=[document],
                metadatas=[metadata],
            )
            seen_ids.add(entry_id)
            module_count += 1

        if progress_callback:
            progress_callback(idx + 1, total_repos)

    # Clean up stale entries not seen in this full rebuild
    if not incremental and seen_ids:
        all_ids = collection.get()["ids"]
        stale_ids = [eid for eid in all_ids if eid not in seen_ids]
        if stale_ids:
            collection.delete(ids=stale_ids)
            logger.info("Removed %d stale entries from index", len(stale_ids))

    # Store build timestamp in collection metadata
    build_time = datetime.now(timezone.utc).isoformat()
    collection.modify(metadata={
        **collection.metadata,
        "hnsw:space": "cosine",
        "last_built": build_time,
    })

    return module_count


def get_index_status(db_path: str | None = None) -> IndexStatus:
    """Check the status of the local ChromaDB search index.

    Args:
        db_path: Path to ChromaDB storage directory. Defaults to DEFAULT_DB_PATH.

    Returns:
        IndexStatus with index existence, module count, build timestamp, and size.
    """
    from amil_utils.search.types import IndexStatus

    resolved_path = db_path or str(DEFAULT_DB_PATH)
    path_obj = Path(resolved_path)

    if not path_obj.exists():
        return IndexStatus(
            exists=False,
            module_count=0,
            last_built=None,
            db_path=resolved_path,
            size_bytes=0,
        )

    try:
        client = chromadb.PersistentClient(path=resolved_path)
        collection = client.get_or_create_collection(
            name="odoo_modules",
            metadata={"hnsw:space": "cosine"},
        )
        count = collection.count()
        meta = collection.metadata or {}
        last_built = meta.get("last_built")

        # Calculate directory size
        size_bytes = sum(f.stat().st_size for f in path_obj.rglob("*") if f.is_file())

        return IndexStatus(
            exists=True,
            module_count=count,
            last_built=last_built,
            db_path=resolved_path,
            size_bytes=size_bytes,
        )
    except (OSError, ValueError, RuntimeError):
        return IndexStatus(
            exists=False,
            module_count=0,
            last_built=None,
            db_path=resolved_path,
            size_bytes=0,
        )
