"""Integration tests for the Discussion sync workflow.

These tests verify the end-to-end behavior of syncing knowledge graph
entities to GitHub Discussions without making actual API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.cli.commands.discussions import sync_discussions_cli
from src.integrations.github.discussions import (
    Discussion,
    DiscussionCategory,
    DiscussionComment,
    GitHubDiscussionError,
)
from src.knowledge.aggregation import KnowledgeAggregator, AggregatedEntity
from src.knowledge.storage import KnowledgeGraphStorage


class TestIdempotentSync:
    """Test that sync operations are idempotent (safe to run multiple times)."""

    @pytest.fixture
    def knowledge_graph_dir(self, tmp_path: Path) -> Path:
        """Create a minimal knowledge graph structure."""
        kg_dir = tmp_path / "knowledge-graph"
        
        # Create directories
        (kg_dir / "profiles").mkdir(parents=True)
        (kg_dir / "people").mkdir(parents=True)
        (kg_dir / "organizations").mkdir(parents=True)
        (kg_dir / "concepts").mkdir(parents=True)
        (kg_dir / "associations").mkdir(parents=True)
        
        # Create a test profile
        # Note: Use ISO format without 'Z' suffix for Python compatibility
        profile_data = {
            "source_checksum": "abc123",
            "profiles": [
                {
                    "name": "Niccolo Machiavelli",
                    "entity_type": "Person",
                    "summary": "Florentine diplomat and political philosopher",
                    "attributes": {
                        "birth_year": 1469,
                        "death_year": 1527,
                        "notable_work": "The Prince"
                    },
                    "mentions": ["Machiavelli wrote The Prince"],
                    "confidence": 0.95
                },
                {
                    "name": "Florence",
                    "entity_type": "Organization",
                    "summary": "Italian city-state during the Renaissance",
                    "attributes": {
                        "type": "City-State"
                    },
                    "mentions": ["Florence was a major power"],
                    "confidence": 0.9
                }
            ],
            "extracted_at": "2025-11-27T00:00:00",
            "metadata": {}
        }
        
        (kg_dir / "profiles" / "abc123.json").write_text(
            json.dumps(profile_data, indent=2),
            encoding="utf-8"
        )
        
        return kg_dir

    @patch("src.cli.commands.discussions.github_discussions")
    def test_sync_creates_then_skips_unchanged(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Running sync twice should create on first run, skip on second."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        # Category exists
        people_category = DiscussionCategory(
            id="DIC_people",
            name="People",
            slug="people",
            description="Entity profiles for people"
        )
        
        def get_category(token: str, repository: str, category_name: str) -> DiscussionCategory | None:
            if category_name == "People":
                return people_category
            return None
        
        mock_discussions.get_category_by_name.side_effect = get_category
        
        # First run: no existing discussions
        created_discussion = Discussion(
            id="D_123",
            number=1,
            title="Niccolo Machiavelli",
            body="# Niccolo Machiavelli\n...",
            url="https://github.com/test/repo/discussions/1",
            category_id=people_category.id,
            category_name=people_category.name,
        )
        mock_discussions.find_discussion_by_title.return_value = None
        mock_discussions.create_discussion.return_value = created_discussion
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        # First sync - should create
        result1 = sync_discussions_cli(args)
        assert result1 == 0
        captured1 = capsys.readouterr()
        assert "Created" in captured1.out
        mock_discussions.create_discussion.assert_called_once()
        
        # Reset mocks for second run
        mock_discussions.create_discussion.reset_mock()
        mock_discussions.update_discussion.reset_mock()
        
        # Second run: discussion exists with same content
        # Capture the body that was used in creation
        create_call_args = mock_discussions.create_discussion.call_args
        
        # Mock find to return existing discussion
        # The body should match what we'd generate
        from src.knowledge.aggregation import build_entity_discussion_content
        
        storage = KnowledgeGraphStorage(root=knowledge_graph_dir)
        aggregator = KnowledgeAggregator(storage=storage)
        entity = aggregator.get_aggregated_entity("Niccolo Machiavelli", "Person")
        assert entity is not None
        expected_body = build_entity_discussion_content(entity)
        
        existing_discussion = Discussion(
            id="D_123",
            number=1,
            title="Niccolo Machiavelli",
            body=expected_body,
            url="https://github.com/test/repo/discussions/1",
            category_id=people_category.id,
            category_name=people_category.name,
        )
        mock_discussions.find_discussion_by_title.return_value = existing_discussion
        
        # Second sync - should skip (unchanged)
        result2 = sync_discussions_cli(args)
        assert result2 == 0
        captured2 = capsys.readouterr()
        assert "Unchanged" in captured2.out
        mock_discussions.create_discussion.assert_not_called()
        mock_discussions.update_discussion.assert_not_called()

    @patch("src.cli.commands.discussions.github_discussions")
    def test_sync_updates_when_content_changes(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Sync should update discussion when knowledge graph content changes."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        people_category = DiscussionCategory(
            id="DIC_people",
            name="People",
            slug="people",
            description="Entity profiles for people"
        )
        mock_discussions.get_category_by_name.return_value = people_category
        
        # Existing discussion with OLD content
        existing_discussion = Discussion(
            id="D_123",
            number=1,
            title="Niccolo Machiavelli",
            body="# Old Content\n\nThis is outdated.",
            url="https://github.com/test/repo/discussions/1",
            category_id=people_category.id,
            category_name=people_category.name,
        )
        mock_discussions.find_discussion_by_title.return_value = existing_discussion
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
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


class TestDryRunMode:
    """Test that dry-run mode doesn't make any changes."""

    @pytest.fixture
    def knowledge_graph_dir(self, tmp_path: Path) -> Path:
        """Create a minimal knowledge graph structure."""
        kg_dir = tmp_path / "knowledge-graph"
        
        (kg_dir / "profiles").mkdir(parents=True)
        (kg_dir / "people").mkdir(parents=True)
        (kg_dir / "organizations").mkdir(parents=True)
        (kg_dir / "concepts").mkdir(parents=True)
        (kg_dir / "associations").mkdir(parents=True)
        
        profile_data = {
            "source_checksum": "test123",
            "profiles": [
                {
                    "name": "Test Person",
                    "entity_type": "Person",
                    "summary": "A test person",
                    "attributes": {},
                    "mentions": [],
                    "confidence": 0.9
                }
            ],
            "extracted_at": "2025-11-27T00:00:00",
            "metadata": {}
        }
        
        (kg_dir / "profiles" / "test123.json").write_text(
            json.dumps(profile_data, indent=2),
            encoding="utf-8"
        )
        
        return kg_dir

    @patch("src.cli.commands.discussions.github_discussions")
    def test_dry_run_does_not_create(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Dry run should show what would be created without doing it."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        people_category = DiscussionCategory(
            id="DIC_people",
            name="People",
            slug="people",
            description=""
        )
        mock_discussions.get_category_by_name.return_value = people_category
        mock_discussions.find_discussion_by_title.return_value = None
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = True
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "Would create" in captured.out
        mock_discussions.create_discussion.assert_not_called()
        mock_discussions.update_discussion.assert_not_called()

    @patch("src.cli.commands.discussions.github_discussions")
    def test_dry_run_does_not_update(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Dry run should show what would be updated without doing it."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        people_category = DiscussionCategory(
            id="DIC_people",
            name="People",
            slug="people",
            description=""
        )
        mock_discussions.get_category_by_name.return_value = people_category
        
        # Existing discussion with different content
        existing = Discussion(
            id="D_123",
            number=1,
            title="Test Person",
            body="Old content",
            url="https://github.com/test/repo/discussions/1",
            category_id=people_category.id,
            category_name=people_category.name,
        )
        mock_discussions.find_discussion_by_title.return_value = existing
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = True
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "DRY RUN" in captured.out
        assert "Would update" in captured.out
        mock_discussions.update_discussion.assert_not_called()
        mock_discussions.add_discussion_comment.assert_not_called()


class TestReportGeneration:
    """Test that sync generates accurate reports."""

    @pytest.fixture
    def knowledge_graph_dir(self, tmp_path: Path) -> Path:
        """Create knowledge graph with multiple entities."""
        kg_dir = tmp_path / "knowledge-graph"
        
        (kg_dir / "profiles").mkdir(parents=True)
        (kg_dir / "people").mkdir(parents=True)
        (kg_dir / "organizations").mkdir(parents=True)
        (kg_dir / "concepts").mkdir(parents=True)
        (kg_dir / "associations").mkdir(parents=True)
        
        profile_data = {
            "source_checksum": "multi123",
            "profiles": [
                {
                    "name": "Person One",
                    "entity_type": "Person",
                    "summary": "First person",
                    "attributes": {},
                    "mentions": [],
                    "confidence": 0.9
                },
                {
                    "name": "Person Two",
                    "entity_type": "Person",
                    "summary": "Second person",
                    "attributes": {},
                    "mentions": [],
                    "confidence": 0.85
                },
                {
                    "name": "Org One",
                    "entity_type": "Organization",
                    "summary": "First org",
                    "attributes": {},
                    "mentions": [],
                    "confidence": 0.9
                }
            ],
            "extracted_at": "2025-11-27T00:00:00",
            "metadata": {}
        }
        
        (kg_dir / "profiles" / "multi123.json").write_text(
            json.dumps(profile_data, indent=2),
            encoding="utf-8"
        )
        
        return kg_dir

    @patch("src.cli.commands.discussions.github_discussions")
    def test_report_contains_all_results(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Report should contain results for all processed entities."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        people_category = DiscussionCategory(
            id="DIC_people", name="People", slug="people", description=""
        )
        orgs_category = DiscussionCategory(
            id="DIC_orgs", name="Organizations", slug="organizations", description=""
        )
        
        def get_category(token: str, repository: str, category_name: str) -> DiscussionCategory | None:
            if category_name == "People":
                return people_category
            elif category_name == "Organizations":
                return orgs_category
            return None
        
        mock_discussions.get_category_by_name.side_effect = get_category
        mock_discussions.find_discussion_by_title.return_value = None
        
        # Create returns sequential discussions
        call_count = [0]
        def create_disc(**kwargs: Any) -> Discussion:
            call_count[0] += 1
            cat = people_category if "Person" in kwargs.get("body", "") else orgs_category
            return Discussion(
                id=f"D_{call_count[0]}",
                number=call_count[0],
                title=kwargs["title"],
                body=kwargs["body"],
                url=f"https://github.com/test/repo/discussions/{call_count[0]}",
                category_id=cat.id,
                category_name=cat.name,
            )
        
        mock_discussions.create_discussion.side_effect = create_disc
        
        report_path = tmp_path / "report.json"
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "all"
        args.entity_name = None
        args.dry_run = False
        args.output = str(report_path)
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        assert report_path.exists()
        
        with open(report_path) as f:
            report = json.load(f)
        
        assert report["repository"] == "test/repo"
        assert len(report["results"]) == 3  # 2 people + 1 org
        assert report["summary"]["created"] == 3
        assert report["summary"]["updated"] == 0
        assert report["summary"]["unchanged"] == 0

    @patch("src.cli.commands.discussions.github_discussions")
    def test_report_tracks_mixed_operations(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        tmp_path: Path,
    ) -> None:
        """Report should track creates, updates, and unchanged correctly."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        people_category = DiscussionCategory(
            id="DIC_people", name="People", slug="people", description=""
        )
        orgs_category = DiscussionCategory(
            id="DIC_orgs", name="Organizations", slug="organizations", description=""
        )
        
        def get_category(token: str, repository: str, category_name: str) -> DiscussionCategory | None:
            if category_name == "People":
                return people_category
            elif category_name == "Organizations":
                return orgs_category
            return None
        
        mock_discussions.get_category_by_name.side_effect = get_category
        
        # Build expected body for Person Two (will be unchanged)
        storage = KnowledgeGraphStorage(root=knowledge_graph_dir)
        aggregator = KnowledgeAggregator(storage=storage)
        from src.knowledge.aggregation import build_entity_discussion_content
        
        person_two = aggregator.get_aggregated_entity("Person Two", "Person")
        person_two_body = build_entity_discussion_content(person_two) if person_two else ""
        
        # Person One: doesn't exist (create)
        # Person Two: exists with same content (unchanged)
        # Org One: exists with different content (update)
        def find_disc(token: str, repository: str, title: str, category_id: str) -> Discussion | None:
            if title == "Person Two":
                return Discussion(
                    id="D_existing1",
                    number=10,
                    title="Person Two",
                    body=person_two_body,
                    url="https://github.com/test/repo/discussions/10",
                    category_id=people_category.id,
                    category_name=people_category.name,
                )
            elif title == "Org One":
                return Discussion(
                    id="D_existing2",
                    number=20,
                    title="Org One",
                    body="Old org content",
                    url="https://github.com/test/repo/discussions/20",
                    category_id=orgs_category.id,
                    category_name=orgs_category.name,
                )
            return None
        
        mock_discussions.find_discussion_by_title.side_effect = find_disc
        
        mock_discussions.create_discussion.return_value = Discussion(
            id="D_new",
            number=30,
            title="Person One",
            body="...",
            url="https://github.com/test/repo/discussions/30",
            category_id=people_category.id,
            category_name=people_category.name,
        )
        
        report_path = tmp_path / "report.json"
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "all"
        args.entity_name = None
        args.dry_run = False
        args.output = str(report_path)
        
        result = sync_discussions_cli(args)
        
        assert result == 0
        
        with open(report_path) as f:
            report = json.load(f)
        
        assert report["summary"]["created"] == 1  # Person One
        assert report["summary"]["updated"] == 1  # Org One
        assert report["summary"]["unchanged"] == 1  # Person Two


class TestRealKnowledgeGraph:
    """Test with the actual knowledge graph data if it exists."""

    @pytest.mark.skipif(
        not any((Path(__file__).parent.parent.parent / "knowledge-graph" / "people").glob("*.json")),
        reason="Real knowledge graph not available - no people entities"
    )
    def test_list_entities_from_real_graph(self) -> None:
        """Verify we can list entities from the real knowledge graph."""
        storage = KnowledgeGraphStorage()
        aggregator = KnowledgeAggregator(storage=storage)
        
        people = aggregator.list_entities(entity_type="Person")
        
        # Should have at least some entities
        assert len(people) > 0
        
        # Check we can get entity details
        if people:
            entity = aggregator.get_aggregated_entity(people[0], "Person")
            assert entity is not None
            assert entity.name == people[0]
            assert entity.entity_type == "Person"

    @pytest.mark.skipif(
        not any((Path(__file__).parent.parent.parent / "knowledge-graph" / "people").glob("*.json")),
        reason="Real knowledge graph not available - no people entities"
    )
    def test_build_content_for_real_entity(self) -> None:
        """Verify we can build discussion content for real entities."""
        from src.knowledge.aggregation import build_entity_discussion_content
        
        storage = KnowledgeGraphStorage()
        aggregator = KnowledgeAggregator(storage=storage)
        
        people = aggregator.list_entities(entity_type="Person")
        
        if people:
            entity = aggregator.get_aggregated_entity(people[0], "Person")
            assert entity is not None
            
            content = build_entity_discussion_content(entity)
            
            # Content should be non-empty markdown
            assert len(content) > 0
            assert content.startswith("#")
            assert entity.name in content


class TestErrorRecovery:
    """Test error handling and recovery scenarios."""

    @pytest.fixture
    def knowledge_graph_dir(self, tmp_path: Path) -> Path:
        """Create a minimal knowledge graph."""
        kg_dir = tmp_path / "knowledge-graph"
        
        (kg_dir / "profiles").mkdir(parents=True)
        (kg_dir / "people").mkdir(parents=True)
        (kg_dir / "organizations").mkdir(parents=True)
        (kg_dir / "concepts").mkdir(parents=True)
        (kg_dir / "associations").mkdir(parents=True)
        
        profile_data = {
            "source_checksum": "err123",
            "profiles": [
                {
                    "name": "Test Entity",
                    "entity_type": "Person",
                    "summary": "Test",
                    "attributes": {},
                    "mentions": [],
                    "confidence": 0.9
                }
            ],
            "extracted_at": "2025-11-27T00:00:00",
            "metadata": {}
        }
        
        (kg_dir / "profiles" / "err123.json").write_text(
            json.dumps(profile_data, indent=2),
            encoding="utf-8"
        )
        
        return kg_dir

    @patch("src.cli.commands.discussions.github_discussions")
    def test_handles_api_failure_gracefully(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """API failures should be reported but not crash the sync."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        
        people_category = DiscussionCategory(
            id="DIC_people", name="People", slug="people", description=""
        )
        mock_discussions.get_category_by_name.return_value = people_category
        mock_discussions.find_discussion_by_title.return_value = None
        mock_discussions.create_discussion.side_effect = GitHubDiscussionError("API rate limit")
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        # Should return error code
        assert result == 1
        captured = capsys.readouterr()
        assert "API rate limit" in captured.out or "Error" in captured.out

    @patch("src.cli.commands.discussions.github_discussions")
    def test_handles_missing_category(
        self,
        mock_discussions: MagicMock,
        knowledge_graph_dir: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Missing category should be reported clearly."""
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_discussions.get_category_by_name.return_value = None
        
        args = MagicMock()
        args.repository = "test/repo"
        args.token = "token"
        args.knowledge_graph = str(knowledge_graph_dir)
        args.entity_type = "Person"
        args.entity_name = None
        args.dry_run = False
        args.output = None
        
        result = sync_discussions_cli(args)
        
        assert result == 1
        captured = capsys.readouterr()
        assert "Category" in captured.err
        assert "not found" in captured.err
