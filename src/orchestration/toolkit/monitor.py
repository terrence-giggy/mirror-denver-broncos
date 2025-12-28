"""Monitor agent tool registrations for the orchestration runtime.

This toolkit provides tools for detecting content changes in registered sources
and creating acquisition candidate Issues when changes are detected.

Two modes of operation:
1. Initial Acquisition: For sources with no previous content hash
2. Update Monitoring: For sources with existing content, uses tiered detection
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src import paths
from src.integrations.github import issues as github_issues
from src.integrations.github.search_issues import GitHubIssueSearcher
from src.knowledge.monitoring import (
    ChangeDetection,
    SourceMonitor,
    calculate_next_check,
)
from src.knowledge.storage import SourceEntry, SourceRegistry

from ..safety import ActionRisk
from ..tools import ToolDefinition, ToolRegistry
from ..types import ToolResult
from ._github_context import resolve_github_client


def register_monitor_tools(registry: ToolRegistry) -> None:
    """Register all monitor agent tools with the registry."""
    _register_read_tools(registry)
    _register_write_tools(registry)


# =============================================================================
# Helper Functions
# =============================================================================


def _url_hash(url: str) -> str:
    """Generate a short hash of a URL for deduplication markers."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _build_initial_acquisition_body(source: SourceEntry, detection: ChangeDetection) -> str:
    """Build the Issue body for an initial acquisition request."""
    # Extract domain for storage path hint
    from urllib.parse import urlparse
    domain = urlparse(source.url).netloc.replace("www.", "")
    
    return f"""## Initial Acquisition: {source.name}

**Source URL**: {source.url}
**Approved**: {source.added_at.isoformat()}
**Approved By**: {source.added_by}
**Approval Discussion**: {f'#{source.proposal_discussion}' if source.proposal_discussion else 'N/A'}

### Source Profile

- **Type**: {source.source_type} ({source.content_type})
- **Credibility Score**: {source.credibility_score:.2f}
- **Official Domain**: {'Yes' if source.is_official else 'No'}
- **Requires Auth**: {'Yes' if source.requires_auth else 'No'}

---

## ⚡ Task Type: CONTENT ACQUISITION

**This is an EXECUTION task, not an IMPLEMENTATION task.**

DO NOT create new modules, toolkits, or infrastructure. Use the existing parsing system.

---

### Execution Steps

1. **Fetch and parse** using the existing web parser:
   ```python
   from src.parsing.runner import parse_single_target
   from src.parsing.storage import ParseStorage
   from src import paths
   
   storage = ParseStorage(root=paths.get_evidence_root() / "parsed")
   result = parse_single_target("{source.url}", storage=storage, is_remote=True)
   ```

2. **Update source registry** with content hash:
   ```python
   from src.knowledge.storage import SourceRegistry
   from src import paths
   
   registry = SourceRegistry(root=paths.get_knowledge_graph_root())
   source = registry.get_source("{source.url}")
   # Update source.last_content_hash = result.checksum
   # Save with registry.save_source(updated_source)
   ```

3. **Commit changes** via GitHub API (Actions environment requires GitHubStorageClient)

### Available Infrastructure (DO NOT RECREATE)

| Module | Purpose |
|--------|---------|
| `src/parsing/web.py` | WebParser for HTML/URL content |
| `src/parsing/runner.py` | `parse_single_target()` orchestrator |
| `src/parsing/storage.py` | ParseStorage, manifest management |
| `src/knowledge/storage.py` | SourceRegistry for metadata |

### Storage Location

Parsed content will be stored at:
```
evidence/parsed/{{year}}/{domain}-{{hash[:8]}}/
├── content.md          # Extracted content
└── metadata.json       # Provenance info
```

### ⚠️ Network Requirements

This task requires **external network access** to fetch content from the source URL.
- **GitHub Actions**: Network access available
- **Sandboxed environments**: May fail at fetch stage

If network is blocked, close this issue with label `blocked-network` and a comment explaining the limitation.

---

### Acceptance Criteria

- [ ] Content fetched from source URL
- [ ] Parsed content stored in `evidence/parsed/`
- [ ] Manifest entry created with checksum
- [ ] `SourceEntry.last_content_hash` updated in registry
- [ ] Issue closed with acquisition summary

**Urgency**: {detection.urgency}

<!-- monitor-initial:{_url_hash(source.url)} -->
"""


def _build_content_update_body(source: SourceEntry, detection: ChangeDetection) -> str:
    """Build the Issue body for a content update request."""
    prev_hash_display = detection.previous_hash[:16] if detection.previous_hash else "N/A"
    curr_hash_display = detection.current_hash[:16] if detection.current_hash else "N/A"
    prev_etag = source.last_etag or "N/A"
    curr_etag = detection.current_etag or "N/A"
    prev_modified = source.last_modified_header or "N/A"
    curr_modified = detection.current_last_modified or "N/A"
    prev_checked = detection.previous_checked.isoformat() if detection.previous_checked else "N/A"

    # Extract domain for storage path hint
    from urllib.parse import urlparse
    domain = urlparse(source.url).netloc.replace("www.", "")

    return f"""## Content Update: {source.name}

**Source URL**: {source.url}
**Change Detected**: {detection.detected_at.isoformat()}
**Detection Method**: {detection.detection_method}
**Previous Check**: {prev_checked}

### Change Summary

| Metric | Previous | Current |
|--------|----------|---------|
| Content Hash | `{prev_hash_display}` | `{curr_hash_display}` |
| ETag | {prev_etag} | {curr_etag} |
| Last-Modified | {prev_modified} | {curr_modified} |

---

## ⚡ Task Type: CONTENT UPDATE

**This is an EXECUTION task, not an IMPLEMENTATION task.**

DO NOT create new modules, toolkits, or infrastructure. Use the existing parsing system.

---

### Execution Steps

1. **Fetch and parse** the updated content:
   ```python
   from src.parsing.runner import parse_single_target
   from src.parsing.storage import ParseStorage
   from src import paths
   
   storage = ParseStorage(root=paths.get_evidence_root() / "parsed")
   result = parse_single_target("{source.url}", storage=storage, is_remote=True, force=True)
   ```

2. **Update source registry** with new content hash:
   ```python
   from src.knowledge.storage import SourceRegistry
   from src import paths
   
   registry = SourceRegistry(root=paths.get_knowledge_graph_root())
   source = registry.get_source("{source.url}")
   # Update source.last_content_hash = result.checksum
   # Save with registry.save_source(updated_source)
   ```

3. **Commit changes** via GitHub API (Actions environment requires GitHubStorageClient)

### Available Infrastructure (DO NOT RECREATE)

| Module | Purpose |
|--------|---------|
| `src/parsing/web.py` | WebParser for HTML/URL content |
| `src/parsing/runner.py` | `parse_single_target()` orchestrator |
| `src/parsing/storage.py` | ParseStorage, manifest management |
| `src/knowledge/storage.py` | SourceRegistry for metadata |

### Storage Location

New version will be stored at:
```
evidence/parsed/{{year}}/{domain}-{{new_hash[:8]}}/
├── content.md          # Updated content
└── metadata.json       # Provenance (links to previous version)
```

Previous version remains at its original location for diff comparison.

### ⚠️ Network Requirements

This task requires **external network access** to fetch content from the source URL.
- **GitHub Actions**: Network access available
- **Sandboxed environments**: May fail at fetch stage

If network is blocked, close this issue with label `blocked-network` and a comment explaining the limitation.

---

### Acceptance Criteria

- [ ] Updated content fetched from source URL
- [ ] New version stored in `evidence/parsed/`
- [ ] Manifest entry created with new checksum
- [ ] `SourceEntry.last_content_hash` updated in registry
- [ ] Issue closed with update summary (note what changed if detectable)

**Urgency**: {detection.urgency}

<!-- monitor-update:{_url_hash(source.url)}:{detection.current_hash or 'pending'} -->
"""


# =============================================================================
# Read-Only Tools
# =============================================================================


def _register_read_tools(registry: ToolRegistry) -> None:
    """Register safe monitor read-only tools."""

    registry.register_tool(
        ToolDefinition(
            name="get_sources_pending_initial",
            description="List sources that need initial acquisition (never acquired before).",
            parameters={
                "type": "object",
                "properties": {
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root. Defaults to knowledge-graph/.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_get_sources_pending_initial_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_sources_due_for_check",
            description="List sources that are due for update monitoring check.",
            parameters={
                "type": "object",
                "properties": {
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root. Defaults to knowledge-graph/.",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_get_sources_due_for_check_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="check_source_for_changes",
            description="Check a single source for content changes using tiered detection (ETag -> Last-Modified -> Content Hash).",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source to check.",
                    },
                    "force_full": {
                        "type": "boolean",
                        "description": "Skip tiered detection and do full content hash comparison.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root. Defaults to knowledge-graph/.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_check_source_for_changes_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _get_sources_pending_initial_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for get_sources_pending_initial tool."""
    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    reg = SourceRegistry(root=root_path)
    monitor = SourceMonitor(registry=reg)
    sources = monitor.get_sources_pending_initial()

    # Provide helpful context if no active sources but pending ones exist
    pending_review_count = len(reg.list_sources(status="pending_review"))
    message = None
    if len(sources) == 0 and pending_review_count > 0:
        message = (
            f"No active sources need initial acquisition. "
            f"However, {pending_review_count} source(s) are in 'pending_review' status. "
            f"Sources must be approved (status='active') before they can be monitored. "
            f"Use the implement_approved_source tool or update_source_status to activate them."
        )

    return ToolResult(
        success=True,
        output={
            "count": len(sources),
            "sources": [
                {
                    "url": s.url,
                    "name": s.name,
                    "source_type": s.source_type,
                    "added_at": s.added_at.isoformat(),
                    "credibility_score": s.credibility_score,
                }
                for s in sources
            ],
            "message": message,
        },
    )


def _get_sources_due_for_check_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for get_sources_due_for_check tool."""
    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    reg = SourceRegistry(root=root_path)
    monitor = SourceMonitor(registry=reg)
    sources = monitor.get_sources_due_for_check()

    return ToolResult(
        success=True,
        output={
            "count": len(sources),
            "sources": [
                {
                    "url": s.url,
                    "name": s.name,
                    "source_type": s.source_type,
                    "last_checked": s.last_checked.isoformat() if s.last_checked else None,
                    "next_check_after": s.next_check_after.isoformat() if s.next_check_after else None,
                    "check_failures": s.check_failures,
                }
                for s in sources
            ],
        },
    )


def _check_source_for_changes_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for check_source_for_changes tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="URL is required.")

    force_full = arguments.get("force_full", False)
    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    reg = SourceRegistry(root=root_path)
    source = reg.get_source(url)
    if source is None:
        return ToolResult(success=False, output=f"Source not found: {url}")

    monitor = SourceMonitor(registry=reg)
    result = monitor.check_source(source, force_full=force_full)

    output: dict[str, Any] = {
        "source_url": result.source_url,
        "status": result.status,
        "checked_at": result.checked_at.isoformat(),
    }

    if result.status == "initial":
        output["message"] = "Source needs initial acquisition (no previous content hash)."
    elif result.status == "changed":
        output["detection_method"] = result.detection_method
        output["current_etag"] = result.etag
        output["current_last_modified"] = result.last_modified
        output["current_hash"] = result.content_hash
    elif result.status == "unchanged":
        output["message"] = "No changes detected."
    elif result.status == "error":
        output["error"] = result.error_message

    return ToolResult(success=True, output=output)


# =============================================================================
# Write Tools
# =============================================================================


def _register_write_tools(registry: ToolRegistry) -> None:
    """Register monitor tools that modify state."""

    registry.register_tool(
        ToolDefinition(
            name="update_source_monitoring_metadata",
            description="Update the monitoring metadata for a source after a check.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source to update.",
                    },
                    "check_succeeded": {
                        "type": "boolean",
                        "description": "Whether the check was successful.",
                    },
                    "content_hash": {
                        "type": "string",
                        "description": "New content hash (if acquired).",
                    },
                    "etag": {
                        "type": "string",
                        "description": "New ETag value.",
                    },
                    "last_modified": {
                        "type": "string",
                        "description": "New Last-Modified header value.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url", "check_succeeded"],
                "additionalProperties": False,
            },
            handler=_update_source_monitoring_metadata_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="create_initial_acquisition_issue",
            description="Create a GitHub Issue for initial source acquisition.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source needing acquisition.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "GitHub repository (owner/repo). Defaults to GITHUB_REPOSITORY.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_create_initial_acquisition_issue_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="create_content_update_issue",
            description="Create a GitHub Issue for a detected content update.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the source with detected changes.",
                    },
                    "detection_method": {
                        "type": "string",
                        "enum": ["etag", "last_modified", "content_hash"],
                        "description": "How the change was detected.",
                    },
                    "current_etag": {
                        "type": "string",
                        "description": "Current ETag value.",
                    },
                    "current_last_modified": {
                        "type": "string",
                        "description": "Current Last-Modified value.",
                    },
                    "current_hash": {
                        "type": "string",
                        "description": "Current content hash.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "GitHub repository (owner/repo). Defaults to GITHUB_REPOSITORY.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url", "detection_method"],
                "additionalProperties": False,
            },
            handler=_create_content_update_issue_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="report_source_access_problem",
            description="Create a GitHub Discussion to report persistent access problems with a source.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the problematic source.",
                    },
                    "error_message": {
                        "type": "string",
                        "description": "Description of the access problem.",
                    },
                    "consecutive_failures": {
                        "type": "integer",
                        "description": "Number of consecutive check failures.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "GitHub repository (owner/repo). Defaults to GITHUB_REPOSITORY.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root.",
                    },
                },
                "required": ["url", "error_message"],
                "additionalProperties": False,
            },
            handler=_report_source_access_problem_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )


def _update_source_monitoring_metadata_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for update_source_monitoring_metadata tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="URL is required.")

    check_succeeded = arguments.get("check_succeeded", True)
    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    github_client = resolve_github_client()
    reg = SourceRegistry(root=root_path, github_client=github_client)
    source = reg.get_source(url)
    if source is None:
        return ToolResult(success=False, output=f"Source not found: {url}")

    # Update monitoring fields
    now = datetime.now(timezone.utc)
    
    # Create updated source entry using dataclass replace pattern
    updated_fields: dict[str, Any] = {
        "last_checked": now,
    }

    if check_succeeded:
        updated_fields["check_failures"] = 0
        if arguments.get("content_hash"):
            updated_fields["last_content_hash"] = arguments["content_hash"]
        if arguments.get("etag"):
            updated_fields["last_etag"] = arguments["etag"]
        if arguments.get("last_modified"):
            updated_fields["last_modified_header"] = arguments["last_modified"]
    else:
        updated_fields["check_failures"] = source.check_failures + 1

    # Calculate next check time
    next_check = calculate_next_check(source, not check_succeeded)
    updated_fields["next_check_after"] = next_check

    # Create new source entry with updated fields
    source_dict = source.to_dict()
    source_dict.update({
        "last_checked": updated_fields["last_checked"].isoformat(),
        "check_failures": updated_fields["check_failures"],
        "next_check_after": updated_fields["next_check_after"].isoformat(),
    })
    if "last_content_hash" in updated_fields:
        source_dict["last_content_hash"] = updated_fields["last_content_hash"]
    if "last_etag" in updated_fields:
        source_dict["last_etag"] = updated_fields["last_etag"]
    if "last_modified_header" in updated_fields:
        source_dict["last_modified_header"] = updated_fields["last_modified_header"]

    updated_source = SourceEntry.from_dict(source_dict)
    reg.save_source(updated_source)

    return ToolResult(
        success=True,
        output={
            "url": url,
            "last_checked": now.isoformat(),
            "check_failures": updated_fields["check_failures"],
            "next_check_after": next_check.isoformat(),
            "status": "degraded" if updated_fields["check_failures"] >= 5 else "active",
        },
    )


def _create_initial_acquisition_issue_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for create_initial_acquisition_issue tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="URL is required.")

    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    reg = SourceRegistry(root=root_path)
    source = reg.get_source(url)
    if source is None:
        return ToolResult(success=False, output=f"Source not found: {url}")

    if source.last_content_hash is not None:
        return ToolResult(
            success=False,
            output="Source already has content hash. Use content update instead.",
        )

    # Resolve repository and token early for dedup check
    try:
        repository = github_issues.resolve_repository(arguments.get("repository"))
        token = github_issues.resolve_token(None)
    except github_issues.GitHubIssueError as e:
        return ToolResult(success=False, output=str(e))

    # Check for existing issue before creating a new one
    dedup_marker = f"monitor-initial:{_url_hash(source.url)}"
    try:
        searcher = GitHubIssueSearcher(token=token, repository=repository)
        existing_issues = searcher.search_by_body_content(dedup_marker, limit=1)
        if existing_issues:
            existing = existing_issues[0]
            return ToolResult(
                success=True,
                output={
                    "issue_number": existing.number,
                    "issue_url": existing.url,
                    "source_url": source.url,
                    "skipped": True,
                    "reason": "Issue already exists for this source",
                },
            )
    except github_issues.GitHubIssueError:
        # If search fails, proceed with creation (fail-open for dedup)
        pass

    # Create ChangeDetection for initial acquisition
    now = datetime.now(timezone.utc)
    detection = ChangeDetection(
        source_url=source.url,
        source_name=source.name,
        detected_at=now,
        detection_method="initial",
        change_type="initial",
        previous_hash=None,
        previous_checked=None,
        current_etag=None,
        current_last_modified=None,
        current_hash=None,
        urgency="high" if source.source_type == "primary" else "normal",
    )

    # Build issue content
    title = f"[Initial Acquisition] {source.name}"
    body = _build_initial_acquisition_body(source, detection)

    # Determine labels based on urgency
    labels = ["acquisition-candidate", "initial-acquisition", source.source_type]
    if detection.urgency == "high":
        labels.append("high-priority")
    elif detection.urgency == "low":
        labels.append("low-priority")

    # Create the issue
    try:
        outcome = github_issues.create_issue(
            token=token,
            repository=repository,
            title=title,
            body=body,
            labels=labels,
        )
    except github_issues.GitHubIssueError as e:
        return ToolResult(success=False, output=f"Failed to create issue: {e}")

    # Assign the issue to Copilot
    try:
        github_issues.assign_issue_to_copilot(
            token=token,
            repository=repository,
            issue_number=outcome.number,
        )
    except github_issues.GitHubIssueError as e:
        # Issue was created but assignment failed - log but don't fail
        return ToolResult(
            success=True,
            output={
                "issue_number": outcome.number,
                "issue_url": outcome.html_url,
                "source_url": source.url,
                "urgency": detection.urgency,
                "warning": f"Issue created but Copilot assignment failed: {e}",
            },
        )

    return ToolResult(
        success=True,
        output={
            "issue_number": outcome.number,
            "issue_url": outcome.html_url,
            "source_url": source.url,
            "urgency": detection.urgency,
        },
    )


def _create_content_update_issue_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for create_content_update_issue tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="URL is required.")

    detection_method = arguments.get("detection_method")
    if not detection_method:
        return ToolResult(success=False, output="detection_method is required.")

    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    reg = SourceRegistry(root=root_path)
    source = reg.get_source(url)
    if source is None:
        return ToolResult(success=False, output=f"Source not found: {url}")

    if source.last_content_hash is None:
        return ToolResult(
            success=False,
            output="Source has no previous content hash. Use initial acquisition instead.",
        )

    # Create ChangeDetection for content update
    now = datetime.now(timezone.utc)
    urgency = "high" if source.source_type == "primary" else "normal"

    detection = ChangeDetection(
        source_url=source.url,
        source_name=source.name,
        detected_at=now,
        detection_method=detection_method,
        change_type="content",
        previous_hash=source.last_content_hash,
        previous_checked=source.last_checked,
        current_etag=arguments.get("current_etag"),
        current_last_modified=arguments.get("current_last_modified"),
        current_hash=arguments.get("current_hash"),
        urgency=urgency,
    )

    # Build issue content
    title = f"[Content Update] {source.name}"
    body = _build_content_update_body(source, detection)

    # Determine labels based on urgency
    labels = ["acquisition-candidate", "content-update", source.source_type]
    if detection.urgency == "high":
        labels.append("high-priority")
    elif detection.urgency == "low":
        labels.append("low-priority")

    # Resolve repository and token
    try:
        repository = github_issues.resolve_repository(arguments.get("repository"))
        token = github_issues.resolve_token(None)
    except github_issues.GitHubIssueError as e:
        return ToolResult(success=False, output=str(e))

    # Create the issue
    try:
        outcome = github_issues.create_issue(
            token=token,
            repository=repository,
            title=title,
            body=body,
            labels=labels,
        )
    except github_issues.GitHubIssueError as e:
        return ToolResult(success=False, output=f"Failed to create issue: {e}")

    # Assign the issue to Copilot
    try:
        github_issues.assign_issue_to_copilot(
            token=token,
            repository=repository,
            issue_number=outcome.number,
        )
    except github_issues.GitHubIssueError as e:
        # Issue was created but assignment failed - log but don't fail
        return ToolResult(
            success=True,
            output={
                "issue_number": outcome.number,
                "issue_url": outcome.html_url,
                "source_url": source.url,
                "detection_method": detection_method,
                "urgency": detection.urgency,
                "warning": f"Issue created but Copilot assignment failed: {e}",
            },
        )

    return ToolResult(
        success=True,
        output={
            "issue_number": outcome.number,
            "issue_url": outcome.html_url,
            "source_url": source.url,
            "detection_method": detection_method,
            "urgency": detection.urgency,
        },
    )


def _report_source_access_problem_handler(
    arguments: Mapping[str, Any],
) -> ToolResult:
    """Handler for report_source_access_problem tool."""
    url = arguments.get("url")
    if not url:
        return ToolResult(success=False, output="URL is required.")

    error_message = arguments.get("error_message", "Unknown error")
    consecutive_failures = arguments.get("consecutive_failures", 0)

    kb_root = arguments.get("kb_root")
    root_path = Path(kb_root) if kb_root else paths.get_knowledge_graph_root()

    reg = SourceRegistry(root=root_path)
    source = reg.get_source(url)
    if source is None:
        return ToolResult(success=False, output=f"Source not found: {url}")

    # Build discussion content
    title = f"[Access Problem] {source.name}"
    body = f"""## Source Access Problem Report

**Source URL**: {source.url}
**Source Name**: {source.name}
**Source Type**: {source.source_type}
**Consecutive Failures**: {consecutive_failures}

### Error Details

```
{error_message}
```

### Recommended Actions

1. Verify the source URL is still valid
2. Check if the source requires authentication
3. Consider if the source should be marked as deprecated
4. If temporary, wait for the source to recover

### Source Metadata

- **Added By**: {source.added_by}
- **Added At**: {source.added_at.isoformat()}
- **Last Verified**: {source.last_verified.isoformat()}
- **Credibility Score**: {source.credibility_score:.2f}

<!-- monitor-access-problem:{_url_hash(source.url)} -->
"""

    # For now, we'll create an issue instead of a discussion
    # since discussions require additional setup (category ID, etc.)
    # This can be enhanced later to use the discussions API
    
    try:
        repository = github_issues.resolve_repository(arguments.get("repository"))
        token = github_issues.resolve_token(None)
    except github_issues.GitHubIssueError as e:
        return ToolResult(success=False, output=str(e))

    labels = ["access-problem", source.source_type]
    if consecutive_failures >= 5:
        labels.append("needs-attention")

    try:
        outcome = github_issues.create_issue(
            token=token,
            repository=repository,
            title=title,
            body=body,
            labels=labels,
        )
    except github_issues.GitHubIssueError as e:
        return ToolResult(success=False, output=f"Failed to create issue: {e}")

    return ToolResult(
        success=True,
        output={
            "issue_number": outcome.number,
            "issue_url": outcome.html_url,
            "source_url": source.url,
            "consecutive_failures": consecutive_failures,
        },
    )
