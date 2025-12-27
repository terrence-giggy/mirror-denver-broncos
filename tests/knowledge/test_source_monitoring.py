"""Unit tests for source monitoring module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests
from requests.exceptions import SSLError

from src.knowledge.monitoring import (
    FREQUENCY_INTERVALS,
    MAX_BACKOFF_INTERVAL,
    ChangeDetection,
    CheckResult,
    PolitenessPolicy,
    SourceMonitor,
    calculate_next_check,
    calculate_urgency,
)
from src.knowledge.storage import SourceEntry, SourceRegistry


# =============================================================================
# Fixtures
# =============================================================================


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
        proposal_discussion=None,
        implementation_issue=None,
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
def mock_registry(sample_source: SourceEntry, new_source: SourceEntry) -> MagicMock:
    """Create a mock source registry."""
    registry = MagicMock(spec=SourceRegistry)
    registry.list_sources.return_value = [sample_source, new_source]
    return registry


# =============================================================================
# CheckResult Tests
# =============================================================================


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_check_result_creation(self) -> None:
        """CheckResult should be created with required fields."""
        result = CheckResult(
            source_url="https://example.com/page",
            checked_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            status="unchanged",
        )

        assert result.source_url == "https://example.com/page"
        assert result.status == "unchanged"
        assert result.http_status is None
        assert result.etag is None
        assert result.content_hash is None
        assert result.detection_method is None
        assert result.error_message is None

    def test_check_result_to_dict(self) -> None:
        """CheckResult should serialize to dictionary."""
        checked_at = datetime(2025, 12, 26, 12, 0, 0, tzinfo=timezone.utc)
        result = CheckResult(
            source_url="https://example.com/page",
            checked_at=checked_at,
            status="changed",
            http_status=200,
            etag='"new-etag"',
            content_hash="newhash123",
            detection_method="content_hash",
        )

        data = result.to_dict()

        assert data["source_url"] == "https://example.com/page"
        assert data["checked_at"] == "2025-12-26T12:00:00+00:00"
        assert data["status"] == "changed"
        assert data["http_status"] == 200
        assert data["etag"] == '"new-etag"'
        assert data["content_hash"] == "newhash123"
        assert data["detection_method"] == "content_hash"
        assert data["error_message"] is None

    def test_check_result_error_status(self) -> None:
        """CheckResult should handle error status."""
        result = CheckResult(
            source_url="https://example.com/page",
            checked_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            status="error",
            error_message="Connection refused",
        )

        assert result.status == "error"
        assert result.error_message == "Connection refused"


# =============================================================================
# ChangeDetection Tests
# =============================================================================


class TestChangeDetection:
    """Tests for ChangeDetection dataclass."""

    def test_change_detection_is_initial_true(self) -> None:
        """is_initial should be True for initial acquisitions."""
        detection = ChangeDetection(
            source_url="https://example.com/new",
            source_name="New Source",
            detected_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            detection_method="initial",
            change_type="initial",
            previous_hash=None,
            previous_checked=None,
            current_etag=None,
            current_last_modified=None,
            current_hash=None,
            urgency="high",
        )

        assert detection.is_initial is True

    def test_change_detection_is_initial_false(self) -> None:
        """is_initial should be False for content updates."""
        detection = ChangeDetection(
            source_url="https://example.com/existing",
            source_name="Existing Source",
            detected_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            detection_method="content_hash",
            change_type="content",
            previous_hash="oldhash",
            previous_checked=datetime(2025, 12, 25, tzinfo=timezone.utc),
            current_etag='"new-etag"',
            current_last_modified="Thu, 26 Dec 2025 10:00:00 GMT",
            current_hash="newhash",
            urgency="normal",
        )

        assert detection.is_initial is False

    def test_change_detection_to_dict(self) -> None:
        """ChangeDetection should serialize to dictionary."""
        detected_at = datetime(2025, 12, 26, 12, 0, 0, tzinfo=timezone.utc)
        previous_checked = datetime(2025, 12, 25, 10, 0, 0, tzinfo=timezone.utc)

        detection = ChangeDetection(
            source_url="https://example.com/page",
            source_name="Test Page",
            detected_at=detected_at,
            detection_method="etag",
            change_type="content",
            previous_hash="oldhash123",
            previous_checked=previous_checked,
            current_etag='"new-etag"',
            current_last_modified="Thu, 26 Dec 2025 10:00:00 GMT",
            current_hash="newhash456",
            urgency="high",
        )

        data = detection.to_dict()

        assert data["source_url"] == "https://example.com/page"
        assert data["source_name"] == "Test Page"
        assert data["detected_at"] == "2025-12-26T12:00:00+00:00"
        assert data["detection_method"] == "etag"
        assert data["change_type"] == "content"
        assert data["previous_hash"] == "oldhash123"
        assert data["previous_checked"] == "2025-12-25T10:00:00+00:00"
        assert data["current_etag"] == '"new-etag"'
        assert data["current_hash"] == "newhash456"
        assert data["urgency"] == "high"
        assert data["is_initial"] is False

    def test_change_detection_to_dict_null_previous(self) -> None:
        """ChangeDetection should handle null previous_checked."""
        detection = ChangeDetection(
            source_url="https://example.com/new",
            source_name="New Source",
            detected_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            detection_method="initial",
            change_type="initial",
            previous_hash=None,
            previous_checked=None,
            current_etag=None,
            current_last_modified=None,
            current_hash=None,
        )

        data = detection.to_dict()
        assert data["previous_checked"] is None


# =============================================================================
# PolitenessPolicy Tests
# =============================================================================


class TestPolitenessPolicy:
    """Tests for PolitenessPolicy dataclass."""

    def test_politeness_policy_defaults(self) -> None:
        """PolitenessPolicy should have sensible defaults."""
        policy = PolitenessPolicy()

        assert policy.min_delay_seconds == 1.0
        assert policy.max_delay_seconds == 60.0
        assert policy.backoff_factor == 2.0
        assert policy.max_failures == 5
        assert policy.respect_robots_txt is True
        assert "speculum-principum" in policy.user_agent


# =============================================================================
# Utility Function Tests
# =============================================================================


class TestCalculateNextCheck:
    """Tests for calculate_next_check function."""

    def test_success_uses_base_interval(self, sample_source: SourceEntry) -> None:
        """Successful check should use base frequency interval."""
        sample_source.update_frequency = "daily"
        
        next_check = calculate_next_check(sample_source, check_failed=False)
        expected_interval = FREQUENCY_INTERVALS["daily"]
        
        # Should be approximately now + 24 hours
        now = datetime.now(timezone.utc)
        assert next_check > now
        assert next_check < now + expected_interval + timedelta(seconds=5)

    def test_failure_applies_backoff(self, sample_source: SourceEntry) -> None:
        """Failed check should apply exponential backoff."""
        sample_source.update_frequency = "daily"
        sample_source.check_failures = 2  # Already 2 failures
        
        next_check = calculate_next_check(sample_source, check_failed=True)
        
        # With 2 existing failures, next failure = 3, so 2^3 = 8x multiplier
        base_interval = FREQUENCY_INTERVALS["daily"]
        expected_interval = base_interval * 8
        
        now = datetime.now(timezone.utc)
        assert next_check > now + base_interval  # More than base interval
        assert next_check < now + expected_interval + timedelta(seconds=5)

    def test_backoff_caps_at_max(self, sample_source: SourceEntry) -> None:
        """Backoff should not exceed maximum interval."""
        sample_source.update_frequency = "frequent"  # 6 hours
        sample_source.check_failures = 10  # Would be 2^11 = 2048x without cap
        
        next_check = calculate_next_check(sample_source, check_failed=True)
        
        now = datetime.now(timezone.utc)
        # Should be capped at 7 days
        assert next_check <= now + MAX_BACKOFF_INTERVAL + timedelta(seconds=5)

    def test_unknown_frequency_uses_default(self, sample_source: SourceEntry) -> None:
        """Unknown frequency should use default interval."""
        sample_source.update_frequency = "nonexistent"
        
        next_check = calculate_next_check(sample_source, check_failed=False)
        
        # Default is 24 hours
        now = datetime.now(timezone.utc)
        assert next_check > now
        assert next_check < now + timedelta(hours=25)


class TestCalculateUrgency:
    """Tests for calculate_urgency function."""

    def test_initial_primary_is_high(self, sample_source: SourceEntry) -> None:
        """Initial acquisition of primary source should be high urgency."""
        sample_source.source_type = "primary"
        assert calculate_urgency(sample_source, is_initial=True) == "high"

    def test_initial_derived_official_is_normal(self, sample_source: SourceEntry) -> None:
        """Initial acquisition of official derived source should be normal."""
        sample_source.source_type = "derived"
        sample_source.is_official = True
        assert calculate_urgency(sample_source, is_initial=True) == "normal"

    def test_initial_reference_is_low(self, sample_source: SourceEntry) -> None:
        """Initial acquisition of reference source should be low urgency."""
        sample_source.source_type = "reference"
        assert calculate_urgency(sample_source, is_initial=True) == "low"

    def test_update_primary_is_high(self, sample_source: SourceEntry) -> None:
        """Update to primary source should be high urgency."""
        sample_source.source_type = "primary"
        assert calculate_urgency(sample_source, is_initial=False) == "high"

    def test_update_derived_is_normal(self, sample_source: SourceEntry) -> None:
        """Update to derived source should be normal urgency."""
        sample_source.source_type = "derived"
        sample_source.added_at = datetime(2025, 1, 1, tzinfo=timezone.utc)  # Old
        assert calculate_urgency(sample_source, is_initial=False) == "normal"

    def test_recent_source_gets_boost(self, sample_source: SourceEntry) -> None:
        """Recently added sources should get urgency boost."""
        sample_source.source_type = "derived"
        sample_source.added_at = datetime.now(timezone.utc) - timedelta(days=3)
        
        # Derived + recent = high (boosted from normal)
        assert calculate_urgency(sample_source, is_initial=False) == "high"


# =============================================================================
# SourceMonitor Tests
# =============================================================================


class TestSourceMonitorInit:
    """Tests for SourceMonitor initialization."""

    def test_creates_session_with_user_agent(self, mock_registry: MagicMock) -> None:
        """SourceMonitor should configure session with user agent."""
        monitor = SourceMonitor(registry=mock_registry)
        
        assert "User-Agent" in monitor._session.headers
        assert "speculum-principum" in monitor._session.headers["User-Agent"]


class TestGetSourcesPendingInitial:
    """Tests for get_sources_pending_initial method."""

    def test_returns_sources_without_hash(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
        new_source: SourceEntry,
    ) -> None:
        """Should return only sources with no content hash."""
        mock_registry.list_sources.return_value = [sample_source, new_source]
        monitor = SourceMonitor(registry=mock_registry)
        
        pending = monitor.get_sources_pending_initial()
        
        assert len(pending) == 1
        assert pending[0].url == new_source.url
        mock_registry.list_sources.assert_called_once_with(status="active")


class TestGetSourcesDueForCheck:
    """Tests for get_sources_due_for_check method."""

    def test_returns_sources_past_check_time(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Should return sources whose next_check_after has passed."""
        sample_source.next_check_after = datetime.now(timezone.utc) - timedelta(hours=1)
        mock_registry.list_sources.return_value = [sample_source]
        monitor = SourceMonitor(registry=mock_registry)
        
        due = monitor.get_sources_due_for_check()
        
        assert len(due) == 1
        assert due[0].url == sample_source.url

    def test_excludes_sources_not_yet_due(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Should exclude sources whose next_check_after is in the future."""
        sample_source.next_check_after = datetime.now(timezone.utc) + timedelta(hours=1)
        mock_registry.list_sources.return_value = [sample_source]
        monitor = SourceMonitor(registry=mock_registry)
        
        due = monitor.get_sources_due_for_check()
        
        assert len(due) == 0

    def test_includes_sources_with_null_next_check(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Should include sources with null next_check_after."""
        sample_source.next_check_after = None
        mock_registry.list_sources.return_value = [sample_source]
        monitor = SourceMonitor(registry=mock_registry)
        
        due = monitor.get_sources_due_for_check()
        
        assert len(due) == 1

    def test_excludes_sources_without_hash(
        self,
        mock_registry: MagicMock,
        new_source: SourceEntry,
    ) -> None:
        """Should exclude sources that haven't been acquired yet."""
        mock_registry.list_sources.return_value = [new_source]
        monitor = SourceMonitor(registry=mock_registry)
        
        due = monitor.get_sources_due_for_check()
        
        assert len(due) == 0


class TestCheckSourceInitialMode:
    """Tests for check_source in initial acquisition mode."""

    def test_initial_source_returns_initial_status(
        self,
        mock_registry: MagicMock,
        new_source: SourceEntry,
    ) -> None:
        """Source without content hash should return initial status."""
        monitor = SourceMonitor(registry=mock_registry)
        
        result = monitor.check_source(new_source)
        
        assert result.status == "initial"
        assert result.detection_method == "initial"

    def test_initial_source_skips_http_requests(
        self,
        mock_registry: MagicMock,
        new_source: SourceEntry,
    ) -> None:
        """Initial acquisition should not make HTTP requests."""
        monitor = SourceMonitor(registry=mock_registry)
        
        with patch.object(monitor._session, "head") as mock_head:
            with patch.object(monitor._session, "get") as mock_get:
                result = monitor.check_source(new_source)
                
                mock_head.assert_not_called()
                mock_get.assert_not_called()
                assert result.status == "initial"


class TestCheckSourceUpdateMode:
    """Tests for check_source in update monitoring mode."""

    def test_unchanged_etag_returns_unchanged(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Matching ETag should return unchanged status."""
        monitor = SourceMonitor(registry=mock_registry)
        
        mock_response = MagicMock()
        mock_response.headers = {"ETag": sample_source.last_etag}
        mock_response.status_code = 200
        
        with patch.object(monitor._session, "head", return_value=mock_response):
            result = monitor.check_source(sample_source)
            
            assert result.status == "unchanged"
            assert result.etag == sample_source.last_etag

    def test_changed_etag_triggers_hash_check(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Changed ETag should trigger content hash verification."""
        monitor = SourceMonitor(registry=mock_registry)
        
        head_response = MagicMock()
        head_response.headers = {"ETag": '"new-etag"'}
        head_response.status_code = 200
        
        get_response = MagicMock()
        get_response.headers = {"ETag": '"new-etag"', "Last-Modified": "new-date"}
        get_response.status_code = 200
        get_response.content = b"new content here"
        
        with patch.object(monitor._session, "head", return_value=head_response):
            with patch.object(monitor._session, "get", return_value=get_response):
                result = monitor.check_source(sample_source)
                
                # Should have done a GET to verify content
                monitor._session.get.assert_called_once()
                assert result.content_hash is not None

    def test_unchanged_content_hash(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Content with same hash should return unchanged."""
        monitor = SourceMonitor(registry=mock_registry)
        
        # Compute the expected hash for test content
        test_content = b"test content"
        from src.parsing import utils
        expected_hash = utils.sha256_bytes(test_content)
        sample_source.last_content_hash = expected_hash
        sample_source.last_etag = None  # Force full hash check
        sample_source.last_modified_header = None
        
        get_response = MagicMock()
        get_response.headers = {}
        get_response.status_code = 200
        get_response.content = test_content
        
        with patch.object(monitor._session, "get", return_value=get_response):
            result = monitor.check_source(sample_source, force_full=True)
            
            assert result.status == "unchanged"
            assert result.content_hash == expected_hash

    def test_changed_content_hash(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Content with different hash should return changed."""
        monitor = SourceMonitor(registry=mock_registry)
        sample_source.last_etag = None  # Force full hash check
        sample_source.last_modified_header = None
        
        get_response = MagicMock()
        get_response.headers = {}
        get_response.status_code = 200
        get_response.content = b"completely different content"
        
        with patch.object(monitor._session, "get", return_value=get_response):
            result = monitor.check_source(sample_source, force_full=True)
            
            assert result.status == "changed"
            assert result.detection_method == "content_hash"


class TestCheckSourceErrors:
    """Tests for error handling in check_source."""

    def test_timeout_returns_error(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Timeout should return error status."""
        monitor = SourceMonitor(registry=mock_registry)
        
        with patch.object(monitor._session, "head", side_effect=requests.Timeout()):
            result = monitor.check_source(sample_source)
            
            assert result.status == "error"
            assert "timed out" in result.error_message.lower()

    def test_ssl_error_returns_error(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """SSL error should return error status."""
        monitor = SourceMonitor(registry=mock_registry)
        
        with patch.object(monitor._session, "head", side_effect=SSLError("cert error")):
            result = monitor.check_source(sample_source)
            
            assert result.status == "error"
            assert "ssl" in result.error_message.lower()

    def test_connection_error_returns_error(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Connection error should return error status."""
        monitor = SourceMonitor(registry=mock_registry)
        
        with patch.object(
            monitor._session, "head",
            side_effect=requests.ConnectionError("connection refused"),
        ):
            result = monitor.check_source(sample_source)
            
            assert result.status == "error"
            assert result.error_message is not None


class TestCreateChangeDetection:
    """Tests for create_change_detection method."""

    def test_creates_detection_for_initial(
        self,
        mock_registry: MagicMock,
        new_source: SourceEntry,
    ) -> None:
        """Should create ChangeDetection for initial acquisition."""
        monitor = SourceMonitor(registry=mock_registry)
        result = CheckResult(
            source_url=new_source.url,
            checked_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            status="initial",
            detection_method="initial",
        )
        
        detection = monitor.create_change_detection(new_source, result)
        
        assert detection.is_initial is True
        assert detection.change_type == "initial"
        assert detection.source_name == new_source.name
        assert detection.previous_hash is None

    def test_creates_detection_for_change(
        self,
        mock_registry: MagicMock,
        sample_source: SourceEntry,
    ) -> None:
        """Should create ChangeDetection for content update."""
        monitor = SourceMonitor(registry=mock_registry)
        result = CheckResult(
            source_url=sample_source.url,
            checked_at=datetime(2025, 12, 26, tzinfo=timezone.utc),
            status="changed",
            detection_method="content_hash",
            etag='"new-etag"',
            content_hash="newhash123",
        )
        
        detection = monitor.create_change_detection(sample_source, result)
        
        assert detection.is_initial is False
        assert detection.change_type == "content"
        assert detection.previous_hash == sample_source.last_content_hash
        assert detection.current_hash == "newhash123"
        assert detection.urgency == "high"  # Primary source
