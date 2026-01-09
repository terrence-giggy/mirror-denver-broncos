"""GitHub repository sync utilities for template-to-clone code updates.

This module provides functions to sync code directories from an upstream
template repository to a downstream clone using the GitHub API.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass, field
from typing import Any
from urllib import error, request

from .issues import API_VERSION, DEFAULT_API_URL, GitHubIssueError, normalize_repository
from .pull_requests import fetch_pull_request_files


# Directories to sync from upstream (code directories)
CODE_DIRECTORIES = [
    "src/",
    "tests/",
    ".github/",
    "config/missions/",
    "docs/",
]

# Individual files to sync from root
CODE_FILES = [
    "main.py",
    "requirements.txt",
    "pytest.ini",
]

# Directories to never sync (research content)
PROTECTED_DIRECTORIES = [
    "evidence/",
    "knowledge-graph/",
    "reports/",
    "dev_data/",
    "devops/",
]


class SyncError(GitHubIssueError):
    """Raised when a sync operation fails."""


@dataclass
class FileInfo:
    """Represents a file in a repository."""
    path: str
    sha: str
    size: int
    content: str | None = None  # Base64 encoded content, fetched on demand

    def content_hash(self) -> str:
        """Return SHA-1 hash of file content (same as git blob SHA)."""
        return self.sha


@dataclass
class SyncChange:
    """Represents a file change to be synced."""
    path: str
    action: str  # 'add', 'update', 'delete'
    upstream_sha: str | None = None
    downstream_sha: str | None = None


@dataclass
class SyncResult:
    """Result of a sync operation."""
    changes: list[SyncChange] = field(default_factory=list)
    branch_name: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    dry_run: bool = False
    error: str | None = None

    @property
    def has_changes(self) -> bool:
        return len(self.changes) > 0

    def summary(self) -> str:
        """Generate a human-readable summary of changes."""
        if not self.changes:
            return "No changes detected - repository is up to date with upstream."
        
        adds = [c for c in self.changes if c.action == "add"]
        updates = [c for c in self.changes if c.action == "update"]
        deletes = [c for c in self.changes if c.action == "delete"]
        
        lines = [f"Found {len(self.changes)} file(s) to sync:"]
        if adds:
            lines.append(f"\n**Added ({len(adds)}):**")
            for c in adds[:10]:  # Limit display
                lines.append(f"- `{c.path}`")
            if len(adds) > 10:
                lines.append(f"- ... and {len(adds) - 10} more")
        
        if updates:
            lines.append(f"\n**Updated ({len(updates)}):**")
            for c in updates[:10]:
                lines.append(f"- `{c.path}`")
            if len(updates) > 10:
                lines.append(f"- ... and {len(updates) - 10} more")
        
        if deletes:
            lines.append(f"\n**Removed ({len(deletes)}):**")
            for c in deletes[:10]:
                lines.append(f"- `{c.path}`")
            if len(deletes) > 10:
                lines.append(f"- ... and {len(deletes) - 10} more")
        
        return "\n".join(lines)


def _make_request(
    endpoint: str,
    token: str | None = None,
    method: str = "GET",
    data: dict | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Make a GitHub API request."""
    req = request.Request(endpoint, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    
    if data:
        req.add_header("Content-Type", "application/json; charset=utf-8")
        req.data = json.dumps(data).encode("utf-8")
    
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        # Extract useful context for debugging
        operation = f"{method} {endpoint}"
        error_msg = f"GitHub API error ({exc.code}): {error_text}"
        error_msg += f"\nOperation: {operation}"
        if data:
            # Include data summary for debugging, but redact sensitive info
            data_summary = {k: f"<{len(str(v))} chars>" if k in ['content', 'token'] else v 
                          for k, v in (data.items() if isinstance(data, dict) else {})}
            error_msg += f"\nRequest data keys: {list(data_summary.keys())}"
            if 'tree' in data_summary:
                error_msg += f"\nTree entries count: {len(data.get('tree', []))}"
        raise SyncError(error_msg) from exc
    except error.URLError as exc:
        raise SyncError(f"Failed to reach GitHub API: {exc.reason}\nEndpoint: {endpoint}") from exc


def get_template_repository(
    repository: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> str | None:
    """Get the template repository that this repo was created from.
    
    Args:
        repository: Repository in "owner/repo" format
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        Template repository in "owner/repo" format, or None if not from template
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url}/repos/{owner}/{name}"
    
    data = _make_request(endpoint, token)
    template = data.get("template_repository")
    
    if template:
        return template.get("full_name")
    return None


def get_repository_variable(
    repository: str,
    variable_name: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> str | None:
    """Get a repository variable value.
    
    Args:
        repository: Repository in "owner/repo" format
        variable_name: Name of the variable
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        Variable value or None if not found
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url}/repos/{owner}/{name}/actions/variables/{variable_name}"
    
    try:
        data = _make_request(endpoint, token)
        return data.get("value")
    except SyncError as e:
        if "404" in str(e):
            return None
        raise


def set_repository_variable(
    repository: str,
    variable_name: str,
    value: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> bool:
    """Set a repository variable via the GitHub API.
    
    Creates the variable if it doesn't exist, updates if it does.
    
    Args:
        repository: Repository in "owner/repo" format
        variable_name: Name of the variable
        value: Value to set
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        True if successful
    """
    owner, name = normalize_repository(repository)
    
    # Check if variable exists
    existing = get_repository_variable(repository, variable_name, token, api_url)
    
    if existing is not None:
        # Update existing variable
        endpoint = f"{api_url}/repos/{owner}/{name}/actions/variables/{variable_name}"
        data = {"name": variable_name, "value": value}
        _make_request(endpoint, token, method="PATCH", data=data)
    else:
        # Create new variable
        endpoint = f"{api_url}/repos/{owner}/{name}/actions/variables"
        data = {"name": variable_name, "value": value}
        _make_request(endpoint, token, method="POST", data=data)
    
    return True


def configure_upstream_variable(
    repository: str,
    token: str,
    upstream_repo: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Configure the UPSTREAM_REPO variable for sync workflow.
    
    If upstream_repo is not provided, attempts to auto-detect from
    the template_repository field.
    
    Args:
        repository: Repository in "owner/repo" format  
        token: GitHub API token
        upstream_repo: Explicit upstream repo, or None to auto-detect
        api_url: GitHub API base URL
        
    Returns:
        Dict with success status and details
    """
    # Auto-detect from template if not provided
    if not upstream_repo:
        upstream_repo = get_template_repository(repository, token, api_url)
        if not upstream_repo:
            return {
                "success": False,
                "error": "Repository was not created from a template and no upstream specified",
            }
    
    # Set the variable
    set_repository_variable(repository, "UPSTREAM_REPO", upstream_repo, token, api_url)
    
    return {
        "success": True,
        "upstream_repo": upstream_repo,
        "variable": "UPSTREAM_REPO",
        "message": f"Set UPSTREAM_REPO to {upstream_repo}",
    }


def get_default_branch(
    repository: str,
    token: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """Get the default branch name for a repository."""
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url}/repos/{owner}/{name}"
    
    data = _make_request(endpoint, token)
    return data.get("default_branch", "main")


def get_tree_sha(
    repository: str,
    branch: str,
    token: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """Get the tree SHA for a branch."""
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url}/repos/{owner}/{name}/git/ref/heads/{branch}"
    
    data = _make_request(endpoint, token)
    commit_sha = data["object"]["sha"]
    
    # Get the commit to find tree SHA
    commit_endpoint = f"{api_url}/repos/{owner}/{name}/git/commits/{commit_sha}"
    commit_data = _make_request(commit_endpoint, token)
    
    return commit_data["tree"]["sha"]


def get_repository_tree(
    repository: str,
    branch: str | None = None,
    token: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> list[FileInfo]:
    """Get all files in a repository using the Git Trees API.
    
    This is more efficient than listing individual directories as it
    fetches the entire tree in a single API call.
    
    Args:
        repository: Repository in "owner/repo" format
        branch: Branch name (defaults to repository's default branch)
        token: GitHub API token (optional for public repos)
        api_url: GitHub API base URL
        
    Returns:
        List of FileInfo objects for all files in the repository
    """
    owner, name = normalize_repository(repository)
    
    if not branch:
        branch = get_default_branch(repository, token, api_url)
    
    tree_sha = get_tree_sha(repository, branch, token, api_url)
    endpoint = f"{api_url}/repos/{owner}/{name}/git/trees/{tree_sha}?recursive=1"
    
    data = _make_request(endpoint, token)
    
    files = []
    for item in data.get("tree", []):
        if item["type"] == "blob":  # Only include files, not directories
            files.append(FileInfo(
                path=item["path"],
                sha=item["sha"],
                size=item.get("size", 0),
            ))
    
    return files


def filter_syncable_files(files: list[FileInfo]) -> list[FileInfo]:
    """Filter files to only include those in code directories.
    
    Args:
        files: List of all files in repository
        
    Returns:
        List of files that should be synced
    """
    syncable = []
    
    for file in files:
        # Check if file is a root-level code file
        if file.path in CODE_FILES:
            syncable.append(file)
            continue
        
        # Check if file is in a code directory
        for code_dir in CODE_DIRECTORIES:
            if file.path.startswith(code_dir):
                # Ensure it's not in a protected subdirectory
                is_protected = False
                for protected in PROTECTED_DIRECTORIES:
                    if file.path.startswith(protected):
                        is_protected = True
                        break
                
                if not is_protected:
                    syncable.append(file)
                break
    
    return syncable


def get_file_content(
    repository: str,
    path: str,
    branch: str | None = None,
    token: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """Get the base64-encoded content of a file.
    
    Args:
        repository: Repository in "owner/repo" format
        path: Path to file in repository
        branch: Branch name (optional)
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        Base64-encoded file content
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url}/repos/{owner}/{name}/contents/{path}"
    
    if branch:
        endpoint += f"?ref={branch}"
    
    data = _make_request(endpoint, token)
    
    # Handle large files (>1MB) that return download URL instead of content
    if "content" not in data and "download_url" in data:
        # Fetch from download URL
        req = request.Request(data["download_url"])
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with request.urlopen(req, timeout=60) as response:
            content = response.read()
            return base64.b64encode(content).decode("utf-8")
    
    return data.get("content", "").replace("\n", "")  # GitHub adds newlines


def compare_files(
    upstream_files: list[FileInfo],
    downstream_files: list[FileInfo],
) -> list[SyncChange]:
    """Compare upstream and downstream files to determine changes.
    
    Args:
        upstream_files: Files from upstream repository (filtered to syncable)
        downstream_files: Files from downstream repository (filtered to syncable)
        
    Returns:
        List of changes needed to sync downstream with upstream
    """
    upstream_map = {f.path: f for f in upstream_files}
    downstream_map = {f.path: f for f in downstream_files}
    
    changes = []
    
    # Find files to add or update
    for path, upstream_file in upstream_map.items():
        if path not in downstream_map:
            changes.append(SyncChange(
                path=path,
                action="add",
                upstream_sha=upstream_file.sha,
            ))
        elif upstream_file.sha != downstream_map[path].sha:
            changes.append(SyncChange(
                path=path,
                action="update",
                upstream_sha=upstream_file.sha,
                downstream_sha=downstream_map[path].sha,
            ))
    
    # Find files to delete (in downstream but not in upstream)
    for path in downstream_map:
        if path not in upstream_map:
            changes.append(SyncChange(
                path=path,
                action="delete",
                downstream_sha=downstream_map[path].sha,
            ))
    
    return changes


def create_branch(
    repository: str,
    branch_name: str,
    from_branch: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """Create a new branch from an existing branch.
    
    Args:
        repository: Repository in "owner/repo" format
        branch_name: Name of the new branch
        from_branch: Branch to create from
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        SHA of the new branch head
    """
    owner, name = normalize_repository(repository)
    
    # Get the SHA of the source branch
    ref_endpoint = f"{api_url}/repos/{owner}/{name}/git/ref/heads/{from_branch}"
    ref_data = _make_request(ref_endpoint, token)
    sha = ref_data["object"]["sha"]
    
    # Create the new branch
    create_endpoint = f"{api_url}/repos/{owner}/{name}/git/refs"
    data = {
        "ref": f"refs/heads/{branch_name}",
        "sha": sha,
    }
    
    try:
        result = _make_request(create_endpoint, token, method="POST", data=data)
        return result["object"]["sha"]
    except SyncError as e:
        # Branch might already exist, try to update it
        if "422" in str(e) or "Reference already exists" in str(e):
            update_endpoint = f"{api_url}/repos/{owner}/{name}/git/refs/heads/{branch_name}"
            update_data = {"sha": sha, "force": True}
            result = _make_request(update_endpoint, token, method="PATCH", data=update_data)
            return result["object"]["sha"]
        raise


def commit_files(
    repository: str,
    branch: str,
    changes: list[SyncChange],
    upstream_repo: str,
    upstream_branch: str,
    token: str,
    upstream_token: str | None = None,
    api_url: str = DEFAULT_API_URL,
    verbose: bool = True,
) -> str:
    """Commit file changes to a branch.
    
    Uses the Contents API to commit files one by one, which works with
    standard PATs and doesn't require fine-grained permissions.
    
    Args:
        repository: Downstream repository in "owner/repo" format
        branch: Branch to commit to
        changes: List of file changes to apply
        upstream_repo: Upstream repository to fetch content from
        upstream_branch: Upstream branch
        token: GitHub API token for downstream repo
        upstream_token: GitHub API token for upstream repo (if private)
        api_url: GitHub API base URL
        verbose: If True, print progress messages
        
    Returns:
        SHA of the last commit
    """
    owner, name = normalize_repository(repository)
    
    if verbose:
        print(f"Starting commit process for {len(changes)} changes to {repository}/{branch}")
    
    last_commit_sha = None
    
    for idx, change in enumerate(changes, 1):
        if verbose:
            print(f"  [{idx}/{len(changes)}] Processing {change.action}: {change.path}")
        
        contents_endpoint = f"{api_url}/repos/{owner}/{name}/contents/{change.path}"
        commit_message = f"Sync: {change.action} {change.path}"
        
        if change.action == "delete":
            # Get current file SHA for deletion
            try:
                file_data = _make_request(
                    f"{contents_endpoint}?ref={branch}",
                    token,
                )
                file_sha = file_data["sha"]
                
                # Delete the file
                delete_data = {
                    "message": commit_message,
                    "sha": file_sha,
                    "branch": branch,
                }
                result = _make_request(contents_endpoint, token, method="DELETE", data=delete_data)
                last_commit_sha = result["commit"]["sha"]
                if verbose:
                    print(f"      Deleted (commit: {last_commit_sha[:8]})")
            except SyncError as e:
                # File might already be deleted
                if "404" in str(e):
                    if verbose:
                        print(f"      Already deleted, skipping")
                else:
                    raise
        else:
            # Add or update file
            # Get content from upstream
            content = get_file_content(
                upstream_repo,
                change.path,
                upstream_branch,
                upstream_token or token,
                api_url,
            )
            
            # Get current file SHA if it exists (for updates)
            file_sha = None
            try:
                file_data = _make_request(
                    f"{contents_endpoint}?ref={branch}",
                    token,
                )
                file_sha = file_data["sha"]
            except SyncError as e:
                # File doesn't exist yet (add operation)
                if "404" not in str(e):
                    raise
            
            # Create or update the file
            update_data = {
                "message": commit_message,
                "content": content,
                "branch": branch,
            }
            if file_sha:
                update_data["sha"] = file_sha
            
            result = _make_request(contents_endpoint, token, method="PUT", data=update_data)
            last_commit_sha = result["commit"]["sha"]
            if verbose:
                print(f"      Committed (commit: {last_commit_sha[:8]})")
    
    if verbose:
        print(f"  All changes committed successfully")
        print(f"  Final commit: {last_commit_sha[:8] if last_commit_sha else 'none'}")
    
    return last_commit_sha or ""


def create_sync_pull_request(
    repository: str,
    branch: str,
    base_branch: str,
    changes: list[SyncChange],
    upstream_repo: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> tuple[int, str]:
    """Create a pull request for the sync changes.
    
    Args:
        repository: Repository in "owner/repo" format
        branch: Source branch with sync changes
        base_branch: Target branch (usually main)
        changes: List of changes included
        upstream_repo: Upstream repository name for PR description
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        Tuple of (PR number, PR URL)
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url}/repos/{owner}/{name}/pulls"
    
    # Build PR body
    adds = [c for c in changes if c.action == "add"]
    updates = [c for c in changes if c.action == "update"]
    deletes = [c for c in changes if c.action == "delete"]
    
    body_lines = [
        f"## Upstream Sync from {upstream_repo}",
        "",
        "This PR syncs code changes from the upstream template repository.",
        "",
        "### Changes Summary",
        f"- **Added:** {len(adds)} file(s)",
        f"- **Updated:** {len(updates)} file(s)",  
        f"- **Removed:** {len(deletes)} file(s)",
        "",
        "### Files Changed",
    ]
    
    for change in changes[:50]:  # Limit to 50 files in description
        action_icon = {"add": "➕", "update": "📝", "delete": "❌"}[change.action]
        body_lines.append(f"- {action_icon} `{change.path}`")
    
    if len(changes) > 50:
        body_lines.append(f"- ... and {len(changes) - 50} more files")
    
    body_lines.extend([
        "",
        "---",
        "*This PR was automatically created by the upstream sync workflow.*",
    ])
    
    data = {
        "title": f"🔄 Sync code from upstream ({len(changes)} files)",
        "body": "\n".join(body_lines),
        "head": branch,
        "base": base_branch,
        "draft": False,  # Non-draft for auto-merge compatibility
    }
    
    result = _make_request(endpoint, token, method="POST", data=data)
    return result["number"], result["html_url"]


def sync_from_upstream(
    downstream_repo: str,
    upstream_repo: str,
    downstream_token: str,
    upstream_token: str | None = None,
    upstream_branch: str | None = None,
    downstream_branch: str | None = None,
    dry_run: bool = False,
    force_sync: bool = False,
    track_status: bool = True,
    api_url: str = DEFAULT_API_URL,
    verbose: bool = True,
) -> SyncResult:
    """Perform a full sync from upstream repository.
    
    This is the main entry point for syncing code from an upstream
    template repository to a downstream clone.
    
    Args:
        downstream_repo: Downstream repository in "owner/repo" format
        upstream_repo: Upstream repository in "owner/repo" format
        downstream_token: GitHub API token for downstream repo
        upstream_token: GitHub API token for upstream repo (if private)
        upstream_branch: Upstream branch to sync from (default: default branch)
        downstream_branch: Downstream branch to sync to (default: default branch)
        dry_run: If True, only report changes without applying them
        force_sync: If True, skip validation and overwrite local modifications
        track_status: If True, update sync status variables after successful sync
        api_url: GitHub API base URL
        verbose: If True, print progress messages to stdout
        
    Returns:
        SyncResult with details of the sync operation
    """
    result = SyncResult(dry_run=dry_run)
    
    try:
        # Get default branches if not specified
        if verbose:
            print("Resolving branches...")
        if not upstream_branch:
            upstream_branch = get_default_branch(upstream_repo, upstream_token, api_url)
            if verbose:
                print(f"  Upstream branch: {upstream_branch}")
        if not downstream_branch:
            downstream_branch = get_default_branch(downstream_repo, downstream_token, api_url)
            if verbose:
                print(f"  Downstream branch: {downstream_branch}")
        
        # Pre-sync validation (unless force_sync or dry_run)
        if not force_sync and not dry_run:
            if verbose:
                print("Running pre-sync validation...")
            validation = validate_pre_sync(
                downstream_repo=downstream_repo,
                upstream_repo=upstream_repo,
                downstream_token=downstream_token,
                upstream_token=upstream_token,
                downstream_branch=downstream_branch,
                upstream_branch=upstream_branch,
                api_url=api_url,
            )
            if not validation.valid:
                result.error = validation.summary()
                if verbose:
                    print(f"  Validation failed: {result.error}")
                return result
            if verbose:
                print("  Validation passed")
        elif force_sync and verbose:
            print("Skipping validation (force_sync=True)")
        
        # Get file trees
        if verbose:
            print("Fetching repository trees...")
        upstream_files = get_repository_tree(
            upstream_repo, upstream_branch, upstream_token, api_url
        )
        if verbose:
            print(f"  Upstream: {len(upstream_files)} files")
        downstream_files = get_repository_tree(
            downstream_repo, downstream_branch, downstream_token, api_url
        )
        if verbose:
            print(f"  Downstream: {len(downstream_files)} files")
        
        # Filter to syncable files
        upstream_syncable = filter_syncable_files(upstream_files)
        downstream_syncable = filter_syncable_files(downstream_files)
        if verbose:
            print(f"  Syncable files - Upstream: {len(upstream_syncable)}, Downstream: {len(downstream_syncable)}")
        
        # Compare files
        if verbose:
            print("Comparing files...")
        changes = compare_files(upstream_syncable, downstream_syncable)
        result.changes = changes
        if verbose:
            print(f"  Found {len(changes)} changes")
        
        if not changes:
            if verbose:
                print("No changes detected")
            return result
        
        if dry_run:
            if verbose:
                print("Dry run mode - not applying changes")
            return result
        
        # Create sync branch
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        sync_branch = f"sync/upstream-{timestamp}"
        
        if verbose:
            print(f"Creating sync branch: {sync_branch}")
        create_branch(
            downstream_repo,
            sync_branch,
            downstream_branch,
            downstream_token,
            api_url,
        )
        result.branch_name = sync_branch
        if verbose:
            print("  Branch created")
        
        # Commit changes
        if verbose:
            print(f"Committing changes to {sync_branch}...")
        commit_files(
            downstream_repo,
            sync_branch,
            changes,
            upstream_repo,
            upstream_branch,
            downstream_token,
            upstream_token,
            api_url,
            verbose,
        )
        if verbose:
            print("  Changes committed")
        
        # Create PR
        if verbose:
            print("Creating pull request...")
        pr_number, pr_url = create_sync_pull_request(
            downstream_repo,
            sync_branch,
            downstream_branch,
            changes,
            upstream_repo,
            downstream_token,
            api_url,
        )
        result.pr_number = pr_number
        result.pr_url = pr_url
        if verbose:
            print(f"  PR created: #{pr_number}")
        
        # Update sync status tracking
        if track_status:
            try:
                # Get the commit SHA from the sync branch
                owner, name = normalize_repository(downstream_repo)
                ref_endpoint = f"{api_url}/repos/{owner}/{name}/git/ref/heads/{sync_branch}"
                ref_data = _make_request(ref_endpoint, downstream_token)
                commit_sha = ref_data["object"]["sha"]
                
                update_sync_status(
                    downstream_repo,
                    downstream_token,
                    commit_sha,
                    pr_number,
                    pr_url,
                    api_url,
                )
            except Exception:
                # Don't fail the sync if status tracking fails
                pass
        
    except Exception as e:
        result.error = str(e)
    
    return result


@dataclass
class ValidationResult:
    """Result of pre-sync validation."""
    valid: bool = True
    local_modifications: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    def summary(self) -> str:
        """Generate validation summary."""
        if self.valid and not self.warnings:
            return "✅ Pre-sync validation passed"
        
        lines = []
        if self.local_modifications:
            lines.append(f"⚠️ Found {len(self.local_modifications)} locally modified file(s):")
            for path in self.local_modifications[:10]:
                lines.append(f"  - `{path}`")
            if len(self.local_modifications) > 10:
                lines.append(f"  - ... and {len(self.local_modifications) - 10} more")
        
        for warning in self.warnings:
            lines.append(f"⚠️ {warning}")
        
        return "\n".join(lines) if lines else "✅ Pre-sync validation passed"


def validate_pre_sync(
    downstream_repo: str,
    upstream_repo: str,
    downstream_token: str,
    upstream_token: str | None = None,
    downstream_branch: str | None = None,
    upstream_branch: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> ValidationResult:
    """Validate repository state before syncing.
    
    Checks for local modifications in code directories that would be
    overwritten by the sync. This helps prevent accidental loss of
    downstream-specific changes.
    
    Args:
        downstream_repo: Downstream repository in "owner/repo" format
        upstream_repo: Upstream repository in "owner/repo" format
        downstream_token: GitHub API token for downstream repo
        upstream_token: GitHub API token for upstream repo
        downstream_branch: Downstream branch to check
        upstream_branch: Upstream branch to compare against
        api_url: GitHub API base URL
        
    Returns:
        ValidationResult with validation status and any warnings
    """
    result = ValidationResult()
    
    try:
        # Get branches
        if not downstream_branch:
            downstream_branch = get_default_branch(downstream_repo, downstream_token, api_url)
        if not upstream_branch:
            upstream_branch = get_default_branch(upstream_repo, upstream_token, api_url)
        
        # Get file trees
        upstream_files = get_repository_tree(
            upstream_repo, upstream_branch, upstream_token, api_url
        )
        downstream_files = get_repository_tree(
            downstream_repo, downstream_branch, downstream_token, api_url
        )
        
        # Filter to syncable files
        upstream_syncable = filter_syncable_files(upstream_files)
        downstream_syncable = filter_syncable_files(downstream_files)
        
        # Build maps
        upstream_map = {f.path: f for f in upstream_syncable}
        downstream_map = {f.path: f for f in downstream_syncable}
        
        # Find files that exist in both but differ AND are also different from
        # what we'd expect if downstream was in sync with upstream
        # These are "local modifications" - files changed in downstream independently
        for path, downstream_file in downstream_map.items():
            if path in upstream_map:
                # File exists in both - if SHAs differ, it will be overwritten
                if downstream_file.sha != upstream_map[path].sha:
                    # This file has been modified in downstream
                    # We flag this as a local modification
                    result.local_modifications.append(path)
        
        # Check for files only in downstream (would be deleted)
        downstream_only = [p for p in downstream_map if p not in upstream_map]
        if downstream_only:
            result.warnings.append(
                f"{len(downstream_only)} file(s) exist only in downstream and would be removed"
            )
        
        # Set validity based on modifications
        if result.local_modifications:
            result.valid = False
            result.warnings.insert(0, 
                "Local modifications detected. Use force_sync=True to overwrite."
            )
        
    except Exception as e:
        result.valid = False
        result.warnings.append(f"Validation error: {str(e)}")
    
    return result


@dataclass
class SyncStatus:
    """Tracks sync status for a repository."""
    last_sync_sha: str | None = None
    last_sync_time: str | None = None
    upstream_repo: str | None = None
    sync_count: int = 0
    last_pr_number: int | None = None
    last_pr_url: str | None = None


def get_sync_status(
    repository: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> SyncStatus:
    """Get the current sync status from repository variables.
    
    Args:
        repository: Repository in "owner/repo" format
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        SyncStatus with current tracking info
    """
    status = SyncStatus()
    
    # Read status variables
    status.last_sync_sha = get_repository_variable(
        repository, "SYNC_LAST_SHA", token, api_url
    )
    status.last_sync_time = get_repository_variable(
        repository, "SYNC_LAST_TIME", token, api_url
    )
    status.upstream_repo = get_repository_variable(
        repository, "UPSTREAM_REPO", token, api_url
    )
    
    count_str = get_repository_variable(repository, "SYNC_COUNT", token, api_url)
    status.sync_count = int(count_str) if count_str else 0
    
    pr_num_str = get_repository_variable(repository, "SYNC_LAST_PR", token, api_url)
    status.last_pr_number = int(pr_num_str) if pr_num_str else None
    
    return status


def update_sync_status(
    repository: str,
    token: str,
    commit_sha: str,
    pr_number: int | None = None,
    pr_url: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Update sync status after a successful sync.
    
    Args:
        repository: Repository in "owner/repo" format
        token: GitHub API token
        commit_sha: SHA of the sync commit
        pr_number: PR number if created
        pr_url: PR URL if created
        api_url: GitHub API base URL
    """
    import datetime
    
    # Get current count and increment
    current_status = get_sync_status(repository, token, api_url)
    new_count = current_status.sync_count + 1
    
    # Update all status variables
    set_repository_variable(repository, "SYNC_LAST_SHA", commit_sha, token, api_url)
    set_repository_variable(
        repository, 
        "SYNC_LAST_TIME", 
        datetime.datetime.utcnow().isoformat() + "Z",
        token, 
        api_url
    )
    set_repository_variable(repository, "SYNC_COUNT", str(new_count), token, api_url)
    
    if pr_number:
        set_repository_variable(repository, "SYNC_LAST_PR", str(pr_number), token, api_url)


def verify_dispatch_signature(
    upstream_repo: str,
    upstream_branch: str,
    timestamp: str,
    signature: str,
    secret: str,
) -> bool:
    """Verify HMAC-SHA256 signature for repository dispatch payload.
    
    Args:
        upstream_repo: Upstream repository in "owner/repo" format
        upstream_branch: Upstream branch name
        timestamp: Timestamp string from payload
        signature: HMAC signature to verify
        secret: Shared secret for HMAC computation
        
    Returns:
        True if signature is valid, False otherwise
    """
    # Reconstruct payload data in same format as signing
    payload_data = f"{upstream_repo}|{upstream_branch}|{timestamp}"
    
    # Compute expected signature using HMAC-SHA256
    expected_signature = hmac.new(
        secret.encode('utf-8'),
        payload_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature == expected_signature


def discover_downstream_repos(
    org: str,
    token: str,
    topic: str = "speculum-downstream",
    api_url: str = DEFAULT_API_URL,
) -> list[str]:
    """Discover downstream repositories by topic via GitHub Search API.
    
    Args:
        org: Organization name to search within
        token: GitHub API token
        topic: Topic to search for (default: "speculum-downstream")
        api_url: GitHub API base URL
        
    Returns:
        List of repository names in "owner/repo" format
    """
    # Build search query: topic + org
    query = f"topic:{topic} org:{org}"
    
    # GitHub Search API endpoint
    endpoint = f"{api_url}/search/repositories"
    params = f"?q={query.replace(' ', '+')}"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    
    req = request.Request(f"{endpoint}{params}", headers=headers)
    
    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            repos = []
            for item in data.get("items", []):
                full_name = item.get("full_name")
                if full_name:
                    repos.append(full_name)
            return repos
    except error.HTTPError as e:
        error_body = e.read().decode()
        raise SyncError(f"Failed to discover downstream repos: {e.code} - {error_body}")
    except Exception as e:
        raise SyncError(f"Failed to discover downstream repos: {e}")


def verify_satellite_trust(
    repo: str,
    expected_template: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> tuple[bool, str]:
    """Verify that a repository is a trusted downstream satellite.
    
    Checks:
    - Repository is not a fork
    - Repository was created from expected template
    - Repository has the speculum-downstream topic
    
    Args:
        repo: Repository to verify in "owner/repo" format
        expected_template: Expected template repository in "owner/repo" format
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        Tuple of (is_trusted, reason)
    """
    endpoint = f"{api_url}/repos/{repo}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    
    req = request.Request(endpoint, headers=headers)
    
    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            
            # Check 1: Not a fork
            if data.get("fork", False):
                return (False, "Repository is a fork")
            
            # Check 2: Created from expected template
            template_repo = data.get("template_repository")
            if not template_repo:
                return (False, "Repository not created from template")
            
            template_full_name = template_repo.get("full_name")
            if template_full_name != expected_template:
                return (False, f"Template mismatch: {template_full_name} != {expected_template}")
            
            # Check 3: Has speculum-downstream topic
            topics = data.get("topics", [])
            if "speculum-downstream" not in topics:
                return (False, "Missing speculum-downstream topic")
            
            return (True, "All trust checks passed")
            
    except error.HTTPError as e:
        error_body = e.read().decode()
        return (False, f"API error: {e.code} - {error_body}")
    except Exception as e:
        return (False, f"Verification error: {e}")


def notify_downstream_repos(
    upstream_repo: str,
    upstream_branch: str,
    secret: str,
    token: str,
    org: str | None = None,
    release_tag: str | None = None,
    dry_run: bool = False,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Notify downstream repositories of upstream changes via repository_dispatch.
    
    Discovers downstream repos via topic search, verifies trust, and sends
    signed dispatch events.
    
    Args:
        upstream_repo: Upstream repository in "owner/repo" format
        upstream_branch: Upstream branch name
        secret: Shared secret for HMAC signing
        token: GitHub API token with repo:write access
        org: Organization to search for downstream repos (default: upstream org)
        release_tag: Optional release tag to include
        dry_run: If True, only report what would be done
        api_url: GitHub API base URL
        
    Returns:
        Dictionary with results: {success: int, failed: int, repos: [list]}
    """
    import datetime
    
    # Determine organization to search
    if not org:
        org = upstream_repo.split('/')[0]
    
    # Discover downstream repositories
    print(f"Discovering downstream repositories in {org}...")
    downstream_repos = discover_downstream_repos(org, token, api_url=api_url)
    
    if not downstream_repos:
        print("No downstream repositories found")
        return {"success": 0, "failed": 0, "repos": []}
    
    print(f"Found {len(downstream_repos)} repositories with speculum-downstream topic")
    
    # Generate timestamp for signature
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"
    
    # Compute HMAC-SHA256 signature
    payload_data = f"{upstream_repo}|{upstream_branch}|{timestamp}"
    signature = hmac.new(
        secret.encode('utf-8'),
        payload_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    results = {
        "success": 0,
        "failed": 0,
        "repos": []
    }
    
    # Notify each downstream repo
    for repo in downstream_repos:
        print(f"\nProcessing: {repo}")
        
        # Verify trust
        trusted, reason = verify_satellite_trust(repo, upstream_repo, token, api_url)
        if not trusted:
            print(f"  ⚠️  Skipped: {reason}")
            results["failed"] += 1
            results["repos"].append({"repo": repo, "status": "untrusted", "reason": reason})
            continue
        
        print(f"  ✓ Trust verified")
        
        if dry_run:
            print(f"  [DRY RUN] Would send repository_dispatch")
            results["success"] += 1
            results["repos"].append({"repo": repo, "status": "would_notify"})
            continue
        
        # Send repository_dispatch event
        try:
            endpoint = f"{api_url}/repos/{repo}/dispatches"
            payload = {
                "event_type": "upstream-sync",
                "client_payload": {
                    "upstream_repo": upstream_repo,
                    "upstream_branch": upstream_branch,
                    "timestamp": timestamp,
                    "signature": signature,
                    "release_tag": release_tag,
                }
            }
            
            data = json.dumps(payload).encode('utf-8')
            req = request.Request(endpoint, data=data, method='POST')
            req.add_header('Authorization', f'Bearer {token}')
            req.add_header('Accept', 'application/vnd.github+json')
            req.add_header('X-GitHub-Api-Version', API_VERSION)
            req.add_header('Content-Type', 'application/json')
            
            with request.urlopen(req, timeout=30) as response:
                print(f"  ✓ Notified successfully")
                results["success"] += 1
                results["repos"].append({"repo": repo, "status": "notified"})
                
        except error.HTTPError as e:
            error_body = e.read().decode()
            print(f"  ✗ Failed: {e.code} - {error_body}")
            results["failed"] += 1
            results["repos"].append({
                "repo": repo,
                "status": "failed",
                "error": f"{e.code} - {error_body}"
            })
        except Exception as e:
            print(f"  ✗ Failed: {e}")
            results["failed"] += 1
            results["repos"].append({"repo": repo, "status": "failed", "error": str(e)})
    
    return results


def validate_pr_file_scope(
    repo: str,
    pr_number: int,
    token: str,
    api_url: str = DEFAULT_API_URL,
) -> tuple[bool, str]:
    """Validate that a PR only modifies files within allowed code directories.
    
    Checks that all changed files are within CODE_DIRECTORIES or CODE_FILES,
    and none are in PROTECTED_DIRECTORIES.
    
    Args:
        repo: Repository in "owner/repo" format
        pr_number: Pull request number
        token: GitHub API token
        api_url: GitHub API base URL
        
    Returns:
        Tuple of (is_valid, reason)
    """
    try:
        # Fetch PR files
        files = fetch_pull_request_files(
            token=token,
            repository=repo,
            pr_number=pr_number,
            api_url=api_url
        )
        
        if not files:
            return (True, "No files changed")
        
        invalid_files = []
        protected_files = []
        
        for file_data in files:
            filename = file_data.get("filename", "")
            
            # Check if in protected directories
            if any(filename.startswith(pdir) for pdir in PROTECTED_DIRECTORIES):
                protected_files.append(filename)
                continue
            
            # Check if in code directories or is a code file
            is_valid = (
                any(filename.startswith(cdir) for cdir in CODE_DIRECTORIES) or
                filename in CODE_FILES
            )
            
            if not is_valid:
                invalid_files.append(filename)
        
        # Build result message
        if protected_files:
            files_list = ", ".join(protected_files[:5])
            if len(protected_files) > 5:
                files_list += f" (and {len(protected_files) - 5} more)"
            return (False, f"PR modifies protected directories: {files_list}")
        
        if invalid_files:
            files_list = ", ".join(invalid_files[:5])
            if len(invalid_files) > 5:
                files_list += f" (and {len(invalid_files) - 5} more)"
            return (False, f"PR modifies files outside code directories: {files_list}")
        
        return (True, f"All {len(files)} file(s) within allowed scope")
        
    except Exception as e:
        return (False, f"Validation error: {e}")


