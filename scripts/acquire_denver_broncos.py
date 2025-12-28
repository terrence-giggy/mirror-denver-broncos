#!/usr/bin/env python3
"""
Script to acquire content from the Denver Broncos official website.

This script demonstrates the acquisition workflow:
1. Fetch content from the source URL (requires network access or MCP tools)
2. Parse the content using the existing web parser  
3. Store parsed content in evidence/parsed/
4. Update the source registry with the content hash
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

from src.parsing.runner import parse_single_target
from src.parsing.storage import ParseStorage
from src.knowledge.storage import SourceRegistry
from src.integrations.github.storage import get_github_storage_client
from src import paths


def main() -> int:
    """Execute the acquisition workflow for Denver Broncos website."""
    
    source_url = "https://www.denverbroncos.com"
    
    print(f"=" * 70)
    print("Denver Broncos Content Acquisition")
    print(f"=" * 70)
    print(f"Source URL: {source_url}")
    print()
    
    # Initialize GitHub storage client if available
    github_client = get_github_storage_client()
    if github_client:
        print("✓ GitHub storage client initialized (running in Actions)")
    else:
        print("ℹ No GitHub storage client (running locally)")
    print()
    
    # Initialize storage
    storage = ParseStorage(
        root=paths.get_evidence_root() / "parsed",
        github_client=github_client,
        project_root=paths.get_data_root(),
    )
    print(f"Storage root: {storage.root}")
    print(f"Manifest path: {storage.manifest_path}")
    print()
    
    # Initialize source registry
    registry = SourceRegistry(
        root=paths.get_knowledge_graph_root(),
        github_client=github_client,
        project_root=paths.get_data_root(),
    )
    
    # Verify source exists in registry
    source = registry.get_source(source_url)
    if not source:
        print(f"✗ ERROR: Source '{source_url}' not found in registry!")
        return 1
    
    print(f"✓ Source found in registry: {source.name}")
    print(f"  Type: {source.source_type}")
    print(f"  Status: {source.status}")
    print(f"  Current content hash: {source.last_content_hash or 'None'}")
    print()
    
    # Fetch and parse content
    print("Fetching and parsing content...")
    print()
    
    try:
        result = parse_single_target(
            source_url,
            storage=storage,
            is_remote=True,
        )
        
        print(f"Parse Status: {result.status}")
        print(f"Parser Used: {result.parser}")
        print(f"Content Checksum: {result.checksum}")
        print(f"Artifact Path: {result.artifact_path}")
        
        if result.warnings:
            print(f"Warnings ({len(result.warnings)}):")
            for warning in result.warnings:
                print(f"  - {warning}")
        
        if result.error:
            print(f"✗ Error: {result.error}")
            return 1
        
        if not result.succeeded:
            print(f"✗ Parsing did not succeed (status: {result.status})")
            return 1
        
        print(f"✓ Content parsed successfully!")
        print()
        
        # Update source registry with content hash
        if result.checksum:
            print(f"Updating source registry with content hash...")
            source.last_content_hash = result.checksum
            from datetime import datetime, timezone
            source.last_checked = datetime.now(timezone.utc)
            source.check_failures = 0
            registry.save_source(source)
            print(f"✓ Source registry updated!")
            print()
        
        # Summary
        print(f"=" * 70)
        print("Acquisition Summary")
        print(f"=" * 70)
        print(f"Source: {source.name}")
        print(f"URL: {source_url}")
        print(f"Content Hash: {result.checksum}")
        print(f"Artifact Path: {result.artifact_path}")
        print(f"Status: {result.status}")
        print(f"=" * 70)
        
        return 0
        
    except Exception as e:
        print(f"✗ Unexpected error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
