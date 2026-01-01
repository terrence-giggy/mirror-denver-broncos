"""Monitor logic for content change detection.

This module provides LLM-free change detection for registered sources.
It determines which sources need acquisition (initial or update) without
any LLM involvement - the logic is entirely programmatic.

Two modes of operation:
1. Initial acquisition: Sources with no previous content hash
2. Update checking: Tiered detection (ETag → Last-Modified → Content Hash)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Sequence

if TYPE_CHECKING:
    from src.knowledge.storage import SourceEntry, SourceRegistry

from src.knowledge.monitoring import ChangeDetection, CheckResult, SourceMonitor

from .scheduler import (
    DomainScheduler,
    ScheduledSource,
    calculate_backoff_interval,
    calculate_next_check_with_jitter,
)

logger = logging.getLogger(__name__)


@dataclass
class MonitorResult:
    """Result of running the monitor phase.
    
    Attributes:
        sources_checked: Number of sources that were checked.
        initial_needed: Sources that need initial acquisition.
        updates_needed: Sources that have changed content.
        unchanged: Sources with no detected changes.
        errors: Sources that failed during checking.
        skipped: Sources skipped due to limits.
    """
    
    sources_checked: int = 0
    initial_needed: list["SourceEntry"] = field(default_factory=list)
    updates_needed: list[tuple["SourceEntry", CheckResult]] = field(default_factory=list)
    unchanged: list["SourceEntry"] = field(default_factory=list)
    errors: list[tuple["SourceEntry", str]] = field(default_factory=list)
    skipped: list["SourceEntry"] = field(default_factory=list)
    
    @property
    def total_needing_acquisition(self) -> int:
        """Total sources that need content acquisition."""
        return len(self.initial_needed) + len(self.updates_needed)
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for logging/reporting."""
        return {
            "sources_checked": self.sources_checked,
            "initial_needed": len(self.initial_needed),
            "updates_needed": len(self.updates_needed),
            "unchanged": len(self.unchanged),
            "errors": len(self.errors),
            "skipped": len(self.skipped),
            "total_needing_acquisition": self.total_needing_acquisition,
        }


def get_sources_pending_initial(registry: "SourceRegistry") -> list["SourceEntry"]:
    """Get sources that need initial acquisition.
    
    These are active sources where last_content_hash is None,
    meaning they have never been acquired.
    
    Args:
        registry: The source registry.
        
    Returns:
        List of sources needing initial acquisition.
    """
    return [
        source
        for source in registry.list_sources(status="active")
        if source.last_content_hash is None
    ]


def get_sources_due_for_check(registry: "SourceRegistry") -> list["SourceEntry"]:
    """Get sources that are due for update checking.
    
    These are active sources where:
    1. last_content_hash exists (already acquired)
    2. next_check_after is None or has passed
    
    Args:
        registry: The source registry.
        
    Returns:
        List of sources due for checking.
    """
    now = datetime.now(timezone.utc)
    return [
        source
        for source in registry.list_sources(status="active")
        if source.last_content_hash is not None
        and (source.next_check_after is None or source.next_check_after <= now)
    ]


def run_monitor(
    registry: "SourceRegistry",
    scheduler: DomainScheduler,
    dry_run: bool = False,
    force_fresh: bool = False,
) -> MonitorResult:
    """Run the monitor phase to detect sources needing acquisition.
    
    This function:
    1. Identifies sources needing initial acquisition
    2. Checks due sources for content changes
    3. Updates source metadata (last_checked, next_check_after)
    4. Returns lists of sources needing acquisition
    
    Args:
        registry: The source registry.
        scheduler: Domain scheduler with politeness settings.
        dry_run: If True, don't update source metadata.
        force_fresh: If True, treat all active sources as needing acquisition.
        
    Returns:
        MonitorResult with categorized sources.
    """
    result = MonitorResult()
    monitor = SourceMonitor(registry=registry)
    
    # Force fresh mode: treat all active sources as needing acquisition
    if force_fresh:
        all_sources = list(registry.list_sources(status="active"))
        scheduler.add_sources(all_sources, action="initial")
        logger.info("Force fresh mode: treating all %d active sources as needing acquisition", len(all_sources))
        
        # Process all sources through scheduler
        for scheduled in scheduler.get_schedule():
            source = scheduled.source
            result.sources_checked += 1
            result.initial_needed.append(source)
            logger.info("Queued for fresh acquisition: %s", source.name)
            
            if not dry_run:
                # Update last_checked timestamp
                source.last_checked = datetime.now(timezone.utc)
                registry.save_source(source)
        
        return result
    
    # Normal mode: selective acquisition based on status
    
    # Phase 1: Collect sources needing initial acquisition
    initial_sources = get_sources_pending_initial(registry)
    scheduler.add_sources(initial_sources, action="initial")
    logger.info("Found %d sources needing initial acquisition", len(initial_sources))
    
    # Phase 2: Collect sources due for update check
    check_sources = get_sources_due_for_check(registry)
    scheduler.add_sources(check_sources, action="check")
    logger.info("Found %d sources due for update check", len(check_sources))
    
    # Phase 3: Process scheduled sources
    for scheduled in scheduler.get_schedule():
        source = scheduled.source
        
        # Wait for domain cooldown
        cooldown = scheduler.get_domain_cooldown(scheduled.domain)
        if cooldown > 0:
            import time
            logger.debug("Waiting %.1fs for domain %s cooldown", cooldown, scheduled.domain)
            time.sleep(cooldown)
        
        result.sources_checked += 1
        
        if scheduled.action == "initial":
            # Initial acquisition - no check needed, just queue it
            result.initial_needed.append(source)
            logger.info("Queued for initial acquisition: %s", source.name)
            
            if not dry_run:
                # Update last_checked timestamp
                source.last_checked = datetime.now(timezone.utc)
                registry.save_source(source)
            
        else:
            # Update check - use tiered detection
            try:
                check_result = monitor.check_source(source)
                scheduler.record_request(scheduled.domain)
                
                if check_result.status == "changed":
                    result.updates_needed.append((source, check_result))
                    logger.info(
                        "Change detected in %s via %s",
                        source.name,
                        check_result.detection_method,
                    )
                    
                elif check_result.status == "unchanged":
                    result.unchanged.append(source)
                    logger.debug("No change in %s", source.name)
                    
                elif check_result.status == "error":
                    result.errors.append((source, check_result.error_message or "Unknown error"))
                    logger.warning(
                        "Error checking %s: %s",
                        source.name,
                        check_result.error_message,
                    )
                
                if not dry_run:
                    _update_source_after_check(registry, source, check_result, scheduler)
                    
            except Exception as e:
                result.errors.append((source, str(e)))
                logger.error("Exception checking %s: %s", source.name, e)
                
                if not dry_run:
                    source.check_failures += 1
                    source.last_checked = datetime.now(timezone.utc)
                    source.next_check_after = datetime.now(timezone.utc) + calculate_backoff_interval(
                        source.check_failures
                    )
                    registry.save_source(source)
    
    # Track skipped sources (those not scheduled due to limits)
    result.skipped = [
        ScheduledSource.from_source(s, "check").source
        for domain in scheduler.domains_with_pending
        for s in registry.list_sources(status="active")
        if ScheduledSource.from_source(s, "check").domain == domain
        and s not in result.initial_needed
        and s not in [u[0] for u in result.updates_needed]
        and s not in result.unchanged
        and s not in [e[0] for e in result.errors]
    ][:50]  # Limit to first 50 for logging
    
    logger.info(
        "Monitor complete: %d checked, %d initial, %d updated, %d unchanged, %d errors",
        result.sources_checked,
        len(result.initial_needed),
        len(result.updates_needed),
        len(result.unchanged),
        len(result.errors),
    )
    
    return result


def _update_source_after_check(
    registry: "SourceRegistry",
    source: "SourceEntry",
    check_result: CheckResult,
    scheduler: DomainScheduler,
) -> None:
    """Update source metadata after a check.
    
    Args:
        registry: The source registry.
        source: The source that was checked.
        check_result: Result of the check.
        scheduler: Scheduler with jitter settings.
    """
    source.last_checked = datetime.now(timezone.utc)
    
    if check_result.status == "error":
        # Increment failures and apply backoff
        source.check_failures += 1
        source.next_check_after = datetime.now(timezone.utc) + calculate_backoff_interval(
            source.check_failures
        )
    else:
        # Reset failures on success
        source.check_failures = 0
        source.next_check_after = calculate_next_check_with_jitter(
            source,
            jitter_minutes=scheduler.politeness.check_jitter_minutes,
        )
        
        # Update HTTP metadata if available
        if check_result.etag:
            source.last_etag = check_result.etag
        if check_result.last_modified:
            source.last_modified_header = check_result.last_modified
    
    registry.save_source(source)
