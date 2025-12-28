#!/usr/bin/env python3
"""
Content Acquisition Script for Denver Broncos Official Website

This script performs the following steps:
1. Fetches and parses content from https://www.denverbroncos.com
2. Stores parsed content in evidence/parsed/
3. Updates the source registry with the content hash

NETWORK REQUIREMENTS:
    This script requires unrestricted network access to fetch content.
    It will fail in sandboxed environments where DNS resolution is blocked.
    
    Expected to run in:
    - GitHub Actions workflows (network available)
    - Local development environments (network available)
    
    Will fail in:
    - Copilot agent sandboxed environments (network blocked)

USAGE:
    python scripts/acquire_denver_broncos.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.parsing.runner import parse_single_target
from src.parsing.storage import ParseStorage
from src.knowledge.storage import SourceRegistry
from src.integrations.github.storage import get_github_storage_client
from src import paths

# Display configuration
SEPARATOR_WIDTH = 70


def main() -> int:
    """Execute content acquisition for Denver Broncos official website."""
    
    url = "https://www.denverbroncos.com"
    
    print("=" * SEPARATOR_WIDTH)
    print("Denver Broncos Content Acquisition")
    print("=" * SEPARATOR_WIDTH)
    print(f"Source URL: {url}")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print()
    
    # Detect execution environment
    github_client = get_github_storage_client()
    if github_client:
        print("✓ Running in GitHub Actions")
        print(f"  Repository: {github_client.repository}")
        print(f"  Branch: {github_client.branch}")
    else:
        print("✓ Running in local environment")
    print()
    
    # Initialize storage with GitHub client if available
    evidence_root = paths.get_evidence_root()
    storage = ParseStorage(
        root=evidence_root / "parsed",
        github_client=github_client,
        project_root=PROJECT_ROOT,
    )
    print(f"✓ Storage initialized: {storage.root}")
    print()
    
    # Step 1: Fetch and parse content
    print("-" * SEPARATOR_WIDTH)
    print("Step 1: Fetching and parsing content...")
    print("-" * SEPARATOR_WIDTH)
    
    try:
        result = parse_single_target(
            url,
            storage=storage,
            is_remote=True,
        )
    except Exception as e:
        print(f"✗ Fatal error during parsing: {e}")
        return 1
    
    # Report parsing results
    print(f"Parser: {result.parser}")
    print(f"Status: {result.status}")
    print(f"Checksum: {result.checksum}")
    
    if result.warnings:
        print(f"Warnings:")
        for warning in result.warnings:
            print(f"  - {warning}")
    
    if result.error:
        print(f"✗ Error: {result.error}")
        return 1
    
    if not result.succeeded:
        print(f"✗ Acquisition failed with status: {result.status}")
        if result.message:
            print(f"  Message: {result.message}")
        return 1
    
    print(f"✓ Content acquired successfully")
    print(f"  Artifact path: {result.artifact_path}")
    print()
    
    # Step 2: Update source registry
    print("-" * SEPARATOR_WIDTH)
    print("Step 2: Updating source registry...")
    print("-" * SEPARATOR_WIDTH)
    
    kg_root = paths.get_knowledge_graph_root()
    registry = SourceRegistry(
        root=kg_root,
        github_client=github_client,
        project_root=PROJECT_ROOT,
    )
    
    source = registry.get_source(url)
    if source is None:
        print(f"✗ Source not found in registry: {url}")
        print(f"  Please register the source first using:")
        print(f"  python -m main sources register {url}")
        return 1
    
    print(f"✓ Found source: {source.name}")
    print(f"  Type: {source.source_type}")
    print(f"  Status: {source.status}")
    print(f"  Previous hash: {source.last_content_hash or '(none)'}")
    
    # Update content hash and last_checked timestamp
    source.last_content_hash = result.checksum
    source.last_checked = datetime.now(timezone.utc)
    
    registry.save_source(source)
    print(f"✓ Registry updated")
    print(f"  New hash: {result.checksum}")
    print()
    
    # Summary
    print("=" * SEPARATOR_WIDTH)
    print("Acquisition Complete!")
    print("=" * SEPARATOR_WIDTH)
    print(f"Source: {url}")
    print(f"Checksum: {result.checksum}")
    print(f"Artifact: {result.artifact_path}")
    print(f"Registry: Updated")
    print()
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
