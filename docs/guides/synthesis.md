# Synthesis Agent Guide

The **Synthesis Agent** consolidates extracted entities from multiple source documents into canonical (deduplicated) entities with corroboration scores and provenance tracking.

## Overview

After the Extraction Agent processes documents, you'll have many per-source entity files:

```
knowledge-graph/
├── people/
│   ├── abc123.json     # ["Sean Payton", "Courtland Sutton"]
│   ├── def456.json     # ["Sean Payton", "Head Coach Sean Payton"]
│   └── ...
├── organizations/
│   ├── abc123.json     # ["Denver Broncos", "Broncos"]
│   └── ...
```

The **Synthesis Agent** resolves these into canonical entities:

```
knowledge-graph/canonical/
├── people/
│   ├── sean-payton.json     # Canonical record with all aliases
│   └── courtland-sutton.json
├── organizations/
│   └── denver-broncos.json
├── alias-map.json           # Fast lookup: "broncos" → "denver-broncos"
```

## How It Works

### 1. Sequential Issue Creation (Prevents Merge Conflicts)

The synthesis queue workflow ensures **only ONE batch Issue is active at a time**:
- **Before creating** a new Issue, checks for open `synthesis-batch` Issues
- **Skips creation** if any open synthesis Issues exist
- **Waits** for the current batch to complete before queueing the next

This prevents multiple Copilot instances from updating the same files (`alias-map.json`, canonical entities) simultaneously, which would cause merge conflicts.

The workflow runs:
- **After extraction completes** (triggered by extraction workflow)
- **Daily at 7 AM UTC** (scheduled)
- **Manually** via workflow dispatch (with optional `force` flag to override)

### 2. Copilot Resolution

When an Issue is labeled `synthesis-batch`, Copilot is automatically assigned and:

1. Reads the alias map and existing canonical entities
2. For each entity in the Issue:
   - Checks if it matches an existing canonical entity (abbreviation, variant, etc.)
   - **If match:** Adds as alias to existing entity
   - **If new:** Creates new canonical entity file
   - **If ambiguous:** Flags for human review
3. Updates the alias map
4. Creates a PR with all changes
5. Comments on the Issue with summary
6. Closes the Issue

### 3. Auto-Approval for Knowledge Graph PRs

PRs that **only** modify `knowledge-graph/` or `evidence/parsed/` files are automatically:
- **Approved** by the bot (if no code/workflow changes)
- **Auto-merged** when checks pass

This prevents bottlenecks from synthesis batches waiting for human review. Human review is still required for:
- Code changes (`src/`, `tests/`, `requirements.txt`)
- Workflow changes (`.github/workflows/`)
- Any files outside knowledge graph/evidence

### 4. Human Review & Objections

All synthesis changes come via PRs for human review. If you spot an error:

1. **Create a Discussion** in the "Objection" category
2. Include `<!-- objection:synthesis -->` marker in the body
3. Specify the canonical entity ID and proposed correction
4. A workflow creates an Issue for Copilot to review
5. Copilot evaluates the objection and updates entities if valid

## Usage

### Check Pending Entities

```bash
# Check all entity types
python main.py synthesis pending

# Check specific type
python main.py synthesis pending --entity-type Person
```

### Manual Issue Creation

```bash
# Create Issues for unresolved entities
python main.py synthesis create-issue --repository owner/repo

# Specific entity type
python main.py synthesis create-issue --entity-type Organization

# Custom batch size
python main.py synthesis create-issue --batch-size 100

# Full rebuild (reprocess all entities)
python main.py synthesis create-issue --full
```

### Trigger Workflow

Via GitHub Actions web interface:

1. Go to **Actions** → **Synthesis: Create Batch Issue**
2. Click **Run workflow**
3. Select entity type, batch size, and full rebuild option

## Canonical Entity Structure

Each canonical entity file (`knowledge-graph/canonical/{type}/{id}.json`) contains:

```json
{
  "canonical_id": "denver-broncos",
  "canonical_name": "Denver Broncos",
  "entity_type": "Organization",
  "aliases": ["Denver Broncos", "Broncos", "The Broncos"],
  "source_checksums": ["abc123...", "def456...", "..."],
  "corroboration_score": 15,
  "first_seen": "2026-01-08T04:31:00+00:00",
  "last_updated": "2026-01-08T05:15:00+00:00",
  "resolution_history": [
    {
      "action": "created",
      "timestamp": "2026-01-08T04:31:00+00:00",
      "by": "copilot",
      "issue_number": 42,
      "reasoning": "First occurrence from source abc123"
    },
    {
      "action": "alias_added",
      "alias": "Broncos",
      "timestamp": "2026-01-08T05:00:00+00:00",
      "by": "copilot",
      "issue_number": 45,
      "reasoning": "Short name variant for Denver Broncos NFL team"
    }
  ],
  "attributes": {},
  "associations": [
    {
      "target_id": "sean-payton",
      "target_type": "Person",
      "relationships": [
        {"type": "employs", "count": 2}
      ],
      "source_checksums": ["abc123", "def456"]
    }
  ],
  "metadata": {
    "needs_review": false,
    "confidence": 0.95
  }
}
```

## Alias Map

The alias map (`knowledge-graph/canonical/alias-map.json`) enables fast lookups:

```json
{
  "version": 1,
  "last_updated": "2026-01-08T05:15:00+00:00",
  "by_type": {
    "Person": {
      "sean payton": "sean-payton",
      "head coach sean payton": "sean-payton"
    },
    "Organization": {
      "denver broncos": "denver-broncos",
      "broncos": "denver-broncos",
      "the broncos": "denver-broncos"
    },
    "Concept": {
      "home-field advantage": "home-field-advantage"
    }
  }
}
```

**Keys:** Normalized names (lowercase, collapsed spaces)
**Values:** Canonical entity IDs

## Objection Workflow

If you find an incorrect entity resolution:

1. **Create a Discussion** with category "Objection"
2. **Title:** "Objection: [Brief description]"
3. **Body:**

```markdown
## Entity Objection

**Canonical Entity:** `denver-broncos`
**Issue:** The alias "Denver" should NOT be included - it's ambiguous (city vs team)

## Proposed Resolution

Remove "Denver" from aliases for `denver-broncos`.

## Evidence

- Source document abc123 refers to "Denver" as the city, not the team
- See line 45: "The meeting was held in Denver..."

<!-- objection:synthesis -->
```

4. The workflow creates an Issue for Copilot
5. Copilot reviews and updates entities if valid
6. Copilot comments on your Discussion with the resolution

## Rate Limiting

If Copilot hits rate limits mid-Issue:

- Issue remains open with partial progress in comments
- When limit clears, re-assign Copilot to continue
- Or: Close Issue and create new one for remaining entities

## Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `synthesis-queue.yml` | Post-extraction, daily, manual | Create synthesis Issues (one at a time) |
| `synthesis-assign.yml` | Issue labeled | Assign Copilot to synthesis Issues |
| `discussion-dispatcher.yml` | Discussion created (with objection marker) | Create Issue from objection |
| `pr-auto-approve-kb.yml` | PR opened/updated | Auto-approve and merge knowledge-graph-only PRs |

## Best Practices

1. **Review PRs carefully** - All entity resolutions require human approval
2. **Use objections liberally** - Better to flag potential issues than leave errors
3. **Check corroboration scores** - High scores indicate well-attested entities
4. **Monitor `needs_review` flags** - Entities Copilot was uncertain about
5. **Preserve resolution history** - Audit trail for all decisions

## Troubleshooting

### No Issues Created

Check:
- Are there extracted entities in `knowledge-graph/{type}/`?
- Run `python main.py synthesis pending` to see unresolved count
- Check workflow logs for errors

### Copilot Not Assigned

- Manually add `synthesis-batch` label to trigger assignment
- Check that Copilot has access to the repository

### Incorrect Resolutions

- Use the objection workflow to request corrections
- Provide specific evidence in the objection Discussion

### Multiple Synthesis Issues Open

The workflow prevents creating new synthesis Issues when one is already open to avoid merge conflicts.

- **Why:** Parallel PRs modifying `alias-map.json` and canonical entities cause conflicts
- **Behavior:** `synthesis-queue.yml` checks for open `synthesis-batch` Issues and skips creation
- **Override:** Use manual dispatch with `force: true` input if needed (⚠️ may cause conflicts)
- **Wait:** Let current Issue complete and PR merge before next batch runs

## Future Enhancements

Planned features (not yet implemented):

- **Hierarchical entities:** Track parent/child relationships (e.g., "AFC" → "AFC West")
- **Association normalization:** LLM-based relationship type merging
- **Confidence scoring:** ML-based entity matching confidence
- **Batch PR reviews:** Summarize multiple synthesis batches in one PR

---

**Related Guides:**
- [Extraction Pipeline](./extraction-pipeline.md)
- [Entity Extraction](./entity-extraction.md)
- [Knowledge Graph Structure](../README.md#knowledge-graph)
