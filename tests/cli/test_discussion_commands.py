"""Tests for discussion CLI commands."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.cli.commands.discussions import (
    list_entities_cli,
    sync_discussions_cli,
    _resolve_entity_types,
    _get_aggregator,
)
from src.integrations.github.discussions import GitHubDiscussionError


class TestResolveEntityTypes:
    def test_all_returns_both_types(self) -> None:
        result = _resolve_entity_types("all")
        assert result == ["Person", "Organization"]

    def test_person_returns_person(self) -> None:
        result = _resolve_entity_types("Person")
        assert result == ["Person"]

    def test_organization_returns_organization(self) -> None:
        result = _resolve_entity_types("Organization")
        assert result == ["Organization"]


class TestGetAggregator:
    @patch("src.cli.commands.discussions.KnowledgeGraphStorage")
    @patch("src.cli.commands.discussions.KnowledgeAggregator")
    def test_creates_aggregator_with_path(
        self,
        mock_aggregator_cls: MagicMock,
        mock_storage_cls: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_storage = MagicMock()
        mock_storage_cls.return_value = mock_storage
        
        aggregator = _get_aggregator(str(tmp_path))
        
        mock_storage_cls.assert_called_once_with(root=tmp_path)
        mock_aggregator_cls.assert_called_once_with(storage=mock_storage)


class TestListEntitiesCli:
    @pytest.fixture
    def mock_aggregator(self) -> MagicMock:
        aggregator = MagicMock()
        aggregator.list_entities.return_value = ["Niccolo Machiavelli", "Cesare Borgia"]
        
        def get_entity(name: str, entity_type: str) -> MagicMock:
            entity = MagicMock()
            entity.name = name
            entity.entity_type = entity_type
            entity.source_checksums = ["abc123", "def456"]
            entity.associations_as_source = [MagicMock(), MagicMock()]
            entity.associations_as_target = [MagicMock()]
            return entity
        
        aggregator.get_aggregated_entity.side_effect = get_entity
        return aggregator

    @patch("src.cli.commands.discussions._get_aggregator")
    def test_list_entities_table_format(
        self,
        mock_get_aggregator: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        
        args = MagicMock()
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.format = "table"
        
        result = list_entities_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "Niccolo Machiavelli" in captured.out
        assert "Cesare Borgia" in captured.out

    @patch("src.cli.commands.discussions._get_aggregator")
    def test_list_entities_json_format(
        self,
        mock_get_aggregator: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        
        args = MagicMock()
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.format = "json"
        
        result = list_entities_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 2
        assert data[0]["name"] == "Niccolo Machiavelli"

    @patch("src.cli.commands.discussions._get_aggregator")
    def test_list_entities_empty(
        self,
        mock_get_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_aggregator = MagicMock()
        mock_aggregator.list_entities.return_value = []
        mock_get_aggregator.return_value = mock_aggregator
        
        args = MagicMock()
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "all"
        args.format = "table"
        
        result = list_entities_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "No entities found" in captured.out


class TestSyncDiscussionsCli:
    @pytest.fixture
    def mock_aggregator(self) -> MagicMock:
        aggregator = MagicMock()
        aggregator.list_entities.return_value = ["Niccolo Machiavelli"]
        
        entity = MagicMock()
        entity.name = "Niccolo Machiavelli"
        entity.entity_type = "Person"
        entity.profiles = []
        entity.associations_as_source = []
        entity.associations_as_target = []
        entity.source_checksums = []
        aggregator.get_aggregated_entity.return_value = entity
        
        return aggregator

    @patch("src.cli.commands.discussions.build_entity_discussion_content")
    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_dry_run(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_build_content: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_build_content.return_value = "# Test Content"
        
        # Category exists
        category = MagicMock()
        category.id = "DIC_cat123"
        mock_discussions.get_category_by_name.return_value = category
        
        # No existing discussion
        mock_discussions.find_discussion_by_title.return_value = None
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = True
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "Would create" in captured.out or "would_create" in captured.out
        # Should NOT actually create
        mock_discussions.create_discussion.assert_not_called()

    @patch("src.cli.commands.discussions.build_entity_discussion_content")
    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_creates_discussion(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_build_content: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_build_content.return_value = "# Test Content"
        
        # Category exists
        category = MagicMock()
        category.id = "DIC_cat123"
        mock_discussions.get_category_by_name.return_value = category
        
        # No existing discussion
        mock_discussions.find_discussion_by_title.return_value = None
        
        # Create returns discussion
        created = MagicMock()
        created.number = 42
        created.url = "https://github.com/test/repo/discussions/42"
        mock_discussions.create_discussion.return_value = created
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "Created" in captured.out
        mock_discussions.create_discussion.assert_called_once()

    @patch("src.cli.commands.discussions.build_entity_discussion_content")
    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_updates_existing_discussion(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_build_content: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_build_content.return_value = "# New Content"
        
        # Category exists
        category = MagicMock()
        category.id = "DIC_cat123"
        mock_discussions.get_category_by_name.return_value = category
        
        # Existing discussion with different body
        existing = MagicMock()
        existing.id = "D_existing"
        existing.number = 42
        existing.url = "https://github.com/test/repo/discussions/42"
        existing.body = "old content"
        mock_discussions.find_discussion_by_title.return_value = existing
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "Updated" in captured.out
        mock_discussions.update_discussion.assert_called_once()
        mock_discussions.add_discussion_comment.assert_called_once()

    @patch("src.cli.commands.discussions.build_entity_discussion_content")
    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_skips_unchanged_discussion(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_build_content: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        expected_body = "# Same Content"
        mock_build_content.return_value = expected_body
        
        # Category exists
        category = MagicMock()
        category.id = "DIC_cat123"
        mock_discussions.get_category_by_name.return_value = category
        
        # Existing discussion with same body
        existing = MagicMock()
        existing.id = "D_existing"
        existing.number = 42
        existing.url = "https://github.com/test/repo/discussions/42"
        existing.body = expected_body
        mock_discussions.find_discussion_by_title.return_value = existing
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "Unchanged" in captured.out
        mock_discussions.update_discussion.assert_not_called()

    @patch("src.cli.commands.discussions.github_discussions")
    def test_sync_handles_missing_credentials(
        self,
        mock_discussions: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.side_effect = GitHubDiscussionError("Token not found")
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        args = MagicMock()
        args.repository = None
        args.token = None
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "all"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 1
        captured = capsys.readouterr()
        assert "Token not found" in captured.err

    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_handles_missing_category(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        
        # Category does not exist
        mock_discussions.get_category_by_name.return_value = None
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 1
        captured = capsys.readouterr()
        assert "Category" in captured.err
        assert "not found" in captured.err

    @patch("src.cli.commands.discussions.build_entity_discussion_content")
    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_writes_report(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_build_content: MagicMock,
        mock_aggregator: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_build_content.return_value = "# Test Content"
        
        category = MagicMock()
        category.id = "DIC_cat123"
        mock_discussions.get_category_by_name.return_value = category
        mock_discussions.find_discussion_by_title.return_value = None
        
        created = MagicMock()
        created.number = 42
        created.url = "https://github.com/test/repo/discussions/42"
        mock_discussions.create_discussion.return_value = created
        
        output_path = tmp_path / "report.json"
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = str(output_path)
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        assert output_path.exists()
        
        with open(output_path) as f:
            report = json.load(f)
        
        assert report["repository"] == "test/repo"
        assert report["summary"]["created"] == 1
        assert len(report["results"]) == 1

    @patch("src.cli.commands.discussions.build_entity_discussion_content")
    @patch("src.cli.commands.discussions.github_discussions")
    @patch("src.cli.commands.discussions._get_aggregator")
    def test_sync_specific_entity(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        mock_build_content: MagicMock,
        mock_aggregator: MagicMock,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Return multiple entities from list
        mock_aggregator.list_entities.return_value = ["Niccolo Machiavelli", "Cesare Borgia"]
        mock_get_aggregator.return_value = mock_aggregator
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_build_content.return_value = "# Test Content"
        
        category = MagicMock()
        category.id = "DIC_cat123"
        mock_discussions.get_category_by_name.return_value = category
        mock_discussions.find_discussion_by_title.return_value = None
        
        created = MagicMock()
        created.number = 42
        created.url = "https://github.com/test/repo/discussions/42"
        mock_discussions.create_discussion.return_value = created
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = "knowledge-graph"
        args.entity_type = "Person"
        args.entity_name = "Niccolo Machiavelli"  # Only sync this one
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        # Should only call create once for the specific entity
        assert mock_discussions.create_discussion.call_count == 1


class TestCliIntegration:
    def test_main_py_includes_discussion_commands(self) -> None:
        """Verify discussion commands are registered in main."""
        import main
        
        parser = main._build_command_parser()
        
        # Get subparsers
        subparsers_action = None
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                subparsers_action = action
                break
        
        assert subparsers_action is not None
        assert "sync-discussions" in subparsers_action.choices
        assert "list-entities" in subparsers_action.choices


import argparse
