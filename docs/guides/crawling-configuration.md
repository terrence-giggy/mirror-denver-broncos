# Crawling Configuration Guide

This guide explains how to configure sources for multi-page crawling in the content pipeline.

## Overview

The content pipeline supports two acquisition modes:

1. **Single-page**: Acquires one URL (default for most sources)
2. **Multi-page crawl**: Follows links and acquires multiple pages within scope

## Enabling Crawling for a Source

To enable crawling for a source, set `is_crawlable: true` in the source's JSON file:

```json
{
  "url": "https://example.com/research",
  "name": "Example Research Portal",
  "is_crawlable": true,
  "crawl_scope": "path",
  "crawl_max_pages": 500,
  "crawl_max_depth": 5,
  ...
}
```

### Crawl Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `is_crawlable` | boolean | `false` | Enable multi-page crawling |
| `crawl_scope` | string | `"path"` | Scope boundary: `"path"`, `"host"`, or `"domain"` |
| `crawl_max_pages` | integer | `10000` | Maximum pages to acquire total |
| `crawl_max_depth` | integer | `10` | Maximum link depth from source URL |

### Scope Options

- **`path`**: Only URLs under the same path prefix
  - Source: `https://example.com/docs/`
  - In scope: `https://example.com/docs/guide.html`
  - Out of scope: `https://example.com/blog/`

- **`host`**: Only URLs on the same hostname
  - Source: `https://www.example.com/`
  - In scope: `https://www.example.com/about`
  - Out of scope: `https://api.example.com/`

- **`domain`**: URLs on the same domain (includes subdomains)
  - Source: `https://www.example.com/`
  - In scope: `https://api.example.com/`, `https://docs.example.com/`
  - Out of scope: `https://other-site.com/`

## Workflow Configuration

### GitHub Actions Workflow Inputs

The workflow exposes crawl-related inputs:

```yaml
crawl_enabled: true          # Enable/disable crawling globally
max_pages_per_crawl: 100     # Pages per source per run
```

### CLI Command Options

```bash
# Enable crawling (default)
python main.py pipeline run

# Disable crawling (single-page only)
python main.py pipeline run --no-crawl

# Custom page limit per crawl
python main.py pipeline run --max-pages-per-crawl 50

# Force fresh crawl (restart from beginning)
python main.py pipeline run --force-fresh
```

## Crawl State Management

Crawls are resumable across workflow runs:

- **State files**: Stored in `knowledge-graph/crawls/`
- **Content**: Stored in `evidence/parsed/`
- **Frontier**: Queued URLs to visit next
- **Visited tracking**: Prevents duplicate fetching

### Crawl Lifecycle

1. **Started**: Frontier initialized with source URL
2. **In Progress**: Fetching pages, discovering links
3. **Paused**: Run limit reached, can resume later
4. **Completed**: No more URLs in frontier

## Politeness and Rate Limiting

The crawler respects these constraints:

- Minimum delay between requests to same domain (default: 5s)
- Maximum pages per domain per run (default: 3)
- robots.txt compliance
- Crawl-delay directive from robots.txt

## Testing Crawling

### 1. Create a test source

```json
{
  "url": "https://example.com/docs/",
  "name": "Example Docs",
  "source_type": "reference",
  "status": "active",
  "is_crawlable": true,
  "crawl_scope": "path",
  "crawl_max_pages": 50,
  "crawl_max_depth": 3
}
```

### 2. Run acquisition locally

```bash
python main.py pipeline run \
  --max-sources 1 \
  --max-pages-per-crawl 10 \
  --dry-run
```

### 3. Verify results

Check for:
- Multiple pages in `evidence/parsed/`
- Crawl state in `knowledge-graph/crawls/`
- Updated `total_pages_acquired` in source JSON
- Links followed within scope boundaries

## Troubleshooting

### Crawl not following links

- Verify `is_crawlable: true` is set
- Check `crawl_scope` isn't too restrictive
- Ensure links are in HTML (not JavaScript-rendered)

### Pages not being acquired

- Check robots.txt isn't blocking
- Verify scope filter isn't excluding URLs
- Ensure max_depth/max_pages limits aren't too low

### Crawl keeps restarting

- Don't use `--force-fresh` unless intentional
- Check crawl state files are being committed
- Verify GitHub storage client is working in Actions
