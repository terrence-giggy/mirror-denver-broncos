---
name: Extraction Queue (Auto-Generated)
about: Document queued for knowledge extraction (created automatically)
title: "Extract: [Document Title]"
labels: ["extraction-queue", "copilot-queue"]
assignees: ''
---

## Document to Extract

**Checksum:** `{{ checksum }}`
**Source:** {{ source_name }}
**Artifact Path:** `{{ artifact_path }}`
**Parsed At:** {{ parsed_at }}
**Page Count:** {{ page_count }}

<!-- checksum:{{ checksum }} -->

## Extraction Instructions

@copilot Please process this document:

1. **Assess** - Read the document and determine if it contains substantive content
   - Skip if: navigation page, error page, boilerplate, or duplicate content
   - If skipping: Comment with reason and close with "extraction-skipped" label

2. **Extract** (if substantive) - Run extractions in order:
   ```bash
   python main.py extract --checksum {{ checksum }}
   python main.py extract --checksum {{ checksum }} --orgs
   python main.py extract --checksum {{ checksum }} --concepts
   python main.py extract --checksum {{ checksum }} --associations
   ```

3. **Commit** - Save changes to knowledge-graph/

4. **Report** - Comment with summary of extracted entities

---
<!-- copilot:extraction-queue -->
