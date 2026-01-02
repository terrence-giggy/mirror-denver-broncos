"""CLI commands for extraction queue management."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from src.integrations.github.issues import (
    GitHubIssueError,
    IssueOutcome,
    create_issue,
    resolve_repository,
    resolve_token,
)
from src.integrations.github.search_issues import GitHubIssueSearcher, IssueSearchResult
from src.parsing.storage import Manifest, ManifestEntry, ParseStorage
from src.paths import get_evidence_root

if TYPE_CHECKING:
    pass


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add extraction queue subcommands to the main CLI parser."""
    parser = subparsers.add_parser(
        "extraction",
        description="Manage the extraction queue.",
        help="Manage the extraction queue.",
    )
    
    sub = parser.add_subparsers(dest="extraction_command", help="Extraction queue operation")
    
    # Queue command - create issues for pending documents
    queue_parser = sub.add_parser(
        "queue",
        description="Create GitHub Issues for documents needing extraction.",
        help="Create GitHub Issues for documents needing extraction.",
    )
    queue_parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    queue_parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    queue_parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Root directory for evidence. Defaults to evidence/.",
    )
    queue_parser.add_argument(
        "--force",
        action="store_true",
        help="Create issues even if they already exist (re-queue).",
    )
    queue_parser.add_argument(
        "--checksum",
        type=str,
        help="Only queue the document with this checksum.",
    )
    queue_parser.set_defaults(func=queue_cli)
    
    # Status command - show queue status
    status_parser = sub.add_parser(
        "status",
        description="Show extraction queue status.",
        help="Show extraction queue status.",
    )
    status_parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    status_parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    status_parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Root directory for evidence. Defaults to evidence/.",
    )
    status_parser.set_defaults(func=status_cli)
    
    # Pending command - list pending documents
    pending_parser = sub.add_parser(
        "pending",
        description="List documents needing extraction Issues.",
        help="List documents needing extraction Issues.",
    )
    pending_parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    pending_parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    pending_parser.add_argument(
        "--evidence-root",
        type=Path,
        default=None,
        help="Root directory for evidence. Defaults to evidence/.",
    )
    pending_parser.set_defaults(func=pending_cli)


def _parse_checksum_from_issue_body(body: str | None) -> str | None:
    """Extract checksum from Issue body using marker comment.
    
    Looks for: <!-- checksum:abc123 -->
    """
    if not body:
        return None
    match = re.search(r'<!-- checksum:(\w+) -->', body)
    if match:
        return match.group(1)
    return None


def get_documents_needing_issues(
    manifest: Manifest,
    existing_issues: list[IssueSearchResult],
    *,
    force: bool = False,
    specific_checksum: str | None = None,
) -> list[ManifestEntry]:
    """Find documents that don't have extraction Issues yet.
    
    Args:
        manifest: Parse manifest with all documents.
        existing_issues: List of existing extraction-queue issues.
        force: If True, return documents even if they have existing Issues.
        specific_checksum: If provided, only return this document.
    
    Returns:
        List of ManifestEntry objects needing Issues.
    """
    if not force:
        # Build set of checksums that already have issues
        existing_checksums = set()
        for issue in existing_issues:
            # Need to fetch full issue body to parse checksum
            # For now, we'll use a simple heuristic: parse from title or body preview
            # In production, we'd fetch full issue details
            checksum = _parse_checksum_from_issue_body(issue.title)
            if checksum:
                existing_checksums.add(checksum)
    else:
        existing_checksums = set()
    
    candidates = []
    for checksum, entry in manifest.entries.items():
        # Only queue completed documents
        if entry.status != "completed":
            continue
        
        # If specific checksum requested, only include that one
        if specific_checksum and checksum != specific_checksum:
            continue
        
        # Skip if already has an issue (unless force mode)
        if checksum in existing_checksums:
            continue
        
        candidates.append(entry)
    
    return candidates


def _create_extraction_issue(
    entry: ManifestEntry,
    *,
    token: str,
    repository: str,
) -> IssueOutcome:
    """Create a GitHub Issue for a document needing extraction.
    
    Args:
        entry: Manifest entry for the document.
        token: GitHub token.
        repository: Repository in owner/repo format.
    
    Returns:
        IssueOutcome with issue number and URL.
    """
    # Extract source name from metadata or use checksum
    source_name = entry.metadata.get("source_name", entry.source)
    
    # Extract page count if available
    page_count = entry.metadata.get("page_count", "Unknown")
    
    # Format processed_at timestamp
    processed_at = entry.processed_at.strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Build issue title
    title = f"Extract: {source_name}"
    
    # Build issue body
    body = f"""## Document to Extract

**Checksum:** `{entry.checksum}`
**Source:** {source_name}
**Artifact Path:** `{entry.artifact_path}`
**Parsed At:** {processed_at}
**Page Count:** {page_count}

<!-- checksum:{entry.checksum} -->

## Extraction Instructions

@copilot Please process this document:

1. **Assess** - Read the document and determine if it contains substantive content
   - Skip if: navigation page, error page, boilerplate, or duplicate content
   - If skipping: Comment with reason and close with "skipped" label

2. **Extract** (if substantive) - Run extractions in order:
   ```bash
   python main.py extract --checksum {entry.checksum}
   python main.py extract --checksum {entry.checksum} --orgs
   python main.py extract --checksum {entry.checksum} --concepts
   python main.py extract --checksum {entry.checksum} --associations
   ```

3. **Commit** - Save changes to knowledge-graph/

4. **Report** - Comment with summary of extracted entities

---
<!-- copilot:extraction-queue -->
"""
    
    return create_issue(
        token=token,
        repository=repository,
        title=title,
        body=body,
        labels=["extraction-queue", "copilot-queue"],
    )


def queue_documents_for_extraction(
    *,
    repository: str,
    token: str,
    evidence_root: Path,
    force: bool = False,
    specific_checksum: str | None = None,
) -> list[IssueOutcome]:
    """Create GitHub Issues for documents needing extraction.
    
    Args:
        repository: GitHub repository in owner/repo format.
        token: GitHub token.
        evidence_root: Root directory for evidence.
        force: If True, create Issues even for documents that already have them.
        specific_checksum: If provided, only queue this document.
    
    Returns:
        List of created IssueOutcome objects.
    """
    # Load manifest
    storage = ParseStorage(evidence_root / "parsed")
    manifest = storage.manifest()
    
    if not manifest.entries:
        print("No documents found in manifest.", file=sys.stderr)
        return []
    
    # Search for existing extraction-queue issues
    searcher = GitHubIssueSearcher(token=token, repository=repository)
    try:
        existing_issues = searcher.search_by_label("extraction-queue", limit=1000)
    except GitHubIssueError as exc:
        print(f"Warning: Could not search existing issues: {exc}", file=sys.stderr)
        existing_issues = []
    
    # Find documents needing issues
    candidates = get_documents_needing_issues(
        manifest,
        existing_issues,
        force=force,
        specific_checksum=specific_checksum,
    )
    
    if not candidates:
        if specific_checksum:
            print(f"No document found with checksum: {specific_checksum}", file=sys.stderr)
        else:
            print("No documents need extraction Issues.")
        return []
    
    # Create issues
    created_issues = []
    for entry in candidates:
        try:
            outcome = _create_extraction_issue(
                entry,
                token=token,
                repository=repository,
            )
            created_issues.append(outcome)
            print(f"Created Issue #{outcome.number}: {entry.checksum[:8]}... ({entry.source})")
        except GitHubIssueError as exc:
            print(f"Failed to create issue for {entry.checksum}: {exc}", file=sys.stderr)
    
    return created_issues


def queue_cli(args: argparse.Namespace) -> int:
    """Execute the queue command."""
    try:
        repository = resolve_repository(args.repository)
        token = resolve_token(args.token)
        
        # Use default evidence root if not provided
        evidence_root = args.evidence_root or get_evidence_root()
        
        created_issues = queue_documents_for_extraction(
            repository=repository,
            token=token,
            evidence_root=evidence_root,
            force=args.force,
            specific_checksum=args.checksum,
        )
        
        if created_issues:
            print(f"\nCreated {len(created_issues)} extraction Issue(s).")
        
        return 0
    except (GitHubIssueError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def status_cli(args: argparse.Namespace) -> int:
    """Execute the status command."""
    try:
        repository = resolve_repository(args.repository)
        token = resolve_token(args.token)
        
        # Use default evidence root if not provided
        evidence_root = args.evidence_root or get_evidence_root()
        
        # Load manifest
        storage = ParseStorage(evidence_root / "parsed")
        manifest = storage.manifest()
        
        # Search for extraction-queue issues
        searcher = GitHubIssueSearcher(token=token, repository=repository)
        all_issues = searcher.search_by_label("extraction-queue", limit=1000)
        
        # Count by state
        open_issues = [i for i in all_issues if i.state == "open"]
        closed_issues = [i for i in all_issues if i.state == "closed"]
        
        # Find documents needing issues
        candidates = get_documents_needing_issues(manifest, all_issues, force=False)
        
        # Display status
        print("Extraction Queue Status")
        print("=" * 40)
        print(f"Total documents in manifest: {len(manifest.entries)}")
        print(f"Documents with Issues: {len(all_issues)}")
        print(f"  - Open: {len(open_issues)}")
        print(f"  - Closed: {len(closed_issues)}")
        print(f"Documents needing Issues: {len(candidates)}")
        
        return 0
    except (GitHubIssueError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def pending_cli(args: argparse.Namespace) -> int:
    """Execute the pending command."""
    try:
        repository = resolve_repository(args.repository)
        token = resolve_token(args.token)
        
        # Use default evidence root if not provided
        evidence_root = args.evidence_root or get_evidence_root()
        
        # Load manifest
        storage = ParseStorage(evidence_root / "parsed")
        manifest = storage.manifest()
        
        # Search for extraction-queue issues
        searcher = GitHubIssueSearcher(token=token, repository=repository)
        all_issues = searcher.search_by_label("extraction-queue", limit=1000)
        
        # Find documents needing issues
        candidates = get_documents_needing_issues(manifest, all_issues, force=False)
        
        if not candidates:
            print("No pending documents.")
            return 0
        
        print(f"Pending Documents ({len(candidates)}):")
        print("=" * 40)
        for entry in candidates:
            source_name = entry.metadata.get("source_name", entry.source)
            print(f"  {entry.checksum[:8]}... - {source_name}")
            print(f"    Path: {entry.artifact_path}")
        
        return 0
    except (GitHubIssueError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
