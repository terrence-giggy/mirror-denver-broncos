# Upstream Sync Guide

This guide explains how to keep your cloned research repository in sync with the upstream template repository (speculum-principum), including the security model and automated PR approval system.

## Overview

The speculum-principum project is designed as a template that gets cloned for specific research topics. Cloned repositories need to receive code updates (bug fixes, new features) from the base template while preserving their own research content.

### How It Works

1. **Code directories** (`src/`, `tests/`, `.github/`, etc.) are synced from upstream
2. **Research directories** (`evidence/`, `knowledge-graph/`, `reports/`) are preserved locally
3. Sync creates a **Pull Request** for review before merging changes
4. **Automated approval** validates and auto-merges eligible sync PRs

## Trust Architecture

The sync system uses multiple layers of verification to ensure secure, automated updates:

| Layer | Verification | Location |
|-------|-------------|----------|
| **Fork blocking** | `repository.fork == false` | Sync workflow, PR workflow, setup validation |
| **Template origin** | `template_repository` field | `verify_satellite_trust()` |
| **Upstream allowlist** | `vars.UPSTREAM_REPO` matches payload | Sync workflow |
| **Signed dispatch** | HMAC-SHA256 signature validation | Sync workflow |
| **Satellite discovery** | Topic `speculum-downstream` in org | `discover_downstream_repos()` |
| **PR scope** | Files in `CODE_DIRECTORIES`/`CODE_FILES` only | PR validation workflow |
| **PR origin** | Branch `sync/upstream-*` + expected author | PR validation workflow |

### Auto-Approval Matrix

| PR Type | Source | Auto-Approve | Conditions |
|---------|--------|--------------|------------|
| Upstream sync | Sync workflow | ✅ Yes | Valid signature, file scope valid, not fork |
| Knowledge/evidence | Copilot/workflow | ✅ Yes | Files only in `PROTECTED_DIRECTORIES`, author verified |
| Code changes | Copilot | ❌ No | Human review required |
| Manual PR | Human | ❌ No | Human review required |

## Setting Up Upstream Sync

### Manual Setup Steps

After creating your research repo from the template, follow these steps to configure secure syncing:

#### 1. Clone from Template (Not Fork)

**Important:** Use GitHub's "Use this template" feature, not the Fork button. Forked repositories are blocked from auto-sync for security.

1. Go to the template repository
2. Click **"Use this template"** → **"Create a new repository"**
3. Name your repository and set visibility

#### 2. Configure Secrets

Create two secrets in your repository:

**a. GH_TOKEN (Required)**
1. Create a **classic** or **fine-grained** PAT:
   
   **Classic PAT** ([create here](https://github.com/settings/tokens/new)):
   - Select scopes: 
     - `repo` (full control of private repositories)
     - `workflow` (update GitHub Actions workflows)
   
   **Fine-grained PAT** ([create here](https://github.com/settings/tokens?type=beta)):
   - **Repository access**: Select your cloned repo
   - **Permissions**: 
     - Contents: Read and write
     - Pull requests: Read and write
     - Workflows: Read and write
     - Metadata: Read-only (automatic)

2. Go to **Settings → Secrets and variables → Actions → Secrets**
3. Add secret named `GH_TOKEN` with your PAT

**b. SYNC_SIGNATURE_SECRET (Required for dispatch verification)**
1. Generate a random secret (32+ characters):
   ```bash
   openssl rand -hex 32
   ```
2. Add as secret named `SYNC_SIGNATURE_SECRET`
3. **Important:** Share this secret with the upstream template maintainer if you want to receive automatic sync notifications

#### 3. Configure Variables

Set the `UPSTREAM_REPO` variable:

1. Go to **Settings** → **Secrets and variables** → **Actions** → **Variables**
2. Click **New repository variable**
3. Name: `UPSTREAM_REPO`
4. Value: `owner/speculum-principum` (your template repo)

#### 4. Add Repository Topic

Add the `speculum-downstream` topic for automatic discovery:

1. Go to repository main page
2. Click the gear icon ⚙️ next to "About"
3. Add topic: `speculum-downstream`
4. Click "Save changes"

#### 5. Run Setup Workflow

Complete the automated setup:

1. Go to **Actions** → **Template: Initialize Clone**
2. Click **Run workflow**
3. Review the validation results in the created issue
4. Fix any reported issues

### Verification Checklist

After setup, verify your configuration:

- ✅ Repository was created from template (not forked)
- ✅ `GH_TOKEN` secret configured with `repo` + `workflow` scopes (Classic) OR Variables permission (Fine-grained)
- ✅ `SYNC_SIGNATURE_SECRET` secret configured (random 32+ char string)
- ✅ `UPSTREAM_REPO` variable set to template repository
- ✅ `speculum-downstream` topic added to repository
- ✅ Setup workflow completed successfully

### Automated Setup Validation

The setup workflow automatically validates your configuration and posts results as a comment on the setup issue. It checks:

1. **GH_TOKEN** - Secret exists and is accessible
2. **UPSTREAM_REPO** - Variable is set correctly
3. **SYNC_SIGNATURE_SECRET** - Secret exists (verified in workflow)
4. **Fork status** - Repository is not a fork
5. **Topic** - `speculum-downstream` topic is present
6. **Template** - Repository created from correct template

## Setting Up Upstream Sync (Legacy)

### Step 1: Configure the Upstream Repository

After creating your research repo from the template, configure the upstream source:

#### Option A: Automatic Detection (Template Repos)

If your repository was created using GitHub's "Use this template" feature, the upstream is detected automatically. Simply run the setup workflow:

1. Go to **Actions** → **Template: Initialize Clone**
2. Click **Run workflow**
3. The `UPSTREAM_REPO` variable will be set automatically

#### Option B: Manual Configuration

Set the `UPSTREAM_REPO` repository variable manually:

1. Go to **Settings** → **Secrets and variables** → **Actions** → **Variables**
2. Click **New repository variable**
3. Name: `UPSTREAM_REPO`
4. Value: `owner/speculum-principum` (your template repo)

### Step 2: Set Up Authentication

The sync workflow needs a Personal Access Token (PAT) to create branches and pull requests:

1. Create a **classic** or **fine-grained** PAT:
   
   **Classic PAT** ([create here](https://github.com/settings/tokens/new)):
   - Select scopes: 
     - `repo` (full control of private repositories)
     - `workflow` (update GitHub Actions workflows) - **Required for syncing workflow files**
   
   **Fine-grained PAT** ([create here](https://github.com/settings/tokens?type=beta)):
   - **Repository access**: Only select repositories → choose your cloned repo
   - **Permissions**: 
     - Contents: Read and write
     - Pull requests: Read and write
     - Workflows: Read and write - **Required for syncing workflow files**
     - Metadata: Read-only (automatic)

2. Go to your cloned repo's **Settings → Secrets and variables → Actions → Secrets**
3. Add secret named `GH_TOKEN` with your PAT

> **Note:** The sync now uses the Contents API which works with both classic and fine-grained PATs. Classic PATs with `repo` scope or fine-grained PATs with Contents/PR write permissions are both supported.

#### Optional: Private Upstream Authentication

If your upstream repository is **private**, you also need to provide access to it:

1. Create another PAT with read access to the upstream repo
2. Add it as a secret named `GH_TOKEN`

For **public** upstream repos, only `GH_TOKEN` is needed.

## Running a Sync

### Manual Sync

1. Go to **Actions** → **Template: Sync from Upstream**
2. Click **Run workflow**
3. Fill in the options:
   - **upstream_repo**: Pre-filled from `UPSTREAM_REPO` variable
   - **upstream_branch**: Leave empty for default branch
   - **dry_run**: Check to preview changes without applying
   - **force_sync**: Check to skip validation and overwrite local changes
4. Click **Run workflow**

### Automatic Sync

The sync workflow runs automatically:
- **Weekly**: Every Sunday at midnight UTC
- **On release**: When the upstream publishes a release (if notifications are configured)

### Via API (Repository Dispatch)

Trigger sync programmatically:

```bash
curl -X POST \
  -H "Authorization: Bearer $GITHUB_TOKEN" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/repos/OWNER/REPO/dispatches \
  -d '{"event_type":"upstream-sync","client_payload":{"upstream_repo":"owner/speculum-principum"}}'
```

## Understanding Sync Results

### Pull Request Created

When changes are detected, the workflow creates a PR with:
- Summary of added, updated, and removed files
- List of all changed files
- Link to compare with upstream

**Review the PR carefully** before merging to ensure changes are compatible with your research.

### No Changes

If your repository is already in sync, no PR is created.

### Validation Failed

If the sync detects **file differences** in code directories, it will fail with a warning to protect against data loss.

**Common causes:**
1. **Outdated files** (upstream has new changes) - This is normal on first sync or after upstream updates
2. **Local modifications** (you edited code files) - Rare in template workflows

**To proceed:**
- **First sync or receiving upstream updates**: Use `force_sync=true` - this is safe when you haven't edited code files
- **You made intentional code changes**: Review carefully before using `force_sync=true` or merge conflicts manually

> **Note**: The validation can't distinguish between outdated files and local modifications. If you know you haven't modified code directories, it's safe to force sync.

## Directory Classification

| Type | Directories | Behavior |
|------|-------------|----------|
| **Code** | `src/`, `tests/`, `.github/`, `config/missions/`, `docs/`, `main.py`, `requirements.txt`, `pytest.ini` | Synced from upstream |
| **Research** | `evidence/`, `knowledge-graph/`, `reports/`, `dev_data/`, `devops/` | Preserved locally, never synced |

## Sync Status Tracking

The following repository variables track sync history:

| Variable | Description |
|----------|-------------|
| `SYNC_LAST_SHA` | Commit SHA of last successful sync |
| `SYNC_LAST_TIME` | Timestamp of last sync |
| `SYNC_COUNT` | Total number of syncs performed |
| `SYNC_LAST_PR` | PR number from last sync |

## Troubleshooting

### "No upstream repository specified"

Set the `UPSTREAM_REPO` repository variable or provide it as a workflow input.

### "Local modifications detected"

Your code directories have changes not present in upstream. Either:
1. Commit your changes to a separate branch first
2. Run with `force_sync=true` to overwrite (⚠️ data loss risk)

### "Resource not accessible by personal access token" (403 error)

This error typically occurs when your PAT lacks the required permissions, especially for workflow files in `.github/workflows/`.

**Solution:**

Update your `GH_TOKEN` secret with a PAT that has workflow permissions:

**Classic PAT:**
- Required scopes: `repo` + `workflow`

**Fine-grained PAT:**
- Required permissions:
  - Contents: Read and write
  - Pull requests: Read and write
  - Workflows: Read and write ← **Critical for workflow files**
  - Variables: Read-only ← **Required for reading repository variables**
  - Metadata: Read-only

**Other checks:**
1. Verify your token hasn't expired
2. Ensure the token has access to the target repository
3. For organization repos, check that PATs are allowed by organization policies

### "Failed to reach GitHub API"

Check:
- Network connectivity
- Token permissions (needs `repo` scope for private repos)
- Rate limits (5,000 requests/hour)

### Workflow file changes not applied

GitHub Actions workflows in `.github/workflows/` are synced but may require manual re-enabling if they were previously disabled.

## For Template Maintainers

### Notifying Downstream Repos (New: Topic-Based Discovery)

The system now automatically discovers downstream repositories using GitHub's topic search, eliminating the need for manual registry maintenance.

#### Setup for Upstream Template

1. **Configure signature secret** (one-time setup):
   ```bash
   # Generate a secret and store it in SYNC_SIGNATURE_SECRET
   openssl rand -hex 32
   ```
   Add this to your template repository secrets.

2. **Notify downstream on release**:
   The notification workflow runs automatically on releases and:
   - Discovers all repos in your organization with topic `speculum-downstream`
   - Verifies each repo's trust (not a fork, from template, has topic)
   - Sends signed `repository_dispatch` events
   - Each downstream validates the signature before syncing

#### Using the Notification Workflow

**Option A: Automatic (Recommended)**

The workflow triggers automatically when you publish a GitHub release.

**Option B: Manual Trigger**

1. Go to **Actions** → **3. Mgmt: Notify Downstream Repos**
2. Click **Run workflow**
3. Optionally check **dry_run** to preview without sending

#### Discovery and Trust Verification

The notification process:

```python
# Discover repos by topic
repos = discover_downstream_repos(
    org="your-org",
    topic="speculum-downstream",
    token=token
)

# Verify trust for each repo
for repo in repos:
    trusted, reason = verify_satellite_trust(
        repo=repo,
        expected_template="your-org/template-repo",
        token=token
    )
    
    if trusted:
        # Send signed dispatch
        notify_downstream_repos(...)
```

Trust checks performed:
- ✅ Repository is not a fork
- ✅ Repository was created from your template
- ✅ Repository has `speculum-downstream` topic

#### Migration from Static Registry

The old `DOWNSTREAM_REPOS` variable is deprecated. The system now uses:
- **Topic-based discovery**: Finds repos with `speculum-downstream` topic
- **Template verification**: Checks `template_repository` field
- **Dynamic updates**: No manual registry maintenance needed

Downstream repos are automatically included once they:
1. Add the `speculum-downstream` topic
2. Configure their `UPSTREAM_REPO` variable
3. Set up their `SYNC_SIGNATURE_SECRET`

### Security Model

#### Cryptographic Dispatch Verification

All repository dispatch events are signed with HMAC-SHA256:

```python
# Upstream: Generate signature
payload_data = f"{upstream_repo}|{upstream_branch}|{timestamp}"
signature = hmac.new(
    secret.encode('utf-8'),
    payload_data.encode('utf-8'),
    hashlib.sha256
).hexdigest()

# Downstream: Verify signature in workflow
EXPECTED_SIGNATURE=$(echo -n "$PAYLOAD_DATA" | \
    openssl dgst -sha256 -hmac "$SYNC_SIGNATURE_SECRET" | \
    cut -d' ' -f2)
```

#### Fork Blocking Rationale

Forks are blocked because:
1. **Trust chain**: Can't verify template origin for forks
2. **Security**: Prevents unauthorized sync from modified upstream
3. **Discovery**: Forks don't maintain topic relationship
4. **Best practice**: Research repos should use template, not fork

Use "Use this template" instead of "Fork" for creating research repositories.

#### Signature Failure Handling

If signature verification fails:
1. Workflow exits with error
2. No sync is performed
3. Security event should be investigated
4. Possible causes:
   - Secret mismatch between upstream/downstream
   - Replay attack attempt
   - Clock skew (check timestamps)

#### Secret Rotation

To rotate `SYNC_SIGNATURE_SECRET`:
1. Generate new secret
2. Update downstream repos first (old secret still works)
3. Update upstream template last
4. For dual-secret validation during rotation, modify verification step

### Best Practices

- Use semantic versioning for releases
- Document breaking changes in release notes
- Test sync with a staging repo before major releases
- Keep the downstream registry updated

## API Reference

### Key Functions (for developers)

```python
from src.integrations.github.sync import (
    sync_from_upstream,      # Main sync operation
    validate_pre_sync,       # Check for local modifications
    get_sync_status,         # Read sync tracking variables
    configure_upstream_variable,  # Set UPSTREAM_REPO variable
)
```

### Example: Dry Run Sync

```python
from src.integrations.github.sync import sync_from_upstream

result = sync_from_upstream(
    downstream_repo="owner/my-research",
    upstream_repo="owner/speculum-principum",
    downstream_token=token,
    dry_run=True,
)

print(result.summary())
print(f"Changes: {len(result.changes)}")
```
