"""Shared GitHub context utilities for tool handlers.

This module provides a standardized way for toolkit handlers to obtain
a GitHubStorageClient when running in GitHub Actions workflows.
"""

from __future__ import annotations

from src.integrations.github.storage import GitHubStorageClient, get_github_storage_client


def resolve_github_client() -> GitHubStorageClient | None:
    """Get a GitHub storage client for the current execution context.

    Returns a GitHubStorageClient when running in GitHub Actions with
    proper credentials (GITHUB_TOKEN and GITHUB_REPOSITORY env vars).
    Returns None when running locally or without credentials.

    Use this function in tool handlers that need to persist data:

        from src.orchestration.toolkit._github_context import resolve_github_client

        def _my_handler(args: dict) -> ToolResult:
            github_client = resolve_github_client()
            registry = SourceRegistry(github_client=github_client)
            # ... handler logic ...

    Returns:
        GitHubStorageClient if running in GitHub Actions, None otherwise.
    """
    return get_github_storage_client()


__all__ = ["resolve_github_client", "GitHubStorageClient"]
