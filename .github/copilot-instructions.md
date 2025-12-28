# Rules for agentic copilot

## Project Context
This is a **template repository** for research projects. Clones receive code updates via the sync workflow while preserving their research content in `evidence/`, `knowledge-graph/`, `reports/`, and `dev_data/`.

**All operations happen on GitHub.com** via Actions, Issues, Discussions, and the GitHub API—no local git commands required.

## Core Rules
1. Do NOT create changes summary documents. And do NOT create explanation documents for work completed.
2. Prefer specific tools over general-purpose CLIs.
3. Call `configure_python_environment` with `.venv` before any Python command.
4. `main.py` is the only CLI entry point.

## GitHub Actions Persistence
Agent missions run in **ephemeral GitHub Actions runners**. File writes to the local filesystem are discarded when the workflow ends.

**Required Pattern:**
- **Reads**: Use local filesystem (files available from `actions/checkout`)
- **Writes**: Use GitHub Contents API via `GitHubStorageClient` or `commit_file()`

**Never** use git CLI commands (`git add`, `git commit`, `git push`) in workflows. All persistence must go through the GitHub API.

Storage classes (`SourceRegistry`, `KnowledgeGraphStorage`) accept an optional `github_client` parameter. When running in Actions, pass a `GitHubStorageClient` instance to persist changes.

```python
from src.integrations.github.storage import get_github_storage_client

# In tool handlers:
github_client = get_github_storage_client()  # Returns None if not in Actions
registry = SourceRegistry(github_client=github_client)
```

## Key Directories
- `src/integrations/github/`: GitHub API utilities including sync, issues, discussions, PRs.
- `src/orchestration/`: Agent runtime, tools, missions, and LLM planner.
- `src/knowledge/`: Entity extraction, aggregation, and storage.
- `src/parsing/`: Document parsing (PDF, DOCX, web, markdown).
- `tests/`: pytest coverage mirroring src structure.
- `config/missions/`: YAML mission definitions for agent workflows.
- `docs/guides/`: Setup and usage documentation.

## Directory Boundaries
**Code directories** (synced from upstream): `src/`, `tests/`, `.github/`, `config/missions/`, `docs/`, `main.py`, `requirements.txt`, `pytest.ini`

**Research directories** (clone-specific, never synced): `evidence/`, `knowledge-graph/`, `reports/`, `dev_data/`, `devops/`