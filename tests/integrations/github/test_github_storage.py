"""Tests for GitHub storage client."""

from __future__ import annotations

import json
import os
from unittest import mock

import pytest

from src.integrations.github.storage import (
    GitHubStorageClient,
    get_github_storage_client,
    is_github_actions,
)


class TestIsGitHubActions:
    """Tests for is_github_actions helper."""

    def test_returns_true_when_env_var_is_true(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            assert is_github_actions() is True

    def test_returns_false_when_env_var_is_false(self):
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            assert is_github_actions() is False

    def test_returns_false_when_env_var_not_set(self):
        env = os.environ.copy()
        env.pop("GITHUB_ACTIONS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            assert is_github_actions() is False


class TestGitHubStorageClientInit:
    """Tests for GitHubStorageClient initialization."""

    def test_init_stores_parameters(self):
        client = GitHubStorageClient(
            token="test-token",
            repository="owner/repo",
            branch="main",
            api_url="https://api.github.com",
        )
        assert client.token == "test-token"
        assert client.repository == "owner/repo"
        assert client.branch == "main"
        assert client.api_url == "https://api.github.com"

    def test_init_with_defaults(self):
        client = GitHubStorageClient(
            token="test-token",
            repository="owner/repo",
        )
        assert client.branch == "main"
        assert client.api_url == "https://api.github.com"


class TestGitHubStorageClientFromEnvironment:
    """Tests for GitHubStorageClient.from_environment class method."""

    def test_returns_none_when_not_in_actions(self):
        env = os.environ.copy()
        env.pop("GITHUB_ACTIONS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            client = GitHubStorageClient.from_environment()
            assert client is None

    def test_returns_none_when_token_missing(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "owner/repo",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = GitHubStorageClient.from_environment()
            assert client is None

    def test_returns_none_when_repository_missing(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_TOKEN": "test-token",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = GitHubStorageClient.from_environment()
            assert client is None

    def test_returns_client_with_github_token(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_REF_NAME": "feature-branch",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = GitHubStorageClient.from_environment()
            assert client is not None
            assert client.token == "test-token"
            assert client.repository == "owner/repo"
            assert client.branch == "feature-branch"

    def test_returns_client_with_gh_token_fallback(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GH_TOKEN": "gh-token",
            "GITHUB_REPOSITORY": "owner/repo",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = GitHubStorageClient.from_environment()
            assert client is not None
            assert client.token == "gh-token"

    def test_prefers_github_token_over_gh_token(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_TOKEN": "github-token",
            "GH_TOKEN": "gh-token",
            "GITHUB_REPOSITORY": "owner/repo",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = GitHubStorageClient.from_environment()
            assert client is not None
            assert client.token == "github-token"


class TestGetGitHubStorageClient:
    """Tests for get_github_storage_client convenience function."""

    def test_returns_none_when_not_in_actions(self):
        env = os.environ.copy()
        env.pop("GITHUB_ACTIONS", None)
        with mock.patch.dict(os.environ, env, clear=True):
            assert get_github_storage_client() is None

    def test_returns_client_when_in_actions(self):
        env = {
            "GITHUB_ACTIONS": "true",
            "GITHUB_TOKEN": "test-token",
            "GITHUB_REPOSITORY": "owner/repo",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            client = get_github_storage_client()
            assert client is not None


class TestGitHubStorageClientCommitFile:
    """Tests for GitHubStorageClient.commit_file method."""

    def test_commit_file_normalizes_path(self):
        """Test that leading slashes are stripped from paths."""
        client = GitHubStorageClient(
            token="test-token",
            repository="owner/repo",
        )
        
        with mock.patch(
            "src.integrations.github.storage.commit_file"
        ) as mock_commit:
            mock_commit.return_value = {"sha": "abc123"}
            
            client.commit_file(
                path="/leading/slash/file.json",
                content="test content",
                message="Test commit",
            )
            
            mock_commit.assert_called_once()
            call_kwargs = mock_commit.call_args.kwargs
            assert call_kwargs["path"] == "leading/slash/file.json"

    def test_commit_file_passes_all_parameters(self):
        """Test that all parameters are passed to underlying commit_file."""
        client = GitHubStorageClient(
            token="test-token",
            repository="owner/repo",
            branch="dev",
            api_url="https://custom.api.github.com",
        )
        
        with mock.patch(
            "src.integrations.github.storage.commit_file"
        ) as mock_commit:
            mock_commit.return_value = {"sha": "abc123"}
            
            result = client.commit_file(
                path="path/to/file.json",
                content='{"key": "value"}',
                message="Update file",
            )
            
            mock_commit.assert_called_once_with(
                token="test-token",
                repository="owner/repo",
                path="path/to/file.json",
                content='{"key": "value"}',
                message="Update file",
                branch="dev",
                api_url="https://custom.api.github.com",
            )
            assert result == {"sha": "abc123"}


class TestGitHubStorageClientCommitFilesBatch:
    """Tests for GitHubStorageClient.commit_files_batch method."""

    def test_commit_files_batch_normalizes_paths(self):
        """Test that leading slashes are stripped from all paths."""
        client = GitHubStorageClient(
            token="test-token",
            repository="owner/repo",
        )
        
        with mock.patch(
            "src.integrations.github.files.commit_files_batch"
        ) as mock_batch:
            mock_batch.return_value = {"sha": "abc123", "files_count": 2}
            
            client.commit_files_batch(
                files=[
                    ("/path/to/file1.json", "content1"),
                    ("/path/to/file2.json", "content2"),
                ],
                message="Batch commit",
            )
            
            mock_batch.assert_called_once()
            call_kwargs = mock_batch.call_args.kwargs
            files = call_kwargs["files"]
            assert files[0][0] == "path/to/file1.json"
            assert files[1][0] == "path/to/file2.json"

    def test_commit_files_batch_passes_all_parameters(self):
        """Test that all parameters are passed correctly."""
        client = GitHubStorageClient(
            token="test-token",
            repository="owner/repo",
            branch="main",
        )
        
        with mock.patch(
            "src.integrations.github.files.commit_files_batch"
        ) as mock_batch:
            mock_batch.return_value = {"sha": "abc123", "files_count": 2}
            
            result = client.commit_files_batch(
                files=[
                    ("file1.json", "content1"),
                    ("file2.json", "content2"),
                ],
                message="Batch update",
            )
            
            mock_batch.assert_called_once()
            call_kwargs = mock_batch.call_args.kwargs
            assert call_kwargs["token"] == "test-token"
            assert call_kwargs["repository"] == "owner/repo"
            assert call_kwargs["message"] == "Batch update"
            assert call_kwargs["branch"] == "main"
            assert result["files_count"] == 2
