"""Source curator tool registrations for the orchestration runtime."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

import requests

from src import paths
from src.integrations.github import discussions as github_discussions
from src.integrations.github import issues as github_issues
from src.knowledge.source_discovery import SourceDiscoverer
from src.knowledge.storage import SourceEntry, SourceRegistry

from ..safety import ActionRisk
from ..tools import ToolDefinition, ToolRegistry
from ..types import ToolResult


def register_source_curator_tools(registry: ToolRegistry) -> None:
    """Register all source curator tools with the registry."""
    _register_read_tools(registry)
    _register_write_tools(registry)


def _register_read_tools(registry: ToolRegistry) -> None:
    """Register safe source curator read-only tools."""

    registry.register_tool(
        ToolDefinition(
            name="get_source",
            description="Retrieve a source entry from the registry by URL.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source to retrieve.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root. Defaults to knowledge-graph/.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_get_source_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="list_sources",
            description="List all registered sources, optionally filtered by status or type.",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "deprecated", "pending_review"],
                        "description": "Filter by source status.",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["primary", "derived", "reference"],
                        "description": "Filter by source type.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_list_sources_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="verify_source_accessibility",
            description="Check if a source URL is accessible via HTTP HEAD request.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to verify.",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Request timeout in seconds. Defaults to 10.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_verify_source_accessibility_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="calculate_credibility_score",
            description="Calculate credibility score for a URL based on domain characteristics.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to score.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_calculate_credibility_score_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="discover_sources",
            description="Scan parsed documents for potential new sources and rank by credibility.",
            parameters={
                "type": "object",
                "properties": {
                    "checksum": {
                        "type": "string",
                        "description": "Limit discovery to a specific document checksum.",
                    },
                    "domain_filter": {
                        "type": "string",
                        "description": "Regex pattern to filter domains (e.g., '\\.gov$|\\.edu$').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of candidates to return. Defaults to 20.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                    "parsed_root": {
                        "type": "string",
                        "description": "Path to parsed documents root.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_discover_sources_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _register_write_tools(registry: ToolRegistry) -> None:
    """Register source curator tools that modify state."""

    registry.register_tool(
        ToolDefinition(
            name="register_source",
            description="Register a new source in the registry. Requires proposal_discussion and implementation_issue for derived sources.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The canonical URL of the source.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Human-readable name for the source.",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["primary", "derived", "reference"],
                        "description": "Type of source.",
                    },
                    "proposal_discussion": {
                        "type": "integer",
                        "description": "Discussion number where this source was proposed.",
                    },
                    "implementation_issue": {
                        "type": "integer",
                        "description": "Issue number for implementation. Required for derived sources.",
                    },
                    "added_by": {
                        "type": "string",
                        "description": "GitHub username who added this. Defaults to 'system'.",
                    },
                    "credibility_score": {
                        "type": "number",
                        "description": "Credibility score (0.0-1.0). Will be calculated if not provided.",
                    },
                    "content_type": {
                        "type": "string",
                        "enum": ["webpage", "pdf", "api", "feed"],
                        "description": "Type of content. Defaults to 'webpage'.",
                    },
                    "discovered_from": {
                        "type": "string",
                        "description": "Checksum of document where this source was discovered.",
                    },
                    "parent_source_url": {
                        "type": "string",
                        "description": "URL of the source that referenced this.",
                    },
                    "topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Related topics/keywords.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Free-form notes about the source.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url", "name", "source_type"],
                "additionalProperties": False,
            },
            handler=_register_source_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="update_source_status",
            description="Update the status of an existing source.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source to update.",
                    },
                    "status": {
                        "type": "string",
                        "enum": ["active", "deprecated", "pending_review"],
                        "description": "New status for the source.",
                    },
                    "notes": {
                        "type": "string",
                        "description": "Optional notes about the status change.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url", "status"],
                "additionalProperties": False,
            },
            handler=_update_source_status_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="propose_source_discussion",
            description="Create a GitHub Discussion proposing a new source. The agent will post a credibility assessment as a reply.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source to propose.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Human-readable name for the source.",
                    },
                    "discovered_from": {
                        "type": "string",
                        "description": "Checksum of document where this source was discovered.",
                    },
                    "parent_source_url": {
                        "type": "string",
                        "description": "URL of the source that referenced this.",
                    },
                    "context_snippet": {
                        "type": "string",
                        "description": "Text snippet showing where URL was found.",
                    },
                    "category_name": {
                        "type": "string",
                        "description": "Discussion category name. Defaults to 'Sources'.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format. Defaults to GITHUB_REPOSITORY.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token. Defaults to GITHUB_TOKEN.",
                    },
                },
                "required": ["url", "name"],
                "additionalProperties": False,
            },
            handler=_propose_source_discussion_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="assess_source_proposal",
            description="Post a credibility assessment as a reply to a source proposal Discussion.",
            parameters={
                "type": "object",
                "properties": {
                    "discussion_number": {
                        "type": "integer",
                        "description": "Discussion number containing the source proposal.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "URL of the source being assessed.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token.",
                    },
                },
                "required": ["discussion_number", "source_url"],
                "additionalProperties": False,
            },
            handler=_assess_source_proposal_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="create_source_implementation_issue",
            description="Create a GitHub Issue for implementing an approved source. Adds 'source-approved' label and assigns to copilot.",
            parameters={
                "type": "object",
                "properties": {
                    "discussion_number": {
                        "type": "integer",
                        "description": "Discussion number where source was approved.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "URL of the approved source.",
                    },
                    "source_name": {
                        "type": "string",
                        "description": "Name of the source.",
                    },
                    "credibility_score": {
                        "type": "number",
                        "description": "Credibility score from assessment.",
                    },
                    "approved_by": {
                        "type": "string",
                        "description": "GitHub username who approved the source.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token.",
                    },
                },
                "required": ["discussion_number", "source_url", "source_name"],
                "additionalProperties": False,
            },
            handler=_create_source_implementation_issue_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="process_source_approval",
            description="Process /approve-source command from a Discussion comment. Creates implementation Issue.",
            parameters={
                "type": "object",
                "properties": {
                    "discussion_number": {
                        "type": "integer",
                        "description": "Discussion number containing the source proposal.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "URL of the source being approved.",
                    },
                    "source_name": {
                        "type": "string",
                        "description": "Name of the source.",
                    },
                    "comment_author": {
                        "type": "string",
                        "description": "GitHub username who issued the approval command.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["discussion_number", "source_url", "source_name"],
                "additionalProperties": False,
            },
            handler=_process_discussion_approval_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="process_source_rejection",
            description="Process /reject-source command from a Discussion comment. Marks Discussion as rejected.",
            parameters={
                "type": "object",
                "properties": {
                    "discussion_number": {
                        "type": "integer",
                        "description": "Discussion number containing the source proposal.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "URL of the source being rejected.",
                    },
                    "rejection_reason": {
                        "type": "string",
                        "description": "Reason for rejection.",
                    },
                    "comment_author": {
                        "type": "string",
                        "description": "GitHub username who issued the rejection command.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token.",
                    },
                },
                "required": ["discussion_number", "source_url"],
                "additionalProperties": False,
            },
            handler=_process_source_rejection_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="implement_approved_source",
            description="Register an approved source from an implementation Issue and close it.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "description": "Issue number for implementation.",
                    },
                    "discussion_number": {
                        "type": "integer",
                        "description": "Discussion number where source was proposed.",
                    },
                    "source_url": {
                        "type": "string",
                        "description": "URL of the source to register.",
                    },
                    "source_name": {
                        "type": "string",
                        "description": "Name of the source.",
                    },
                    "approved_by": {
                        "type": "string",
                        "description": "GitHub username who approved the source.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["issue_number", "discussion_number", "source_url", "source_name"],
                "additionalProperties": False,
            },
            handler=_implement_approved_source_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    # Keep sync_source_discussion for backward compatibility but mark as deprecated
    registry.register_tool(
        ToolDefinition(
            name="sync_source_discussion",
            description="[DEPRECATED] Sync a source entry to a GitHub Discussion. Use propose_source_discussion instead.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL of the source to sync.",
                    },
                    "category_name": {
                        "type": "string",
                        "description": "Discussion category name. Defaults to 'Sources'.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository in 'owner/name' format.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_sync_source_discussion_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )


# =============================================================================
# Tool Handlers
# =============================================================================


def _get_source_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for get_source tool."""
    url = args["url"]
    kb_root = args.get("kb_root")

    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=root_path)
    source = registry.get_source(url)

    if source is None:
        return ToolResult(
            success=False,
            output=None,
            error=f"Source not found: {url}",
        )

    return ToolResult(
        success=True,
        output=source.to_dict(),
    )


def _list_sources_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for list_sources tool."""
    status = args.get("status")
    source_type = args.get("source_type")
    kb_root = args.get("kb_root")

    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=root_path)
    sources = registry.list_sources(status=status, source_type=source_type)

    return ToolResult(
        success=True,
        output={
            "count": len(sources),
            "sources": [s.to_dict() for s in sources],
        },
    )


def _verify_source_accessibility_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for verify_source_accessibility tool."""
    url = args["url"]
    timeout = args.get("timeout", 10)

    try:
        # Validate URL format
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return ToolResult(
                success=False,
                output={"accessible": False, "reason": "Invalid URL format"},
            )

        # Try HEAD request first (faster)
        response = requests.head(url, timeout=timeout, allow_redirects=True)

        # Some servers don't support HEAD, fall back to GET
        if response.status_code == 405:
            response = requests.get(url, timeout=timeout, allow_redirects=True, stream=True)

        accessible = response.status_code < 400
        ssl_valid = url.startswith("https://")

        return ToolResult(
            success=True,
            output={
                "accessible": accessible,
                "status_code": response.status_code,
                "ssl_valid": ssl_valid,
                "content_type": response.headers.get("Content-Type", "unknown"),
                "final_url": response.url,
            },
        )

    except requests.exceptions.SSLError:
        return ToolResult(
            success=True,
            output={
                "accessible": False,
                "reason": "SSL certificate error",
                "ssl_valid": False,
            },
        )
    except requests.exceptions.Timeout:
        return ToolResult(
            success=True,
            output={
                "accessible": False,
                "reason": f"Request timed out after {timeout}s",
            },
        )
    except requests.exceptions.RequestException as e:
        return ToolResult(
            success=True,
            output={
                "accessible": False,
                "reason": str(e),
            },
        )


def _calculate_credibility_score_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for calculate_credibility_score tool."""
    from src.knowledge.source_discovery import DiscoveredUrl

    url = args["url"]

    # Create a minimal DiscoveredUrl to use the scoring logic
    discovered = DiscoveredUrl(
        url=url,
        source_checksum="",
        context="",
        link_text="",
    )

    discoverer = SourceDiscoverer()
    score = discoverer.score_candidate(discovered)

    return ToolResult(
        success=True,
        output={
            "url": url,
            "credibility_score": round(score, 3),
            "domain": discovered.domain,
            "domain_type": discovered.domain_type,
            "is_https": url.startswith("https://"),
        },
    )


def _discover_sources_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for discover_sources tool."""
    checksum = args.get("checksum")
    domain_filter = args.get("domain_filter")
    limit = args.get("limit", 20)
    kb_root = args.get("kb_root")
    parsed_root = args.get("parsed_root")

    # Get registered sources
    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)
    registered_urls = registry.get_all_urls()

    # Initialize discoverer
    parsed_path = Path(parsed_root) if parsed_root else (paths.get_evidence_root() / "parsed")
    discoverer = SourceDiscoverer(parsed_root=parsed_path)

    # Discover
    if checksum:
        results = discoverer.discover_from_document(
            checksum=checksum,
            registered_sources=registered_urls,
            domain_filter=domain_filter,
        )
    else:
        results = discoverer.discover_all(
            registered_sources=registered_urls,
            domain_filter=domain_filter,
            limit=limit,
        )

    candidates = []
    for discovered_url, score in results:
        candidates.append({
            "url": discovered_url.url,
            "domain": discovered_url.domain,
            "domain_type": discovered_url.domain_type,
            "credibility_score": round(score, 3),
            "source_checksum": discovered_url.source_checksum,
            "link_text": discovered_url.link_text,
            "context": discovered_url.context[:200] if discovered_url.context else "",
        })

    return ToolResult(
        success=True,
        output={
            "registered_count": len(registered_urls),
            "candidate_count": len(candidates),
            "candidates": candidates,
        },
    )


def _register_source_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for register_source tool."""
    url = args["url"]
    name = args["name"]
    source_type = args["source_type"]
    proposal_discussion = args.get("proposal_discussion")
    implementation_issue = args.get("implementation_issue")
    added_by = args.get("added_by", "system")
    credibility_score = args.get("credibility_score")
    content_type = args.get("content_type", "webpage")
    discovered_from = args.get("discovered_from")
    parent_source_url = args.get("parent_source_url")
    topics = args.get("topics", [])
    notes = args.get("notes", "")
    kb_root = args.get("kb_root")

    # Validate: derived sources require implementation_issue
    if source_type == "derived" and implementation_issue is None:
        return ToolResult(
            success=False,
            output=None,
            error="Derived sources require an implementation_issue number.",
        )

    # Check if already registered
    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)

    if registry.source_exists(url):
        return ToolResult(
            success=False,
            output=None,
            error=f"Source already registered: {url}",
        )

    # Calculate credibility if not provided
    if credibility_score is None:
        from src.knowledge.source_discovery import DiscoveredUrl

        discovered = DiscoveredUrl(url=url, source_checksum="", context="", link_text="")
        discoverer = SourceDiscoverer()
        credibility_score = discoverer.score_candidate(discovered)

    # Determine if official domain
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    is_official = any(
        domain.endswith(suffix)
        for suffix in [".gov", ".edu", ".mil", ".int"]
    )

    now = datetime.now(timezone.utc)

    source = SourceEntry(
        url=url,
        name=name,
        source_type=source_type,
        status="active" if source_type == "primary" else "pending_review",
        last_verified=now,
        added_at=now,
        added_by=added_by,
        proposal_discussion=proposal_discussion,
        implementation_issue=implementation_issue,
        credibility_score=credibility_score,
        is_official=is_official,
        requires_auth=False,
        discovered_from=discovered_from,
        parent_source_url=parent_source_url,
        content_type=content_type,
        update_frequency=None,
        topics=topics,
        notes=notes,
    )

    registry.save_source(source)

    return ToolResult(
        success=True,
        output={
            "registered": True,
            "url": url,
            "url_hash": source.url_hash,
            "source": source.to_dict(),
        },
    )


def _update_source_status_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for update_source_status tool."""
    url = args["url"]
    new_status = args["status"]
    notes = args.get("notes")
    kb_root = args.get("kb_root")

    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)

    source = registry.get_source(url)
    if source is None:
        return ToolResult(
            success=False,
            output=None,
            error=f"Source not found: {url}",
        )

    # Create updated source
    updated = SourceEntry(
        url=source.url,
        name=source.name,
        source_type=source.source_type,
        status=new_status,
        last_verified=datetime.now(timezone.utc),
        added_at=source.added_at,
        added_by=source.added_by,
        proposal_discussion=source.proposal_discussion,
        implementation_issue=source.implementation_issue,
        credibility_score=source.credibility_score,
        is_official=source.is_official,
        requires_auth=source.requires_auth,
        discovered_from=source.discovered_from,
        parent_source_url=source.parent_source_url,
        content_type=source.content_type,
        update_frequency=source.update_frequency,
        topics=source.topics,
        notes=notes if notes else source.notes,
    )

    registry.save_source(updated)

    return ToolResult(
        success=True,
        output={
            "updated": True,
            "url": url,
            "old_status": source.status,
            "new_status": new_status,
        },
    )


# =============================================================================
# GitHub Integration Handlers
# =============================================================================


def _resolve_github_credentials(
    args: Mapping[str, Any],
) -> tuple[str, str] | ToolResult:
    """Resolve repository and token from args or environment."""
    repository_arg = args.get("repository")
    token_arg = args.get("token")
    
    try:
        repository = github_issues.resolve_repository(
            str(repository_arg) if repository_arg else None
        )
        token = github_issues.resolve_token(
            str(token_arg) if token_arg else None
        )
        return repository, token
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _propose_source_discussion_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for propose_source_discussion tool - creates Discussion instead of Issue."""
    url = args["url"]
    name = args["name"]
    discovered_from = args.get("discovered_from")
    parent_source_url = args.get("parent_source_url")
    context_snippet = args.get("context_snippet", "")
    category_name = args.get("category_name", "Sources")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    # Find the Sources category
    try:
        categories = github_discussions.list_discussion_categories(
            token=token,
            repository=repository,
        )
    except github_discussions.GitHubDiscussionError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    category = None
    for cat in categories:
        if cat.name.lower() == category_name.lower():
            category = cat
            break

    if category is None:
        return ToolResult(
            success=False,
            output=None,
            error=f"Discussion category '{category_name}' not found. Available: {[c.name for c in categories]}",
        )

    # Build discussion body
    body = f"""## Source Proposal: {name}

**URL**: {url}
**Discovered in**: {discovered_from or 'N/A'}
**Parent Source**: {parent_source_url or 'N/A'}
**Discovery Method**: {'Automated scan via `discover-sources`' if discovered_from else 'Manual proposal'}

### Why This Source?
"""
    if context_snippet:
        body += f"{context_snippet}\n\n"
    else:
        body += "_No justification provided._\n\n"

    body += """---

### Agent Assessment
_Pending - agent will post credibility analysis as a reply_

---

### Commands
- **To approve**: Comment `/approve-source` after reaching consensus
- **To reject**: Comment `/reject-source [reason]`

_Proposed by Source Curator Agent_
"""

    # Create the discussion
    try:
        discussion_title = f"Source Proposal: {name}"
        discussion = github_discussions.create_discussion(
            token=token,
            repository=repository,
            category_id=category.id,
            title=discussion_title,
            body=body,
        )
        return ToolResult(
            success=True,
            output={
                "discussion_number": discussion.number,
                "discussion_id": discussion.id,
                "discussion_url": discussion.url,
                "url": url,
                "name": name,
            },
        )
    except github_discussions.GitHubDiscussionError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _assess_source_proposal_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for assess_source_proposal tool - posts credibility assessment as Discussion reply."""
    discussion_number = args["discussion_number"]
    source_url = args["source_url"]

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    # Calculate credibility score
    discoverer = SourceDiscoverer()
    from src.knowledge.source_discovery import DiscoveredUrl
    discovered = DiscoveredUrl(
        url=source_url,
        source_checksum="",
        context="",
        link_text="",
    )
    score = discoverer.score_candidate(discovered)

    # Determine domain info
    parsed = urlparse(source_url)
    domain = parsed.netloc.lower()
    
    # Determine domain type
    if domain.endswith(".gov") or domain.endswith(".gov.uk"):
        domain_type = "government"
    elif domain.endswith(".edu"):
        domain_type = "education"
    elif domain.endswith(".org"):
        domain_type = "organization"
    elif domain.endswith(".mil"):
        domain_type = "military"
    else:
        domain_type = "commercial/unknown"

    # Check accessibility
    accessible = False
    status_code = "N/A"
    ssl_valid = "N/A"
    content_type = "N/A"
    
    try:
        response = requests.head(source_url, timeout=10, allow_redirects=True)
        accessible = response.status_code < 400
        status_code = str(response.status_code)
        ssl_valid = "✅" if source_url.startswith("https://") else "⚠️ HTTP only"
        content_type = response.headers.get("Content-Type", "unknown")[:50]
    except requests.RequestException:
        accessible = False
        status_code = "Error"

    # Build assessment comment
    is_official = domain.endswith((".gov", ".edu", ".mil", ".int", ".gov.uk"))
    recommendation = "Recommended for approval" if score >= 0.6 else "Review carefully - lower credibility score"

    assessment_body = f"""## Credibility Assessment

**Credibility Score**: {score:.2f}/1.0
**Domain**: {domain}
**Domain Type**: {domain_type}

### Integrity Indicators
| Check | Status | Details |
|-------|--------|---------|
| Official domain | {'✅' if is_official else '❌'} | {domain_type} |
| Accessible | {'✅' if accessible else '❌'} | HTTP {status_code} |
| Valid SSL | {ssl_valid} | {'HTTPS' if source_url.startswith('https://') else 'HTTP'} |
| Content parseable | {'✅' if accessible else '❌'} | {content_type} |

### Recommendation
{recommendation}

---
_Assessment by Source Curator Agent_
"""

    # Post the assessment as a comment
    try:
        # Get the discussion first to get its ID
        discussion = github_discussions.get_discussion(
            token=token,
            repository=repository,
            discussion_number=discussion_number,
        )
        
        comment = github_discussions.add_discussion_comment(
            token=token,
            discussion_id=discussion.id,
            body=assessment_body,
        )
        
        return ToolResult(
            success=True,
            output={
                "discussion_number": discussion_number,
                "comment_id": comment.id,
                "credibility_score": score,
                "domain_type": domain_type,
                "accessible": accessible,
                "recommendation": recommendation,
            },
        )
    except github_discussions.GitHubDiscussionError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _create_source_implementation_issue_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for create_source_implementation_issue tool."""
    discussion_number = args["discussion_number"]
    source_url = args["source_url"]
    source_name = args["source_name"]
    credibility_score = args.get("credibility_score", 0.0)
    approved_by = args.get("approved_by", "unknown")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    # Build issue body
    body = f"""## Implement Approved Source

This issue tracks the implementation of an approved source from Discussion #{discussion_number}.

### Source Details
| Field | Value |
|-------|-------|
| **URL** | {source_url} |
| **Name** | {source_name} |
| **Credibility Score** | {credibility_score:.2f} |
| **Approved By** | @{approved_by} |
| **Proposal Discussion** | #{discussion_number} |

### Implementation Tasks
- [ ] Register source in `knowledge-graph/sources/`
- [ ] Update Discussion with approval status
- [ ] Close this Issue with implementation summary

---
_Created by Source Curator Agent_
"""

    # Create the issue with label and assignment
    try:
        issue_title = f"Implement Source: {source_name}"
        outcome = github_issues.create_issue(
            token=token,
            repository=repository,
            title=issue_title,
            body=body,
            labels=["source-approved"],
            assignees=["copilot"],
        )
        return ToolResult(
            success=True,
            output={
                "issue_number": outcome.number,
                "issue_url": outcome.html_url,
                "discussion_number": discussion_number,
                "source_url": source_url,
            },
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _process_discussion_approval_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for process_source_approval tool - Discussion-first workflow."""
    discussion_number = args["discussion_number"]
    source_url = args["source_url"]
    source_name = args["source_name"]
    comment_author = args.get("comment_author", "unknown")
    kb_root = args.get("kb_root")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)

    # Check if already registered
    if registry.source_exists(source_url):
        return ToolResult(
            success=False,
            output=None,
            error=f"Source already registered: {source_url}",
        )

    # Calculate credibility
    discoverer = SourceDiscoverer()
    from src.knowledge.source_discovery import DiscoveredUrl
    discovered = DiscoveredUrl(
        url=source_url,
        source_checksum="",
        context="",
        link_text=source_name,
    )
    score = discoverer.score_candidate(discovered)

    # Create implementation issue
    issue_result = _create_source_implementation_issue_handler({
        "discussion_number": discussion_number,
        "source_url": source_url,
        "source_name": source_name,
        "credibility_score": score,
        "approved_by": comment_author,
        "repository": repository,
        "token": token,
    })

    if not issue_result.success:
        return issue_result

    issue_number = issue_result.output["issue_number"]
    issue_url = issue_result.output["issue_url"]

    # Post approval comment to Discussion
    try:
        discussion = github_discussions.get_discussion(
            token=token,
            repository=repository,
            discussion_number=discussion_number,
        )
        
        approval_comment = f"""✅ **Source Approved**

This source has been approved for implementation.

| Field | Value |
|-------|-------|
| **Approved By** | @{comment_author} |
| **Implementation Issue** | #{issue_number} |
| **Credibility Score** | {score:.2f} |

The source will be registered when the implementation Issue is processed.

_Processed by Source Curator Agent_
"""
        github_discussions.add_discussion_comment(
            token=token,
            discussion_id=discussion.id,
            body=approval_comment,
        )
    except github_discussions.GitHubDiscussionError as exc:
        # Non-fatal - Issue was created successfully
        pass

    return ToolResult(
        success=True,
        output={
            "action": "approved",
            "discussion_number": discussion_number,
            "issue_number": issue_number,
            "issue_url": issue_url,
            "source_url": source_url,
            "credibility_score": score,
        },
    )


def _process_source_rejection_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for process_source_rejection tool."""
    discussion_number = args["discussion_number"]
    source_url = args["source_url"]
    rejection_reason = args.get("rejection_reason", "No reason provided")
    comment_author = args.get("comment_author", "unknown")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    # Post rejection comment to Discussion
    try:
        discussion = github_discussions.get_discussion(
            token=token,
            repository=repository,
            discussion_number=discussion_number,
        )
        
        rejection_comment = f"""❌ **Source Rejected**

This source proposal has been rejected.

| Field | Value |
|-------|-------|
| **Rejected By** | @{comment_author} |
| **Reason** | {rejection_reason} |
| **URL** | {source_url} |

No implementation Issue will be created.

_Processed by Source Curator Agent_
"""
        github_discussions.add_discussion_comment(
            token=token,
            discussion_id=discussion.id,
            body=rejection_comment,
        )
        
        return ToolResult(
            success=True,
            output={
                "action": "rejected",
                "discussion_number": discussion_number,
                "source_url": source_url,
                "reason": rejection_reason,
            },
        )
    except github_discussions.GitHubDiscussionError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _implement_approved_source_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for implement_approved_source tool - registers source and closes Issue."""
    issue_number = args["issue_number"]
    discussion_number = args["discussion_number"]
    source_url = args["source_url"]
    source_name = args["source_name"]
    approved_by = args.get("approved_by", "unknown")
    kb_root = args.get("kb_root")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)

    # Check if already registered
    if registry.source_exists(source_url):
        return ToolResult(
            success=False,
            output=None,
            error=f"Source already registered: {source_url}",
        )

    # Calculate credibility
    discoverer = SourceDiscoverer()
    from src.knowledge.source_discovery import DiscoveredUrl
    discovered = DiscoveredUrl(
        url=source_url,
        source_checksum="",
        context="",
        link_text=source_name,
    )
    score = discoverer.score_candidate(discovered)

    # Determine if official domain
    parsed = urlparse(source_url)
    domain = parsed.netloc.lower()
    is_official = any(
        domain.endswith(suffix)
        for suffix in [".gov", ".edu", ".mil", ".int"]
    )

    now = datetime.now(timezone.utc)
    source = SourceEntry(
        url=source_url,
        name=source_name,
        source_type="derived",
        status="active",
        last_verified=now,
        added_at=now,
        added_by=approved_by,
        proposal_discussion=discussion_number,
        implementation_issue=issue_number,
        credibility_score=score,
        is_official=is_official,
        requires_auth=False,
        discovered_from=None,
        parent_source_url=None,
        content_type="webpage",
        update_frequency=None,
        topics=[],
        notes=f"Approved via Discussion #{discussion_number}, Issue #{issue_number}",
    )
    registry.save_source(source)

    # Post completion comment and close Issue
    try:
        completion_comment = f"""✅ **Source Implemented**

The source has been successfully registered in the source registry.

| Field | Value |
|-------|-------|
| **URL** | {source_url} |
| **Name** | {source_name} |
| **Credibility Score** | {score:.2f} |
| **Registered By** | @{approved_by} |
| **Proposal Discussion** | #{discussion_number} |

_Implemented by Source Curator Agent_
"""
        github_issues.post_comment(
            token=token,
            repository=repository,
            issue_number=issue_number,
            body=completion_comment,
        )
        github_issues.update_issue(
            token=token,
            repository=repository,
            issue_number=issue_number,
            state="closed",
        )
    except github_issues.GitHubIssueError as exc:
        # Non-fatal - source was registered successfully
        pass

    # Update Discussion with completion status
    try:
        discussion = github_discussions.get_discussion(
            token=token,
            repository=repository,
            discussion_number=discussion_number,
        )
        
        status_comment = f"""🎉 **Implementation Complete**

The source has been registered in the knowledge graph.

| Field | Value |
|-------|-------|
| **Status** | Active |
| **Implementation Issue** | #{issue_number} (closed) |

_Updated by Source Curator Agent_
"""
        github_discussions.add_discussion_comment(
            token=token,
            discussion_id=discussion.id,
            body=status_comment,
        )
    except github_discussions.GitHubDiscussionError:
        pass  # Non-fatal

    return ToolResult(
        success=True,
        output={
            "action": "implemented",
            "registered": True,
            "issue_number": issue_number,
            "issue_closed": True,
            "discussion_number": discussion_number,
            "source_url": source_url,
            "source": source.to_dict(),
        },
    )


# Keep legacy handler for backward compatibility
def _propose_source_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for propose_source tool."""
    url = args["url"]
    name = args["name"]
    discovered_from = args.get("discovered_from")
    parent_source_url = args.get("parent_source_url")
    context_snippet = args.get("context_snippet", "")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    # Calculate credibility score
    discoverer = SourceDiscoverer()
    from src.knowledge.source_discovery import DiscoveredUrl
    discovered = DiscoveredUrl(
        url=url,
        source_checksum=discovered_from or "",
        context=context_snippet,
        link_text=name,
    )
    score = discoverer.score_candidate(discovered)

    # Determine domain info
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Determine domain type
    if domain.endswith(".gov") or domain.endswith(".gov.uk"):
        domain_type = "government"
    elif domain.endswith(".edu"):
        domain_type = "education"
    elif domain.endswith(".org"):
        domain_type = "organization"
    elif domain.endswith(".mil"):
        domain_type = "military"
    else:
        domain_type = "commercial/unknown"

    # Build issue body
    body = f"""## Discovered Source Proposal

**URL**: {url}
**Name**: {name}
**Discovered in**: {discovered_from or 'N/A'}
**Parent Source**: {parent_source_url or 'N/A'}
**Discovery Method**: Automated scan via `discover-sources`

### Preliminary Assessment
**Credibility Score**: {score:.2f}/1.0
**Domain**: {domain}
**Domain Type**: {domain_type}

### Context
"""
    if context_snippet:
        body += f"Found in document section:\n> {context_snippet}\n\n"
    else:
        body += "_No context snippet provided._\n\n"

    body += """### Integrity Indicators
- [ ] Official domain (requires manual verification)
- [ ] Accessible (requires verification)
- [ ] Valid SSL certificate
- [ ] Content parseable

---
**To approve**: Comment with `/approve-source`
**To reject**: Comment with `/reject-source [reason]`

_Proposed by Source Curator Agent_
"""

    # Create the issue
    try:
        issue_title = f"Source Proposal: {name}"
        outcome = github_issues.create_issue(
            token=token,
            repository=repository,
            title=issue_title,
            body=body,
            labels=["source-proposal"],
        )
        return ToolResult(
            success=True,
            output={
                "issue_number": outcome.number,
                "issue_url": outcome.html_url,
                "url": url,
                "credibility_score": score,
                "domain_type": domain_type,
            },
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _process_source_approval_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for process_source_approval tool."""
    issue_number = args["issue_number"]
    command = args["command"]
    source_url = args["source_url"]
    source_name = args["source_name"]
    comment_author = args.get("comment_author", "unknown")
    rejection_reason = args.get("rejection_reason", "")
    kb_root = args.get("kb_root")

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)

    if command == "approve":
        # Check if already registered
        if registry.source_exists(source_url):
            return ToolResult(
                success=False,
                output=None,
                error=f"Source already registered: {source_url}",
            )

        # Calculate credibility
        discoverer = SourceDiscoverer()
        from src.knowledge.source_discovery import DiscoveredUrl
        discovered = DiscoveredUrl(
            url=source_url,
            source_checksum="",
            context="",
            link_text=source_name,
        )
        score = discoverer.score_candidate(discovered)

        # Determine if official
        parsed = urlparse(source_url)
        domain = parsed.netloc.lower()
        is_official = any(
            domain.endswith(suffix)
            for suffix in [".gov", ".edu", ".mil", ".int"]
        )

        now = datetime.now(timezone.utc)
        source = SourceEntry(
            url=source_url,
            name=source_name,
            source_type="derived",
            status="active",
            last_verified=now,
            added_at=now,
            added_by=comment_author,
            proposal_discussion=None,  # Will be set when Discussion-first flow is implemented
            implementation_issue=issue_number,
            credibility_score=score,
            is_official=is_official,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency=None,
            topics=[],
            notes=f"Approved via issue #{issue_number}",
        )
        registry.save_source(source)

        # Post approval comment and close issue
        try:
            approval_comment = f"""✅ **Source Approved**

The source has been registered in the source registry.

| Field | Value |
|-------|-------|
| URL | {source_url} |
| Name | {source_name} |
| Credibility Score | {score:.2f} |
| Approved By | @{comment_author} |
| Issue | #{issue_number} |

_Registered by Source Curator Agent_
"""
            github_issues.post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body=approval_comment,
            )
            github_issues.update_issue(
                token=token,
                repository=repository,
                issue_number=issue_number,
                state="closed",
            )
        except github_issues.GitHubIssueError as exc:
            # Source was registered but GitHub update failed
            return ToolResult(
                success=True,
                output={
                    "registered": True,
                    "url": source_url,
                    "github_error": str(exc),
                },
            )

        return ToolResult(
            success=True,
            output={
                "action": "approved",
                "registered": True,
                "url": source_url,
                "issue_closed": True,
            },
        )

    elif command == "reject":
        # Post rejection comment and close issue
        try:
            rejection_comment = f"""❌ **Source Rejected**

This source proposal has been rejected.

**Reason**: {rejection_reason or 'No reason provided'}
**Rejected By**: @{comment_author}

_Processed by Source Curator Agent_
"""
            github_issues.post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body=rejection_comment,
            )
            github_issues.update_issue(
                token=token,
                repository=repository,
                issue_number=issue_number,
                state="closed",
            )
        except github_issues.GitHubIssueError as exc:
            return ToolResult(success=False, output=None, error=str(exc))

        return ToolResult(
            success=True,
            output={
                "action": "rejected",
                "url": source_url,
                "reason": rejection_reason,
                "issue_closed": True,
            },
        )

    else:
        return ToolResult(
            success=False,
            output=None,
            error=f"Unknown command: {command}",
        )


def _sync_source_discussion_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for sync_source_discussion tool."""
    source_url = args["url"]
    category_name = args.get("category_name", "Sources")
    kb_root = args.get("kb_root")

    # Get source from registry first (before GitHub API calls)
    registry_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=registry_path)
    source = registry.get_source(source_url)

    if source is None:
        return ToolResult(
            success=False,
            output=None,
            error=f"Source not found in registry: {source_url}",
        )

    # Resolve credentials
    creds = _resolve_github_credentials(args)
    if isinstance(creds, ToolResult):
        return creds
    repository, token = creds

    # Find the category ID
    try:
        categories = github_discussions.list_discussion_categories(
            token=token,
            repository=repository,
        )
    except github_discussions.GitHubDiscussionError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    category = None
    for cat in categories:
        if cat.name.lower() == category_name.lower():
            category = cat
            break

    if category is None:
        return ToolResult(
            success=False,
            output=None,
            error=f"Discussion category '{category_name}' not found. Available: {[c.name for c in categories]}",
        )

    # Build discussion body
    discussion_title = f"Source: {source.name}"
    discussion_body = f"""## Source Information

| Field | Value |
|-------|-------|
| **URL** | {source.url} |
| **Type** | {source.source_type} |
| **Status** | {source.status} |
| **Credibility Score** | {source.credibility_score:.2f} |
| **Official Domain** | {'Yes' if source.is_official else 'No'} |
| **Content Type** | {source.content_type} |
| **Added By** | {source.added_by} |
| **Added At** | {source.added_at.isoformat()} |
| **Last Verified** | {source.last_verified.isoformat()} |

### Notes
{source.notes or '_No notes._'}

### Topics
{', '.join(source.topics) if source.topics else '_No topics assigned._'}

---
_Synced from source registry by Source Curator Agent_
"""

    # Check if discussion already exists
    try:
        existing = github_discussions.find_discussion_by_title(
            token=token,
            repository=repository,
            category_id=category.id,
            title=discussion_title,
        )
    except github_discussions.GitHubDiscussionError:
        existing = None

    try:
        if existing:
            # Update existing discussion
            github_discussions.update_discussion(
                token=token,
                discussion_id=existing.id,
                body=discussion_body,
            )
            return ToolResult(
                success=True,
                output={
                    "action": "updated",
                    "discussion_id": existing.id,
                    "discussion_url": existing.url,
                    "source_url": source_url,
                },
            )
        else:
            # Create new discussion
            discussion = github_discussions.create_discussion(
                token=token,
                repository=repository,
                category_id=category.id,
                title=discussion_title,
                body=discussion_body,
            )
            return ToolResult(
                success=True,
                output={
                    "action": "created",
                    "discussion_id": discussion.id,
                    "discussion_url": discussion.url,
                    "source_url": source_url,
                },
            )
    except github_discussions.GitHubDiscussionError as exc:
        return ToolResult(success=False, output=None, error=str(exc))
