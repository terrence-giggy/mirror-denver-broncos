# Source Acquisition Guide

This guide explains how the source acquisition system works and how to use it.

## Overview

The source acquisition system is designed to:
1. Fetch content from registered sources (webpages, PDFs, etc.)
2. Parse the content into structured markdown format
3. Store parsed content in the `evidence/` directory with full provenance tracking
4. Update the source registry with content hashes and timestamps for change detection

## Architecture

The acquisition workflow involves several components:

### 1. Monitor Agent (`config/missions/monitor_sources.yaml`)

Periodically checks registered sources and creates acquisition issues when:
- A source needs **initial acquisition** (never acquired before)
- A source has **content updates** detected via change monitoring

### 2. Acquisition Agent (`config/missions/acquire_source.yaml`)

Processes acquisition issues by:
- Fetching content from source URLs
- Parsing content using appropriate parsers (web, PDF, DOCX)
- Storing parsed artifacts in `evidence/parsed/`
- Updating source registry with content hash and timestamps
- Closing the issue with acquisition summary

### 3. Acquisition Toolkit (`src/orchestration/toolkit/acquisition.py`)

Provides the `acquire_source_content` tool that:
- Takes a source URL as input
- Verifies the source exists in the registry
- Calls the appropriate parser based on content type
- Stores results with GitHub API integration for Actions
- Updates source monitoring metadata

## Usage

### Initial Acquisition

When a new source is approved and added to the registry:

1. Monitor Agent detects `last_content_hash = None`
2. Monitor Agent creates an Issue with `initial-acquisition` label
3. Acquisition Agent picks up the Issue
4. Acquisition Agent calls `acquire_source_content(url="https://example.com")`
5. Content is fetched, parsed, and stored
6. Source registry updated with content hash
7. Issue closed with acquisition summary

### Content Updates

When Monitor Agent detects changes to an existing source:

1. Monitor Agent performs tiered change detection (ETag → Last-Modified → Content Hash)
2. If change detected, creates Issue with `content-update` label
3. Acquisition Agent fetches and parses updated content
4. New content hash replaces old one in registry
5. Issue closed with update summary

## Tool Reference

### `acquire_source_content`

Acquires content from a registered source URL.

**Parameters:**
- `url` (required): The source URL to acquire content from
- `force` (optional): Force reprocessing even if content hash is unchanged
- `evidence_root` (optional): Override evidence directory path
- `kb_root` (optional): Override knowledge graph directory path

**Returns:**
- `url`: The source URL
- `source_name`: Human-readable source name
- `status`: Parse status (completed, error, skipped)
- `parser`: Parser used (web, pdf, docx)
- `checksum`: Content hash (SHA-256)
- `artifact_path`: Path to parsed content
- `warnings`: Any parser warnings
- `registry_updated`: Whether source registry was updated

**Example:**

```python
result = acquire_source_content(
    url="https://www.denverbroncos.com",
    force=False
)

if result.success:
    print(f"Acquired: {result.output['artifact_path']}")
    print(f"Checksum: {result.output['checksum']}")
else:
    print(f"Error: {result.error}")
```

## Network Requirements

**Important:** Source acquisition requires internet access to fetch content from external URLs.

- In **local development**: Limited by local network configuration
- In **GitHub Actions**: Has internet access for real acquisitions
- In **sandboxed environments**: May have restricted external access

If acquisition fails due to network restrictions:
- The Issue should be left open for retry
- Consider running in a GitHub Actions workflow
- Check firewall/proxy settings

## Storage Structure

Acquired content is stored in `evidence/parsed/` with this structure:

```
evidence/
└── parsed/
    ├── manifest.json                    # Index of all parsed documents
    └── 2025/
        └── <source-slug>-<checksum>/
            ├── index.md                 # Summary and metadata
            └── segment-001.md           # Parsed content segment
```

Each parsed document includes:
- **Front matter**: Metadata (source, checksum, parser, timestamps)
- **Content**: Extracted text in markdown format
- **Provenance**: Full chain of custody from source to parsed artifact

## Source Registry Updates

After successful acquisition, the source entry is updated with:

- `last_content_hash`: SHA-256 of the acquired content
- `last_checked`: Timestamp of acquisition
- `last_verified`: Timestamp of successful access
- `check_failures`: Reset to 0 on success

This enables future change detection and monitoring.

## Troubleshooting

### Network Errors

If acquisition fails with network errors:
- Verify the source URL is accessible
- Check for network restrictions
- Try running in GitHub Actions instead of local environment
- Review source `requires_auth` flag

### Parser Errors

If parsing fails:
- Check the source content type matches expected parser
- Review parser warnings in acquisition output
- Verify content is parseable (not behind login, not corrupted)
- Consider adding custom parser configuration

### Registry Not Updated

If acquisition succeeds but registry not updated:
- Check GitHub API credentials (in Actions)
- Verify source exists in registry before acquisition
- Review tool output for `registry_updated` flag
- Check file permissions on `knowledge-graph/sources/`

## See Also

- [Monitor Sources Mission](../config/missions/monitor_sources.yaml)
- [Acquire Source Mission](../config/missions/acquire_source.yaml)
- [Parsing Documentation](parsing.md) (if exists)
- [Source Registry](../knowledge-graph/sources/)
