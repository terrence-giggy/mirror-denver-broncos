"""Integration tests for storage classes with GitHubStorageClient."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from src.knowledge.storage import (
    KnowledgeGraphStorage,
    SourceEntry,
    SourceRegistry,
)
from src.parsing.storage import ParseStorage


class MockGitHubStorageClient:
    """Mock GitHub storage client for testing."""

    def __init__(self):
        self.committed_files: list[tuple[str, str, str]] = []  # (path, content, message)
        self.batch_commits: list[tuple[list, str]] = []  # (files, message)
        self._pr_branch: str | None = None

    def commit_file(self, path: str, content: str | bytes, message: str) -> dict:
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.committed_files.append((path, content, message))
        return {"sha": "mock-sha-123", "commit": {"sha": "mock-commit-sha"}}

    def commit_to_pr_branch(
        self,
        path: str,
        content: str | bytes,
        message: str,
        timestamp_suffix: str | None = None,
    ) -> dict:
        """Mock commit_to_pr_branch for PR-based workflow."""
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.committed_files.append((path, content, message))
        self._pr_branch = f"content-acquisition-{timestamp_suffix or 'test'}"
        return {"sha": "mock-sha-123", "commit": {"sha": "mock-commit-sha"}}

    def commit_files_batch(
        self, files: list[tuple[str, str | bytes]], message: str
    ) -> dict:
        normalized = []
        for path, content in files:
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            normalized.append((path, content))
        self.batch_commits.append((normalized, message))
        return {"sha": "mock-sha-123", "files_count": len(files)}


class TestSourceRegistryWithGitHubClient:
    """Tests for SourceRegistry with GitHubStorageClient."""

    def test_save_source_uses_github_client_batch(self, tmp_path: Path):
        """Test that save_source uses batch commit when github_client is set."""
        mock_client = MockGitHubStorageClient()
        registry = SourceRegistry(
            root=tmp_path,
            github_client=mock_client,
            project_root=tmp_path,  # Set project_root to tmp_path for testing
        )

        now = datetime.now(timezone.utc)
        source = SourceEntry(
            url="https://example.com/test",
            name="Test Source",
            source_type="derived",
            status="active",
            last_verified=now,
            added_at=now,
            added_by="test-user",
            proposal_discussion=None,
            implementation_issue=123,
            credibility_score=0.8,
            is_official=False,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency=None,
        )

        registry.save_source(source)

        # Should use batch commit for source + index
        assert len(mock_client.batch_commits) == 1
        files, message = mock_client.batch_commits[0]
        assert len(files) == 2
        assert "Test Source" in message

        # Verify source file content
        source_path, source_content = files[0]
        assert source_path.endswith(".json")
        source_data = json.loads(source_content)
        assert source_data["url"] == "https://example.com/test"

        # Verify index file content
        index_path, index_content = files[1]
        assert "registry.json" in index_path
        index_data = json.loads(index_content)
        assert "sources" in index_data

    def test_save_source_local_without_github_client(self, tmp_path: Path):
        """Test that save_source writes locally when no github_client."""
        registry = SourceRegistry(root=tmp_path, github_client=None)

        now = datetime.now(timezone.utc)
        source = SourceEntry(
            url="https://example.com/local",
            name="Local Source",
            source_type="primary",
            status="active",
            last_verified=now,
            added_at=now,
            added_by="local-user",
            proposal_discussion=None,
            implementation_issue=None,
            credibility_score=0.9,
            is_official=True,
            requires_auth=False,
            discovered_from=None,
            parent_source_url=None,
            content_type="webpage",
            update_frequency=None,
        )

        registry.save_source(source)

        # Verify files written locally
        assert registry._registry_path.exists()
        source_path = registry._get_source_path(source.url)
        assert source_path.exists()

        # Verify content
        source_data = json.loads(source_path.read_text())
        assert source_data["name"] == "Local Source"


class TestKnowledgeGraphStorageWithGitHubClient:
    """Tests for KnowledgeGraphStorage with GitHubStorageClient."""

    def test_save_extracted_people_uses_github_client(self, tmp_path: Path):
        """Test that save_extracted_people uses github_client when set."""
        mock_client = MockGitHubStorageClient()
        storage = KnowledgeGraphStorage(
            root=tmp_path,
            github_client=mock_client,
            project_root=tmp_path,
        )

        storage.save_extracted_people("checksum123", ["Alice", "Bob"])

        assert len(mock_client.committed_files) == 1
        path, content, message = mock_client.committed_files[0]
        assert "checksum123" in path
        assert "people" in path
        data = json.loads(content)
        assert data["people"] == ["Alice", "Bob"]

    def test_save_extracted_organizations_uses_github_client(self, tmp_path: Path):
        """Test that save_extracted_organizations uses github_client when set."""
        mock_client = MockGitHubStorageClient()
        storage = KnowledgeGraphStorage(
            root=tmp_path,
            github_client=mock_client,
            project_root=tmp_path,
        )

        storage.save_extracted_organizations("checksum456", ["Acme Corp", "TechCo"])

        assert len(mock_client.committed_files) == 1
        path, content, message = mock_client.committed_files[0]
        assert "organizations" in path
        data = json.loads(content)
        assert data["organizations"] == ["Acme Corp", "TechCo"]

    def test_save_extracted_concepts_uses_github_client(self, tmp_path: Path):
        """Test that save_extracted_concepts uses github_client when set."""
        mock_client = MockGitHubStorageClient()
        storage = KnowledgeGraphStorage(
            root=tmp_path,
            github_client=mock_client,
            project_root=tmp_path,
        )

        storage.save_extracted_concepts("checksum789", ["Machine Learning", "AI"])

        assert len(mock_client.committed_files) == 1
        path, content, message = mock_client.committed_files[0]
        assert "concepts" in path
        data = json.loads(content)
        assert data["concepts"] == ["Machine Learning", "AI"]

    def test_get_operations_still_use_local_filesystem(self, tmp_path: Path):
        """Test that read operations use local filesystem even with github_client."""
        mock_client = MockGitHubStorageClient()
        storage = KnowledgeGraphStorage(
            root=tmp_path,
            github_client=mock_client,
            project_root=tmp_path,
        )

        # Write directly to filesystem (simulating checkout)
        people_dir = tmp_path / "people"
        people_dir.mkdir(parents=True, exist_ok=True)
        people_file = people_dir / "localcheck.json"
        people_file.write_text(
            json.dumps({
                "source_checksum": "localcheck",
                "people": ["Local Person"],
                "extracted_at": datetime.now(timezone.utc).isoformat(),
            })
        )

        # Read should work from local filesystem
        result = storage.get_extracted_people("localcheck")
        assert result is not None
        assert result.people == ["Local Person"]

        # No API calls should have been made for reads
        assert len(mock_client.committed_files) == 0


class TestParseStorageWithGitHubClient:
    """Tests for ParseStorage with GitHubStorageClient."""

    def test_write_manifest_uses_github_client(self, tmp_path: Path):
        """Test that manifest writes use github_client when set."""
        mock_client = MockGitHubStorageClient()
        storage = ParseStorage(
            root=tmp_path,
            github_client=mock_client,
            project_root=tmp_path,
        )

        # Force a manifest write
        storage._write_manifest()

        assert len(mock_client.committed_files) == 1
        path, content, message = mock_client.committed_files[0]
        assert "manifest.json" in path
        data = json.loads(content)
        assert "version" in data

    def test_write_manifest_local_without_github_client(self, tmp_path: Path):
        """Test that manifest writes locally when no github_client."""
        storage = ParseStorage(root=tmp_path, github_client=None)

        storage._write_manifest()

        assert storage.manifest_path.exists()
        data = json.loads(storage.manifest_path.read_text())
        assert "version" in data
