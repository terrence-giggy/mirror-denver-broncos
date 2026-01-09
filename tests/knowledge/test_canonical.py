"""Tests for canonical entity storage."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.knowledge.canonical import (
    AliasMap,
    CanonicalAssociation,
    CanonicalEntity,
    CanonicalStorage,
    ResolutionEvent,
    create_canonical_id,
    normalize_name,
)


class TestNormalizeName:
    """Tests for name normalization."""
    
    def test_lowercase(self):
        assert normalize_name("Sean Payton") == "sean payton"
    
    def test_strip_whitespace(self):
        assert normalize_name("  Denver Broncos  ") == "denver broncos"
    
    def test_collapse_spaces(self):
        assert normalize_name("AFC    West") == "afc west"
    
    def test_empty_string(self):
        assert normalize_name("") == ""


class TestCreateCanonicalId:
    """Tests for canonical ID slug creation."""
    
    def test_basic_slug(self):
        assert create_canonical_id("Sean Payton", "Person") == "sean-payton"
    
    def test_organization_slug(self):
        assert create_canonical_id("Denver Broncos", "Organization") == "denver-broncos"
    
    def test_special_characters(self):
        assert create_canonical_id("AFC West (Division)", "Concept") == "afc-west-division"
    
    def test_unicode_stripped(self):
        result = create_canonical_id("Café Münchën", "Organization")
        # Unicode characters are stripped by ASCII encoding
        assert result == "caf-mnchn"
    
    def test_long_name_truncated(self):
        long_name = "A" * 100
        result = create_canonical_id(long_name, "Person")
        assert len(result) <= 48
        assert not result.endswith("-")
    
    def test_empty_name_default(self):
        assert create_canonical_id("", "Person") == "entity"
        assert create_canonical_id("!!!", "Person") == "entity"


class TestResolutionEvent:
    """Tests for resolution event dataclass."""
    
    def test_to_dict_minimal(self):
        event = ResolutionEvent(
            action="created",
            timestamp=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            by="copilot",
        )
        
        data = event.to_dict()
        assert data["action"] == "created"
        assert data["timestamp"] == "2026-01-08T12:00:00+00:00"
        assert data["by"] == "copilot"
        assert "issue_number" not in data
        assert "reasoning" not in data
    
    def test_to_dict_complete(self):
        event = ResolutionEvent(
            action="alias_added",
            timestamp=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            by="copilot",
            issue_number=42,
            reasoning="Short name variant",
            alias="Broncos",
        )
        
        data = event.to_dict()
        assert data["issue_number"] == 42
        assert data["reasoning"] == "Short name variant"
        assert data["alias"] == "Broncos"
    
    def test_from_dict(self):
        data = {
            "action": "created",
            "timestamp": "2026-01-08T12:00:00+00:00",
            "by": "copilot",
            "issue_number": 42,
            "reasoning": "First occurrence",
        }
        
        event = ResolutionEvent.from_dict(data)
        assert event.action == "created"
        assert event.by == "copilot"
        assert event.issue_number == 42
        assert event.reasoning == "First occurrence"


class TestCanonicalAssociation:
    """Tests for canonical association dataclass."""
    
    def test_to_dict(self):
        assoc = CanonicalAssociation(
            target_id="sean-payton",
            target_type="Person",
            relationships=[{"type": "employs", "count": 2}],
            source_checksums=["abc123", "def456"],
        )
        
        data = assoc.to_dict()
        assert data["target_id"] == "sean-payton"
        assert data["target_type"] == "Person"
        assert len(data["relationships"]) == 1
        assert len(data["source_checksums"]) == 2
    
    def test_from_dict(self):
        data = {
            "target_id": "denver-broncos",
            "target_type": "Organization",
            "relationships": [{"type": "member_of", "count": 1}],
            "source_checksums": ["xyz789"],
        }
        
        assoc = CanonicalAssociation.from_dict(data)
        assert assoc.target_id == "denver-broncos"
        assert assoc.target_type == "Organization"
        assert len(assoc.relationships) == 1
        assert len(assoc.source_checksums) == 1


class TestCanonicalEntity:
    """Tests for canonical entity dataclass."""
    
    def test_to_dict_minimal(self):
        entity = CanonicalEntity(
            canonical_id="sean-payton",
            canonical_name="Sean Payton",
            entity_type="Person",
            aliases=["Sean Payton"],
            source_checksums=["abc123"],
            corroboration_score=1,
            first_seen=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            resolution_history=[],
        )
        
        data = entity.to_dict()
        assert data["canonical_id"] == "sean-payton"
        assert data["canonical_name"] == "Sean Payton"
        assert data["entity_type"] == "Person"
        assert data["corroboration_score"] == 1
        assert data["resolution_history"] == []
    
    def test_to_dict_complete(self):
        entity = CanonicalEntity(
            canonical_id="denver-broncos",
            canonical_name="Denver Broncos",
            entity_type="Organization",
            aliases=["Denver Broncos", "Broncos"],
            source_checksums=["abc123", "def456"],
            corroboration_score=2,
            first_seen=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 1, 8, 13, 0, 0, tzinfo=timezone.utc),
            resolution_history=[
                ResolutionEvent(
                    action="created",
                    timestamp=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
                    by="copilot",
                )
            ],
            attributes={"location": "Denver, CO"},
            associations=[
                CanonicalAssociation(
                    target_id="sean-payton",
                    target_type="Person",
                    relationships=[{"type": "employs", "count": 1}],
                    source_checksums=["abc123"],
                )
            ],
            metadata={"confidence": 0.95},
        )
        
        data = entity.to_dict()
        assert len(data["aliases"]) == 2
        assert len(data["source_checksums"]) == 2
        assert len(data["resolution_history"]) == 1
        assert data["attributes"]["location"] == "Denver, CO"
        assert len(data["associations"]) == 1
        assert data["metadata"]["confidence"] == 0.95
    
    def test_from_dict(self):
        data = {
            "canonical_id": "sean-payton",
            "canonical_name": "Sean Payton",
            "entity_type": "Person",
            "aliases": ["Sean Payton"],
            "source_checksums": ["abc123"],
            "corroboration_score": 1,
            "first_seen": "2026-01-08T12:00:00+00:00",
            "last_updated": "2026-01-08T12:00:00+00:00",
            "resolution_history": [
                {
                    "action": "created",
                    "timestamp": "2026-01-08T12:00:00+00:00",
                    "by": "copilot",
                }
            ],
        }
        
        entity = CanonicalEntity.from_dict(data)
        assert entity.canonical_id == "sean-payton"
        assert entity.entity_type == "Person"
        assert len(entity.resolution_history) == 1


class TestAliasMap:
    """Tests for alias map dataclass."""
    
    def test_to_dict(self):
        alias_map = AliasMap(
            version=1,
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            by_type={
                "Person": {"sean payton": "sean-payton"},
                "Organization": {"denver broncos": "denver-broncos"},
            },
        )
        
        data = alias_map.to_dict()
        assert data["version"] == 1
        assert data["last_updated"] == "2026-01-08T12:00:00+00:00"
        assert "Person" in data["by_type"]
        assert "Organization" in data["by_type"]
    
    def test_from_dict(self):
        data = {
            "version": 1,
            "last_updated": "2026-01-08T12:00:00+00:00",
            "by_type": {
                "Person": {"sean payton": "sean-payton"},
            },
        }
        
        alias_map = AliasMap.from_dict(data)
        assert alias_map.version == 1
        assert "Person" in alias_map.by_type
        assert alias_map.by_type["Person"]["sean payton"] == "sean-payton"
    
    def test_create_empty(self):
        alias_map = AliasMap.create_empty()
        assert alias_map.version == 1
        assert "Person" in alias_map.by_type
        assert "Organization" in alias_map.by_type
        assert "Concept" in alias_map.by_type
        assert len(alias_map.by_type["Person"]) == 0


class TestCanonicalStorage:
    """Tests for canonical storage manager."""
    
    @pytest.fixture
    def temp_storage(self, tmp_path: Path) -> CanonicalStorage:
        """Create a temporary canonical storage."""
        return CanonicalStorage(root=tmp_path / "canonical")
    
    def test_initialization(self, temp_storage: CanonicalStorage):
        """Test that directories are created."""
        assert temp_storage.root.exists()
        assert temp_storage._people_dir.exists()  # noqa: SLF001
        assert temp_storage._organizations_dir.exists()  # noqa: SLF001
        assert temp_storage._concepts_dir.exists()  # noqa: SLF001
    
    def test_save_and_get_entity_person(self, temp_storage: CanonicalStorage):
        """Test saving and retrieving a person entity."""
        entity = CanonicalEntity(
            canonical_id="sean-payton",
            canonical_name="Sean Payton",
            entity_type="Person",
            aliases=["Sean Payton"],
            source_checksums=["abc123"],
            corroboration_score=1,
            first_seen=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            resolution_history=[],
        )
        
        temp_storage.save_entity(entity)
        
        retrieved = temp_storage.get_entity("sean-payton", "Person")
        assert retrieved is not None
        assert retrieved.canonical_id == "sean-payton"
        assert retrieved.canonical_name == "Sean Payton"
    
    def test_save_and_get_entity_organization(self, temp_storage: CanonicalStorage):
        """Test saving and retrieving an organization entity."""
        entity = CanonicalEntity(
            canonical_id="denver-broncos",
            canonical_name="Denver Broncos",
            entity_type="Organization",
            aliases=["Denver Broncos", "Broncos"],
            source_checksums=["abc123"],
            corroboration_score=1,
            first_seen=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            resolution_history=[],
        )
        
        temp_storage.save_entity(entity)
        
        retrieved = temp_storage.get_entity("denver-broncos", "Organization")
        assert retrieved is not None
        assert retrieved.canonical_id == "denver-broncos"
        assert len(retrieved.aliases) == 2
    
    def test_get_entity_not_found(self, temp_storage: CanonicalStorage):
        """Test retrieving a non-existent entity."""
        result = temp_storage.get_entity("nonexistent", "Person")
        assert result is None
    
    def test_list_entities_empty(self, temp_storage: CanonicalStorage):
        """Test listing entities when none exist."""
        entities = temp_storage.list_entities("Person")
        assert len(entities) == 0
    
    def test_list_entities_multiple(self, temp_storage: CanonicalStorage):
        """Test listing multiple entities."""
        entity1 = CanonicalEntity(
            canonical_id="sean-payton",
            canonical_name="Sean Payton",
            entity_type="Person",
            aliases=["Sean Payton"],
            source_checksums=["abc123"],
            corroboration_score=1,
            first_seen=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            resolution_history=[],
        )
        
        entity2 = CanonicalEntity(
            canonical_id="courtland-sutton",
            canonical_name="Courtland Sutton",
            entity_type="Person",
            aliases=["Courtland Sutton"],
            source_checksums=["def456"],
            corroboration_score=1,
            first_seen=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            resolution_history=[],
        )
        
        temp_storage.save_entity(entity1)
        temp_storage.save_entity(entity2)
        
        entities = temp_storage.list_entities("Person")
        assert len(entities) == 2
        ids = {e.canonical_id for e in entities}
        assert "sean-payton" in ids
        assert "courtland-sutton" in ids
    
    def test_save_and_load_alias_map(self, temp_storage: CanonicalStorage):
        """Test saving and loading alias map."""
        alias_map = AliasMap(
            version=1,
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            by_type={
                "Person": {"sean payton": "sean-payton"},
                "Organization": {"denver broncos": "denver-broncos"},
            },
        )
        
        temp_storage.save_alias_map(alias_map)
        
        loaded = temp_storage.load_alias_map()
        assert loaded.version == 1
        assert loaded.by_type["Person"]["sean payton"] == "sean-payton"
        assert loaded.by_type["Organization"]["denver broncos"] == "denver-broncos"
    
    def test_load_alias_map_not_exists(self, temp_storage: CanonicalStorage):
        """Test loading alias map when file doesn't exist."""
        loaded = temp_storage.load_alias_map()
        assert loaded.version == 1
        assert "Person" in loaded.by_type
        assert len(loaded.by_type["Person"]) == 0
    
    def test_lookup_canonical_id_found(self, temp_storage: CanonicalStorage):
        """Test looking up canonical ID."""
        alias_map = AliasMap(
            version=1,
            last_updated=datetime(2026, 1, 8, 12, 0, 0, tzinfo=timezone.utc),
            by_type={
                "Person": {"sean payton": "sean-payton"},
            },
        )
        temp_storage.save_alias_map(alias_map)
        
        result = temp_storage.lookup_canonical_id("Sean Payton", "Person")
        assert result == "sean-payton"
    
    def test_lookup_canonical_id_not_found(self, temp_storage: CanonicalStorage):
        """Test looking up non-existent canonical ID."""
        result = temp_storage.lookup_canonical_id("Unknown Person", "Person")
        assert result is None
    
    def test_add_alias(self, temp_storage: CanonicalStorage):
        """Test adding an alias to the map."""
        temp_storage.add_alias("denver-broncos", "Denver Broncos", "Organization")
        temp_storage.add_alias("denver-broncos", "Broncos", "Organization")
        
        alias_map = temp_storage.load_alias_map()
        assert alias_map.by_type["Organization"]["denver broncos"] == "denver-broncos"
        assert alias_map.by_type["Organization"]["broncos"] == "denver-broncos"
    
    def test_add_alias_new_type(self, temp_storage: CanonicalStorage):
        """Test adding alias for a type not in the map."""
        temp_storage.add_alias("custom-entity", "Custom Entity", "CustomType")
        
        alias_map = temp_storage.load_alias_map()
        assert "CustomType" in alias_map.by_type
        assert alias_map.by_type["CustomType"]["custom entity"] == "custom-entity"
    
    def test_entity_path_invalid_type(self, temp_storage: CanonicalStorage):
        """Test that invalid entity type raises error."""
        with pytest.raises(ValueError, match="Unknown entity type"):
            temp_storage._get_entity_path("some-id", "InvalidType")  # noqa: SLF001
    
    def test_list_entities_invalid_type(self, temp_storage: CanonicalStorage):
        """Test that listing invalid entity type raises error."""
        with pytest.raises(ValueError, match="Unknown entity type"):
            temp_storage.list_entities("InvalidType")
