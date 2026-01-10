"""CLI commands for synthesis queue management."""

from __future__ import annotations

import argparse
import sys
from typing import List

from src.integrations.github.issues import (
    assign_issue_to_copilot,
    create_issue,
    resolve_repository,
    resolve_token,
)
from src.knowledge.canonical import CanonicalStorage, normalize_name
from src.knowledge.storage import KnowledgeGraphStorage
from src.paths import get_knowledge_graph_root


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add synthesis subcommands to the main CLI parser."""
    parser = subparsers.add_parser(
        "synthesis",
        description="Manage the synthesis queue.",
        help="Manage the synthesis queue.",
    )
    
    sub = parser.add_subparsers(dest="synthesis_command", help="Synthesis operation")
    
    # Create-issue command - create GitHub Issue for entity resolution
    create_parser = sub.add_parser(
        "create-issue",
        description="Create GitHub Issue for batch entity resolution.",
        help="Create GitHub Issue for batch entity resolution.",
    )
    create_parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    create_parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    create_parser.add_argument(
        "--entity-type",
        type=str,
        choices=["Person", "Organization", "Concept", "all"],
        default="all",
        help="Entity type to process (default: all).",
    )
    create_parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Maximum entities per Issue (default: 50).",
    )
    create_parser.add_argument(
        "--full",
        action="store_true",
        help="Full rebuild - reprocess all entities.",
    )
    create_parser.set_defaults(func=create_issue_cli)
    
    # Pending command - list pending entities
    pending_parser = sub.add_parser(
        "pending",
        description="List entities needing synthesis.",
        help="List entities needing synthesis.",
    )
    pending_parser.add_argument(
        "--entity-type",
        type=str,
        choices=["Person", "Organization", "Concept", "all"],
        default="all",
        help="Entity type to check (default: all).",
    )
    pending_parser.set_defaults(func=pending_cli)


def _gather_unresolved_entities(
    entity_type: str,
    kg_storage: KnowledgeGraphStorage,
    canonical_storage: CanonicalStorage,
) -> List[tuple[str, str]]:
    """Gather entities that haven't been resolved to canonical form.
    
    Returns:
        List of (entity_name, source_checksum) tuples
    """
    unresolved: List[tuple[str, str]] = []
    alias_map = canonical_storage.load_alias_map()
    
    # Get all source checksums
    checksums = _list_all_checksums(kg_storage)
    
    # Check each source for unresolved entities
    for checksum in checksums:
        if entity_type == "Person":
            extracted = kg_storage.get_extracted_people(checksum)
            if extracted:
                for name in extracted.people:
                    normalized = normalize_name(name)
                    if normalized not in alias_map.by_type.get("Person", {}):
                        unresolved.append((name, checksum))
        
        elif entity_type == "Organization":
            extracted = kg_storage.get_extracted_organizations(checksum)
            if extracted:
                for name in extracted.organizations:
                    normalized = normalize_name(name)
                    if normalized not in alias_map.by_type.get("Organization", {}):
                        unresolved.append((name, checksum))
        
        elif entity_type == "Concept":
            extracted = kg_storage.get_extracted_concepts(checksum)
            if extracted:
                for name in extracted.concepts:
                    normalized = normalize_name(name)
                    if normalized not in alias_map.by_type.get("Concept", {}):
                        unresolved.append((name, checksum))
    
    return unresolved


def _list_all_checksums(kg_storage: KnowledgeGraphStorage) -> List[str]:
    """List all source checksums in the knowledge graph."""
    checksums: set[str] = set()
    
    # Access protected members - justified for internal CLI utilities
    for directory in [
        kg_storage._people_dir,  # noqa: SLF001
        kg_storage._organizations_dir,  # noqa: SLF001
        kg_storage._concepts_dir,  # noqa: SLF001
    ]:
        if directory.exists():
            for path in directory.glob("*.json"):
                checksums.add(path.stem)
    
    return sorted(checksums)


def _generate_issue_body(
    entity_type: str,
    entities: List[tuple[str, str]],
    batch_number: int,
) -> str:
    """Generate Issue body with instructions for Copilot.
    
    Args:
        entity_type: Type of entities in this batch
        entities: List of (entity_name, source_checksum) tuples
        batch_number: Batch number for title
        
    Returns:
        Markdown formatted issue body
    """
    # Build entity table
    table_rows = ["| Raw Name | Source File |", "|----------|-------------|"]
    for name, checksum in entities:
        source_file = f"`knowledge-graph/{entity_type.lower()}s/{checksum}.json`"
        table_rows.append(f"| {name} | {source_file} |")
    
    entity_table = "\n".join(table_rows)
    
    # Get entity type directory name
    type_dir = f"{entity_type.lower()}s"
    
    body = f"""## Task: Entity Resolution

Resolve the following {entity_type.lower()} names to canonical entities.

## Entities to Process

{entity_table}

## Current Canonical Store

Read existing entities from:
- `knowledge-graph/canonical/{type_dir}/` (all `.json` files)
- `knowledge-graph/canonical/alias-map.json`

## Resolution Rules

For each entity above:

1. **Check alias map** - If normalized name exists in alias map, it's already resolved
2. **Search canonical entities** - Look for semantic match (abbreviation, nickname, variant)
3. **Decide:**
   - **MATCH** → Add as alias to existing canonical entity
   - **NEW** → Create new canonical entity file
   - **AMBIGUOUS** → Add `"needs_review": true` to metadata

## Output Format

### For existing canonical entity (add alias):

Edit `knowledge-graph/canonical/{type_dir}/[canonical-id].json`:
- Add new name to `aliases` array
- Add source checksum to `source_checksums`
- Increment `corroboration_score`
- Add entry to `resolution_history`

### For new canonical entity:

Create `knowledge-graph/canonical/{type_dir}/[slug].json`:
```json
{{
  "canonical_id": "[slug]",
  "canonical_name": "[Primary Name]",
  "entity_type": "{entity_type}",
  "aliases": ["[name]"],
  "source_checksums": ["[checksum]"],
  "corroboration_score": 1,
  "first_seen": "[ISO timestamp]",
  "last_updated": "[ISO timestamp]",
  "resolution_history": [
    {{
      "action": "created",
      "timestamp": "[ISO timestamp]",
      "issue_number": [this issue number],
      "by": "copilot",
      "reasoning": "[why this is a new entity]"
    }}
  ],
  "attributes": {{}},
  "associations": [],
  "metadata": {{"needs_review": false, "confidence": 0.95}}
}}
```

### Update alias map:

Edit `knowledge-graph/canonical/alias-map.json`:
- Add normalized name → canonical_id mapping in `by_type["{entity_type}"]`

## Completion

When you have finished processing all entities:

1. **Create a Pull Request** (NOT a draft - mark it ready for review immediately)
   - Include all new/updated canonical entity files
   - Include updated alias-map.json
   - In the PR description, reference this issue: "Fixes #{{issue_number}}"
   - **IMPORTANT:** Create the PR as "ready for review" from the start (do NOT create as draft)

2. **Add a summary comment** to this issue:
   - How many entities matched to existing canonical entities
   - How many new canonical entities created
   - Any entities flagged for review (ambiguous)

3. **The PR will be auto-approved and merged** by workflows (no manual review needed)

**Note:** If you create a fork to work on this, that's fine - the workflows are configured to handle fork PRs automatically.

---
<!-- copilot:synthesis-batch -->
<!-- batch:{batch_number} -->
<!-- entity-type:{entity_type} -->
"""
    
    return body


def create_issue_cli(args: argparse.Namespace) -> int:
    """Create GitHub Issues for entity resolution."""
    repository = resolve_repository(args.repository)
    token = resolve_token(args.token)
    
    kg_root = get_knowledge_graph_root()
    kg_storage = KnowledgeGraphStorage(root=kg_root)
    canonical_storage = CanonicalStorage(root=kg_root / "canonical")
    
    # Determine which entity types to process
    if args.entity_type == "all":
        entity_types = ["Person", "Organization", "Concept"]
    else:
        entity_types = [args.entity_type]
    
    # Process ONLY the first entity type with work
    # synthesis-continue.yml will trigger next type after this one completes
    for entity_type in entity_types:
        # Gather unresolved entities
        if args.full:
            # Full rebuild - gather all entities
            unresolved = _gather_all_entities(entity_type, kg_storage)
        else:
            # Normal mode - only unresolved
            unresolved = _gather_unresolved_entities(entity_type, kg_storage, canonical_storage)
        
        if not unresolved:
            print(f"No unresolved {entity_type} entities found.")
            continue
            print(f"No unresolved {entity_type} entities found.")
            continue
        
        print(f"Found {len(unresolved)} unresolved {entity_type} entities.")
        
        # Create ONLY the first batch (sequential processing)
        # synthesis-continue.yml will trigger next batch after this one completes
        batch = unresolved[:args.batch_size]
        batch_number = 1
        
        # Generate issue
        remaining = len(unresolved) - len(batch)
        title = f"Synthesis: Resolve {entity_type} Entities (Batch {batch_number}"
        if remaining > 0:
            title += f", {remaining} more pending)"
        else:
            title += ")"
        
        body = _generate_issue_body(entity_type, batch, batch_number)
        
        # Create issue
        try:
            outcome = create_issue(
                repository=repository,
                token=token,
                title=title,
                body=body,
                labels=["synthesis-batch", "copilot"],
            )
            
            print(f"Created Issue #{outcome.number}: {outcome.html_url}")
            
            # Assign to Copilot
            assign_issue_to_copilot(
                repository=repository,
                token=token,
                issue_number=outcome.number,
            )
            print("  → Assigned to Copilot")
            
            if remaining > 0:
                print(f"\n⏳ {remaining} {entity_type} entities remain (will be processed in next batch)")
            
            # Check if other entity types have work
            remaining_types = []
            for other_type in entity_types:
                if other_type == entity_type:
                    continue
                other_unresolved = _gather_unresolved_entities(other_type, kg_storage, canonical_storage) if not args.full else _gather_all_entities(other_type, kg_storage)
                if other_unresolved:
                    remaining_types.append(f"{other_type} ({len(other_unresolved)})")
            
            if remaining_types:
                print(f"⏳ Other entity types pending: {', '.join(remaining_types)}")
            
            print(f"\n✅ Created 1 synthesis Issue. Remaining work will be processed in next batch.")
            return 0
            
        except Exception as e:  # noqa: BLE001 - broad exception for CLI error handling
            print(f"Error creating issue: {e}", file=sys.stderr)
            return 1
    
    print(f"\n✅ No unresolved entities found across all types.")
    return 0
    return 0


def _gather_all_entities(
    entity_type: str,
    kg_storage: KnowledgeGraphStorage,
) -> List[tuple[str, str]]:
    """Gather all entities of a given type (for full rebuild).
    
    Returns:
        List of (entity_name, source_checksum) tuples
    """
    all_entities: List[tuple[str, str]] = []
    checksums = _list_all_checksums(kg_storage)
    
    for checksum in checksums:
        if entity_type == "Person":
            extracted = kg_storage.get_extracted_people(checksum)
            if extracted:
                for name in extracted.people:
                    all_entities.append((name, checksum))
        
        elif entity_type == "Organization":
            extracted = kg_storage.get_extracted_organizations(checksum)
            if extracted:
                for name in extracted.organizations:
                    all_entities.append((name, checksum))
        
        elif entity_type == "Concept":
            extracted = kg_storage.get_extracted_concepts(checksum)
            if extracted:
                for name in extracted.concepts:
                    all_entities.append((name, checksum))
    
    return all_entities


def pending_cli(args: argparse.Namespace) -> int:
    """List pending entities needing synthesis."""
    kg_root = get_knowledge_graph_root()
    kg_storage = KnowledgeGraphStorage(root=kg_root)
    canonical_storage = CanonicalStorage(root=kg_root / "canonical")
    
    # Determine which entity types to check
    if args.entity_type == "all":
        entity_types = ["Person", "Organization", "Concept"]
    else:
        entity_types = [args.entity_type]
    
    total_pending = 0
    
    for entity_type in entity_types:
        unresolved = _gather_unresolved_entities(entity_type, kg_storage, canonical_storage)
        
        if unresolved:
            print(f"\n{entity_type} ({len(unresolved)} pending):")
            print("-" * 60)
            
            # Show first 20
            for name, checksum in unresolved[:20]:
                print(f"  - {name} (from {checksum[:12]}...)")
            
            if len(unresolved) > 20:
                print(f"  ... and {len(unresolved) - 20} more")
            
            total_pending += len(unresolved)
        else:
            print(f"\n{entity_type}: All entities resolved ✓")
    
    print(f"\n{'=' * 60}")
    print(f"Total pending: {total_pending}")
    
    return 0
