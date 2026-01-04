---
name: Extraction Queue (Auto-Generated)
about: Document queued for knowledge extraction (created automatically)
title: "Extract: [Document Title]"
labels: ["extraction-queue"]
assignees: ''
---

## Document to Extract

**Checksum:** `{{ checksum }}`
**Source:** {{ source_name }}
**Artifact Path:** `{{ artifact_path }}`
**Parsed At:** {{ parsed_at }}
**Page Count:** {{ page_count }}

<!-- checksum:{{ checksum }} -->

## Automatic Processing

This issue will be processed automatically by the extraction workflow.

The workflow will:
1. ✅ Assess if content is substantive (using LLM)
2. ✅ Extract entities if substantive (people, orgs, concepts, associations)
3. ✅ Create a PR with changes
4. ✅ Close this issue with results

**If rate limited:** Issue will be labeled `extraction-rate-limited` and retried in 30 minutes.

**If extraction fails:** Issue will be labeled `extraction-error` for manual review.

No manual intervention needed - just wait for the workflow to complete.

---
<!-- copilot:extraction-queue -->
