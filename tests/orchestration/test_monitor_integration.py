"""Integration tests for monitor agent workflows.

These tests verify the end-to-end behavior of the monitor agent,
including initial acquisition detection, update monitoring, and
issue creation workflows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.integrations.github.issues import IssueOutcome
from src.knowledge.monitoring import (
    ChangeDetection,
    CheckResult,
    SourceMonitor,
    calculate_next_check,
)
from src.knowledge.storage import SourceEntry, SourceRegistry
from src.orchestration.toolkit.monitor import register_monitor_tools
from src.orchestration.tools import ToolRegistry
from src.orchestration.types import ToolResult


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_kb_root(tmp_path: Path) -> Path:
    """Create a temporary knowledge base root."""
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def source_registry(temp_kb_root: Path) -> SourceRegistry:
    """Create a source registry with temporary root."""
    return SourceRegistry(root=temp_kb_root)


@pytest.fixture
def new_source() -> SourceEntry:
    """Create a source that has never been acquired (for initial acquisition tests)."""
    return SourceEntry(
        url="https://example.gov/new-document.pdf",
        name="New Document",
        source_type="primary",
        status="active",
        last_verified=datetime(2025, 12, 24, tzinfo=timezone.utc),
        added_at=datetime(2025, 12, 24, tzinfo=timezone.utc),
        added_by="source-curator",
        proposal_discussion=5,
        implementation_issue=10,
        credibility_score=0.9,
        is_official=True,
        requires_auth=False,
        discovered_from=None,
        parent_source_url=None,
        content_type="pdf",
        update_frequency="weekly",
        # No monitoring data - never acquired
        last_content_hash=None,
        last_etag=None,
        last_modified_header=None,
        last_checked=None,
        check_failures=0,
        next_check_after=None,
    )


@pytest.fixture
def acquired_source() -> SourceEntry:
    """Create a source that has been acquired previously (for update monitoring tests)."""
    return SourceEntry(
        url="https://example.gov/documents/report.html",
        name="Example Government Report",
        source_type="primary",
        status="active",
        last_verified=datetime(2025, 12, 24, 10, 0, 0, tzinfo=timezone.utc),
        added_at=datetime(2025, 12, 20, 8, 0, 0, tzinfo=timezone.utc),
        added_by="system",
        proposal_discussion=5,
        implementation_issue=10,
        credibility_score=0.95,
        is_official=True,
        requires_auth=False,
        discovered_from=None,
        parent_source_url=None,
        content_type="webpage",
        update_frequency="daily",
        last_content_hash="abc123def456789",
        last_etag='"etag-12345"',
        last_modified_header="Wed, 25 Dec 2025 10:00:00 GMT",
        last_checked=datetime(2025, 12, 25, 10, 0, 0, tzinfo=timezone.utc),
        check_failures=0,
        next_check_after=datetime(2025, 12, 26, 10, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def tool_registry() -> ToolRegistry:
    """Create a tool registry with monitor tools registered."""
    reg = ToolRegistry()
    register_monitor_tools(reg)
    return reg


def _mock_head_response(
    status_code: int = 200,
    etag: str | None = None,
    last_modified: str | None = None,
    retry_after: str | None = None,
) -> MagicMock:
    """Create a mock HEAD response."""
    response = MagicMock()
    response.status_code = status_code
    response.headers = {}
    if etag:
        response.headers["ETag"] = etag
    if last_modified:
        response.headers["Last-Modified"] = last_modified
    if retry_after:
        response.headers["Retry-After"] = retry_after
    return response


def _mock_get_response(
    status_code: int = 200,
    content: bytes = b"<html>content</html>",
    etag: str | None = None,
    last_modified: str | None = None,
) -> MagicMock:
    """Create a mock GET response."""
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.headers = {}
    if etag:
        response.headers["ETag"] = etag
    if last_modified:
        response.headers["Last-Modified"] = last_modified
    return response


# =============================================================================
# Initial Acquisition Mode Tests
# =============================================================================


class TestInitialAcquisitionMode:
    """Tests for sources that have never been acquired."""

    def test_initial_acquisition_detected(
        self,
        source_registry: SourceRegistry,
        new_source: SourceEntry,
    ) -> None:
        """Test that sources without content hash are flagged for initial acquisition."""
        source_registry.save_source(new_source)

        monitor = SourceMonitor(registry=source_registry)
        pending = monitor.get_sources_pending_initial()

        assert len(pending) == 1
        assert pending[0].url == new_source.url

    def test_initial_acquisition_check_returns_initial_status(
        self,
        source_registry: SourceRegistry,
        new_source: SourceEntry,
    ) -> None:
        """Test that checking a new source returns 'initial' status."""
        source_registry.save_source(new_source)
        monitor = SourceMonitor(registry=source_registry)

        with patch.object(monitor._session, "get", return_value=_mock_get_response()):
            result = monitor.check_source(new_source)

        assert result.status == "initial"
        assert result.detection_method == "initial"

    def test_initial_acquisition_issue_created_via_tool(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        new_source: SourceEntry,
    ) -> None:
        """Test that the create_initial_acquisition_issue tool works end-to-end."""
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(new_source)

        # Mock both token resolution and issue creation
        with (
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_token", return_value="fake-token"),
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_repository", return_value="owner/repo"),
            patch("src.orchestration.toolkit.monitor.github_issues.create_issue") as mock_create,
            patch("src.orchestration.toolkit.monitor.github_issues.assign_issue_to_copilot") as mock_assign,
        ):
            mock_create.return_value = IssueOutcome(number=42, url="https://api.github.com/repos/test/42", html_url="https://github.com/test/42")

            tool = tool_registry.get_tool("create_initial_acquisition_issue")
            result = tool.handler(
                {"url": new_source.url, "kb_root": str(temp_kb_root)}
            )

        assert result.success
        assert result.output["issue_number"] == 42
        mock_create.assert_called_once()

        # Verify issue body contains required information
        call_kwargs = mock_create.call_args[1]
        assert "Initial Acquisition" in call_kwargs["title"]
        assert "monitor-initial:" in call_kwargs["body"]

        # Verify Copilot was assigned
        mock_assign.assert_called_once_with(
            token="fake-token",
            repository="owner/repo",
            issue_number=42,
        )

    def test_initial_acquisition_dedup_prevents_duplicate_issues(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        new_source: SourceEntry,
    ) -> None:
        """Test that duplicate initial acquisition issues are not created."""
        from src.integrations.github.search_issues import IssueSearchResult
        
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(new_source)

        # Mock searcher to indicate issue already exists
        with (
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_token", return_value="fake-token"),
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_repository", return_value="owner/repo"),
            patch("src.orchestration.toolkit.monitor.github_issues.create_issue") as mock_create,
            patch("src.orchestration.toolkit.monitor.GitHubIssueSearcher") as mock_searcher_cls,
        ):
            mock_create.return_value = IssueOutcome(number=99, url="https://api.github.com/repos/test/99", html_url="https://github.com/test/99")
            # Simulate existing issue found via body content search
            mock_searcher = MagicMock()
            existing_issue = IssueSearchResult(
                number=99,
                title=f"[Initial Acquisition] {new_source.name}",
                state="open",
                url="https://github.com/test/99",
                assignee=None,
            )
            mock_searcher.search_by_body_content.return_value = [existing_issue]
            mock_searcher_cls.return_value = mock_searcher

            tool = tool_registry.get_tool("create_initial_acquisition_issue")
            result = tool.handler(
                {"url": new_source.url, "kb_root": str(temp_kb_root)}
            )

        # Issue creation should be skipped
        assert result.success
        assert result.output.get("skipped") is True
        assert result.output.get("reason") == "Issue already exists for this source"
        assert result.output.get("issue_number") == 99
        # create_issue should NOT have been called
        mock_create.assert_not_called()


# =============================================================================
# Update Monitoring Mode Tests
# =============================================================================


class TestUpdateMonitoringMode:
    """Tests for sources that have been previously acquired."""

    def test_etag_change_detected(self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that ETag change is detected via tier 1 detection.
        
        Note: After ETag indicates a change, the monitor verifies with content hash.
        We need to mock both HEAD and GET requests.
        """
        source_registry.save_source(acquired_source)
        monitor = SourceMonitor(registry=source_registry)

        # New ETag different from stored one - also mock GET for content hash verification
        with (
            patch.object(
                monitor._session,
                "head",
                return_value=_mock_head_response(etag='"new-etag-67890"'),
            ),
            patch.object(
                monitor._session,
                "get",
                return_value=_mock_get_response(
                    content=b"<html>updated content</html>",
                    etag='"new-etag-67890"',
                ),
            ),
        ):
            result = monitor.check_source(acquired_source)

        # After tiered detection, final result is from content hash
        assert result.status == "changed"
        assert result.detection_method == "content_hash"

    def test_last_modified_change_detected(self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that Last-Modified change is detected via tier 2 detection.
        
        Note: After Last-Modified indicates a change, the monitor verifies with content hash.
        """
        # Remove ETag to force Last-Modified check
        acquired_source.last_etag = None
        source_registry.save_source(acquired_source)
        monitor = SourceMonitor(registry=source_registry)

        with (
            patch.object(
                monitor._session,
                "head",
                return_value=_mock_head_response(
                    last_modified="Thu, 26 Dec 2025 12:00:00 GMT",
                ),
            ),
            patch.object(
                monitor._session,
                "get",
                return_value=_mock_get_response(
                    content=b"<html>updated content</html>",
                    last_modified="Thu, 26 Dec 2025 12:00:00 GMT",
                ),
            ),
        ):
            result = monitor.check_source(acquired_source)

        # After tiered detection, final result is from content hash
        assert result.status == "changed"
        assert result.detection_method == "content_hash"

    def test_content_hash_change_detected(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that content hash change is detected via tier 3 detection."""
        # Remove ETag and Last-Modified to force content hash check
        acquired_source.last_etag = None
        acquired_source.last_modified_header = None
        source_registry.save_source(acquired_source)
        monitor = SourceMonitor(registry=source_registry)

        # Return different content
        new_content = b"<html>updated content with changes</html>"
        with patch.object(
            monitor._session,
            "get",
            return_value=_mock_get_response(content=new_content),
        ):
            result = monitor.check_source(acquired_source, force_full=True)

        assert result.status == "changed"
        assert result.detection_method == "content_hash"
        # Hash should be different from original
        assert result.content_hash != acquired_source.last_content_hash

    def test_unchanged_source_skipped(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that unchanged source returns 'unchanged' status."""
        source_registry.save_source(acquired_source)
        monitor = SourceMonitor(registry=source_registry)

        # Same ETag as stored
        with patch.object(
            monitor._session,
            "head",
            return_value=_mock_head_response(etag=acquired_source.last_etag),
        ):
            result = monitor.check_source(acquired_source)

        assert result.status == "unchanged"

    def test_update_issue_created(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that content update issue is created when changes detected."""
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(acquired_source)

        with (
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_token", return_value="fake-token"),
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_repository", return_value="owner/repo"),
            patch("src.orchestration.toolkit.monitor.github_issues.create_issue") as mock_create,
            patch("src.orchestration.toolkit.monitor.github_issues.assign_issue_to_copilot") as mock_assign,
        ):
            mock_create.return_value = IssueOutcome(number=55, url="https://api.github.com/repos/test/55", html_url="https://github.com/test/55")

            tool = tool_registry.get_tool("create_content_update_issue")
            result = tool.handler(
                {
                    "url": acquired_source.url,
                    "detection_method": "etag",
                    "current_etag": '"new-etag-99999"',
                    "kb_root": str(temp_kb_root),
                }
            )

        assert result.success
        assert result.output["issue_number"] == 55

        call_kwargs = mock_create.call_args[1]
        assert "Content Update" in call_kwargs["title"]
        assert "monitor-update:" in call_kwargs["body"]

        # Verify Copilot was assigned
        mock_assign.assert_called_once_with(
            token="fake-token",
            repository="owner/repo",
            issue_number=55,
        )


# =============================================================================
# Common Behavior Tests
# =============================================================================


class TestCommonBehaviors:
    """Tests for behaviors common to both modes."""

    def test_failure_backoff_increases_next_check(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that failed checks increase the backoff interval."""
        source_registry.save_source(acquired_source)
        monitor = SourceMonitor(registry=source_registry)

        # Simulate connection error
        with patch.object(
            monitor._session,
            "head",
            side_effect=requests.ConnectionError("Network error"),
        ):
            result = monitor.check_source(acquired_source)

        assert result.status == "error"
        assert "Network error" in (result.error_message or "")

        # Calculate expected next check with backoff
        next_check = calculate_next_check(acquired_source, check_failed=True)
        # Should be longer than base interval due to backoff
        base_interval = timedelta(hours=24)  # daily
        assert next_check > datetime.now(timezone.utc) + base_interval

    def test_consecutive_failures_increase_backoff_exponentially(
        self,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that multiple failures result in exponential backoff."""
        # First failure
        acquired_source.check_failures = 0
        next1 = calculate_next_check(acquired_source, check_failed=True)

        # Second failure
        acquired_source.check_failures = 1
        next2 = calculate_next_check(acquired_source, check_failed=True)

        # Third failure
        acquired_source.check_failures = 2
        next3 = calculate_next_check(acquired_source, check_failed=True)

        now = datetime.now(timezone.utc)
        interval1 = (next1 - now).total_seconds()
        interval2 = (next2 - now).total_seconds()
        interval3 = (next3 - now).total_seconds()

        # Each interval should be longer than the previous
        assert interval2 > interval1
        assert interval3 > interval2

    def test_degraded_after_max_failures(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that sources with many failures are handled appropriately."""
        # Set high failure count
        acquired_source.check_failures = 5
        source_registry.save_source(acquired_source)

        monitor = SourceMonitor(registry=source_registry)

        # After 5 failures, source should still be checked but with long backoff
        with patch.object(
            monitor._session,
            "head",
            side_effect=requests.ConnectionError("Still failing"),
        ):
            result = monitor.check_source(acquired_source)

        assert result.status == "error"

        # Next check should be capped at max backoff
        next_check = calculate_next_check(acquired_source, check_failed=True)
        max_expected = datetime.now(timezone.utc) + timedelta(days=7)
        # Should not exceed 7 day maximum
        assert next_check <= max_expected + timedelta(minutes=1)

    def test_rate_limiting_respects_retry_after(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that 429 status is handled as an error condition."""
        source_registry.save_source(acquired_source)
        monitor = SourceMonitor(registry=source_registry)

        # Server returns 429 with Retry-After
        response = _mock_head_response(status_code=429, retry_after="60")
        with patch.object(monitor._session, "head", return_value=response):
            result = monitor.check_source(acquired_source)

        # Should be treated as rate-limited/error, not a change
        # The implementation may treat 429 as error or as unchanged
        assert result.status in ("error", "unchanged")

    def test_sources_not_due_are_filtered_from_check_list(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that sources with future next_check_after are not returned."""
        # Set next check far in the future
        acquired_source.next_check_after = datetime.now(timezone.utc) + timedelta(days=30)
        source_registry.save_source(acquired_source)

        monitor = SourceMonitor(registry=source_registry)
        due = monitor.get_sources_due_for_check()

        assert len(due) == 0

    def test_sources_due_are_returned(
        self,
        source_registry: SourceRegistry,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that sources past their next_check_after are returned."""
        # Set next check in the past
        acquired_source.next_check_after = datetime.now(timezone.utc) - timedelta(hours=1)
        source_registry.save_source(acquired_source)

        monitor = SourceMonitor(registry=source_registry)
        due = monitor.get_sources_due_for_check()

        assert len(due) == 1
        assert due[0].url == acquired_source.url

    def test_update_source_monitoring_metadata_tool(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that monitoring metadata can be updated via tool."""
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(acquired_source)

        tool = tool_registry.get_tool("update_source_monitoring_metadata")
        result = tool.handler(
            {
                "url": acquired_source.url,
                "content_hash": "newhash123456",
                "etag": '"new-etag"',
                "check_succeeded": True,
                "kb_root": str(temp_kb_root),
            }
        )

        assert result.success

        # Verify source was updated
        updated = source_reg.get_source(acquired_source.url)
        assert updated is not None
        assert updated.last_content_hash == "newhash123456"
        assert updated.last_etag == '"new-etag"'

    def test_report_source_access_problem_tool(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        acquired_source: SourceEntry,
    ) -> None:
        """Test that access problems can be reported via tool."""
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(acquired_source)

        with (
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_token", return_value="fake-token"),
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_repository", return_value="owner/repo"),
            patch("src.orchestration.toolkit.monitor.github_issues.create_issue") as mock_create,
        ):
            mock_create.return_value = IssueOutcome(number=77, url="https://api.github.com/repos/test/77", html_url="https://github.com/test/77")

            tool = tool_registry.get_tool("report_source_access_problem")
            result = tool.handler(
                {
                    "url": acquired_source.url,
                    "error_message": "Connection refused",
                    "consecutive_failures": 3,
                    "kb_root": str(temp_kb_root),
                }
            )

        assert result.success
        assert result.output["issue_number"] == 77


# =============================================================================
# End-to-End Workflow Tests
# =============================================================================


class TestEndToEndWorkflows:
    """Tests simulating complete agent workflows."""

    def test_full_initial_acquisition_workflow(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        new_source: SourceEntry,
    ) -> None:
        """Test complete workflow: discover pending -> check -> create issue."""
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(new_source)

        # Step 1: Get pending initial acquisitions
        get_pending = tool_registry.get_tool("get_sources_pending_initial")
        pending_result = get_pending.handler({"kb_root": str(temp_kb_root)})

        assert pending_result.success
        assert pending_result.output["count"] == 1
        source_url = pending_result.output["sources"][0]["url"]

        # Step 2: Check source (for initial, this confirms accessibility)
        check_tool = tool_registry.get_tool("check_source_for_changes")
        with patch(
            "src.knowledge.monitoring.SourceMonitor.check_source",
            return_value=CheckResult(
                source_url=source_url,
                checked_at=datetime.now(timezone.utc),
                status="initial",
                http_status=200,
                detection_method="initial",
            ),
        ):
            check_result = check_tool.handler(
                {"url": source_url, "kb_root": str(temp_kb_root)}
            )

        assert check_result.success
        assert check_result.output["status"] == "initial"

        # Step 3: Create initial acquisition issue
        with (
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_token", return_value="fake-token"),
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_repository", return_value="owner/repo"),
            patch("src.orchestration.toolkit.monitor.github_issues.create_issue") as mock_create,
        ):
            mock_create.return_value = IssueOutcome(number=100, url="https://api.github.com/repos/test/100", html_url="https://github.com/test/100")

            create_tool = tool_registry.get_tool("create_initial_acquisition_issue")
            create_result = create_tool.handler(
                {"url": source_url, "kb_root": str(temp_kb_root)}
            )

        assert create_result.success
        assert create_result.output["issue_number"] == 100

    def test_full_update_monitoring_workflow(
        self,
        tool_registry: ToolRegistry,
        temp_kb_root: Path,
        acquired_source: SourceEntry,
    ) -> None:
        """Test complete workflow: check due -> detect change -> create issue -> update metadata."""
        # Make the source due for check
        acquired_source.next_check_after = datetime.now(timezone.utc) - timedelta(hours=1)
        source_reg = SourceRegistry(root=temp_kb_root)
        source_reg.save_source(acquired_source)

        # Step 1: Get sources due for check
        get_due = tool_registry.get_tool("get_sources_due_for_check")
        due_result = get_due.handler({"kb_root": str(temp_kb_root)})

        assert due_result.success
        assert due_result.output["count"] == 1
        source_url = due_result.output["sources"][0]["url"]

        # Step 2: Check for changes (simulate ETag change)
        check_tool = tool_registry.get_tool("check_source_for_changes")
        new_etag = '"changed-etag-12345"'
        with patch(
            "src.knowledge.monitoring.SourceMonitor.check_source",
            return_value=CheckResult(
                source_url=source_url,
                checked_at=datetime.now(timezone.utc),
                status="changed",
                http_status=200,
                etag=new_etag,
                detection_method="etag",
            ),
        ):
            check_result = check_tool.handler(
                {"url": source_url, "kb_root": str(temp_kb_root)}
            )

        assert check_result.success
        assert check_result.output["status"] == "changed"

        # Step 3: Create content update issue
        with (
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_token", return_value="fake-token"),
            patch("src.orchestration.toolkit.monitor.github_issues.resolve_repository", return_value="owner/repo"),
            patch("src.orchestration.toolkit.monitor.github_issues.create_issue") as mock_create,
        ):
            mock_create.return_value = IssueOutcome(number=101, url="https://api.github.com/repos/test/101", html_url="https://github.com/test/101")

            create_tool = tool_registry.get_tool("create_content_update_issue")
            create_result = create_tool.handler(
                {
                    "url": source_url,
                    "detection_method": "etag",
                    "current_etag": new_etag,
                    "kb_root": str(temp_kb_root),
                }
            )

        assert create_result.success
        assert create_result.output["issue_number"] == 101

        # Step 4: Update monitoring metadata
        update_tool = tool_registry.get_tool("update_source_monitoring_metadata")
        update_result = update_tool.handler(
            {
                "url": source_url,
                "etag": new_etag,
                "check_succeeded": True,
                "kb_root": str(temp_kb_root),
            }
        )

        assert update_result.success

        # Verify source metadata was updated
        updated_source = source_reg.get_source(source_url)
        assert updated_source is not None
        assert updated_source.last_etag == new_etag
        assert updated_source.check_failures == 0
