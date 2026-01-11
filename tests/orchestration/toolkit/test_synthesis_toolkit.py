"""Tests for synthesis toolkit tools."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.orchestration.toolkit.synthesis import (
    _get_alias_map_handler,
    _get_canonical_entity_handler,
    _list_pending_entities_handler,
    _resolve_entity_handler,
    _save_synthesis_batch_handler,
    register_synthesis_tools,
)
from src.orchestration.tools import ToolRegistry


@pytest.fixture
def temp_kb_dir(tmp_path: Path) -> Path:
    """Create temporary knowledge graph directory."""
    kb_dir = tmp_path / "knowledge-graph"
    kb_dir.mkdir()
    
    # Create entity directories
    (kb_dir / "people").mkdir()
    (kb_dir / "organizations").mkdir()
    (kb_dir / "concepts").mkdir()
    (kb_dir / "canonical").mkdir()
    (kb_dir / "canonical" / "people").mkdir()
    (kb_dir / "canonical" / "organizations").mkdir()
    (kb_dir / "canonical" / "concepts").mkdir()
    
    # Create some extracted entities
    (kb_dir / "organizations" / "abc123.json").write_text(
        json.dumps(["Denver Broncos", "Kansas City Chiefs"])
    )
    (kb_dir / "organizations" / "def456.json").write_text(
        json.dumps(["Broncos", "AFC West"])
    )
    
    # Create alias map
    alias_map = {
        "version": 1,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "by_type": {
            "Organization": {
                "denver broncos": "denver-broncos",
            }
        }
    }
    (kb_dir / "canonical" / "alias-map.json").write_text(json.dumps(alias_map, indent=2))
    
    # Create existing canonical entity
    entity = {
        "canonical_id": "denver-broncos",
        "canonical_name": "Denver Broncos",
        "entity_type": "Organization",
        "aliases": ["Denver Broncos"],
        "source_checksums": ["abc123"],
        "corroboration_score": 1,
        "first_seen": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "resolution_history": [],
        "attributes": {},
        "associations": [],
        "metadata": {}
    }
    (kb_dir / "canonical" / "organizations" / "denver-broncos.json").write_text(
        json.dumps(entity, indent=2)
    )
    
    return kb_dir


def test_register_synthesis_tools():
    """Test synthesis tools registration."""
    registry = ToolRegistry()
    register_synthesis_tools(registry)
    
    expected_tools = [
        "list_pending_entities",
        "get_canonical_entity",
        "get_alias_map",
        "resolve_entity",
        "save_synthesis_batch",
    ]
    
    for tool_name in expected_tools:
        assert tool_name in [t.name for t in registry]


def test_list_pending_entities(temp_kb_dir: Path):
    """Test listing pending entities."""
    with patch("src.orchestration.toolkit.synthesis.get_knowledge_graph_root", return_value=temp_kb_dir):
        result = _list_pending_entities_handler({"entity_type": "Organization", "limit": 10})
        
        assert result.success
        assert result.output is not None
        assert result.output["entity_type"] == "Organization"
        
        # Should find entities not in alias map
        pending_names = [e["raw_name"] for e in result.output["pending_entities"]]
        assert "Kansas City Chiefs" in pending_names  # Not in alias map
        assert "Broncos" in pending_names  # Not in alias map
        assert "AFC West" in pending_names  # Not in alias map
        # "Denver Broncos" already in alias map, should not be pending


def test_get_canonical_entity(temp_kb_dir: Path):
    """Test retrieving canonical entity."""
    with patch("src.orchestration.toolkit.synthesis.get_knowledge_graph_root", return_value=temp_kb_dir):
        result = _get_canonical_entity_handler({
            "entity_type": "Organization",
            "canonical_id": "denver-broncos"
        })
        
        assert result.success
        assert result.output is not None
        assert result.output["canonical_id"] == "denver-broncos"
        assert result.output["canonical_name"] == "Denver Broncos"


def test_get_canonical_entity_not_found(temp_kb_dir: Path):
    """Test retrieving non-existent canonical entity."""
    with patch("src.orchestration.toolkit.synthesis.get_knowledge_graph_root", return_value=temp_kb_dir):
        result = _get_canonical_entity_handler({
            "entity_type": "Organization",
            "canonical_id": "nonexistent"
        })
        
        assert not result.success
        assert "not found" in result.error.lower()


def test_get_alias_map(temp_kb_dir: Path):
    """Test retrieving alias map."""
    with patch("src.orchestration.toolkit.synthesis.get_knowledge_graph_root", return_value=temp_kb_dir):
        result = _get_alias_map_handler({})
        
        assert result.success
        assert result.output is not None
        assert "by_type" in result.output
        assert "Organization" in result.output["by_type"]
        assert result.output["by_type"]["Organization"]["denver broncos"] == "denver-broncos"


def test_resolve_entity():
    """Test resolving entity (recording for batch)."""
    # Clear batch state
    import src.orchestration.toolkit.synthesis as synthesis_module
    synthesis_module._batch_pending_changes = []
    
    result = _resolve_entity_handler({
        "raw_name": "Broncos",
        "entity_type": "Organization",
        "source_checksum": "def456",
        "canonical_id": "denver-broncos",
        "is_new": False,
        "reasoning": "Abbreviation of Denver Broncos",
        "confidence": 0.95,
        "needs_review": False,
    })
    
    assert result.success
    assert result.output["pending_changes_count"] == 1


def test_save_synthesis_batch(temp_kb_dir: Path):
    """Test saving synthesis batch."""
    import src.orchestration.toolkit.synthesis as synthesis_module
    
    with patch("src.orchestration.toolkit.synthesis.get_knowledge_graph_root", return_value=temp_kb_dir):
        # Clear and set up batch state
        synthesis_module._batch_pending_changes = []
        synthesis_module._batch_canonical_store = None
        
        # Add some resolutions
        _resolve_entity_handler({
            "raw_name": "Broncos",
            "entity_type": "Organization",
            "source_checksum": "def456",
            "canonical_id": "denver-broncos",
            "is_new": False,
            "reasoning": "Abbreviation",
            "confidence": 0.95,
        })
        
        _resolve_entity_handler({
            "raw_name": "Kansas City Chiefs",
            "entity_type": "Organization",
            "source_checksum": "abc123",
            "canonical_id": "kansas-city-chiefs",
            "is_new": True,
            "reasoning": "New entity",
            "confidence": 0.98,
        })
        
        # Save batch
        result = _save_synthesis_batch_handler({"batch_id": "test-batch-001"})
        
        assert result.success
        assert result.output["entities_created"] == 1
        assert result.output["entities_updated"] == 1
        assert result.output["total_resolutions"] == 2
        
        # Verify files were created
        assert (temp_kb_dir / "canonical" / "organizations" / "kansas-city-chiefs.json").exists()
        
        # Verify alias map was updated
        alias_map_content = json.loads((temp_kb_dir / "canonical" / "alias-map.json").read_text())
        assert "broncos" in alias_map_content["by_type"]["Organization"]
        assert "kansas city chiefs" in alias_map_content["by_type"]["Organization"]
