---
name: Source Proposal
about: Propose a new authoritative source for the research registry
title: "Source Proposal: [Source Name]"
labels: ["source-proposal"]
assignees: ''
---

## Proposed Source

**URL:**
<!-- Provide the full URL to the proposed source -->


**Name:**
<!-- A human-readable name for this source -->


## Source Type

<!-- Select one: -->
- [ ] Derived - Discovered within an existing source document
- [ ] Reference - External authoritative reference

## Justification

<!-- Explain why this source should be added to the registry -->
<!-- Include information about the source's authority, relevance, and credibility -->


## Discovery Context (if derived)

**Discovered in document:**
<!-- If this source was found in an existing parsed document, provide the checksum -->


**Parent source URL:**
<!-- If this source was referenced by another source, provide that URL -->


## Additional Context

<!-- Any additional information about the source -->


---
**Instructions:**
This issue will automatically trigger the Content: AI Assessment workflow.
The Source Curator Agent will:
1. Verify the URL is accessible
2. Calculate a credibility score based on domain characteristics
3. Post an assessment as a comment

**To approve**: Reply with `/approve-source`
**To reject**: Reply with `/reject-source [reason]`
