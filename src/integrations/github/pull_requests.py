"""GitHub Pull Request integration helpers for the orchestrator."""

from __future__ import annotations

import json
from typing import Any, Mapping
from urllib import error, request

from .issues import API_VERSION, DEFAULT_API_URL, GitHubIssueError, normalize_repository


def fetch_pull_request(
    *,
    token: str,
    repository: str,
    pr_number: int,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Fetch pull request details from GitHub API.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        pr_number: Pull request number
        api_url: GitHub API base URL

    Returns:
        Dictionary containing PR details

    Raises:
        GitHubIssueError: If the API request fails
    """

    normalized_repo = normalize_repository(repository)
    owner, name = normalized_repo
    endpoint = f"{api_url}/repos/{owner}/{name}/pulls/{pr_number}"

    req = request.Request(endpoint)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except error.HTTPError as exc:
        raise GitHubIssueError(f"Failed to fetch PR #{pr_number}: {exc}") from exc
    except Exception as exc:
        raise GitHubIssueError(f"Error fetching PR #{pr_number}: {exc}") from exc


def fetch_pull_request_files(
    *,
    token: str,
    repository: str,
    pr_number: int,
    api_url: str = DEFAULT_API_URL,
) -> list[dict[str, Any]]:
    """Fetch the list of files changed in a pull request.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        pr_number: Pull request number
        api_url: GitHub API base URL

    Returns:
        List of file change dictionaries with filename, status, additions, deletions, etc.

    Raises:
        GitHubIssueError: If the API request fails
    """

    normalized_repo = normalize_repository(repository)
    owner, name = normalized_repo
    endpoint = f"{api_url}/repos/{owner}/{name}/pulls/{pr_number}/files"

    req = request.Request(endpoint)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except error.HTTPError as exc:
        raise GitHubIssueError(f"Failed to fetch files for PR #{pr_number}: {exc}") from exc
    except Exception as exc:
        raise GitHubIssueError(f"Error fetching files for PR #{pr_number}: {exc}") from exc


def normalize_pr_payload(pr_data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize a PR payload to a consistent format for agent consumption.

    Args:
        pr_data: Raw PR data from GitHub API

    Returns:
        Normalized dictionary with essential PR fields
    """

    return {
        "number": pr_data.get("number"),
        "title": pr_data.get("title", ""),
        "body": pr_data.get("body", ""),
        "state": pr_data.get("state", ""),
        "url": pr_data.get("html_url", ""),
        "head_ref": pr_data.get("head", {}).get("ref", ""),
        "base_ref": pr_data.get("base", {}).get("ref", ""),
        "user": pr_data.get("user", {}).get("login", ""),
        "mergeable": pr_data.get("mergeable"),
        "merged": pr_data.get("merged", False),
        "draft": pr_data.get("draft", False),
        "labels": [label.get("name", "") for label in pr_data.get("labels", [])],
    }


def normalize_pr_files(files_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize PR file changes to a consistent format.

    Args:
        files_data: Raw file change data from GitHub API

    Returns:
        List of normalized file change dictionaries
    """

    normalized = []
    for file_data in files_data:
        normalized.append({
            "filename": file_data.get("filename", ""),
            "status": file_data.get("status", ""),
            "additions": file_data.get("additions", 0),
            "deletions": file_data.get("deletions", 0),
            "changes": file_data.get("changes", 0),
            "patch": file_data.get("patch", ""),
        })
    return normalized


def create_pr_review(
    *,
    token: str,
    repository: str,
    pr_number: int,
    event: str,
    body: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """Create a review on a pull request.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        pr_number: Pull request number
        event: Review event - APPROVE, REQUEST_CHANGES, or COMMENT
        body: Optional review comment body
        api_url: GitHub API base URL

    Returns:
        URL of the created review

    Raises:
        GitHubIssueError: If the API request fails or event is invalid
    """

    valid_events = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}
    if event not in valid_events:
        raise GitHubIssueError(
            f"Review event must be one of: {', '.join(valid_events)}"
        )

    owner, name = normalize_repository(repository)
    endpoint = f"{api_url.rstrip('/')}/repos/{owner}/{name}/pulls/{pr_number}/reviews"
    
    payload: dict[str, Any] = {"event": event}
    if body:
        payload["body"] = body
    
    raw_body = json.dumps(payload).encode("utf-8")
    req = request.Request(endpoint, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return str(data.get("html_url", data.get("url", "")))
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def merge_pull_request(
    *,
    token: str,
    repository: str,
    pr_number: int,
    merge_method: str = "merge",
    commit_title: str | None = None,
    commit_message: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Merge a pull request.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        pr_number: Pull request number
        merge_method: Merge method - merge, squash, or rebase
        commit_title: Optional custom commit title
        commit_message: Optional custom commit message
        api_url: GitHub API base URL

    Returns:
        Dictionary with merge result details

    Raises:
        GitHubIssueError: If the API request fails or merge method is invalid
    """

    valid_methods = {"merge", "squash", "rebase"}
    if merge_method not in valid_methods:
        raise GitHubIssueError(
            f"Merge method must be one of: {', '.join(valid_methods)}"
        )

    owner, name = normalize_repository(repository)
    endpoint = f"{api_url.rstrip('/')}/repos/{owner}/{name}/pulls/{pr_number}/merge"
    
    payload: dict[str, Any] = {"merge_method": merge_method}
    if commit_title:
        payload["commit_title"] = commit_title
    if commit_message:
        payload["commit_message"] = commit_message
    
    raw_body = json.dumps(payload).encode("utf-8")
    req = request.Request(endpoint, data=raw_body, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def create_pull_request(
    *,
    token: str,
    repository: str,
    title: str,
    body: str,
    head: str,
    base: str,
    draft: bool = False,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Create a new pull request.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        title: Title of the pull request
        body: Body/description of the pull request
        head: The name of the branch where your changes are implemented
        base: The name of the branch you want the changes pulled into
        draft: Whether to create as a draft PR (default: False for auto-merge compatibility)
        api_url: GitHub API base URL

    Returns:
        Dictionary containing the created PR details

    Raises:
        GitHubIssueError: If the API request fails
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url.rstrip('/')}/repos/{owner}/{name}/pulls"

    payload = {
        "title": title,
        "body": body,
        "head": head,
        "base": base,
        "draft": draft,
    }

    raw_body = json.dumps(payload).encode("utf-8")
    req = request.Request(endpoint, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def enable_pull_request_auto_merge(
    *,
    token: str,
    pr_node_id: str,
    merge_method: str = "SQUASH",
) -> dict[str, Any]:
    """Enable auto-merge on a pull request using GraphQL API.

    Args:
        token: GitHub API token
        pr_node_id: Pull request node ID (GraphQL ID)
        merge_method: Merge method - MERGE, SQUASH, or REBASE

    Returns:
        Dictionary with auto-merge enablement result

    Raises:
        GitHubIssueError: If the GraphQL request fails
    """
    valid_methods = {"MERGE", "SQUASH", "REBASE"}
    if merge_method not in valid_methods:
        raise GitHubIssueError(
            f"Merge method must be one of: {', '.join(valid_methods)}"
        )

    query = """
    mutation EnableAutoMerge($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
      enablePullRequestAutoMerge(input: {
        pullRequestId: $pullRequestId,
        mergeMethod: $mergeMethod
      }) {
        pullRequest {
          autoMergeRequest {
            enabledAt
            enabledBy {
              login
            }
          }
        }
      }
    }
    """

    variables = {
        "pullRequestId": pr_node_id,
        "mergeMethod": merge_method,
    }

    payload = {
        "query": query,
        "variables": variables,
    }

    data = json.dumps(payload).encode("utf-8")
    req = request.Request(
        "https://api.github.com/graphql",
        data=data,
        method="POST"
    )
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")

    try:
        with request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode())

            if "errors" in result:
                raise GitHubIssueError(
                    f"GraphQL errors: {result['errors']}"
                )

            return result
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"Failed to enable auto-merge: {exc.code} - {error_text}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def approve_pull_request(
    *,
    token: str,
    repository: str,
    pr_number: int,
    body: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Approve a pull request.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        pr_number: Pull request number
        body: Optional review comment body
        api_url: GitHub API base URL

    Returns:
        Dictionary with approval details

    Raises:
        GitHubIssueError: If the API request fails
    """
    return create_pr_review(
        token=token,
        repository=repository,
        pr_number=pr_number,
        event="APPROVE",
        body=body or "✅ Approved",
        api_url=api_url,
    )

