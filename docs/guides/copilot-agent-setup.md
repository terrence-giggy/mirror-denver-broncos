# Copilot Agent Setup Guide

This guide explains how to configure GitHub Copilot coding agent for automated extraction workflows.

## Required Repository Secrets

The extraction workflow requires access to GitHub Models API for LLM-based entity extraction. This requires the `GH_TOKEN` repository secret to be configured for use by Copilot agents.

### Configuring Secrets for Copilot Agent

1. Go to **Repository Settings**
2. Navigate to **Copilot** → **Secrets**
3. Add the `GH_TOKEN` secret to the list of secrets available to Copilot agents

This allows the Copilot agent to authenticate with GitHub Models API when calling:
- `python main.py extract --checksum <checksum>`
- `python main.py extract --checksum <checksum> --orgs`
- `python main.py extract --checksum <checksum> --concepts`
- `python main.py extract --checksum <checksum> --associations`

### Why This Is Required

The entity extraction commands use the `CopilotClient` class which requires authentication to GitHub Models API. The client looks for credentials in this order:

1. `api_key` parameter (not used by CLI)
2. `GH_TOKEN` environment variable
3. `GITHUB_TOKEN` environment variable

Without one of these tokens, extraction will fail with:
```
Initialization error: GitHub token required. Set GH_TOKEN or GITHUB_TOKEN environment variable or pass api_key parameter.
```

### Verification

To verify the secret is properly configured:

1. Trigger an extraction workflow by labeling an issue with `extraction-queue`
2. Check that the Copilot agent can successfully run extraction commands
3. Monitor for successful entity extractions in the `knowledge-graph/` directory

### Troubleshooting

**Problem**: Extraction fails with "GitHub token required" error

**Solution**:
1. Verify `GH_TOKEN` is set in Repository Settings → Secrets and variables → Actions
2. Verify `GH_TOKEN` is configured in Repository Settings → Copilot → Secrets
3. The secret must be available in BOTH locations:
   - Actions secrets: For regular GitHub Actions workflows
   - Copilot secrets: For Copilot agent executions

**Problem**: Secret is configured but still not available

**Solution**:
- Check that the repository has Copilot enabled
- Verify the secret name is exactly `GH_TOKEN` (case-sensitive)
- Try removing and re-adding the secret to Copilot configuration
- Check GitHub status page for any Copilot service issues

## Related Documentation

- [Extraction Pipeline](extraction-pipeline.md) - Full extraction workflow documentation
- [Entity Extraction](entity-extraction.md) - Technical details on extraction logic
