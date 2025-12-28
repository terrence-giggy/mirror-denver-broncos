"""Helpers for searching GitHub issues."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Mapping, Sequence
from urllib import error, parse, request

from .issues import API_VERSION, DEFAULT_API_URL, GitHubIssueError, normalize_repository


@dataclass(frozen=True)
class IssueSearchResult:
    """Represents a single issue returned by the search API."""

    number: int
    title: str
    state: str
    url: str
    assignee: str | None

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, object]) -> "IssueSearchResult":
        try:
            number = int(payload["number"])  # type: ignore[arg-type]
            title = str(payload.get("title", ""))
            state = str(payload.get("state", ""))
            url = str(payload.get("html_url") or payload.get("url"))
            assignee_payload = payload.get("assignee")
            assignee = None
            if isinstance(assignee_payload, Mapping):
                assignee_login = assignee_payload.get("login")
                if assignee_login is not None:
                    assignee = str(assignee_login)
        except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - protective
            raise GitHubIssueError("Unexpected GitHub response payload") from exc

        if not url:
            raise GitHubIssueError("Issue payload missing URL field")

        return cls(number=number, title=title, state=state, url=url, assignee=assignee)

    def to_dict(self) -> dict[str, object]:
        return {
            "number": self.number,
            "title": self.title,
            "state": self.state,
            "url": self.url,
            "assignee": self.assignee,
        }


class GitHubIssueSearcher:
    """Simple client for searching GitHub issues via the REST API."""

    def __init__(self, *, token: str, repository: str, api_url: str = DEFAULT_API_URL) -> None:
        if not token:
            raise GitHubIssueError("A GitHub token is required for searching issues.")
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._owner, self._name = normalize_repository(repository)

    def search_assigned(
        self, assignee: str | None = None, *, limit: int = 30
    ) -> list[IssueSearchResult]:
        """Return issues assigned to the provided user (or unassigned when omitted)."""

        qualifier = f"assignee:{assignee}" if assignee else "no:assignee"
        return self._search(qualifier, limit=limit)

    def search_by_label(
        self, label: str, *, limit: int = 30
    ) -> list[IssueSearchResult]:
        """Return issues that contain the provided label."""

        if not label:
            raise GitHubIssueError("Label must be provided for label searches.")
        return self.search_with_label_filters(
            required_labels=[label],
            limit=limit,
            sort=None,
            order="desc",
        )

    def search_by_body_content(
        self, body_text: str, *, limit: int = 30
    ) -> list[IssueSearchResult]:
        """Return open issues containing the specified text in their body.
        
        Args:
            body_text: The text to search for in issue bodies.
            limit: Maximum number of results to return.
            
        Returns:
            List of matching issues.
        """
        if not body_text:
            raise GitHubIssueError("Body text must be provided for body searches.")
        # Quote the search term and use in:body qualifier
        quoted_text = f'"{body_text}"'
        qualifier = f"{quoted_text} in:body"
        return self._search(qualifier, limit=limit)

    def search_with_label_filters(
        self,
        *,
        required_labels: Sequence[str] | None = None,
        excluded_labels: Sequence[str] | None = None,
        limit: int = 30,
        sort: str | None = None,
        order: str = "asc",
    ) -> list[IssueSearchResult]:
        """Return open issues matching the provided label filters."""

        order_normalized = order.lower()
        if order_normalized not in {"asc", "desc"}:
            raise GitHubIssueError("Order must be 'asc' or 'desc'.")

        qualifiers: list[str] = []

        if required_labels:
            for label in required_labels:
                label_normalized = label.strip()
                if not label_normalized:
                    continue
                qualifiers.append(f"label:{label_normalized}")

        if excluded_labels:
            for label in excluded_labels:
                label_normalized = label.strip()
                if not label_normalized:
                    continue
                qualifiers.append(f"-label:{label_normalized}")

        if not qualifiers:
            return self.search_unlabeled(limit=limit, order=order_normalized)

        qualifier = " ".join(qualifiers)
        return self._search(qualifier, limit=limit, sort=sort, order=order_normalized)

    def search_unlabeled(
        self,
        *,
        limit: int = 30,
        order: str = "asc",
    ) -> list[IssueSearchResult]:
        """Return open issues that currently have no labels applied."""

        # GitHub search API accepts "sort=created" to order by creation time.
        order_normalized = order.lower()
        if order_normalized not in {"asc", "desc"}:
            raise GitHubIssueError("Order must be 'asc' or 'desc'.")
        return self._search(
            "no:label",
            limit=limit,
            sort="created",
            order=order_normalized,
        )

    def _search(
        self,
        qualifier: str,
        *,
        limit: int,
        sort: str | None = None,
        order: str = "desc",
    ) -> list[IssueSearchResult]:
        if limit < 1:
            raise GitHubIssueError("Search limit must be a positive integer.")
        per_page = max(1, min(limit, 100))
        base_query = f"repo:{self._owner}/{self._name} is:issue is:open"
        query = f"{base_query} {qualifier}".strip()
        params: dict[str, str] = {"q": query, "per_page": str(per_page)}
        if sort:
            params["sort"] = sort
            params["order"] = order
        url = f"{self._api_url}/search/issues?{parse.urlencode(params)}"
        req = request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self._token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", API_VERSION)

        try:
            with request.urlopen(req) as response:  # type: ignore[no-any-unimported]
                body = response.read().decode("utf-8")
        except error.HTTPError as exc:
            error_text = exc.read().decode("utf-8", errors="replace")
            raise GitHubIssueError(
                f"GitHub API error ({exc.code}): {error_text.strip()}"
            ) from exc
        except error.URLError as exc:
            raise GitHubIssueError(f"Failed to reach GitHub API: {exc.reason}") from exc

        payload = json.loads(body)
        items = payload.get("items", [])
        if not isinstance(items, Sequence):  # pragma: no cover - defensive
            raise GitHubIssueError("Unexpected GitHub search response payload.")

        results: list[IssueSearchResult] = []
        for item in items:
            if not isinstance(item, Mapping):  # pragma: no cover - defensive
                raise GitHubIssueError("Unexpected issue entry in search response.")
            results.append(IssueSearchResult.from_api_payload(item))
        return results[:limit]