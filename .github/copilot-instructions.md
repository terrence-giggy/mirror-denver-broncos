# Rules for Local Development

These instructions apply when using Copilot in VS Code or other local IDEs.

For coding agent (GitHub.com) instructions, see `.github/instructions/coding-agent.instructions.md`.

## Project Context

This is a **template repository** for research projects. Clones receive code updates via the sync workflow while preserving their research content in `evidence/`, `knowledge-graph/`, `reports/`, and `dev_data/`.

## Core Rules

1. Do NOT create changes summary documents or explanation files
2. Prefer specific tools over general-purpose CLIs
3. Activate the virtual environment: `source .venv/bin/activate`
4. `main.py` is the only CLI entry point

## Development Commands

```bash
# Setup
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Testing
pytest tests/ -v --tb=short

# Run CLI
python main.py <command> [options]
```

## Key Directories

| Directory | Purpose | Synced |
|-----------|---------|--------|
| `src/` | Source code | ✅ |
| `tests/` | pytest coverage | ✅ |
| `config/missions/` | YAML mission definitions | ✅ |
| `docs/guides/` | Documentation | ✅ |
| `evidence/` | Acquired source content | ❌ |
| `knowledge-graph/` | Entities and sources | ❌ |
| `reports/` | Generated reports | ❌ |
| `dev_data/` | Local development data | ❌ |

## Architecture

- `src/orchestration/` - Agent runtime, tools, missions, LLM planner
- `src/integrations/github/` - GitHub API utilities (issues, PRs, sync)
- `src/knowledge/` - Entity extraction, aggregation, storage
- `src/parsing/` - Document parsing (PDF, DOCX, web, markdown)

## Testing Conventions

- Test files mirror `src/` structure in `tests/`
- Use pytest fixtures for setup/teardown
- Mock external services (GitHub API, LLM calls)
