"""GitHub-backed storage utilities for persisting files via the API.

This module provides a storage client that writes files directly to a GitHub
repository via the Contents API. This is critical for GitHub Actions workflows
where the local filesystem is ephemeral and discarded when the job ends.

Usage Pattern:
- Reads: Use local filesystem (faster, no API calls)
- Writes: Use GitHubStorageClient when running in GitHub Actions

Example:
    from src.integrations.github.storage import get_github_storage_client

    github_client = get_github_storage_client()
    if github_client:
        github_client.commit_file(
            path="knowledge-graph/sources/abc123.json",
            content=json.dumps(data, indent=2),
            message="Update source entry",
        )
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .files import commit_file
from .issues import DEFAULT_API_URL
from .sync import create_branch
from .pull_requests import create_pull_request


def is_github_actions() -> bool:
    """Check if currently running in GitHub Actions.

    Returns:
        True if the GITHUB_ACTIONS environment variable is set to 'true'.
    """
    return os.environ.get("GITHUB_ACTIONS") == "true"


class GitHubStorageClient:
    """Client for persisting files to a GitHub repository via the API.

    This client wraps the low-level GitHub Contents API operations and provides
    a convenient interface for storage classes to persist their data.

    Attributes:
        token: GitHub API token with contents:write permission.
        repository: Repository in "owner/repo" format.
        branch: Target branch for commits (default: "main").
        api_url: GitHub API base URL.
    """

    def __init__(
        self,
        token: str,
        repository: str,
        branch: str = "main",
        api_url: str = DEFAULT_API_URL,
        pr_branch_prefix: str = "content-acquisition",
    ) -> None:
        """Initialize the GitHub storage client.

        Args:
            token: GitHub API token with contents:write permission.
            repository: Repository in "owner/repo" format.
            branch: Target branch for commits.
            api_url: GitHub API base URL.
            pr_branch_prefix: Prefix for PR branch names (e.g., "content-acquisition-20251231").
        """
        self.token = token
        self.repository = repository
        self.branch = branch
        self.api_url = api_url
        self.pr_branch_prefix = pr_branch_prefix
        self._pr_branch: str | None = None
        self._pr_number: int | None = None

    def commit_file(
        self,
        path: str | Path,
        content: str | bytes,
        message: str,
    ) -> dict[str, Any]:
        """Create or update a file in the repository.

        Args:
            path: Relative path within the repository (e.g., "knowledge-graph/sources/abc.json").
            content: File content as string or bytes.
            message: Commit message describing the change.

        Returns:
            Dictionary containing the GitHub API response with commit details.

        Raises:
            GitHubIssueError: If the API request fails.
        """
        # Normalize path to string and ensure it's relative
        path_str = str(path)
        if path_str.startswith("/"):
            path_str = path_str[1:]

        return commit_file(
            token=self.token,
            repository=self.repository,
            path=path_str,
            content=content,
            message=message,
            branch=self.branch,
            api_url=self.api_url,
        )

    def commit_files_batch(
        self,
        files: list[tuple[str | Path, str | bytes]],
        message: str,
        use_pr_branch: bool = False,
    ) -> dict[str, Any]:
        """Commit multiple files in a single commit using the Git Trees API.

        This is more efficient than multiple individual commits and creates
        a cleaner git history. All files are committed atomically.

        Args:
            files: List of (path, content) tuples. Paths should be relative to repo root.
            message: Commit message for the batch.
            use_pr_branch: If True, commit to PR branch instead of default branch.

        Returns:
            Dictionary containing the GitHub API response with commit details.

        Raises:
            GitHubIssueError: If the API request fails.
        """
        from .files import commit_files_batch

        # Normalize paths
        normalized_files = []
        for path, content in files:
            path_str = str(path)
            if path_str.startswith("/"):
                path_str = path_str[1:]
            normalized_files.append((path_str, content))

        # Determine target branch
        target_branch = self.branch
        if use_pr_branch:
            target_branch = self.ensure_pr_branch()

        return commit_files_batch(
            token=self.token,
            repository=self.repository,
            files=normalized_files,
            message=message,
            branch=target_branch,
            api_url=self.api_url,
        )

    def ensure_pr_branch(self, timestamp_suffix: str | None = None) -> str:
        """Ensure a PR branch exists for content commits.

        Args:
            timestamp_suffix: Optional timestamp to append to branch name.

        Returns:
            Name of the PR branch.
        """
        if self._pr_branch:
            return self._pr_branch

        from datetime import datetime

        if timestamp_suffix is None:
            timestamp_suffix = datetime.utcnow().strftime("%Y%m%d-%H%M%S")

        branch_name = f"{self.pr_branch_prefix}-{timestamp_suffix}"

        # Create branch from base branch
        create_branch(
            repository=self.repository,
            branch_name=branch_name,
            from_branch=self.branch,
            token=self.token,
            api_url=self.api_url,
        )

        self._pr_branch = branch_name
        return branch_name

    def commit_to_pr_branch(
        self,
        path: str | Path,
        content: str | bytes,
        message: str,
        timestamp_suffix: str | None = None,
    ) -> dict[str, Any]:
        """Commit a file to a PR branch instead of the main branch.

        This is used for content acquisition to create a proper audit trail via PRs.

        Args:
            path: Relative path within the repository.
            content: File content as string or bytes.
            message: Commit message describing the change.
            timestamp_suffix: Optional timestamp for branch name.

        Returns:
            Dictionary containing the GitHub API response with commit details.
        """
        pr_branch = self.ensure_pr_branch(timestamp_suffix)

        # Normalize path
        path_str = str(path)
        if path_str.startswith("/"):
            path_str = path_str[1:]

        return commit_file(
            token=self.token,
            repository=self.repository,
            path=path_str,
            content=content,
            message=message,
            branch=pr_branch,
            api_url=self.api_url,
        )

    def create_content_pr(
        self,
        title: str | None = None,
        body: str | None = None,
    ) -> tuple[int, str]:
        """Create a pull request for the accumulated content commits.

        Args:
            title: PR title. Defaults to timestamp-based title.
            body: PR description. Defaults to generic description.

        Returns:
            Tuple of (PR number, PR URL).

        Raises:
            RuntimeError: If no PR branch has been created yet.
        """
        if not self._pr_branch:
            raise RuntimeError("No PR branch created. Call ensure_pr_branch() first.")

        from datetime import datetime

        if title is None:
            timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
            title = f"Content Acquisition - {timestamp}"

        if body is None:
            body = (
                "Automated content acquisition from monitored sources.\n\n"
                "This PR contains newly acquired or updated content from external sources. "
                "Review the changes to ensure content quality and relevance before merging."
            )

        pr_data = create_pull_request(
            token=self.token,
            repository=self.repository,
            title=title,
            body=body,
            head=self._pr_branch,
            base=self.branch,
            draft=False,  # Non-draft for auto-merge compatibility
            api_url=self.api_url,
        )

        self._pr_number = pr_data["number"]
        pr_url = pr_data["html_url"]

        return self._pr_number, pr_url

    @classmethod
    def from_environment(cls) -> "GitHubStorageClient | None":
        """Create a client from environment variables.

        Looks for GITHUB_TOKEN (or GH_TOKEN) and GITHUB_REPOSITORY env vars,
        which are automatically set in GitHub Actions workflows.

        Returns:
            A GitHubStorageClient if the required environment variables are set,
            None otherwise.
        """
        if not is_github_actions():
            return None

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        repository = os.environ.get("GITHUB_REPOSITORY")

        if not token or not repository:
            return None

        # GITHUB_REF_NAME contains the branch name in Actions
        branch = os.environ.get("GITHUB_REF_NAME", "main")

        return cls(
            token=token,
            repository=repository,
            branch=branch,
        )


def get_github_storage_client() -> GitHubStorageClient | None:
    """Get a GitHub storage client if running in GitHub Actions.

    This is the recommended way to obtain a storage client in tool handlers.
    It returns None when running locally, allowing code to fall back to
    local filesystem writes during development.

    Returns:
        A GitHubStorageClient if running in GitHub Actions with proper
        credentials, None otherwise.

    Example:
        github_client = get_github_storage_client()
        registry = SourceRegistry(github_client=github_client)
    """
    return GitHubStorageClient.from_environment()
