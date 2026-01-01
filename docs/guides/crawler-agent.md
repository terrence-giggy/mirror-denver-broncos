# Crawler Agent Guide

> ⚠️ **DEPRECATION NOTICE**: This agent is being replaced by the unified [Content Pipeline](content-pipeline.md). The Content Pipeline provides the same functionality with zero LLM cost and improved politeness controls. See the [migration guide](content-pipeline.md#migration-from-monitorcrawler-agents).

## Overview

The Crawler Agent is a site-wide content acquisition agent that systematically discovers and downloads pages from registered sources within defined scope boundaries. It respects robots.txt, enforces politeness delays, and maintains persistent state for resumable multi-session crawls.

## Key Concepts

### Scope Types

The crawler operates within three scope boundaries:

| Scope | Description | Example |
|-------|-------------|---------|
| `path` | Most restrictive. Only URLs under the source path | Source `https://example.com/docs/` allows `https://example.com/docs/guide` but not `https://example.com/blog` |
| `host` | Same hostname only | Source `https://docs.example.com/` allows any path on `docs.example.com` but not `api.example.com` |
| `domain` | Least restrictive. All hosts on the same domain | Source `https://example.com/` allows `docs.example.com`, `api.example.com` |

### Frontier Management

The frontier is a queue of URLs waiting to be crawled:

- **In-memory frontier**: Up to 1,000 URLs stored in state
- **Overflow file**: When frontier exceeds 1,000 URLs, excess is written to a JSONL file
- **URL deduplication**: URLs are normalized and hashed to prevent duplicate fetches

### Crawl State

Crawl progress is persisted to enable resumption across sessions:

- **State file**: `crawl/{source_hash}/state.yaml`
- **Visited hashes**: Set of SHA-256 URL hashes for deduplication
- **Statistics**: Counts for visited, discovered, in-scope, out-of-scope, failed, skipped

## Architecture

### Components

```
src/knowledge/
├── crawl_state.py     # CrawlState dataclass and persistence
├── page_registry.py   # PageEntry, PageBatch for page tracking
└── storage.py         # Extended SourceEntry with crawl fields

src/parsing/
├── url_scope.py       # URL normalization and scope validation
├── link_extractor.py  # HTML link extraction
└── robots.py          # robots.txt parsing and checking

src/orchestration/toolkit/
└── crawler.py         # 12 agent tools for crawl operations

config/missions/
└── crawl_source.yaml  # Mission configuration

.github/workflows/
└── 4-op-crawl-source.yml  # Manual dispatch workflow
```

### Data Flow

```
Source URL + Scope
    ↓
load_crawl_state (create or resume)
    ↓
check_robots_txt (fetch and parse)
    ↓
┌─────────────────────────────────┐
│  CRAWL LOOP (per page)          │
│  ├─ get_frontier_urls           │
│  ├─ fetch_page (with delay)     │
│  ├─ store_page_content          │
│  ├─ extract_links               │
│  ├─ filter_urls_by_scope        │
│  ├─ add_to_frontier             │
│  └─ mark_url_visited            │
└─────────────────────────────────┘
    ↓
save_crawl_state
    ↓
(Next workflow run resumes)
```

## Crawl State Fields

| Field | Type | Description |
|-------|------|-------------|
| `source_url` | `str` | The source URL defining the crawl boundary |
| `source_hash` | `str` | SHA-256 hash (16 chars) for storage paths |
| `scope` | `str` | One of: `path`, `host`, `domain` |
| `status` | `str` | `pending`, `crawling`, `paused`, `completed` |
| `frontier` | `list[str]` | URLs waiting to be crawled |
| `frontier_overflow_count` | `int` | Count of URLs in overflow file |
| `visited_hashes` | `set[str]` | SHA-256 hashes of visited URLs |
| `visited_count` | `int` | Total pages successfully visited |
| `discovered_count` | `int` | Total URLs discovered |
| `in_scope_count` | `int` | URLs within scope boundary |
| `out_of_scope_count` | `int` | URLs rejected as out of scope |
| `failed_count` | `int` | Pages that failed to fetch |
| `skipped_count` | `int` | Pages skipped (robots.txt, etc.) |
| `max_pages` | `int` | Maximum pages per crawl (default: 10000) |
| `max_depth` | `int` | Maximum link depth (default: 10) |

## Agent Tools

### State Management Tools (SAFE)

| Tool | Description |
|------|-------------|
| `load_crawl_state` | Load or create crawl state for a source |
| `save_crawl_state` | Persist current crawl state |
| `get_crawl_statistics` | Get crawl progress statistics |

### Frontier Tools (SAFE)

| Tool | Description |
|------|-------------|
| `get_frontier_urls` | Get next URLs to crawl |
| `add_to_frontier` | Add discovered URLs to frontier |
| `filter_urls_by_scope` | Filter URLs by scope boundary |

### Fetch Tools (REVIEW)

| Tool | Description |
|------|-------------|
| `fetch_page` | Fetch a page with politeness delay |
| `check_robots_txt` | Check if URL is allowed by robots.txt |
| `extract_links` | Extract links from HTML content |

### Storage Tools (REVIEW)

| Tool | Description |
|------|-------------|
| `store_page_content` | Store fetched content to disk |
| `update_page_registry` | Record page metadata in registry |
| `mark_url_visited` | Mark URL as visited and update state |

## Workflow Configuration

### GitHub Workflow

The crawler runs via manual dispatch with configurable parameters:

```yaml
# .github/workflows/content-monitor-acquire.yml
# Note: This workflow replaces the old 4-op-crawl-source.yml
on:
  workflow_dispatch:
    inputs:
      source_url:
        description: 'Source URL to crawl'
        required: true
      scope:
        description: 'Crawl scope (path, host, domain)'
        default: 'path'
      max_pages_per_run:
        description: 'Maximum pages per run'
        default: '100'
      force_new:
        description: 'Discard existing state'
        default: 'false'
```

### Mission Configuration

```yaml
# config/missions/crawl_source.yaml
id: crawl_source
version: 1
goal: |
  Crawl a source website within scope boundaries, discovering
  and storing all accessible pages for later parsing.
constraints:
  - Respect robots.txt directives
  - Apply politeness delay between requests
  - Stay within defined scope boundary
  - Stop at max_pages_per_run limit
allowed_tools:
  - load_crawl_state
  - save_crawl_state
  - get_frontier_urls
  - fetch_page
  - extract_links
  - filter_urls_by_scope
  - add_to_frontier
  - store_page_content
  - mark_url_visited
  - check_robots_txt
max_steps: 500
```

## Politeness Rules

### Rate Limiting

- **Default delay**: 1 second between requests to same host
- **Configurable**: Per-source delay can be adjusted
- **Timeout**: 30 seconds per request

### robots.txt Compliance

The crawler respects robots.txt directives:

1. Fetches robots.txt from source domain on crawl start
2. Parses Allow/Disallow rules for user-agent
3. Checks each URL before fetching
4. Skips disallowed URLs (increments `skipped_count`)

### Supported Directives

| Directive | Support |
|-----------|---------|
| `User-agent` | ✅ Matches specific or `*` wildcard |
| `Disallow` | ✅ Path prefix matching |
| `Allow` | ✅ Override Disallow |
| `*` wildcard in path | ✅ Matches any characters |
| `$` end anchor | ✅ Matches end of URL |
| `Crawl-delay` | ⚠️ Parsed but not enforced |

## Storage Structure

### Content Storage

```
evidence/parsed/{domain}/{source_hash}/
├── 0/  # Shards 0-f based on URL hash
├── 1/
├── ...
└── f/
    └── {url_hash}.html
```

### Crawl State Storage

```
knowledge-graph/
└── crawl/
    └── {source_hash}/
        ├── state.yaml           # CrawlState
        ├── frontier_overflow.jsonl  # Overflow URLs
        └── pages/
            └── batch_{n}.yaml   # PageEntry batches
```

## Resumption Logic

### State Loading

1. Check for existing `state.yaml`
2. If exists and `force_new=False`: Load and resume
3. If exists and `force_new=True`: Discard and create new
4. If not exists: Create new state, seed frontier with source URL

### Multi-Run Behavior

Each workflow run:
1. Loads existing state
2. Processes up to `max_pages_per_run` pages
3. Saves state with updated statistics
4. Exits cleanly for next run to continue

## Example Usage

### Crawl a Documentation Site

```bash
# Trigger workflow via GitHub CLI
gh workflow run 4-op-crawl-source.yml \
  -f source_url="https://docs.example.com/guide/" \
  -f scope="path" \
  -f max_pages_per_run="50"
```

### Check Crawl Progress

```python
from src.knowledge.crawl_state import CrawlStateStorage
from src import paths

storage = CrawlStateStorage(root=paths.get_knowledge_graph_root())
state = storage.load_state("https://docs.example.com/guide/")

if state:
    print(f"Status: {state.status}")
    print(f"Visited: {state.visited_count}")
    print(f"Remaining: {len(state.frontier)}")
    print(f"Discovered: {state.discovered_count}")
```

### Resume a Paused Crawl

Simply trigger the workflow again with the same `source_url`. The agent will:
1. Load existing state
2. Continue from where it left off
3. Skip already-visited URLs

## Troubleshooting

### Crawl Not Making Progress

1. Check `skipped_count` - may be blocked by robots.txt
2. Check `out_of_scope_count` - scope may be too restrictive
3. Check `failed_count` - pages may be failing to fetch

### Duplicate Content

URLs are normalized before deduplication:
- Fragments (`#section`) are stripped
- Hostnames are lowercased
- Default ports are removed

### Large Sites

For sites with many pages:
- Use `max_pages` limit to control total crawl size
- Adjust `max_pages_per_run` to fit workflow timeout
- Frontier overflow handles large link discovery

## Related Guides

- [Acquisition Guide](acquisition.md) - Single-page content acquisition
- [Monitor Agent Guide](monitor-agent.md) - Change detection workflow
- [Agent Operations Guide](agent-operations.md) - General agent patterns
