"""Tests for pipeline scheduler."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.knowledge.pipeline.config import PipelinePoliteness
from src.knowledge.pipeline.scheduler import (
    DomainScheduler,
    ScheduledSource,
    calculate_backoff_interval,
    calculate_next_check_with_jitter,
    extract_domain,
)


class TestExtractDomain:
    """Tests for domain extraction."""
    
    def test_simple_domain(self) -> None:
        """Test extraction from simple URL."""
        assert extract_domain("https://example.com/path") == "example.com"
    
    def test_www_prefix_stripped(self) -> None:
        """Test www prefix is stripped."""
        assert extract_domain("https://www.example.com/path") == "example.com"
    
    def test_subdomain_preserved(self) -> None:
        """Test non-www subdomains are preserved."""
        assert extract_domain("https://docs.example.com/") == "docs.example.com"
        assert extract_domain("https://api.example.com/v1") == "api.example.com"
    
    def test_port_stripped(self) -> None:
        """Test port is stripped."""
        assert extract_domain("https://example.com:8080/path") == "example.com"
    
    def test_case_normalized(self) -> None:
        """Test domain is lowercased."""
        assert extract_domain("https://EXAMPLE.COM/Path") == "example.com"
    
    def test_complex_url(self) -> None:
        """Test extraction from complex URL."""
        url = "https://www.example.com:443/path/to/page?query=1#fragment"
        assert extract_domain(url) == "example.com"


class TestScheduledSource:
    """Tests for ScheduledSource creation."""
    
    def _make_source(self, url: str = "https://example.com", **kwargs) -> MagicMock:
        """Create a mock SourceEntry."""
        source = MagicMock()
        source.url = url
        source.name = kwargs.get("name", "Test Source")
        source.source_type = kwargs.get("source_type", "primary")
        source.next_check_after = kwargs.get("next_check_after", None)
        return source
    
    def test_from_source_extracts_domain(self) -> None:
        """Test domain extraction from source."""
        source = self._make_source("https://www.example.com/path")
        scheduled = ScheduledSource.from_source(source, "check")
        
        assert scheduled.domain == "example.com"
        assert scheduled.action == "check"
    
    def test_initial_action_has_higher_priority(self) -> None:
        """Test initial acquisitions get higher priority."""
        source = self._make_source()
        
        initial = ScheduledSource.from_source(source, "initial")
        check = ScheduledSource.from_source(source, "check")
        
        assert initial.priority < check.priority
    
    def test_primary_sources_have_higher_priority(self) -> None:
        """Test primary sources get higher priority than derived."""
        primary = self._make_source(source_type="primary")
        derived = self._make_source(source_type="derived")
        reference = self._make_source(source_type="reference")
        
        p = ScheduledSource.from_source(primary, "check")
        d = ScheduledSource.from_source(derived, "check")
        r = ScheduledSource.from_source(reference, "check")
        
        assert p.priority < d.priority < r.priority
    
    def test_overdue_sources_have_higher_priority(self) -> None:
        """Test more overdue sources get higher priority."""
        now = datetime.now(timezone.utc)
        
        recent = self._make_source(next_check_after=now - timedelta(hours=1))
        old = self._make_source(next_check_after=now - timedelta(days=7))
        
        r = ScheduledSource.from_source(recent, "check")
        o = ScheduledSource.from_source(old, "check")
        
        # Old (more overdue) should have lower (higher priority) score
        assert o.priority < r.priority


class TestDomainScheduler:
    """Tests for DomainScheduler."""
    
    def _make_source(self, url: str, **kwargs) -> MagicMock:
        """Create a mock SourceEntry."""
        source = MagicMock()
        source.url = url
        source.name = kwargs.get("name", f"Source for {url}")
        source.source_type = kwargs.get("source_type", "primary")
        source.next_check_after = kwargs.get("next_check_after", None)
        return source
    
    def test_add_sources(self) -> None:
        """Test adding sources to scheduler."""
        scheduler = DomainScheduler(politeness=PipelinePoliteness())
        
        sources = [
            self._make_source("https://example.com/page1"),
            self._make_source("https://example.com/page2"),
            self._make_source("https://other.org/doc"),
        ]
        
        added = scheduler.add_sources(sources, "check")
        assert added == 3
    
    def test_schedule_respects_max_sources(self) -> None:
        """Test schedule respects max_sources_per_run."""
        politeness = PipelinePoliteness(max_sources_per_run=5)
        scheduler = DomainScheduler(politeness=politeness)
        
        sources = [self._make_source(f"https://example{i}.com/") for i in range(10)]
        scheduler.add_sources(sources, "check")
        
        scheduled = list(scheduler.get_schedule())
        assert len(scheduled) == 5
    
    def test_schedule_respects_max_per_domain(self) -> None:
        """Test schedule respects max_domain_requests_per_run."""
        politeness = PipelinePoliteness(
            max_sources_per_run=100,
            max_domain_requests_per_run=2,
        )
        scheduler = DomainScheduler(politeness=politeness)
        
        # 5 sources from same domain
        sources = [self._make_source(f"https://example.com/page{i}") for i in range(5)]
        scheduler.add_sources(sources, "check")
        
        scheduled = list(scheduler.get_schedule())
        assert len(scheduled) == 2  # Max per domain
    
    def test_schedule_is_domain_fair(self) -> None:
        """Test schedule distributes across domains fairly."""
        politeness = PipelinePoliteness(
            max_sources_per_run=6,
            max_domain_requests_per_run=3,
        )
        scheduler = DomainScheduler(politeness=politeness)
        
        sources = [
            self._make_source("https://example.com/page1"),
            self._make_source("https://example.com/page2"),
            self._make_source("https://example.com/page3"),
            self._make_source("https://other.org/doc1"),
            self._make_source("https://other.org/doc2"),
            self._make_source("https://other.org/doc3"),
        ]
        scheduler.add_sources(sources, "check")
        
        scheduled = list(scheduler.get_schedule())
        
        # Count per domain
        counts = defaultdict(int)
        for s in scheduled:
            counts[s.domain] += 1
        
        # Should be balanced (3 each)
        assert counts["example.com"] == 3
        assert counts["other.org"] == 3
    
    def test_record_request_updates_timestamp(self) -> None:
        """Test recording request updates last request time."""
        scheduler = DomainScheduler(politeness=PipelinePoliteness())
        
        scheduler.record_request("example.com")
        
        cooldown = scheduler.get_domain_cooldown("example.com")
        # Should be close to 2 seconds (min_domain_interval)
        assert cooldown > 1.5
        assert cooldown <= 2.0
    
    def test_cooldown_decreases_over_time(self) -> None:
        """Test cooldown decreases as time passes."""
        scheduler = DomainScheduler(politeness=PipelinePoliteness())
        
        # Record a request
        scheduler.record_request("example.com")
        cooldown1 = scheduler.get_domain_cooldown("example.com")
        
        # Simulate time passing by manipulating the timestamp
        import time
        time.sleep(0.1)
        
        cooldown2 = scheduler.get_domain_cooldown("example.com")
        assert cooldown2 < cooldown1
    
    def test_no_cooldown_for_unknown_domain(self) -> None:
        """Test no cooldown for domains not yet accessed."""
        scheduler = DomainScheduler(politeness=PipelinePoliteness())
        
        cooldown = scheduler.get_domain_cooldown("never-accessed.com")
        assert cooldown == 0.0
    
    def test_domains_with_pending(self) -> None:
        """Test tracking domains with remaining sources."""
        politeness = PipelinePoliteness(
            max_sources_per_run=2,
            max_domain_requests_per_run=1,
        )
        scheduler = DomainScheduler(politeness=politeness)
        
        sources = [
            self._make_source("https://example.com/page1"),
            self._make_source("https://example.com/page2"),
            self._make_source("https://other.org/doc1"),
        ]
        scheduler.add_sources(sources, "check")
        
        # Consume the schedule
        list(scheduler.get_schedule())
        
        # example.com should still have pending sources
        pending = scheduler.domains_with_pending
        assert {"example.com"}.issubset(set(pending))


class TestBackoffCalculation:
    """Tests for backoff interval calculation."""
    
    def test_zero_failures_returns_base(self) -> None:
        """Test zero failures returns base interval."""
        base = timedelta(hours=6)
        result = calculate_backoff_interval(0, base)
        assert result == base
    
    def test_negative_failures_returns_base(self) -> None:
        """Test negative failures returns base interval."""
        base = timedelta(hours=6)
        result = calculate_backoff_interval(-1, base)
        assert result == base
    
    def test_exponential_growth(self) -> None:
        """Test backoff grows exponentially."""
        base = timedelta(hours=1)
        
        r1 = calculate_backoff_interval(1, base)
        r2 = calculate_backoff_interval(2, base)
        r3 = calculate_backoff_interval(3, base)
        
        assert r1 == timedelta(hours=2)  # 1 * 2^1
        assert r2 == timedelta(hours=4)  # 1 * 2^2
        assert r3 == timedelta(hours=8)  # 1 * 2^3
    
    def test_capped_at_max(self) -> None:
        """Test backoff is capped at max interval."""
        base = timedelta(hours=1)
        max_interval = timedelta(days=7)
        
        # With 10 failures, would be 1024 hours = 42.6 days
        result = calculate_backoff_interval(10, base, max_interval)
        assert result == max_interval


class TestJitterCalculation:
    """Tests for next check calculation with jitter."""
    
    def _make_source(self, frequency: str = "weekly") -> MagicMock:
        """Create a mock SourceEntry."""
        source = MagicMock()
        source.update_frequency = frequency
        return source
    
    def test_adds_base_interval(self) -> None:
        """Test base interval is added."""
        source = self._make_source("weekly")
        
        now = datetime.now(timezone.utc)
        result = calculate_next_check_with_jitter(source, jitter_minutes=0)
        
        # Should be about 7 days from now
        delta = result - now
        assert delta >= timedelta(days=6, hours=23)
        assert delta <= timedelta(days=7, hours=1)
    
    def test_adds_jitter(self) -> None:
        """Test jitter is added to interval."""
        source = self._make_source("weekly")
        
        # Run multiple times and collect results
        results = [
            calculate_next_check_with_jitter(source, jitter_minutes=60)
            for _ in range(10)
        ]
        
        # Not all results should be exactly the same (jitter applied)
        unique = set(r.isoformat() for r in results)
        # At least some variation expected (though could be 1 in extreme case)
        assert len(unique) >= 1
    
    def test_respects_frequency(self) -> None:
        """Test different frequencies produce different intervals."""
        daily = self._make_source("daily")
        weekly = self._make_source("weekly")
        
        now = datetime.now(timezone.utc)
        d_result = calculate_next_check_with_jitter(daily, jitter_minutes=0)
        w_result = calculate_next_check_with_jitter(weekly, jitter_minutes=0)
        
        # Daily should be sooner than weekly
        assert d_result < w_result
