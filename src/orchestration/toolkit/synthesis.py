"""Synthesis tool registrations for entity resolution in the orchestration runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.knowledge.canonical import CanonicalStorage
from src.knowledge.storage import KnowledgeGraphStorage
from src.paths import get_knowledge_graph_root

from ..safety import ActionRisk
from ..tools import ToolDefinition, ToolRegistry
from ..types import ToolResult


def register_synthesis_tools(registry: ToolRegistry) -> None:
    """Register synthesis-specific tools for entity resolution."""

    registry.register_tool(
        ToolDefinition(
            name="list_pending_entities",
            description="List entities that need synthesis (not yet in canonical store).",
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["Person", "Organization", "Concept"],
                        "description": "Type of entities to list.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum number of entities to return. Defaults to 50.",
                    },
                },
                "required": ["entity_type"],
                "additionalProperties": False,
            },
            handler=_list_pending_entities_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_canonical_entity",
            description="Retrieve a canonical entity by ID.",
            parameters={
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["Person", "Organization", "Concept"],
                        "description": "Type of entity.",
                    },
                    "canonical_id": {
                        "type": "string",
                        "description": "Canonical ID (slug) of the entity.",
                    },
                },
                "required": ["entity_type", "canonical_id"],
                "additionalProperties": False,
            },
            handler=_get_canonical_entity_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_alias_map",
            description="Retrieve the alias map for looking up existing canonical entities.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
            handler=_get_alias_map_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="resolve_entity",
            description="Resolve a raw entity name to a canonical entity (create or update).",
            parameters={
                "type": "object",
                "properties": {
                    "raw_name": {
                        "type": "string",
                        "description": "Raw entity name from extraction.",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["Person", "Organization", "Concept"],
                        "description": "Type of entity.",
                    },
                    "source_checksum": {
                        "type": "string",
                        "description": "Checksum of the source document.",
                    },
                    "canonical_id": {
                        "type": "string",
                        "description": "Canonical ID to match to (for existing entities), or new slug (for new entities).",
                    },
                    "is_new": {
                        "type": "boolean",
                        "description": "Whether this creates a new canonical entity (true) or adds to existing (false).",
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Explanation of why this resolution decision was made.",
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                        "description": "Confidence score for this resolution. Defaults to 0.95.",
                    },
                    "needs_review": {
                        "type": "boolean",
                        "description": "Whether this resolution needs human review. Defaults to false.",
                    },
                },
                "required": ["raw_name", "entity_type", "source_checksum", "canonical_id", "is_new", "reasoning"],
                "additionalProperties": False,
            },
            handler=_resolve_entity_handler,
            risk_level=ActionRisk.SAFE,  # Only modifies in-memory state, not persisted until save_batch
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="save_synthesis_batch",
            description="Save all pending entity resolutions and return file changes with content for PR creation. Returns array of {path, content} objects ready for commit_files_batch.",
            parameters={
                "type": "object",
                "properties": {
                    "batch_id": {
                        "type": "string",
                        "description": "Unique identifier for this synthesis batch (e.g., timestamp or run ID).",
                    },
                },
                "required": ["batch_id"],
                "additionalProperties": False,
            },
            handler=_save_synthesis_batch_handler,
            risk_level=ActionRisk.SAFE,  # Returns data for external PR creation
        )
    )


# Internal state for batch processing
_batch_canonical_store: CanonicalStorage | None = None
_batch_pending_changes: list[dict[str, Any]] = []


def _get_or_create_canonical_store() -> CanonicalStorage:
    """Get or create the canonical store for batch processing."""
    global _batch_canonical_store
    if _batch_canonical_store is None:
        kb_dir = get_knowledge_graph_root()
        canonical_dir = kb_dir / "canonical"
        _batch_canonical_store = CanonicalStorage(canonical_dir, github_client=None)
    return _batch_canonical_store


def _list_pending_entities_handler(args: Mapping[str, Any]) -> ToolResult:
    """List entities that need synthesis."""
    entity_type = args["entity_type"]
    limit = args.get("limit", 50)

    try:
        kb_dir = get_knowledge_graph_root()
        kb_storage = KnowledgeGraphStorage(kb_dir)
        canonical_store = _get_or_create_canonical_store()

        # Load alias map to check what's already resolved
        alias_map = canonical_store.load_alias_map()
        
        # Get type-specific aliases
        type_key = entity_type
        existing_aliases = alias_map.by_type.get(type_key, {})

        # Collect unresolved entities using KnowledgeGraphStorage
        from src.knowledge.canonical import normalize_name
        pending = []
        
        # Get all checksums for this entity type
        if entity_type == "Person":
            directory = kb_storage._people_dir  # noqa: SLF001
        elif entity_type == "Organization":
            directory = kb_storage._organizations_dir  # noqa: SLF001
        elif entity_type == "Concept":
            directory = kb_storage._concepts_dir  # noqa: SLF001
        else:
            return ToolResult(success=False, output=None, error=f"Unknown entity_type: {entity_type}")
        
        if directory.exists():
            for entity_file in directory.glob("*.json"):
                source_checksum = entity_file.stem
                entity_list = []
                
                try:
                    # Try loading using KnowledgeGraphStorage methods first
                    if entity_type == "Person":
                        extracted = kb_storage.get_extracted_people(source_checksum)
                        entity_list = extracted.people if extracted else []
                    elif entity_type == "Organization":
                        extracted = kb_storage.get_extracted_organizations(source_checksum)
                        entity_list = extracted.organizations if extracted else []
                    elif entity_type == "Concept":
                        extracted = kb_storage.get_extracted_concepts(source_checksum)
                        entity_list = extracted.concepts if extracted else []
                except Exception:
                    # Fallback: Try reading as simple JSON list (for backward compatibility / tests)
                    try:
                        with entity_file.open("r", encoding="utf-8") as f:
                            data = json.load(f)
                        if isinstance(data, list):
                            entity_list = data
                    except Exception:
                        # Skip files that can't be loaded
                        continue
                
                for entity_name in entity_list:
                    normalized = normalize_name(entity_name)
                    
                    if normalized not in existing_aliases:
                        pending.append({
                            "raw_name": entity_name,
                            "source_checksum": source_checksum,
                            "normalized": normalized,
                        })
                        
                        if len(pending) >= limit:
                            break
                
                if len(pending) >= limit:
                    break

        return ToolResult(
            success=True,
            output={
                "entity_type": entity_type,
                "pending_count": len(pending),
                "pending_entities": pending[:limit],
            },
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _get_canonical_entity_handler(args: Mapping[str, Any]) -> ToolResult:
    """Retrieve a canonical entity by ID."""
    entity_type = args["entity_type"]
    canonical_id = args["canonical_id"]

    try:
        canonical_store = _get_or_create_canonical_store()
        entity = canonical_store.get_entity(canonical_id, entity_type)
        
        if entity is None:
            return ToolResult(
                success=False,
                output=None,
                error=f"Entity not found: {entity_type}/{canonical_id}",
            )

        return ToolResult(
            success=True,
            output=entity.to_dict(),
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _get_alias_map_handler(args: Mapping[str, Any]) -> ToolResult:
    """Retrieve the alias map."""
    try:
        canonical_store = _get_or_create_canonical_store()
        alias_map = canonical_store.load_alias_map()

        return ToolResult(
            success=True,
            output=alias_map.to_dict(),
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _resolve_entity_handler(args: Mapping[str, Any]) -> ToolResult:
    """Resolve a raw entity name to a canonical entity."""
    global _batch_pending_changes
    
    raw_name = args["raw_name"]
    entity_type = args["entity_type"]
    source_checksum = args["source_checksum"]
    canonical_id = args["canonical_id"]
    is_new = args["is_new"]
    reasoning = args["reasoning"]
    confidence = args.get("confidence", 0.95)
    needs_review = args.get("needs_review", False)

    try:
        # Record this resolution for later batch save
        _batch_pending_changes.append({
            "raw_name": raw_name,
            "entity_type": entity_type,
            "source_checksum": source_checksum,
            "canonical_id": canonical_id,
            "is_new": is_new,
            "reasoning": reasoning,
            "confidence": confidence,
            "needs_review": needs_review,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return ToolResult(
            success=True,
            output={
                "message": f"Recorded resolution: {raw_name} → {canonical_id}",
                "pending_changes_count": len(_batch_pending_changes),
            },
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _save_synthesis_batch_handler(args: Mapping[str, Any]) -> ToolResult:
    """Save all pending entity resolutions and return file changes."""
    global _batch_pending_changes
    
    batch_id = args["batch_id"]

    try:
        from src.knowledge.canonical import CanonicalEntity, ResolutionEvent, normalize_name
        
        canonical_store = _get_or_create_canonical_store()
        alias_map = canonical_store.load_alias_map()
        
        # Track which files need to be updated
        files_to_save = []
        entities_created = 0
        entities_updated = 0

        # Process each resolution
        for change in _batch_pending_changes:
            entity_type = change["entity_type"]
            canonical_id = change["canonical_id"]
            raw_name = change["raw_name"]
            source_checksum = change["source_checksum"]
            is_new = change["is_new"]
            reasoning = change["reasoning"]
            confidence = change["confidence"]
            needs_review = change["needs_review"]

            if is_new:
                # Create new canonical entity
                now = datetime.now(timezone.utc)
                entity = CanonicalEntity(
                    canonical_id=canonical_id,
                    canonical_name=raw_name,
                    entity_type=entity_type,
                    aliases=[raw_name],
                    source_checksums=[source_checksum],
                    corroboration_score=1,
                    first_seen=now,
                    last_updated=now,
                    resolution_history=[
                        ResolutionEvent(
                            action="created",
                            timestamp=now,
                            by="synthesis-agent",
                            reasoning=reasoning,
                        )
                    ],
                    attributes={},
                    associations=[],
                    metadata={
                        "synthesis_complete": True,
                        "synthesis_batch_id": batch_id,
                        "confidence": confidence,
                        "needs_review": needs_review,
                    },
                )
                canonical_store.save_entity(entity)
                entities_created += 1
            else:
                # Update existing entity
                entity = canonical_store.get_entity(canonical_id, entity_type)
                if entity is None:
                    return ToolResult(
                        success=False,
                        output=None,
                        error=f"Cannot update non-existent entity: {canonical_id}",
                    )

                # Add alias if not already present
                if raw_name not in entity.aliases:
                    entity.aliases.append(raw_name)

                # Add source checksum if not already present
                if source_checksum not in entity.source_checksums:
                    entity.source_checksums.append(source_checksum)
                    entity.corroboration_score = len(entity.source_checksums)

                # Update metadata
                entity.last_updated = datetime.now(timezone.utc)
                entity.resolution_history.append(
                    ResolutionEvent(
                        action="alias_added",
                        timestamp=entity.last_updated,
                        by="synthesis-agent",
                        alias=raw_name,
                        reasoning=reasoning,
                    )
                )
                entity.metadata["synthesis_batch_id"] = batch_id

                canonical_store.save_entity(entity)
                entities_updated += 1

            # Update alias map
            normalized = normalize_name(raw_name)
            type_key = entity_type
            if type_key not in alias_map.by_type:
                alias_map.by_type[type_key] = {}
            alias_map.by_type[type_key][normalized] = canonical_id

        # Save alias map
        alias_map.last_updated = datetime.now(timezone.utc)
        canonical_store.save_alias_map(alias_map)

        # Get list of modified files and their content for PR
        canonical_dir = canonical_store.root
        modified_files = []
        seen_paths = set()  # Track paths to avoid duplicates
        
        import sys
        print(f"\n📂 Building modified files list:", file=sys.stderr)
        print(f"   canonical_dir: {canonical_dir}", file=sys.stderr)
        print(f"   canonical_dir.parent.parent: {canonical_dir.parent.parent}", file=sys.stderr)
        
        # Add all entity files that were created/updated (with content)
        # Use seen_paths to deduplicate - multiple raw entities may resolve to same canonical entity
        for change in _batch_pending_changes:
            entity_type = change["entity_type"]
            canonical_id = change["canonical_id"]
            entity_path = canonical_dir / f"{entity_type.lower()}s" / f"{canonical_id}.json"
            if entity_path.exists():
                rel_path = str(entity_path.relative_to(canonical_dir.parent.parent))
                if rel_path not in seen_paths:
                    content = entity_path.read_text(encoding="utf-8")
                    modified_files.append({"path": rel_path, "content": content})
                    seen_paths.add(rel_path)
                    print(f"   ✓ Added entity: {rel_path}", file=sys.stderr)
                else:
                    print(f"   ⊘ Skipped duplicate: {rel_path}", file=sys.stderr)
            else:
                print(f"   ✗ Entity file not found: {entity_path}", file=sys.stderr)
        
        # Add alias map (with content)
        alias_map_path = canonical_dir / "alias-map.json"
        print(f"\n📍 Checking alias map:", file=sys.stderr)
        print(f"   Path: {alias_map_path}", file=sys.stderr)
        print(f"   Exists: {alias_map_path.exists()}", file=sys.stderr)
        
        if alias_map_path.exists():
            rel_path = str(alias_map_path.relative_to(canonical_dir.parent.parent))
            content = alias_map_path.read_text(encoding="utf-8")
            print(f"   ✓ Adding alias-map.json as: {rel_path}", file=sys.stderr)
            print(f"   Content preview: {content[:200]}...", file=sys.stderr)
            modified_files.append({"path": rel_path, "content": content})
        else:
            # This should never happen - log warning
            print(f"   ✗ ERROR: alias-map.json not found at {alias_map_path}", file=sys.stderr)

        # Clear batch state
        _batch_pending_changes = []
        
        # Debug: Log what files are being returned
        import sys
        print(f"📦 save_synthesis_batch returning {len(modified_files)} files:", file=sys.stderr)
        for f in modified_files:
            print(f"   - {f['path']} ({len(f['content'])} bytes)", file=sys.stderr)

        return ToolResult(
            success=True,
            output={
                "batch_id": batch_id,
                "entities_created": entities_created,
                "entities_updated": entities_updated,
                "total_resolutions": entities_created + entities_updated,
                "modified_files": modified_files,
            },
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))
