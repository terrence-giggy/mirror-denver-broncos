"""Unit tests for source registry storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.knowledge.storage import (
    SourceEntry,
    SourceRegistry,
    _url_hash,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_source_registry(tmp_path: Path) -> SourceRegistry:
    """Create a temporary source registry."""
    return SourceRegistry(root=tmp_path)


@pytest.fixture
def sample_source_entry() -> SourceEntry:
    """Create a sample source entry for testing."""
    return SourceEntry(
        url="https://example.gov/documents/report.html",
        name="Example Government Report",
        source_type="primary",
        status="active",
        last_verified=datetime(2025, 12, 24, 10, 0, 0, tzinfo=timezone.utc),
        added_at=datetime(2025, 12, 20, 8, 0, 0, tzinfo=timezone.utc),
        added_by="system",
        proposal_discussion=None,
        implementation_issue=None,
        credibility_score=0.95,
        is_official=True,
        requires_auth=False,
        discovered_from=None,
        parent_source_url=None,
        content_type="webpage",
        update_frequency="monthly",
        topics=["government", "policy"],
        notes="Primary source from manifest",
    )


@pytest.fixture
def sample_derived_source() -> SourceEntry:
    """Create a sample derived source entry."""
    return SourceEntry(
        url="https://research.edu/papers/analysis.pdf",
        name="Research Analysis Paper",
        source_type="derived",
        status="pending_review",
        last_verified=datetime(2025, 12, 23, 15, 30, 0, tzinfo=timezone.utc),
        added_at=datetime(2025, 12, 23, 15, 30, 0, tzinfo=timezone.utc),
        added_by="source-curator-agent",
        proposal_discussion=10,
        implementation_issue=42,
        credibility_score=0.75,
        is_official=False,
        requires_auth=False,
        discovered_from="abc123",
        parent_source_url="https://example.gov/documents/report.html",
        content_type="pdf",
        update_frequency=None,
        topics=["research", "analysis"],
        notes="Discovered via automated scan",
    )


# =============================================================================
# URL Hash Tests
# =============================================================================


class TestUrlHash:
    """Tests for URL hashing function."""

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

    def test_url_hash_alphanumeric(self) -> None:
        """Hash should be alphanumeric (hex)."""
        url = "https://example.com/page"
        result = _url_hash(url)
        assert all(c in "0123456789abcdef" for c in result)


# =============================================================================
# SourceEntry Serialization Tests
# =============================================================================


class TestSourceEntrySerialization:
    """Tests for SourceEntry to_dict/from_dict round-trip."""

    def test_to_dict_contains_all_fields(self, sample_source_entry: SourceEntry) -> None:
        """to_dict should include all fields."""
        data = sample_source_entry.to_dict()

        assert data["url"] == "https://example.gov/documents/report.html"
        assert data["name"] == "Example Government Report"
        assert data["source_type"] == "primary"
        assert data["status"] == "active"
        assert data["last_verified"] == "2025-12-24T10:00:00+00:00"
        assert data["added_at"] == "2025-12-20T08:00:00+00:00"
        assert data["added_by"] == "system"
        assert data["proposal_discussion"] is None
        assert data["implementation_issue"] is None
        assert data["credibility_score"] == 0.95
        assert data["is_official"] is True
        assert data["requires_auth"] is False
        assert data["discovered_from"] is None
        assert data["parent_source_url"] is None
        assert data["content_type"] == "webpage"
        assert data["update_frequency"] == "monthly"
        assert data["topics"] == ["government", "policy"]
        assert data["notes"] == "Primary source from manifest"

    def test_from_dict_round_trip(self, sample_source_entry: SourceEntry) -> None:
        """from_dict(to_dict(entry)) should equal original entry."""
        data = sample_source_entry.to_dict()
        restored = SourceEntry.from_dict(data)

        assert restored.url == sample_source_entry.url
        assert restored.name == sample_source_entry.name
        assert restored.source_type == sample_source_entry.source_type
        assert restored.status == sample_source_entry.status
        assert restored.last_verified == sample_source_entry.last_verified
        assert restored.added_at == sample_source_entry.added_at
        assert restored.added_by == sample_source_entry.added_by
        assert restored.proposal_discussion == sample_source_entry.proposal_discussion
        assert restored.implementation_issue == sample_source_entry.implementation_issue
        assert restored.credibility_score == sample_source_entry.credibility_score
        assert restored.is_official == sample_source_entry.is_official
        assert restored.requires_auth == sample_source_entry.requires_auth
        assert restored.discovered_from == sample_source_entry.discovered_from
        assert restored.parent_source_url == sample_source_entry.parent_source_url
        assert restored.content_type == sample_source_entry.content_type
        assert restored.update_frequency == sample_source_entry.update_frequency
        assert restored.topics == sample_source_entry.topics
        assert restored.notes == sample_source_entry.notes

    def test_from_dict_with_minimal_data(self) -> None:
        """from_dict should handle minimal payload with defaults."""
        data = {
            "url": "https://example.com/page",
            "name": "Test",
            "source_type": "reference",
            "status": "active",
            "last_verified": "2025-12-24T00:00:00+00:00",
            "added_at": "2025-12-24T00:00:00+00:00",
            "added_by": "user",
        }
        entry = SourceEntry.from_dict(data)

        assert entry.url == "https://example.com/page"
        assert entry.credibility_score == 0.0  # default
        assert entry.is_official is False  # default
        assert entry.content_type == "webpage"  # default
        assert entry.topics == []  # default
        assert entry.notes == ""  # default
        assert entry.proposal_discussion is None  # default
        assert entry.implementation_issue is None  # default

    def test_from_dict_legacy_approval_issue_migration(self) -> None:
        """from_dict should migrate legacy approval_issue field to implementation_issue."""
        data = {
            "url": "https://example.com/legacy",
            "name": "Legacy Source",
            "source_type": "derived",
            "status": "active",
            "last_verified": "2025-12-24T00:00:00+00:00",
            "added_at": "2025-12-24T00:00:00+00:00",
            "added_by": "user",
            "approval_issue": 42,  # Legacy field name
        }
        entry = SourceEntry.from_dict(data)

        # Legacy approval_issue should migrate to implementation_issue
        assert entry.implementation_issue == 42
        # proposal_discussion wasn't in legacy format
        assert entry.proposal_discussion is None

    def test_from_dict_prefers_implementation_issue_over_legacy(self) -> None:
        """from_dict should prefer implementation_issue when both fields exist."""
        data = {
            "url": "https://example.com/both",
            "name": "Both Fields",
            "source_type": "derived",
            "status": "active",
            "last_verified": "2025-12-24T00:00:00+00:00",
            "added_at": "2025-12-24T00:00:00+00:00",
            "added_by": "user",
            "approval_issue": 42,  # Legacy field
            "implementation_issue": 99,  # New field takes precedence
            "proposal_discussion": 10,
        }
        entry = SourceEntry.from_dict(data)

        assert entry.implementation_issue == 99  # New field preferred
        assert entry.proposal_discussion == 10

    def test_url_hash_property(self, sample_source_entry: SourceEntry) -> None:
        """url_hash property should return consistent hash."""
        expected_hash = _url_hash(sample_source_entry.url)
        assert sample_source_entry.url_hash == expected_hash

    def test_json_serialization(self, sample_source_entry: SourceEntry) -> None:
        """to_dict output should be JSON serializable."""
        data = sample_source_entry.to_dict()
        json_str = json.dumps(data)
        restored_data = json.loads(json_str)
        restored = SourceEntry.from_dict(restored_data)
        assert restored.url == sample_source_entry.url


# =============================================================================
# SourceEntry Monitoring Fields Tests
# =============================================================================


class TestSourceEntryMonitoringFields:
    """Tests for SourceEntry monitoring metadata fields."""

    def test_monitoring_fields_default_values(self) -> None:
        """Monitoring fields should have correct defaults."""
        entry = SourceEntry(
            url="https://example.com/page",
            name="Test",
            source_type="primary",
            status="active",
            last_verified=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_at=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_by="user",
            proposal_discussion=None,
            implementation_issue=None,
            credibility_score=0.5,
            is_official=False,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency=None,
        )
        
        assert entry.last_content_hash is None
        assert entry.last_etag is None
        assert entry.last_modified_header is None
        assert entry.last_checked is None
        assert entry.check_failures == 0
        assert entry.next_check_after is None

    def test_monitoring_fields_serialization(self) -> None:
        """Monitoring fields should serialize correctly."""
        checked_at = datetime(2025, 12, 25, 10, 30, 0, tzinfo=timezone.utc)
        next_check = datetime(2025, 12, 26, 10, 30, 0, tzinfo=timezone.utc)
        
        entry = SourceEntry(
            url="https://example.com/monitored",
            name="Monitored Source",
            source_type="primary",
            status="active",
            last_verified=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_at=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_by="user",
            proposal_discussion=None,
            implementation_issue=None,
            credibility_score=0.9,
            is_official=True,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency="daily",
            last_content_hash="abc123def456",
            last_etag='"etag-value-12345"',
            last_modified_header="Wed, 25 Dec 2025 10:00:00 GMT",
            last_checked=checked_at,
            check_failures=2,
            next_check_after=next_check,
        )
        
        data = entry.to_dict()
        
        assert data["last_content_hash"] == "abc123def456"
        assert data["last_etag"] == '"etag-value-12345"'
        assert data["last_modified_header"] == "Wed, 25 Dec 2025 10:00:00 GMT"
        assert data["last_checked"] == "2025-12-25T10:30:00+00:00"
        assert data["check_failures"] == 2
        assert data["next_check_after"] == "2025-12-26T10:30:00+00:00"

    def test_monitoring_fields_round_trip(self) -> None:
        """Monitoring fields should survive to_dict/from_dict round-trip."""
        checked_at = datetime(2025, 12, 25, 10, 30, 0, tzinfo=timezone.utc)
        next_check = datetime(2025, 12, 26, 10, 30, 0, tzinfo=timezone.utc)
        
        original = SourceEntry(
            url="https://example.com/monitored",
            name="Monitored Source",
            source_type="derived",
            status="active",
            last_verified=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_at=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_by="monitor-agent",
            proposal_discussion=5,
            implementation_issue=10,
            credibility_score=0.8,
            is_official=False,
            requires_auth=False,
            discovered_from="checksum123",
            parent_source_url="https://parent.com/source",
            content_type="pdf",
            update_frequency="weekly",
            last_content_hash="sha256-hash-here",
            last_etag='"weak-etag"',
            last_modified_header="Tue, 24 Dec 2025 08:00:00 GMT",
            last_checked=checked_at,
            check_failures=1,
            next_check_after=next_check,
        )
        
        data = original.to_dict()
        restored = SourceEntry.from_dict(data)
        
        assert restored.last_content_hash == original.last_content_hash
        assert restored.last_etag == original.last_etag
        assert restored.last_modified_header == original.last_modified_header
        assert restored.last_checked == original.last_checked
        assert restored.check_failures == original.check_failures
        assert restored.next_check_after == original.next_check_after

    def test_monitoring_fields_backward_compatibility(self) -> None:
        """from_dict should handle legacy payloads without monitoring fields."""
        legacy_data = {
            "url": "https://example.com/legacy",
            "name": "Legacy Source",
            "source_type": "reference",
            "status": "active",
            "last_verified": "2025-12-24T00:00:00+00:00",
            "added_at": "2025-12-20T00:00:00+00:00",
            "added_by": "admin",
            # No monitoring fields present
        }
        
        entry = SourceEntry.from_dict(legacy_data)
        
        # Should have default values
        assert entry.last_content_hash is None
        assert entry.last_etag is None
        assert entry.last_modified_header is None
        assert entry.last_checked is None
        assert entry.check_failures == 0
        assert entry.next_check_after is None

    def test_monitoring_fields_null_datetime_serialization(self) -> None:
        """Null datetime fields should serialize as None."""
        entry = SourceEntry(
            url="https://example.com/page",
            name="Test",
            source_type="primary",
            status="active",
            last_verified=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_at=datetime(2025, 12, 24, tzinfo=timezone.utc),
            added_by="user",
            proposal_discussion=None,
            implementation_issue=None,
            credibility_score=0.5,
            is_official=False,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency=None,
            last_checked=None,
            next_check_after=None,
        )
        
        data = entry.to_dict()
        
        assert data["last_checked"] is None
        assert data["next_check_after"] is None
        
        # Round-trip should preserve None values
        restored = SourceEntry.from_dict(data)
        assert restored.last_checked is None
        assert restored.next_check_after is None


# =============================================================================
# SourceRegistry Tests
# =============================================================================


class TestSourceRegistry:
    """Tests for SourceRegistry storage operations."""

    def test_save_and_get_source(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
    ) -> None:
        """Should save and retrieve a source entry."""
        temp_source_registry.save_source(sample_source_entry)
        retrieved = temp_source_registry.get_source(sample_source_entry.url)

        assert retrieved is not None
        assert retrieved.url == sample_source_entry.url
        assert retrieved.name == sample_source_entry.name
        assert retrieved.credibility_score == sample_source_entry.credibility_score

    def test_get_nonexistent_source(
        self,
        temp_source_registry: SourceRegistry,
    ) -> None:
        """Should return None for non-existent source."""
        result = temp_source_registry.get_source("https://nonexistent.com/page")
        assert result is None

    def test_source_exists(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
    ) -> None:
        """source_exists should return correct status."""
        assert not temp_source_registry.source_exists(sample_source_entry.url)

        temp_source_registry.save_source(sample_source_entry)

        assert temp_source_registry.source_exists(sample_source_entry.url)

    def test_list_sources_empty(
        self,
        temp_source_registry: SourceRegistry,
    ) -> None:
        """list_sources should return empty list when no sources."""
        result = temp_source_registry.list_sources()
        assert result == []

    def test_list_sources_all(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
        sample_derived_source: SourceEntry,
    ) -> None:
        """list_sources should return all sources."""
        temp_source_registry.save_source(sample_source_entry)
        temp_source_registry.save_source(sample_derived_source)

        result = temp_source_registry.list_sources()

        assert len(result) == 2
        urls = {s.url for s in result}
        assert sample_source_entry.url in urls
        assert sample_derived_source.url in urls

    def test_list_sources_filter_by_status(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
        sample_derived_source: SourceEntry,
    ) -> None:
        """list_sources should filter by status."""
        temp_source_registry.save_source(sample_source_entry)  # active
        temp_source_registry.save_source(sample_derived_source)  # pending_review

        active_sources = temp_source_registry.list_sources(status="active")
        pending_sources = temp_source_registry.list_sources(status="pending_review")

        assert len(active_sources) == 1
        assert active_sources[0].url == sample_source_entry.url

        assert len(pending_sources) == 1
        assert pending_sources[0].url == sample_derived_source.url

    def test_list_sources_filter_by_type(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
        sample_derived_source: SourceEntry,
    ) -> None:
        """list_sources should filter by source_type."""
        temp_source_registry.save_source(sample_source_entry)  # primary
        temp_source_registry.save_source(sample_derived_source)  # derived

        primary_sources = temp_source_registry.list_sources(source_type="primary")
        derived_sources = temp_source_registry.list_sources(source_type="derived")

        assert len(primary_sources) == 1
        assert primary_sources[0].source_type == "primary"

        assert len(derived_sources) == 1
        assert derived_sources[0].source_type == "derived"

    def test_delete_source(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
    ) -> None:
        """delete_source should remove source and return True."""
        temp_source_registry.save_source(sample_source_entry)
        assert temp_source_registry.source_exists(sample_source_entry.url)

        result = temp_source_registry.delete_source(sample_source_entry.url)

        assert result is True
        assert not temp_source_registry.source_exists(sample_source_entry.url)
        assert temp_source_registry.list_sources() == []

    def test_delete_nonexistent_source(
        self,
        temp_source_registry: SourceRegistry,
    ) -> None:
        """delete_source should return False for non-existent source."""
        result = temp_source_registry.delete_source("https://nonexistent.com/page")
        assert result is False

    def test_get_source_by_hash(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
    ) -> None:
        """Should retrieve source by its URL hash."""
        temp_source_registry.save_source(sample_source_entry)
        url_hash = sample_source_entry.url_hash

        retrieved = temp_source_registry.get_source_by_hash(url_hash)

        assert retrieved is not None
        assert retrieved.url == sample_source_entry.url

    def test_get_all_urls(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
        sample_derived_source: SourceEntry,
    ) -> None:
        """get_all_urls should return all registered URLs."""
        temp_source_registry.save_source(sample_source_entry)
        temp_source_registry.save_source(sample_derived_source)

        urls = temp_source_registry.get_all_urls()

        assert len(urls) == 2
        assert sample_source_entry.url in urls
        assert sample_derived_source.url in urls

    def test_update_existing_source(
        self,
        temp_source_registry: SourceRegistry,
        sample_source_entry: SourceEntry,
    ) -> None:
        """Saving same URL should update the entry."""
        temp_source_registry.save_source(sample_source_entry)

        # Modify and save again
        updated = SourceEntry(
            url=sample_source_entry.url,
            name="Updated Name",
            source_type=sample_source_entry.source_type,
            status="deprecated",
            last_verified=sample_source_entry.last_verified,
            added_at=sample_source_entry.added_at,
            added_by=sample_source_entry.added_by,
            proposal_discussion=sample_source_entry.proposal_discussion,
            implementation_issue=sample_source_entry.implementation_issue,
            credibility_score=0.5,
            is_official=sample_source_entry.is_official,
            requires_auth=sample_source_entry.requires_auth,
            discovered_from=sample_source_entry.discovered_from,
            parent_source_url=sample_source_entry.parent_source_url,
            content_type=sample_source_entry.content_type,
            update_frequency=sample_source_entry.update_frequency,
            topics=sample_source_entry.topics,
            notes="Updated notes",
        )
        temp_source_registry.save_source(updated)

        # Should still be just one source
        assert len(temp_source_registry.list_sources()) == 1

        retrieved = temp_source_registry.get_source(sample_source_entry.url)
        assert retrieved is not None
        assert retrieved.name == "Updated Name"
        assert retrieved.status == "deprecated"
        assert retrieved.credibility_score == 0.5
        assert retrieved.notes == "Updated notes"

    def test_registry_creates_sources_directory(
        self,
        tmp_path: Path,
    ) -> None:
        """Registry should create sources directory on init."""
        registry = SourceRegistry(root=tmp_path)
        sources_dir = tmp_path / "sources"

        assert sources_dir.exists()
        assert sources_dir.is_dir()

    def test_registry_index_persistence(
        self,
        tmp_path: Path,
        sample_source_entry: SourceEntry,
    ) -> None:
        """Registry index should persist across instances."""
        registry1 = SourceRegistry(root=tmp_path)
        registry1.save_source(sample_source_entry)

        # Create new registry instance pointing to same location
        registry2 = SourceRegistry(root=tmp_path)
        retrieved = registry2.get_source(sample_source_entry.url)

        assert retrieved is not None
        assert retrieved.url == sample_source_entry.url
