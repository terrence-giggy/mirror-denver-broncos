"""Tests for GitHub Discussions orchestration tool registrations."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.github.discussions import (
    Discussion,
    DiscussionCategory,
    DiscussionComment,
    GitHubDiscussionError,
)
from src.knowledge.aggregation import AggregatedEntity
from src.knowledge.storage import EntityProfile
from src.orchestration.toolkit.discussion_tools import (
    register_all_discussion_tools,
    register_discussion_mutation_tools,
    register_discussion_read_tools,
    register_discussion_sync_tools,
    register_knowledge_graph_tools,
)
from src.orchestration.tools import ToolRegistry


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def registry() -> ToolRegistry:
    """Create a registry with all discussion tools registered."""
    reg = ToolRegistry()
    register_all_discussion_tools(reg)
    return reg


@pytest.fixture
def read_registry() -> ToolRegistry:
    """Create a registry with only read tools."""
    reg = ToolRegistry()
    register_discussion_read_tools(reg)
    return reg


@pytest.fixture
def mutation_registry() -> ToolRegistry:
    """Create a registry with only mutation tools."""
    reg = ToolRegistry()
    register_discussion_mutation_tools(reg)
    return reg


@pytest.fixture
def knowledge_registry() -> ToolRegistry:
    """Create a registry with only knowledge graph tools."""
    reg = ToolRegistry()
    register_knowledge_graph_tools(reg)
    return reg


@pytest.fixture
def sample_category() -> DiscussionCategory:
    return DiscussionCategory(
        id="DIC_abc123",
        name="People",
        slug="people",
        description="Profiles of people",
    )


@pytest.fixture
def sample_discussion() -> Discussion:
    return Discussion(
        id="D_xyz789",
        number=42,
        title="Niccolo Machiavelli",
        body="# Niccolo Machiavelli\n\n**Type:** Person",
        url="https://github.com/test/repo/discussions/42",
        category_id="DIC_abc123",
        category_name="People",
        author_login="testuser",
        created_at="2025-11-27T10:00:00Z",
        updated_at="2025-11-27T12:00:00Z",
    )


@pytest.fixture
def sample_entity() -> AggregatedEntity:
    return AggregatedEntity(
        name="Niccolo Machiavelli",
        entity_type="Person",
        profiles=[
            EntityProfile(
                name="Niccolo Machiavelli",
                entity_type="Person",
                summary="An influential political philosopher.",
                attributes={"birth_date": "May 3, 1469"},
                confidence=0.95,
            )
        ],
        source_checksums=["abc123"],
    )


# =============================================================================
# Tool Registration Tests
# =============================================================================


class TestToolRegistration:
    def test_all_discussion_tools_registered(self, registry: ToolRegistry) -> None:
        # Read tools
        assert "list_discussion_categories" in registry
        assert "get_category_by_name" in registry
        assert "find_discussion_by_title" in registry
        assert "get_discussion" in registry
        assert "list_discussions" in registry

        # Mutation tools
        assert "create_discussion" in registry
        assert "update_discussion" in registry
        assert "add_discussion_comment" in registry

        # Knowledge graph tools
        assert "list_knowledge_entities" in registry
        assert "get_entity_profile" in registry
        assert "build_entity_discussion_body" in registry

        # Sync tools
        assert "sync_entity_discussion" in registry

    def test_read_tools_are_safe(self, registry: ToolRegistry) -> None:
        safe_tools = [
            "list_discussion_categories",
            "get_category_by_name",
            "find_discussion_by_title",
            "get_discussion",
            "list_discussions",
            "list_knowledge_entities",
            "get_entity_profile",
            "build_entity_discussion_body",
        ]
        from src.orchestration.safety import ActionRisk
        for tool_name in safe_tools:
            tool = registry.get_tool(tool_name)
            assert tool.risk_level == ActionRisk.SAFE, f"{tool_name} should be SAFE"

    def test_mutation_tools_require_review(self, registry: ToolRegistry) -> None:
        review_tools = [
            "create_discussion",
            "update_discussion",
            "add_discussion_comment",
            "sync_entity_discussion",
        ]
        from src.orchestration.safety import ActionRisk
        for tool_name in review_tools:
            tool = registry.get_tool(tool_name)
            assert tool.risk_level == ActionRisk.REVIEW, f"{tool_name} should be REVIEW"


# =============================================================================
# Read Tool Handler Tests
# =============================================================================


class TestListDiscussionCategories:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_success(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
        sample_category: DiscussionCategory,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.list_discussion_categories.return_value = [sample_category]

        result = read_registry.execute_tool("list_discussion_categories", {})

        assert result.success
        assert result.output["count"] == 1
        assert result.output["categories"][0]["name"] == "People"

    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_handles_api_error(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.GitHubDiscussionError = GitHubDiscussionError
        mock_discussions.list_discussion_categories.side_effect = GitHubDiscussionError("API error")

        result = read_registry.execute_tool("list_discussion_categories", {})

        assert not result.success
        assert "API error" in result.error


class TestGetCategoryByName:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_found(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
        sample_category: DiscussionCategory,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.get_category_by_name.return_value = sample_category

        result = read_registry.execute_tool(
            "get_category_by_name",
            {"category_name": "People"},
        )

        assert result.success
        assert result.output["found"] is True
        assert result.output["category"]["id"] == "DIC_abc123"

    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_not_found(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.get_category_by_name.return_value = None

        result = read_registry.execute_tool(
            "get_category_by_name",
            {"category_name": "NonExistent"},
        )

        assert result.success
        assert result.output["found"] is False

    def test_validates_category_name(self, read_registry: ToolRegistry) -> None:
        result = read_registry.execute_tool(
            "get_category_by_name",
            {"category_name": ""},
        )
        assert not result.success
        # Error message can vary between "is too short" and "should be non-empty"
        assert ("is too short" in result.error or "should be non-empty" in result.error)


class TestFindDiscussionByTitle:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_found(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
        sample_discussion: Discussion,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.find_discussion_by_title.return_value = sample_discussion

        result = read_registry.execute_tool(
            "find_discussion_by_title",
            {"title": "Niccolo Machiavelli"},
        )

        assert result.success
        assert result.output["found"] is True
        assert result.output["discussion"]["number"] == 42

    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_not_found(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.find_discussion_by_title.return_value = None

        result = read_registry.execute_tool(
            "find_discussion_by_title",
            {"title": "Unknown Person"},
        )

        assert result.success
        assert result.output["found"] is False


class TestGetDiscussion:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_success(
        self,
        mock_discussions: MagicMock,
        read_registry: ToolRegistry,
        sample_discussion: Discussion,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.get_discussion.return_value = sample_discussion

        result = read_registry.execute_tool(
            "get_discussion",
            {"discussion_number": 42},
        )

        assert result.success
        assert result.output["id"] == "D_xyz789"
        assert result.output["title"] == "Niccolo Machiavelli"

    def test_validates_discussion_number(self, read_registry: ToolRegistry) -> None:
        result = read_registry.execute_tool(
            "get_discussion",
            {"discussion_number": 0},
        )
        assert not result.success


# =============================================================================
# Mutation Tool Handler Tests
# =============================================================================


class TestCreateDiscussion:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_success(
        self,
        mock_discussions: MagicMock,
        mutation_registry: ToolRegistry,
        sample_discussion: Discussion,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.create_discussion.return_value = sample_discussion

        result = mutation_registry.execute_tool(
            "create_discussion",
            {
                "category_id": "DIC_abc123",
                "title": "Niccolo Machiavelli",
                "body": "Profile content",
            },
        )

        assert result.success
        assert result.output["created"] is True
        assert result.output["number"] == 42

    def test_validates_required_fields(self, mutation_registry: ToolRegistry) -> None:
        result = mutation_registry.execute_tool(
            "create_discussion",
            {"category_id": "", "title": "Test", "body": "Body"},
        )
        assert not result.success
        assert "category_id" in result.error


class TestUpdateDiscussion:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_success(
        self,
        mock_discussions: MagicMock,
        mutation_registry: ToolRegistry,
        sample_discussion: Discussion,
    ) -> None:
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.update_discussion.return_value = sample_discussion

        result = mutation_registry.execute_tool(
            "update_discussion",
            {"discussion_id": "D_xyz789", "body": "Updated content"},
        )

        assert result.success
        assert result.output["updated"] is True

    def test_requires_title_or_body(self, mutation_registry: ToolRegistry) -> None:
        result = mutation_registry.execute_tool(
            "update_discussion",
            {"discussion_id": "D_xyz789"},
        )
        assert not result.success
        assert "At least one" in result.error


class TestAddDiscussionComment:
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_success(
        self,
        mock_discussions: MagicMock,
        mutation_registry: ToolRegistry,
    ) -> None:
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.add_discussion_comment.return_value = DiscussionComment(
            id="DC_123",
            body="Changelog",
            url="https://github.com/test/repo/discussions/42#comment-123",
        )

        result = mutation_registry.execute_tool(
            "add_discussion_comment",
            {"discussion_id": "D_xyz789", "body": "Changelog entry"},
        )

        assert result.success
        assert result.output["created"] is True


# =============================================================================
# Knowledge Graph Tool Handler Tests
# =============================================================================


class TestListKnowledgeEntities:
    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    def test_success(
        self,
        mock_get_aggregator: MagicMock,
        knowledge_registry: ToolRegistry,
    ) -> None:
        mock_aggregator = MagicMock()
        mock_aggregator.list_entities.return_value = ["Niccolo Machiavelli", "Francesco Sforza"]
        mock_get_aggregator.return_value = mock_aggregator

        result = knowledge_registry.execute_tool(
            "list_knowledge_entities",
            {"entity_type": "Person"},
        )

        assert result.success
        assert result.output["count"] == 2
        assert "Niccolo Machiavelli" in result.output["entities"]


class TestGetEntityProfile:
    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    def test_found(
        self,
        mock_get_aggregator: MagicMock,
        knowledge_registry: ToolRegistry,
        sample_entity: AggregatedEntity,
    ) -> None:
        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = sample_entity
        mock_get_aggregator.return_value = mock_aggregator

        result = knowledge_registry.execute_tool(
            "get_entity_profile",
            {"name": "Niccolo Machiavelli"},
        )

        assert result.success
        assert result.output["found"] is True
        assert result.output["entity"]["name"] == "Niccolo Machiavelli"

    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    def test_not_found(
        self,
        mock_get_aggregator: MagicMock,
        knowledge_registry: ToolRegistry,
    ) -> None:
        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = None
        mock_get_aggregator.return_value = mock_aggregator

        result = knowledge_registry.execute_tool(
            "get_entity_profile",
            {"name": "Unknown Person"},
        )

        assert result.success
        assert result.output["found"] is False


class TestBuildEntityDiscussionBody:
    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    def test_success(
        self,
        mock_get_aggregator: MagicMock,
        knowledge_registry: ToolRegistry,
        sample_entity: AggregatedEntity,
    ) -> None:
        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = sample_entity
        mock_get_aggregator.return_value = mock_aggregator

        result = knowledge_registry.execute_tool(
            "build_entity_discussion_body",
            {"name": "Niccolo Machiavelli"},
        )

        assert result.success
        assert "# Niccolo Machiavelli" in result.output["body"]
        assert result.output["body_length"] > 0

    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    def test_entity_not_found(
        self,
        mock_get_aggregator: MagicMock,
        knowledge_registry: ToolRegistry,
    ) -> None:
        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = None
        mock_get_aggregator.return_value = mock_aggregator

        result = knowledge_registry.execute_tool(
            "build_entity_discussion_body",
            {"name": "Unknown"},
        )

        assert not result.success
        assert "not found" in result.error


# =============================================================================
# Sync Tool Handler Tests
# =============================================================================


class TestSyncEntityDiscussion:
    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_creates_new_discussion(
        self,
        mock_discussions: MagicMock,
        mock_get_aggregator: MagicMock,
        registry: ToolRegistry,
        sample_category: DiscussionCategory,
        sample_discussion: Discussion,
        sample_entity: AggregatedEntity,
    ) -> None:
        # Setup mocks
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.get_category_by_name.return_value = sample_category
        mock_discussions.find_discussion_by_title.return_value = None  # No existing discussion
        mock_discussions.create_discussion.return_value = sample_discussion

        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = sample_entity
        mock_get_aggregator.return_value = mock_aggregator

        result = registry.execute_tool(
            "sync_entity_discussion",
            {"entity_name": "Niccolo Machiavelli", "entity_type": "Person"},
        )

        assert result.success
        assert result.output["action"] == "created"
        assert result.output["discussion_number"] == 42
        mock_discussions.create_discussion.assert_called_once()

    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_updates_existing_discussion(
        self,
        mock_discussions: MagicMock,
        mock_get_aggregator: MagicMock,
        registry: ToolRegistry,
        sample_category: DiscussionCategory,
        sample_entity: AggregatedEntity,
    ) -> None:
        # Setup mocks
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.get_category_by_name.return_value = sample_category

        existing_discussion = Discussion(
            id="D_xyz789",
            number=42,
            title="Niccolo Machiavelli",
            body="Old content",  # Different from generated content
            url="https://github.com/test/repo/discussions/42",
        )
        mock_discussions.find_discussion_by_title.return_value = existing_discussion

        updated_discussion = Discussion(
            id="D_xyz789",
            number=42,
            title="Niccolo Machiavelli",
            body="New content",
            url="https://github.com/test/repo/discussions/42",
        )
        mock_discussions.update_discussion.return_value = updated_discussion
        mock_discussions.add_discussion_comment.return_value = DiscussionComment(
            id="DC_1", body="Updated", url="http://x"
        )

        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = sample_entity
        mock_get_aggregator.return_value = mock_aggregator

        result = registry.execute_tool(
            "sync_entity_discussion",
            {"entity_name": "Niccolo Machiavelli", "entity_type": "Person"},
        )

        assert result.success
        assert result.output["action"] == "updated"
        mock_discussions.update_discussion.assert_called_once()
        mock_discussions.add_discussion_comment.assert_called_once()

    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    def test_skips_unchanged_discussion(
        self,
        mock_discussions: MagicMock,
        mock_get_aggregator: MagicMock,
        registry: ToolRegistry,
        sample_category: DiscussionCategory,
        sample_entity: AggregatedEntity,
    ) -> None:
        # Setup mocks
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_discussions.get_category_by_name.return_value = sample_category

        # Generate what the body would be
        from src.knowledge.aggregation import build_entity_discussion_content
        expected_body = build_entity_discussion_content(sample_entity)

        existing_discussion = Discussion(
            id="D_xyz789",
            number=42,
            title="Niccolo Machiavelli",
            body=expected_body,  # Same content
            url="https://github.com/test/repo/discussions/42",
        )
        mock_discussions.find_discussion_by_title.return_value = existing_discussion

        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = sample_entity
        mock_get_aggregator.return_value = mock_aggregator

        result = registry.execute_tool(
            "sync_entity_discussion",
            {"entity_name": "Niccolo Machiavelli", "entity_type": "Person"},
        )

        assert result.success
        assert result.output["action"] == "unchanged"
        mock_discussions.update_discussion.assert_not_called()

    @patch("src.orchestration.toolkit.discussion_tools.github_discussions")
    @patch("src.orchestration.toolkit.discussion_tools._get_aggregator")
    def test_entity_not_in_knowledge_graph(
        self,
        mock_get_aggregator: MagicMock,
        mock_discussions: MagicMock,
        registry: ToolRegistry,
    ) -> None:
        mock_discussions.resolve_repository.return_value = "test/repo"
        mock_discussions.resolve_token.return_value = "token"
        mock_aggregator = MagicMock()
        mock_aggregator.get_aggregated_entity.return_value = None
        mock_get_aggregator.return_value = mock_aggregator

        result = registry.execute_tool(
            "sync_entity_discussion",
            {"entity_name": "Unknown Person", "entity_type": "Person"},
        )

        assert not result.success
        assert "not found" in result.error

    def test_validates_entity_type(self, registry: ToolRegistry) -> None:
        result = registry.execute_tool(
            "sync_entity_discussion",
            {"entity_name": "Test", "entity_type": "InvalidType"},
        )
        assert not result.success
        assert "Person" in result.error or "Organization" in result.error
