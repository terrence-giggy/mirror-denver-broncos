"""Helpers for creating GitHub issues programmatically."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence
from urllib import error, request
from urllib.parse import urlparse

DEFAULT_API_URL = "https://api.github.com"
API_VERSION = "2022-11-28"
AGENT_RESPONSE_TAG = "\n\n<!-- agent-response -->"


class GitHubIssueError(RuntimeError):
    """Raised when the GitHub API returns an error."""


@dataclass(frozen=True)
class IssueOutcome:
    """Represents the response from a successful issue creation."""

    number: int
    url: str
    html_url: str

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, object]) -> "IssueOutcome":
        try:
            number = int(payload["number"])  # type: ignore[arg-type]
            url = str(payload["url"])
            html_url = str(payload.get("html_url", url))
        except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - protective
            raise GitHubIssueError("Unexpected GitHub response payload") from exc
        return cls(number=number, url=url, html_url=html_url)


def normalize_repository(repository: str | None) -> tuple[str, str]:
    """Split an ``owner/repo`` string into its two components."""

    if not repository:
        raise GitHubIssueError("Repository must be provided as 'owner/repo'.")
    owner, sep, name = repository.partition("/")
    if not sep or not owner or not name:
        raise GitHubIssueError(f"Invalid repository format: {repository!r}")
    return owner, name


def _get_repository_from_git() -> str | None:
    """Extract repository owner/name from git remote URL."""
    try:
        result = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        remote_url = result.stdout.strip()
        
        # Handle SSH URLs: git@github.com:owner/repo.git
        if remote_url.startswith("git@github.com:"):
            repo_path = remote_url.replace("git@github.com:", "")
            repo_path = repo_path.removesuffix(".git")
            return repo_path
        
        # Handle HTTPS URLs: https://github.com/owner/repo.git
        # Handle HTTPS URLs: https://github.com/owner/repo.git
        try:
            parsed = urlparse(remote_url)
            # Accept only if host is github.com
            if parsed.hostname and parsed.hostname.lower() == "github.com":
                repo_path = parsed.path.lstrip("/")
                repo_path = repo_path.removesuffix(".git")
                return repo_path
        except Exception:
            pass  # Fall through to return None if parsing fails
        
        return None
    except (subprocess.SubprocessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def resolve_repository(explicit_repo: str | None) -> str:
    """Return the repository name, preferring explicit input over the environment."""

    if explicit_repo:
        return explicit_repo
    
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    
    # Fall back to detecting from git configuration
    repo = _get_repository_from_git()
    if repo:
        return repo
    
    raise GitHubIssueError(
        "Repository not provided; set --repo, the GITHUB_REPOSITORY environment variable, "
        "or ensure you're in a git repository with a GitHub remote."
    )


def resolve_token(explicit_token: str | None) -> str:
    """Return the token, preferring explicit input over the environment."""

    if explicit_token:
        return explicit_token
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GitHubIssueError(
            "Token not provided; set --token or the GH_TOKEN/GITHUB_TOKEN environment variable."
        )
    return token


def load_template(template_path: Path) -> str:
    """Read the template file as UTF-8 text."""

    if not template_path.exists():
        raise GitHubIssueError(f"Template not found: {template_path}")
    return template_path.read_text(encoding="utf-8")


def render_template(template: str, variables: Mapping[str, str] | None = None) -> str:
    """Inject variables into the template body using ``str.format`` semantics."""

    if not variables:
        return template
    try:
        return template.format(**variables)
    except KeyError as exc:
        missing = ", ".join(sorted(exc.args))
        raise GitHubIssueError(f"Missing template variables: {missing}") from exc


def get_repository_details(
    *,
    token: str,
    repository: str,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, object]:
    """Fetch repository details from GitHub API."""
    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}"

    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req) as response:
            response_bytes = response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

    return json.loads(response_bytes.decode("utf-8"))


def create_issue(
    *,
    token: str,
    repository: str,
    title: str,
    body: str,
    api_url: str = DEFAULT_API_URL,
    labels: Sequence[str] | None = None,
    assignees: Sequence[str] | None = None,
) -> IssueOutcome:
    """Create a GitHub issue and return the result."""

    if "<!-- agent-response -->" not in body:
        body += AGENT_RESPONSE_TAG

    owner, name = normalize_repository(repository)
    payload: dict[str, object] = {"title": title, "body": body}
    if labels:
        payload["labels"] = list(labels)
    if assignees:
        payload["assignees"] = list(assignees)

    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues"
    raw_body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response_bytes = response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

    data = json.loads(response_bytes.decode("utf-8"))
    return IssueOutcome.from_api_payload(data)


def _graphql_endpoint(api_url: str) -> str:
    normalized = api_url.rstrip("/")
    if normalized.endswith("/api/v3"):
        return f"{normalized[:-len('/api/v3')]}/api/graphql"
    return f"{normalized}/graphql"


def _graphql_request(
    *,
    token: str,
    api_url: str,
    query: str,
    variables: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    payload: dict[str, object] = {"query": query}
    if variables:
        payload["variables"] = dict(variables)

    url = _graphql_endpoint(api_url)
    raw_body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response_bytes = response.read()
    except error.HTTPError as exc:  # pragma: no cover - network failure safeguard
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub GraphQL error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:  # pragma: no cover - network failure safeguard
        raise GitHubIssueError(f"Failed to reach GitHub GraphQL API: {exc.reason}") from exc

    data = json.loads(response_bytes.decode("utf-8"))
    if "errors" in data:
        formatted = "; ".join(
            message.get("message", "Unknown GraphQL error")  # type: ignore[assignment]
            for message in data.get("errors", [])  # type: ignore[assignment]
            if isinstance(message, Mapping)
        )
        raise GitHubIssueError(formatted or "GitHub GraphQL reported errors.")

    output = data.get("data")
    if not isinstance(output, Mapping):  # pragma: no cover - defensive
        raise GitHubIssueError("Unexpected GitHub GraphQL payload.")
    return output


def fetch_issue(
    *,
    token: str,
    repository: str,
    issue_number: int,
    api_url: str = DEFAULT_API_URL,
) -> Mapping[str, object]:
    """Return the raw API payload for a GitHub issue."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}"
    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req) as response:
            response_bytes = response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

    payload = json.loads(response_bytes.decode("utf-8"))
    if not isinstance(payload, Mapping):  # pragma: no cover - defensive
        raise GitHubIssueError("Unexpected GitHub issue payload type.")
    return payload


def fetch_issue_comments(
    *,
    token: str,
    repository: str,
    issue_number: int,
    api_url: str = DEFAULT_API_URL,
) -> Sequence[Mapping[str, object]]:
    """Return the list of comments for a GitHub issue."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}/comments"
    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req) as response:
            response_bytes = response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

    payload = json.loads(response_bytes.decode("utf-8"))
    if not isinstance(payload, Sequence):
        raise GitHubIssueError("Unexpected GitHub comments payload type.")
    return payload


def assign_issue_to_copilot(
    *,
    token: str,
    repository: str,
    issue_number: int,
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Assign the GitHub Copilot coding agent to an existing issue."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")

    owner, name = normalize_repository(repository)
    variables = {"owner": owner, "name": name, "issueNumber": issue_number}
    query = """
    query($owner: String!, $name: String!, $issueNumber: Int!) {
      repository(owner: $owner, name: $name) {
        id
        issue(number: $issueNumber) {
          id
        }
        suggestedActors(capabilities: [CAN_BE_ASSIGNED], first: 100) {
          nodes {
            login
            __typename
            ... on Bot { id }
            ... on User { id }
          }
        }
      }
    }
    """

    data = _graphql_request(token=token, api_url=api_url, query=query, variables=variables)
    repository_data = data.get("repository") if isinstance(data, Mapping) else None
    if not isinstance(repository_data, Mapping):
        raise GitHubIssueError("Repository information missing from GraphQL response.")

    issue_data = repository_data.get("issue")
    if not isinstance(issue_data, Mapping) or not issue_data.get("id"):
        raise GitHubIssueError("Issue not found or inaccessible for Copilot assignment.")
    issue_id = str(issue_data["id"])

    suggested = repository_data.get("suggestedActors")
    nodes: Sequence[object] | None = None
    if isinstance(suggested, Mapping):
        raw_nodes = suggested.get("nodes", [])  # type: ignore[assignment]
        if isinstance(raw_nodes, Sequence):
            nodes = raw_nodes

    copilot_id: str | None = None
    if nodes:
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            login = str(node.get("login", ""))
            if login.lower() in {"copilot-swe-agent", "github-copilot"}:
                node_id = node.get("id")
                if node_id:
                    copilot_id = str(node_id)
                    break

    if not copilot_id:
        raise GitHubIssueError(
            "Copilot coding agent is not enabled for this repository or account."
        )

    mutation = """
    mutation($assignableId: ID!, $actorIds: [ID!]!) {
      replaceActorsForAssignable(input: {assignableId: $assignableId, actorIds: $actorIds}) {
        assignable {
          ... on Issue {
            id
          }
        }
      }
    }
    """

    mutation_data = _graphql_request(
        token=token,
        api_url=api_url,
        query=mutation,
        variables={"assignableId": issue_id, "actorIds": [copilot_id]},
    )

    assignment = mutation_data.get("replaceActorsForAssignable")
    if isinstance(assignment, Mapping):
        assignable = assignment.get("assignable")
        if isinstance(assignable, Mapping) and assignable.get("id"):
            return

    raise GitHubIssueError("Copilot assignment failed to confirm the issue was updated.")


def add_labels(
    *,
    token: str,
    repository: str,
    issue_number: int,
    labels: Sequence[str],
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Add one or more labels to a GitHub issue."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")
    if not labels:
        raise GitHubIssueError("At least one label must be provided.")

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}/labels"
    payload = {"labels": list(labels)}
    raw_body = json.dumps(payload).encode("utf-8")
    
    req = request.Request(url, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def remove_label(
    *,
    token: str,
    repository: str,
    issue_number: int,
    label: str,
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Remove a label from a GitHub issue."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")
    if not label:
        raise GitHubIssueError("Label name must be provided.")

    owner, name = normalize_repository(repository)
    # URL encode the label name
    from urllib.parse import quote
    encoded_label = quote(label, safe='')
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}/labels/{encoded_label}"
    
    req = request.Request(url, method="DELETE")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def post_comment(
    *,
    token: str,
    repository: str,
    issue_number: int,
    body: str,
    api_url: str = DEFAULT_API_URL,
) -> str:
    """Post a comment on a GitHub issue and return the comment URL."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")
    if not body:
        raise GitHubIssueError("Comment body must be provided.")

    if "<!-- agent-response -->" not in body:
        body += AGENT_RESPONSE_TAG

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}/comments"
    payload = {"body": body}
    raw_body = json.dumps(payload).encode("utf-8")
    
    req = request.Request(url, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response_bytes = response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

    data = json.loads(response_bytes.decode("utf-8"))
    if not isinstance(data, Mapping):  # pragma: no cover - defensive
        raise GitHubIssueError("Unexpected comment response payload type.")
    
    return str(data.get("html_url", data.get("url", "")))


def assign_issue(
    *,
    token: str,
    repository: str,
    issue_number: int,
    assignees: Sequence[str],
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Assign one or more users to a GitHub issue."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")
    if not assignees:
        raise GitHubIssueError("At least one assignee must be provided.")

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}/assignees"
    payload = {"assignees": list(assignees)}
    raw_body = json.dumps(payload).encode("utf-8")
    
    req = request.Request(url, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def update_issue(
    *,
    token: str,
    repository: str,
    issue_number: int,
    title: str | None = None,
    body: str | None = None,
    state: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Update a GitHub issue's title, body, or state."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")
    if not any([title, body, state]):
        raise GitHubIssueError("At least one field (title, body, state) must be provided.")
    if state and state not in ("open", "closed"):
        raise GitHubIssueError("State must be 'open' or 'closed'.")

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}"
    payload: dict[str, object] = {}
    if title is not None:
        payload["title"] = title
    if body is not None:
        payload["body"] = body
    if state is not None:
        payload["state"] = state
    
    raw_body = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=raw_body, method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


# -----------------------------------------------------------------------------
# Repository Label Management
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelInfo:
    """Represents a repository label with its metadata."""

    name: str
    color: str
    description: str


# Required labels for project operations with their metadata
REQUIRED_LABELS: tuple[LabelInfo, ...] = (
    LabelInfo(
        name="setup",
        color="0e8a16",  # Green
        description="Repository setup and configuration",
    ),
    LabelInfo(
        name="question",
        color="d876e3",  # Purple
        description="Question requiring agent response",
    ),
    LabelInfo(
        name="source-approved",
        color="0052cc",  # Blue
        description="Approved source pending implementation",
    ),
    LabelInfo(
        name="source-proposal",
        color="fbca04",  # Yellow
        description="Proposed source under review",
    ),
    LabelInfo(
        name="wontfix",
        color="ffffff",  # White
        description="This will not be worked on",
    ),
)


def get_repository_labels(
    *,
    token: str,
    repository: str,
    api_url: str = DEFAULT_API_URL,
) -> list[dict[str, str]]:
    """Get all labels from a repository."""
    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/labels?per_page=100"

    req = request.Request(url, method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)

    try:
        with request.urlopen(req) as response:
            data = json.loads(response.read().decode("utf-8"))
            return [{"name": lbl["name"], "color": lbl.get("color", ""), "description": lbl.get("description", "")} for lbl in data]
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def create_label(
    *,
    token: str,
    repository: str,
    name: str,
    color: str,
    description: str = "",
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Create a new label in a repository."""
    if not name:
        raise GitHubIssueError("Label name must be provided.")
    if not color:
        raise GitHubIssueError("Label color must be provided.")

    owner, repo_name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{repo_name}/labels"
    payload = {
        "name": name,
        "color": color.lstrip("#"),  # GitHub API expects color without #
        "description": description,
    }
    raw_body = json.dumps(payload).encode("utf-8")

    req = request.Request(url, data=raw_body, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def update_label(
    *,
    token: str,
    repository: str,
    name: str,
    color: str | None = None,
    description: str | None = None,
    new_name: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Update an existing label in a repository."""
    if not name:
        raise GitHubIssueError("Label name must be provided.")

    owner, repo_name = normalize_repository(repository)
    from urllib.parse import quote
    encoded_name = quote(name, safe="")
    url = f"{api_url.rstrip('/')}/repos/{owner}/{repo_name}/labels/{encoded_name}"

    payload: dict[str, str] = {}
    if new_name is not None:
        payload["new_name"] = new_name
    if color is not None:
        payload["color"] = color.lstrip("#")
    if description is not None:
        payload["description"] = description

    if not payload:
        return  # Nothing to update

    raw_body = json.dumps(payload).encode("utf-8")

    req = request.Request(url, data=raw_body, method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc


def ensure_required_labels(
    *,
    token: str,
    repository: str,
    api_url: str = DEFAULT_API_URL,
) -> dict[str, list[str]]:
    """Ensure all required labels exist in the repository.

    Creates missing labels and optionally updates existing ones if metadata differs.

    Returns:
        Dictionary with 'created' and 'existing' label name lists.
    """
    existing_labels = get_repository_labels(
        token=token, repository=repository, api_url=api_url
    )
    existing_names = {lbl["name"].lower(): lbl for lbl in existing_labels}

    created: list[str] = []
    existing: list[str] = []

    for label in REQUIRED_LABELS:
        label_lower = label.name.lower()
        if label_lower in existing_names:
            existing.append(label.name)
        else:
            create_label(
                token=token,
                repository=repository,
                name=label.name,
                color=label.color,
                description=label.description,
                api_url=api_url,
            )
            created.append(label.name)

    return {"created": created, "existing": existing}


def lock_issue(
    *,
    token: str,
    repository: str,
    issue_number: int,
    lock_reason: str | None = None,
    api_url: str = DEFAULT_API_URL,
) -> None:
    """Lock a GitHub issue to prevent new comments."""

    if issue_number < 1:
        raise GitHubIssueError("Issue number must be a positive integer.")
    
    valid_reasons = {"off-topic", "too heated", "resolved", "spam"}
    if lock_reason and lock_reason not in valid_reasons:
        raise GitHubIssueError(
            f"Lock reason must be one of: {', '.join(valid_reasons)}"
        )

    owner, name = normalize_repository(repository)
    url = f"{api_url.rstrip('/')}/repos/{owner}/{name}/issues/{issue_number}/lock"
    
    payload: dict[str, object] = {}
    if lock_reason:
        payload["lock_reason"] = lock_reason
    
    raw_body = json.dumps(payload).encode("utf-8") if payload else b""
    req = request.Request(url, data=raw_body, method="PUT")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", API_VERSION)
    if payload:
        req.add_header("Content-Type", "application/json; charset=utf-8")

    try:
        with request.urlopen(req) as response:
            response.read()
    except error.HTTPError as exc:
        error_text = exc.read().decode("utf-8", errors="replace")
        raise GitHubIssueError(
            f"GitHub API error ({exc.code}): {error_text.strip()}"
        ) from exc
    except error.URLError as exc:
        raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc