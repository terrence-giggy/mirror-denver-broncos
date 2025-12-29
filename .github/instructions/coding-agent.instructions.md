---
applyTo: "**"
excludeAgent: "code-review"
---

# Coding Agent Instructions

These instructions apply when the Copilot coding agent works on Issues/PRs on GitHub.com.

## Environment Context

You are running in an **ephemeral GitHub Actions runner**. Local filesystem writes are discarded when the workflow ends.

### Persistence Rules

- **Reads**: Use local filesystem (files from `actions/checkout`)
- **Writes**: Use GitHub Contents API via `GitHubStorageClient` or `commit_file()`
- **Never** use git CLI commands (`git add`, `git commit`, `git push`)

```python
from src.integrations.github.storage import get_github_storage_client

github_client = get_github_storage_client()  # Returns None if not in Actions
registry = SourceRegistry(github_client=github_client)
```

## Firewall Sandbox

External network requests are **blocked** unless the domain is on the firewall allowlist (Repository Settings → Copilot → Coding Agent → Firewall).

### If Blocked by Firewall

1. Add comment: "Blocked by firewall - domain `<domain>` not on allowlist"
2. Add label `blocked-by-firewall`
3. Close the issue (a human must add the domain)

## Content Acquisition

For fetching web content (when domain is allowlisted), use `WebParser` with JavaScript rendering support:

```python
from src.parsing.web import WebParser
from src.parsing.base import ParseTarget

parser = WebParser(enable_rendering=True)
target = ParseTarget(source="https://example.com/page", is_remote=True)
document = parser.extract(target)
markdown = parser.to_markdown(document)
```

## Key Directories

| Directory | Purpose |
|-----------|---------|
| `src/orchestration/toolkit/` | Agent tools (monitor, setup, knowledge) |
| `src/integrations/github/` | GitHub API utilities |
| `src/knowledge/` | Entity extraction, storage |
| `src/parsing/` | Document parsing (PDF, DOCX, web) |
| `evidence/` | Acquired source content (clone-specific) |
| `knowledge-graph/` | Entities and sources (clone-specific) |

## CLI Entry Point

All commands go through `main.py`:
```bash
python main.py <command> [options]
```

## Workflow Rules

1. Do NOT create summary documents or explanation files
2. Prefer specific tools over general-purpose CLIs
3. Validate changes with `pytest tests/` before completing
