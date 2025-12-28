# Acquisition Guide

This guide covers content acquisition - the process of fetching, parsing, and storing source content.

## Overview

The acquisition workflow is a two-stage process:

```
┌─────────────────┐     Creates Issue      ┌─────────────────┐
│  Monitor Agent  │ ───────────────────▶  │   GitHub Issue   │
│                 │    with source URL     │                  │
└─────────────────┘                        └────────┬─────────┘
                                                    │
                                                    │ Triggers
                                                    ▼
                                           ┌─────────────────┐
                                           │ Acquisition     │
                                           │ (Copilot/Agent) │
                                           └────────┬────────┘
                                                    │
                                      ┌─────────────┼─────────────┐
                                      │             │             │
                                      ▼             ▼             ▼
                              ┌───────────┐ ┌───────────┐ ┌───────────┐
                              │ evidence/ │ │ manifest  │ │  source   │
                              │  parsed/  │ │   .json   │ │ registry  │
                              └───────────┘ └───────────┘ └───────────┘
```

## Issue Types

### Initial Acquisition (`initial-acquisition` label)

Created when a source has never been acquired (no `last_content_hash`):
- First-time fetch of approved source
- Full content scope
- Sets baseline for future change detection

### Content Update (`content-update` label)

Created when Monitor Agent detects content changes:
- Source was previously acquired
- Change detected via ETag, Last-Modified, or content hash comparison
- Incremental update to existing content

## Existing Infrastructure

**DO NOT recreate these modules** - they already exist and should be used as-is.

| Module | Purpose | Key Functions |
|--------|---------|---------------|
| `src/parsing/web.py` | Web content parser | `WebParser.extract()` |
| `src/parsing/pdf.py` | PDF document parser | `PdfParser.extract()` |
| `src/parsing/docx.py` | Word document parser | `DocxParser.extract()` |
| `src/parsing/runner.py` | Orchestrator | `parse_single_target()` |
| `src/parsing/storage.py` | Evidence storage | `ParseStorage` |
| `src/knowledge/storage.py` | Source metadata | `SourceRegistry` |

## Execution Pattern

### Step 1: Parse Content

```python
from src.parsing.runner import parse_single_target
from src.parsing.storage import ParseStorage
from src import paths

# Initialize storage
storage = ParseStorage(root=paths.get_evidence_root() / "parsed")

# Parse the URL (web content)
result = parse_single_target(
    "https://example.com/page",
    storage=storage,
    is_remote=True,
    force=False  # Set True for updates to skip dedup
)

# Check result
if result.succeeded:
    print(f"Stored at: {result.artifact_path}")
    print(f"Checksum: {result.checksum}")
else:
    print(f"Failed: {result.error}")
```

### Step 2: Update Source Registry

```python
from src.knowledge.storage import SourceRegistry
from src import paths
from datetime import datetime, timezone

# Load registry
registry = SourceRegistry(root=paths.get_knowledge_graph_root())

# Get and update source
source = registry.get_source("https://example.com/page")
if source:
    # Create updated entry
    updated = SourceEntry(
        url=source.url,
        name=source.name,
        # ... other fields ...
        last_content_hash=result.checksum,
        last_checked=datetime.now(timezone.utc),
    )
    registry.save_source(updated)
```

### Step 3: Persist in GitHub Actions

When running in GitHub Actions, use `GitHubStorageClient` for persistence:

```python
from src.integrations.github.storage import get_github_storage_client

# Get client (returns None if not in Actions)
github_client = get_github_storage_client()

if github_client:
    # Use client for storage operations
    storage = ParseStorage(
        root=paths.get_evidence_root() / "parsed",
        github_client=github_client
    )
    registry = SourceRegistry(
        root=paths.get_knowledge_graph_root(),
        github_client=github_client
    )
```

## Storage Structure

Parsed content is stored in `evidence/parsed/` with this structure:

```
evidence/parsed/
└── 2025/
    └── example.com-a1b2c3d4/
        ├── content.md          # Extracted text content
        └── metadata.json       # Provenance information
```

The manifest at `evidence/parsed/manifest.json` tracks all entries:

```json
{
  "version": 1,
  "entries": [
    {
      "source": "https://example.com/page",
      "checksum": "a1b2c3d4e5f6...",
      "parser": "web",
      "artifact_path": "2025/example.com-a1b2c3d4",
      "processed_at": "2025-12-28T10:00:00+00:00",
      "status": "completed"
    }
  ]
}
```

## Network Requirements

Content acquisition requires external network access to fetch from source URLs.

| Environment | Network Status | Behavior |
|-------------|---------------|----------|
| GitHub Actions | ✅ Available | Normal operation |
| Local development | ✅ Available | Normal operation |
| Sandboxed/Firewalled | ❌ Blocked | Fails at fetch |

### Handling Network Blocks

If acquisition fails due to network restrictions:

1. Add the `blocked-network` label to the Issue
2. Close the Issue with a comment explaining the limitation
3. The Issue can be retried when network access is available

## Troubleshooting

### "Parser not found" Error

The parser registry auto-detects content type. If detection fails:
- Check the URL is accessible
- Verify Content-Type header
- Try specifying `media_type` parameter

### "Already processed" Message

Content is deduplicated by checksum. To force reprocessing:
- Set `force=True` in `parse_single_target()`
- This creates a new entry even if content hash matches

### Registry Update Fails

Ensure you're using `GitHubStorageClient` in Actions:
- Local filesystem writes are discarded when workflow ends
- All persistence must go through GitHub API

## Related Documentation

- [Monitor Agent Guide](monitor-agent.md) - Change detection and Issue creation
- [Entity Extraction Guide](entity-extraction.md) - Post-acquisition knowledge extraction
- [Agent Operations Guide](agent-operations.md) - General agent patterns
