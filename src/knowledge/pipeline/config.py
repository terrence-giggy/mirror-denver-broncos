"""Configuration for the content pipeline.

This module defines configuration dataclasses for pipeline execution,
including politeness settings that control rate limiting and scheduling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True)
class PipelinePoliteness:
    """Rate limiting and politeness configuration.
    
    These settings ensure the pipeline is a good citizen when accessing
    external websites, preventing excessive load on source servers.
    
    Attributes:
        min_domain_interval: Minimum time between requests to the same domain.
            This prevents hammering a single server with rapid requests.
        max_domain_requests_per_run: Maximum pages to fetch from one domain
            in a single pipeline run. Spreads load across domains.
        max_sources_per_run: Maximum sources to process per workflow run.
            Sources not processed will be picked up in the next run.
        max_total_requests_per_run: Hard limit on total HTTP requests per run.
            Prevents runaway execution.
        check_jitter_minutes: Random offset (0 to N minutes) added to 
            next_check_after timestamps. Prevents predictable access patterns.
        crawler_delay_seconds: Delay between page fetches during crawling.
            Applied between every page fetch within a crawl.
        respect_robots_crawl_delay: If True, use Crawl-delay from robots.txt
            when it exceeds our default delay.
    """
    
    # Per-domain limits
    min_domain_interval: timedelta = field(default_factory=lambda: timedelta(seconds=2))
    max_domain_requests_per_run: int = 10
    
    # Per-run limits
    max_sources_per_run: int = 20
    max_total_requests_per_run: int = 100
    
    # Scheduling
    check_jitter_minutes: int = 60
    
    # Crawler settings
    crawler_delay_seconds: float = 1.0
    respect_robots_crawl_delay: bool = True


@dataclass
class PipelineConfig:
    """Configuration for a pipeline run.
    
    Attributes:
        politeness: Rate limiting and politeness settings.
        kb_root: Path to knowledge graph root. Uses default if None.
        evidence_root: Path to evidence storage root. Uses default if None.
        dry_run: If True, simulate execution without making changes.
        create_issues: If True, create GitHub Issues for errors/exceptions.
            When False, errors are only logged.
        mode: Execution mode - "full", "check", or "acquire".
            - "full": Run both detection and acquisition
            - "check": Detection only (monitor for changes)
            - "acquire": Acquisition only (process pending sources)
        github_client: Optional GitHub storage client for Actions environment.
    """
    
    politeness: PipelinePoliteness = field(default_factory=PipelinePoliteness)
    kb_root: "Path | None" = None
    evidence_root: "Path | None" = None
    dry_run: bool = False
    create_issues: bool = False
    mode: str = "full"  # "full" | "check" | "acquire"
    github_client: object = None  # GitHubStorageClient
    
    def __post_init__(self) -> None:
        """Validate configuration."""
        valid_modes = ("full", "check", "acquire")
        if self.mode not in valid_modes:
            raise ValueError(f"Invalid mode: {self.mode}. Must be one of {valid_modes}")


# Default check intervals by update frequency
CHECK_INTERVALS: dict[str, timedelta] = {
    "frequent": timedelta(hours=6),
    "daily": timedelta(hours=24),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    "unknown": timedelta(days=7),  # Conservative default
}


def get_check_interval(update_frequency: str | None) -> timedelta:
    """Get the check interval for a given update frequency.
    
    Args:
        update_frequency: The source's update frequency setting.
        
    Returns:
        timedelta: How long to wait between checks.
    """
    return CHECK_INTERVALS.get(update_frequency or "unknown", timedelta(days=7))
