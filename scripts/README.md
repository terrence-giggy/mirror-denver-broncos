# Scripts Directory

This directory contains utility scripts for repository operations.

## Available Scripts

### `acquire_denver_broncos.py`

Acquires and parses content from the Denver Broncos official website.

**Purpose:**
- Fetches HTML content from https://www.denverbroncos.com
- Parses content to markdown using the web parser
- Stores parsed content in `evidence/parsed/`
- Updates source registry with content hash

**Requirements:**
- Network access to the target URL
- Python dependencies from `requirements.txt`
- GitHub token (optional, for Actions persistence)

**Usage:**

```bash
# Local execution (saves to filesystem)
python scripts/acquire_denver_broncos.py

# In GitHub Actions (commits via API)
# GITHUB_TOKEN and GITHUB_REPOSITORY are auto-detected
python scripts/acquire_denver_broncos.py
```

**Network Limitation:**

This script requires external network access and will fail in sandboxed environments without internet connectivity. If you encounter DNS resolution errors, the script should be run in an environment with network access (e.g., standard GitHub Actions workflow, local machine with internet).

## Creating Additional Acquisition Scripts

To create scripts for acquiring other sources:

1. Copy `acquire_denver_broncos.py` as a template
2. Update the `source_url` variable
3. Ensure the source is registered in the source registry
4. Run the script in a network-enabled environment

## GitHub Actions Integration

These scripts are designed to work seamlessly in GitHub Actions:

- Automatically detect GitHub Actions environment
- Use GitHub API for persistence when available
- Fall back to local filesystem when running locally

No code changes needed between local and Actions execution.
