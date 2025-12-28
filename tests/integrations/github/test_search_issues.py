from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from urllib import error

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.integrations.github import search_issues
from src.integrations.github.issues import GitHubIssueError
from src.integrations.github.search_issues import GitHubIssueSearcher


class DummyResponse:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "DummyResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def make_payload(result_count: int = 1) -> dict[str, object]:
    items = []
    for idx in range(result_count):
        items.append(
            {
                "number": idx + 1,
                "title": f"Issue {idx + 1}",
                "state": "open",
                "html_url": f"https://example.com/{idx + 1}",
                "assignee": None,
            }
        )
    return {"items": items}


def test_search_assigned_defaults_to_unassigned(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    def fake_urlopen(req: object):
        captured_urls.append(req.full_url)  # type: ignore[attr-defined]
        return DummyResponse(make_payload())

    monkeypatch.setattr(search_issues.request, "urlopen", fake_urlopen)

    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    results = searcher.search_assigned()

    assert captured_urls
    assert "no%3Aassignee" in captured_urls[0]
    assert "is%3Aopen" in captured_urls[0]
    assert len(results) == 1
    assert results[0].assignee is None


def test_search_assigned_with_user(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    def fake_urlopen(req: object):
        captured_urls.append(req.full_url)  # type: ignore[attr-defined]
        return DummyResponse(make_payload())

    monkeypatch.setattr(search_issues.request, "urlopen", fake_urlopen)

    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    searcher.search_assigned("octocat", limit=10)

    assert captured_urls
    assert "assignee%3Aoctocat" in captured_urls[0]
    assert "per_page=10" in captured_urls[0]
    assert "is%3Aopen" in captured_urls[0]


def test_search_requires_positive_limit() -> None:
    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    with pytest.raises(GitHubIssueError):
        searcher.search_assigned(limit=0)


def test_search_by_label(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    def fake_urlopen(req: object):
        captured_urls.append(req.full_url)  # type: ignore[attr-defined]
        return DummyResponse(make_payload(2))

    monkeypatch.setattr(search_issues.request, "urlopen", fake_urlopen)

    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    results = searcher.search_by_label("bug", limit=200)

    assert captured_urls
    assert "label%3Abug" in captured_urls[0]
    assert "is%3Aopen" in captured_urls[0]
    assert "per_page=100" in captured_urls[0]
    assert len(results) == 2


def test_search_unlabeled(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_urls: list[str] = []

    def fake_urlopen(req: object):
        captured_urls.append(req.full_url)  # type: ignore[attr-defined]
        return DummyResponse(make_payload(3))

    monkeypatch.setattr(search_issues.request, "urlopen", fake_urlopen)

    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    results = searcher.search_unlabeled(limit=5, order="asc")

    assert captured_urls
    url = captured_urls[0]
    assert "no%3Alabel" in url
    assert "sort=created" in url
    assert "order=asc" in url
    assert "per_page=5" in url
    assert len(results) == 3


def test_search_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(req: object):
        raise error.HTTPError(
            req.full_url,  # type: ignore[attr-defined]
            500,
            "boom",
            hdrs=None,  # type: ignore[arg-type]
            fp=io.BytesIO(b"failure"),
        )

    monkeypatch.setattr(search_issues.request, "urlopen", fake_urlopen)

    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")

    with pytest.raises(GitHubIssueError):
        searcher.search_by_label("bug")


def test_search_by_label_requires_label() -> None:
    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    with pytest.raises(GitHubIssueError):
        searcher.search_by_label("")


def test_search_by_body_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that search_by_body_content uses in:body qualifier."""
    captured_urls: list[str] = []

    def fake_urlopen(req: object):
        captured_urls.append(req.full_url)  # type: ignore[attr-defined]
        return DummyResponse(make_payload(1))

    monkeypatch.setattr(search_issues.request, "urlopen", fake_urlopen)

    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    results = searcher.search_by_body_content("monitor-initial:abc123", limit=5)

    assert captured_urls
    url = captured_urls[0]
    # Verify the quoted search term and in:body qualifier are present
    assert "monitor-initial%3Aabc123" in url
    assert "in%3Abody" in url
    assert "is%3Aopen" in url
    assert len(results) == 1


def test_search_by_body_content_requires_text() -> None:
    """Test that search_by_body_content requires non-empty text."""
    searcher = GitHubIssueSearcher(token="token", repository="octocat/hello-world")
    with pytest.raises(GitHubIssueError):
        searcher.search_by_body_content("")
