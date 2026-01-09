"""Storage for canonical (deduplicated) knowledge graph entities.

This module provides dataclasses and I/O functions for managing canonical entities
that consolidate multiple source mentions into single authoritative records.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List

from src import paths
from src.parsing import utils

if TYPE_CHECKING:
    from src.integrations.github.storage import GitHubStorageClient

_DEFAULT_CANONICAL_ROOT = paths.get_knowledge_graph_root() / "canonical"
_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class ResolutionEvent:
    """Represents a single event in an entity's resolution history."""
    
    action: str  # "created" | "alias_added" | "merged" | "split" | "updated"
    timestamp: datetime
    by: str  # "copilot" | username
    issue_number: int | None = None
    reasoning: str = ""
    alias: str | None = None  # For alias_added actions
    merged_from: str | None = None  # For merged actions
    
    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "action": self.action,
            "timestamp": self.timestamp.isoformat(),
            "by": self.by,
        }
        if self.issue_number is not None:
            result["issue_number"] = self.issue_number
        if self.reasoning:
            result["reasoning"] = self.reasoning
        if self.alias is not None:
            result["alias"] = self.alias
        if self.merged_from is not None:
            result["merged_from"] = self.merged_from
        return result
    
    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ResolutionEvent":
        timestamp = datetime.fromisoformat(payload["timestamp"])
        return cls(
            action=payload["action"],
            timestamp=timestamp,
            by=payload["by"],
            issue_number=payload.get("issue_number"),
            reasoning=payload.get("reasoning", ""),
            alias=payload.get("alias"),
            merged_from=payload.get("merged_from"),
        )


@dataclass(slots=True)
class CanonicalAssociation:
    """Represents an association to another canonical entity."""
    
    target_id: str  # Canonical ID of the target entity
    target_type: str  # "Person" | "Organization" | "Concept"
    relationships: List[dict[str, Any]]  # [{"type": "employs", "count": 2}, ...]
    source_checksums: List[str]  # Which sources contributed this association
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "target_id": self.target_id,
            "target_type": self.target_type,
            "relationships": self.relationships,
            "source_checksums": self.source_checksums,
        }
    
    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalAssociation":
        return cls(
            target_id=payload["target_id"],
            target_type=payload["target_type"],
            relationships=payload.get("relationships", []),
            source_checksums=payload.get("source_checksums", []),
        )


@dataclass(slots=True)
class CanonicalEntity:
    """A deduplicated entity with all source references and resolution history.
    
    This represents the authoritative record for a real-world entity,
    consolidating all mentions across source documents.
    """
    
    canonical_id: str  # Slug: "sean-payton", "denver-broncos"
    canonical_name: str  # Display name: "Sean Payton", "Denver Broncos"
    entity_type: str  # "Person" | "Organization" | "Concept"
    aliases: List[str]  # All known names for this entity
    source_checksums: List[str]  # Which documents mention this entity
    corroboration_score: int  # Number of sources (len(source_checksums))
    first_seen: datetime
    last_updated: datetime
    resolution_history: List[ResolutionEvent]
    attributes: dict[str, Any] = field(default_factory=dict)  # Merged attributes
    associations: List[CanonicalAssociation] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)  # Confidence, review flags
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "canonical_name": self.canonical_name,
            "entity_type": self.entity_type,
            "aliases": self.aliases,
            "source_checksums": self.source_checksums,
            "corroboration_score": self.corroboration_score,
            "first_seen": self.first_seen.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "resolution_history": [e.to_dict() for e in self.resolution_history],
            "attributes": self.attributes,
            "associations": [a.to_dict() for a in self.associations],
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CanonicalEntity":
        first_seen = datetime.fromisoformat(payload["first_seen"])
        last_updated = datetime.fromisoformat(payload["last_updated"])
        resolution_history = [
            ResolutionEvent.from_dict(e) for e in payload.get("resolution_history", [])
        ]
        associations = [
            CanonicalAssociation.from_dict(a) for a in payload.get("associations", [])
        ]
        
        return cls(
            canonical_id=payload["canonical_id"],
            canonical_name=payload["canonical_name"],
            entity_type=payload["entity_type"],
            aliases=payload.get("aliases", []),
            source_checksums=payload.get("source_checksums", []),
            corroboration_score=payload.get("corroboration_score", 0),
            first_seen=first_seen,
            last_updated=last_updated,
            resolution_history=resolution_history,
            attributes=payload.get("attributes", {}),
            associations=associations,
            metadata=payload.get("metadata", {}),
        )


@dataclass(slots=True)
class AliasMap:
    """Maps normalized entity names to canonical IDs."""
    
    version: int
    last_updated: datetime
    by_type: dict[str, dict[str, str]]  # {"Person": {"sean payton": "sean-payton"}, ...}
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "last_updated": self.last_updated.isoformat(),
            "by_type": self.by_type,
        }
    
    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AliasMap":
        last_updated = datetime.fromisoformat(payload["last_updated"])
        return cls(
            version=payload.get("version", 1),
            last_updated=last_updated,
            by_type=payload.get("by_type", {}),
        )
    
    @classmethod
    def create_empty(cls) -> "AliasMap":
        """Create an empty alias map with default structure."""
        return cls(
            version=1,
            last_updated=datetime.now(timezone.utc),
            by_type={
                "Person": {},
                "Organization": {},
                "Concept": {},
            },
        )


def normalize_name(name: str) -> str:
    """Normalize entity name for alias map lookup.
    
    Converts to lowercase, strips whitespace, and collapses multiple spaces.
    
    Args:
        name: Raw entity name
        
    Returns:
        Normalized name for dictionary lookup
    """
    return " ".join(name.lower().strip().split())


def create_canonical_id(name: str, entity_type: str = "") -> str:
    """Create a canonical ID slug from an entity name.
    
    Args:
        name: Display name of the entity
        entity_type: Entity type (for potential disambiguation, currently unused)
        
    Returns:
        URL-safe slug like "sean-payton" or "denver-broncos"
    """
    _ = entity_type  # Reserved for future disambiguation logic
    # Convert to ASCII, lowercase
    candidate = name.encode("ascii", errors="ignore").decode().lower()
    # Replace non-alphanumeric with hyphens
    candidate = _SLUG_PATTERN.sub("-", candidate).strip("-")
    
    if not candidate:
        candidate = "entity"
    
    # Limit length
    max_length = 48
    if len(candidate) > max_length:
        candidate = candidate[:max_length].rstrip("-")
        if not candidate:
            candidate = "entity"
    
    return candidate


class CanonicalStorage:
    """Manages storage of canonical entities and alias maps.
    
    When running in GitHub Actions, pass a GitHubStorageClient to persist
    writes via the GitHub API instead of the local filesystem.
    """
    
    def __init__(
        self,
        root: Path | None = None,
        github_client: "GitHubStorageClient | None" = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = root or _DEFAULT_CANONICAL_ROOT
        self.root = self.root if self.root.is_absolute() else self.root.resolve()
        self._github_client = github_client
        self._project_root = project_root or Path.cwd()
        
        # Ensure directories exist
        utils.ensure_directory(self.root)
        self._people_dir = self.root / "people"
        utils.ensure_directory(self._people_dir)
        self._organizations_dir = self.root / "organizations"
        utils.ensure_directory(self._organizations_dir)
        self._concepts_dir = self.root / "concepts"
        utils.ensure_directory(self._concepts_dir)
        
        self._alias_map_path = self.root / "alias-map.json"
    
    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to project root for GitHub API."""
        try:
            return str(path.relative_to(self._project_root))
        except ValueError:
            return str(path)
    
    def _get_entity_path(self, canonical_id: str, entity_type: str) -> Path:
        """Get file path for a canonical entity."""
        if entity_type == "Person":
            return self._people_dir / f"{canonical_id}.json"
        elif entity_type == "Organization":
            return self._organizations_dir / f"{canonical_id}.json"
        elif entity_type == "Concept":
            return self._concepts_dir / f"{canonical_id}.json"
        else:
            raise ValueError(f"Unknown entity type: {entity_type}")
    
    def save_entity(self, entity: CanonicalEntity) -> None:
        """Save a canonical entity to disk or GitHub."""
        path = self._get_entity_path(entity.canonical_id, entity.entity_type)
        content = json.dumps(entity.to_dict(), indent=2)
        
        if self._github_client:
            rel_path = self._get_relative_path(path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Update canonical entity {entity.canonical_id}",
            )
        else:
            # Local atomic write
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)
    
    def get_entity(self, canonical_id: str, entity_type: str) -> CanonicalEntity | None:
        """Retrieve a canonical entity by ID and type."""
        path = self._get_entity_path(canonical_id, entity_type)
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CanonicalEntity.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None
    
    def list_entities(self, entity_type: str) -> List[CanonicalEntity]:
        """List all canonical entities of a given type."""
        if entity_type == "Person":
            directory = self._people_dir
        elif entity_type == "Organization":
            directory = self._organizations_dir
        elif entity_type == "Concept":
            directory = self._concepts_dir
        else:
            raise ValueError(f"Unknown entity type: {entity_type}")
        
        entities: List[CanonicalEntity] = []
        if directory.exists():
            for path in directory.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    entities.append(CanonicalEntity.from_dict(data))
                except (json.JSONDecodeError, KeyError):
                    continue
        
        return entities
    
    def save_alias_map(self, alias_map: AliasMap) -> None:
        """Save the alias map to disk or GitHub."""
        content = json.dumps(alias_map.to_dict(), indent=2)
        
        if self._github_client:
            rel_path = self._get_relative_path(self._alias_map_path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message="Update canonical alias map",
            )
        else:
            # Local atomic write
            tmp_path = self._alias_map_path.with_suffix(self._alias_map_path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self._alias_map_path)
    
    def load_alias_map(self) -> AliasMap:
        """Load the alias map from disk.
        
        Returns an empty alias map if the file doesn't exist.
        """
        if not self._alias_map_path.exists():
            return AliasMap.create_empty()
        
        try:
            data = json.loads(self._alias_map_path.read_text(encoding="utf-8"))
            return AliasMap.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return AliasMap.create_empty()
    
    def lookup_canonical_id(self, name: str, entity_type: str) -> str | None:
        """Look up the canonical ID for a given name and type.
        
        Args:
            name: Raw entity name to look up
            entity_type: Entity type for scoped lookup
            
        Returns:
            Canonical ID if found, None otherwise
        """
        alias_map = self.load_alias_map()
        normalized = normalize_name(name)
        
        type_aliases = alias_map.by_type.get(entity_type, {})
        return type_aliases.get(normalized)
    
    def add_alias(
        self,
        canonical_id: str,
        alias: str,
        entity_type: str,
    ) -> None:
        """Add an alias to the alias map.
        
        Args:
            canonical_id: Canonical ID to map to
            alias: New alias to add
            entity_type: Entity type for scoped storage
        """
        alias_map = self.load_alias_map()
        normalized = normalize_name(alias)
        
        # Ensure type exists in map
        if entity_type not in alias_map.by_type:
            alias_map.by_type[entity_type] = {}
        
        # Add the alias
        alias_map.by_type[entity_type][normalized] = canonical_id
        alias_map.last_updated = datetime.now(timezone.utc)
        
        self.save_alias_map(alias_map)
