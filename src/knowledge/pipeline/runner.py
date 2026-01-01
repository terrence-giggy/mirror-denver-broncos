"""Unified pipeline runner for content monitoring and acquisition.

This module provides the main entry point for running the content pipeline.
It orchestrates:
1. Monitor phase: Detect sources needing acquisition
2. Crawler phase: Acquire content from detected sources
3. Finalization: Update registry and report results

The pipeline is designed to run without LLM involvement - all logic is
deterministic and programmatic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.integrations.github.storage import GitHubStorageClient

from src import paths
from src.knowledge.storage import SourceRegistry

from .config import PipelineConfig
from .crawler import CrawlerResult, run_crawler
from .monitor import MonitorResult, run_monitor
from .scheduler import DomainScheduler

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Result of running the full content pipeline.
    
    Attributes:
        started_at: When the pipeline started.
        completed_at: When the pipeline finished.
        mode: Execution mode that was used.
        monitor: Results from the monitor phase (if run).
        crawler: Results from the crawler phase (if run).
        dry_run: Whether this was a dry run.
    """
    
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    mode: str = "full"
    monitor: MonitorResult | None = None
    crawler: CrawlerResult | None = None
    dry_run: bool = False
    
    @property
    def duration_seconds(self) -> float:
        """Duration of the pipeline run in seconds."""
        if self.completed_at is None:
            return 0.0
        return (self.completed_at - self.started_at).total_seconds()
    
    @property
    def total_sources_processed(self) -> int:
        """Total sources processed across all phases."""
        total = 0
        if self.monitor:
            total += self.monitor.sources_checked
        return total
    
    @property
    def total_pages_acquired(self) -> int:
        """Total pages acquired."""
        if self.crawler:
            return self.crawler.pages_total
        return 0
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for logging/reporting."""
        return {
            "started_at": self.started_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "mode": self.mode,
            "dry_run": self.dry_run,
            "monitor": self.monitor.to_dict() if self.monitor else None,
            "crawler": self.crawler.to_dict() if self.crawler else None,
            "total_sources_processed": self.total_sources_processed,
            "total_pages_acquired": self.total_pages_acquired,
        }
    
    def summary(self) -> str:
        """Generate a human-readable summary."""
        lines = [
            f"Pipeline completed in {self.duration_seconds:.1f}s",
            f"  Mode: {self.mode}" + (" (dry run)" if self.dry_run else ""),
        ]
        
        if self.monitor:
            lines.extend([
                f"  Monitor: {self.monitor.sources_checked} checked",
                f"    - Initial needed: {len(self.monitor.initial_needed)}",
                f"    - Updates needed: {len(self.monitor.updates_needed)}",
                f"    - Unchanged: {len(self.monitor.unchanged)}",
                f"    - Errors: {len(self.monitor.errors)}",
            ])
        
        if self.crawler:
            lines.extend([
                f"  Crawler: {self.crawler.sources_processed} processed",
                f"    - Successful: {len(self.crawler.successful)}",
                f"    - Failed: {len(self.crawler.failed)}",
                f"    - Pages acquired: {self.crawler.pages_total}",
            ])
        
        return "\n".join(lines)


def run_pipeline(
    config: PipelineConfig | None = None,
) -> PipelineResult:
    """Run the unified content pipeline.
    
    This is the main entry point for programmatic pipeline execution.
    It runs the monitor and/or crawler phases based on the configured mode.
    
    Args:
        config: Pipeline configuration. Uses defaults if None.
        
    Returns:
        PipelineResult with outcomes from all phases.
    """
    if config is None:
        config = PipelineConfig()
    
    result = PipelineResult(mode=config.mode, dry_run=config.dry_run)
    
    logger.info(
        "Starting content pipeline (mode=%s, dry_run=%s)",
        config.mode,
        config.dry_run,
    )
    
    # Initialize registry
    kb_root = config.kb_root or paths.get_knowledge_graph_root()
    registry = SourceRegistry(
        root=kb_root,
        github_client=config.github_client,
    )
    
    # Initialize scheduler
    scheduler = DomainScheduler(politeness=config.politeness)
    
    # Track sources needing acquisition
    sources_to_acquire: list[tuple] = []
    
    # Phase 1: Monitor (if mode is "full" or "check")
    if config.mode in ("full", "check"):
        logger.info("Running monitor phase...")
        result.monitor = run_monitor(
            registry=registry,
            scheduler=scheduler,
            dry_run=config.dry_run,
            force_fresh=config.force_fresh,
        )
        
        # Collect sources needing acquisition
        for source in result.monitor.initial_needed:
            sources_to_acquire.append((source, None))
        
        for source, check_result in result.monitor.updates_needed:
            sources_to_acquire.append((source, check_result))
        
        logger.info(
            "Monitor phase complete: %d sources need acquisition",
            len(sources_to_acquire),
        )
    
    # Phase 2: Crawler (if mode is "full" or "acquire")
    if config.mode in ("full", "acquire"):
        # If acquire-only mode, find sources that need acquisition
        if config.mode == "acquire" and not sources_to_acquire:
            if config.force_fresh:
                logger.info("Acquire mode with force fresh: acquiring all active sources...")
                all_sources = list(registry.list_sources(status="active"))
                for source in all_sources[:config.politeness.max_sources_per_run]:
                    sources_to_acquire.append((source, None))
            else:
                logger.info("Acquire mode: looking for pending sources...")
                from .monitor import get_sources_pending_initial
                
                pending = get_sources_pending_initial(registry)
                for source in pending[:config.politeness.max_sources_per_run]:
                    sources_to_acquire.append((source, None))
        
        if sources_to_acquire:
            logger.info("Running crawler phase for %d sources...", len(sources_to_acquire))
            
            # Re-initialize scheduler for crawler phase
            crawler_scheduler = DomainScheduler(politeness=config.politeness)
            
            result.crawler = run_crawler(
                sources=sources_to_acquire,
                config=config,
                registry=registry,
                scheduler=crawler_scheduler,
            )
        else:
            logger.info("No sources need acquisition, skipping crawler phase")
            result.crawler = CrawlerResult()
    
    result.completed_at = datetime.now(timezone.utc)
    
    logger.info("Pipeline complete:\n%s", result.summary())
    
    return result


def run_check_only(
    config: PipelineConfig | None = None,
) -> PipelineResult:
    """Run only the monitor phase (detection without acquisition).
    
    Convenience wrapper that sets mode="check".
    
    Args:
        config: Pipeline configuration. Uses defaults if None.
        
    Returns:
        PipelineResult with monitor outcomes only.
    """
    if config is None:
        config = PipelineConfig(mode="check")
    else:
        config.mode = "check"
    
    return run_pipeline(config)


def run_acquire_only(
    config: PipelineConfig | None = None,
) -> PipelineResult:
    """Run only the crawler phase (acquire pending sources).
    
    Convenience wrapper that sets mode="acquire".
    
    Args:
        config: Pipeline configuration. Uses defaults if None.
        
    Returns:
        PipelineResult with crawler outcomes only.
    """
    if config is None:
        config = PipelineConfig(mode="acquire")
    else:
        config.mode = "acquire"
    
    return run_pipeline(config)
