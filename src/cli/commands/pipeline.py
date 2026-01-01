"""CLI commands for the unified content pipeline.

The pipeline consolidates monitor and crawler operations into a single,
LLM-free workflow with politeness-aware scheduling.

Commands:
- pipeline run: Full pipeline (detect changes + acquire content)
- pipeline check: Detection only (no acquisition)
- pipeline acquire: Acquisition only (for pending sources)
- pipeline status: Show source status and next scheduled checks
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add pipeline subcommands to the main CLI parser."""

    # pipeline command group
    pipeline_parser = subparsers.add_parser(
        "pipeline",
        description="Unified content pipeline for source monitoring and acquisition.",
        help="Run the content pipeline (replaces monitor/crawler agents).",
    )
    pipeline_subparsers = pipeline_parser.add_subparsers(
        dest="pipeline_command",
        metavar="SUBCOMMAND",
    )
    pipeline_subparsers.required = True

    # Shared arguments for all pipeline subcommands
    def add_common_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be done without making changes.",
        )
        parser.add_argument(
            "--kb-root",
            type=Path,
            help="Root directory for the knowledge graph.",
        )
        parser.add_argument(
            "--evidence-root",
            type=Path,
            help="Root directory for acquired evidence.",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="output_json",
            help="Output results in JSON format.",
        )

    # pipeline run
    run_parser = pipeline_subparsers.add_parser(
        "run",
        description="Run the full content pipeline (detect + acquire).",
        help="Detect changes and acquire updated content.",
    )
    add_common_args(run_parser)
    run_parser.add_argument(
        "--max-sources",
        type=int,
        default=20,
        help="Maximum sources to process per run (default: 20).",
    )
    run_parser.add_argument(
        "--max-per-domain",
        type=int,
        default=3,
        help="Maximum sources per domain per run (default: 3).",
    )
    run_parser.add_argument(
        "--min-interval",
        type=float,
        default=5.0,
        help="Minimum seconds between requests to same domain (default: 5).",
    )
    run_parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="Force fresh acquisition, ignoring existing content.",
    )
    run_parser.add_argument(
        "--no-crawl",
        action="store_true",
        help="Disable crawling, only acquire single pages.",
    )
    run_parser.add_argument(
        "--max-pages-per-crawl",
        type=int,
        default=100,
        help="Maximum pages to crawl per source (default: 100).",
    )
    run_parser.set_defaults(func=pipeline_run_cli, pipeline_command="run")

    # pipeline check
    check_parser = pipeline_subparsers.add_parser(
        "check",
        description="Check sources for changes without acquiring content.",
        help="Detection only - identify sources needing updates.",
    )
    add_common_args(check_parser)
    check_parser.add_argument(
        "--max-sources",
        type=int,
        default=50,
        help="Maximum sources to check (default: 50).",
    )
    check_parser.add_argument(
        "--max-per-domain",
        type=int,
        default=5,
        help="Maximum sources per domain (default: 5).",
    )
    check_parser.set_defaults(func=pipeline_check_cli, pipeline_command="check")

    # pipeline acquire
    acquire_parser = pipeline_subparsers.add_parser(
        "acquire",
        description="Acquire content for sources marked as needing updates.",
        help="Acquisition only - fetch content for pending sources.",
    )
    add_common_args(acquire_parser)
    acquire_parser.add_argument(
        "--max-sources",
        type=int,
        default=10,
        help="Maximum sources to acquire (default: 10).",
    )
    acquire_parser.add_argument(
        "--source-url",
        type=str,
        help="Acquire a specific source by URL.",
    )
    acquire_parser.add_argument(
        "--force-fresh",
        action="store_true",
        help="Force fresh acquisition, ignoring existing content.",
    )
    acquire_parser.add_argument(
        "--no-crawl",
        action="store_true",
        help="Disable crawling, only acquire single pages.",
    )
    acquire_parser.add_argument(
        "--max-pages-per-crawl",
        type=int,
        default=100,
        help="Maximum pages to crawl per source (default: 100).",
    )
    acquire_parser.set_defaults(func=pipeline_acquire_cli, pipeline_command="acquire")

    # pipeline status
    status_parser = pipeline_subparsers.add_parser(
        "status",
        description="Show pipeline status and source schedules.",
        help="Display source status and upcoming checks.",
    )
    status_parser.add_argument(
        "--kb-root",
        type=Path,
        help="Root directory for the knowledge graph.",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        help="Output in JSON format.",
    )
    status_parser.add_argument(
        "--due-only",
        action="store_true",
        help="Show only sources that are due for checking.",
    )
    status_parser.add_argument(
        "--pending-only",
        action="store_true",
        help="Show only sources pending initial acquisition.",
    )
    status_parser.set_defaults(func=pipeline_status_cli, pipeline_command="status")


def pipeline_run_cli(args: argparse.Namespace) -> int:
    """Execute the full content pipeline.
    
    Combines detection and acquisition phases:
    1. Identify sources needing initial acquisition
    2. Check due sources for content changes
    3. Acquire content for sources with detected changes
    """
    from datetime import timedelta

    from src import paths
    from src.knowledge.pipeline import (
        PipelineConfig,
        PipelinePoliteness,
        run_pipeline,
    )

    # Build configuration
    politeness = PipelinePoliteness(
        min_domain_interval=timedelta(seconds=args.min_interval),
        max_sources_per_run=args.max_sources,
        max_domain_requests_per_run=args.max_per_domain,
    )

    config = PipelineConfig(
        mode="full",
        dry_run=args.dry_run,
        force_fresh=args.force_fresh,
        politeness=politeness,
        kb_root=args.kb_root or paths.get_knowledge_graph_root(),
        evidence_root=args.evidence_root or paths.get_evidence_root(),
        enable_crawling=not args.no_crawl,
        max_pages_per_crawl=args.max_pages_per_crawl,
    )

    if not args.output_json:
        print("Starting content pipeline (mode=full)...")
        if args.dry_run:
            print("  [DRY RUN - no changes will be made]")
        if args.force_fresh:
            print("  [FORCE FRESH - ignoring existing content]")
        print(f"  Max sources: {args.max_sources}")
        print(f"  Max per domain: {args.max_per_domain}")
        print(f"  Min interval: {args.min_interval}s")
        print()

    # Run the pipeline
    result = run_pipeline(config)

    # Output results
    if args.output_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(result.summary())
        print()
        
        if result.monitor and result.monitor.errors:
            print("Errors encountered:")
            for source, error in result.monitor.errors:
                print(f"  - {source.name}: {error}")

    return 0 if not (result.monitor and result.monitor.errors) else 1


def pipeline_check_cli(args: argparse.Namespace) -> int:
    """Execute detection-only phase.
    
    Identifies sources that need acquisition without actually fetching content.
    Useful for dry-run verification before acquisition.
    """
    from datetime import timedelta

    from src import paths
    from src.knowledge.pipeline import (
        PipelineConfig,
        PipelinePoliteness,
        run_pipeline,
    )

    politeness = PipelinePoliteness(
        max_sources_per_run=args.max_sources,
        max_domain_requests_per_run=args.max_per_domain,
    )

    config = PipelineConfig(
        mode="check",
        dry_run=args.dry_run,
        politeness=politeness,
        kb_root=args.kb_root or paths.get_knowledge_graph_root(),
        evidence_root=args.evidence_root or paths.get_evidence_root(),
    )

    if not args.output_json:
        print("Starting content pipeline (mode=check)...")
        if args.dry_run:
            print("  [DRY RUN - no changes will be made]")
        print()

    result = run_pipeline(config)

    if args.output_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(result.summary())
        
        if result.monitor:
            if result.monitor.initial_needed:
                print("\nSources needing initial acquisition:")
                for source in result.monitor.initial_needed[:10]:
                    print(f"  - {source.name}: {source.url}")
                if len(result.monitor.initial_needed) > 10:
                    print(f"  ... and {len(result.monitor.initial_needed) - 10} more")
            
            if result.monitor.updates_needed:
                print("\nSources with detected changes:")
                for source, check_result in result.monitor.updates_needed[:10]:
                    print(f"  - {source.name} ({check_result.detection_method})")
                if len(result.monitor.updates_needed) > 10:
                    print(f"  ... and {len(result.monitor.updates_needed) - 10} more")

    return 0


def pipeline_acquire_cli(args: argparse.Namespace) -> int:
    """Execute acquisition-only phase.
    
    Acquires content for sources that have been marked as needing updates.
    Can also acquire a specific source by URL.
    """
    from src import paths
    from src.knowledge.pipeline import (
        PipelineConfig,
        PipelinePoliteness,
        run_pipeline,
    )

    politeness = PipelinePoliteness(
        max_sources_per_run=args.max_sources,
    )

    config = PipelineConfig(
        mode="acquire",
        dry_run=args.dry_run,
        force_fresh=args.force_fresh,
        politeness=politeness,
        kb_root=args.kb_root or paths.get_knowledge_graph_root(),
        evidence_root=args.evidence_root or paths.get_evidence_root(),
        enable_crawling=not args.no_crawl,
        max_pages_per_crawl=args.max_pages_per_crawl,
    )

    if not args.output_json:
        print("Starting content pipeline (mode=acquire)...")
        if args.dry_run:
            print("  [DRY RUN - no changes will be made]")
        if args.force_fresh:
            print("  [FORCE FRESH - ignoring existing content]")
        if args.source_url:
            print(f"  Acquiring specific source: {args.source_url}")
        print()

    result = run_pipeline(config)

    if args.output_json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        print(result.summary())
        
        if result.crawler:
            if result.crawler.successful:
                print("\nSuccessfully acquired:")
                for acq in result.crawler.successful[:10]:
                    print(f"  ✓ {acq.source_url} ({acq.pages_acquired} pages)")
                if len(result.crawler.successful) > 10:
                    print(f"  ... and {len(result.crawler.successful) - 10} more")
            
            if result.crawler.failed:
                print("\nFailed acquisitions:")
                for acq in result.crawler.failed:
                    print(f"  ✗ {acq.source_url}: {acq.error}")

    return 0 if not (result.crawler and result.crawler.failed) else 1


def pipeline_status_cli(args: argparse.Namespace) -> int:
    """Display pipeline status and source schedules.
    
    Shows:
    - Sources pending initial acquisition
    - Sources due for checking
    - Upcoming scheduled checks
    """
    from src import paths
    from src.knowledge.storage import SourceRegistry
    from src.knowledge.pipeline.monitor import (
        get_sources_pending_initial,
        get_sources_due_for_check,
    )

    kb_root = args.kb_root or paths.get_knowledge_graph_root()
    registry = SourceRegistry(root=kb_root)

    # Gather status information
    all_sources = registry.list_sources(status="active")
    pending_initial = get_sources_pending_initial(registry)
    due_for_check = get_sources_due_for_check(registry)

    now = datetime.now(timezone.utc)

    status = {
        "timestamp": now.isoformat(),
        "total_active_sources": len(all_sources),
        "pending_initial": len(pending_initial),
        "due_for_check": len(due_for_check),
        "sources": [],
    }

    # Build source details
    for source in all_sources:
        source_info = {
            "name": source.name,
            "url": source.url,
            "status": "pending_initial" if source.last_content_hash is None else "acquired",
            "last_checked": source.last_checked.isoformat() if source.last_checked else None,
            "next_check_after": source.next_check_after.isoformat() if source.next_check_after else None,
            "failed_checks": source.failed_checks,
        }
        
        # Calculate if due
        if source.last_content_hash is None:
            source_info["is_due"] = True
            source_info["due_reason"] = "never acquired"
        elif source.next_check_after is None:
            source_info["is_due"] = True
            source_info["due_reason"] = "no schedule"
        elif source.next_check_after <= now:
            source_info["is_due"] = True
            source_info["due_reason"] = "past due"
        else:
            source_info["is_due"] = False
            delta = source.next_check_after - now
            source_info["next_check_in"] = str(delta)

        status["sources"].append(source_info)

    # Apply filters
    if args.due_only:
        status["sources"] = [s for s in status["sources"] if s.get("is_due")]
    elif args.pending_only:
        status["sources"] = [s for s in status["sources"] if s["status"] == "pending_initial"]

    # Output
    if args.output_json:
        print(json.dumps(status, indent=2, default=str))
    else:
        print(f"Pipeline Status as of {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("=" * 60)
        print(f"Total active sources: {status['total_active_sources']}")
        print(f"Pending initial acquisition: {status['pending_initial']}")
        print(f"Due for checking: {status['due_for_check']}")
        print()

        if args.pending_only:
            print("Sources pending initial acquisition:")
            print("-" * 40)
        elif args.due_only:
            print("Sources due for action:")
            print("-" * 40)
        else:
            print("Source details:")
            print("-" * 40)

        for source in status["sources"][:20]:
            status_icon = "⏳" if source["status"] == "pending_initial" else "✓"
            due_icon = "🔴" if source.get("is_due") else "🟢"
            
            print(f"{status_icon} {source['name']}")
            print(f"   URL: {source['url']}")
            
            if source["status"] == "pending_initial":
                print("   Status: Never acquired")
            else:
                if source.get("is_due"):
                    print(f"   Status: {due_icon} Due ({source.get('due_reason', 'unknown')})")
                else:
                    print(f"   Status: {due_icon} Next check in {source.get('next_check_in', 'unknown')}")
            
            if source["failed_checks"] > 0:
                print(f"   ⚠️  Failed checks: {source['failed_checks']}")
            print()

        if len(status["sources"]) > 20:
            print(f"... and {len(status['sources']) - 20} more sources")
            print("Use --json for complete listing")

    return 0
