"""Acquisition tool registrations for the orchestration runtime.

This toolkit provides tools for acquiring content from registered sources
and storing them in the evidence directory with full provenance tracking.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src import paths
from src.integrations.github.storage import get_github_storage_client
from src.knowledge.storage import SourceEntry, SourceRegistry
from src.parsing.runner import parse_single_target
from src.parsing.storage import ParseStorage

from ..safety import ActionRisk
from ..tools import ToolDefinition, ToolRegistry
from ..types import ToolResult
from ._github_context import resolve_github_client


def register_acquisition_tools(registry: ToolRegistry) -> None:
    """Register all acquisition tools with the registry."""
    
    registry.register_tool(
        ToolDefinition(
            name="acquire_source_content",
            description=(
                "Acquire content from a source URL, parse it, and store in evidence/. "
                "Updates the source registry with content hash and acquisition timestamp. "
                "This is the primary tool for initial acquisition and content updates."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "minLength": 1,
                        "description": "The source URL to acquire content from.",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Force reprocessing even if content hash is unchanged (default: false).",
                    },
                    "evidence_root": {
                        "type": "string",
                        "description": "Path to evidence directory. Defaults to evidence/.",
                    },
                    "kb_root": {
                        "type": "string",
                        "description": "Path to knowledge graph root. Defaults to knowledge-graph/.",
                    },
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            handler=_acquire_source_content_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )


def _acquire_source_content_handler(args: Mapping[str, Any]) -> ToolResult:
    """Handler for acquire_source_content tool."""
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        return ToolResult(
            success=False,
            output=None,
            error="url must be a non-empty string.",
        )
    
    url = url.strip()
    force = bool(args.get("force", False))
    
    # Resolve paths
    evidence_root_arg = args.get("evidence_root")
    evidence_root = Path(evidence_root_arg) if evidence_root_arg else paths.get_evidence_root()
    
    kb_root_arg = args.get("kb_root")
    kb_root = Path(kb_root_arg) if kb_root_arg else paths.get_knowledge_graph_root()
    
    # Get source entry from registry
    registry = SourceRegistry(root=kb_root)
    source = registry.get_source(url)
    
    if source is None:
        return ToolResult(
            success=False,
            output=None,
            error=f"Source not found in registry: {url}",
        )
    
    # Set up parsed storage
    parsed_root = evidence_root / "parsed"
    github_client = get_github_storage_client()
    storage = ParseStorage(parsed_root, github_client=github_client)
    
    # Parse the source
    try:
        outcome = parse_single_target(
            url,
            storage=storage,
            expected_parser="web",
            force=force,
            is_remote=True,
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            output=None,
            error=f"Failed to parse source: {exc}",
        )
    
    # Check for parsing errors
    if outcome.status == "error":
        error_msg = outcome.error or "Parsing failed for unknown reason."
        return ToolResult(
            success=False,
            output=None,
            error=error_msg,
        )
    
    # Update source registry with content hash
    if outcome.checksum:
        # Create updated source entry with content hash
        now = datetime.now(timezone.utc)
        source_dict = source.to_dict()
        source_dict.update({
            "last_content_hash": outcome.checksum,
            "last_checked": now.isoformat(),
            "last_verified": now.isoformat(),
            "check_failures": 0,
        })
        
        updated_source = SourceEntry.from_dict(source_dict)
        
        # Save with GitHub client if available
        github_client_for_registry = resolve_github_client()
        registry_with_client = SourceRegistry(root=kb_root, github_client=github_client_for_registry)
        registry_with_client.save_source(updated_source)
    
    # Build response
    return ToolResult(
        success=True,
        output={
            "url": url,
            "source_name": source.name,
            "status": outcome.status,
            "parser": outcome.parser,
            "checksum": outcome.checksum,
            "artifact_path": outcome.artifact_path,
            "warnings": list(outcome.warnings) if outcome.warnings else [],
            "message": outcome.message,
            "registry_updated": outcome.checksum is not None,
        },
        error=None,
    )


__all__ = ["register_acquisition_tools"]
