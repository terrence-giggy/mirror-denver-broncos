"""Batch extraction processing for multiple documents in one workflow run."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any

from src.integrations.github.models import RateLimitError
from src.integrations.github.issues import (
    GitHubIssueError,
    resolve_repository,
    resolve_token,
)
from src.integrations.github.storage import get_github_storage_client
from src.orchestration.toolkit.extraction import ExtractionToolkit
from src.parsing.config import load_parsing_config
from src.parsing.storage import ParseStorage, ManifestEntry

logger = logging.getLogger(__name__)

# Exit codes
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_RATE_LIMITED = 42


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add extraction batch commands to the main CLI parser."""
    parser = subparsers.add_parser(
        "extraction-batch",
        description="Batch extraction processing for multiple documents.",
        help="Batch extraction processing for multiple documents.",
    )
    subparsers_batch = parser.add_subparsers(dest="extraction_batch_command", metavar="COMMAND")
    subparsers_batch.required = True
    
    # extraction-batch run command
    run_parser = subparsers_batch.add_parser(
        "run",
        description="Process N pending documents in a batch.",
        help="Process N pending documents in a batch.",
    )
    run_parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Maximum number of documents to process in this batch (default: 10).",
    )
    run_parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    run_parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    run_parser.set_defaults(func=extraction_batch_run_cli)
    
    # extraction-batch pending command
    pending_parser = subparsers_batch.add_parser(
        "pending",
        description="Count pending documents ready for extraction.",
        help="Count pending documents ready for extraction.",
    )
    pending_parser.add_argument(
        "--count-only",
        action="store_true",
        help="Output only the count as a single integer (for scripting).",
    )
    pending_parser.set_defaults(func=extraction_batch_pending_cli)


def get_pending_documents(storage: ParseStorage, limit: int = 10) -> list[ManifestEntry]:
    """Get documents ready for extraction.
    
    Returns documents where:
    - status == "completed" (parsing succeeded)
    - extraction_complete not set (not yet extracted)
    - extraction_skipped not set (not filtered as non-substantive)
    
    Args:
        storage: ParseStorage instance with loaded manifest.
        limit: Maximum number of documents to return.
        
    Returns:
        List of ManifestEntry objects ready for extraction.
    """
    pending = []
    for entry in storage.manifest().entries.values():
        # Must be successfully parsed
        if entry.status != "completed":
            continue
        
        # Skip if already extracted
        if entry.metadata.get("extraction_complete"):
            continue
        
        # Skip if marked non-substantive
        if entry.metadata.get("extraction_skipped"):
            continue
        
        pending.append(entry)
        
        if len(pending) >= limit:
            break
    
    return pending


def _format_extraction_stats(results: dict[str, Any]) -> str:
    """Format extraction statistics for logging."""
    people_count = 0
    orgs_count = 0
    concepts_count = 0
    assocs_count = 0
    
    # Extract counts from results
    if "people" in results and isinstance(results["people"], dict):
        people_count = results["people"].get("extracted_count", 0)
    
    if "organizations" in results and isinstance(results["organizations"], dict):
        orgs_count = results["organizations"].get("extracted_count", 0)
    
    if "concepts" in results and isinstance(results["concepts"], dict):
        concepts_count = results["concepts"].get("extracted_count", 0)
    
    if "associations" in results and isinstance(results["associations"], dict):
        assocs_count = results["associations"].get("extracted_count", 0)
    
    return f"People: {people_count}, Orgs: {orgs_count}, Concepts: {concepts_count}, Assocs: {assocs_count}"


def extract_batch(
    batch_size: int,
    repository: str,
    token: str,
) -> int:
    """
    Process extraction for multiple documents in a single run.
    
    Steps:
    1. Query manifest for pending documents (limit to batch_size)
    2. Ensure PR branch extraction/queue exists
    3. Begin batch mode (defer commits)
    4. For each document:
       - Assess document quality (gpt-4o-mini)
       - If not substantive: mark skipped, continue
       - If substantive: extract entities (4x gpt-4o calls)
       - Update manifest metadata
       - Handle rate limits by breaking loop and returning exit 42
    5. Flush all changes to PR branch in single commit
    6. Return appropriate exit code
    
    Returns:
        EXIT_SUCCESS (0): All documents processed successfully
        EXIT_RATE_LIMITED (42): Rate limited, partial progress saved
        EXIT_ERROR (1): Unexpected error occurred
    """
    logger.info(f"Starting batch extraction (batch_size={batch_size})")
    
    try:
        # Load parsing config and storage
        config = load_parsing_config(None)
        github_client = get_github_storage_client()
        storage = ParseStorage(config.output_root, github_client=github_client)
        
        # Query pending documents
        pending_docs = get_pending_documents(storage, limit=batch_size)
        
        if not pending_docs:
            logger.info("No pending documents found")
            return EXIT_SUCCESS
        
        logger.info(f"Found {len(pending_docs)} pending documents")
        
        # Initialize extraction toolkit
        toolkit = ExtractionToolkit()
        
        # Get workflow run ID if available
        run_id = os.environ.get("GITHUB_RUN_ID", "local")
        
        # Begin batch mode to defer commits
        storage.begin_batch()
        
        # Track documents processed in this batch
        processed_count = 0
        skipped_count = 0
        
        # Process each document
        for i, entry in enumerate(pending_docs, 1):
            checksum = entry.checksum
            source = entry.source
            logger.info(f"[{i}/{len(pending_docs)}] Processing document: {source} (checksum: {checksum[:12]}...)")
            
            try:
                # Step 1: Assess document quality
                logger.info(f"  Assessing document quality...")
                assessment = toolkit._assess_document({"checksum": checksum})
                
                if not isinstance(assessment, dict) or assessment.get("status") == "error":
                    error_msg = assessment.get("message", "Unknown error") if isinstance(assessment, dict) else str(assessment)
                    logger.error(f"  Assessment failed: {error_msg}")
                    # Mark with error metadata but continue to next document
                    entry.metadata["extraction_error"] = error_msg
                    entry.metadata["extraction_last_batch_run"] = run_id
                    storage.record_entry(entry)
                    continue
                
                is_substantive = assessment.get("is_substantive", False)
                reason = assessment.get("reason", "No reason provided")
                confidence = assessment.get("confidence", 0.0)
                
                logger.info(f"  Assessment: substantive={is_substantive}, confidence={confidence:.2f}")
                
                if not is_substantive:
                    # Mark as skipped
                    logger.info(f"  Skipping non-substantive document: {reason}")
                    entry.metadata["extraction_skipped"] = True
                    entry.metadata["extraction_skipped_reason"] = reason
                    entry.metadata["extraction_last_batch_run"] = run_id
                    storage.record_entry(entry)
                    skipped_count += 1
                    continue
                
                # Step 2: Extract entities
                logger.info(f"  Extracting entities...")
                results = {}
                
                # Extract people
                logger.info(f"    Extracting people...")
                people_result = toolkit._extract_people({"checksum": checksum})
                results["people"] = people_result
                
                # Extract organizations
                logger.info(f"    Extracting organizations...")
                orgs_result = toolkit._extract_organizations({"checksum": checksum})
                results["organizations"] = orgs_result
                
                # Extract concepts
                logger.info(f"    Extracting concepts...")
                concepts_result = toolkit._extract_concepts({"checksum": checksum})
                results["concepts"] = concepts_result
                
                # Extract associations
                logger.info(f"    Extracting associations...")
                assocs_result = toolkit._extract_associations({"checksum": checksum})
                results["associations"] = assocs_result
                
                # Step 3: Mark as complete
                logger.info(f"  Marking extraction as complete...")
                entry.metadata["extraction_complete"] = True
                entry.metadata["extraction_last_batch_run"] = run_id
                storage.record_entry(entry)
                processed_count += 1
                
                # Log stats
                stats = _format_extraction_stats(results)
                logger.info(f"  ✓ Extraction complete: {stats}")
                
            except RateLimitError as exc:
                # Rate limit hit - save progress so far and exit with code 42
                logger.warning(f"Rate limit encountered on document {checksum[:12]}...: {exc}")
                
                # Record rate limit timestamp in metadata
                entry.metadata["extraction_rate_limited_at"] = datetime.now(timezone.utc).isoformat()
                entry.metadata["extraction_last_batch_run"] = run_id
                storage.record_entry(entry)
                
                # Flush what we have so far
                logger.info(f"Flushing {processed_count + skipped_count} document updates before exiting...")
                storage.flush_all()
                
                logger.info(f"Batch processing paused due to rate limit. Processed: {processed_count}, Skipped: {skipped_count}")
                return EXIT_RATE_LIMITED
        
        # Step 4: Flush all changes in single commit
        logger.info(f"Flushing all changes ({processed_count + skipped_count} document updates)...")
        storage.flush_all()
        
        logger.info(f"Batch extraction complete. Processed: {processed_count}, Skipped: {skipped_count}")
        return EXIT_SUCCESS
        
    except GitHubIssueError as exc:
        logger.error(f"GitHub API error: {exc}")
        print(f"GitHub API error: {exc}", file=sys.stderr)
        return EXIT_ERROR
        
    except Exception as exc:
        logger.exception(f"Unexpected error during batch extraction: {exc}")
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def count_pending_documents() -> int:
    """Count pending documents ready for extraction.
    
    Returns:
        Number of pending documents.
    """
    config = load_parsing_config(None)
    storage = ParseStorage(config.output_root)
    pending_docs = get_pending_documents(storage, limit=10000)  # Get all
    return len(pending_docs)


def extraction_batch_run_cli(args: argparse.Namespace) -> int:
    """Execute the extraction-batch run command."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    try:
        repository = resolve_repository(args.repository)
        token = resolve_token(args.token)
        
        return extract_batch(
            batch_size=args.batch_size,
            repository=repository,
            token=token,
        )
        
    except (GitHubIssueError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR


def extraction_batch_pending_cli(args: argparse.Namespace) -> int:
    """Execute the extraction-batch pending command."""
    try:
        count = count_pending_documents()
        
        if args.count_only:
            print(count)
        else:
            print(f"Pending documents ready for extraction: {count}")
        
        return EXIT_SUCCESS
        
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_ERROR
