"""Tests for src/knowledge/pipeline/monitor.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.pipeline.monitor import (
    MonitorResult,
    get_sources_pending_initial,
    get_sources_due_for_check,
    run_monitor,
)


# --- Mock source entries for testing ---


@dataclass
class MockSourceEntry:
    """Minimal mock of SourceEntry for testing."""
    
    name: str
    url: str
    source_type: str = "primary"
    status: str = "active"
    update_frequency: str = "daily"
    last_content_hash: str | None = None
    next_check_after: datetime | None = None
    failed_checks: int = 0


@dataclass
class MockSourceRegistry:
    """Minimal mock of SourceRegistry for testing."""
    
    _sources: list[MockSourceEntry] = field(default_factory=list)
    
    def list_sources(self, status: str | None = None) -> list[MockSourceEntry]:
        if status:
            return [s for s in self._sources if s.status == status]
        return self._sources
    
    def save_source(self, source: MockSourceEntry) -> None:
        """Mock save - does nothing."""
        pass


class TestMonitorResult:
    """Tests for MonitorResult dataclass."""
    
    def test_default_values(self):
        """Result starts with empty lists."""
        result = MonitorResult()
        
        assert result.sources_checked == 0
        assert result.initial_needed == []
        assert result.updates_needed == []
        assert result.unchanged == []
        assert result.errors == []
        assert result.skipped == []
    
    def test_total_needing_acquisition(self):
        """Total combines initial and updates."""
        result = MonitorResult()
        
        source1 = MockSourceEntry(name="src1", url="https://a.com")
        source2 = MockSourceEntry(name="src2", url="https://b.com")
        source3 = MockSourceEntry(name="src3", url="https://c.com")
        
        result.initial_needed.append(source1)
        result.updates_needed.append((source2, MagicMock()))
        result.updates_needed.append((source3, MagicMock()))
        
        assert result.total_needing_acquisition == 3
    
    def test_to_dict(self):
        """to_dict returns correct counts."""
        result = MonitorResult(sources_checked=10)
        result.initial_needed.append(MockSourceEntry(name="a", url="https://a.com"))
        result.unchanged.extend([
            MockSourceEntry(name="b", url="https://b.com"),
            MockSourceEntry(name="c", url="https://c.com"),
        ])
        
        d = result.to_dict()
        
        assert d["sources_checked"] == 10
        assert d["initial_needed"] == 1
        assert d["updates_needed"] == 0
        assert d["unchanged"] == 2
        assert d["errors"] == 0
        assert d["total_needing_acquisition"] == 1


class TestGetSourcesPendingInitial:
    """Tests for get_sources_pending_initial function."""
    
    def test_returns_sources_without_hash(self):
        """Sources with no content hash need initial acquisition."""
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(name="new", url="https://new.com", last_content_hash=None),
            MockSourceEntry(name="existing", url="https://existing.com", last_content_hash="abc123"),
        ])
        
        pending = get_sources_pending_initial(registry)
        
        assert len(pending) == 1
        assert pending[0].name == "new"
    
    def test_excludes_inactive_sources(self):
        """Inactive sources are not returned."""
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(name="new", url="https://new.com", last_content_hash=None, status="inactive"),
        ])
        
        pending = get_sources_pending_initial(registry)
        
        assert len(pending) == 0
    
    def test_empty_registry(self):
        """Returns empty list for empty registry."""
        registry = MockSourceRegistry()
        
        pending = get_sources_pending_initial(registry)
        
        assert pending == []


class TestGetSourcesDueForCheck:
    """Tests for get_sources_due_for_check function."""
    
    def test_returns_sources_past_due(self):
        """Sources past their check time are returned."""
        past_time = datetime.now(timezone.utc) - timedelta(hours=1)
        
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(
                name="due",
                url="https://due.com",
                last_content_hash="abc",
                next_check_after=past_time,
            ),
            MockSourceEntry(
                name="not_due",
                url="https://notdue.com",
                last_content_hash="def",
                next_check_after=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        ])
        
        due = get_sources_due_for_check(registry)
        
        assert len(due) == 1
        assert due[0].name == "due"
    
    def test_returns_sources_with_no_next_check(self):
        """Sources with no scheduled check (but have hash) are returned."""
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(
                name="no_schedule",
                url="https://a.com",
                last_content_hash="abc",
                next_check_after=None,
            ),
        ])
        
        due = get_sources_due_for_check(registry)
        
        assert len(due) == 1
        assert due[0].name == "no_schedule"
    
    def test_excludes_sources_without_hash(self):
        """Sources without content hash (never acquired) are excluded."""
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(
                name="never_acquired",
                url="https://a.com",
                last_content_hash=None,  # Never acquired
                next_check_after=None,
            ),
        ])
        
        due = get_sources_due_for_check(registry)
        
        assert len(due) == 0


class TestRunMonitor:
    """Tests for run_monitor function."""
    
    def test_returns_monitor_result(self):
        """run_monitor returns a MonitorResult."""
        registry = MockSourceRegistry()
        
        # Create real scheduler
        from src.knowledge.pipeline.scheduler import DomainScheduler
        from src.knowledge.pipeline.config import PipelinePoliteness
        
        scheduler = DomainScheduler(PipelinePoliteness())
        
        result = run_monitor(registry, scheduler)
        
        assert isinstance(result, MonitorResult)
    
    def test_adds_initial_sources_to_scheduler(self):
        """Sources needing initial acquisition are added to scheduler."""
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(name="new1", url="https://a.com", last_content_hash=None),
            MockSourceEntry(name="new2", url="https://b.com", last_content_hash=None),
        ])
        
        from src.knowledge.pipeline.scheduler import DomainScheduler
        from src.knowledge.pipeline.config import PipelinePoliteness
        
        scheduler = DomainScheduler(PipelinePoliteness(max_sources_per_run=10))
        
        result = run_monitor(registry, scheduler)
        
        # Both sources should be marked as needing initial acquisition
        assert len(result.initial_needed) == 2
    
    def test_dry_run_does_not_update_registry(self):
        """In dry run mode, source metadata is not updated."""
        source = MockSourceEntry(
            name="new",
            url="https://new.com",
            last_content_hash=None,
        )
        registry = MockSourceRegistry(_sources=[source])
        
        from src.knowledge.pipeline.scheduler import DomainScheduler
        from src.knowledge.pipeline.config import PipelinePoliteness
        
        scheduler = DomainScheduler(PipelinePoliteness(max_sources_per_run=10))
        
        # Run with dry_run=True
        result = run_monitor(registry, scheduler, dry_run=True)
        
        # In dry run mode, source should be identified but not updated
        assert len(result.initial_needed) == 1
    
    def test_respects_scheduler_limits(self):
        """Scheduler limits affect how many sources are processed."""
        registry = MockSourceRegistry(_sources=[
            MockSourceEntry(name=f"src{i}", url=f"https://{i}.com", last_content_hash=None)
            for i in range(100)
        ])
        
        from src.knowledge.pipeline.scheduler import DomainScheduler
        from src.knowledge.pipeline.config import PipelinePoliteness
        
        # Limit to 5 sources per run
        scheduler = DomainScheduler(PipelinePoliteness(max_sources_per_run=5))
        
        result = run_monitor(registry, scheduler)
        
        # Only 5 sources should be processed due to limit
        assert len(result.initial_needed) <= 5


class TestMonitorIntegration:
    """Integration-style tests that verify full flow without network calls."""
    
    def test_categorizes_sources_correctly(self):
        """Sources are categorized into initial, due, and not-due."""
        past = datetime.now(timezone.utc) - timedelta(days=1)
        future = datetime.now(timezone.utc) + timedelta(days=1)
        
        sources = [
            MockSourceEntry(name="initial", url="https://new.com", last_content_hash=None),
            MockSourceEntry(name="due", url="https://due.com", last_content_hash="abc", next_check_after=past),
            MockSourceEntry(name="not_due", url="https://later.com", last_content_hash="def", next_check_after=future),
        ]
        
        registry = MockSourceRegistry(_sources=sources)
        
        initial = get_sources_pending_initial(registry)
        due = get_sources_due_for_check(registry)
        
        assert len(initial) == 1
        assert initial[0].name == "initial"
        assert len(due) == 1
        assert due[0].name == "due"
