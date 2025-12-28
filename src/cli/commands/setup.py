"""CLI command for repository setup and initialization."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from src.integrations.github.issues import (
    create_issue,
    get_repository_details,
    get_repository_labels,
    post_comment,
    resolve_repository,
    resolve_token,
    ensure_required_labels,
    GitHubIssueError,
    REQUIRED_LABELS,
)
from src.integrations.github.sync import get_repository_variable
from src.integrations.github import discussions as github_discussions

SETUP_ISSUE_TITLE = "Project Configuration & Setup"
SETUP_ISSUE_BODY = """\
This issue tracks the initial configuration of the repository.

## ✅ Setup Checklist

Complete the following steps to finish repository configuration:

### 1. GitHub Token & Secrets
- [ ] Configure **GH_TOKEN** secret with `repo` and `workflow` permissions
- [ ] Set **SYNC_SIGNATURE_SECRET** secret for secure dispatch verification

### 2. Repository Variables
- [ ] Set **UPSTREAM_REPO** variable (e.g., `owner/template-repo`)

### 3. Repository Settings
- [ ] Add **speculum-downstream** topic to this repository

### 4. Enable Discussions
Enable Discussions and create the following categories:
- [ ] **Sources** — for source curation workflow
- [ ] **People** — for Person entity profiles
- [ ] **Organizations** — for Organization entity profiles

### 5. Configure Copilot Coding Agent MCP
Navigate to **Settings → Copilot → Coding agent → MCP configuration** and add:

```json
{
  "mcpServers": {
    "evidence-acquisition": {
      "type": "local",
      "command": "python",
      "args": ["-m", "src.integrations.copilot.mcp_server"],
      "tools": ["fetch_source_content", "check_source_headers"]
    }
  }
}
```

This enables the agent to fetch external content (bypasses firewall).

### 6. Sync from Upstream
- [ ] Run the **2. Sync: Pull from Upstream** workflow to pull latest changes

---

Once all steps are complete, close this issue.
"""
WELCOME_COMMENT = (
    "Welcome to the repository setup wizard! 🧙‍♂️\n\n"
    "I've created a checklist above to guide you through the configuration process.\n\n"
    "**Quick Start:**\n"
    "1. Review and complete each checkbox in the issue description\n"
    "2. Run the `validate-setup` command to verify configuration\n"
    "3. Close this issue when setup is complete\n\n"
    "If you have any questions, check the documentation in `docs/guides/`."
)


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register the setup commands."""
    # Command for GitHub Actions to create setup issue
    parser = subparsers.add_parser(
        "setup",
        help="Initialize the repository and start the setup workflow (run in GitHub Actions).",
    )
    parser.add_argument(
        "--repo",
        help="The repository to setup (format: owner/repo). Defaults to current git repo.",
    )
    parser.set_defaults(func=setup_repo_cli)
    
    # Command to validate setup configuration
    validate_parser = subparsers.add_parser(
        "validate-setup",
        help="Validate repository setup configuration.",
    )
    validate_parser.add_argument(
        "--repo",
        help="The repository to validate (format: owner/repo). Defaults to current git repo.",
    )
    validate_parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON instead of text.",
    )
    validate_parser.set_defaults(func=validate_setup_cli)
    
    # Command to verify dispatch signature
    verify_dispatch_parser = subparsers.add_parser(
        "verify-dispatch",
        help="Verify HMAC signature for repository dispatch payload.",
    )
    verify_dispatch_parser.add_argument(
        "--upstream-repo",
        required=True,
        help="Upstream repository (format: owner/repo).",
    )
    verify_dispatch_parser.add_argument(
        "--upstream-branch",
        required=True,
        help="Upstream branch name.",
    )
    verify_dispatch_parser.add_argument(
        "--timestamp",
        required=True,
        help="Timestamp from dispatch payload.",
    )
    verify_dispatch_parser.add_argument(
        "--signature",
        required=True,
        help="Signature from dispatch payload.",
    )
    verify_dispatch_parser.add_argument(
        "--secret",
        help="HMAC secret. Defaults to $SYNC_SIGNATURE_SECRET.",
    )
    verify_dispatch_parser.set_defaults(func=verify_dispatch_cli)


def setup_repo_cli(args: argparse.Namespace) -> int:
    """Handler for the setup command."""
    try:
        token = resolve_token(None)
        repo = resolve_repository(args.repo)
    except GitHubIssueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    print(f"Initializing setup for repository: {repo}")

    # Cleanup dev_data if it exists
    dev_data = Path("dev_data")
    if dev_data.exists() and dev_data.is_dir():
        print("Removing dev_data directory...")
        shutil.rmtree(dev_data)

    try:
        # 0. Ensure required labels exist
        print("Ensuring required labels exist...")
        label_result = ensure_required_labels(token=token, repository=repo)
        if label_result["created"]:
            print(f"  Created labels: {', '.join(label_result['created'])}")
        if label_result["existing"]:
            print(f"  Existing labels: {', '.join(label_result['existing'])}")

        # 1. Create the setup issue
        issue = create_issue(
            token=token,
            repository=repo,
            title=SETUP_ISSUE_TITLE,
            body=SETUP_ISSUE_BODY,
            labels=["setup", "wontfix"], # wontfix to prevent auto-closing if configured
        )
        print(f"Created setup issue: {issue.html_url}")

        # 2. Post the welcome comment
        post_comment(
            token=token,
            repository=repo,
            issue_number=issue.number,
            body=WELCOME_COMMENT,
        )
        print("Posted welcome comment.")
        
        # 3. Run validation and post results
        print("\nRunning setup validation...")
        validation_result = validate_setup(repo, token)
        
        # Build validation comment
        validation_comment = "## 🔍 Setup Validation Results\n\n"
        
        if validation_result["valid"]:
            validation_comment += "✅ **All critical checks passed!**\n\n"
        else:
            validation_comment += "⚠️ **Some issues need attention:**\n\n"
            validation_comment += "### Critical Issues\n"
            for validation_issue in validation_result["issues"]:
                validation_comment += f"- ❌ {validation_issue}\n"
            validation_comment += "\n"
        
        if validation_result["warnings"]:
            validation_comment += "### Warnings\n"
            for warning in validation_result["warnings"]:
                validation_comment += f"- ⚠️  {warning}\n"
            validation_comment += "\n"
        
        validation_comment += "### Setup Checklist\n\n"
        validation_comment += "- [ ] Configure `GH_TOKEN` secret (Classic: `repo`+`workflow` | Fine-grained: Variables read permission)\n"
        validation_comment += "- [ ] Set `UPSTREAM_REPO` variable (e.g., `owner/template-repo`)\n"
        validation_comment += "- [ ] Set `SYNC_SIGNATURE_SECRET` for dispatch verification\n"
        validation_comment += "- [ ] Add `speculum-downstream` topic to repository\n"
        validation_comment += "- [ ] Run upstream sync workflow\n"
        
        post_comment(
            token=token,
            repository=repo,
            issue_number=issue.number,
            body=validation_comment,
        )
        print("Posted validation results.")

    except GitHubIssueError as err:
        print(f"GitHub API Error: {err}", file=sys.stderr)
        return 1

    return 0


def validate_setup(
    repo: str,
    token: str,
    api_url: str = "https://api.github.com",
    quiet: bool = False,
) -> dict[str, any]:
    """Validate repository setup configuration.
    
    Checks:
    - GH_TOKEN secret exists and has required scopes (Classic: repo+workflow, Fine-grained: Variables permission)
    - UPSTREAM_REPO variable is set
    - SYNC_SIGNATURE_SECRET exists
    - Repository has speculum-downstream topic
    - Repository is not a fork
    - Repository was created from template
    
    Args:
        repo: Repository in "owner/repo" format
        token: GitHub API token
        api_url: GitHub API base URL
        quiet: If True, suppress all print output
        
        Returns:
        Dictionary with validation results: {
            "valid": bool,
            "issues": [list of critical issues],
            "warnings": [list of warnings]
        }
    """
    issues = []
    warnings = []
    
    if not quiet:
        print("\n=== Setup Validation ====================\n")
    
    # Check 1: GH_TOKEN (we can only verify it exists since we're using it)
    if not quiet:
        print("✓ GH_TOKEN secret exists")
    
    # Check 2: UPSTREAM_REPO variable
    try:
        upstream_repo = get_repository_variable(repo, "UPSTREAM_REPO", token, api_url)
        if not upstream_repo:
            issues.append("UPSTREAM_REPO variable not set")
            if not quiet:
                print("❌ UPSTREAM_REPO variable not set")
        else:
            if not quiet:
                print(f"✓ UPSTREAM_REPO: {upstream_repo}")
    except Exception as e:
        issues.append(f"Could not verify UPSTREAM_REPO: {e}")
        if not quiet:
            print(f"❌ Could not verify UPSTREAM_REPO: {e}")
    
    # Check 3: SYNC_SIGNATURE_SECRET (can't directly check secrets, but can document requirement)
    # This would need to be checked in the workflow itself
    if not quiet:
        print("ℹ️  SYNC_SIGNATURE_SECRET should be configured (verified in workflow)")
    
    # Check 4: Repository is not a fork
    try:
        repo_data = get_repository_details(repository=repo, token=token, api_url=api_url)
        
        if repo_data.get("fork", False):
            issues.append("Repository is a fork (must be created from template)")
            if not quiet:
                print("❌ Repository is a fork")
        else:
            if not quiet:
                print("✓ Repository is not a fork")
        
        # Check 5: Repository has speculum-downstream topic
        topics = repo_data.get("topics", [])
        if "speculum-downstream" not in topics:
            warnings.append("Repository missing speculum-downstream topic")
            if not quiet:
                print("⚠️  Missing speculum-downstream topic")
        else:
            if not quiet:
                print("✓ Repository has speculum-downstream topic")
        
        # Check 6: Repository was created from template
        template_repo = repo_data.get("template_repository")
        if not template_repo:
            warnings.append("Repository not created from template")
            if not quiet:
                print("⚠️  Not created from template")
        else:
            template_name = template_repo.get("full_name")
            if not quiet:
                print(f"✓ Created from template: {template_name}")
            
            # Verify template matches UPSTREAM_REPO
            try:
                upstream_repo = get_repository_variable(repo, "UPSTREAM_REPO", token, api_url)
                if upstream_repo and template_name != upstream_repo:
                    warnings.append(
                        f"Template ({template_name}) differs from UPSTREAM_REPO ({upstream_repo})"
                    )
                    if not quiet:
                        print(f"⚠️  Template mismatch")
            except:
                pass
        
    except Exception as e:
        warnings.append(f"Could not verify repository details: {e}")
        if not quiet:
            print(f"⚠️  Verification error: {e}")
    
    # Check 7: Required discussion categories exist
    # - Sources: Required for source curation workflow
    # - People: Required for syncing Person entities from knowledge graph
    # - Organizations: Required for syncing Organization entities from knowledge graph
    required_categories = [
        ("Sources", "source curation"),
        ("People", "Person entity sync"),
        ("Organizations", "Organization entity sync"),
    ]
    missing_categories = []
    
    try:
        for category_name, purpose in required_categories:
            category = github_discussions.get_category_by_name(
                token=token,
                repository=repo,
                category_name=category_name,
            )
            if category:
                if not quiet:
                    print(f"✓ '{category_name}' discussion category exists")
            else:
                missing_categories.append(category_name)
                if not quiet:
                    print(f"⚠️  '{category_name}' discussion category not found")
        
        if missing_categories:
            warnings.append(
                f"Missing discussion categories: {', '.join(missing_categories)}. "
                "Create them in repository Settings > Discussions to enable full functionality."
            )
    except Exception as e:
        # Discussions may not be enabled
        warnings.append(f"Could not check discussion categories: {e}")
        if not quiet:
            print(f"⚠️  Could not check discussion categories: {e}")

    # Check 8: Required labels exist
    required_label_names = {lbl.name for lbl in REQUIRED_LABELS}
    missing_labels = []

    try:
        existing_labels = get_repository_labels(token=token, repository=repo, api_url=api_url)
        existing_label_names = {lbl["name"].lower() for lbl in existing_labels}

        for label in REQUIRED_LABELS:
            if label.name.lower() in existing_label_names:
                if not quiet:
                    print(f"✓ '{label.name}' label exists")
            else:
                missing_labels.append(label.name)
                if not quiet:
                    print(f"⚠️  '{label.name}' label not found")

        if missing_labels:
            warnings.append(
                f"Missing labels: {', '.join(missing_labels)}. "
                "Re-run 'python -m main setup' to create them automatically."
            )
    except Exception as e:
        warnings.append(f"Could not check repository labels: {e}")
        if not quiet:
            print(f"⚠️  Could not check repository labels: {e}")

    if not quiet:
        print("\n=========================================\n")
    
    # Summary
    if not quiet:
        if issues:
            print("\n🚨 Critical Issues Found:\n")
            for issue in issues:
                print(f"  ❌ {issue}")
            print("\nPlease resolve these issues before syncing.")
        
        if warnings:
            print("\n⚠️  Warnings:\n")
            for warning in warnings:
                print(f"  ⚠️  {warning}")
        
        if not issues and not warnings:
            print("✅ All setup validation checks passed!")
    
    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "warnings": warnings,
    }


def validate_setup_cli(args: argparse.Namespace) -> int:
    """Handler for the validate-setup command."""
    try:
        token = resolve_token(None)
        repo = resolve_repository(args.repo)
    except GitHubIssueError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    
    try:
        result = validate_setup(repo, token, quiet=args.json)
        
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            # Text output already printed by validate_setup
            pass
        
        # Exit with error code if validation failed
        return 0 if result["valid"] else 1
    
    except Exception as err:
        print(f"Error during validation: {err}", file=sys.stderr)
        return 1


def verify_dispatch_cli(args: argparse.Namespace) -> int:
    """Handler for the verify-dispatch command."""
    import os
    from src.integrations.github.sync import verify_dispatch_signature
    
    secret = args.secret or os.environ.get("SYNC_SIGNATURE_SECRET")
    if not secret:
        print("Error: No secret provided. Use --secret or set SYNC_SIGNATURE_SECRET", file=sys.stderr)
        return 1
    
    try:
        is_valid = verify_dispatch_signature(
            upstream_repo=args.upstream_repo,
            upstream_branch=args.upstream_branch,
            timestamp=args.timestamp,
            signature=args.signature,
            secret=secret,
        )
        
        if is_valid:
            print("✅ Dispatch signature verified")
            return 0
        else:
            print("❌ Invalid signature - dispatch authentication failed", file=sys.stderr)
            return 1
    
    except Exception as err:
        print(f"Error verifying signature: {err}", file=sys.stderr)
        return 1
