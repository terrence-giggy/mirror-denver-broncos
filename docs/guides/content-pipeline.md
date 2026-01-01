# Content Pipeline Guide

## Overview

The Content Pipeline is a unified, LLM-free system for monitoring source changes and acquiring content. It replaces the separate Monitor Agent and Crawler Agent with a single, programmatic pipeline that emphasizes politeness, efficiency, and deterministic behavior.

## Key Benefits

| Benefit | Description |
|---------|-------------|
| **Zero LLM Cost** | All logic is deterministic Python—no LLM API calls |
| **Unified Workflow** | Single pipeline handles detection and acquisition |
| **Politeness-Aware** | Domain-fair scheduling prevents site overload |
| **Resumable** | State persisted for multi-session execution |
| **Observable** | JSON output, step summaries, structured logging |

## Quick Start

```bash
# Check what sources need updates (detection only)
python main.py pipeline check --dry-run

# Run full pipeline (detect + acquire)
python main.py pipeline run --dry-run

# Actually run (remove --dry-run when ready)
python main.py pipeline run --max-sources 10

# Check pipeline status
python main.py pipeline status
```

## Architecture

### Module Structure

```
src/knowledge/pipeline/
├── __init__.py      # Public API exports
├── config.py        # PipelinePoliteness, PipelineConfig
├── scheduler.py     # DomainScheduler, fair queuing
├── monitor.py       # Change detection (LLM-free)
├── crawler.py       # Content acquisition (LLM-free)
└── runner.py        # run_pipeline() entry point
```

### Data Flow

```
Source Registry (knowledge-graph/sources/*.json)
    ↓
┌─────────────────────────────────────┐
│  MONITOR PHASE                       │
│  ├─ get_sources_pending_initial()    │ → Sources never acquired
│  ├─ get_sources_due_for_check()      │ → Sources past next_check_after
│  └─ DomainScheduler.schedule()       │ → Fair queuing by domain
└─────────────────────────────────────┘
    ↓
┌─────────────────────────────────────┐
│  CRAWLER PHASE                       │
│  ├─ acquire_single_page()            │ → Single-page sources
│  ├─ acquire_crawl()                  │ → Multi-page sources
│  └─ Update source metadata           │ → last_content_hash, next_check
└─────────────────────────────────────┘
    ↓
Updated evidence/ and knowledge-graph/
```

## Politeness Model

The pipeline implements multiple layers of politeness to avoid overloading source websites:

### Per-Domain Rate Limiting

| Setting | Default | Description |
|---------|---------|-------------|
| `min_domain_interval` | 2 seconds | Minimum time between requests to same domain |
| `max_domain_requests_per_run` | 10 | Max pages from one domain per run |

### Per-Run Limits

| Setting | Default | Description |
|---------|---------|-------------|
| `max_sources_per_run` | 20 | Max sources to process per workflow |
| `max_total_requests_per_run` | 100 | Hard limit on total HTTP requests |

### Scheduling Features

| Feature | Description |
|---------|-------------|
| **Domain-Fair Queuing** | Round-robin across domains prevents single-site focus |
| **Jitter** | Random 0-60 minute offset on `next_check_after` |
| **Exponential Backoff** | Failures increase wait time (max 7 days) |
| **robots.txt Respect** | Honors Crawl-delay when present |

## CLI Commands

### `pipeline run`

Full pipeline: detect changes and acquire content.

```bash
python main.py pipeline run [OPTIONS]

Options:
  --dry-run              Show what would be done without changes
  --max-sources N        Maximum sources to process (default: 20)
  --max-per-domain N     Maximum sources per domain (default: 3)
  --min-interval SECS    Minimum seconds between same-domain requests (default: 5)
  --json                 Output results as JSON
  --kb-root PATH         Override knowledge graph root
  --evidence-root PATH   Override evidence root
```

### `pipeline check`

Detection only: identify sources needing updates.

```bash
python main.py pipeline check [OPTIONS]

Options:
  --dry-run              Show candidates without updating metadata
  --max-sources N        Maximum sources to check (default: 50)
  --json                 Output results as JSON
```

### `pipeline acquire`

Acquisition only: fetch content for pending sources.

```bash
python main.py pipeline acquire [OPTIONS]

Options:
  --dry-run              Show what would be acquired
  --max-sources N        Maximum sources to acquire (default: 10)
  --source-url URL       Acquire a specific source by URL
  --json                 Output results as JSON
```

### `pipeline status`

Display source status and schedules.

```bash
python main.py pipeline status [OPTIONS]

Options:
  --due-only         Show only sources due for checking
  --pending-only     Show only sources pending initial acquisition
  --json             Output as JSON for scripting
```

## GitHub Workflow

The pipeline runs via `.github/workflows/content-monitor-acquire.yml`:

### Scheduled Execution

Runs weekly on Sundays at 00:00 UTC.

### Manual Dispatch

Trigger from GitHub Actions with inputs:

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | choice | full | `full`, `check`, or `acquire` |
| `max_sources` | number | 20 | Sources per run |
| `max_per_domain` | number | 3 | Sources per domain |
| `min_interval` | number | 5 | Seconds between requests |
| `dry_run` | boolean | false | Test mode |

### Workflow Output

The workflow generates a step summary with:
- Sources checked
- Initial acquisitions needed
- Updates detected
- Pages acquired
- Duration

## Programmatic Usage

```python
from src.knowledge.pipeline import (
    PipelineConfig,
    PipelinePoliteness,
    run_pipeline,
)
from datetime import timedelta

# Configure politeness
politeness = PipelinePoliteness(
    min_domain_interval=timedelta(seconds=5),
    max_sources_per_run=10,
    max_domain_requests_per_run=3,
)

# Run pipeline
config = PipelineConfig(
    mode="full",
    dry_run=False,
    politeness=politeness,
)

result = run_pipeline(config)

print(result.summary())
print(f"Sources checked: {result.monitor.sources_checked}")
print(f"Pages acquired: {result.total_pages_acquired}")
```

## Migration from Monitor/Crawler Agents

### Comparison

| Aspect | Old Agents | New Pipeline |
|--------|-----------|--------------|
| LLM Calls | ~50-150 per run | 0 |
| Workflows | 2 separate | 1 unified |
| Coordination | Manual dispatch | Automatic |
| Politeness | Basic delays | Domain-fair scheduling |
| State | Fragmented | Unified |

### Migration Steps

1. **Parallel Period**: Run both systems for 2 weeks
2. **Verify Results**: Compare detection and acquisition outcomes
3. **Disable Old**: Comment out schedule triggers in old workflows
4. **Archive**: Move old workflows to `.github/workflows/deprecated/`

## Troubleshooting

### Common Issues

**No sources found for checking**
- Verify sources exist: `python main.py pipeline status`
- Check if sources have `status: active`

**Sources skipped due to backoff**
- Check `failed_checks` count in source metadata
- Use `--force` (if implemented) to override

**Rate limiting errors (429)**
- Increase `--min-interval`
- Reduce `--max-per-domain`
- Check robots.txt for Crawl-delay

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
python main.py pipeline run --dry-run
```

## Related Documentation

- [Monitor Agent Guide](monitor-agent.md) - Legacy agent (being replaced)
- [Crawler Agent Guide](crawler-agent.md) - Legacy agent (being replaced)
- [Acquisition Guide](acquisition.md) - Content acquisition concepts
- [Entity Extraction](entity-extraction.md) - Post-acquisition processing
