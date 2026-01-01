---
name: Parse and Extract Document
about: Parse a document and extract person names from it
title: "Parse and Extract: [Document Name/URL]"
labels: ["parse-and-extract"]
assignees: ''
---

## Document to Process

**Source (file path or URL):**
<!-- Provide the full file path or URL to the document to be parsed -->


## Expected Results

<!-- Optional: Describe what you expect to find in the document -->


## Additional Context

<!-- Any additional information about the document or extraction requirements -->


---
**Instructions:**
This issue will automatically trigger the Content: Parse & Extract Entities workflow which will assign it to GitHub Copilot.
Copilot will:
1. Parse the document from the provided source using `python -m main parse`
2. Extract person names using `python -m main extract`
3. Save the extracted information to the knowledge graph
4. Create a branch with all changes and open a pull request
