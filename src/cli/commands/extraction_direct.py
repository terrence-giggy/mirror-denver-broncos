"""Direct extraction processing without Copilot agent wrapper."""

from __future__ import annotations

import argparse
import logging
import re
import sys
from typing import Any

from src.integrations.github.models import RateLimitError
from src.integrations.github.issues import (
    GitHubIssueError,
    add_labels,
    fetch_issue,
    post_comment,
    remove_label,
    resolve_repository,
    resolve_token,
    update_issue,
)
from src.orchestration.toolkit.extraction import ExtractionToolkit

logger = logging.getLogger(__name__)


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add extraction-direct command to the main CLI parser."""
    parser = subparsers.add_parser(
        "extraction-direct",
        description="Process extraction issue directly (without Copilot agent).",
        help="Process extraction issue directly (without Copilot agent).",
    )
    parser.add_argument(
        "--issue-number",
        type=int,
        required=True,
        help="GitHub Issue number to process.",
    )
    parser.add_argument(
        "--repository",
        type=str,
        help="GitHub repository in owner/repo format. Defaults to GITHUB_REPOSITORY env var or git remote.",
    )
    parser.add_argument(
        "--token",
        type=str,
        help="GitHub token. Defaults to GH_TOKEN or GITHUB_TOKEN env var.",
    )
    parser.set_defaults(func=extraction_direct_cli)


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


def _format_extraction_stats(results: dict[str, Any]) -> str:
    """Format extraction statistics for posting as a comment."""
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
    
    return f"""## ✅ Extraction Complete

Successfully extracted entities from the document:

- **People:** {people_count}
- **Organizations:** {orgs_count}
- **Concepts:** {concepts_count}
- **Associations:** {assocs_count}

**Total Entities:** {people_count + orgs_count + concepts_count + assocs_count}

Changes have been committed to the knowledge graph.
"""


def extract_directly(
    issue_number: int,
    repository: str,
    token: str,
) -> int:
    """
    Process extraction for an issue directly using extraction tools.
    
    This replaces the Copilot agent assignment approach.
    
    Steps:
    1. Fetch issue details, extract checksum from body
    2. Use assess_document_value tool (LLM assessment)
    3. If not substantive: mark_extraction_skipped, comment, label, close
    4. If substantive: 
       - extract_people_from_document
       - extract_organizations_from_document  
       - extract_concepts_from_document
       - extract_associations_from_document
       - mark_extraction_complete
       - create_extraction_pull_request
       - Comment with stats, label, close
    
    Returns:
        0 on success, 1 on error, 2 on rate limit (for workflow detection)
    """
    logger.info(f"Processing extraction for issue #{issue_number}")
    
    try:
        # Step 1: Fetch issue and extract checksum
        logger.info("Fetching issue details...")
        issue = fetch_issue(token=token, repository=repository, issue_number=issue_number)
        
        checksum = _parse_checksum_from_issue_body(issue.get("body"))
        if not checksum:
            logger.error(f"Could not find checksum in issue #{issue_number}")
            post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body="❌ Error: Could not find checksum in issue body. Issue may be malformed.",
            )
            add_labels(
                token=token,
                repository=repository,
                issue_number=issue_number,
                labels=["extraction-error"],
            )
            return 1
        
        logger.info(f"Found checksum: {checksum}")
        
        # Initialize extraction toolkit
        logger.info("Initializing extraction toolkit...")
        toolkit = ExtractionToolkit()
        
        # Step 2: Assess document quality
        logger.info("Assessing document quality...")
        assessment = toolkit._assess_document({"checksum": checksum})
        
        if not isinstance(assessment, dict) or assessment.get("status") == "error":
            error_msg = assessment.get("message", "Unknown error") if isinstance(assessment, dict) else str(assessment)
            logger.error(f"Assessment failed: {error_msg}")
            post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body=f"❌ Error during document assessment: {error_msg}",
            )
            add_labels(
                token=token,
                repository=repository,
                issue_number=issue_number,
                labels=["extraction-error"],
            )
            return 1
        
        is_substantive = assessment.get("is_substantive", False)
        reason = assessment.get("reason", "No reason provided")
        confidence = assessment.get("confidence", 0.0)
        
        logger.info(f"Assessment result: substantive={is_substantive}, confidence={confidence:.2f}")
        logger.info(f"Reason: {reason}")
        
        # Step 3: Process based on assessment
        if not is_substantive:
            # Document is not substantive - skip extraction
            logger.info("Document not substantive - marking as skipped")
            
            skip_result = toolkit._mark_skipped({
                "checksum": checksum,
                "reason": reason,
            })
            
            if isinstance(skip_result, dict) and skip_result.get("status") == "error":
                logger.error(f"Failed to mark as skipped: {skip_result.get('message')}")
                return 1
            
            # Post comment
            post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body=f"""## ⏭️ Extraction Skipped

This document was assessed as not containing substantive, extractable content.

**Assessment Reasoning:** {reason}

**Confidence:** {confidence:.0%}

The document has been marked as `extraction_skipped` in the manifest to prevent re-queuing.
""",
            )
            
            # Add label and remove queue label
            remove_label(
                token=token,
                repository=repository,
                issue_number=issue_number,
                label="extraction-queue",
            )
            add_labels(
                token=token,
                repository=repository,
                issue_number=issue_number,
                labels=["extraction-skipped"],
            )
            
            # Close issue
            update_issue(
                token=token,
                repository=repository,
                issue_number=issue_number,
                state="closed",
            )
            
            logger.info("Successfully marked document as skipped and closed issue")
            return 0
        
        # Document is substantive - proceed with extraction
        logger.info("Document is substantive - proceeding with extraction")
        
        results = {}
        
        # Step 4: Extract entities
        logger.info("Extracting people...")
        people_result = toolkit._extract_people({"checksum": checksum})
        results["people"] = people_result
        
        if isinstance(people_result, dict) and people_result.get("status") == "success":
            logger.info(f"Extracted {people_result.get('extracted_count', 0)} people")
        else:
            logger.warning(f"People extraction result: {people_result}")
        
        logger.info("Extracting organizations...")
        orgs_result = toolkit._extract_organizations({"checksum": checksum})
        results["organizations"] = orgs_result
        
        if isinstance(orgs_result, dict) and orgs_result.get("status") == "success":
            logger.info(f"Extracted {orgs_result.get('extracted_count', 0)} organizations")
        else:
            logger.warning(f"Organizations extraction result: {orgs_result}")
        
        logger.info("Extracting concepts...")
        concepts_result = toolkit._extract_concepts({"checksum": checksum})
        results["concepts"] = concepts_result
        
        if isinstance(concepts_result, dict) and concepts_result.get("status") == "success":
            logger.info(f"Extracted {concepts_result.get('extracted_count', 0)} concepts")
        else:
            logger.warning(f"Concepts extraction result: {concepts_result}")
        
        logger.info("Extracting associations...")
        assocs_result = toolkit._extract_associations({"checksum": checksum})
        results["associations"] = assocs_result
        
        if isinstance(assocs_result, dict) and assocs_result.get("status") == "success":
            logger.info(f"Extracted {assocs_result.get('extracted_count', 0)} associations")
        else:
            logger.warning(f"Associations extraction result: {assocs_result}")
        
        # Step 5: Mark as complete
        logger.info("Marking extraction as complete...")
        complete_result = toolkit._mark_complete({"checksum": checksum})
        
        if isinstance(complete_result, dict) and complete_result.get("status") == "error":
            logger.error(f"Failed to mark as complete: {complete_result.get('message')}")
            return 1
        
        # Step 6: Create PR (if in GitHub Actions)
        logger.info("Creating pull request (if in GitHub Actions)...")
        pr_result = toolkit._create_pr({
            "branch_name": f"extraction/doc-{checksum[:8]}",
            "pr_title": f"Extract entities from {checksum[:8]}",
            "pr_body": f"""## Extraction Results

Automated entity extraction from document `{checksum}`.

{_format_extraction_stats(results)}

Closes #{issue_number}
""",
            "base_branch": "main",
        })
        
        if isinstance(pr_result, dict):
            if pr_result.get("status") == "success":
                logger.info(f"Created PR: {pr_result.get('pr_url')}")
            elif pr_result.get("status") == "skip":
                logger.info(f"Skipped PR creation: {pr_result.get('message')}")
            elif pr_result.get("status") == "error":
                # PR creation failed - this is a critical error in Actions context
                error_msg = pr_result.get('message', 'Unknown error')
                logger.error(f"PR creation failed: {error_msg}")
                post_comment(
                    token=token,
                    repository=repository,
                    issue_number=issue_number,
                    body=f"❌ Failed to create pull request: {error_msg}\n\nChanges may have been committed but PR creation failed.",
                )
                add_labels(
                    token=token,
                    repository=repository,
                    issue_number=issue_number,
                    labels=["extraction-error"],
                )
                return 1  # Fail the job
            else:
                logger.warning(f"Unexpected PR creation result: {pr_result}")
        
        # Step 7: Post completion comment
        comment_body = _format_extraction_stats(results)
        
        # Add PR link if created
        if isinstance(pr_result, dict) and pr_result.get("status") == "success":
            comment_body += f"\n\n**Pull Request:** #{pr_result.get('pr_number')}"
        
        post_comment(
            token=token,
            repository=repository,
            issue_number=issue_number,
            body=comment_body,
        )
        
        # Step 8: Add label and remove queue label
        remove_label(
            token=token,
            repository=repository,
            issue_number=issue_number,
            label="extraction-queue",
        )
        add_labels(
            token=token,
            repository=repository,
            issue_number=issue_number,
            labels=["extraction-complete"],
        )
        
        # Step 9: Close issue
        update_issue(
            token=token,
            repository=repository,
            issue_number=issue_number,
            state="closed",
        )
        
        logger.info("Extraction completed successfully")
        return 0
        
    except RateLimitError as exc:
        # Rate limit hit - swap label and post comment
        logger.warning(f"Rate limit encountered: {exc}")
        
        try:
            # Swap labels: extraction-queue → extraction-rate-limited
            remove_label(
                token=token,
                repository=repository,
                issue_number=issue_number,
                label="extraction-queue",
            )
            add_labels(
                token=token,
                repository=repository,
                issue_number=issue_number,
                labels=["extraction-rate-limited"],
            )
            
            post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body="⏸️ Rate limit encountered. Will retry automatically in 30 minutes.",
            )
            logger.info("Successfully updated labels and posted rate limit comment")
        except Exception as comment_exc:
            logger.warning(f"Failed to update labels/post comment: {comment_exc}")
        
        return 0  # Return success since we handled it gracefully
        
    except GitHubIssueError as exc:
        logger.error(f"GitHub API error: {exc}")
        print(f"GitHub API error: {exc}", file=sys.stderr)
        return 1
        
    except Exception as exc:
        logger.exception(f"Unexpected error during extraction: {exc}")
        
        try:
            # Swap labels: extraction-queue → extraction-error
            remove_label(
                token=token,
                repository=repository,
                issue_number=issue_number,
                label="extraction-queue",
            )
            add_labels(
                token=token,
                repository=repository,
                issue_number=issue_number,
                labels=["extraction-error"],
            )
            
            post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body=f"❌ Extraction failed. Check workflow logs for details.",
            )
            logger.info("Successfully updated labels and posted error comment")
        except Exception as comment_exc:
            logger.warning(f"Failed to update labels/post comment: {comment_exc}")
        
        return 1


def extraction_direct_cli(args: argparse.Namespace) -> int:
    """Execute the extraction-direct command."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    
    try:
        repository = resolve_repository(args.repository)
        token = resolve_token(args.token)
        
        return extract_directly(
            issue_number=args.issue_number,
            repository=repository,
            token=token,
        )
        
    except (GitHubIssueError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
