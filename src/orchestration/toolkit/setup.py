"""Setup tools for repository configuration."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from urllib.parse import urlparse
from pathlib import Path
from typing import Any, Mapping

from src.knowledge.storage import SourceEntry, SourceRegistry
from src.orchestration.tools import ToolDefinition, ToolRegistry, ActionRisk

def validate_url(args: Mapping[str, Any]) -> dict[str, Any]:
    url = args.get("url")
    if not url:
        return {"valid": False, "error": "URL is required"}
    
    try:
        result = urlparse(url)
        if all([result.scheme, result.netloc]):
            return {"valid": True, "url": url}
        else:
            return {"valid": False, "error": "Invalid URL format"}
    except Exception as e:
        return {"valid": False, "error": str(e)}

def configure_repository(args: Mapping[str, Any]) -> dict[str, Any]:
    """Configure repository and register primary source.
    
    This function:
    1. Writes the manifest.json configuration file
    2. Registers the source_url as the primary source in the source registry
    
    The primary source is automatically set to 'active' status and does not
    require a proposal_discussion or implementation_issue since it comes from the manifest.
    """
    source_url = args.get("source_url")
    topic = args.get("topic")
    frequency = args.get("frequency")
    model = args.get("model", "gpt-4o-mini")
    
    config = {
        "source_url": source_url,
        "topic": topic,
        "frequency": frequency,
        "model": model
    }
    
    config_path = Path("config/manifest.json")
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    
    # Register the primary source in the source registry
    primary_source_registered = False
    primary_source_error = None
    
    if source_url:
        try:
            from src import paths
            registry = SourceRegistry(root=paths.get_knowledge_graph_root())
            
            # Check if source already exists
            if not registry.source_exists(source_url):
                now = datetime.now(timezone.utc)
                
                # Derive name from topic or URL
                source_name = f"{topic} - Primary Source" if topic else "Primary Source"
                
                # Calculate credibility score based on domain
                score = _calculate_primary_source_score(source_url)
                is_official = _is_official_domain(source_url)
                
                source_entry = SourceEntry(
                    url=source_url,
                    name=source_name,
                    source_type="primary",
                    status="active",
                    last_verified=now,
                    added_at=now,
                    added_by="system",
                    proposal_discussion=None,  # Primary sources don't need approval
                    implementation_issue=None,  # Primary sources don't need approval
                    credibility_score=score,
                    is_official=is_official,
                    requires_auth=False,
                    discovered_from=None,
                    parent_source_url=None,
                    content_type="webpage",
                    update_frequency=frequency,
                    topics=[topic] if topic else [],
                    notes="Primary source from manifest.json",
                )
                registry.save_source(source_entry)
                primary_source_registered = True
            else:
                primary_source_registered = True  # Already exists
        except Exception as e:
            primary_source_error = str(e)
        
    return {
        "success": True,
        "path": str(config_path),
        "primary_source_registered": primary_source_registered,
        "primary_source_error": primary_source_error,
    }


def _calculate_primary_source_score(url: str) -> float:
    """Calculate credibility score for a primary source URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Government and education domains get high scores
        if domain.endswith('.gov') or domain.endswith('.gov.uk'):
            return 0.95
        elif domain.endswith('.edu'):
            return 0.90
        elif domain.endswith('.org'):
            return 0.80
        else:
            return 0.70  # Default for primary sources
    except Exception:
        return 0.70


def _is_official_domain(url: str) -> bool:
    """Determine if URL is from an official/authoritative domain."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        return (
            domain.endswith('.gov') or
            domain.endswith('.gov.uk') or
            domain.endswith('.edu') or
            domain.endswith('.mil')
        )
    except Exception:
        return False

def clean_workspace(_: Mapping[str, Any]) -> dict[str, Any]:
    """Clean up workspace directories, preserving .gitkeep."""
    from src import paths
    
    directories = [
        paths.get_evidence_root(),
        paths.get_knowledge_graph_root(),
        paths.get_reports_root(),
    ]
    cleaned = []
    
    for dir_path in directories:
        if not dir_path.exists():
            continue

        # Safety check: Prevent accidental deletion of dev data
        if "dev_data" in str(dir_path.resolve()):
            print(f"Skipping cleanup of dev data directory: {dir_path}")
            continue
            
        for item in dir_path.iterdir():
            if item.name == ".gitkeep":
                continue
            
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        cleaned.append(str(dir_path))
        
    return {"success": True, "cleaned": cleaned}

def configure_upstream(args: Mapping[str, Any]) -> dict[str, Any]:
    """Set the UPSTREAM_REPO repository variable for the sync workflow.
    
    This tool uses the GitHub API to set a repository variable (not a git remote).
    The UPSTREAM_REPO variable is read by the sync-from-upstream.yml workflow to
    know which repository to pull code updates from.
    
    If upstream_repo is not provided, auto-detects from the template repository
    that this repo was created from.
    
    Related:
    - Workflow: .github/workflows/sync-from-upstream.yml
    - Guide: docs/guides/upstream-sync.md
    - API function: src/integrations/github/sync.py::configure_upstream_variable()
    """
    from src.integrations.github.issues import resolve_token, resolve_repository
    from src.integrations.github.sync import configure_upstream_variable
    
    # Get repository and token, defaulting to environment variables
    try:
        repository = args.get("repository")
        repository = resolve_repository(str(repository) if repository else None)
        
        token = args.get("token")
        token = resolve_token(str(token) if token else None)
    except Exception as e:
        return {"success": False, "error": f"Failed to resolve credentials: {str(e)}"}
    
    # Get optional explicit upstream
    upstream_repo = args.get("upstream_repo")
    
    try:
        result = configure_upstream_variable(
            repository=repository,
            token=token,
            upstream_repo=upstream_repo,
        )
        return result
    except Exception as e:
        return {"success": False, "error": str(e)}


def create_welcome_announcement(args: Mapping[str, Any]) -> dict[str, Any]:
    """Create a welcome announcement discussion for a newly-configured repository.
    
    This generates a topic-appropriate announcement in the "Announcements" category
    to welcome users to the research repository.
    
    Args:
        topic: The research topic (required)
        source_url: Primary source URL (optional, for context)
        repository: Repository in owner/repo format (optional)
        token: GitHub token (optional)
        
    Returns:
        dict with success status and discussion URL or error message.
    """
    import os
    from src.integrations.github import discussions as github_discussions
    
    topic = args.get("topic")
    if not topic:
        return {"success": False, "error": "Topic is required"}
    
    source_url = args.get("source_url", "")
    repository = args.get("repository") or os.environ.get("GITHUB_REPOSITORY")
    token = args.get("token") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    
    if not repository:
        return {"success": False, "error": "Repository not specified and GITHUB_REPOSITORY not set"}
    if not token:
        return {"success": False, "error": "Token not specified and GITHUB_TOKEN not set"}
    
    try:
        # Check for Announcements category
        category = github_discussions.get_category_by_name(
            token=token,
            repository=repository,
            category_name="Announcements",
        )
        
        if not category:
            return {
                "success": False,
                "error": (
                    "Announcements category not found. Please enable Discussions in "
                    "repository Settings and create an 'Announcements' category."
                ),
                "category_missing": True,
            }
        
        # Generate announcement content
        title = f"Welcome to {topic} Research"
        
        body_parts = [
            f"# 🔬 Welcome to {topic} Research\n",
            "This repository is set up for automated research tracking and knowledge extraction.\n",
            "## What to Expect\n",
            "- **Source Curation**: New sources will be submitted and discussed in the Sources category",
            "- **Entity Extraction**: People, organizations, and concepts will be tracked in Discussions",
            "- **Evidence Collection**: Documents and data will be parsed and stored",
            "- **Knowledge Graph**: Relationships and profiles will be aggregated automatically\n",
        ]
        
        if source_url:
            body_parts.append("## Primary Source\n")
            body_parts.append(f"This repository is tracking: [{source_url}]({source_url})\n")
        
        body_parts.extend([
            "## Getting Started\n",
            "1. Check the **Sources** category for curated data sources",
            "2. Review entity discussions (People, Organizations, Concepts) for extracted knowledge",
            "3. Browse the `evidence/` directory for parsed documents",
            "4. Explore `knowledge-graph/` for aggregated profiles\n",
            "---\n",
            "*This announcement was automatically generated during repository setup.*",
        ])
        
        body = "\n".join(body_parts)
        
        # Check if announcement already exists
        existing = github_discussions.find_discussion_by_title(
            token=token,
            repository=repository,
            title=title,
            category_id=category.id,
        )
        
        if existing:
            return {
                "success": True,
                "action": "already_exists",
                "discussion_url": existing.url,
                "discussion_number": existing.number,
            }
        
        # Create the announcement
        discussion = github_discussions.create_discussion(
            token=token,
            repository=repository,
            category_id=category.id,
            title=title,
            body=body,
        )
        
        return {
            "success": True,
            "action": "created",
            "discussion_url": discussion.url,
            "discussion_number": discussion.number,
        }
        
    except github_discussions.GitHubDiscussionError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"Failed to create announcement: {e}"}


def register_setup_tools(registry: ToolRegistry) -> None:
    registry.register_tool(
        ToolDefinition(
            name="validate_url",
            description="Validate a source URL.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to validate"}
                },
                "required": ["url"],
            },
            handler=validate_url,
            risk_level=ActionRisk.SAFE
        )
    )
    registry.register_tool(
        ToolDefinition(
            name="configure_repository",
            description="Generate the repository configuration file.",
            parameters={
                "type": "object",
                "properties": {
                    "source_url": {"type": "string"},
                    "topic": {"type": "string"},
                    "frequency": {"type": "string"},
                    "model": {"type": "string", "description": "LLM model to use (default: gpt-4o-mini)"}
                },
                "required": ["source_url", "topic", "frequency"],
            },
            handler=configure_repository,
            risk_level=ActionRisk.SAFE
        )
    )
    registry.register_tool(
        ToolDefinition(
            name="clean_workspace",
            description="Clean up workspace directories (evidence, knowledge-graph, reports), preserving .gitkeep.",
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            handler=clean_workspace,
            risk_level=ActionRisk.DESTRUCTIVE
        )
    )
    registry.register_tool(
        ToolDefinition(
            name="configure_upstream",
            description="Set the UPSTREAM_REPO repository variable via GitHub API. This variable is used by the sync-from-upstream workflow to pull code updates from the template repository. Auto-detects the upstream from the template if not specified.",
            parameters={
                "type": "object",
                "properties": {
                    "repository": {"type": "string", "description": "Repository name in format owner/repo. Defaults to GITHUB_REPOSITORY env var."},
                    "token": {"type": "string", "description": "GitHub token for API access. Defaults to GITHUB_TOKEN env var."},
                    "upstream_repo": {"type": "string", "description": "Explicit upstream repository in owner/repo format. If not provided, auto-detects from the template repository."}
                },
                "required": [],
            },
            handler=configure_upstream,
            risk_level=ActionRisk.SAFE
        )
    )
    registry.register_tool(
        ToolDefinition(
            name="create_welcome_announcement",
            description=(
                "Create a welcome announcement discussion in the repository. "
                "Generates a topic-appropriate title and body for the Announcements category. "
                "Returns the discussion URL if created successfully, or an error if the "
                "Announcements category doesn't exist (requires manual creation)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The research topic for this repository (used to generate welcome content)."
                    },
                    "source_url": {
                        "type": "string",
                        "description": "The primary source URL being tracked (optional, for context in announcement)."
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in format owner/repo. Defaults to GITHUB_REPOSITORY env var."
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token for API access. Defaults to GITHUB_TOKEN env var."
                    }
                },
                "required": ["topic"],
            },
            handler=create_welcome_announcement,
            risk_level=ActionRisk.REVIEW
        )
    )
