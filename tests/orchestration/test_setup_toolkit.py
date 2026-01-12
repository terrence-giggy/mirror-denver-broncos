"""Unit tests for setup toolkit."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from src.knowledge.storage import SourceEntry, SourceRegistry
from src.orchestration.toolkit.setup import (
    _calculate_primary_source_score,
    _is_official_domain,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a temporary workspace with proper structure."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    kg_dir = tmp_path / "knowledge-graph"
    kg_dir.mkdir()
    
    # Change working directory to tmp_path so relative paths work
    monkeypatch.chdir(tmp_path)
    
    # Mock paths module to use tmp_path
    monkeypatch.setattr("src.paths.get_knowledge_graph_root", lambda: kg_dir)
    
    return tmp_path


# =============================================================================
# Tests for configure_repository
# =============================================================================


class TestConfigureRepository:
    """Tests for configure_repository function."""

    def test_creates_manifest_file(self, temp_workspace: Path) -> None:
        """Should create manifest.json with configuration."""
        from src.orchestration.toolkit.setup import configure_repository
        
        result = configure_repository({
            "source_url": "https://example.gov/data",
            "topic": "Government Policy",
            "frequency": "weekly",
        })
        
        assert result["success"] is True
        manifest_path = temp_workspace / "config" / "manifest.json"
        assert manifest_path.exists()
        
        with open(manifest_path) as f:
            config = json.load(f)
        
        assert config["source_url"] == "https://example.gov/data"
        assert config["topic"] == "Government Policy"
        assert config["frequency"] == "weekly"
        assert config["model"] == "gpt-4o-mini"

    def test_registers_primary_source(self, temp_workspace: Path) -> None:
        """Should register source_url as primary source."""
        from src.orchestration.toolkit.setup import configure_repository
        
        kg_dir = temp_workspace / "knowledge-graph"
        
        result = configure_repository({
            "source_url": "https://example.gov/data",
            "topic": "Government Policy",
            "frequency": "weekly",
        })
        
        assert result["success"] is True
        assert result["primary_source_registered"] is True
        assert result["primary_source_error"] is None
        
        # Verify source was registered
        registry = SourceRegistry(root=kg_dir)
        source = registry.get_source("https://example.gov/data")
        assert source is not None
        assert source.source_type == "primary"
        assert source.status == "active"
        assert source.name == "Government Policy - Primary Source"
        assert source.is_official is True
        assert source.credibility_score == 0.95
        assert source.added_by == "system"
        assert source.proposal_discussion is None
        assert source.implementation_issue is None
        assert source.notes == "Primary source from manifest.json"

    def test_does_not_duplicate_existing_source(self, temp_workspace: Path) -> None:
        """Should not re-register if source already exists."""
        from src.orchestration.toolkit.setup import configure_repository
        
        kg_dir = temp_workspace / "knowledge-graph"
        
        # Pre-register the source
        registry = SourceRegistry(root=kg_dir)
        existing_source = SourceEntry(
            url="https://example.gov/data",
            name="Existing Source",
            source_type="primary",
            status="active",
            last_verified=datetime(2025, 12, 1, tzinfo=timezone.utc),
            added_at=datetime(2025, 12, 1, tzinfo=timezone.utc),
            added_by="test",
            proposal_discussion=None,
            implementation_issue=None,
            credibility_score=0.9,
            is_official=True,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency=None,
            topics=[],
            notes="Pre-existing",
        )
        registry.save_source(existing_source)
        
        result = configure_repository({
            "source_url": "https://example.gov/data",
            "topic": "Government Policy",
            "frequency": "weekly",
        })
        
        assert result["success"] is True
        assert result["primary_source_registered"] is True
        
        # Verify original source is unchanged
        source = registry.get_source("https://example.gov/data")
        assert source.name == "Existing Source"
        assert source.notes == "Pre-existing"

    def test_handles_missing_source_url(self, temp_workspace: Path) -> None:
        """Should succeed even without source_url."""
        from src.orchestration.toolkit.setup import configure_repository
        
        result = configure_repository({
            "topic": "Test Topic",
            "frequency": "daily",
        })
        
        assert result["success"] is True
        assert result["primary_source_registered"] is False

    def test_source_with_edu_domain(self, temp_workspace: Path) -> None:
        """Should correctly score .edu domains."""
        from src.orchestration.toolkit.setup import configure_repository
        
        kg_dir = temp_workspace / "knowledge-graph"
        
        result = configure_repository({
            "source_url": "https://stanford.edu/research/data",
            "topic": "Academic Research",
            "frequency": "monthly",
        })
        
        assert result["success"] is True
        assert result["primary_source_registered"] is True
        
        registry = SourceRegistry(root=kg_dir)
        source = registry.get_source("https://stanford.edu/research/data")
        assert source is not None
        assert source.credibility_score == 0.90
        assert source.is_official is True

    def test_source_with_commercial_domain(self, temp_workspace: Path) -> None:
        """Should correctly score commercial domains."""
        from src.orchestration.toolkit.setup import configure_repository
        
        kg_dir = temp_workspace / "knowledge-graph"
        
        result = configure_repository({
            "source_url": "https://example.com/data",
            "topic": "Commercial Data",
            "frequency": "weekly",
        })
        
        assert result["success"] is True
        assert result["primary_source_registered"] is True
        
        registry = SourceRegistry(root=kg_dir)
        source = registry.get_source("https://example.com/data")
        assert source is not None
        assert source.credibility_score == 0.70
        assert source.is_official is False


# =============================================================================
# Tests for credibility scoring helpers
# =============================================================================


class TestCalculatePrimarySourceScore:
    """Tests for _calculate_primary_source_score helper."""

    def test_gov_domain_high_score(self) -> None:
        """Government domains should get highest score."""
        assert _calculate_primary_source_score("https://example.gov/data") == 0.95
        assert _calculate_primary_source_score("https://data.gov.uk/api") == 0.95

    def test_edu_domain_high_score(self) -> None:
        """Education domains should get high score."""
        assert _calculate_primary_source_score("https://stanford.edu/research") == 0.90

    def test_org_domain_medium_score(self) -> None:
        """Organization domains should get medium-high score."""
        assert _calculate_primary_source_score("https://mozilla.org/docs") == 0.80

    def test_commercial_domain_default_score(self) -> None:
        """Commercial domains should get default score."""
        assert _calculate_primary_source_score("https://example.com/data") == 0.70

    def test_malformed_url_default_score(self) -> None:
        """Malformed URLs should return default score."""
        assert _calculate_primary_source_score("not-a-url") == 0.70


class TestIsOfficialDomain:
    """Tests for _is_official_domain helper."""

    def test_gov_is_official(self) -> None:
        """Government domains should be official."""
        assert _is_official_domain("https://example.gov/data") is True
        assert _is_official_domain("https://data.gov.uk/api") is True

    def test_edu_is_official(self) -> None:
        """Education domains should be official."""
        assert _is_official_domain("https://mit.edu/research") is True

    def test_mil_is_official(self) -> None:
        """Military domains should be official."""
        assert _is_official_domain("https://army.mil/info") is True

    def test_commercial_not_official(self) -> None:
        """Commercial domains should not be official."""
        assert _is_official_domain("https://example.com/data") is False
        assert _is_official_domain("https://company.org/docs") is False

    def test_malformed_url_not_official(self) -> None:
        """Malformed URLs should not be official."""
        assert _is_official_domain("not-a-url") is False


# =============================================================================
# Tests for create_welcome_announcement
# =============================================================================


class TestCreateWelcomeAnnouncement:
    """Tests for create_welcome_announcement function."""

    def test_requires_topic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should fail if topic is not provided."""
        from src.orchestration.toolkit.setup import create_welcome_announcement
        
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        
        result = create_welcome_announcement({})
        
        assert result["success"] is False
        assert "Topic is required" in result["error"]

    def test_requires_repository(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should fail if repository is not set."""
        from src.orchestration.toolkit.setup import create_welcome_announcement
        
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        
        result = create_welcome_announcement({"topic": "Test Topic"})
        
        assert result["success"] is False
        assert "Repository not specified" in result["error"]

    def test_requires_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Should fail if token is not set."""
        from src.orchestration.toolkit.setup import create_welcome_announcement
        
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)
        
        result = create_welcome_announcement({"topic": "Test Topic"})
        
        assert result["success"] is False
        assert "Token not specified" in result["error"]

    def test_handles_missing_announcements_category(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return specific error if Announcements category is missing."""
        from src.orchestration.toolkit.setup import create_welcome_announcement
        
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        
        with patch(
            "src.integrations.github.discussions.get_category_by_name",
            return_value=None,
        ):
            result = create_welcome_announcement({"topic": "Test Topic"})
        
        assert result["success"] is False
        assert "Announcements category not found" in result["error"]
        assert result.get("category_missing") is True

    def test_creates_announcement_successfully(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should create announcement when category exists."""
        from src.orchestration.toolkit.setup import create_welcome_announcement
        from src.integrations.github.discussions import Discussion, DiscussionCategory
        
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        
        mock_category = DiscussionCategory(
            id="cat123",
            name="Announcements",
            slug="announcements",
        )
        mock_discussion = Discussion(
            id="disc123",
            number=42,
            title="Welcome to Test Topic Research",
            body="...",
            url="https://github.com/owner/repo/discussions/42",
        )
        
        with patch(
            "src.integrations.github.discussions.get_category_by_name",
            return_value=mock_category,
        ), patch(
            "src.integrations.github.discussions.find_discussion_by_title",
            return_value=None,
        ), patch(
            "src.integrations.github.discussions.create_discussion",
            return_value=mock_discussion,
        ):
            result = create_welcome_announcement({
                "topic": "Test Topic",
                "source_url": "https://example.gov/data",
            })
        
        assert result["success"] is True
        assert result["action"] == "created"
        assert result["discussion_url"] == "https://github.com/owner/repo/discussions/42"
        assert result["discussion_number"] == 42

    def test_skips_if_announcement_exists(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Should return existing discussion if already created."""
        from src.orchestration.toolkit.setup import create_welcome_announcement
        from src.integrations.github.discussions import Discussion, DiscussionCategory
        
        monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
        monkeypatch.setenv("GITHUB_TOKEN", "test-token")
        
        mock_category = DiscussionCategory(
            id="cat123",
            name="Announcements",
            slug="announcements",
        )
        existing_discussion = Discussion(
            id="disc999",
            number=99,
            title="Welcome to Test Topic Research",
            body="...",
            url="https://github.com/owner/repo/discussions/99",
        )
        
        with patch(
            "src.integrations.github.discussions.get_category_by_name",
            return_value=mock_category,
        ), patch(
            "src.integrations.github.discussions.find_discussion_by_title",
            return_value=existing_discussion,
        ):
            result = create_welcome_announcement({"topic": "Test Topic"})
        
        assert result["success"] is True
        assert result["action"] == "already_exists"
        assert result["discussion_url"] == "https://github.com/owner/repo/discussions/99"
