#!/usr/bin/env python
"""Script to handle firewall-blocked acquisition issues."""

import os
import sys

from src.integrations.github.issues import (
    add_labels,
    post_comment,
    resolve_repository,
    resolve_token,
    update_issue,
)


def handle_firewall_block(issue_number: int, domain: str) -> None:
    """Handle a firewall-blocked acquisition by commenting, labeling, and closing."""
    
    # Get credentials from environment
    token = resolve_token(None)
    repository = resolve_repository(None)
    
    # Post comment
    comment_body = f"Blocked by firewall - domain `{domain}` not on allowlist"
    print(f"Posting comment: {comment_body}")
    post_comment(
        token=token,
        repository=repository,
        issue_number=issue_number,
        body=comment_body,
    )
    
    # Add label
    print("Adding label: blocked-by-firewall")
    add_labels(
        token=token,
        repository=repository,
        issue_number=issue_number,
        labels=["blocked-by-firewall"],
    )
    
    # Close the issue
    print("Closing issue")
    update_issue(
        token=token,
        repository=repository,
        issue_number=issue_number,
        state="closed",
    )
    
    print(f"Issue #{issue_number} has been marked as firewall-blocked and closed.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python handle_firewall_block.py <issue_number> <domain>")
        sys.exit(1)
    
    issue_num = int(sys.argv[1])
    domain_name = sys.argv[2]
    
    handle_firewall_block(issue_num, domain_name)
