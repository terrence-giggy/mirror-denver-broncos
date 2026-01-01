"""Domain-aware scheduling for polite content acquisition.

This module provides fair queuing across domains to prevent hammering
any single server with too many requests. It implements:

1. Domain fairness: Round-robin across domains
2. Per-domain limits: Maximum requests per domain per run
3. Jitter: Randomization of next check times
4. Cooldown tracking: Enforce delays between same-domain requests
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Iterator, Sequence
from urllib.parse import urlparse

if TYPE_CHECKING:
    from src.knowledge.storage import SourceEntry

from .config import PipelinePoliteness, get_check_interval


@dataclass
class ScheduledSource:
    """A source scheduled for processing with domain metadata.
    
    Attributes:
        source: The source entry to process.
        domain: Extracted domain for rate limiting.
        action: What action is needed - "initial" or "check".
        priority: Priority score (lower = higher priority).
    """
    
    source: "SourceEntry"
    domain: str
    action: str  # "initial" | "check"
    priority: float = 0.0
    
    @classmethod
    def from_source(cls, source: "SourceEntry", action: str) -> "ScheduledSource":
        """Create a scheduled source from a SourceEntry.
        
        Args:
            source: The source entry.
            action: The action type ("initial" or "check").
            
        Returns:
            ScheduledSource with extracted domain.
        """
        domain = extract_domain(source.url)
        
        # Calculate priority (lower = process first)
        priority = 0.0
        
        # Initial acquisitions get higher priority
        if action == "initial":
            priority -= 100
        
        # Primary sources get higher priority
        if source.source_type == "primary":
            priority -= 50
        elif source.source_type == "derived":
            priority -= 25
        
        # Older due dates get higher priority
        if source.next_check_after:
            overdue = datetime.now(timezone.utc) - source.next_check_after
            priority -= overdue.total_seconds() / 3600  # Hours overdue
        
        return cls(
            source=source,
            domain=domain,
            action=action,
            priority=priority,
        )


def extract_domain(url: str) -> str:
    """Extract the base domain from a URL for rate limiting.
    
    Args:
        url: The URL to extract domain from.
        
    Returns:
        The domain (e.g., "example.com" from "https://www.example.com/path").
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    
    # Strip www prefix for domain grouping
    if host.startswith("www."):
        host = host[4:]
    
    # Strip port if present
    if ":" in host:
        host = host.split(":")[0]
    
    return host


@dataclass
class DomainScheduler:
    """Schedules sources for processing with domain fairness.
    
    The scheduler ensures:
    1. No more than max_per_domain sources from one domain per run
    2. Round-robin ordering across domains
    3. Total sources limited to max_sources
    4. Cooldown enforcement between same-domain requests
    
    Usage:
        scheduler = DomainScheduler(politeness)
        scheduler.add_sources(sources, action="check")
        
        for scheduled in scheduler.get_schedule():
            scheduler.wait_for_domain(scheduled.domain)
            process(scheduled)
            scheduler.record_request(scheduled.domain)
    """
    
    politeness: PipelinePoliteness
    _sources_by_domain: dict[str, list[ScheduledSource]] = field(
        default_factory=lambda: defaultdict(list)
    )
    _domain_request_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _last_request_by_domain: dict[str, datetime] = field(default_factory=dict)
    _total_scheduled: int = 0
    
    def add_sources(
        self,
        sources: Sequence["SourceEntry"],
        action: str,
    ) -> int:
        """Add sources to the scheduler.
        
        Args:
            sources: Source entries to schedule.
            action: The action type ("initial" or "check").
            
        Returns:
            Number of sources added.
        """
        added = 0
        for source in sources:
            scheduled = ScheduledSource.from_source(source, action)
            self._sources_by_domain[scheduled.domain].append(scheduled)
            added += 1
        
        # Sort each domain's sources by priority
        for domain_sources in self._sources_by_domain.values():
            domain_sources.sort(key=lambda s: s.priority)
        
        return added
    
    def get_schedule(self) -> Iterator[ScheduledSource]:
        """Generate a fair schedule across domains.
        
        Yields sources in round-robin order across domains, respecting:
        - max_sources_per_run total limit
        - max_domain_requests_per_run per-domain limit
        
        Yields:
            ScheduledSource in fair order.
        """
        max_sources = self.politeness.max_sources_per_run
        max_per_domain = self.politeness.max_domain_requests_per_run
        
        yielded = 0
        domain_yielded: dict[str, int] = defaultdict(int)
        
        # Get list of domains with sources
        domains = list(self._sources_by_domain.keys())
        if not domains:
            return
        
        # Round-robin across domains
        domain_index = 0
        empty_rounds = 0
        
        while yielded < max_sources and empty_rounds < len(domains):
            domain = domains[domain_index]
            domain_sources = self._sources_by_domain[domain]
            
            # Check if this domain has more sources and hasn't hit limit
            if domain_sources and domain_yielded[domain] < max_per_domain:
                source = domain_sources.pop(0)
                domain_yielded[domain] += 1
                yielded += 1
                empty_rounds = 0
                yield source
            else:
                empty_rounds += 1
            
            domain_index = (domain_index + 1) % len(domains)
        
        self._total_scheduled = yielded
    
    def record_request(self, domain: str) -> None:
        """Record that a request was made to a domain.
        
        Args:
            domain: The domain that was accessed.
        """
        self._last_request_by_domain[domain] = datetime.now(timezone.utc)
        self._domain_request_counts[domain] += 1
    
    def get_domain_cooldown(self, domain: str) -> float:
        """Get seconds to wait before next request to domain.
        
        Args:
            domain: The domain to check.
            
        Returns:
            Seconds to wait (0 if no wait needed).
        """
        last_request = self._last_request_by_domain.get(domain)
        if last_request is None:
            return 0.0
        
        elapsed = datetime.now(timezone.utc) - last_request
        min_interval = self.politeness.min_domain_interval
        
        if elapsed >= min_interval:
            return 0.0
        
        return (min_interval - elapsed).total_seconds()
    
    @property
    def total_scheduled(self) -> int:
        """Total sources scheduled in the current run."""
        return self._total_scheduled
    
    @property
    def domains_with_pending(self) -> list[str]:
        """Domains that still have unscheduled sources."""
        return [
            domain for domain, sources in self._sources_by_domain.items()
            if sources
        ]


def calculate_next_check_with_jitter(
    source: "SourceEntry",
    jitter_minutes: int = 60,
) -> datetime:
    """Calculate when to next check a source, with jitter.
    
    Adds random jitter to prevent all sources with the same frequency
    from being checked at the exact same time.
    
    Args:
        source: The source to calculate next check for.
        jitter_minutes: Maximum random offset in minutes.
        
    Returns:
        datetime: When the source should next be checked.
    """
    base_interval = get_check_interval(source.update_frequency)
    jitter = timedelta(minutes=random.randint(0, jitter_minutes))
    
    return datetime.now(timezone.utc) + base_interval + jitter


def calculate_backoff_interval(
    failures: int,
    base_interval: timedelta = timedelta(hours=6),
    max_interval: timedelta = timedelta(days=7),
) -> timedelta:
    """Calculate backoff interval after failures.
    
    Uses exponential backoff with a maximum cap.
    
    Args:
        failures: Number of consecutive failures.
        base_interval: Base interval for backoff.
        max_interval: Maximum backoff interval.
        
    Returns:
        timedelta: How long to wait before next attempt.
    """
    if failures <= 0:
        return base_interval
    
    # Exponential backoff: base * 2^failures
    # Cap failures at 20 to avoid overflow (2^20 ≈ 1 million)
    # The max_interval parameter provides the actual cap
    capped_failures = min(failures, 20)
    multiplier = 2 ** capped_failures
    backoff = base_interval * multiplier
    
    return min(backoff, max_interval)
