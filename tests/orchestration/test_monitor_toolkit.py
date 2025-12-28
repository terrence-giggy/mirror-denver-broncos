"""Unit tests for monitor agent toolkit."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.storage import SourceEntry, SourceRegistry
from src.orchestration.toolkit.monitor import (
    _build_content_update_body,
    _build_initial_acquisition_body,
    _url_hash,
    register_monitor_tools,
)
from src.orchestration.tools import ToolRegistry
from src.orchestration.types import ToolResult


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_registry(tmp_path: Path) -> SourceRegistry:
    """Create a temporary source registry."""
    return SourceRegistry(root=tmp_path)


@pytest.fixture
def sample_source() -> SourceEntry:
    """Create a sample source entry for testing."""
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
        last_content_hash="abc123def456",
        last_etag='"etag-12345"',
        last_modified_header="Wed, 25 Dec 2025 10:00:00 GMT",
        last_checked=datetime(2025, 12, 25, 10, 0, 0, tzinfo=timezone.utc),
        check_failures=0,
        next_check_after=None,
    )


@pytest.fixture
def new_source() -> SourceEntry:
    """Create a source that has never been acquired."""
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
def tool_registry() -> ToolRegistry:
    """Create a tool registry with monitor tools registered."""
    registry = ToolRegistry()
    register_monitor_tools(registry)
    return registry


@pytest.fixture
def pending_review_source() -> SourceEntry:
    """Create a source in pending_review status."""
    return SourceEntry(
        url="https://example.com/pending-doc.html",
        name="Pending Review Document",
        source_type="derived",
        status="pending_review",
        last_verified=datetime(2025, 12, 24, tzinfo=timezone.utc),
        added_at=datetime(2025, 12, 24, tzinfo=timezone.utc),
        added_by="source-curator",
        proposal_discussion=15,
        implementation_issue=16,
        credibility_score=0.7,
        is_official=False,
        requires_auth=False,
        discovered_from=None,
        parent_source_url=None,
        content_type="webpage",
        update_frequency=None,
        last_content_hash=None,
        last_etag=None,
        last_modified_header=None,
        last_checked=None,
        check_failures=0,
        next_check_after=None,
    )


# =============================================================================
# URL Hash Tests
# =============================================================================


class TestUrlHash:
    """Tests for URL hashing helper."""

    def test_url_hash_consistent(self) -> None:
        """URL hash should be consistent for same URL."""
        url = "https://example.com/page"
        assert _url_hash(url) == _url_hash(url)

    def test_url_hash_different_for_different_urls(self) -> None:
        """Different URLs should produce different hashes."""
        url1 = "https://example.com/page1"
        url2 = "https://example.com/page2"
        assert _url_hash(url1) != _url_hash(url2)

    def test_url_hash_length(self) -> None:
        """Hash should be 16 characters."""
        url = "https://example.com/some/long/path/to/document.html"
        assert len(_url_hash(url)) == 16


# =============================================================================
# Issue Body Builder Tests
# =============================================================================


class TestBuildInitialAcquisitionBody:
    """Tests for initial acquisition issue body builder."""

    def test_contains_source_url(self, new_source: SourceEntry) -> None:
        """Body should contain source URL."""
        from src.knowledge.monitoring import ChangeDetection
        
        detection = ChangeDetection(
            source_url=new_source.url,
            source_name=new_source.name,
            detected_at=datetime.now(timezone.utc),
            detection_method="initial",
            change_type="initial",
            previous_hash=None,
            previous_checked=None,
            current_etag=None,
            current_last_modified=None,
            current_hash=None,
            urgency="high",
        )
        
        body = _build_initial_acquisition_body(new_source, detection)
        
        assert new_source.url in body
        assert new_source.name in body

    def test_contains_dedup_marker(self, new_source: SourceEntry) -> None:
        """Body should contain deduplication marker."""
        from src.knowledge.monitoring import ChangeDetection
        
        detection = ChangeDetection(
            source_url=new_source.url,
            source_name=new_source.name,
            detected_at=datetime.now(timezone.utc),
            detection_method="initial",
            change_type="initial",
            previous_hash=None,
            previous_checked=None,
            current_etag=None,
            current_last_modified=None,
            current_hash=None,
            urgency="high",
        )
        
        body = _build_initial_acquisition_body(new_source, detection)
        expected_marker = f"<!-- monitor-initial:{_url_hash(new_source.url)} -->"
        
        assert expected_marker in body

    def test_contains_urgency(self, new_source: SourceEntry) -> None:
        """Body should contain urgency level."""
        from src.knowledge.monitoring import ChangeDetection
        
        detection = ChangeDetection(
            source_url=new_source.url,
            source_name=new_source.name,
            detected_at=datetime.now(timezone.utc),
            detection_method="initial",
            change_type="initial",
            previous_hash=None,
            previous_checked=None,
            current_etag=None,
            current_last_modified=None,
            current_hash=None,
            urgency="high",
        )
        
        body = _build_initial_acquisition_body(new_source, detection)
        
        assert "high" in body.lower()


class TestBuildContentUpdateBody:
    """Tests for content update issue body builder."""

    def test_contains_change_summary_table(self, sample_source: SourceEntry) -> None:
        """Body should contain change summary table."""
        from src.knowledge.monitoring import ChangeDetection
        
        detection = ChangeDetection(
            source_url=sample_source.url,
            source_name=sample_source.name,
            detected_at=datetime.now(timezone.utc),
            detection_method="content_hash",
            change_type="content",
            previous_hash=sample_source.last_content_hash,
            previous_checked=sample_source.last_checked,
            current_etag='"new-etag"',
            current_last_modified="Thu, 26 Dec 2025 10:00:00 GMT",
            current_hash="newhash123456",
            urgency="high",
        )
        
        body = _build_content_update_body(sample_source, detection)
        
        assert "| Metric |" in body
        assert "Content Hash" in body
        assert "ETag" in body

    def test_contains_dedup_marker_with_hash(self, sample_source: SourceEntry) -> None:
        """Body should contain deduplication marker with current hash."""
        from src.knowledge.monitoring import ChangeDetection
        
        detection = ChangeDetection(
            source_url=sample_source.url,
            source_name=sample_source.name,
            detected_at=datetime.now(timezone.utc),
            detection_method="content_hash",
            change_type="content",
            previous_hash=sample_source.last_content_hash,
            previous_checked=sample_source.last_checked,
            current_etag=None,
            current_last_modified=None,
            current_hash="newhash123456",
            urgency="normal",
        )
        
        body = _build_content_update_body(sample_source, detection)
        url_hash = _url_hash(sample_source.url)
        
        assert f"<!-- monitor-update:{url_hash}:newhash123456 -->" in body


# =============================================================================
# Tool Registration Tests
# =============================================================================


class TestToolRegistration:
    """Tests for tool registration."""

    def test_register_all_tools(self, tool_registry: ToolRegistry) -> None:
        """All monitor tools should be registered."""
        expected_tools = [
            "get_sources_pending_initial",
            "get_sources_due_for_check",
            "check_source_for_changes",
            "update_source_monitoring_metadata",
            "create_initial_acquisition_issue",
            "create_content_update_issue",
            "report_source_access_problem",
        ]
        
        for tool_name in expected_tools:
            tool = tool_registry.get_tool(tool_name)
            assert tool is not None, f"Tool {tool_name} not registered"


# =============================================================================
# Read Tool Handler Tests
# =============================================================================


class TestGetSourcesPendingInitialHandler:
    """Tests for get_sources_pending_initial handler."""

    def test_returns_sources_without_hash(
        self,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
        new_source: SourceEntry,
    ) -> None:
        """Should return only sources needing initial acquisition."""
        temp_registry.save_source(sample_source)
        temp_registry.save_source(new_source)
        
        registry = ToolRegistry()
        register_monitor_tools(registry)
        
        tool = registry.get_tool("get_sources_pending_initial")
        result = tool.handler({"kb_root": str(temp_registry.root)})
        
        assert result.success is True
        assert result.output["count"] == 1
        assert result.output["sources"][0]["url"] == new_source.url

    def test_returns_empty_when_all_acquired(
        self,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should return empty list when all sources are acquired."""
        temp_registry.save_source(sample_source)
        
        registry = ToolRegistry()
        register_monitor_tools(registry)
        
        tool = registry.get_tool("get_sources_pending_initial")
        result = tool.handler({"kb_root": str(temp_registry.root)})
        
        assert result.success is True
        assert result.output["count"] == 0

    def test_returns_message_when_pending_review_sources_exist(
        self,
        temp_registry: SourceRegistry,
        pending_review_source: SourceEntry,
    ) -> None:
        """Should return helpful message when no active sources but pending_review exists."""
        temp_registry.save_source(pending_review_source)
        
        registry = ToolRegistry()
        register_monitor_tools(registry)
        
        tool = registry.get_tool("get_sources_pending_initial")
        result = tool.handler({"kb_root": str(temp_registry.root)})
        
        assert result.success is True
        assert result.output["count"] == 0
        assert result.output["message"] is not None
        assert "pending_review" in result.output["message"]
        assert "1 source(s)" in result.output["message"]
        assert "implement_approved_source" in result.output["message"]


class TestGetSourcesDueForCheckHandler:
    """Tests for get_sources_due_for_check handler."""

    def test_returns_sources_due(
        self,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should return sources due for checking."""
        sample_source.next_check_after = datetime.now(timezone.utc) - timedelta(hours=1)
        temp_registry.save_source(sample_source)
        
        registry = ToolRegistry()
        register_monitor_tools(registry)
        
        tool = registry.get_tool("get_sources_due_for_check")
        result = tool.handler({"kb_root": str(temp_registry.root)})
        
        assert result.success is True
        assert result.output["count"] == 1

    def test_excludes_sources_not_due(
        self,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should exclude sources not yet due."""
        sample_source.next_check_after = datetime.now(timezone.utc) + timedelta(hours=1)
        temp_registry.save_source(sample_source)
        
        registry = ToolRegistry()
        register_monitor_tools(registry)
        
        tool = registry.get_tool("get_sources_due_for_check")
        result = tool.handler({"kb_root": str(temp_registry.root)})
        
        assert result.success is True
        assert result.output["count"] == 0


class TestCheckSourceForChangesHandler:
    """Tests for check_source_for_changes handler."""

    def test_requires_url(self, tool_registry: ToolRegistry) -> None:
        """Should require URL parameter."""
        tool = tool_registry.get_tool("check_source_for_changes")
        result = tool.handler({})
        
        assert result.success is False
        assert "URL" in result.output

    def test_returns_error_for_unknown_source(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
    ) -> None:
        """Should return error for unknown source."""
        tool = tool_registry.get_tool("check_source_for_changes")
        result = tool.handler({
            "url": "https://unknown.com/page",
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is False
        assert "not found" in result.output.lower()

    def test_returns_initial_for_new_source(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        new_source: SourceEntry,
    ) -> None:
        """Should return initial status for unacquired source."""
        temp_registry.save_source(new_source)
        
        tool = tool_registry.get_tool("check_source_for_changes")
        result = tool.handler({
            "url": new_source.url,
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is True
        assert result.output["status"] == "initial"


# =============================================================================
# Write Tool Handler Tests
# =============================================================================


class TestUpdateSourceMonitoringMetadataHandler:
    """Tests for update_source_monitoring_metadata handler."""

    def test_requires_url(self, tool_registry: ToolRegistry) -> None:
        """Should require URL parameter."""
        tool = tool_registry.get_tool("update_source_monitoring_metadata")
        result = tool.handler({"check_succeeded": True})
        
        assert result.success is False
        assert "URL" in result.output

    def test_updates_last_checked(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should update last_checked timestamp."""
        temp_registry.save_source(sample_source)
        
        tool = tool_registry.get_tool("update_source_monitoring_metadata")
        result = tool.handler({
            "url": sample_source.url,
            "check_succeeded": True,
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is True
        assert "last_checked" in result.output
        
        # Verify source was updated
        updated = temp_registry.get_source(sample_source.url)
        assert updated.last_checked is not None
        assert updated.last_checked > sample_source.last_checked

    def test_increments_failures_on_error(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should increment check_failures on error."""
        sample_source.check_failures = 2
        temp_registry.save_source(sample_source)
        
        tool = tool_registry.get_tool("update_source_monitoring_metadata")
        result = tool.handler({
            "url": sample_source.url,
            "check_succeeded": False,
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is True
        assert result.output["check_failures"] == 3

    def test_resets_failures_on_success(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should reset check_failures on success."""
        sample_source.check_failures = 3
        temp_registry.save_source(sample_source)
        
        tool = tool_registry.get_tool("update_source_monitoring_metadata")
        result = tool.handler({
            "url": sample_source.url,
            "check_succeeded": True,
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is True
        assert result.output["check_failures"] == 0


class TestCreateInitialAcquisitionIssueHandler:
    """Tests for create_initial_acquisition_issue handler."""

    def test_requires_url(self, tool_registry: ToolRegistry) -> None:
        """Should require URL parameter."""
        tool = tool_registry.get_tool("create_initial_acquisition_issue")
        result = tool.handler({})
        
        assert result.success is False
        assert "URL" in result.output

    def test_rejects_already_acquired_source(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should reject source that already has content hash."""
        temp_registry.save_source(sample_source)
        
        tool = tool_registry.get_tool("create_initial_acquisition_issue")
        result = tool.handler({
            "url": sample_source.url,
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is False
        assert "already has content hash" in result.output.lower()


class TestCreateContentUpdateIssueHandler:
    """Tests for create_content_update_issue handler."""

    def test_requires_url(self, tool_registry: ToolRegistry) -> None:
        """Should require URL parameter."""
        tool = tool_registry.get_tool("create_content_update_issue")
        result = tool.handler({"detection_method": "content_hash"})
        
        assert result.success is False
        assert "URL" in result.output

    def test_requires_detection_method(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        sample_source: SourceEntry,
    ) -> None:
        """Should require detection_method parameter."""
        temp_registry.save_source(sample_source)
        
        tool = tool_registry.get_tool("create_content_update_issue")
        result = tool.handler({
            "url": sample_source.url,
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is False
        assert "detection_method" in result.output

    def test_rejects_source_without_hash(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
        new_source: SourceEntry,
    ) -> None:
        """Should reject source without previous content hash."""
        temp_registry.save_source(new_source)
        
        tool = tool_registry.get_tool("create_content_update_issue")
        result = tool.handler({
            "url": new_source.url,
            "detection_method": "content_hash",
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is False
        assert "no previous content hash" in result.output.lower()


class TestReportSourceAccessProblemHandler:
    """Tests for report_source_access_problem handler."""

    def test_requires_url(self, tool_registry: ToolRegistry) -> None:
        """Should require URL parameter."""
        tool = tool_registry.get_tool("report_source_access_problem")
        result = tool.handler({"error_message": "Connection refused"})
        
        assert result.success is False
        assert "URL" in result.output

    def test_returns_error_for_unknown_source(
        self,
        tool_registry: ToolRegistry,
        temp_registry: SourceRegistry,
    ) -> None:
        """Should return error for unknown source."""
        tool = tool_registry.get_tool("report_source_access_problem")
        result = tool.handler({
            "url": "https://unknown.com/page",
            "error_message": "Connection refused",
            "kb_root": str(temp_registry.root),
        })
        
        assert result.success is False
        assert "not found" in result.output.lower()
