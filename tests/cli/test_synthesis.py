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
        checksums = _list_all_checksums(temp_kg_storage)
        assert checksums == []
    
    def test_multiple_checksums(self, temp_kg_storage: KnowledgeGraphStorage):
        """Test with multiple source documents."""
        temp_kg_storage.save_extracted_people("abc123", ["Person A"])
        temp_kg_storage.save_extracted_organizations("abc123", ["Org A"])
        temp_kg_storage.save_extracted_people("def456", ["Person B"])
        
        checksums = _list_all_checksums(temp_kg_storage)
        assert set(checksums) == {"abc123", "def456"}
        assert checksums == sorted(checksums)  # Should be sorted


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
