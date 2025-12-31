"""Crawler agent tool registrations for the orchestration runtime.

This toolkit provides tools for site-wide crawling of sources within
their defined scope boundaries. The crawler discovers pages via link
extraction, respects robots.txt, and stores content in a sharded structure.

Key capabilities:
1. Crawl State Management: Load, save, and resume crawl state across runs
2. Frontier Management: Get and add URLs to the crawl frontier
3. Page Fetching: Fetch pages with politeness delays
4. Link Extraction: Extract and filter links by scope
5. Content Storage: Store content in sharded directories
6. Robots.txt Compliance: Check URL allowance before fetching
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from src import paths
from src.knowledge.crawl_state import CrawlState, CrawlStateStorage
from src.knowledge.page_registry import PageEntry, PageRegistry
from src.knowledge.storage import SourceRegistry
from src.parsing.link_extractor import extract_links, extract_urls
from src.parsing.robots import RobotsChecker, parse_robots_txt
from src.parsing.url_scope import (
    filter_urls_by_scope,
    is_url_in_scope,
    normalize_url,
)

from ..safety import ActionRisk
from ..tools import ToolDefinition, ToolRegistry
from ..types import ToolResult
from ._github_context import resolve_github_client


# Default user agent for crawler
DEFAULT_USER_AGENT = "SpeculumPrincipum-Crawler/1.0"


def register_crawler_tools(registry: ToolRegistry) -> None:
    """Register all crawler agent tools with the registry."""
    _register_state_tools(registry)
    _register_frontier_tools(registry)
    _register_fetch_tools(registry)
    _register_storage_tools(registry)


# =============================================================================
# Helper Functions
# =============================================================================


def _source_hash(url: str) -> str:
    """Generate a consistent hash for a source URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _url_hash(url: str) -> str:
    """Generate a consistent hash for a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _content_hash(content: str) -> str:
    """Generate SHA-256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_domain(url: str) -> str:
    """Extract domain from URL, stripping www prefix."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _get_storage(kb_root: str | None = None) -> CrawlStateStorage:
    """Get CrawlStateStorage instance."""
    root = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    github_client = resolve_github_client()
    return CrawlStateStorage(root=root, github_client=github_client)


def _get_registry(kb_root: str | None = None) -> SourceRegistry:
    """Get SourceRegistry instance."""
    root = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    github_client = resolve_github_client()
    return SourceRegistry(root=root, github_client=github_client)


def _get_page_registry(kb_root: str | None = None) -> PageRegistry:
    """Get PageRegistry instance."""
    root = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    github_client = resolve_github_client()
    return PageRegistry(
        root=root,
        github_client=github_client,
    )


# =============================================================================
# State Management Tools
# =============================================================================


def _register_state_tools(registry: ToolRegistry) -> None:
    """Register crawl state management tools."""

    registry.register_tool(
        ToolDefinition(
            name="load_crawl_state",
            description=(
                "Load existing crawl state for a source URL, or create a new one. "
                "Returns the state with frontier, visited URLs, and statistics."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL defining the crawl boundary.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["path", "host", "domain"],
                        "description": "Crawl scope constraint. Default: 'path'.",
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": "Maximum pages to crawl. Default: 10000.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum link depth from source. Default: 10.",
                    },
                    "force_new": {
                        "type": "boolean",
                        "description": "Force creating new state (discard existing).",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url"],
                "additionalProperties": False,
            },
            handler=_load_crawl_state_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="save_crawl_state",
            description=(
                "Save the current crawl state to persistent storage. "
                "Should be called after processing pages to enable resume."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["crawling", "paused", "completed"],
                        "description": "New status for the crawl.",
                    },
                    "frontier": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Updated frontier URLs.",
                    },
                    "visited_hashes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Hashes of visited URLs.",
                    },
                    "statistics": {
                        "type": "object",
                        "description": "Updated statistics (visited_count, discovered_count, etc.).",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url"],
                "additionalProperties": False,
            },
            handler=_save_crawl_state_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_crawl_statistics",
            description="Get current statistics for a crawl (visited, discovered, failed counts).",
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url"],
                "additionalProperties": False,
            },
            handler=_get_crawl_statistics_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _load_crawl_state_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for load_crawl_state tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    scope = arguments.get("scope", "path")
    if scope not in ("path", "host", "domain"):
        return ToolResult(success=False, output=f"Invalid scope: {scope}")

    max_pages = arguments.get("max_pages", 10000)
    max_depth = arguments.get("max_depth", 10)
    force_new = arguments.get("force_new", False)
    kb_root = arguments.get("kb_root")

    storage = _get_storage(kb_root)

    # Try to load existing state
    if not force_new:
        state = storage.load_state(source_url)
        if state is not None:
            return ToolResult(
                success=True,
                output={
                    "loaded_existing": True,
                    "source_url": state.source_url,
                    "source_hash": state.source_hash,
                    "scope": state.scope,
                    "status": state.status,
                    "frontier_count": len(state.frontier),
                    "frontier_overflow_count": state.frontier_overflow_count,
                    "visited_count": state.visited_count,
                    "discovered_count": state.discovered_count,
                    "in_scope_count": state.in_scope_count,
                    "out_of_scope_count": state.out_of_scope_count,
                    "failed_count": state.failed_count,
                    "skipped_count": state.skipped_count,
                    "max_pages": state.max_pages,
                    "max_depth": state.max_depth,
                    "started_at": state.started_at.isoformat() if state.started_at else None,
                    "last_activity": state.last_activity.isoformat() if state.last_activity else None,
                },
            )

    # Create new state
    source_hash = _source_hash(source_url)
    domain = _get_domain(source_url)
    parsed = urlparse(source_url)
    path_hash = _source_hash(parsed.path or "/")

    state = CrawlState(
        source_url=source_url,
        source_hash=source_hash,
        scope=scope,
        status="pending",
        max_pages=max_pages,
        max_depth=max_depth,
        content_root=f"evidence/parsed/{domain}/{path_hash}",
        registry_path=f"crawls/{source_hash}",
        frontier=[source_url],  # Seed with source URL
    )

    # Save the new state
    storage.save_state(state)

    return ToolResult(
        success=True,
        output={
            "loaded_existing": False,
            "created_new": True,
            "source_url": state.source_url,
            "source_hash": state.source_hash,
            "scope": state.scope,
            "status": state.status,
            "frontier_count": len(state.frontier),
            "max_pages": state.max_pages,
            "max_depth": state.max_depth,
            "content_root": state.content_root,
            "registry_path": state.registry_path,
        },
    )


def _save_crawl_state_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for save_crawl_state tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    kb_root = arguments.get("kb_root")
    storage = _get_storage(kb_root)

    # Load existing state
    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    # Update status if provided
    if "status" in arguments:
        state.status = arguments["status"]
        if arguments["status"] == "completed":
            state.completed_at = datetime.now(timezone.utc)

    # Update frontier if provided
    if "frontier" in arguments:
        state.frontier = arguments["frontier"]

    # Update visited hashes if provided
    if "visited_hashes" in arguments:
        state.visited_hashes = set(arguments["visited_hashes"])

    # Update statistics if provided
    stats = arguments.get("statistics", {})
    if "visited_count" in stats:
        state.visited_count = stats["visited_count"]
    if "discovered_count" in stats:
        state.discovered_count = stats["discovered_count"]
    if "in_scope_count" in stats:
        state.in_scope_count = stats["in_scope_count"]
    if "out_of_scope_count" in stats:
        state.out_of_scope_count = stats["out_of_scope_count"]
    if "failed_count" in stats:
        state.failed_count = stats["failed_count"]
    if "skipped_count" in stats:
        state.skipped_count = stats["skipped_count"]

    # Update last activity
    state.last_activity = datetime.now(timezone.utc)

    # Mark as crawling if was pending
    if state.status == "pending":
        state.status = "crawling"
        state.started_at = datetime.now(timezone.utc)

    # Save state
    storage.save_state(state)

    return ToolResult(
        success=True,
        output={
            "saved": True,
            "source_url": state.source_url,
            "status": state.status,
            "frontier_count": len(state.frontier),
            "visited_count": state.visited_count,
            "last_activity": state.last_activity.isoformat() if state.last_activity else None,
        },
    )


def _get_crawl_statistics_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for get_crawl_statistics tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    kb_root = arguments.get("kb_root")
    storage = _get_storage(kb_root)

    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    return ToolResult(
        success=True,
        output={
            "source_url": state.source_url,
            "scope": state.scope,
            "status": state.status,
            "frontier_count": len(state.frontier),
            "frontier_overflow_count": state.frontier_overflow_count,
            "visited_count": state.visited_count,
            "discovered_count": state.discovered_count,
            "in_scope_count": state.in_scope_count,
            "out_of_scope_count": state.out_of_scope_count,
            "skipped_count": state.skipped_count,
            "failed_count": state.failed_count,
            "max_pages": state.max_pages,
            "max_depth": state.max_depth,
            "started_at": state.started_at.isoformat() if state.started_at else None,
            "last_activity": state.last_activity.isoformat() if state.last_activity else None,
            "completed_at": state.completed_at.isoformat() if state.completed_at else None,
            "progress_percentage": round(
                (state.visited_count / state.max_pages) * 100, 1
            ) if state.max_pages > 0 else 0,
        },
    )


# =============================================================================
# Frontier Management Tools
# =============================================================================


def _register_frontier_tools(registry: ToolRegistry) -> None:
    """Register URL frontier management tools."""

    registry.register_tool(
        ToolDefinition(
            name="get_frontier_urls",
            description=(
                "Get the next batch of URLs from the crawl frontier. "
                "URLs are returned in FIFO order (breadth-first crawl)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "count": {
                        "type": "integer",
                        "description": "Number of URLs to retrieve. Default: 10.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url"],
                "additionalProperties": False,
            },
            handler=_get_frontier_urls_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="add_to_frontier",
            description=(
                "Add discovered URLs to the crawl frontier after scope validation. "
                "Only URLs within the source scope are added."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs to add to the frontier.",
                    },
                    "discovered_from": {
                        "type": "string",
                        "description": "URL where these links were discovered.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url", "urls"],
                "additionalProperties": False,
            },
            handler=_add_to_frontier_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="filter_urls_by_scope",
            description=(
                "Filter a list of URLs by the crawl scope constraint. "
                "Returns URLs that are within scope and not yet visited."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL defining the scope boundary.",
                    },
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "URLs to filter.",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["path", "host", "domain"],
                        "description": "Scope constraint. Default: 'path'.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url", "urls"],
                "additionalProperties": False,
            },
            handler=_filter_urls_by_scope_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _get_frontier_urls_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for get_frontier_urls tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    count = arguments.get("count", 10)
    kb_root = arguments.get("kb_root")
    storage = _get_storage(kb_root)

    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    # Get URLs from frontier
    urls = state.frontier[:count]

    return ToolResult(
        success=True,
        output={
            "urls": urls,
            "count": len(urls),
            "remaining_in_frontier": len(state.frontier) - len(urls),
            "frontier_overflow_count": state.frontier_overflow_count,
        },
    )


def _add_to_frontier_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for add_to_frontier tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    urls = arguments.get("urls", [])
    if not urls:
        return ToolResult(
            success=True,
            output={"added": 0, "filtered_out": 0, "already_visited": 0},
        )

    kb_root = arguments.get("kb_root")
    storage = _get_storage(kb_root)

    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    added = 0
    filtered_out = 0
    already_visited = 0
    already_in_frontier = 0

    # Track URLs already in frontier
    frontier_set = set(state.frontier)

    for url in urls:
        # Normalize URL
        normalized = normalize_url(url, strip_fragment=True)
        url_h = _url_hash(normalized)

        # Check if already visited
        if url_h in state.visited_hashes:
            already_visited += 1
            continue

        # Check if already in frontier
        if normalized in frontier_set:
            already_in_frontier += 1
            continue

        # Check scope
        if not is_url_in_scope(normalized, source_url, state.scope):
            filtered_out += 1
            state.out_of_scope_count += 1
            continue

        # Add to frontier
        state.frontier.append(normalized)
        frontier_set.add(normalized)
        added += 1
        state.in_scope_count += 1
        state.discovered_count += 1

    # Save updated state
    storage.save_state(state)

    return ToolResult(
        success=True,
        output={
            "added": added,
            "filtered_out_of_scope": filtered_out,
            "already_visited": already_visited,
            "already_in_frontier": already_in_frontier,
            "frontier_count": len(state.frontier),
        },
    )


def _filter_urls_by_scope_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for filter_urls_by_scope tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    urls = arguments.get("urls", [])
    scope = arguments.get("scope", "path")
    kb_root = arguments.get("kb_root")

    # Load state for visited checking
    storage = _get_storage(kb_root)
    state = storage.load_state(source_url)
    visited_hashes = state.visited_hashes if state else set()

    in_scope = []
    out_of_scope = []
    already_visited = []

    for url in urls:
        normalized = normalize_url(url, strip_fragment=True)
        url_h = _url_hash(normalized)

        if url_h in visited_hashes:
            already_visited.append(normalized)
        elif is_url_in_scope(normalized, source_url, scope):
            in_scope.append(normalized)
        else:
            out_of_scope.append(normalized)

    return ToolResult(
        success=True,
        output={
            "in_scope": in_scope,
            "in_scope_count": len(in_scope),
            "out_of_scope": out_of_scope,
            "out_of_scope_count": len(out_of_scope),
            "already_visited": already_visited,
            "already_visited_count": len(already_visited),
        },
    )


# =============================================================================
# Fetch and Link Extraction Tools
# =============================================================================


def _register_fetch_tools(registry: ToolRegistry) -> None:
    """Register page fetching and link extraction tools."""

    registry.register_tool(
        ToolDefinition(
            name="check_robots_txt",
            description=(
                "Check if a URL is allowed by the site's robots.txt. "
                "Should be called before fetching each page."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to check.",
                    },
                    "robots_content": {
                        "type": "string",
                        "description": "Pre-fetched robots.txt content (optional).",
                    },
                    "user_agent": {
                        "type": "string",
                        "description": "User agent to check against. Default: '*'.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_check_robots_txt_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="extract_links",
            description=(
                "Extract links from HTML content and filter by scope. "
                "Returns absolute, normalized URLs within the crawl scope."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "html": {
                        "type": "string",
                        "description": "HTML content to extract links from.",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Base URL for resolving relative links.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "Source URL for scope filtering (optional).",
                    },
                    "scope": {
                        "type": "string",
                        "enum": ["path", "host", "domain"],
                        "description": "Scope for filtering. Default: 'path'.",
                    },
                },
                "required": ["html", "base_url"],
                "additionalProperties": False,
            },
            handler=_extract_links_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="fetch_page",
            description=(
                "Fetch a web page content. Uses WebParser for extraction. "
                "Includes politeness delay. Returns HTML and metadata. "
                "Enable rendering for JavaScript-heavy sites. "
                "Enable stealth to bypass headless browser detection."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL to fetch.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "Source URL (for tracking relationship).",
                    },
                    "delay_seconds": {
                        "type": "number",
                        "description": "Politeness delay before fetch. Default: 1.0.",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Request timeout. Default: 30.",
                    },
                    "enable_rendering": {
                        "type": "boolean",
                        "description": "Enable JavaScript rendering via Playwright. Default: false.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_fetch_page_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _check_robots_txt_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for check_robots_txt tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="url is required.")

    robots_content = arguments.get("robots_content")
    user_agent = arguments.get("user_agent", "*")

    # Parse robots.txt if provided
    if robots_content:
        robots = parse_robots_txt(robots_content)
        ruleset = robots.get_ruleset(user_agent)

        if ruleset is None:
            return ToolResult(
                success=True,
                output={
                    "allowed": True,
                    "reason": "No rules for user agent",
                    "crawl_delay": None,
                },
            )

        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"

        allowed = ruleset.is_allowed(path)

        return ToolResult(
            success=True,
            output={
                "allowed": allowed,
                "reason": "Checked against robots.txt rules",
                "crawl_delay": ruleset.crawl_delay,
            },
        )

    # No robots.txt content provided - assume allowed
    return ToolResult(
        success=True,
        output={
            "allowed": True,
            "reason": "No robots.txt provided - assuming allowed",
            "crawl_delay": None,
        },
    )


def _extract_links_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for extract_links tool."""
    html = arguments.get("html")
    if not html:
        return ToolResult(success=False, output="html is required.")

    base_url = arguments.get("base_url")
    if not base_url:
        return ToolResult(success=False, output="base_url is required.")

    source_url = arguments.get("source_url")
    scope = arguments.get("scope", "path")

    # Extract all links
    links = extract_links(html, base_url)
    all_urls = [link.url for link in links]

    # Filter by scope if source_url provided
    if source_url:
        in_scope = filter_urls_by_scope(all_urls, source_url, scope)
        out_of_scope = [u for u in all_urls if u not in in_scope]

        return ToolResult(
            success=True,
            output={
                "all_links": [
                    {"url": link.url, "anchor_text": link.anchor_text, "is_nofollow": link.is_nofollow}
                    for link in links
                ],
                "total_count": len(links),
                "in_scope_urls": in_scope,
                "in_scope_count": len(in_scope),
                "out_of_scope_urls": out_of_scope,
                "out_of_scope_count": len(out_of_scope),
            },
        )

    return ToolResult(
        success=True,
        output={
            "all_links": [
                {"url": link.url, "anchor_text": link.anchor_text, "is_nofollow": link.is_nofollow}
                for link in links
            ],
            "total_count": len(links),
        },
    )


def _fetch_page_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for fetch_page tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="url is required.")

    delay_seconds = arguments.get("delay_seconds", 1.0)
    timeout_seconds = arguments.get("timeout_seconds", 30)
    enable_rendering = arguments.get("enable_rendering", False)

    # Apply politeness delay
    if delay_seconds > 0:
        time.sleep(delay_seconds)

    try:
        # Use WebParser for fetching and extraction
        from src.parsing.web import WebParser
        from src.parsing.base import ParseTarget

        parser = WebParser(
            enable_rendering=enable_rendering,
        )
        target = ParseTarget(source=url, is_remote=True)
        document = parser.extract(target)
        markdown = parser.to_markdown(document)

        return ToolResult(
            success=True,
            output={
                "url": url,
                "success": True,
                "content": markdown,
                "content_length": len(markdown),
                "content_hash": _content_hash(markdown),
                "title": document.metadata.get("title", ""),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "rendered": document.metadata.get("rendered", False),
            },
        )

    except Exception as e:
        return ToolResult(
            success=True,  # Tool succeeded, but fetch failed
            output={
                "url": url,
                "success": False,
                "error": str(e),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
        )


# =============================================================================
# Storage Tools
# =============================================================================


def _register_storage_tools(registry: ToolRegistry) -> None:
    """Register content storage and page registry tools."""

    registry.register_tool(
        ToolDefinition(
            name="store_page_content",
            description=(
                "Store fetched page content in the sharded directory structure. "
                "Returns the storage path for the content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "page_url": {
                        "type": "string",
                        "description": "URL of the page being stored.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Markdown content to store.",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Additional metadata to store with content.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url", "page_url", "content"],
                "additionalProperties": False,
            },
            handler=_store_page_content_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="update_page_registry",
            description=(
                "Update the page registry with a fetched or failed page entry. "
                "Tracks URL status, content metadata, and links discovered."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "page_url": {
                        "type": "string",
                        "description": "URL of the page to update.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "fetched", "failed", "skipped"],
                        "description": "Status of the page.",
                    },
                    "discovered_from": {
                        "type": "string",
                        "description": "URL that linked to this page.",
                    },
                    "link_depth": {
                        "type": "integer",
                        "description": "Depth from source URL.",
                    },
                    "http_status": {
                        "type": "integer",
                        "description": "HTTP response status code.",
                    },
                    "content_hash": {
                        "type": "string",
                        "description": "SHA-256 hash of content.",
                    },
                    "content_path": {
                        "type": "string",
                        "description": "Path to stored content.",
                    },
                    "content_size": {
                        "type": "integer",
                        "description": "Content size in bytes.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Page title.",
                    },
                    "outgoing_links_count": {
                        "type": "integer",
                        "description": "Number of links on the page.",
                    },
                    "outgoing_links_in_scope": {
                        "type": "integer",
                        "description": "Number of in-scope links.",
                    },
                    "error_message": {
                        "type": "string",
                        "description": "Error message if failed.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url", "page_url", "status"],
                "additionalProperties": False,
            },
            handler=_update_page_registry_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="mark_url_visited",
            description=(
                "Mark a URL as visited and remove it from the frontier. "
                "Used after successfully processing a page."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {
                        "type": "string",
                        "description": "The source URL of the crawl.",
                    },
                    "page_url": {
                        "type": "string",
                        "description": "URL to mark as visited.",
                    },
                    "success": {
                        "type": "boolean",
                        "description": "Whether the fetch was successful.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["source_url", "page_url"],
                "additionalProperties": False,
            },
            handler=_mark_url_visited_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _store_page_content_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for store_page_content tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    page_url = arguments.get("page_url")
    if not page_url:
        return ToolResult(success=False, output="page_url is required.")

    content = arguments.get("content")
    if content is None:
        return ToolResult(success=False, output="content is required.")

    metadata = arguments.get("metadata", {})
    kb_root = arguments.get("kb_root")

    # Get storage paths
    storage = _get_storage(kb_root)
    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    # Calculate content hash and shard
    c_hash = _content_hash(content)
    shard = c_hash[0]  # First hex character

    # Build storage path
    root = Path(kb_root) if kb_root else paths.get_evidence_root()
    domain = _get_domain(source_url)
    parsed = urlparse(source_url)
    path_hash = _source_hash(parsed.path or "/")

    content_dir = root / "parsed" / domain / path_hash / shard / c_hash[:16]
    content_file = content_dir / "content.md"
    metadata_file = content_dir / "metadata.json"

    # Prepare metadata
    full_metadata = {
        "source_url": source_url,
        "page_url": page_url,
        "content_hash": c_hash,
        "stored_at": datetime.now(timezone.utc).isoformat(),
        **metadata,
    }

    # Store using GitHub client if available
    github_client = resolve_github_client()
    if github_client:
        try:
            # Store content
            github_client.write_file(
                str(content_file),
                content,
                f"chore(crawl): store content for {page_url[:50]}",
            )
            # Store metadata
            github_client.write_file(
                str(metadata_file),
                json.dumps(full_metadata, indent=2),
                f"chore(crawl): store metadata for {page_url[:50]}",
            )
        except Exception as e:
            return ToolResult(success=False, output=f"Failed to store content: {e}")
    else:
        # Local storage
        content_dir.mkdir(parents=True, exist_ok=True)
        content_file.write_text(content, encoding="utf-8")
        metadata_file.write_text(json.dumps(full_metadata, indent=2), encoding="utf-8")

    # Return relative path for storage
    relative_path = f"parsed/{domain}/{path_hash}/{shard}/{c_hash[:16]}"

    return ToolResult(
        success=True,
        output={
            "stored": True,
            "content_path": relative_path,
            "content_hash": c_hash,
            "content_size": len(content),
        },
    )


def _update_page_registry_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for update_page_registry tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    page_url = arguments.get("page_url")
    if not page_url:
        return ToolResult(success=False, output="page_url is required.")

    status = arguments.get("status")
    if not status:
        return ToolResult(success=False, output="status is required.")

    kb_root = arguments.get("kb_root")
    storage = _get_storage(kb_root)

    # Load crawl state
    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    source_hash = state.source_hash
    
    # Get page registry
    page_registry = _get_page_registry(kb_root)

    # Check if page already exists
    existing = page_registry.get_page(page_url, source_hash)

    if existing:
        # Update existing entry
        existing.status = status
        if status == "fetched":
            existing.fetched_at = datetime.now(timezone.utc)
        if "http_status" in arguments:
            existing.http_status = arguments["http_status"]
        if "content_hash" in arguments:
            existing.content_hash = arguments["content_hash"]
        if "content_path" in arguments:
            existing.content_path = arguments["content_path"]
        if "content_size" in arguments:
            existing.content_size = arguments["content_size"]
        if "title" in arguments:
            existing.title = arguments["title"]
        if "outgoing_links_count" in arguments:
            existing.outgoing_links_count = arguments["outgoing_links_count"]
        if "outgoing_links_in_scope" in arguments:
            existing.outgoing_links_in_scope = arguments["outgoing_links_in_scope"]
        if "error_message" in arguments:
            existing.error_message = arguments["error_message"]

        page_registry.save_page(existing, source_hash)
        action = "updated"
    else:
        # Create new entry
        entry = PageEntry(
            url=page_url,
            url_hash=_url_hash(page_url),
            source_url=source_url,
            discovered_from=arguments.get("discovered_from"),
            link_depth=arguments.get("link_depth", 0),
            status=status,
            http_status=arguments.get("http_status"),
            content_hash=arguments.get("content_hash"),
            content_path=arguments.get("content_path"),
            content_size=arguments.get("content_size"),
            title=arguments.get("title"),
            outgoing_links_count=arguments.get("outgoing_links_count"),
            outgoing_links_in_scope=arguments.get("outgoing_links_in_scope"),
            error_message=arguments.get("error_message"),
        )
        if status == "fetched":
            entry.fetched_at = datetime.now(timezone.utc)

        page_registry.save_page(entry, source_hash)
        action = "added"

    # Get page count from stats
    stats = page_registry.get_stats(source_hash)

    return ToolResult(
        success=True,
        output={
            "action": action,
            "page_url": page_url,
            "status": status,
            "registry_count": stats["total"],
        },
    )


def _mark_url_visited_handler(arguments: Mapping[str, Any]) -> ToolResult:
    """Handler for mark_url_visited tool."""
    source_url = arguments.get("source_url")
    if not source_url:
        return ToolResult(success=False, output="source_url is required.")

    page_url = arguments.get("page_url")
    if not page_url:
        return ToolResult(success=False, output="page_url is required.")

    success = arguments.get("success", True)
    kb_root = arguments.get("kb_root")

    storage = _get_storage(kb_root)
    state = storage.load_state(source_url)
    if state is None:
        return ToolResult(success=False, output=f"No crawl state found for: {source_url}")

    # Normalize and hash URL
    normalized = normalize_url(page_url, strip_fragment=True)
    url_h = _url_hash(normalized)

    # Add to visited
    state.visited_hashes.add(url_h)

    # Remove from frontier
    if normalized in state.frontier:
        state.frontier.remove(normalized)

    # Update counts
    if success:
        state.visited_count += 1
    else:
        state.failed_count += 1

    # Update last activity
    state.last_activity = datetime.now(timezone.utc)

    # Check if complete
    if len(state.frontier) == 0 and state.frontier_overflow_count == 0:
        state.status = "completed"
        state.completed_at = datetime.now(timezone.utc)

    # Save state
    storage.save_state(state)

    return ToolResult(
        success=True,
        output={
            "marked_visited": True,
            "page_url": page_url,
            "visited_count": state.visited_count,
            "frontier_remaining": len(state.frontier),
            "status": state.status,
        },
    )
