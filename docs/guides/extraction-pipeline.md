# Extraction Pipeline Guide

This guide explains the automated extraction pipeline that processes parsed documents and extracts knowledge entities (people, organizations, concepts, and associations).

## Overview

The extraction pipeline is a **Copilot-orchestrated queue system** that:

1. **Automatically creates GitHub Issues** for newly parsed documents
2. **Filters documents** using AI to skip non-substantive content
3. **Extracts entities** in a structured order (people → organizations → concepts → associations)
4. **Saves results** to the knowledge graph
5. **Provides an audit trail** via Issue comments and labels

**Important:** All documents must enter through the **Source Approval Process**. Manual document submission is not permitted to ensure source authenticity and reliability.

## Architecture

```
Source Proposal → AI Assessment → Human Approval → Source Registry
                                                           ↓
                                        Content Pipeline monitors source
                                                           ↓
                           Content Pipeline → PR merged → Queue Workflow → Issues created
                                                                                 ↓
                                                     Copilot picks up Issues as available
                                                                                 ↓
                                                     Filter → Extract → Commit → Close Issue
```

### Source Approval Requirement

**All sources must be approved before documents can be acquired and extracted.** This ensures:

- **Authenticity**: Sources are verified for legitimacy
- **Credibility**: AI assessment evaluates source authority
- **Provenance**: Full tracking of where knowledge originated
- **Quality**: Prevents spam, misinformation, or unreliable sources

To add a new source:
1. Create a [Source Proposal](../../.github/ISSUE_TEMPLATE/source-proposal.md) issue
2. AI curator assesses credibility and relevance
3. Human reviewer approves with `/approve-source` command
4. Source is registered in the source registry
5. Content Pipeline begins monitoring

### Workflow Chain

1. **Content Pipeline** (`content-monitor-acquire.yml`)
   - Monitors sources for changes
   - Acquires and parses content
   - Creates PR with new documents in `evidence/parsed/`

2. **Queue Creation** (`extraction-queue.yml`)
   - Triggered when PR merges to main
   - Scans manifest for new documents
   - Creates GitHub Issue for each document needing extraction
   - Labels Issues with `extraction-queue` and `copilot-queue`

3. **Extraction Processing** (`extraction-process.yml`)
   - Triggered when Issue labeled with `extraction-queue`
   - Posts detailed extraction instructions as a comment
   - Assigns the issue to GitHub Copilot

4. **Copilot Execution** (GitHub Copilot, automated)
   - Picks up assigned issues automatically
   - Reads document content
   - Filters: Assesses if content is substantive
   - If skip: Comments reason, labels `extraction-skipped`, closes
   - If extract: Runs all entity extractors in order
   - Commits changes to knowledge graph
   - Comments summary, labels `extraction-complete`, closes

## Labels

| Label | Purpose |
|-------|---------|
| `extraction-queue` | Document queued for extraction (triggers workflow) |
| `copilot-queue` | Ready for Copilot pickup |
| `extraction-complete` | Successfully extracted and saved |
| `extraction-skipped` | Filtered out (non-substantive content) |
| `extraction-error` | Failed extraction, needs investigation |

## CLI Commands

### Queue Documents

Create Issues for documents needing extraction:

```bash
python main.py extraction queue
```

Options:
- `--repository owner/repo` - GitHub repository (defaults to current repo)
- `--token TOKEN` - GitHub token (defaults to `GH_TOKEN` env var)
- `--evidence-root PATH` - Evidence directory (defaults to `evidence/`)
- `--force` - Re-queue documents even if they have existing Issues
- `--checksum CHECKSUM` - Only queue a specific document

### View Queue Status

See extraction queue health:

```bash
python main.py extraction status
```

Output example:
```
Extraction Queue Status
========================================
Total documents in manifest: 47
Documents with Issues: 32
  - Open: 4
  - Closed: 28
Documents needing Issues: 15
```

### List Pending Documents

Show documents that need extraction Issues:

```bash
python main.py extraction pending
```

Output example:
```
Pending Documents (3):
========================================
  1327a866... - The Prince (Machiavelli)
    Path: evidence/parsed/2025/prince01mach-1-pdf-1327a866df4a/index.md
  8f4d2a1c... - On War (Clausewitz)
    Path: evidence/parsed/2025/onwar00clau-pdf-8f4d2a1c/index.md
```

## Filtering Logic

Copilot uses AI-based filtering to skip non-substantive documents. Documents are skipped if they are:

- **Navigation pages** - Tables of contents, index pages, site maps
- **Error pages** - 404 errors, access denied, server errors
- **Boilerplate** - Copyright notices, disclaimers, standard footers
- **Duplicates** - Content already processed from another source
- **Low-value content** - Minimal text, mostly images/formatting

### Why AI Filtering?

- **Better judgment**: Heuristics produce false positives; AI understands context
- **Audit trail**: Skip decisions are explained in Issue comments
- **Low volume**: Small number of documents makes LLM cost acceptable
- **Adaptable**: Filtering criteria can evolve without code changes

## Entity Extraction Order

Entities are extracted in a specific order to maximize context:

1. **People** - Individual names
2. **Organizations** - Using people as hints for affiliations
3. **Concepts** - Key themes and ideas
4. **Associations** - Relationships between all entity types

Each step informs the next, improving extraction quality.

## Issue Structure

Extraction queue Issues are created automatically with:

```markdown
## Document to Extract

**Checksum:** `abc123...`
**Source:** Example Document
**Artifact Path:** `evidence/parsed/2025/example-abc123/index.md`
**Parsed At:** 2026-01-02 15:30:00 UTC
**Page Count:** 42

<!-- checksum:abc123... -->

## Extraction Instructions

@copilot Please process this document:

1. **Assess** - Determine if substantive content
   - Skip if: navigation, error, boilerplate, duplicate
   - If skipping: Comment reason, label "extraction-skipped"

2. **Extract** (if substantive):
   ```bash
   python main.py extract --checksum abc123...
   python main.py extract --checksum abc123... --orgs
   python main.py extract --checksum abc123... --concepts
   python main.py extract --checksum abc123... --associations
   ```

3. **Commit** - Save to knowledge-graph/

4. **Report** - Comment summary
```

## Checksum Tracking

Documents are tracked by **SHA-256 checksum** to ensure:
- No duplicate processing
- Stable references across renames
- Version tracking (same URL, different content)

The checksum is embedded in:
- Issue body: `<!-- checksum:abc123... -->`
- Manifest: `evidence/parsed/manifest.json`
- Knowledge graph files: `knowledge-graph/people/abc123.json`

## Manual Operations

### Force Re-Queue a Document

Re-create an Issue for a specific document:

```bash
python main.py extraction queue --checksum abc123... --force
```

### Manually Trigger Queue Creation

Trigger the queue workflow without waiting for a PR merge:

1. Go to Actions → "Extraction: Queue Documents 📋"
2. Click "Run workflow"
3. Optional: Check "Create Issues for all documents" to force re-queue

### Manually Process a Document

If Copilot isn't available, run extraction locally:

```bash
# Filter assessment (manual judgment)
cat evidence/parsed/2025/doc-abc123/index.md

# If substantive, extract entities
python main.py extract --checksum abc123...
python main.py extract --checksum abc123... --orgs
python main.py extract --checksum abc123... --concepts
python main.py extract --checksum abc123... --associations

# Commit changes
git add knowledge-graph/
git commit -m "Extract entities from doc abc123"
git push

# Close the Issue manually
```

## Troubleshooting

### No Issues Created

**Problem**: Queue workflow runs but doesn't create Issues.

**Solutions**:
- Check manifest: `cat evidence/parsed/manifest.json`
- Verify documents have `status: "completed"`
- Check existing Issues: Documents may already have Issues
- Run with force: `python main.py extraction queue --force`

### Issue Created But Not Processed

**Problem**: Issue labeled `extraction-queue` but Copilot doesn't pick it up.

**Root Cause**: GitHub's `labeled` webhook event only fires when a label is **added** to an existing issue, not when an issue is created with labels already applied.

**Solution**: The queue CLI now creates issues in two steps:
1. Create issue with `copilot-queue` label
2. Add `extraction-queue` label in separate API call (triggers workflow)

**If still not working**:
- Check workflow logs: Actions → "Extraction: Assign to Copilot 🧠"
- Verify `GH_TOKEN` secret is set in repository settings
- Verify Copilot is enabled for the repository
- Check that issue was assigned to `copilot` (visible in assignees)
- Manually add the `extraction-queue` label again to re-trigger

### Extraction Failed

**Problem**: Issue has `extraction-error` label.

**Solutions**:
- Read Copilot's error comment for details
- Check document format: `cat evidence/parsed/.../index.md`
- Run extraction locally to debug:
  ```bash
  python main.py extract --checksum abc123... --dry-run
  ```
- If bug: Fix and re-queue with `--force`

### Document Incorrectly Skipped

**Problem**: Copilot skipped a substantive document.

**Solutions**:
- Review Copilot's skip reason in Issue comment
- If incorrect: Remove `extraction-skipped` label, add `extraction-queue` label
- Workflow will re-trigger
- If pattern: Adjust mission in `config/missions/extract_document.yaml`

## Configuration

### Mission Definition

Extraction behavior is configured in `config/missions/extract_document.yaml`:

```yaml
id: extract_document
version: 1
goal: |
  Extract knowledge entities with intelligent filtering.
  
  1. Read document
  2. Filter: Assess substantive value
  3. Extract: People → Orgs → Concepts → Associations
  4. Save to knowledge-graph/
  5. Commit and report

constraints:
  - Always assess document value BEFORE extracting
  - Skip navigation, error, boilerplate content
  - Extract in order: people → orgs → concepts → associations
  - Commit all changes before closing Issue
  - Explain skip decisions clearly

max_steps: 25
```

### Workflow Triggers

**Queue Creation** (`.github/workflows/extraction-queue.yml`):
```yaml
on:
  push:
    branches: [main]
    paths:
      - 'evidence/parsed/**'
  workflow_dispatch:
```

**Extraction Processing** (`.github/workflows/extraction-process.yml`):
```yaml
on:
  issues:
    types: [labeled]
jobs:
  process-extraction:
    if: github.event.label.name == 'extraction-queue'
```

## Integration with Other Agents

### Upstream Dependencies

- **Content Pipeline**: Provides parsed documents in `evidence/parsed/`
- **Parse Storage**: Maintains manifest with document metadata

### Downstream Consumers

- **Synthesis Agent**: Aggregates entities into unified profiles
- **Conflict Detection**: Identifies inconsistencies in entity data
- **Report Generation**: Uses knowledge graph for analytical reports
- **Discussion Sync**: Publishes entity profiles to GitHub Discussions

## Best Practices

1. **Monitor queue health**: Run `python main.py extraction status` regularly
2. **Review skip decisions**: Check Issues labeled `extraction-skipped` to ensure quality
3. **Batch processing**: Content Pipeline merges trigger automatic queue creation
4. **Local testing**: Use `--dry-run` to test extraction before committing
5. **Audit trail**: All decisions are documented in Issue comments

## Related Documentation

- [Content Pipeline](content-pipeline.md) - Document acquisition and parsing
- [Entity Extraction](entity-extraction.md) - Extractor implementation details
- [Agent Operations](agent-operations.md) - Copilot orchestration patterns
