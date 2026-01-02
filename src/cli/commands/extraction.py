
"""CLI commands for person extraction."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.integrations.copilot import CopilotClient, CopilotClientError
from src.integrations.github.storage import get_github_storage_client
from src.knowledge.extraction import (
    AssociationExtractor,
    ConceptExtractor,
    OrganizationExtractor,
    PersonExtractor,
    ProfileExtractor,
    process_document,
    process_document_associations,
    process_document_concepts,
    process_document_organizations,
    process_document_profiles,
)
from src.knowledge.storage import KnowledgeGraphStorage
from src.parsing.config import load_parsing_config
from src.parsing.storage import ParseStorage


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add extraction-focused subcommands to the main CLI parser."""
    parser = subparsers.add_parser(
        "extract",
        description="Extract entities from parsed documents.",
        help="Extract entities from parsed documents.",
    )
    parser.add_argument(
        "--checksum",
        type=str,
        help="Extract from a specific document by checksum.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of documents to process.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess documents even if already extracted.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be extracted without saving.",
    )
    parser.add_argument(
        "--kb-root",
        type=Path,
        help="Root directory for the knowledge graph.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="Path to parsing configuration file.",
    )
    parser.add_argument(
        "--orgs",
        "--organizations",
        dest="extract_orgs",
        action="store_true",
        help="Extract organizations instead of people.",
    )
    parser.add_argument(
        "--concepts",
        action="store_true",
        help="Extract concepts instead of people or organizations.",
    )
    parser.add_argument(
        "--associations",
        dest="extract_associations",
        action="store_true",
        help="Extract associations between people and organizations.",
    )
    parser.add_argument(
        "--profiles",
        action="store_true",
        help="Extract detailed profiles for entities.",
    )
    parser.set_defaults(func=extract_cli, command="extract")


def extract_cli(args: argparse.Namespace) -> int:
    """Execute the extraction workflow."""
    
    # Initialize components
    try:
        config = load_parsing_config(args.config)
        storage = ParseStorage(config.output_root)
        
        # Get GitHub storage client if running in GitHub Actions
        # (returns None when running locally, allowing local file writes)
        github_client = get_github_storage_client()
        kb_storage = KnowledgeGraphStorage(root=args.kb_root, github_client=github_client)
        
        # Initialize Copilot client
        # This will raise if token is missing
        client = CopilotClient()
        
        if args.extract_orgs:
            extractor = OrganizationExtractor(client)
            process_func = process_document_organizations
            entity_type = "organizations"
        elif args.concepts:
            extractor = ConceptExtractor(client)
            process_func = process_document_concepts
            entity_type = "concepts"
        elif args.extract_associations:
            extractor = AssociationExtractor(client)
            process_func = process_document_associations
            entity_type = "associations"
        elif args.profiles:
            extractor = ProfileExtractor(client)
            process_func = process_document_profiles
            entity_type = "profiles"
        else:
            extractor = PersonExtractor(client)
            process_func = process_document
            entity_type = "people"
        
    except (FileNotFoundError, ValueError, CopilotClientError) as exc:
        print(f"Initialization error: {exc}", file=sys.stderr)
        return 1

    # Find candidates
    manifest = storage.manifest()
    candidates = []
    
    # If checksum is specified, only process that document
    if args.checksum:
        if args.checksum not in manifest.entries:
            print(f"Error: Document with checksum {args.checksum} not found in manifest.", file=sys.stderr)
            return 1
        
        entry = manifest.entries[args.checksum]
        if entry.status != "completed":
            print(f"Error: Document {args.checksum} has status '{entry.status}', not 'completed'.", file=sys.stderr)
            return 1
        
        candidates = [entry]
    else:
        # Process all eligible documents
        for checksum, entry in manifest.entries.items():
            if entry.status != "completed":
                continue
                
            candidates.append(entry)
    
    # Filter out already extracted (unless force mode or specific checksum)
    if not args.force and not args.checksum:
        filtered_candidates = []
        for entry in candidates:
            if args.extract_orgs:
                existing = kb_storage.get_extracted_organizations(entry.checksum)
            elif args.concepts:
                existing = kb_storage.get_extracted_concepts(entry.checksum)
            elif args.extract_associations:
                existing = kb_storage.get_extracted_associations(entry.checksum)
            elif args.profiles:
                existing = kb_storage.get_extracted_profiles(entry.checksum)
            else:
                existing = kb_storage.get_extracted_people(entry.checksum)
                
            if not existing:
                filtered_candidates.append(entry)
        
        candidates = filtered_candidates

    if not candidates:
        print(f"No documents found needing {entity_type} extraction.")
        return 0

    print(f"Found {len(candidates)} documents to process for {entity_type}.")
    
    # Apply limit
    if args.limit:
        candidates = candidates[:args.limit]
        print(f"Limiting to {len(candidates)} documents.")

    success_count = 0
    fail_count = 0

    for entry in candidates:
        print(f"Processing {entry.source} ({entry.checksum[:8]})...")
        
        if args.dry_run:
            print(f"  (dry run) would extract {entity_type}")
            continue

        try:
            entities = process_func(entry, storage, kb_storage, extractor)
            preview = [str(e) for e in entities[:5]]
            print(f"  Extracted {len(entities)} {entity_type}: {', '.join(preview)}{'...' if len(entities) > 5 else ''}")
            success_count += 1
        except Exception as exc:
            print(f"  Failed: {exc}", file=sys.stderr)
            fail_count += 1

    print(f"\nExtraction complete. Success: {success_count}, Failed: {fail_count}")
    return 1 if fail_count > 0 else 0
