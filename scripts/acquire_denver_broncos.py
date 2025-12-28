#!/usr/bin/env python3
"""
Script to acquire content from Denver Broncos official website.

This script fetches and parses content from https://www.denverbroncos.com
using the existing parsing infrastructure, then updates the source registry
with the content hash.

Requirements:
- Network access to https://www.denverbroncos.com
- GitHub API token (for persistence in Actions)

Usage:
    python scripts/acquire_denver_broncos.py

Environment Variables:
    GITHUB_TOKEN    - GitHub API token for committing changes (optional, auto-detected in Actions)
    GITHUB_REPOSITORY - Repository in owner/repo format (optional, auto-detected in Actions)
"""

from pathlib import Path
from datetime import datetime, timezone
from src.parsing.runner import parse_single_target
from src.parsing.storage import ParseStorage
from src.knowledge.storage import SourceRegistry
from src.integrations.github.storage import get_github_storage_client
from src import paths


def main():
    """Execute the content acquisition workflow."""
    print("=" * 80)
    print("Denver Broncos Website Acquisition")
    print("=" * 80)
    
    # Get GitHub client for persistence (if running in Actions)
    github_client = get_github_storage_client()
    project_root = Path.cwd()
    
    if github_client:
        print(f"✓ Running in GitHub Actions - changes will be committed via API")
    else:
        print(f"✓ Running locally - changes will be saved to filesystem")
    
    # Setup storage with GitHub client
    evidence_root = paths.get_evidence_root()
    storage_root = evidence_root / "parsed"
    storage = ParseStorage(
        root=storage_root,
        github_client=github_client,
        project_root=project_root,
    )
    
    # Parse the Denver Broncos website
    source_url = "https://www.denverbroncos.com"
    print(f"\nFetching and parsing: {source_url}")
    print(f"This may take a moment...")
    
    result = parse_single_target(
        source_url,
        storage=storage,
        is_remote=True,
    )
    
    # Display result
    print(f"\n{'=' * 80}")
    print(f"Parsing Result")
    print(f"{'=' * 80}")
    print(f"  Source:         {result.source}")
    print(f"  Parser:         {result.parser}")
    print(f"  Status:         {result.status}")
    print(f"  Checksum:       {result.checksum}")
    if result.artifact_path:
        print(f"  Artifact Path:  {result.artifact_path}")
    
    if result.warnings:
        print(f"\nWarnings:")
        for warning in result.warnings:
            print(f"  ⚠ {warning}")
    
    if result.error:
        print(f"\n✗ Error: {result.error}")
        return 1
    
    if not result.succeeded:
        print(f"\n✗ Parsing did not succeed.")
        return 1
    
    # Update source registry with content hash
    print(f"\n{'=' * 80}")
    print(f"Updating Source Registry")
    print(f"{'=' * 80}")
    
    kg_root = paths.get_knowledge_graph_root()
    registry = SourceRegistry(
        root=kg_root,
        github_client=github_client,
        project_root=project_root,
    )
    
    source = registry.get_source(source_url)
    if source is None:
        print(f"✗ ERROR: Source {source_url} not found in registry!")
        print(f"  The source must be registered before acquisition.")
        return 1
    
    print(f"  Source Name:    {source.name}")
    print(f"  Source Type:    {source.source_type}")
    print(f"  Status:         {source.status}")
    
    # Update the content hash and last checked timestamp
    source.last_content_hash = result.checksum
    source.last_checked = datetime.now(timezone.utc)
    
    # Save the updated source
    registry.save_source(source)
    print(f"\n✓ Updated source registry:")
    print(f"  Content Hash:   {result.checksum}")
    print(f"  Last Checked:   {source.last_checked.isoformat()}")
    
    print(f"\n{'=' * 80}")
    print(f"✓ Content Acquisition Complete!")
    print(f"{'=' * 80}")
    print(f"\nNext Steps:")
    print(f"  1. Review parsed content in: {storage_root}")
    print(f"  2. Verify source registry update in: {kg_root}/sources/")
    print(f"  3. Close the acquisition issue with a success summary")
    
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
