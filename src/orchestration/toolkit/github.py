"""GitHub tool registrations for the orchestration runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

from src.integrations.github import issues as github_issues
from src.integrations.github.search_issues import GitHubIssueSearcher, IssueSearchResult

from ..safety import ActionRisk
from ..tools import ToolDefinition, ToolRegistry
from ..types import ToolResult


def register_github_read_only_tools(registry: ToolRegistry) -> None:
    """Register safe GitHub read-only tools with the registry."""

    registry.register_tool(
        ToolDefinition(
            name="get_issue_details",
            description="Fetch a GitHub issue and return its key metadata.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue to fetch.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number"],
                "additionalProperties": False,
            },
            handler=_get_issue_details_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_issue_comments",
            description="Fetch all comments for a specific GitHub issue.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number"],
                "additionalProperties": False,
            },
            handler=_get_issue_comments_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="search_issues_by_label",
            description="List open issues that match a specific label.",
            parameters={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Label used to filter issues.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum number of issues to return (1-100).",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                    "api_url": {
                        "type": "string",
                        "description": "Override the GitHub API base URL (for GitHub Enterprise).",
                    },
                },
                "required": ["label"],
                "additionalProperties": False,
            },
            handler=_search_issues_by_label_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="search_issues_assigned",
            description="List open issues assigned to a specific user or unassigned when omitted.",
            parameters={
                "type": "object",
                "properties": {
                    "assignee": {
                        "type": "string",
                        "description": "GitHub username to filter by. Omit to search for unassigned issues.",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "description": "Maximum number of issues to return (1-100).",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                    "api_url": {
                        "type": "string",
                        "description": "Override the GitHub API base URL (for GitHub Enterprise).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_search_issues_assigned_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_ready_for_copilot_issue",
            description="Return the next open issue matching a readiness label.",
            parameters={
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Label to target. Defaults to 'ready-for-copilot'.",
                        "default": "ready-for-copilot",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                    "api_url": {
                        "type": "string",
                        "description": "Override the GitHub API base URL (for GitHub Enterprise).",
                    },
                },
                "required": [],
                "additionalProperties": False,
            },
            handler=_get_ready_for_copilot_issue_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="render_issue_template",
            description="Render an issue body from a template file and optional variables.",
            parameters={
                "type": "object",
                "properties": {
                    "template_path": {
                        "type": "string",
                        "description": "Filesystem path to the issue template file.",
                    },
                    "variables": {
                        "type": "object",
                        "description": "Template variables to substitute when rendering.",
                        "additionalProperties": {
                            "type": "string",
                        },
                    },
                },
                "required": ["template_path"],
                "additionalProperties": False,
            },
            handler=_render_issue_template_handler,
            risk_level=ActionRisk.SAFE,
        )
    )


def _get_issue_details_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        raw_issue = github_issues.fetch_issue(token=token, repository=repository, issue_number=issue_number)
        raw_comments = github_issues.fetch_issue_comments(token=token, repository=repository, issue_number=issue_number)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    normalized = _normalize_issue_payload(raw_issue)
    normalized["comments"] = [_normalize_comment_payload(c) for c in raw_comments]
    return ToolResult(success=True, output=normalized, error=None)


def _get_issue_comments_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        raw_comments = github_issues.fetch_issue_comments(token=token, repository=repository, issue_number=issue_number)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    comments = [_normalize_comment_payload(c) for c in raw_comments]
    return ToolResult(success=True, output=comments, error=None)


def _search_issues_by_label_handler(args: Mapping[str, Any]) -> ToolResult:
    label = args.get("label")
    if not isinstance(label, str) or not label.strip():
        return ToolResult(success=False, output=None, error="label must be a non-empty string.")

    limit = _parse_limit(args.get("limit"))
    if limit is None:
        return ToolResult(success=False, output=None, error="limit must be an integer between 1 and 100.")

    searcher = _build_searcher(args)
    if isinstance(searcher, ToolResult):
        return searcher

    try:
        results = searcher.search_by_label(label, limit=limit)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    return ToolResult(success=True, output=_serialize_issue_results(results), error=None)


def _search_issues_assigned_handler(args: Mapping[str, Any]) -> ToolResult:
    assignee_arg = args.get("assignee")
    if assignee_arg is not None and not isinstance(assignee_arg, str):
        return ToolResult(success=False, output=None, error="assignee must be a string when provided.")

    limit = _parse_limit(args.get("limit"))
    if limit is None:
        return ToolResult(success=False, output=None, error="limit must be an integer between 1 and 100.")

    searcher = _build_searcher(args)
    if isinstance(searcher, ToolResult):
        return searcher

    try:
        results = searcher.search_assigned(assignee_arg, limit=limit)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    return ToolResult(success=True, output=_serialize_issue_results(results), error=None)


def _get_ready_for_copilot_issue_handler(args: Mapping[str, Any]) -> ToolResult:
    label_arg = args.get("label")
    label = str(label_arg).strip() if label_arg else "ready-for-copilot"
    if not label:
        return ToolResult(success=False, output=None, error="label must be a non-empty string when provided.")

    searcher = _build_searcher(args)
    if isinstance(searcher, ToolResult):
        return searcher

    try:
        results = searcher.search_by_label(label, limit=1)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    if not results:
        return ToolResult(success=True, output={"label": label, "issue": None}, error=None)

    issue = results[0]
    return ToolResult(success=True, output={"label": label, "issue": issue.to_dict()}, error=None)


def _render_issue_template_handler(args: Mapping[str, Any]) -> ToolResult:
    template_path_arg = args.get("template_path")
    if not isinstance(template_path_arg, str) or not template_path_arg.strip():
        return ToolResult(success=False, output=None, error="template_path must be a non-empty string.")

    variables_arg = args.get("variables")
    if variables_arg is not None and not isinstance(variables_arg, Mapping):
        return ToolResult(success=False, output=None, error="variables must be an object when provided.")

    variables: dict[str, str] = {}
    if isinstance(variables_arg, Mapping):
        variables = {str(key): str(value) for key, value in variables_arg.items()}

    template_path = Path(template_path_arg).expanduser()
    try:
        template = github_issues.load_template(template_path)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        rendered = github_issues.render_template(template, variables or None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    return ToolResult(success=True, output=rendered, error=None)


def _parse_issue_number(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < 1:
        return None
    return number


def _normalize_issue_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    number = _as_int(payload.get("number"))
    title = _as_str(payload.get("title"))
    state = _as_str(payload.get("state"))
    body = _as_str(payload.get("body"))
    url = _as_str(payload.get("html_url") or payload.get("url"))
    labels = [_extract_label_name(label) for label in _as_sequence(payload.get("labels"))]
    assignees = [_extract_assignee_login(assignee) for assignee in _as_sequence(payload.get("assignees"))]
    author = _extract_author_login(payload.get("user"))

    return {
        "number": number,
        "title": title,
        "state": state,
        "body": body,
        "url": url,
        "author": author,
        "labels": labels,
        "assignees": assignees,
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "closed_at": payload.get("closed_at"),
        "comments": _as_int(payload.get("comments")),
    }


def _normalize_comment_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id"),
        "body": payload.get("body"),
        "author": _extract_author_login(payload.get("user")),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "url": payload.get("html_url"),
    }


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _as_sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    return []


def _extract_label_name(label: Any) -> str:
    if isinstance(label, Mapping):
        name = label.get("name")
        if name is not None:
            return str(name)
    return str(label or "")


def _extract_assignee_login(assignee: Any) -> str:
    if isinstance(assignee, Mapping):
        login = assignee.get("login")
        if login is not None:
            return str(login)
    return str(assignee or "")


def _extract_author_login(author: Any) -> str | None:
    if isinstance(author, Mapping):
        login = author.get("login")
        if login is not None:
            return str(login)
    if author is None:
        return None
    return str(author)


def _build_searcher(args: Mapping[str, Any]) -> GitHubIssueSearcher | ToolResult:
    repository_arg = args.get("repository")
    token_arg = args.get("token")
    api_url_arg = args.get("api_url")

    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    api_url = str(api_url_arg) if api_url_arg else github_issues.DEFAULT_API_URL

    try:
        return GitHubIssueSearcher(token=token, repository=repository, api_url=api_url)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _parse_limit(value: Any) -> int | None:
    if value is None:
        return 30
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    if limit < 1 or limit > 100:
        return None
    return limit


def _serialize_issue_results(results: Sequence[IssueSearchResult]) -> list[dict[str, object]]:
    return [result.to_dict() for result in results]


def register_github_pr_tools(registry: ToolRegistry) -> None:
    """Register GitHub pull request read-only tools with the registry."""

    from src.integrations.github import pull_requests as github_prs

    registry.register_tool(
        ToolDefinition(
            name="get_pr_details",
            description="Fetch a GitHub pull request and return its metadata.",
            parameters={
                "type": "object",
                "properties": {
                    "pr_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the pull request to fetch.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["pr_number"],
                "additionalProperties": False,
            },
            handler=lambda args: _get_pr_details_handler(args, github_prs),
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="get_pr_files",
            description="Fetch the list of files changed in a pull request.",
            parameters={
                "type": "object",
                "properties": {
                    "pr_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the pull request.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["pr_number"],
                "additionalProperties": False,
            },
            handler=lambda args: _get_pr_files_handler(args, github_prs),
            risk_level=ActionRisk.SAFE,
        )
    )


def _get_pr_details_handler(args: Mapping[str, Any], github_prs: Any) -> ToolResult:
    pr_number = _parse_issue_number(args.get("pr_number"))
    if pr_number is None:
        return ToolResult(success=False, output=None, error="pr_number must be an integer >= 1.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        raw_pr = github_prs.fetch_pull_request(token=token, repository=repository, pr_number=pr_number)
        normalized = github_prs.normalize_pr_payload(raw_pr)
        return ToolResult(success=True, output=normalized, error=None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _get_pr_files_handler(args: Mapping[str, Any], github_prs: Any) -> ToolResult:
    pr_number = _parse_issue_number(args.get("pr_number"))
    if pr_number is None:
        return ToolResult(success=False, output=None, error="pr_number must be an integer >= 1.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        raw_files = github_prs.fetch_pull_request_files(token=token, repository=repository, pr_number=pr_number)
        normalized = github_prs.normalize_pr_files(raw_files)
        return ToolResult(success=True, output={"files": normalized, "count": len(normalized)}, error=None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def register_github_mutation_tools(registry: ToolRegistry) -> None:
    """Register GitHub mutation tools (write operations) with the registry."""

    registry.register_tool(
        ToolDefinition(
            name="add_label",
            description="Add one or more labels to a GitHub issue.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "List of label names to add.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number", "labels"],
                "additionalProperties": False,
            },
            handler=_add_label_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="remove_label",
            description="Remove a label from a GitHub issue.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "label": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Label name to remove.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number", "label"],
                "additionalProperties": False,
            },
            handler=_remove_label_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="post_comment",
            description="Post a comment on a GitHub issue or pull request.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue or PR.",
                    },
                    "body": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Markdown content of the comment.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number", "body"],
                "additionalProperties": False,
            },
            handler=_post_comment_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="assign_issue",
            description="Assign one or more users to a GitHub issue.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "assignees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": "List of GitHub usernames to assign.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number", "assignees"],
                "additionalProperties": False,
            },
            handler=_assign_issue_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="assign_issue_to_copilot",
            description=(
                "Assign the GitHub Copilot coding agent to an issue so it can create a PR."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                    "api_url": {
                        "type": "string",
                        "description": "Override the GitHub API base URL (for GitHub Enterprise).",
                    },
                },
                "required": ["issue_number"],
                "additionalProperties": False,
            },
            handler=_assign_issue_to_copilot_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="update_issue_body",
            description="Update the body (description) of a GitHub issue.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "body": {
                        "type": "string",
                        "description": "New Markdown content for the issue body.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number", "body"],
                "additionalProperties": False,
            },
            handler=_update_issue_body_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="close_issue",
            description="Close a GitHub issue with an optional reason comment.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "reason": {
                        "type": "string",
                        "description": "Optional comment explaining why the issue is being closed.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number"],
                "additionalProperties": False,
            },
            handler=_close_issue_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="lock_issue",
            description="Lock a GitHub issue to prevent further comments.",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the issue.",
                    },
                    "lock_reason": {
                        "type": "string",
                        "enum": ["off-topic", "too heated", "resolved", "spam"],
                        "description": "Reason for locking the issue.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["issue_number"],
                "additionalProperties": False,
            },
            handler=_lock_issue_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="approve_pr",
            description="Approve a pull request with an optional comment.",
            parameters={
                "type": "object",
                "properties": {
                    "pr_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the pull request.",
                    },
                    "comment": {
                        "type": "string",
                        "description": "Optional comment to include with the approval.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["pr_number"],
                "additionalProperties": False,
            },
            handler=_approve_pr_handler,
            risk_level=ActionRisk.REVIEW,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="merge_pr",
            description="Merge a pull request using specified merge method.",
            parameters={
                "type": "object",
                "properties": {
                    "pr_number": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Numeric identifier of the pull request.",
                    },
                    "merge_method": {
                        "type": "string",
                        "enum": ["merge", "squash", "rebase"],
                        "description": "Method to use for merging. Defaults to 'merge'.",
                    },
                    "commit_title": {
                        "type": "string",
                        "description": "Optional custom commit title.",
                    },
                    "commit_message": {
                        "type": "string",
                        "description": "Optional custom commit message.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["pr_number"],
                "additionalProperties": False,
            },
            handler=_merge_pr_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="create_pull_request",
            description="Create a new pull request. By default creates a non-draft PR ready for auto-merge.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title of the pull request."},
                    "body": {"type": "string", "description": "Body/description of the pull request."},
                    "head": {"type": "string", "description": "The name of the branch where your changes are implemented."},
                    "base": {"type": "string", "description": "The name of the branch you want the changes pulled into."},
                    "draft": {
                        "type": "boolean",
                        "description": "Whether to create as a draft PR. Default: false (non-draft for auto-merge compatibility).",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["title", "body", "head", "base"],
                "additionalProperties": False,
            },
            handler=_create_pr_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="commit_file",
            description="Create or update a file in the repository.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "content": {"type": "string", "description": "Content of the file."},
                    "message": {"type": "string", "description": "Commit message."},
                    "branch": {"type": "string", "description": "Branch to commit to."},
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["path", "content", "message", "branch"],
                "additionalProperties": False,
            },
            handler=_commit_file_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="read_file_content",
            description="Read the content of a file from the repository.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file."},
                    "ref": {
                        "type": "string",
                        "description": "Git reference (branch, tag, or commit SHA). Uses default branch if not provided.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with read access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            handler=_read_file_content_handler,
            risk_level=ActionRisk.SAFE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="create_branch",
            description="Create a new branch from an existing branch.",
            parameters={
                "type": "object",
                "properties": {
                    "branch_name": {"type": "string", "description": "Name of the new branch."},
                    "from_branch": {
                        "type": "string",
                        "description": "Branch to create from. Defaults to 'main'.",
                    },
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["branch_name"],
                "additionalProperties": False,
            },
            handler=_create_branch_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )

    registry.register_tool(
        ToolDefinition(
            name="commit_files_batch",
            description="Commit multiple files in a single atomic commit using the Git Trees API.",
            parameters={
                "type": "object",
                "properties": {
                    "files": {
                        "type": "array",
                        "description": "List of file objects with 'path' and 'content' properties.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Path to the file."},
                                "content": {"type": "string", "description": "Content of the file."},
                            },
                            "required": ["path", "content"],
                        },
                    },
                    "message": {"type": "string", "description": "Commit message for the batch."},
                    "branch": {"type": "string", "description": "Branch to commit to."},
                    "repository": {
                        "type": "string",
                        "description": "Repository name in 'owner/name' format. Defaults to GITHUB_REPOSITORY env var.",
                    },
                    "token": {
                        "type": "string",
                        "description": "GitHub token with write access. Defaults to GITHUB_TOKEN env var.",
                    },
                },
                "required": ["files", "message", "branch"],
                "additionalProperties": False,
            },
            handler=_commit_files_batch_handler,
            risk_level=ActionRisk.DESTRUCTIVE,
        )
    )


# Handler implementations for mutation tools

def _add_label_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    labels = args.get("labels")
    if not isinstance(labels, list) or not labels:
        return ToolResult(success=False, output=None, error="labels must be a non-empty list.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        github_issues.add_labels(
            token=token,
            repository=repository,
            issue_number=issue_number,
            labels=[str(lbl) for lbl in labels],
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "labels_added": labels},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _remove_label_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    label = args.get("label")
    if not isinstance(label, str) or not label:
        return ToolResult(success=False, output=None, error="label must be a non-empty string.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        github_issues.remove_label(
            token=token,
            repository=repository,
            issue_number=issue_number,
            label=str(label),
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "label_removed": label},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _post_comment_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    body = args.get("body")
    if not isinstance(body, str) or not body:
        return ToolResult(success=False, output=None, error="body must be a non-empty string.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        # Append hidden marker to identify agent responses
        final_body = f"{str(body)}\n\n<!-- agent-response -->"
        
        comment_url = github_issues.post_comment(
            token=token,
            repository=repository,
            issue_number=issue_number,
            body=final_body,
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "comment_url": comment_url},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _assign_issue_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    assignees = args.get("assignees")
    if not isinstance(assignees, list) or not assignees:
        return ToolResult(success=False, output=None, error="assignees must be a non-empty list.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        github_issues.assign_issue(
            token=token,
            repository=repository,
            issue_number=issue_number,
            assignees=[str(a) for a in assignees],
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "assignees": assignees},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _update_issue_body_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    body = args.get("body")
    if not isinstance(body, str):
        return ToolResult(success=False, output=None, error="body must be a string.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        github_issues.update_issue(
            token=token,
            repository=repository,
            issue_number=issue_number,
            body=str(body),
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "updated": True},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _assign_issue_to_copilot_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    api_url_arg = args.get("api_url")

    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    api_url = str(api_url_arg) if api_url_arg else github_issues.DEFAULT_API_URL

    try:
        github_issues.assign_issue_to_copilot(
            token=token,
            repository=repository,
            issue_number=issue_number,
            api_url=api_url,
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "assignee": "copilot-swe-agent"},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _close_issue_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    reason = args.get("reason")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        # Post reason comment if provided
        if reason and isinstance(reason, str):
            github_issues.post_comment(
                token=token,
                repository=repository,
                issue_number=issue_number,
                body=str(reason),
            )
        
        # Close the issue
        github_issues.update_issue(
            token=token,
            repository=repository,
            issue_number=issue_number,
            state="closed",
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "closed": True},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _lock_issue_handler(args: Mapping[str, Any]) -> ToolResult:
    issue_number = _parse_issue_number(args.get("issue_number"))
    if issue_number is None:
        return ToolResult(success=False, output=None, error="issue_number must be an integer >= 1.")
    
    lock_reason = args.get("lock_reason")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        github_issues.lock_issue(
            token=token,
            repository=repository,
            issue_number=issue_number,
            lock_reason=str(lock_reason) if lock_reason else None,
        )
        return ToolResult(
            success=True,
            output={"issue_number": issue_number, "locked": True},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _approve_pr_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import pull_requests as github_prs
    
    pr_number = _parse_issue_number(args.get("pr_number"))
    if pr_number is None:
        return ToolResult(success=False, output=None, error="pr_number must be an integer >= 1.")
    
    comment = args.get("comment")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        review_url = github_prs.create_pr_review(
            token=token,
            repository=repository,
            pr_number=pr_number,
            event="APPROVE",
            body=str(comment) if comment else None,
        )
        return ToolResult(
            success=True,
            output={"pr_number": pr_number, "review_url": review_url, "approved": True},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _merge_pr_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import pull_requests as github_prs
    
    pr_number = _parse_issue_number(args.get("pr_number"))
    if pr_number is None:
        return ToolResult(success=False, output=None, error="pr_number must be an integer >= 1.")
    
    merge_method = args.get("merge_method", "merge")
    commit_title = args.get("commit_title")
    commit_message = args.get("commit_message")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        result = github_prs.merge_pull_request(
            token=token,
            repository=repository,
            pr_number=pr_number,
            merge_method=str(merge_method),
            commit_title=str(commit_title) if commit_title else None,
            commit_message=str(commit_message) if commit_message else None,
        )
        return ToolResult(
            success=True,
            output={"pr_number": pr_number, "merged": True, "sha": result.get("sha")},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _create_pr_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import pull_requests as github_prs
    
    title = args.get("title")
    body = args.get("body")
    head = args.get("head")
    base = args.get("base")
    draft = args.get("draft", False)  # Default to non-draft for auto-merge
    
    if not all([title, body, head, base]):
        return ToolResult(success=False, output=None, error="title, body, head, and base are required.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        result = github_prs.create_pull_request(
            token=token,
            repository=repository,
            title=str(title),
            body=str(body),
            head=str(head),
            base=str(base),
            draft=bool(draft),
        )
        return ToolResult(
            success=True,
            output=result,
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _commit_file_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import files as github_files
    
    path = args.get("path")
    content = args.get("content")
    message = args.get("message")
    branch = args.get("branch")
    
    if not all([path, content, message, branch]):
        return ToolResult(success=False, output=None, error="path, content, message, and branch are required.")

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        result = github_files.commit_file(
            token=token,
            repository=repository,
            path=str(path),
            content=str(content),
            message=str(message),
            branch=str(branch),
        )
        return ToolResult(
            success=True,
            output=result,
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _read_file_content_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import files as github_files
    
    path = args.get("path")
    if not path:
        return ToolResult(success=False, output=None, error="path is required.")

    ref = args.get("ref")
    repository_arg = args.get("repository")
    token_arg = args.get("token")
    
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        content, sha = github_files.get_file_content(
            token=token,
            repository=repository,
            path=str(path),
            ref=str(ref) if ref else None,
        )
        return ToolResult(
            success=True,
            output={"content": content, "sha": sha, "path": path},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _create_branch_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import sync as github_sync
    
    branch_name = args.get("branch_name")
    if not branch_name:
        return ToolResult(success=False, output=None, error="branch_name is required.")

    from_branch = args.get("from_branch", "main")
    repository_arg = args.get("repository")
    token_arg = args.get("token")
    
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        sha = github_sync.create_branch(
            repository=repository,
            branch_name=str(branch_name),
            from_branch=str(from_branch),
            token=token,
        )
        return ToolResult(
            success=True,
            output={"branch_name": branch_name, "sha": sha, "from_branch": from_branch},
            error=None,
        )
    except Exception as exc:
        return ToolResult(success=False, output=None, error=str(exc))


def _commit_files_batch_handler(args: Mapping[str, Any]) -> ToolResult:
    from src.integrations.github import files as github_files
    
    files = args.get("files")
    message = args.get("message")
    branch = args.get("branch")
    
    if not files or not isinstance(files, list):
        return ToolResult(success=False, output=None, error="files must be a non-empty list.")
    if not message:
        return ToolResult(success=False, output=None, error="message is required.")
    if not branch:
        return ToolResult(success=False, output=None, error="branch is required.")

    # Convert files array of objects to list of tuples
    file_tuples = []
    for f in files:
        if not isinstance(f, dict) or "path" not in f or "content" not in f:
            return ToolResult(success=False, output=None, error="Each file must have 'path' and 'content' properties.")
        file_tuples.append((str(f["path"]), str(f["content"])))

    repository_arg = args.get("repository")
    token_arg = args.get("token")
    
    try:
        repository = github_issues.resolve_repository(str(repository_arg) if repository_arg else None)
        token = github_issues.resolve_token(str(token_arg) if token_arg else None)
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))

    try:
        result = github_files.commit_files_batch(
            token=token,
            repository=repository,
            files=file_tuples,
            message=str(message),
            branch=str(branch),
        )
        return ToolResult(
            success=True,
            output={"commit_sha": result.get("sha"), "files_count": len(file_tuples)},
            error=None,
        )
    except github_issues.GitHubIssueError as exc:
        return ToolResult(success=False, output=None, error=str(exc))


