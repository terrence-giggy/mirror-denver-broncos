"""Tests for the crawler toolkit.

This module tests the crawler agent tools for site-wide crawling,
including state management, frontier operations, link extraction,
and content storage.
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.crawl_state import CrawlState, CrawlStateStorage
from src.knowledge.page_registry import PageEntry, PageRegistry
from src.orchestration.safety import ActionRisk
from src.orchestration.toolkit.crawler import (
    DEFAULT_USER_AGENT,
    _add_to_frontier_handler,
    _check_robots_txt_handler,
    _content_hash,
    _extract_links_handler,
    _filter_urls_by_scope_handler,
    _get_crawl_statistics_handler,
    _get_domain,
    _get_frontier_urls_handler,
    _get_storage,
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


# =============================================================================
# Helper Functions Tests
# =============================================================================


class TestHelperFunctions:
    """Tests for crawler helper functions."""

    def test_source_hash(self):
        """Test source hash generation."""
        h = _source_hash("https://example.com/docs/")
        assert len(h) == 16
        assert h.isalnum()

    def test_source_hash_consistency(self):
        """Test source hash is consistent."""
        h1 = _source_hash("https://example.com/docs/")
        h2 = _source_hash("https://example.com/docs/")
        assert h1 == h2

    def test_source_hash_uniqueness(self):
        """Test different URLs produce different hashes."""
        h1 = _source_hash("https://example.com/docs/")
        h2 = _source_hash("https://example.com/blog/")
        assert h1 != h2

    def test_url_hash(self):
        """Test URL hash generation."""
        h = _url_hash("https://example.com/page")
        assert len(h) == 64  # Full SHA-256

    def test_content_hash(self):
        """Test content hash generation."""
        h = _content_hash("Hello, World!")
        assert len(h) == 64

    def test_get_domain_simple(self):
        """Test domain extraction."""
        assert _get_domain("https://example.com/page") == "example.com"

    def test_get_domain_strips_www(self):
        """Test domain extraction strips www prefix."""
        assert _get_domain("https://www.example.com/page") == "example.com"

    def test_get_domain_preserves_subdomain(self):
        """Test domain extraction preserves non-www subdomains."""
        assert _get_domain("https://docs.example.com/page") == "docs.example.com"


# =============================================================================
# Tool Registration Tests
# =============================================================================


class TestToolRegistration:
    """Tests for crawler tool registration."""

    def test_register_crawler_tools(self):
        """Test that all crawler tools are registered."""
        registry = ToolRegistry()
        register_crawler_tools(registry)

        expected_tools = [
            "load_crawl_state",
            "save_crawl_state",
            "get_crawl_statistics",
            "get_frontier_urls",
            "add_to_frontier",
            "filter_urls_by_scope",
            "check_robots_txt",
            "extract_links",
            "fetch_page",
            "store_page_content",
            "update_page_registry",
            "mark_url_visited",
        ]

        for tool_name in expected_tools:
            assert tool_name in registry, f"Tool '{tool_name}' not registered"

    def test_tool_risk_levels(self):
        """Test that tools have appropriate risk levels."""
        registry = ToolRegistry()
        register_crawler_tools(registry)

        # SAFE tools - read-only operations
        safe_tools = [
            "load_crawl_state",
            "save_crawl_state",
            "get_crawl_statistics",
            "get_frontier_urls",
            "add_to_frontier",
            "filter_urls_by_scope",
            "check_robots_txt",
            "extract_links",
            "fetch_page",
            "mark_url_visited",
        ]

        # REVIEW tools - modify persistent state
        review_tools = [
            "store_page_content",
            "update_page_registry",
        ]

        for tool_name in safe_tools:
            tool = registry.get_tool(tool_name)
            assert tool.risk_level == ActionRisk.SAFE, f"{tool_name} should be SAFE"

        for tool_name in review_tools:
            tool = registry.get_tool(tool_name)
            assert tool.risk_level == ActionRisk.REVIEW, f"{tool_name} should be REVIEW"


# =============================================================================
# State Management Tools Tests
# =============================================================================


class TestLoadCrawlState:
    """Tests for load_crawl_state tool."""

    def test_load_requires_source_url(self):
        """Test that source_url is required."""
        result = _load_crawl_state_handler({})
        assert not result.success
        assert "source_url is required" in result.output

    def test_load_invalid_scope(self):
        """Test that invalid scope is rejected."""
        result = _load_crawl_state_handler({
            "source_url": "https://example.com/",
            "scope": "invalid",
        })
        assert not result.success
        assert "Invalid scope" in result.output

    def test_load_creates_new_state(self):
        """Test creating new crawl state."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _load_crawl_state_handler({
                "source_url": "https://example.com/docs/",
                "scope": "path",
                "max_pages": 500,
                "max_depth": 5,
                "kb_root": tmp,
            })

            assert result.success
            output = result.output
            assert output["created_new"] is True
            assert output["loaded_existing"] is False
            assert output["source_url"] == "https://example.com/docs/"
            assert output["scope"] == "path"
            assert output["status"] == "pending"
            assert output["frontier_count"] == 1  # Seeded with source URL
            assert output["max_pages"] == 500
            assert output["max_depth"] == 5

    def test_load_existing_state(self):
        """Test loading existing crawl state."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create initial state
            result1 = _load_crawl_state_handler({
                "source_url": "https://example.com/docs/",
                "kb_root": tmp,
            })
            assert result1.success

            # Load again
            result2 = _load_crawl_state_handler({
                "source_url": "https://example.com/docs/",
                "kb_root": tmp,
            })

            assert result2.success
            output = result2.output
            assert output["loaded_existing"] is True

    def test_force_new_discards_existing(self):
        """Test force_new creates fresh state."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create initial state
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "max_pages": 100,
                "kb_root": tmp,
            })

            # Force new with different config
            result = _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "max_pages": 500,
                "force_new": True,
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["created_new"] is True
            assert result.output["max_pages"] == 500


class TestSaveCrawlState:
    """Tests for save_crawl_state tool."""

    def test_save_requires_source_url(self):
        """Test that source_url is required."""
        result = _save_crawl_state_handler({})
        assert not result.success
        assert "source_url is required" in result.output

    def test_save_nonexistent_state(self):
        """Test saving to nonexistent state fails."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _save_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })
            assert not result.success
            assert "No crawl state found" in result.output

    def test_save_updates_status(self):
        """Test saving with status update."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create state
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            # Save with status
            result = _save_crawl_state_handler({
                "source_url": "https://example.com/",
                "status": "crawling",
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["status"] == "crawling"

    def test_save_updates_frontier(self):
        """Test saving with updated frontier."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _save_crawl_state_handler({
                "source_url": "https://example.com/",
                "frontier": [
                    "https://example.com/page1",
                    "https://example.com/page2",
                ],
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["frontier_count"] == 2

    def test_save_updates_statistics(self):
        """Test saving with updated statistics."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _save_crawl_state_handler({
                "source_url": "https://example.com/",
                "statistics": {
                    "visited_count": 10,
                    "discovered_count": 50,
                    "failed_count": 2,
                },
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["visited_count"] == 10


class TestGetCrawlStatistics:
    """Tests for get_crawl_statistics tool."""

    def test_get_stats_requires_source_url(self):
        """Test that source_url is required."""
        result = _get_crawl_statistics_handler({})
        assert not result.success
        assert "source_url is required" in result.output

    def test_get_stats_nonexistent(self):
        """Test getting stats for nonexistent crawl."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _get_crawl_statistics_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })
            assert not result.success
            assert "No crawl state found" in result.output

    def test_get_stats_returns_all_fields(self):
        """Test that all statistics are returned."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _get_crawl_statistics_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            assert result.success
            output = result.output
            assert "frontier_count" in output
            assert "visited_count" in output
            assert "discovered_count" in output
            assert "in_scope_count" in output
            assert "out_of_scope_count" in output
            assert "failed_count" in output
            assert "skipped_count" in output
            assert "progress_percentage" in output


# =============================================================================
# Frontier Management Tools Tests
# =============================================================================


class TestGetFrontierUrls:
    """Tests for get_frontier_urls tool."""

    def test_get_frontier_requires_source_url(self):
        """Test that source_url is required."""
        result = _get_frontier_urls_handler({})
        assert not result.success

    def test_get_frontier_returns_urls(self):
        """Test getting frontier URLs."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _get_frontier_urls_handler({
                "source_url": "https://example.com/",
                "count": 5,
                "kb_root": tmp,
            })

            assert result.success
            assert len(result.output["urls"]) == 1  # Source URL
            assert result.output["urls"][0] == "https://example.com/"

    def test_get_frontier_respects_count(self):
        """Test that count limit is respected."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            # Add more URLs
            _add_to_frontier_handler({
                "source_url": "https://example.com/",
                "urls": [
                    "https://example.com/page1",
                    "https://example.com/page2",
                    "https://example.com/page3",
                ],
                "kb_root": tmp,
            })

            result = _get_frontier_urls_handler({
                "source_url": "https://example.com/",
                "count": 2,
                "kb_root": tmp,
            })

            assert result.success
            assert len(result.output["urls"]) == 2
            assert result.output["remaining_in_frontier"] == 2


class TestAddToFrontier:
    """Tests for add_to_frontier tool."""

    def test_add_requires_source_url(self):
        """Test that source_url is required."""
        result = _add_to_frontier_handler({})
        assert not result.success

    def test_add_empty_urls_succeeds(self):
        """Test adding empty URL list."""
        result = _add_to_frontier_handler({
            "source_url": "https://example.com/",
            "urls": [],
        })
        assert result.success
        assert result.output["added"] == 0

    def test_add_filters_out_of_scope(self):
        """Test that out-of-scope URLs are filtered."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/docs/",
                "scope": "path",
                "kb_root": tmp,
            })

            result = _add_to_frontier_handler({
                "source_url": "https://example.com/docs/",
                "urls": [
                    "https://example.com/docs/page1",  # In scope
                    "https://example.com/blog/post",   # Out of scope
                    "https://other.com/page",          # Out of scope
                ],
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["added"] == 1
            assert result.output["filtered_out_of_scope"] == 2

    def test_add_skips_duplicates(self):
        """Test that duplicates are not re-added."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            # Add once
            _add_to_frontier_handler({
                "source_url": "https://example.com/",
                "urls": ["https://example.com/page1"],
                "kb_root": tmp,
            })

            # Add again
            result = _add_to_frontier_handler({
                "source_url": "https://example.com/",
                "urls": ["https://example.com/page1"],
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["added"] == 0
            assert result.output["already_in_frontier"] == 1


class TestFilterUrlsByScope:
    """Tests for filter_urls_by_scope tool."""

    def test_filter_requires_source_url(self):
        """Test that source_url is required."""
        result = _filter_urls_by_scope_handler({})
        assert not result.success

    def test_filter_requires_urls(self):
        """Test that urls list is provided."""
        result = _filter_urls_by_scope_handler({
            "source_url": "https://example.com/",
        })
        assert result.success
        assert result.output["in_scope_count"] == 0

    def test_filter_path_scope(self):
        """Test path scope filtering."""
        result = _filter_urls_by_scope_handler({
            "source_url": "https://example.com/docs/",
            "urls": [
                "https://example.com/docs/guide",
                "https://example.com/docs/api/",
                "https://example.com/blog/post",
            ],
            "scope": "path",
        })

        assert result.success
        assert result.output["in_scope_count"] == 2
        assert result.output["out_of_scope_count"] == 1

    def test_filter_host_scope(self):
        """Test host scope filtering."""
        result = _filter_urls_by_scope_handler({
            "source_url": "https://example.com/",
            "urls": [
                "https://example.com/page1",
                "https://example.com/page2",
                "https://shop.example.com/",
            ],
            "scope": "host",
        })

        assert result.success
        assert result.output["in_scope_count"] == 2
        assert result.output["out_of_scope_count"] == 1

    def test_filter_domain_scope(self):
        """Test domain scope filtering."""
        result = _filter_urls_by_scope_handler({
            "source_url": "https://example.com/",
            "urls": [
                "https://example.com/page1",
                "https://shop.example.com/",
                "https://docs.example.com/",
                "https://other.com/",
            ],
            "scope": "domain",
        })

        assert result.success
        assert result.output["in_scope_count"] == 3
        assert result.output["out_of_scope_count"] == 1


# =============================================================================
# Fetch and Link Extraction Tools Tests
# =============================================================================


class TestCheckRobotsTxt:
    """Tests for check_robots_txt tool."""

    def test_check_requires_url(self):
        """Test that url is required."""
        result = _check_robots_txt_handler({})
        assert not result.success
        assert "url is required" in result.output

    def test_check_without_robots_allows(self):
        """Test that missing robots.txt allows access."""
        result = _check_robots_txt_handler({
            "url": "https://example.com/page",
        })

        assert result.success
        assert result.output["allowed"] is True
        assert "No robots.txt provided" in result.output["reason"]

    def test_check_with_robots_allows(self):
        """Test allowed URL with robots.txt."""
        robots_content = """
User-agent: *
Disallow: /private/
Allow: /public/
"""
        result = _check_robots_txt_handler({
            "url": "https://example.com/public/page",
            "robots_content": robots_content,
        })

        assert result.success
        assert result.output["allowed"] is True

    def test_check_with_robots_disallows(self):
        """Test disallowed URL with robots.txt."""
        robots_content = """
User-agent: *
Disallow: /private/
"""
        result = _check_robots_txt_handler({
            "url": "https://example.com/private/secret",
            "robots_content": robots_content,
        })

        assert result.success
        assert result.output["allowed"] is False

    def test_check_crawl_delay(self):
        """Test crawl delay is returned."""
        robots_content = """
User-agent: *
Crawl-delay: 5
Disallow:
"""
        result = _check_robots_txt_handler({
            "url": "https://example.com/page",
            "robots_content": robots_content,
        })

        assert result.success
        assert result.output["crawl_delay"] == 5.0


class TestExtractLinks:
    """Tests for extract_links tool."""

    def test_extract_requires_html(self):
        """Test that html is required."""
        result = _extract_links_handler({
            "base_url": "https://example.com/",
        })
        assert not result.success

    def test_extract_requires_base_url(self):
        """Test that base_url is required."""
        result = _extract_links_handler({
            "html": "<html></html>",
        })
        assert not result.success

    def test_extract_simple_links(self):
        """Test extracting simple links."""
        html = """
<html>
<body>
<a href="/page1">Page 1</a>
<a href="/page2">Page 2</a>
<a href="https://other.com/">External</a>
</body>
</html>
"""
        result = _extract_links_handler({
            "html": html,
            "base_url": "https://example.com/",
        })

        assert result.success
        assert result.output["total_count"] == 3

    def test_extract_with_scope_filter(self):
        """Test extracting links with scope filtering."""
        html = """
<html>
<body>
<a href="/docs/page1">Docs 1</a>
<a href="/docs/page2">Docs 2</a>
<a href="/blog/post">Blog</a>
</body>
</html>
"""
        result = _extract_links_handler({
            "html": html,
            "base_url": "https://example.com/docs/",
            "source_url": "https://example.com/docs/",
            "scope": "path",
        })

        assert result.success
        assert result.output["in_scope_count"] == 2
        assert result.output["out_of_scope_count"] == 1

    def test_extract_anchor_text(self):
        """Test that anchor text is captured."""
        html = '<a href="/page">Click Here</a>'

        result = _extract_links_handler({
            "html": html,
            "base_url": "https://example.com/",
        })

        assert result.success
        links = result.output["all_links"]
        assert len(links) == 1
        assert links[0]["anchor_text"] == "Click Here"


# =============================================================================
# Storage Tools Tests
# =============================================================================


class TestStorePageContent:
    """Tests for store_page_content tool."""

    def test_store_requires_source_url(self):
        """Test that source_url is required."""
        result = _store_page_content_handler({})
        assert not result.success
        assert "source_url is required" in result.output

    def test_store_requires_page_url(self):
        """Test that page_url is required."""
        result = _store_page_content_handler({
            "source_url": "https://example.com/",
        })
        assert not result.success
        assert "page_url is required" in result.output

    def test_store_requires_content(self):
        """Test that content is required."""
        result = _store_page_content_handler({
            "source_url": "https://example.com/",
            "page_url": "https://example.com/page",
        })
        assert not result.success
        assert "content is required" in result.output

    def test_store_content_locally(self):
        """Test storing content to local filesystem."""
        with tempfile.TemporaryDirectory() as tmp:
            # Create state first
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            with patch("src.orchestration.toolkit.crawler.resolve_github_client", return_value=None):
                with patch("src.orchestration.toolkit.crawler.paths.get_evidence_root", return_value=Path(tmp)):
                    result = _store_page_content_handler({
                        "source_url": "https://example.com/",
                        "page_url": "https://example.com/page",
                        "content": "# Test Page\n\nContent here.",
                        "kb_root": tmp,
                    })

            assert result.success
            assert result.output["stored"] is True
            assert "content_path" in result.output
            assert result.output["content_size"] > 0


class TestUpdatePageRegistry:
    """Tests for update_page_registry tool."""

    def test_update_requires_source_url(self):
        """Test that source_url is required."""
        result = _update_page_registry_handler({})
        assert not result.success

    def test_update_requires_page_url(self):
        """Test that page_url is required."""
        result = _update_page_registry_handler({
            "source_url": "https://example.com/",
        })
        assert not result.success

    def test_update_requires_status(self):
        """Test that status is required."""
        result = _update_page_registry_handler({
            "source_url": "https://example.com/",
            "page_url": "https://example.com/page",
        })
        assert not result.success

    def test_update_adds_new_page(self):
        """Test adding a new page to registry."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _update_page_registry_handler({
                "source_url": "https://example.com/",
                "page_url": "https://example.com/page1",
                "status": "fetched",
                "title": "Test Page",
                "content_hash": "abc123",
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["action"] == "added"
            assert result.output["status"] == "fetched"


class TestMarkUrlVisited:
    """Tests for mark_url_visited tool."""

    def test_mark_requires_source_url(self):
        """Test that source_url is required."""
        result = _mark_url_visited_handler({})
        assert not result.success

    def test_mark_requires_page_url(self):
        """Test that page_url is required."""
        result = _mark_url_visited_handler({
            "source_url": "https://example.com/",
        })
        assert not result.success

    def test_mark_updates_state(self):
        """Test marking URL as visited updates state."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _mark_url_visited_handler({
                "source_url": "https://example.com/",
                "page_url": "https://example.com/",
                "success": True,
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["marked_visited"] is True
            assert result.output["visited_count"] == 1
            assert result.output["frontier_remaining"] == 0

    def test_mark_failed_increments_failed_count(self):
        """Test marking URL as failed."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            # First mark source as visited
            _mark_url_visited_handler({
                "source_url": "https://example.com/",
                "page_url": "https://example.com/",
                "success": True,
                "kb_root": tmp,
            })

            # Add and mark failed URL
            _add_to_frontier_handler({
                "source_url": "https://example.com/",
                "urls": ["https://example.com/bad-page"],
                "kb_root": tmp,
            })

            result = _mark_url_visited_handler({
                "source_url": "https://example.com/",
                "page_url": "https://example.com/bad-page",
                "success": False,
                "kb_root": tmp,
            })

            assert result.success

    def test_mark_empty_frontier_completes_crawl(self):
        """Test that empty frontier marks crawl as completed."""
        with tempfile.TemporaryDirectory() as tmp:
            _load_crawl_state_handler({
                "source_url": "https://example.com/",
                "kb_root": tmp,
            })

            result = _mark_url_visited_handler({
                "source_url": "https://example.com/",
                "page_url": "https://example.com/",
                "success": True,
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["frontier_remaining"] == 0
            assert result.output["status"] == "completed"


# =============================================================================
# Integration Tests
# =============================================================================


class TestCrawlerIntegration:
    """Integration tests for the crawler toolkit."""

    def test_full_crawl_workflow(self):
        """Test a complete crawl workflow."""
        with tempfile.TemporaryDirectory() as tmp:
            source_url = "https://example.com/"

            # 1. Initialize crawl state
            init_result = _load_crawl_state_handler({
                "source_url": source_url,
                "scope": "host",
                "kb_root": tmp,
            })
            assert init_result.success
            assert init_result.output["created_new"] is True

            # 2. Get URLs from frontier
            frontier_result = _get_frontier_urls_handler({
                "source_url": source_url,
                "count": 10,
                "kb_root": tmp,
            })
            assert frontier_result.success
            assert source_url in frontier_result.output["urls"]

            # 3. Check robots.txt (no content)
            robots_result = _check_robots_txt_handler({
                "url": source_url,
            })
            assert robots_result.success
            assert robots_result.output["allowed"] is True

            # 4. Extract links from mock HTML
            html = """
<html>
<body>
<a href="/page1">Page 1</a>
<a href="/page2">Page 2</a>
</body>
</html>
"""
            extract_result = _extract_links_handler({
                "html": html,
                "base_url": source_url,
                "source_url": source_url,
                "scope": "host",
            })
            assert extract_result.success
            assert extract_result.output["in_scope_count"] == 2

            # 5. Add discovered URLs to frontier
            add_result = _add_to_frontier_handler({
                "source_url": source_url,
                "urls": extract_result.output["in_scope_urls"],
                "kb_root": tmp,
            })
            assert add_result.success
            assert add_result.output["added"] == 2

            # 6. Mark source URL as visited
            mark_result = _mark_url_visited_handler({
                "source_url": source_url,
                "page_url": source_url,
                "success": True,
                "kb_root": tmp,
            })
            assert mark_result.success
            assert mark_result.output["visited_count"] == 1

            # 7. Check statistics
            stats_result = _get_crawl_statistics_handler({
                "source_url": source_url,
                "kb_root": tmp,
            })
            assert stats_result.success
            assert stats_result.output["visited_count"] == 1
            assert stats_result.output["frontier_count"] == 2

    def test_scope_enforcement_throughout(self):
        """Test that scope is enforced throughout the workflow."""
        with tempfile.TemporaryDirectory() as tmp:
            source_url = "https://example.com/docs/"

            # Initialize with path scope
            _load_crawl_state_handler({
                "source_url": source_url,
                "scope": "path",
                "kb_root": tmp,
            })

            # Try to add out-of-scope URLs
            result = _add_to_frontier_handler({
                "source_url": source_url,
                "urls": [
                    "https://example.com/docs/guide",  # In scope
                    "https://example.com/blog/post",   # Out of scope
                    "https://example.com/docs/api",    # In scope
                    "https://other.com/page",          # Out of scope
                ],
                "kb_root": tmp,
            })

            assert result.success
            assert result.output["added"] == 2
            assert result.output["filtered_out_of_scope"] == 2
