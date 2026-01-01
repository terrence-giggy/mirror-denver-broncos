"""Crawler logic for content acquisition.

This module provides LLM-free content acquisition for sources that need
initial acquisition or have detected changes. It handles both:

1. Single-page sources: Fetch and store one page
2. Multi-page sources: Crawl within scope boundary

The crawler respects politeness constraints and maintains resumable state.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Sequence
from urllib.parse import urlparse

if TYPE_CHECKING:
    from src.knowledge.storage import SourceEntry, SourceRegistry
    from src.knowledge.monitoring import CheckResult

from src.knowledge.crawl_state import CrawlState, CrawlStateStorage
from src.parsing.base import ParseTarget, ParserError
from src.parsing.link_extractor import extract_links
from src.parsing.robots import RobotsChecker
from src.parsing.storage import ParseStorage
from src.parsing.url_scope import filter_urls_by_scope, normalize_url
from src.parsing.web import WebParser

from .config import PipelineConfig
from .scheduler import DomainScheduler

logger = logging.getLogger(__name__)


@dataclass
class AcquisitionResult:
    """Result of acquiring content from a source.
    
    Attributes:
        source_url: The source URL that was acquired.
        success: Whether acquisition succeeded.
        content_hash: SHA-256 hash of acquired content.
        content_path: Path where content was stored.
        pages_acquired: Number of pages acquired (1 for single-page).
        error: Error message if acquisition failed.
    """
    
    source_url: str
    success: bool
    content_hash: str | None = None
    content_path: str | None = None
    pages_acquired: int = 0
    error: str | None = None


@dataclass
class CrawlerResult:
    """Result of running the crawler phase.
    
    Attributes:
        sources_processed: Number of sources processed.
        successful: Sources that were successfully acquired.
        failed: Sources that failed with error messages.
        pages_total: Total pages acquired across all sources.
    """
    
    sources_processed: int = 0
    successful: list[AcquisitionResult] = field(default_factory=list)
    failed: list[AcquisitionResult] = field(default_factory=list)
    pages_total: int = 0
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for logging/reporting."""
        return {
            "sources_processed": self.sources_processed,
            "successful": len(self.successful),
            "failed": len(self.failed),
            "pages_total": self.pages_total,
        }


def _content_hash(content: str) -> str:
    """Generate SHA-256 hash of content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_domain(url: str) -> str:
    """Extract domain from URL."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def acquire_single_page(
    source: "SourceEntry",
    storage: ParseStorage,
    delay_seconds: float = 1.0,
) -> AcquisitionResult:
    """Acquire content from a single-page source.
    
    Args:
        source: The source to acquire.
        storage: Storage for parsed content.
        delay_seconds: Delay before fetching (politeness).
        
    Returns:
        AcquisitionResult with content hash and path.
    """
    logger.info("Acquiring single page: %s", source.url)
    
    # Apply politeness delay
    if delay_seconds > 0:
        time.sleep(delay_seconds)
    
    try:
        parser = WebParser()
        target = ParseTarget(source=source.url, is_remote=True)
        
        document = parser.extract(target)
        markdown = parser.to_markdown(document)
        
        # Add source metadata to document
        document.metadata.update({
            "source_name": source.name,
            "source_type": source.source_type,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        })
        
        # Store content using persist_document
        entry = storage.persist_document(document)
        
        content_hash = _content_hash(markdown)
        
        logger.info(
            "Acquired %s: %d chars, hash=%s",
            source.name,
            len(markdown),
            content_hash[:16],
        )
        
        return AcquisitionResult(
            source_url=source.url,
            success=True,
            content_hash=content_hash,
            content_path=entry.artifact_path,
            pages_acquired=1,
        )
        
    except ParserError as e:
        logger.error("Parser error acquiring %s: %s", source.url, e)
        return AcquisitionResult(
            source_url=source.url,
            success=False,
            error=str(e),
        )
    except Exception as e:
        logger.error("Error acquiring %s: %s", source.url, e)
        return AcquisitionResult(
            source_url=source.url,
            success=False,
            error=str(e),
        )


def acquire_crawl(
    source: "SourceEntry",
    storage: ParseStorage,
    crawl_storage: CrawlStateStorage,
    max_pages: int = 100,
    delay_seconds: float = 1.0,
    force_restart: bool = False,
) -> AcquisitionResult:
    """Acquire content from a multi-page source via crawling.
    
    Args:
        source: The source to crawl.
        storage: Storage for parsed content.
        crawl_storage: Storage for crawl state.
        max_pages: Maximum pages to acquire this run.
        delay_seconds: Delay between page fetches.
        force_restart: If True, restart crawl from scratch.
        
    Returns:
        AcquisitionResult with aggregate statistics.
    """
    logger.info(
        "Starting crawl: %s (scope=%s, max=%d)",
        source.url,
        source.crawl_scope,
        max_pages,
    )
    
    # Load or create crawl state
    state = None if force_restart else crawl_storage.load_state(source.url)
    
    if state is None:
        state = CrawlState.create_new(
            source_url=source.url,
            scope=source.crawl_scope,
            max_pages=source.crawl_max_pages,
            max_depth=source.crawl_max_depth,
        )
        logger.info("Created new crawl state for %s", source.url)
    else:
        logger.info(
            "Resuming crawl for %s: %d visited, %d in frontier",
            source.url,
            state.visited_count,
            len(state.frontier),
        )
    
    state.mark_started()
    
    # Load robots.txt
    robots = RobotsChecker(source.url)
    
    # Initialize parser
    parser = WebParser()
    pages_this_run = 0
    content_hashes: list[str] = []
    
    while state.frontier and pages_this_run < max_pages:
        url = state.pop_frontier()
        if url is None:
            break
        
        # Skip if already visited
        if state.is_url_visited(url):
            continue
        
        # Normalize URL
        url = normalize_url(url)
        
        # Check robots.txt
        if not robots.is_allowed(url):
            state.skipped_count += 1
            state.mark_url_visited(url)
            logger.debug("Skipped (robots.txt): %s", url)
            continue
        
        # Apply politeness delay
        if delay_seconds > 0:
            time.sleep(delay_seconds)
        
        # Fetch page
        try:
            target = ParseTarget(source=url, is_remote=True)
            document = parser.extract(target)
            markdown = parser.to_markdown(document)
            
            # Store content
            document.metadata.update({
                "crawl_source": source.url,
                "acquired_at": datetime.now(timezone.utc).isoformat(),
            })
            storage.persist_document(document)
            
            page_hash = _content_hash(markdown)
            content_hashes.append(page_hash)
            
            # Extract links
            if document.raw_content:
                links = extract_links(document.raw_content, url)
                in_scope = filter_urls_by_scope(
                    [link.url for link in links],
                    source.url,
                    source.crawl_scope,
                )
                
                # Add to frontier
                for link_url in in_scope:
                    if state.add_to_frontier(link_url):
                        state.in_scope_count += 1
                
                state.discovered_count += len(links)
                state.out_of_scope_count += len(links) - len(in_scope)
            
            state.mark_url_visited(url)
            pages_this_run += 1
            
            logger.debug(
                "Crawled [%d/%d]: %s",
                pages_this_run,
                max_pages,
                url[:80],
            )
            
        except Exception as e:
            state.failed_count += 1
            state.mark_url_visited(url)
            logger.warning("Failed to crawl %s: %s", url, e)
        
        # Periodic state save
        if pages_this_run % 10 == 0:
            crawl_storage.save_state(state)
    
    # Final state update
    if not state.frontier:
        state.mark_completed()
    else:
        state.mark_paused()
    
    crawl_storage.save_state(state)
    
    # Compute aggregate content hash
    aggregate_hash = None
    if content_hashes:
        combined = "".join(sorted(content_hashes))
        aggregate_hash = _content_hash(combined)
    
    logger.info(
        "Crawl complete for %s: %d pages this run, %d total visited, %d failed",
        source.url,
        pages_this_run,
        state.visited_count,
        state.failed_count,
    )
    
    # Consider crawl successful only if we got at least one page this run
    # (previous visits don't count for this acquisition attempt)
    return AcquisitionResult(
        source_url=source.url,
        success=pages_this_run > 0,
        content_hash=aggregate_hash,
        pages_acquired=pages_this_run,
    )


def run_crawler(
    sources: Sequence[tuple["SourceEntry", "CheckResult | None"]],
    config: PipelineConfig,
    registry: "SourceRegistry",
    scheduler: DomainScheduler,
) -> CrawlerResult:
    """Run the crawler phase to acquire content from sources.
    
    Args:
        sources: List of (source, check_result) tuples to acquire.
            check_result is None for initial acquisitions.
        config: Pipeline configuration.
        registry: Source registry for metadata updates.
        scheduler: Domain scheduler for politeness.
        
    Returns:
        CrawlerResult with acquisition outcomes.
    """
    from src import paths
    from src.integrations.github.storage import get_github_storage_client
    
    result = CrawlerResult()
    
    # Initialize storage
    evidence_root = config.evidence_root or paths.get_evidence_root()
    kb_root = config.kb_root or paths.get_knowledge_graph_root()
    
    # Get GitHub client for PR-based persistence in Actions
    github_client = config.github_client or get_github_storage_client()
    
    parse_storage = ParseStorage(
        root=evidence_root / "parsed",
        github_client=github_client,
    )
    crawl_storage = CrawlStateStorage(
        root=kb_root,
        github_client=github_client,
    )
    
    delay = config.politeness.crawler_delay_seconds
    
    for source, check_result in sources:
        result.sources_processed += 1
        
        # Wait for domain cooldown
        domain = _get_domain(source.url)
        cooldown = scheduler.get_domain_cooldown(domain)
        if cooldown > 0:
            logger.debug("Waiting %.1fs for domain %s", cooldown, domain)
            time.sleep(cooldown)
        
        if config.dry_run:
            logger.info("[DRY RUN] Would acquire: %s", source.url)
            result.successful.append(AcquisitionResult(
                source_url=source.url,
                success=True,
                pages_acquired=0,
            ))
            continue
        
        # Decide: single page or crawl
        if config.enable_crawling and source.is_crawlable:
            max_pages = min(
                config.max_pages_per_crawl,
                config.politeness.max_domain_requests_per_run,
            )
            acq_result = acquire_crawl(
                source=source,
                storage=parse_storage,
                crawl_storage=crawl_storage,
                max_pages=max_pages,
                delay_seconds=delay,
                force_restart=config.force_fresh,
            )
        else:
            acq_result = acquire_single_page(
                source=source,
                storage=parse_storage,
                delay_seconds=delay,
            )
        
        scheduler.record_request(domain)
        
        if acq_result.success:
            result.successful.append(acq_result)
            result.pages_total += acq_result.pages_acquired
            
            # Update source metadata
            if acq_result.content_hash:
                source.last_content_hash = acq_result.content_hash
            source.last_checked = datetime.now(timezone.utc)
            source.check_failures = 0
            
            if source.is_crawlable:
                source.total_pages_acquired = (
                    source.total_pages_acquired + acq_result.pages_acquired
                )
                source.last_crawl_completed = datetime.now(timezone.utc)
            
            registry.save_source(source)
        else:
            result.failed.append(acq_result)
            
            # Update failure count
            source.check_failures += 1
            source.last_checked = datetime.now(timezone.utc)
            registry.save_source(source)
    
    logger.info(
        "Crawler complete: %d processed, %d successful, %d failed, %d pages",
        result.sources_processed,
        len(result.successful),
        len(result.failed),
        result.pages_total,
    )
    
    # Create PR for acquired content if using GitHub client
    if github_client and github_client._pr_branch and len(result.successful) > 0:
        try:
            pr_number, pr_url = github_client.create_content_pr(
                title=f"Content Acquisition - {len(result.successful)} sources acquired",
                body=(
                    f"Acquired content from {len(result.successful)} source(s):\n\n"
                    + "\n".join(f"- {acq.source_url}" for acq in result.successful[:10])
                    + ("\n- ..." if len(result.successful) > 10 else "")
                    + f"\n\nTotal pages acquired: {result.pages_total}"
                ),
            )
            logger.info("Created PR #%d for content acquisition: %s", pr_number, pr_url)
        except Exception as e:
            logger.error("Failed to create PR for content acquisition: %s", e)
    
    return result
