"""Tests for synthesis CLI commands."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from src.cli.commands.synthesis import (
    _gather_all_entities,
    _gather_unresolved_entities,
    _generate_issue_body,
    _list_all_checksums,
)
from src.knowledge.canonical import CanonicalStorage
from src.knowledge.storage import KnowledgeGraphStorage


class TestListAllChecksums:
    """Tests for _list_all_checksums helper."""
    
    @pytest.fixture
    def temp_kg_storage(self, tmp_path: Path) -> KnowledgeGraphStorage:
        """Create temporary knowledge graph storage."""
        return KnowledgeGraphStorage(root=tmp_path / "kg")
    
    def test_empty_storage(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test with no extracted entities."""
        checksums = _list_all_checksums(temp_kg_storage, entity_type=None)
        assert checksums == []
    
    def test_multiple_checksums(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test with multiple source documents (no entity type filter)."""
        temp_kg_storage.save_extracted_people("abc123", ["Person A"])
        temp_kg_storage.save_extracted_organizations("abc123", ["Org A"])
        temp_kg_storage.save_extracted_people("def456", ["Person B"])
        
        checksums = _list_all_checksums(temp_kg_storage, entity_type=None)
        assert set(checksums) == {"abc123", "def456"}
        assert checksums == sorted(checksums)  # Should be sorted
    
    def test_filter_by_person(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test filtering checksums by Person entity type."""
        temp_kg_storage.save_extracted_people("abc123", ["Person A"])
        temp_kg_storage.save_extracted_organizations("def456", ["Org A"])
        temp_kg_storage.save_extracted_people("ghi789", ["Person B"])
        
        checksums = _list_all_checksums(temp_kg_storage, entity_type="Person")
        assert set(checksums) == {"abc123", "ghi789"}
    
    def test_filter_by_organization(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test filtering checksums by Organization entity type."""
        temp_kg_storage.save_extracted_people("abc123", ["Person A"])
        temp_kg_storage.save_extracted_organizations("def456", ["Org A"])
        temp_kg_storage.save_extracted_organizations("ghi789", ["Org B"])
        
        checksums = _list_all_checksums(temp_kg_storage, entity_type="Organization")
        assert set(checksums) == {"def456", "ghi789"}
    
    def test_filter_by_concept(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test filtering checksums by Concept entity type."""
        temp_kg_storage.save_extracted_people("abc123", ["Person A"])
        temp_kg_storage.save_extracted_concepts("def456", ["Concept A"])
        
        checksums = _list_all_checksums(temp_kg_storage, entity_type="Concept")
        assert set(checksums) == {"def456"}


class TestGatherUnresolvedEntities:
    """Tests for _gather_unresolved_entities helper."""
    
    @pytest.fixture
    def temp_storages(self, tmp_path: Path) -> tuple[KnowledgeGraphStorage, CanonicalStorage]:
        """Create temporary storages."""
        kg_storage = KnowledgeGraphStorage(root=tmp_path / "kg")
        canonical_storage = CanonicalStorage(root=tmp_path / "canonical")
        return kg_storage, canonical_storage
    
    def test_no_entities(self, temp_storages):
        """Test with no extracted entities."""
        kg_storage, canonical_storage = temp_storages
        
        unresolved = _gather_unresolved_entities("Person", kg_storage, canonical_storage)
        assert unresolved == []
    
    def test_all_unresolved(self, temp_storages):
        """Test with all entities unresolved."""
        kg_storage, canonical_storage = temp_storages
        
        kg_storage.save_extracted_people("abc123", ["Sean Payton", "Courtland Sutton"])
        kg_storage.save_extracted_people("def456", ["Sean Payton"])
        
        unresolved = _gather_unresolved_entities("Person", kg_storage, canonical_storage)
        
        # Should have 3 entries (Sean Payton appears twice)
        assert len(unresolved) == 3
        names = [name for name, _ in unresolved]
        assert "Sean Payton" in names
        assert "Courtland Sutton" in names
    
    def test_some_resolved(self, temp_storages):
        """Test with some entities already resolved."""
        kg_storage, canonical_storage = temp_storages
        
        kg_storage.save_extracted_people("abc123", ["Sean Payton", "Courtland Sutton"])
        kg_storage.save_extracted_people("def456", ["Sean Payton"])
        
        # Resolve "Sean Payton" in alias map
        canonical_storage.add_alias("sean-payton", "Sean Payton", "Person")
        
        unresolved = _gather_unresolved_entities("Person", kg_storage, canonical_storage)
        
        # Should only have "Courtland Sutton" (unresolved)
        assert len(unresolved) == 1
        assert unresolved[0][0] == "Courtland Sutton"
    
    def test_organizations(self, temp_storages):
        """Test with organizations."""
        kg_storage, canonical_storage = temp_storages
        
        kg_storage.save_extracted_organizations("abc123", ["Denver Broncos", "Broncos"])
        
        unresolved = _gather_unresolved_entities("Organization", kg_storage, canonical_storage)
        
        assert len(unresolved) == 2
        names = [name for name, _ in unresolved]
        assert "Denver Broncos" in names
        assert "Broncos" in names
    
    def test_concepts(self, temp_storages):
        """Test with concepts."""
        kg_storage, canonical_storage = temp_storages
        
        kg_storage.save_extracted_concepts("abc123", ["Home-field advantage"])
        
        unresolved = _gather_unresolved_entities("Concept", kg_storage, canonical_storage)
        
        assert len(unresolved) == 1
        assert unresolved[0][0] == "Home-field advantage"


class TestGatherAllEntities:
    """Tests for _gather_all_entities helper."""
    
    @pytest.fixture
    def temp_kg_storage(self, tmp_path: Path) -> KnowledgeGraphStorage:
        """Create temporary knowledge graph storage."""
        return KnowledgeGraphStorage(root=tmp_path / "kg")
    
    def test_gather_all_people(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test gathering all people entities."""
        temp_kg_storage.save_extracted_people("abc123", ["Sean Payton", "Courtland Sutton"])
        temp_kg_storage.save_extracted_people("def456", ["Sean Payton"])
        
        all_entities = _gather_all_entities("Person", temp_kg_storage)
        
        # Should have 3 entries (including duplicate)
        assert len(all_entities) == 3
        names = [name for name, _ in all_entities]
        assert names.count("Sean Payton") == 2
        assert names.count("Courtland Sutton") == 1
    
    def test_gather_all_organizations(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test gathering all organization entities."""
        temp_kg_storage.save_extracted_organizations("abc123", ["Denver Broncos"])
        
        all_entities = _gather_all_entities("Organization", temp_kg_storage)
        
        assert len(all_entities) == 1
        assert all_entities[0][0] == "Denver Broncos"


class TestGenerateIssueBody:
    """Tests for _generate_issue_body helper."""
    
    def test_basic_generation(self):
        """Test basic issue body generation."""
        entities = [
            ("Sean Payton", "abc123"),
            ("Courtland Sutton", "def456"),
        ]
        
        body = _generate_issue_body("Person", entities, batch_number=1)
        
        # Check basic structure
        assert "## Task: Entity Resolution" in body
        assert "## Entities to Process" in body
        assert "## Current Canonical Store" in body
        assert "## Resolution Rules" in body
        assert "## Output Format" in body
        
        # Check entity table
        assert "Sean Payton" in body
        assert "Courtland Sutton" in body
        assert "knowledge-graph/persons/abc123.json" in body
        assert "knowledge-graph/persons/def456.json" in body
        
        # Check markers
        assert "<!-- copilot:synthesis-batch -->" in body
        assert "<!-- batch:1 -->" in body
        assert "<!-- entity-type:Person -->" in body
    
    def test_organization_type(self):
        """Test with organization entity type."""
        entities = [("Denver Broncos", "abc123")]
        
        body = _generate_issue_body("Organization", entities, batch_number=2)
        
        assert "organization" in body.lower()
        assert "knowledge-graph/organizations/abc123.json" in body
        assert "<!-- entity-type:Organization -->" in body
        assert "<!-- batch:2 -->" in body
    
    def test_concept_type(self):
        """Test with concept entity type."""
        entities = [("Home-field advantage", "xyz789")]
        
        body = _generate_issue_body("Concept", entities, batch_number=3)
        
        assert "concept" in body.lower()
        assert "knowledge-graph/concepts/xyz789.json" in body
        assert "<!-- entity-type:Concept -->" in body
    
    def test_multiple_entities(self):
        """Test with many entities."""
        entities = [(f"Entity {i}", f"checksum{i}") for i in range(10)]
        
        body = _generate_issue_body("Person", entities, batch_number=1)
        
        # Should have all 10 entities in table
        for i in range(10):
            assert f"Entity {i}" in body
            assert f"checksum{i}" in body


class TestSynthesisCLI:
    """Integration tests for synthesis CLI commands."""
    
    @pytest.fixture
    def temp_storages(self, tmp_path: Path) -> tuple[KnowledgeGraphStorage, CanonicalStorage]:
        """Create temporary storages."""
        kg_storage = KnowledgeGraphStorage(root=tmp_path / "kg")
        canonical_storage = CanonicalStorage(root=tmp_path / "canonical")
        return kg_storage, canonical_storage
    
    def test_pending_cli_no_entities(self, temp_storages, capsys):
        """Test pending command with no entities."""
        from src.cli.commands.synthesis import pending_cli
        import argparse
        
        kg_storage, _ = temp_storages
        
        args = argparse.Namespace(entity_type="all")
        
        with mock.patch("src.cli.commands.synthesis.get_knowledge_graph_root", return_value=kg_storage.root):
            result = pending_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "All entities resolved" in captured.out
    
    def test_pending_cli_with_entities(self, temp_storages, capsys):
        """Test pending command with unresolved entities."""
        from src.cli.commands.synthesis import pending_cli
        import argparse
        
        kg_storage, _ = temp_storages
        
        # Add some unresolved entities
        kg_storage.save_extracted_people("abc123", ["Sean Payton", "Courtland Sutton"])
        
        args = argparse.Namespace(entity_type="Person")
        
        # Need to mock the path to return the correct root
        with mock.patch("src.cli.commands.synthesis.get_knowledge_graph_root", return_value=kg_storage.root):
            result = pending_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "Person (2 pending)" in captured.out
        assert "Sean Payton" in captured.out
        assert "Courtland Sutton" in captured.out

class TestRunBatchCLI:
    """Tests for run_batch_cli command to verify correct API usage."""
    
    @mock.patch("src.orchestration.missions.load_mission")
    @mock.patch("src.integrations.github.models.GitHubModelsClient")
    @mock.patch("src.orchestration.agent.AgentRuntime")
    def test_agent_runtime_receives_correct_parameters(self, mock_runtime_class, mock_client_class, mock_load_mission):
        """Verify AgentRuntime is initialized with planner, tools, safety, evaluator (not mission)."""
        from src.cli.commands.synthesis import run_batch_cli
        import argparse
        from src.orchestration.types import MissionStatus
        from src.knowledge.storage import KnowledgeGraphStorage
        from src.paths import get_knowledge_graph_root
        
        # Previous bug: AgentRuntime(mission=mission, planner=planner)
        # Correct API: AgentRuntime(planner=planner, tools=registry, safety=validator, evaluator=evaluator)
        
        # Add test entities so CLI doesn't exit early
        kg_root = get_knowledge_graph_root()
        kg_storage = KnowledgeGraphStorage(root=kg_root)
        kg_storage.save_extracted_organizations("test123", ["Test Org"])
        
        args = argparse.Namespace(
            entity_type="Organization",
            batch_size=10,
            branch_name="test-branch",
            model="gpt-4o",
            repository="owner/repo",
            token="test_token",
        )
        
        # Setup mocks
        mock_mission = mock.Mock()
        mock_mission.inputs = {}
        mock_load_mission.return_value = mock_mission
        
        mock_runtime = mock.Mock()
        mock_outcome = mock.Mock()
        mock_outcome.status = MissionStatus.SUCCEEDED
        mock_outcome.steps = []
        mock_outcome.summary = None
        mock_runtime.execute_mission.return_value = mock_outcome
        mock_runtime_class.return_value = mock_runtime
        
        # Run
        with mock.patch("src.cli.commands.synthesis.resolve_repository", return_value="owner/repo"):
            with mock.patch("src.cli.commands.synthesis.resolve_token", return_value="test_token"):
                result = run_batch_cli(args)
        
        # Verify AgentRuntime constructor was called
        assert mock_runtime_class.called
        call_kwargs = mock_runtime_class.call_args.kwargs
        
        # Verify required parameters are present
        assert "planner" in call_kwargs, "AgentRuntime must receive planner parameter"
        assert "tools" in call_kwargs, "AgentRuntime must receive tools parameter"
        assert "safety" in call_kwargs, "AgentRuntime must receive safety parameter"
        assert "evaluator" in call_kwargs, "AgentRuntime must receive evaluator parameter"
        
        # Verify mission is NOT passed to constructor (it goes to execute_mission instead)
        assert "mission" not in call_kwargs, "mission should NOT be in AgentRuntime constructor"
        
        # Verify execute_mission was called with mission
        assert mock_runtime.execute_mission.called
        exec_args = mock_runtime.execute_mission.call_args.args
        assert len(exec_args) == 2, "execute_mission should receive (mission, context)"
        assert result == 0
    
    @mock.patch("src.orchestration.missions.load_mission")
    @mock.patch("src.orchestration.agent.AgentRuntime")
    def test_github_models_client_uses_api_key_parameter(self, mock_runtime_class, mock_load_mission):
        """Verify GitHubModelsClient is initialized with api_key= not token=."""
        from src.cli.commands.synthesis import run_batch_cli
        import argparse
        from src.orchestration.types import MissionStatus
        from src.knowledge.storage import KnowledgeGraphStorage
        from src.paths import get_knowledge_graph_root
        
        # Previous bug: GitHubModelsClient(token=token, ...)
        # Correct API: GitHubModelsClient(api_key=token, ...)
        
        # Add test entities so CLI doesn't exit early
        kg_root = get_knowledge_graph_root()
        kg_storage = KnowledgeGraphStorage(root=kg_root)
        kg_storage.save_extracted_organizations("test456", ["Test Org 2"])
        
        args = argparse.Namespace(
            entity_type="Organization",
            batch_size=10,
            branch_name="test-branch",
            model="gpt-4o",
            repository="owner/repo",
            token="test_token",
        )
        
        # Capture kwargs passed to GitHubModelsClient
        captured_kwargs = {}
        
        class MockGitHubModelsClient:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)
                self.model = kwargs.get("model", "gpt-4o")
        
        # Setup other mocks
        mock_mission = mock.Mock()
        mock_mission.inputs = {}
        mock_load_mission.return_value = mock_mission
        
        mock_runtime = mock.Mock()
        mock_outcome = mock.Mock()
        mock_outcome.status = MissionStatus.SUCCEEDED
        mock_outcome.steps = []
        mock_outcome.summary = None
        mock_runtime.execute_mission.return_value = mock_outcome
        mock_runtime_class.return_value = mock_runtime
        
        # Run with our custom mock
        with mock.patch("src.cli.commands.synthesis.resolve_repository", return_value="owner/repo"):
            with mock.patch("src.cli.commands.synthesis.resolve_token", return_value="test_token"):
                with mock.patch("src.integrations.github.models.GitHubModelsClient", MockGitHubModelsClient):
                    result = run_batch_cli(args)
        
        # Verify api_key parameter was used (not token)
        assert "api_key" in captured_kwargs, "GitHubModelsClient must be initialized with api_key parameter"
        assert captured_kwargs["api_key"] == "test_token"
        assert "token" not in captured_kwargs, "token parameter should NOT be used"
        assert result == 0
    
    @mock.patch("src.orchestration.missions.load_mission")
    @mock.patch("src.orchestration.agent.AgentRuntime")
    def test_execution_context_receives_inputs_not_mission(self, mock_runtime_class, mock_load_mission):
        """Verify inputs are passed via ExecutionContext, not by modifying frozen mission."""
        from src.cli.commands.synthesis import run_batch_cli
        import argparse
        from src.orchestration.types import MissionStatus
        from src.knowledge.storage import KnowledgeGraphStorage
        from src.paths import get_knowledge_graph_root
        
        # Mission is a frozen dataclass - we can't modify mission.inputs
        # Inputs should be passed via ExecutionContext(inputs={...})
        
        # Add test entities so CLI doesn't exit early
        kg_root = get_knowledge_graph_root()
        kg_storage = KnowledgeGraphStorage(root=kg_root)
        kg_storage.save_extracted_organizations("test789", ["Test Org 3"])
        
        args = argparse.Namespace(
            entity_type="Organization",
            batch_size=10,
            branch_name="test-branch",
            model="gpt-4o",
            repository="owner/repo",
            token="test_token",
        )
        
        # Setup mocks
        mock_mission = mock.Mock()
        mock_load_mission.return_value = mock_mission
        
        mock_runtime = mock.Mock()
        mock_outcome = mock.Mock()
        mock_outcome.status = MissionStatus.SUCCEEDED
        mock_outcome.steps = []
        mock_outcome.summary = None
        mock_runtime.execute_mission.return_value = mock_outcome
        mock_runtime_class.return_value = mock_runtime
        
        # Run
        with mock.patch("src.cli.commands.synthesis.resolve_repository", return_value="owner/repo"):
            with mock.patch("src.cli.commands.synthesis.resolve_token", return_value="test_token"):
                with mock.patch("src.integrations.github.models.GitHubModelsClient"):
                    result = run_batch_cli(args)
        
        # Verify execute_mission was called with mission and context
        assert mock_runtime.execute_mission.called
        call_args = mock_runtime.execute_mission.call_args.args
        assert len(call_args) == 2, "execute_mission should receive (mission, context)"
        
        mission_arg, context_arg = call_args
        
        # Verify the context has the inputs
        assert hasattr(context_arg, 'inputs'), "context should have inputs attribute"
        assert context_arg.inputs is not None, "context.inputs should not be None"
        assert "entity_type" in context_arg.inputs, "context.inputs should contain entity_type"
        assert context_arg.inputs["entity_type"] == "Organization"
        assert context_arg.inputs["batch_size"] == 10
        assert context_arg.inputs["branch_name"] == "test-branch"
        assert context_arg.inputs["repository"] == "owner/repo"
        
        assert result == 0
