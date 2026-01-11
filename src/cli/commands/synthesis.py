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
    
    # Run-batch command - LLM-driven entity resolution
    batch_parser = sub.add_parser(
        "run-batch",
        description="Run LLM-driven entity resolution batch.",
        help="Run LLM-driven entity resolution batch.",
    )
    batch_parser.add_argument(
        "--entity-type",
        type=str,
        choices=["Person", "Organization", "Concept"],
        default="Organization",
        help="Entity type to process (default: Organization).",
    )
    batch_parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Maximum entities per batch (default: 50).",
    )
    batch_parser.add_argument(
        "--branch-name",
        type=str,
        help="Branch name for commits. If not provided, will be generated as 'synthesis/ENTITY_TYPE-TIMESTAMP'.",
    )
    batch_parser.add_argument(
        "--model",
        type=str,
        default="gpt-4o",
        help="Model to use for synthesis (default: gpt-4o).",
    )
    batch_parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    batch_parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    batch_parser.set_defaults(func=run_batch_cli)


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
    
    # Get source checksums for this entity type only
    checksums = _list_all_checksums(kg_storage, entity_type)
    
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


def _list_all_checksums(kg_storage: KnowledgeGraphStorage, entity_type: str | None = None) -> List[str]:
    """List all source checksums in the knowledge graph.
    
    Args:
        kg_storage: Knowledge graph storage instance
        entity_type: Optional entity type to filter by (Person, Organization, Concept).
                     If None, returns checksums from all types.
    
    Returns:
        Sorted list of unique checksums
    """
    checksums: set[str] = set()
    
    # Determine which directories to scan based on entity_type
    # Access protected members - justified for internal CLI utilities
    if entity_type == "Person":
        directories = [kg_storage._people_dir]  # noqa: SLF001
    elif entity_type == "Organization":
        directories = [kg_storage._organizations_dir]  # noqa: SLF001
    elif entity_type == "Concept":
        directories = [kg_storage._concepts_dir]  # noqa: SLF001
    else:
        # No filter - scan all types
        directories = [
            kg_storage._people_dir,  # noqa: SLF001
            kg_storage._organizations_dir,  # noqa: SLF001
            kg_storage._concepts_dir,  # noqa: SLF001
        ]
    
    for directory in directories:
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

1. **Normalize the name** - Lowercase, strip whitespace, collapse multiple spaces
2. **Check alias map** - Read `knowledge-graph/canonical/alias-map.json` and check if normalized name exists under `by_type["{entity_type}"]`
   - If found → Entity already resolved, skip it
3. **Search canonical entities** - Read files in `knowledge-graph/canonical/{type_dir}/` to find semantic matches
   - Look for abbreviations (e.g., "Broncos" → "Denver Broncos")
   - Look for variants (e.g., "The Denver Broncos" → "Denver Broncos")
   - Look for nicknames or alternate spellings
4. **Decide:**
   - **MATCH** → Update existing canonical entity file (add alias, increment corroboration)
   - **NEW** → Create new canonical entity file
   - **AMBIGUOUS** → Create new entity with `"needs_review": true`, `"confidence": 0.5`

## Output Format

### For existing canonical entity (add alias):

**Steps:**
1. Read the existing entity file: `knowledge-graph/canonical/{type_dir}/[canonical-id].json`
2. Parse the JSON
3. Add the new name to the `aliases` array (if not already present)
4. Add the source checksum to `source_checksums` array (if not already present)
5. Update `corroboration_score` to match the length of `source_checksums`
6. Update `last_updated` to current ISO timestamp
7. Add a new entry to `resolution_history` array:
   ```json
   {{
     "action": "alias_added",
     "timestamp": "[current ISO timestamp]",
     "by": "copilot",
     "issue_number": [this issue number],
     "alias": "[the new name being added]",
     "reasoning": "Matched to existing entity via [explain: abbreviation/variant/etc]"
   }}
   ```
8. Write the updated JSON back to the same file

**Example result after adding "Broncos" as alias to "Denver Broncos":**
```json
{{
  "canonical_id": "denver-broncos",
  "canonical_name": "Denver Broncos",
  "entity_type": "Organization",
  "aliases": ["Denver Broncos", "Broncos"],
  "source_checksums": ["abc123", "def456"],
  "corroboration_score": 2,
  "first_seen": "2026-01-08T10:00:00Z",
  "last_updated": "2026-01-09T15:30:00Z",
  "resolution_history": [
    {{
      "action": "created",
      "timestamp": "2026-01-08T10:00:00Z",
      "by": "copilot",
      "issue_number": 42,
      "reasoning": "New entity extracted from source"
    }},
    {{
      "action": "alias_added",
      "timestamp": "2026-01-09T15:30:00Z",
      "by": "copilot",
      "issue_number": [this issue number],
      "alias": "Broncos",
      "reasoning": "Matched as abbreviation of canonical name"
    }}
  ],
  "attributes": {{}},
  "associations": [],
  "metadata": {{"needs_review": false, "confidence": 0.95}}
}}
```

### For new canonical entity:

**Steps:**
1. Create a slug from the name: lowercase, replace spaces with hyphens
2. Create new file: `knowledge-graph/canonical/{type_dir}/[slug].json`
3. Use this JSON structure:

```json
{{
  "canonical_id": "[slug]",
  "canonical_name": "[Primary Name - the most common/official form]",
  "entity_type": "{entity_type}",
  "aliases": ["[the extracted name]"],
  "source_checksums": ["[the source checksum from table]"],
  "corroboration_score": 1,
  "first_seen": "[current ISO timestamp]",
  "last_updated": "[current ISO timestamp]",
  "resolution_history": [
    {{
      "action": "created",
      "timestamp": "[current ISO timestamp]",
      "issue_number": [this issue number],
      "by": "copilot",
      "reasoning": "New entity - no existing match found in canonical store"
    }}
  ],
  "attributes": {{}},
  "associations": [],
  "metadata": {{"needs_review": false, "confidence": 0.95}}
}}
```

**Example - creating new entity for "Kansas City Chiefs":**
```json
{{
  "canonical_id": "kansas-city-chiefs",
  "canonical_name": "Kansas City Chiefs",
  "entity_type": "Organization",
  "aliases": ["Kansas City Chiefs"],
  "source_checksums": ["xyz789"],
  "corroboration_score": 1,
  "first_seen": "2026-01-09T15:30:00Z",
  "last_updated": "2026-01-09T15:30:00Z",
  "resolution_history": [
    {{
      "action": "created",
      "timestamp": "2026-01-09T15:30:00Z",
      "issue_number": [this issue number],
      "by": "copilot",
      "reasoning": "New entity - no existing match found in canonical store"
    }}
  ],
  "attributes": {{}},
  "associations": [],
  "metadata": {{"needs_review": false, "confidence": 0.95}}
}}
```

### Update alias map:

**CRITICAL:** For EVERY entity you process (both matched and new), update the alias map:

1. Read `knowledge-graph/canonical/alias-map.json`
2. Normalize the entity name (lowercase, strip, collapse spaces)
3. Add mapping: `by_type["{entity_type}"][normalized_name] = canonical_id`
4. Update `last_updated` timestamp
5. Write the file back

**Normalization example:**
- "Denver Broncos" → "denver broncos"
- "  AFC West  " → "afc west"
- "The    Chiefs" → "the chiefs"

**Example alias-map.json after processing:**
```json
{{
  "version": 1,
  "last_updated": "2026-01-09T15:30:00Z",
  "by_type": {{
    "Person": {{
      "sean payton": "sean-payton",
      "john doe": "john-doe"
    }},
    "Organization": {{
      "denver broncos": "denver-broncos",
      "broncos": "denver-broncos",
      "kansas city chiefs": "kansas-city-chiefs"
    }},
    "Concept": {{
      "afc west": "afc-west"
    }}
  }}
}}
```

## Completion Checklist

When you have finished processing all entities:

1. **Verify all entities processed:**
   - [ ] Every entity in the table above has been either matched or created
   - [ ] Alias map updated for every entity
   - [ ] All changes committed via `CanonicalStorage` methods

2. **Create a Pull Request**:
   - Title: "Synthesis: Resolved {entity_type} entities (Batch {batch_number})"
   - Description: "Fixes #{{issue_number}}"

3. **Add a summary comment** to this issue:
   ```
   ## Synthesis Complete
   
   - **Matched to existing:** X entities
   - **Created new:** Y entities  
   - **Flagged for review:** Z entities (ambiguous)
   
   See PR #{{pr_number}} for details.
   ```

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


def run_batch_cli(args: argparse.Namespace) -> int:
    """Run LLM-driven entity resolution batch.
    
    Exit codes:
        0 - Success (all entities processed)
        1 - Error (unexpected failure)
        42 - Rate limited (partial progress saved)
    """
    from datetime import datetime, timezone
    
    from pathlib import Path
    
    from src.integrations.github.models import GitHubModelsClient, RateLimitError
    from src.integrations.github.pull_requests import create_pull_request
    from src.integrations.github.storage import GitHubStorageClient
    from src.orchestration.agent import AgentRuntime, MissionEvaluator, EvaluationResult
    from src.orchestration.llm import LLMPlanner
    from src.orchestration.missions import load_mission, Mission
    from src.orchestration.safety import SafetyValidator
    from src.orchestration.tools import ToolRegistry
    from src.orchestration.toolkit.github import register_github_mutation_tools, register_github_read_only_tools, register_github_pr_tools
    from src.orchestration.toolkit.synthesis import register_synthesis_tools
    from src.orchestration.types import ExecutionContext, MissionStatus, AgentStep
    
    class SimpleEvaluator(MissionEvaluator):
        """Simple evaluator for synthesis missions."""
        
        def evaluate(self, mission: Mission, steps: Sequence[AgentStep], context: ExecutionContext) -> EvaluationResult:
            """Validate mission success based on successful tool executions."""
            successful_steps = [s for s in steps if s.result and s.result.success]
            if successful_steps:
                summary = f"Successfully executed {len(successful_steps)} action(s)"
                return EvaluationResult(complete=True, reason=summary)
            return EvaluationResult(complete=False, reason="No successful actions completed")
    
    EXIT_SUCCESS = 0
    EXIT_ERROR = 1
    EXIT_RATE_LIMITED = 42
    
    entity_type = args.entity_type
    batch_size = args.batch_size
    model_name = args.model
    
    try:
        repository = resolve_repository(args.repository)
        token = resolve_token(args.token)
    except Exception as e:
        print(f"Error resolving repository or token: {e}", file=sys.stderr)
        return EXIT_ERROR
    
    # Generate branch name if not provided
    if args.branch_name:
        branch_name = args.branch_name
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        branch_name = f"synthesis/{entity_type.lower()}-{timestamp}"
    
    print(f"🔄 Starting synthesis batch for {entity_type}")
    print(f"   Model: {model_name}")
    print(f"   Batch size: {batch_size}")
    print(f"   Branch: {branch_name}")
    
    # Discover pending entities
    print(f"\n📊 Discovering pending {entity_type} entities...")
    kg_root = get_knowledge_graph_root()
    kg_storage = KnowledgeGraphStorage(root=kg_root)
    canonical_storage = CanonicalStorage(root=kg_root / "canonical")
    
    unresolved = _gather_unresolved_entities(entity_type, kg_storage, canonical_storage)
    
    print(f"   Found {len(unresolved)} unresolved {entity_type} entities")
    
    if not unresolved:
        print(f"   ✓ All {entity_type} entities are already resolved")
        print(f"   Nothing to process")
        return EXIT_SUCCESS
    
    # Show sample of entities
    sample_size = min(5, len(unresolved))
    print(f"   Sample entities to process:")
    for name, checksum in unresolved[:sample_size]:
        print(f"     • {name} (from {checksum[:12]}...)")
    if len(unresolved) > sample_size:
        print(f"     ... and {len(unresolved) - sample_size} more")
    
    # Determine actual batch size
    actual_batch = min(batch_size, len(unresolved))
    print(f"   Processing batch of {actual_batch} entities")
    
    try:
        # Initialize components
        models_client = GitHubModelsClient(api_key=token, model=model_name)
        
        # Load mission configuration
        mission_path = Path("config/missions/synthesize_batch.yaml")
        mission = load_mission(mission_path)
        
        # Prepare execution context with inputs
        inputs = {
            "entity_type": entity_type,
            "batch_size": batch_size,
            "branch_name": branch_name,
            "repository": repository,
        }
        context = ExecutionContext(inputs=inputs)
        
        # Register tools
        registry = ToolRegistry()
        register_github_read_only_tools(registry)
        register_github_mutation_tools(registry)
        register_github_pr_tools(registry)
        register_synthesis_tools(registry)
        
        # Create planner and agent
        planner = LLMPlanner(
            models_client=models_client,
            tool_registry=registry,
            max_tokens=4000,
            temperature=0.7,
        )
        
        validator = SafetyValidator()
        evaluator = SimpleEvaluator()
        
        runtime = AgentRuntime(
            planner=planner,
            tools=registry,
            safety=validator,
            evaluator=evaluator,
        )
        
        print(f"\n🤖 Running synthesis agent...")
        print(f"   Mission: {mission.name}")
        print(f"   Max steps: {mission.constraints.get('max_steps', 'unlimited')}")
        print(f"")
        
        # Execute the mission with the context we created earlier
        outcome = runtime.execute_mission(mission, context)
        
        if outcome.status == MissionStatus.SUCCEEDED:
            print(f"\n✅ Synthesis batch completed successfully")
            print(f"   Total steps: {len(outcome.steps)}")
            
            # Count action types
            action_counts = {}
            for step in outcome.steps:
                if step.result:
                    action_name = step.result.tool_name if hasattr(step.result, 'tool_name') else 'unknown'
                    action_counts[action_name] = action_counts.get(action_name, 0) + 1
            
            if action_counts:
                print(f"   Actions executed:")
                for action, count in sorted(action_counts.items()):
                    print(f"     • {action}: {count}")
            
            # Show summary if available
            if outcome.summary:
                print(f"   Summary: {outcome.summary}")
            
            return EXIT_SUCCESS
        else:
            print(f"\n❌ Synthesis batch failed: {outcome.status.value}")
            print(f"   Total steps attempted: {len(outcome.steps)}")
            
            # Show failed steps
            failed_steps = [s for s in outcome.steps if s.result and not s.result.success]
            if failed_steps:
                print(f"   Failed actions: {len(failed_steps)}")
                for step in failed_steps[:3]:  # Show first 3 failures
                    if step.result:
                        tool_name = step.result.tool_name if hasattr(step.result, 'tool_name') else 'unknown'
                        error_msg = step.result.error if hasattr(step.result, 'error') else 'unknown error'
                        print(f"     • {tool_name}: {error_msg}")
            
            if outcome.summary:
                print(f"   Summary: {outcome.summary}")
            return EXIT_ERROR
            
    except RateLimitError as e:
        print(f"\n⏸️  Rate limit hit: {e}", file=sys.stderr)
        print(f"   Partial progress has been saved")
        print(f"   Workflow will retry automatically")
        return EXIT_RATE_LIMITED
        
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return EXIT_ERROR


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
