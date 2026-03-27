"""Context7 REST API client for querying live Odoo documentation.

Queries the Context7 service (https://context7.com) for Odoo documentation
snippets as a supplement to the static knowledge base. Degrades gracefully
when unconfigured (no API key) or when Context7 is unavailable.

Uses only stdlib modules (urllib.request, urllib.error, urllib.parse, json,
os, logging, dataclasses, hashlib, time, pathlib) -- no third-party dependencies.

Exports:
    Context7Config           -- frozen config dataclass (api_key, base_url, timeout)
    DocSnippet               -- frozen dataclass for a documentation result
    Context7Client           -- main client class with resolve + query
    build_context7_from_env  -- factory that reads CONTEXT7_API_KEY from env
    context7_enrich          -- batch enrichment: detect patterns, query, cache, truncate
    PATTERN_QUERIES          -- dict mapping pattern names to Context7 query strings
    _context7_get            -- low-level HTTP GET helper (for testing)
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("amil.context7")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Context7Config:
    """Immutable configuration for the Context7 REST API client.

    Attributes:
        api_key:  Bearer token for Context7 authentication (empty = unconfigured).
        base_url: Root URL for the Context7 v2 API.
        timeout:  HTTP request timeout in seconds.
    """

    api_key: str = ""
    base_url: str = "https://context7.com/api/v2"
    timeout: int = 10


@dataclass(frozen=True)
class DocSnippet:
    """A single documentation snippet returned by Context7.

    Attributes:
        title:      Snippet title (e.g. "Model Fields").
        content:    Snippet body text.
        source_url: Original documentation URL (may be empty).
    """

    title: str
    content: str
    source_url: str = ""


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

def _context7_get(url: str, api_key: str, timeout: int = 10) -> dict | list | None:
    """Perform an HTTP GET against a Context7 endpoint.

    Adds an ``Authorization: Bearer`` header when *api_key* is non-empty.
    Returns parsed JSON on success, or ``None`` on any network / parse error.

    Args:
        url:     Fully-qualified URL to GET.
        api_key: Bearer token (empty string skips auth header).
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON (dict or list), or None on failure.
    """
    request = urllib.request.Request(url)
    if api_key:
        request.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            TimeoutError, OSError) as exc:
        logger.warning("Context7 GET %s failed: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Context7Client:
    """REST client for the Context7 documentation service.

    When *config* has an empty ``api_key`` the client is considered
    unconfigured and all query methods return empty results without making
    any HTTP requests.

    Usage::

        client = Context7Client(Context7Config(api_key="..."))
        snippets = client.query_docs("how to define Many2one fields in Odoo")
    """

    def __init__(self, config: Context7Config | None = None) -> None:
        self._config: Context7Config = config if config is not None else Context7Config()
        self._odoo_library_id: str | None = None

    @property
    def is_configured(self) -> bool:
        """Return True when an API key is present."""
        return bool(self._config.api_key)

    # -- Library resolution ------------------------------------------------

    def resolve_odoo_library(self) -> str | None:
        """Resolve the Odoo library ID from Context7.

        The result is cached after the first successful lookup so that
        repeated calls do not hit the network.

        Returns:
            Library ID string, or None when unconfigured / on failure.
        """
        if self._odoo_library_id is not None:
            return self._odoo_library_id

        if not self.is_configured:
            return None

        search_query = urllib.parse.quote_plus("odoo framework development")
        url = (
            f"{self._config.base_url}/libs/search"
            f"?libraryName=odoo&query={search_query}"
        )

        data = _context7_get(url, self._config.api_key, self._config.timeout)
        if not isinstance(data, list) or len(data) == 0:
            logger.warning("Context7 library search returned no results")
            return None

        try:
            raw_id = data[0]["id"]
            if raw_id is None:
                logger.warning("Context7 library search returned null id")
                return None
            library_id = str(raw_id)
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Context7 library search response malformed: %s", exc)
            return None

        self._odoo_library_id = library_id
        return library_id

    # -- Documentation querying --------------------------------------------

    def query_docs(self, query: str) -> list[DocSnippet]:
        """Query Context7 for Odoo documentation snippets.

        Returns an empty list (never raises) when:
        - The client is unconfigured (no API key).
        - Library resolution fails.
        - The HTTP request fails or returns invalid data.

        Args:
            query: Natural-language search query.

        Returns:
            List of DocSnippet results (may be empty).
        """
        if not self.is_configured:
            return []

        try:
            library_id = self.resolve_odoo_library()
            if library_id is None:
                return []

            encoded_query = urllib.parse.quote_plus(query)
            url = (
                f"{self._config.base_url}/context"
                f"?libraryId={library_id}&query={encoded_query}"
            )

            data = _context7_get(url, self._config.api_key, self._config.timeout)
            if not isinstance(data, list):
                logger.warning("Context7 docs query returned non-list: %s", type(data))
                return []

            return [
                DocSnippet(
                    title=str(item.get("title", "")),
                    content=str(item.get("content", "")),
                    source_url=str(item.get("sourceUrl", "")),
                )
                for item in data
            ]

        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
                TimeoutError, OSError, KeyError, TypeError) as exc:
            logger.warning("Context7 query_docs error (degrading gracefully): %s", exc)
            return []


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_context7_from_env() -> Context7Client:
    """Build a Context7Client from the CONTEXT7_API_KEY environment variable.

    Returns an unconfigured client (no-op) when the variable is absent.
    Never raises.
    """
    api_key = os.environ.get("CONTEXT7_API_KEY", "")
    config = Context7Config(api_key=api_key)
    return Context7Client(config)


# ---------------------------------------------------------------------------
# Enrichment pipeline (PIPE-01)
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS: int = int(os.environ.get("CONTEXT7_CACHE_TTL", "86400"))
TOKENS_PER_QUERY: int = 500
CHARS_PER_TOKEN: int = 4  # Conservative estimate for English text

PATTERN_QUERIES: dict[str, str] = {
    "mail_thread": "odoo mail.thread mixin chatter integration tracking fields message_post",
    "monetary": "odoo Monetary field currency_id company_currency_id accounting",
    "approval": "odoo approval workflow state field action buttons groups permissions",
    "computed": "odoo computed field @api.depends store inverse compute method",
    "reports": "odoo QWeb report template ir.actions.report PDF rendering",
}


def _detect_patterns(spec: dict[str, Any]) -> list[str]:
    """Detect which Context7 query patterns are relevant for this spec.

    Scans the preprocessed spec for active patterns and returns a list of
    pattern names in deterministic order: mail_thread, monetary, approval,
    computed, reports.

    Args:
        spec: Preprocessed module specification dict.

    Returns:
        List of matched pattern name strings.
    """
    patterns: list[str] = []
    models: list[dict[str, Any]] = spec.get("models", [])

    # mail_thread: mail in module depends
    if "mail" in spec.get("depends", []):
        patterns.append("mail_thread")

    # monetary: any model with Monetary field OR Float field with "amount" in name
    if any(
        any(
            f.get("type") == "Monetary"
            or (f.get("type") == "Float" and "amount" in f.get("name", "").lower())
            for f in m.get("fields", [])
        )
        for m in models
    ):
        patterns.append("monetary")

    # approval: any model with has_approval flag (set by preprocessor)
    if any(m.get("has_approval") for m in models):
        patterns.append("approval")

    # computed: any model with computed fields
    if any(any(f.get("compute") for f in m.get("fields", [])) for m in models):
        patterns.append("computed")

    # reports: spec has reports section
    if spec.get("reports"):
        patterns.append("reports")

    return patterns


def _truncate_to_tokens(text: str, max_tokens: int = TOKENS_PER_QUERY) -> str:
    """Truncate text to approximate token budget.

    Uses a conservative estimate of 4 characters per token. Truncates at
    the nearest word boundary to avoid mid-word cuts.

    Args:
        text:       Input text to truncate.
        max_tokens: Maximum token budget (default 500).

    Returns:
        Original text if within budget, or truncated text ending with "...".
    """
    max_chars = max_tokens * CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    # Try to truncate at word boundary
    truncated = text[:max_chars]
    parts = truncated.rsplit(" ", 1)
    if len(parts) > 1:
        return parts[0] + "..."
    # No space found -- truncate at max_chars directly
    return truncated + "..."


def _cache_key(query: str, odoo_version: str) -> str:
    """Generate a deterministic SHA256 cache key from query + version.

    Args:
        query:        The Context7 query string.
        odoo_version: Target Odoo version (e.g. "17.0", "19.0").

    Returns:
        Hex-encoded SHA256 hash string.
    """
    raw = f"{query}|{odoo_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_read(cache_dir: Path, key: str) -> str | None:
    """Read cached response if fresh (within TTL).

    Returns the cached response string when the entry exists and is within
    the 24-hour TTL window. Returns None on miss, stale, or any I/O error.

    Args:
        cache_dir: Directory containing cache files.
        key:       Cache key (SHA256 hex digest).

    Returns:
        Cached response string, or None.
    """
    cache_file = cache_dir / f"{key}.json"
    try:
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        if time.time() - data["ts"] < CACHE_TTL_SECONDS:
            return data["response"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.debug("Cache miss for key %s: %s", key, exc)
    return None


def _cache_write(cache_dir: Path, key: str, query: str, response: str) -> None:
    """Write response to cache. Failures logged as warnings, never raised.

    Creates the cache directory on demand if it doesn't exist.

    Args:
        cache_dir: Directory for cache files (created if missing).
        key:       Cache key (SHA256 hex digest).
        query:     Original query string (stored for debugging).
        response:  Response text to cache.
    """
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / f"{key}.json"
        cache_file.write_text(
            json.dumps({
                "query": query,
                "response": response,
                "ts": time.time(),
            }),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Context7 cache write failed: %s", exc)


def context7_enrich(
    spec: dict[str, Any],
    client: Context7Client | None = None,
    *,
    cache_dir: Path | None = None,
    fresh: bool = False,
    odoo_version: str = "19.0",
) -> dict[str, str]:
    """Pre-fetch Context7 docs for detected patterns and return c7_hints dict.

    Detects active patterns from the preprocessed spec, queries Context7 for
    each pattern (with disk caching), truncates responses to a token budget,
    and returns a dict mapping pattern names to documentation hint strings.

    Returns ``{}`` when *client* is unconfigured or no patterns are detected.
    Never raises -- all errors are logged as warnings.

    Args:
        spec:         Preprocessed module specification dict.
        client:       Context7Client instance (None = skip).
        cache_dir:    Optional disk cache directory for response caching.
        fresh:        If True, bypass cache and force re-query.
        odoo_version: Target Odoo version for cache keying (default "19.0").

    Returns:
        Dict mapping pattern names to truncated documentation hint strings.
        Only patterns with non-empty results are included.
    """
    if client is None or not client.is_configured:
        logger.info("Context7: skipped (no API key configured)")
        return {}

    try:
        patterns = _detect_patterns(spec)
        if not patterns:
            return {}

        logger.info(
            "Context7: querying %d patterns (%s)",
            len(patterns),
            ", ".join(patterns),
        )

        hints: dict[str, str] = {}
        for pattern in patterns:
            query = PATTERN_QUERIES[pattern]

            # Check cache (unless fresh=True)
            if cache_dir is not None and not fresh:
                key = _cache_key(query, odoo_version)
                cached = _cache_read(cache_dir, key)
                if cached is not None:
                    logger.info("Context7: cache hit for %s", pattern)
                    hints[pattern] = cached
                    continue

            # Query Context7
            snippets = client.query_docs(query)
            if not snippets:
                continue

            # Concatenate snippets with titles as section headers
            combined = "\n\n".join(
                f"## {s.title}\n{s.content}" for s in snippets
            )
            truncated = _truncate_to_tokens(combined)

            # Write to cache
            if cache_dir is not None:
                key = _cache_key(query, odoo_version)
                _cache_write(cache_dir, key, query, truncated)

            hints[pattern] = truncated

        return hints

    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError,
            TimeoutError, OSError, KeyError, TypeError) as exc:
        logger.warning(
            "Context7 enrichment failed (degrading gracefully): %s", exc,
        )
        return {}
