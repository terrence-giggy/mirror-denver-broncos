# Monitor Agent Guide

> ⚠️ **DEPRECATION NOTICE**: This agent is being replaced by the unified [Content Pipeline](content-pipeline.md). The Content Pipeline provides the same functionality with zero LLM cost and improved politeness controls. See the [migration guide](content-pipeline.md#migration-from-monitorcrawler-agents).

## Overview

The Monitor Agent is a lightweight change detector that monitors registered sources for content changes and queues them for acquisition by creating GitHub Issues. It operates on a scheduled basis (every 6 hours by default) and uses a bandwidth-efficient tiered detection strategy.

## Key Concepts

### Two Modes of Operation

1. **Initial Acquisition Mode**: For newly approved sources that have never been acquired (no content hash). Creates `initial-acquisition` Issues.

2. **Update Monitoring Mode**: For previously acquired sources. Uses tiered detection to efficiently detect content changes without fetching full content when possible.

### Tiered Detection Strategy

The Monitor Agent uses a bandwidth-efficient tiered approach:

| Tier | Method | Request Type | When Used |
|------|--------|--------------|-----------|
| 1 | ETag comparison | HEAD | If source has previous ETag |
| 2 | Last-Modified comparison | HEAD | If source has previous Last-Modified |
| 3 | Content hash comparison | GET | When tiers 1-2 can't determine change |

This approach minimizes bandwidth usage by only performing full content downloads when necessary.

## Architecture

### Components

```
src/knowledge/
├── storage.py         # SourceEntry model with monitoring fields
└── monitoring.py      # SourceMonitor class and detection logic

src/orchestration/toolkit/
└── monitor.py         # Agent tools for monitor mission

config/missions/
└── monitor_sources.yaml   # Mission configuration

.github/workflows/
└── 3-op-monitor-sources.yml   # Scheduled workflow
```

### Data Flow

```
Source Registry (sources/*.yaml)
    ↓
SourceMonitor.get_sources_pending_initial() / get_sources_due_for_check()
    ↓
SourceMonitor.check_source() - tiered detection
    ↓
ChangeDetection created
    ↓
GitHub Issue created (acquisition-candidate)
    ↓
Source metadata updated (last_checked, next_check_after, etc.)
```

## Source Entry Monitoring Fields

Each source in the registry includes these monitoring-specific fields:

| Field | Type | Description |
|-------|------|-------------|
| `last_content_hash` | `str \| None` | SHA-256 hash of last acquired content |
| `last_etag` | `str \| None` | Last ETag header received |
| `last_modified_header` | `str \| None` | Last Last-Modified header received |
| `last_checked` | `datetime \| None` | When the source was last checked |
| `check_failures` | `int` | Consecutive check failure count |
| `next_check_after` | `datetime \| None` | Earliest time to check again |

## Agent Tools

The monitor agent has access to these tools:

### Read-Only Tools (SAFE)

| Tool | Description |
|------|-------------|
| `get_sources_pending_initial` | List sources needing initial acquisition |
| `get_sources_due_for_check` | List sources due for update check |
| `check_source_for_changes` | Perform tiered change detection on a source |

### Write Tools (REVIEW)

| Tool | Description |
|------|-------------|
| `update_source_monitoring_metadata` | Update last_checked, hashes, failure counts |
| `create_initial_acquisition_issue` | Create Issue for first-time acquisition |
| `create_content_update_issue` | Create Issue for detected content changes |
| `report_source_access_problem` | Report access failures (creates Issue) |

## Workflow Configuration

### GitHub Workflow

The monitor agent runs via GitHub Actions on a 6-hour schedule:

```yaml
# .github/workflows/content-monitor-acquire.yml
# Note: This workflow replaces the old 3-op-monitor-sources.yml
on:
  schedule:
    - cron: "0 */6 * * *"  # Every 6 hours
  workflow_dispatch:       # Manual trigger
    inputs:
      mode:
        description: 'initial-only, updates-only, or both'
        default: 'both'
```

### Mission Configuration

The mission YAML defines agent behavior:

```yaml
# config/missions/monitor_sources.yaml
id: monitor_sources
version: 1
goal: |
  Monitor registered sources for content changes...
constraints:
  - Respect rate limiting and politeness
  - Create one Issue per detected change
  - Update source metadata after each check
allowed_tools:
  - get_sources_pending_initial
  - get_sources_due_for_check
  - check_source_for_changes
  # ... more tools
max_steps: 50
```

## Scheduling Logic

### Update Frequency

Sources are scheduled based on their `update_frequency` field:

| Frequency | Check Interval |
|-----------|---------------|
| `frequent` | 6 hours |
| `daily` | 24 hours |
| `weekly` | 7 days |
| `monthly` | 30 days |
| `unknown` | 24 hours |

### Failure Backoff

When checks fail, exponential backoff is applied:

- **1st failure**: 2× base interval
- **2nd failure**: 4× base interval
- **3rd failure**: 8× base interval
- **Maximum backoff**: 7 days

After 5 consecutive failures, the source is considered "degraded" but still checked.

## Issue Templates

### Initial Acquisition Issue

Created when a source has no previous content hash:

```markdown
## Initial Acquisition: {source.name}

**Source URL**: {source.url}
**Approved**: {source.added_at}
...

### Acquisition Scope
This is the **first acquisition** of this source...

<!-- monitor-initial:{url_hash} -->
```

### Content Update Issue

Created when content changes are detected:

```markdown
## Content Update: {source.name}

**Source URL**: {source.url}
**Change Detected**: {detection.detected_at}
**Detection Method**: {detection.detection_method}

### Change Summary
| Metric | Previous | Current |
|--------|----------|---------|
| Content Hash | ... | ... |
...

<!-- monitor-update:{url_hash}:{content_hash} -->
```

## Manual Execution

### Via CLI

```bash
# Run the monitor mission manually
python -m main agent run-mission monitor_sources

# With specific mode
python -m main agent run-mission monitor_sources --context mode=initial-only
```

### Via GitHub Actions

Trigger the workflow manually from the Actions tab, optionally specifying:
- `mode`: `initial-only`, `updates-only`, or `both`

## Testing

### Unit Tests

```bash
# Run all monitor-related tests
pytest tests/knowledge/test_source_monitoring.py \
       tests/orchestration/test_monitor_toolkit.py \
       tests/knowledge/test_source_storage.py -v

# Run integration tests
pytest tests/orchestration/test_monitor_integration.py -v
```

### Test Coverage

- `tests/knowledge/test_source_storage.py` - SourceEntry monitoring field tests
- `tests/knowledge/test_source_monitoring.py` - SourceMonitor and detection logic (35 tests)
- `tests/orchestration/test_monitor_toolkit.py` - Agent tools (27 tests)
- `tests/orchestration/test_monitor_integration.py` - End-to-end workflows (19 tests)

## Troubleshooting

### Common Issues

**"Token not provided" error**
- Ensure `GH_TOKEN` or `GITHUB_TOKEN` environment variable is set
- In GitHub Actions, use `${{ secrets.GITHUB_TOKEN }}`

**Sources not being checked**
- Check `next_check_after` field - source may not be due yet
- Verify `status` is "active" (not "deprecated" or "pending")

**High failure counts**
- Review `error_message` in CheckResult
- Check network connectivity and SSL certificates
- Verify source URL is still valid

### Viewing Source State

```bash
# Check a source's monitoring state
cat knowledge-graph/sources/{url_hash}.yaml | grep -A 10 "last_"
```

## Future Enhancements

- **Body search for deduplication**: Enhance issue searcher to check for dedup markers in body
- **Domain-level rate limiting**: Track delays between requests per domain
- **Discussion support**: Report access problems via Discussions instead of Issues
- **Retry-After header handling**: Honor server-specified retry delays
