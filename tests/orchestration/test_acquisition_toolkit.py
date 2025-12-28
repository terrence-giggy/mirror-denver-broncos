"""Tests for acquisition toolkit."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.storage import SourceEntry, SourceRegistry
from src.orchestration.toolkit.acquisition import (
    _acquire_source_content_handler,
    register_acquisition_tools,
)
from src.orchestration.tools import ToolRegistry
from src.parsing.runner import ParseOutcome


@pytest.fixture
def temp_dirs(tmp_path):
    """Create temporary directories for testing."""
    evidence_root = tmp_path / "evidence"
    kb_root = tmp_path / "knowledge-graph"
    evidence_root.mkdir()
    kb_root.mkdir()
    return evidence_root, kb_root


@pytest.fixture
def sample_source(temp_dirs):
    """Create a sample source entry."""
    evidence_root, kb_root = temp_dirs
    registry = SourceRegistry(root=kb_root)
    
    now = datetime.now(timezone.utc)
    source = SourceEntry(
        url="https://example.com/test",
        name="Test Source",
        source_type="derived",
        status="active",
        last_verified=now,
        added_at=now,
        added_by="test-user",
        proposal_discussion=None,
        implementation_issue=None,
        credibility_score=0.8,
        is_official=False,
        requires_auth=False,
        discovered_from=None,
        parent_source_url=None,
        content_type="webpage",
        update_frequency=None,
        topics=[],
        notes="Test source",
    )
    
    registry.save_source(source)
    return source, evidence_root, kb_root


def test_register_acquisition_tools():
    """Test that acquisition tools are registered correctly."""
    registry = ToolRegistry()
    register_acquisition_tools(registry)
    
    assert "acquire_source_content" in registry


def test_acquire_source_content_missing_url(sample_source):
    """Test acquire_source_content with missing URL."""
    source, evidence_root, kb_root = sample_source
    
    result = _acquire_source_content_handler({
        "evidence_root": str(evidence_root),
        "kb_root": str(kb_root),
    })
    
    assert not result.success
    assert "url must be a non-empty string" in result.error


def test_acquire_source_content_source_not_found(sample_source):
    """Test acquire_source_content with non-existent source."""
    source, evidence_root, kb_root = sample_source
    
    result = _acquire_source_content_handler({
        "url": "https://example.com/nonexistent",
        "evidence_root": str(evidence_root),
        "kb_root": str(kb_root),
    })
    
    assert not result.success
    assert "Source not found in registry" in result.error


@patch("src.orchestration.toolkit.acquisition.parse_single_target")
def test_acquire_source_content_success(mock_parse, sample_source):
    """Test successful source acquisition."""
    source, evidence_root, kb_root = sample_source
    
    # Mock successful parse
    mock_parse.return_value = ParseOutcome(
        source=source.url,
        status="completed",
        parser="web",
        checksum="abc123def456",
        artifact_path="2025/test-source-abc123/index.md",
        warnings=[],
        message="Parsed successfully",
    )
    
    result = _acquire_source_content_handler({
        "url": source.url,
        "evidence_root": str(evidence_root),
        "kb_root": str(kb_root),
    })
    
    assert result.success
    assert result.output["url"] == source.url
    assert result.output["checksum"] == "abc123def456"
    assert result.output["registry_updated"] is True
    
    # Verify registry was updated
    registry = SourceRegistry(root=kb_root)
    updated_source = registry.get_source(source.url)
    assert updated_source.last_content_hash == "abc123def456"
    assert updated_source.check_failures == 0


@patch("src.orchestration.toolkit.acquisition.parse_single_target")
def test_acquire_source_content_parse_error(mock_parse, sample_source):
    """Test source acquisition with parsing error."""
    source, evidence_root, kb_root = sample_source
    
    # Mock parse error
    mock_parse.return_value = ParseOutcome(
        source=source.url,
        status="error",
        parser="web",
        checksum=None,
        artifact_path=None,
        warnings=[],
        message=None,
        error="Failed to fetch URL",
    )
    
    result = _acquire_source_content_handler({
        "url": source.url,
        "evidence_root": str(evidence_root),
        "kb_root": str(kb_root),
    })
    
    assert not result.success
    assert "Failed to fetch URL" in result.error


@patch("src.orchestration.toolkit.acquisition.parse_single_target")
def test_acquire_source_content_with_force(mock_parse, sample_source):
    """Test source acquisition with force flag."""
    source, evidence_root, kb_root = sample_source
    
    mock_parse.return_value = ParseOutcome(
        source=source.url,
        status="completed",
        parser="web",
        checksum="xyz789",
        artifact_path="2025/test-source-xyz789/index.md",
        warnings=[],
        message="Parsed successfully",
    )
    
    result = _acquire_source_content_handler({
        "url": source.url,
        "force": True,
        "evidence_root": str(evidence_root),
        "kb_root": str(kb_root),
    })
    
    assert result.success
    # Verify parse was called with force=True
    assert mock_parse.call_args[1]["force"] is True


@patch("src.orchestration.toolkit.acquisition.parse_single_target")
def test_acquire_source_content_with_warnings(mock_parse, sample_source):
    """Test source acquisition with parser warnings."""
    source, evidence_root, kb_root = sample_source
    
    mock_parse.return_value = ParseOutcome(
        source=source.url,
        status="completed",
        parser="web",
        checksum="abc123",
        artifact_path="2025/test-source-abc123/index.md",
        warnings=["No extractable text found in some sections", "Empty table detected"],
        message="Parsed with warnings",
    )
    
    result = _acquire_source_content_handler({
        "url": source.url,
        "evidence_root": str(evidence_root),
        "kb_root": str(kb_root),
    })
    
    assert result.success
    assert len(result.output["warnings"]) == 2
    assert "No extractable text" in result.output["warnings"][0]
