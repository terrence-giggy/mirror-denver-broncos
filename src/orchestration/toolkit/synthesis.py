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
            name="get_source_associations",
            description="Retrieve extracted associations for a source document. Returns associations where entities from this source are involved.",
            parameters={
                "type": "object",
                "properties": {
                    "source_checksum": {
                        "type": "string",
                        "description": "Checksum of the source document.",
                    },
                },
                "required": ["source_checksum"],
                "additionalProperties": False,
            },
            handler=_get_source_associations_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="enrich_entity_attributes",
            description="Extract description/definition for any entity (Person, Organization, or Concept) from its source document using LLM. Returns attributes dict with 'description' field containing a concise, factual summary.",
            parameters={
                "type": "object",
                "properties": {
                    "raw_name": {
                        "type": "string",
                        "description": "The entity name to enrich.",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["Person", "Organization", "Concept"],
                        "description": "Type of entity.",
                    },
                    "source_checksum": {
                        "type": "string",
                        "description": "Checksum of the source document where entity was found.",
                    },
                },
                "required": ["raw_name", "entity_type", "source_checksum"],
                "additionalProperties": False,
            },
            handler=_enrich_entity_attributes_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    # Keep old tool name for backward compatibility
    registry.register_tool(
        ToolDefinition(
            name="enrich_concept_attributes",
            description="DEPRECATED: Use enrich_entity_attributes instead. Extract description/definition for a concept from its source document.",
            parameters={
                "type": "object",
                "properties": {
                    "raw_name": {
                        "type": "string",
                        "description": "The concept name to enrich.",
                    },
                    "source_checksum": {
                        "type": "string",
                        "description": "Checksum of the source document where concept was found.",
                    },
                },
                "required": ["raw_name", "source_checksum"],
                "additionalProperties": False,
            },
            handler=lambda args: _enrich_entity_attributes_handler({**args, "entity_type": "Concept"}),
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="resolve_association_targets",
            description="Convert raw association target names to canonical IDs. Takes associations with 'target' (entity name) and returns associations with 'target_id' (canonical ID). For targets not yet in canonical store, uses empty string.",
            parameters={
                "type": "object",
                "properties": {
                    "associations": {
                        "type": "array",
                        "description": "List of association objects with fields: source, target, source_type, target_type, relationship, evidence, confidence",
                        "items": {"type": "object"},
                    },
                },
                "required": ["associations"],
                "additionalProperties": False,
            },
            handler=_resolve_association_targets_handler,
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
                    "attributes": {
                        "type": "object",
                        "description": "Optional entity-specific attributes (e.g., for Concepts: {'description': '...'}). Defaults to empty dict.",
                    },
                    "associations": {
                        "type": "array",
                        "description": "Optional list of association objects from source data. Each should have: target_id, target_type, relationship, evidence. Defaults to empty array.",
                        "items": {"type": "object"},
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
            import sys
            print(f"\n🔍 Scanning {entity_type} directory: {directory}", file=sys.stderr)
            
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
                    
                    print(f"  ✓ Loaded {source_checksum[:12]}... via KnowledgeGraphStorage: {len(entity_list)} entities", file=sys.stderr)
                except Exception as e:
                    # Fallback: Try reading JSON directly and extract entity list
                    print(f"  ⚠ KnowledgeGraphStorage failed for {source_checksum[:12]}...: {type(e).__name__}", file=sys.stderr)
                    try:
                        with entity_file.open("r", encoding="utf-8") as f:
                            data = json.load(f)
                        
                        # Handle different JSON formats
                        if isinstance(data, list):
                            # Simple list format (backward compatibility)
                            entity_list = data
                            print(f"  ✓ Loaded {source_checksum[:12]}... as JSON list: {len(entity_list)} entities", file=sys.stderr)
                        elif isinstance(data, dict):
                            # ExtractedPeople/Organizations/Concepts format
                            if entity_type == "Person" and "people" in data:
                                entity_list = data["people"]
                                print(f"  ✓ Loaded {source_checksum[:12]}... from dict['people']: {len(entity_list)} entities", file=sys.stderr)
                            elif entity_type == "Organization" and "organizations" in data:
                                entity_list = data["organizations"]
                                print(f"  ✓ Loaded {source_checksum[:12]}... from dict['organizations']: {len(entity_list)} entities", file=sys.stderr)
                            elif entity_type == "Concept" and "concepts" in data:
                                entity_list = data["concepts"]
                                print(f"  ✓ Loaded {source_checksum[:12]}... from dict['concepts']: {len(entity_list)} entities", file=sys.stderr)
                            else:
                                print(f"  ✗ Unrecognized dict format for {source_checksum[:12]}...: keys={list(data.keys())}", file=sys.stderr)
                    except Exception as fallback_error:
                        # Skip files that can't be loaded
                        print(f"  ✗ Fallback failed for {source_checksum[:12]}...: {type(fallback_error).__name__}: {fallback_error}", file=sys.stderr)
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

        import sys
        print(f"\n📊 Summary for {entity_type}:", file=sys.stderr)
        print(f"   Total pending entities: {len(pending)}", file=sys.stderr)
        print(f"   Existing aliases in canonical store: {len(existing_aliases)}", file=sys.stderr)
        if pending:
            print(f"   Sample pending entities:", file=sys.stderr)
            for entity in pending[:3]:
                print(f"     • {entity['raw_name']} (from {entity['source_checksum'][:12]}...)", file=sys.stderr)

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
    attributes = args.get("attributes", {})
    associations = args.get("associations", [])

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
            "attributes": attributes,
            "associations": associations,
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
            attributes = change.get("attributes", {})
            associations = change.get("associations", [])

            if is_new:
                # Create new canonical entity
                now = datetime.now(timezone.utc)
                
                # Convert associations to CanonicalAssociation objects
                from src.knowledge.canonical import CanonicalAssociation
                canonical_associations = []
                for assoc in associations:
                    # Group associations by target
                    canonical_associations.append(CanonicalAssociation(
                        target_id=assoc.get("target_id", ""),
                        target_type=assoc.get("target_type", "Unknown"),
                        relationships=[{"type": assoc.get("relationship", "related"), "count": 1}],
                        source_checksums=[source_checksum],
                    ))
                
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
                    attributes=attributes,
                    associations=canonical_associations,
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

        # Get list of modified file paths (not content - files are already on disk)
        canonical_dir = canonical_store.root
        modified_files = []
        seen_paths = set()  # Track paths to avoid duplicates
        
        import sys
        print(f"\n📂 Building modified files list:", file=sys.stderr)
        print(f"   canonical_dir: {canonical_dir}", file=sys.stderr)
        print(f"   canonical_dir.parent.parent: {canonical_dir.parent.parent}", file=sys.stderr)
        
        # Add all entity files that were created/updated (paths only)
        # Use seen_paths to deduplicate - multiple raw entities may resolve to same canonical entity
        for change in _batch_pending_changes:
            entity_type = change["entity_type"]
            canonical_id = change["canonical_id"]
            entity_path = canonical_dir / f"{entity_type.lower()}s" / f"{canonical_id}.json"
            if entity_path.exists():
                rel_path = str(entity_path.relative_to(canonical_dir.parent.parent))
                if rel_path not in seen_paths:
                    modified_files.append(rel_path)  # Just the path
                    seen_paths.add(rel_path)
                    print(f"   ✓ Added entity: {rel_path}", file=sys.stderr)
                else:
                    print(f"   ⊘ Skipped duplicate: {rel_path}", file=sys.stderr)
            else:
                print(f"   ✗ Entity file not found: {entity_path}", file=sys.stderr)
        
        # Add alias map (path only)
        alias_map_path = canonical_dir / "alias-map.json"
        print(f"\n📍 Checking alias map:", file=sys.stderr)
        print(f"   Path: {alias_map_path}", file=sys.stderr)
        print(f"   Exists: {alias_map_path.exists()}", file=sys.stderr)
        
        if alias_map_path.exists():
            rel_path = str(alias_map_path.relative_to(canonical_dir.parent.parent))
            print(f"   ✓ Adding alias-map.json as: {rel_path}", file=sys.stderr)
            modified_files.append(rel_path)  # Just the path
        else:
            # This should never happen - log warning
            print(f"   ✗ ERROR: alias-map.json not found at {alias_map_path}", file=sys.stderr)

        # Debug: Log what files are being returned
        import sys
        print(f"📦 save_synthesis_batch returning {len(modified_files)} file paths:", file=sys.stderr)
        for f in modified_files:
            print(f"   - {f}", file=sys.stderr)

        return ToolResult(
            success=True,
            output={
                "batch_id": batch_id,
                "entities_created": entities_created,
                "entities_updated": entities_updated,
                "total_resolutions": entities_created + entities_updated,
                "modified_file_paths": modified_files,  # Just paths, not content
            },
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _get_source_associations_handler(args: Mapping[str, Any]) -> ToolResult:
    """Retrieve extracted associations for a source document."""
    source_checksum = args["source_checksum"]

    try:
        kb_dir = get_knowledge_graph_root()
        kb_storage = KnowledgeGraphStorage(kb_dir)

        # Load associations for this source
        extracted_assoc = kb_storage.get_extracted_associations(source_checksum)
        
        if extracted_assoc is None or not extracted_assoc.associations:
            return ToolResult(
                success=True,
                output={
                    "source_checksum": source_checksum,
                    "associations": [],
                    "count": 0,
                },
                error=None,
            )

        # Convert to dict format for agent
        associations = [assoc.to_dict() for assoc in extracted_assoc.associations]

        return ToolResult(
            success=True,
            output={
                "source_checksum": source_checksum,
                "associations": associations,
                "count": len(associations),
            },
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _enrich_entity_attributes_handler(args: Mapping[str, Any]) -> ToolResult:
    """Extract description/definition for any entity from its source document using LLM."""
    raw_name = args["raw_name"]
    entity_type = args.get("entity_type", "Concept")  # Default to Concept for backward compatibility
    source_checksum = args["source_checksum"]

    try:
        # Try to load the source document's parsed markdown
        kb_dir = get_knowledge_graph_root()
        evidence_dir = kb_dir.parent / "evidence" / "parsed"
        
        # Look for parsed markdown file with this checksum
        parsed_file = evidence_dir / f"{source_checksum}.md"
        
        if not parsed_file.exists():
            # No parsed content available - return minimal attributes
            return ToolResult(
                success=True,
                output={
                    "attributes": {
                        "description": f"{entity_type}: {raw_name}",
                        "enrichment_status": "no_source_content",
                    },
                },
                error=None,
            )

        # Read the source content
        content = parsed_file.read_text(encoding="utf-8")
        
        # Use LLM to extract a high-quality description
        description = _extract_entity_description_llm(raw_name, entity_type, content)
        
        # Fallback to heuristics if LLM fails
        if not description or description.startswith(f"{entity_type}:"):
            description = _extract_entity_description_heuristic(raw_name, entity_type, content)

        return ToolResult(
            success=True,
            output={
                "attributes": {
                    "description": description,
                    "enrichment_status": "extracted",
                },
            },
            error=None,
        )
    except Exception as exc:
        # Fallback to minimal attributes on error
        return ToolResult(
            success=True,
            output={
                "attributes": {
                    "description": f"{entity_type}: {raw_name}",
                    "enrichment_status": "error",
                    "error_message": str(exc),
                },
            },
            error=None,
        )


def _extract_entity_description_llm(entity_name: str, entity_type: str, content: str, max_length: int = 300) -> str:
    """Extract a description for any entity from document content using LLM."""
    try:
        from src.integrations.github.models import GitHubModelsClient
        
        # Truncate content to avoid token limits (keep first ~8000 chars = ~2000 tokens)
        truncated_content = content[:8000] if len(content) > 8000 else content
        
        # Create LLM client with mini model for efficiency
        client = GitHubModelsClient(model="gpt-4o-mini")
        
        # Customize prompt based on entity type
        entity_guidance = {
            "Person": "who they are, their role, title, or significance in the context",
            "Organization": "what the organization is, its purpose, or its role in the context",
            "Concept": "what the concept is, its meaning, or its significance in the context",
        }
        
        guidance = entity_guidance.get(entity_type, "what it is or represents")
        
        system_prompt = (
            f"You are an expert at extracting concise, informative descriptions of {entity_type.lower()}s from documents. "
            "Your task is to read the provided text and extract a clear, factual description. "
            "The description should be 1-3 sentences, focusing on key information. "
            f"Do NOT just say '{entity_type}: [name]' - provide actual information about it. "
            "If not clearly defined in the text, provide context about how it's mentioned or used."
        )
        
        user_prompt = f"""Extract a description for the {entity_type.lower()} "{entity_name}" from this text:

{truncated_content}

Provide a clear, concise description (1-3 sentences) focusing on {guidance}."""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        response = client.chat_completion(
            messages=messages,
            temperature=0.3,  # Low temperature for factual extraction
            max_tokens=150,    # Limit response length
        )
        
        description = response.content.strip()
        
        # Truncate if too long
        if len(description) > max_length:
            # Try to cut at sentence boundary
            sentences = description.split(". ")
            description = ". ".join(sentences[:2])
            if len(description) > max_length:
                description = description[:max_length] + "..."
        
        return description
        
    except Exception as e:
        # Log error and return empty string to trigger fallback
        import sys
        print(f"⚠ LLM description extraction failed for {entity_type} '{entity_name}': {type(e).__name__}: {e}", file=sys.stderr)
        return ""


def _extract_entity_description_heuristic(entity_name: str, entity_type: str, content: str, max_length: int = 500) -> str:
    """Extract a description for any entity from document content using heuristics (fallback)."""
    # Simple heuristic: find sentences containing the entity
    # Split into sentences (rough approximation)
    sentences = content.replace("\\n", " ").split(". ")
    
    # Find sentences mentioning the entity (case-insensitive)
    entity_lower = entity_name.lower()
    relevant_sentences = []
    
    for sentence in sentences:
        if entity_lower in sentence.lower():
            # Clean up the sentence
            clean_sentence = sentence.strip()
            if clean_sentence and len(clean_sentence) > 20:  # Ignore very short sentences
                relevant_sentences.append(clean_sentence)
                
                # Stop if we have enough context
                total_length = sum(len(s) for s in relevant_sentences)
                if total_length >= max_length:
                    break
    
    if relevant_sentences:
        # Combine relevant sentences
        description = ". ".join(relevant_sentences[:3])  # Max 3 sentences
        
        # Truncate if too long
        if len(description) > max_length:
            description = description[:max_length] + "..."
        
        return description
    
    # Fallback: use first part of document if no specific mention found
    preview = content[:max_length].strip()
    if "\\n" in preview:
        # Get first paragraph
        preview = preview.split("\\n\\n")[0]
    
    return f"{entity_type}: {entity_name} - {preview}..."

def _resolve_association_targets_handler(args: Mapping[str, Any]) -> ToolResult:
    """Convert raw association target names to canonical IDs using alias map."""
    associations = args.get("associations", [])

    if not associations:
        return ToolResult(
            success=True,
            output={
                "resolved_associations": [],
                "count": 0,
                "unresolved_count": 0,
            },
            error=None,
        )

    try:
        canonical_store = _get_or_create_canonical_store()
        alias_map = canonical_store.load_alias_map()
        
        from src.knowledge.canonical import normalize_name
        
        resolved_associations = []
        unresolved_count = 0
        
        for assoc in associations:
            # Extract fields from raw association
            target_name = assoc.get("target", "")
            target_type = assoc.get("target_type", "Unknown")
            relationship = assoc.get("relationship", "related")
            
            if not target_name:
                # Skip associations without target
                unresolved_count += 1
                continue
            
            # Normalize target name and look up in alias map
            normalized_target = normalize_name(target_name)
            target_id = ""
            
            # Look up canonical ID in alias map by type
            if target_type in alias_map.by_type:
                target_id = alias_map.by_type[target_type].get(normalized_target, "")
            
            if not target_id:
                # Target not yet in canonical store - leave target_id empty
                # (this is expected for entities that haven't been synthesized yet)
                unresolved_count += 1
            
            # Create resolved association with canonical target_id
            resolved_associations.append({
                "target_id": target_id,
                "target_type": target_type,
                "relationship": relationship,
            })
        
        return ToolResult(
            success=True,
            output={
                "resolved_associations": resolved_associations,
                "count": len(resolved_associations),
                "unresolved_count": unresolved_count,
            },
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))