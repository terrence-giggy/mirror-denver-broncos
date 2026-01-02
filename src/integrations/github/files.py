"""GitHub file manipulation helpers."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib import error, request

from .issues import API_VERSION, DEFAULT_API_URL, GitHubIssueError, normalize_repository


def get_file_content(
    *,
    token: str,
    repository: str,
    path: str,
    ref: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> tuple[str, str]:
    """Get the content and SHA of a file from a repository.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        path: Path to the file
        ref: Git reference (branch, tag, or commit SHA). Uses default branch if not provided.
        api_url: GitHub API base URL

    Returns:
        Tuple of (decoded_content, sha)

    Raises:
        GitHubIssueError: If the file is not found or API request fails.
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url.rstrip('/')}/repos/{owner}/{name}/contents/{path}"
    if ref:
        endpoint = f"{endpoint}?ref={ref}"

    req = request.Request(endpoint)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
            content_b64 = data.get("content", "")
            # GitHub returns base64 with newlines, so we need to handle that
            content = base64.b64decode(content_b64).decode("utf-8")
            sha = data["sha"]
            return content, sha
    except error.HTTPError as exc:
        if exc.code == 404:
            raise GitHubIssueError(f"File not found: {path}") from exc
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

def commit_file(
    *,
    token: str,
    repository: str,
    path: str,
    content: str | bytes,
    message: str,
    branch: str,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Create or update a file in a repository.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        path: Path to the file
        content: Content of the file (string or bytes)
        message: Commit message
        branch: Branch to commit to
        api_url: GitHub API base URL

    Returns:
        Dictionary containing the commit details
    """
    owner, name = normalize_repository(repository)
    endpoint = f"{api_url.rstrip('/')}/repos/{owner}/{name}/contents/{path}"

    # If content is string, encode to bytes
    if isinstance(content, str):
        content_bytes = content.encode("utf-8")
    else:
        content_bytes = content

    encoded_content = base64.b64encode(content_bytes).decode("utf-8")

    payload = {
        "message": message,
        "content": encoded_content,
        "branch": branch,
    }

    # Check if file exists to get SHA (needed for update)
    try:
        get_req = request.Request(f"{endpoint}?ref={branch}")
        get_req.add_header("Authorization", f"Bearer {token}")
        get_req.add_header("Accept", "application/vnd.github+json")
        get_req.add_header("X-GitHub-Api-Version", API_VERSION)
        
        with request.urlopen(get_req) as response:
            data = json.loads(response.read().decode("utf-8"))
            payload["sha"] = data["sha"]
    except error.HTTPError as exc:
        if exc.code != 404:
             raise GitHubIssueError(f"Failed to check file existence: {exc}")
    except Exception:
        pass # File doesn't exist, proceed with creation

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


def commit_files_batch(
    *,
    token: str,
    repository: str,
    files: list[tuple[str, str | bytes]],
    message: str,
    branch: str,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, Any]:
    """Create or update multiple files in a single commit using the Git Trees API.

    This is more efficient than multiple individual commit_file calls and creates
    a cleaner git history with a single commit for all changes.

    Args:
        token: GitHub API token
        repository: Repository in "owner/repo" format
        files: List of (path, content) tuples
        message: Commit message
        branch: Branch to commit to
        api_url: GitHub API base URL

    Returns:
        Dictionary containing the commit details

    Raises:
        GitHubIssueError: If the API request fails
    """
    if not files:
        raise GitHubIssueError("No files provided for batch commit")

    owner, name = normalize_repository(repository)
    base_url = f"{api_url.rstrip('/')}/repos/{owner}/{name}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
        "Content-Type": "application/json; charset=utf-8",
    }

    def api_request(endpoint: str, method: str = "GET", data: dict | None = None) -> dict:
        url = f"{base_url}/{endpoint}"
        body = json.dumps(data).encode("utf-8") if data else None
        req = request.Request(url, data=body, method=method)
        for key, value in headers.items():
            req.add_header(key, value)
        try:
            with request.urlopen(req, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            raise GitHubIssueError(
                f"GitHub API error ({exc.code}) at {endpoint}: {error_text.strip()}"
            ) from exc
        except error.URLError as exc:
            raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

    # Step 1: Get the current commit SHA for the branch
    ref_data = api_request(f"git/refs/heads/{branch}")
    current_commit_sha = ref_data["object"]["sha"]

    # Step 2: Get the tree SHA for the current commit
    commit_data = api_request(f"git/commits/{current_commit_sha}")
    base_tree_sha = commit_data["tree"]["sha"]

    # Step 3: Create blobs for each file and build tree entries
    tree_entries = []
    for path, content in files:
        # Normalize path
        if path.startswith("/"):
            path = path[1:]

        # Encode content
        if isinstance(content, str):
            content_bytes = content.encode("utf-8")
        else:
            content_bytes = content

        encoded_content = base64.b64encode(content_bytes).decode("utf-8")

        # Create blob
        blob_data = api_request(
            "git/blobs",
            method="POST",
            data={"content": encoded_content, "encoding": "base64"},
        )

        tree_entries.append({
            "path": path,
            "mode": "100644",  # Regular file
            "type": "blob",
            "sha": blob_data["sha"],
        })

    # Step 4: Create new tree with the changes
    tree_data = api_request(
        "git/trees",
        method="POST",
        data={"base_tree": base_tree_sha, "tree": tree_entries},
    )
    new_tree_sha = tree_data["sha"]

    # Step 5: Create new commit
    commit_response = api_request(
        "git/commits",
        method="POST",
        data={
            "message": message,
            "tree": new_tree_sha,
            "parents": [current_commit_sha],
        },
    )
    new_commit_sha = commit_response["sha"]

    # Step 6: Update the branch reference with retry on 'not a fast forward'
    max_retries = 3
    for attempt in range(max_retries):
        try:
            # On retry, use force flag to handle concurrent updates
            update_data = {"sha": new_commit_sha}
            if attempt > 0:
                update_data["force"] = True
            
            api_request(
                f"git/refs/heads/{branch}",
                method="PATCH",
                data=update_data,
            )
            break  # Success, exit retry loop
            
        except GitHubIssueError as e:
            if "not a fast forward" in str(e).lower() and attempt < max_retries - 1:
                # Branch was updated between read and write, retry
                # Refetch the latest commit and recreate our commit on top
                ref_data = api_request(f"git/refs/heads/{branch}")
                latest_commit_sha = ref_data["object"]["sha"]
                
                # Recreate commit with updated parent
                commit_response = api_request(
                    "git/commits",
                    method="POST",
                    data={
                        "message": message,
                        "tree": new_tree_sha,
                        "parents": [latest_commit_sha],
                    },
                )
                new_commit_sha = commit_response["sha"]
                # Loop will retry the PATCH
            else:
                raise  # Re-raise if not a fast-forward error or out of retries

    return {
        "commit": commit_response,
        "tree": tree_data,
        "files_count": len(files),
        "sha": new_commit_sha,
    }
