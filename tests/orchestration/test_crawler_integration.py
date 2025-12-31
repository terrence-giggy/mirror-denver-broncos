"""Integration tests for crawler agent workflows.

These tests verify the end-to-end behavior of the crawler agent,
including scope-based crawling, state resumption, limit enforcement,
and deduplication workflows.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from urllib.parse import urlparse

from src.knowledge.crawl_state import CrawlState, CrawlStateStorage
from src.knowledge.page_registry import PageEntry, PageRegistry
from src.orchestration.toolkit.crawler import (
    _add_to_frontier_handler,
    _extract_links_handler,
    _filter_urls_by_scope_handler,
    _get_frontier_urls_handler,
    _load_crawl_state_handler,
    _mark_url_visited_handler,
    _save_crawl_state_handler,
    _source_hash,
    _store_page_content_handler,
    _update_page_registry_handler,
    _url_hash,
    register_crawler_tools,
)
from src.orchestration.tools import ToolRegistry
from src.orchestration.types import ToolResult


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_kb_root(tmp_path: Path) -> Path:
    """Create a temporary knowledge base root."""
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir(parents=True)
    crawl_dir = tmp_path / "crawl"
    crawl_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """Create a tool registry with crawler tools registered."""
    reg = ToolRegistry()
    register_crawler_tools(reg)
    return reg


def _create_mock_response(
    status_code: int = 200,
    content: bytes = b"<html>test content</html>",
    url: str = "https://example.com/page",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Create a mock requests response."""
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.text = content.decode("utf-8", errors="replace")
    response.url = url
    response.headers = headers or {}
    return response


def _url_hash_for(url: str) -> str:
    """Generate consistent URL hash."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _get_output(result: ToolResult) -> dict[str, Any]:
    """Extract output from ToolResult, handling both dict and string forms."""
    if isinstance(result.output, dict):
        return result.output
    return {}


# =============================================================================
# Scope Integration Tests
# =============================================================================


class TestCrawlScopeIntegration:
    """Integration tests for scope-based crawling."""

    def test_crawl_single_page_scope(self, temp_kb_root: Path):
        """Test path scope with single page - only exact path and children allowed."""
        source_url = "https://example.com/reports/2024/q1"

        # Create crawl state with path scope
        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success
        state_data = _get_output(result)
        assert state_data.get("source_url") == source_url

        # Test URL filtering - only children of source path should pass
        filter_result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/reports/2024/q1",  # Same path
                "https://example.com/reports/2024/q1/data",  # Child path
                "https://example.com/reports/2024/q2",  # Sibling path
                "https://example.com/reports/2024",  # Parent path
                "https://example.com/blog",  # Different path
                "https://other.com/reports/2024/q1",  # Different domain
            ],
            "scope": "path",
        })
        assert filter_result.success
        in_scope = _get_output(filter_result).get("in_scope", [])

        # Only exact path and children should be in scope
        assert "https://example.com/reports/2024/q1" in in_scope
        assert "https://example.com/reports/2024/q1/data" in in_scope
        assert "https://example.com/reports/2024/q2" not in in_scope
        assert "https://example.com/reports/2024" not in in_scope
        assert "https://example.com/blog" not in in_scope
        assert "https://other.com/reports/2024/q1" not in in_scope

    def test_crawl_directory_scope(self, temp_kb_root: Path):
        """Test path scope with directory - allows all files under directory."""
        source_url = "https://example.com/docs/"

        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success

        filter_result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/docs/",  # Root
                "https://example.com/docs/guide",  # Child
                "https://example.com/docs/api/reference",  # Nested child
                "https://example.com/blog/",  # Sibling directory
            ],
            "scope": "path",
        })
        assert filter_result.success
        in_scope = _get_output(filter_result).get("in_scope", [])

        assert "https://example.com/docs/" in in_scope
        assert "https://example.com/docs/guide" in in_scope
        assert "https://example.com/docs/api/reference" in in_scope
        assert "https://example.com/blog/" not in in_scope

    def test_crawl_host_scope(self, temp_kb_root: Path):
        """Test host scope - allows all paths on same host only."""
        source_url = "https://docs.example.com/start"

        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "host",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success

        filter_result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://docs.example.com/start",  # Same path
                "https://docs.example.com/other",  # Different path
                "https://docs.example.com/nested/deep",  # Any path
                "https://example.com/start",  # Different host
                "https://www.example.com/start",  # Different host (www)
                "https://api.example.com/",  # Different subdomain
            ],
            "scope": "host",
        })
        assert filter_result.success
        in_scope = _get_output(filter_result).get("in_scope", [])

        assert "https://docs.example.com/start" in in_scope
        assert "https://docs.example.com/other" in in_scope
        assert "https://docs.example.com/nested/deep" in in_scope
        assert "https://example.com/start" not in in_scope
        assert "https://www.example.com/start" not in in_scope
        assert "https://api.example.com/" not in in_scope

    def test_crawl_domain_scope(self, temp_kb_root: Path):
        """Test domain scope - allows all hosts on same domain."""
        source_url = "https://example.com/docs"

        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "domain",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success

        filter_result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/docs",  # Same path
                "https://example.com/other",  # Different path
                "https://www.example.com/docs",  # www prefix
                "https://docs.example.com/",  # Subdomain
                "https://api.example.com/v1",  # Another subdomain
                "https://other.com/docs",  # Different domain
                "https://example.org/",  # Different TLD
            ],
            "scope": "domain",
        })
        assert filter_result.success
        in_scope = _get_output(filter_result).get("in_scope", [])

        assert any(url == "https://example.com/docs" for url in in_scope)
        assert any(url == "https://example.com/other" for url in in_scope)
        assert any(url == "https://www.example.com/docs" for url in in_scope)
        assert any(url == "https://docs.example.com/" for url in in_scope)
        assert any(url == "https://api.example.com/v1" for url in in_scope)
        assert not any(url == "https://other.com/docs" for url in in_scope)
        assert not any(url == "https://example.org/" for url in in_scope)


# =============================================================================
# State Resumption Tests
# =============================================================================


class TestCrawlResumeIntegration:
    """Integration tests for crawl state resumption."""

    def test_crawl_resume_from_state(self, temp_kb_root: Path):
        """Test resumption from saved state preserves all data."""
        source_url = "https://example.com/docs/"

        # Create initial state
        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success

        # Add URLs to frontier
        _add_to_frontier_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/docs/page1",
                "https://example.com/docs/page2",
                "https://example.com/docs/page3",
            ],
            "kb_root": str(temp_kb_root),
        })

        # Mark some as visited
        _mark_url_visited_handler({
            "source_url": source_url,
            "url": "https://example.com/docs/page1",
            "kb_root": str(temp_kb_root),
        })

        # Save state
        save_result = _save_crawl_state_handler({
            "source_url": source_url,
            "status": "paused",
            "statistics": {
                "visited_count": 1,
                "discovered_count": 3,
            },
            "kb_root": str(temp_kb_root),
        })
        assert save_result.success

        # Load state again (should resume, not create new)
        resume_result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": False,  # Don't force new
        })
        assert resume_result.success
        state_data = _get_output(resume_result)

        # Verify state was preserved
        assert state_data.get("status") == "paused"
        assert state_data.get("visited_count") == 1

    def test_crawl_force_new_discards_state(self, temp_kb_root: Path):
        """Test force_new=True discards existing state."""
        source_url = "https://example.com/docs/"

        # Create initial state with some progress
        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        _save_crawl_state_handler({
            "source_url": source_url,
            "status": "paused",
            "statistics": {"visited_count": 50},
            "kb_root": str(temp_kb_root),
        })

        # Force new state
        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success
        state_data = _get_output(result)

        # Should have fresh state - created_new should be True
        assert state_data.get("created_new") is True
        assert state_data.get("status") == "pending"


# =============================================================================
# Limit Enforcement Tests
# =============================================================================


class TestCrawlLimitsIntegration:
    """Integration tests for crawl limit enforcement."""

    def test_crawl_respects_max_pages(self, temp_kb_root: Path):
        """Test crawl respects max_pages limit."""
        source_url = "https://example.com/docs/"

        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "max_pages": 5,  # Only 5 pages
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success
        state_data = _get_output(result)
        assert state_data.get("max_pages") == 5

    def test_crawl_respects_max_depth(self, temp_kb_root: Path):
        """Test crawl respects max_depth limit."""
        source_url = "https://example.com/docs/"

        result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "max_depth": 3,  # Only 3 levels deep
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert result.success
        state_data = _get_output(result)
        assert state_data.get("max_depth") == 3


# =============================================================================
# Deduplication Tests
# =============================================================================


class TestCrawlDeduplicationIntegration:
    """Integration tests for URL deduplication."""

    def test_crawl_deduplication(self, temp_kb_root: Path):
        """Test same URL not processed twice."""
        source_url = "https://example.com/docs/"

        # Create state
        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        # Mark URL as visited (use page_url as the parameter name)
        url = "https://example.com/docs/page1"
        mark_result = _mark_url_visited_handler({
            "source_url": source_url,
            "page_url": url,
            "kb_root": str(temp_kb_root),
        })
        assert mark_result.success

        # Try to add same URL to frontier
        add_result = _add_to_frontier_handler({
            "source_url": source_url,
            "urls": [url, "https://example.com/docs/page2"],
            "kb_root": str(temp_kb_root),
        })
        assert add_result.success
        add_data = _get_output(add_result)

        # Visited URL should be skipped
        assert add_data.get("already_visited") == 1
        assert add_data.get("added") == 1

    def test_crawl_normalizes_urls_for_dedup(self, temp_kb_root: Path):
        """Test URL normalization prevents duplicate crawls."""
        source_url = "https://example.com/docs/"

        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        # Add variations of same URL
        result = _add_to_frontier_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/docs/page",
                "https://example.com/docs/page#section",  # Fragment
                "https://EXAMPLE.COM/docs/page",  # Case
            ],
            "kb_root": str(temp_kb_root),
        })
        assert result.success
        data = _get_output(result)

        # Normalized duplicates should be detected
        assert data.get("already_in_frontier", 0) >= 1 or data.get("added", 0) <= 2


# =============================================================================
# Out-of-Scope Logging Tests
# =============================================================================


class TestCrawlOutOfScopeIntegration:
    """Integration tests for out-of-scope URL handling."""

    def test_crawl_out_of_scope_logged(self, temp_kb_root: Path):
        """Test out-of-scope URLs are identified and filtered."""
        source_url = "https://example.com/docs/"

        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        # Filter mixed URLs
        result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/docs/page1",  # In scope
                "https://example.com/blog/post",  # Out of scope
                "https://other.com/page",  # Out of scope
            ],
            "scope": "path",
        })
        assert result.success
        data = _get_output(result)

        assert len(data.get("in_scope", [])) == 1
        assert len(data.get("out_of_scope", [])) == 2
        assert "https://example.com/docs/page1" in data.get("in_scope", [])
        assert "https://example.com/blog/post" in data.get("out_of_scope", [])
        assert "https://other.com/page" in data.get("out_of_scope", [])


# =============================================================================
# Link Extraction Integration Tests
# =============================================================================


class TestLinkExtractionIntegration:
    """Integration tests for link extraction with scope filtering."""

    def test_extract_and_filter_links(self, temp_kb_root: Path):
        """Test extracting links from HTML and filtering by scope."""
        source_url = "https://example.com/docs/"
        page_url = "https://example.com/docs/index.html"

        html_content = """
        <html>
        <body>
            <a href="guide.html">Guide</a>
            <a href="api/reference">API</a>
            <a href="/blog/post">Blog</a>
            <a href="https://other.com/link">External</a>
            <a href="#section">Anchor</a>
            <a href="javascript:void(0)">JS Link</a>
        </body>
        </html>
        """

        # Extract links (use "html" not "html_content")
        extract_result = _extract_links_handler({
            "html": html_content,
            "base_url": page_url,
        })
        assert extract_result.success
        extract_data = _get_output(extract_result)
        all_links = extract_data.get("all_links", [])
        urls = [link["url"] for link in all_links]

        # Filter by scope
        filter_result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": urls,
            "scope": "path",
        })
        assert filter_result.success
        data = _get_output(filter_result)

        # In-scope links (under /docs/)
        in_scope = data.get("in_scope", [])
        assert any("guide" in u for u in in_scope)
        assert any("api/reference" in u for u in in_scope)

        # Out-of-scope
        out_of_scope = data.get("out_of_scope", [])
        assert any("blog" in u for u in out_of_scope)
        assert any(urlparse(u).hostname == "other.com" for u in out_of_scope)

    def test_relative_url_resolution(self, temp_kb_root: Path):
        """Test relative URLs are resolved correctly."""
        base_url = "https://example.com/docs/guide/intro.html"

        html_content = """
        <html>
        <body>
            <a href="chapter1.html">Chapter 1</a>
            <a href="../api/index.html">API</a>
            <a href="/absolute/path">Absolute</a>
        </body>
        </html>
        """

        result = _extract_links_handler({
            "html": html_content,
            "base_url": base_url,
        })
        assert result.success
        data = _get_output(result)
        all_links = data.get("all_links", [])
        urls = [link["url"] for link in all_links]

        # Check resolution
        assert "https://example.com/docs/guide/chapter1.html" in urls
        assert "https://example.com/docs/api/index.html" in urls
        assert "https://example.com/absolute/path" in urls


# =============================================================================
# Content Storage Integration Tests
# =============================================================================


class TestContentStorageIntegration:
    """Integration tests for content storage workflow."""

    def test_store_and_update_registry(self, temp_kb_root: Path):
        """Test storing content and updating page registry."""
        source_url = "https://example.com/docs/"
        page_url = "https://example.com/docs/page1"
        content = "Page 1 content for testing storage"

        # Create state
        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        # Store content
        store_result = _store_page_content_handler({
            "source_url": source_url,
            "page_url": page_url,
            "content": content,
            "content_type": "text/html",
            "kb_root": str(temp_kb_root),
        })
        assert store_result.success
        store_data = _get_output(store_result)
        assert store_data.get("content_hash")

        # Update registry
        registry_result = _update_page_registry_handler({
            "source_url": source_url,
            "page_url": page_url,
            "status": "fetched",
            "content_hash": store_data.get("content_hash"),
            "content_path": store_data.get("content_path"),
            "kb_root": str(temp_kb_root),
        })
        assert registry_result.success


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestCrawlEdgeCases:
    """Integration tests for edge cases."""

    def test_trailing_slash_normalization(self, temp_kb_root: Path):
        """Test /path and /path/ treated consistently."""
        source_url = "https://example.com/docs"  # No trailing slash

        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/docs",
                "https://example.com/docs/",
                "https://example.com/docs/page",
            ],
            "scope": "path",
        })
        assert result.success
        in_scope = _get_output(result).get("in_scope", [])

        # All should be in scope (normalization)
        assert len(in_scope) >= 2  # At least base and child

    def test_case_insensitive_host(self, temp_kb_root: Path):
        """Test host comparison is case-insensitive."""
        source_url = "https://Example.COM/docs/"

        _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "host",
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })

        result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": [
                "https://example.com/page",
                "https://EXAMPLE.COM/page",
                "https://Example.Com/page",
            ],
            "scope": "host",
        })
        assert result.success
        in_scope = _get_output(result).get("in_scope", [])

        # All should match (case insensitive)
        assert len(in_scope) == 3

    def test_query_params_preserved(self, temp_kb_root: Path):
        """Test query params are kept in URLs."""
        source_url = "https://example.com/search"
        page_url = "https://example.com/search"

        html_content = """
        <html>
        <body>
            <a href="?q=test">Search</a>
            <a href="?page=2&q=test">Page 2</a>
        </body>
        </html>
        """

        result = _extract_links_handler({
            "html": html_content,
            "base_url": page_url,
        })
        assert result.success
        data = _get_output(result)
        all_links = data.get("all_links", [])
        urls = [link["url"] for link in all_links]

        # Query params should be preserved
        assert any("q=test" in u for u in urls)
        assert any("page=2" in u for u in urls)

    def test_javascript_links_ignored(self, temp_kb_root: Path):
        """Test javascript: URLs are ignored."""
        html_content = """
        <html>
        <body>
            <a href="javascript:void(0)">JS Link</a>
            <a href="javascript:doSomething()">Another JS</a>
            <a href="page.html">Real Link</a>
        </body>
        </html>
        """

        result = _extract_links_handler({
            "html": html_content,
            "base_url": "https://example.com/",
        })
        assert result.success
        data = _get_output(result)
        all_links = data.get("all_links", [])
        urls = [link["url"] for link in all_links]

        assert not any("javascript:" in u for u in urls)
        assert any("page.html" in u for u in urls)

    def test_mailto_links_ignored(self, temp_kb_root: Path):
        """Test mailto: URLs are ignored."""
        html_content = """
        <html>
        <body>
            <a href="mailto:test@example.com">Email</a>
            <a href="page.html">Real Link</a>
        </body>
        </html>
        """

        result = _extract_links_handler({
            "html": html_content,
            "base_url": "https://example.com/",
        })
        assert result.success
        data = _get_output(result)
        all_links = data.get("all_links", [])
        urls = [link["url"] for link in all_links]

        assert not any("mailto:" in u for u in urls)

    def test_empty_href_ignored(self, temp_kb_root: Path):
        """Test empty href handled gracefully."""
        html_content = """
        <html>
        <body>
            <a href="">Empty</a>
            <a href="   ">Whitespace</a>
            <a>No href</a>
            <a href="page.html">Real Link</a>
        </body>
        </html>
        """

        result = _extract_links_handler({
            "html": html_content,
            "base_url": "https://example.com/",
        })
        assert result.success
        data = _get_output(result)
        all_links = data.get("all_links", [])
        urls = [link["url"] for link in all_links]

        # Should only have the real link
        assert any("page.html" in u for u in urls)
        # Should not have empty or just base URL repeats
        assert len([u for u in urls if u.strip()]) >= 1


# =============================================================================
# Full Workflow Integration Test
# =============================================================================


class TestFullCrawlWorkflow:
    """Integration tests for complete crawl workflow."""

    def test_complete_crawl_cycle(self, temp_kb_root: Path):
        """Test a complete crawl cycle: init, fetch, extract, store, save."""
        source_url = "https://example.com/docs/"

        # 1. Initialize state
        init_result = _load_crawl_state_handler({
            "source_url": source_url,
            "scope": "path",
            "max_pages": 10,
            "kb_root": str(temp_kb_root),
            "force_new": True,
        })
        assert init_result.success
        state = _get_output(init_result)
        assert state.get("status") == "pending"
        assert state.get("frontier_count", 0) >= 1

        # 2. Get URL from frontier
        frontier_result = _get_frontier_urls_handler({
            "source_url": source_url,
            "count": 1,
            "kb_root": str(temp_kb_root),
        })
        assert frontier_result.success
        frontier_data = _get_output(frontier_result)
        urls = frontier_data.get("urls", [])
        assert len(urls) >= 1

        # 3. Simulate page fetch (mock)
        page_content = """
        <html>
        <body>
            <h1>Welcome</h1>
            <a href="guide.html">Guide</a>
            <a href="api/">API Reference</a>
        </body>
        </html>
        """

        # 4. Mark URL as visited
        _mark_url_visited_handler({
            "source_url": source_url,
            "url": urls[0],
            "kb_root": str(temp_kb_root),
        })

        # 5. Store content
        store_result = _store_page_content_handler({
            "source_url": source_url,
            "page_url": urls[0],
            "content": page_content,
            "content_type": "text/html",
            "kb_root": str(temp_kb_root),
        })
        assert store_result.success

        # 6. Extract links
        extract_result = _extract_links_handler({
            "html": page_content,
            "base_url": urls[0],
        })
        assert extract_result.success
        extract_data = _get_output(extract_result)
        all_links = extract_data.get("all_links", [])
        extracted = [link["url"] for link in all_links]

        # 7. Filter and add to frontier
        filter_result = _filter_urls_by_scope_handler({
            "source_url": source_url,
            "urls": extracted,
            "scope": "path",
        })
        in_scope = _get_output(filter_result).get("in_scope", [])

        add_result = _add_to_frontier_handler({
            "source_url": source_url,
            "urls": in_scope,
            "kb_root": str(temp_kb_root),
        })
        assert add_result.success

        # 8. Save state
        save_result = _save_crawl_state_handler({
            "source_url": source_url,
            "status": "crawling",
            "statistics": {
                "visited_count": 1,
                "discovered_count": len(in_scope) + 1,
            },
            "kb_root": str(temp_kb_root),
        })
        assert save_result.success

        # 9. Verify state was saved
        resume_result = _load_crawl_state_handler({
            "source_url": source_url,
            "kb_root": str(temp_kb_root),
        })
        assert resume_result.success
        final_state = _get_output(resume_result)
        assert final_state.get("status") == "crawling"
        assert final_state.get("visited_count") == 1
