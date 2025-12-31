"""Storage for knowledge graph entities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List

from src import paths
from src.parsing import utils

if TYPE_CHECKING:
    from src.integrations.github.storage import GitHubStorageClient

_DEFAULT_KB_ROOT = paths.get_knowledge_graph_root()


@dataclass(slots=True)
class ExtractedPeople:
    """List of people extracted from a source document."""
    
    source_checksum: str
    people: List[str]
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_checksum": self.source_checksum,
            "people": self.people,
            "extracted_at": self.extracted_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractedPeople":
        extracted_at = datetime.fromisoformat(payload["extracted_at"])
        return cls(
            source_checksum=payload["source_checksum"],
            people=payload["people"],
            extracted_at=extracted_at,
            metadata=payload.get("metadata", {}),
        )



@dataclass(slots=True)
class ExtractedOrganizations:
    """List of organizations extracted from a source document."""
    
    source_checksum: str
    organizations: List[str]
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_checksum": self.source_checksum,
            "organizations": self.organizations,
            "extracted_at": self.extracted_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractedOrganizations":
        extracted_at = datetime.fromisoformat(payload["extracted_at"])
        return cls(
            source_checksum=payload["source_checksum"],
            organizations=payload["organizations"],
            extracted_at=extracted_at,
            metadata=payload.get("metadata", {}),
        )


@dataclass(slots=True)
class ExtractedConcepts:
    """List of concepts extracted from a source document."""
    
    source_checksum: str
    concepts: List[str]
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_checksum": self.source_checksum,
            "concepts": self.concepts,
            "extracted_at": self.extracted_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractedConcepts":
        extracted_at = datetime.fromisoformat(payload["extracted_at"])
        return cls(
            source_checksum=payload["source_checksum"],
            concepts=payload["concepts"],
            extracted_at=extracted_at,
            metadata=payload.get("metadata", {}),
        )


@dataclass(slots=True)
class EntityAssociation:
    """Represents an association between two entities."""
    
    source: str
    target: str
    relationship: str
    evidence: str
    source_type: str = "Unknown"
    target_type: str = "Unknown"
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relationship": self.relationship,
            "evidence": self.evidence,
            "source_type": self.source_type,
            "target_type": self.target_type,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityAssociation":
        return cls(
            source=payload["source"],
            target=payload["target"],
            relationship=payload["relationship"],
            evidence=payload.get("evidence", ""),
            source_type=payload.get("source_type", "Unknown"),
            target_type=payload.get("target_type", "Unknown"),
            confidence=payload.get("confidence", 1.0),
        )


@dataclass(slots=True)
class ExtractedAssociations:
    """List of associations extracted from a source document."""
    
    source_checksum: str
    associations: List[EntityAssociation]
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_checksum": self.source_checksum,
            "associations": [a.to_dict() for a in self.associations],
            "extracted_at": self.extracted_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractedAssociations":
        extracted_at = datetime.fromisoformat(payload["extracted_at"])
        return cls(
            source_checksum=payload["source_checksum"],
            associations=[EntityAssociation.from_dict(a) for a in payload["associations"]],
            extracted_at=extracted_at,
            metadata=payload.get("metadata", {}),
        )


class KnowledgeGraphStorage:
    """Manages storage of extracted knowledge graph entities.

    When running in GitHub Actions, pass a GitHubStorageClient to persist
    writes via the GitHub API instead of the local filesystem.
    """

    def __init__(
        self,
        root: Path | None = None,
        github_client: "GitHubStorageClient | None" = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = root or _DEFAULT_KB_ROOT
        self.root = self.root if self.root.is_absolute() else self.root.resolve()
        self._github_client = github_client
        # Project root for computing relative paths (defaults to cwd)
        self._project_root = project_root or Path.cwd()
        utils.ensure_directory(self.root)
        self._people_dir = self.root / "people"
        utils.ensure_directory(self._people_dir)
        self._organizations_dir = self.root / "organizations"
        utils.ensure_directory(self._organizations_dir)
        self._concepts_dir = self.root / "concepts"
        utils.ensure_directory(self._concepts_dir)
        self._associations_dir = self.root / "associations"
        utils.ensure_directory(self._associations_dir)
        self._profiles_dir = self.root / "profiles"
        utils.ensure_directory(self._profiles_dir)

    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to project root for GitHub API."""
        try:
            return str(path.relative_to(self._project_root))
        except ValueError:
            # Path is not under project root, use path relative to storage root
            # and prefix with the root's name
            return str(path)

    def save_extracted_people(self, source_checksum: str, people: List[str]) -> None:
        """Save extracted people for a given source document."""
        entry = ExtractedPeople(source_checksum=source_checksum, people=people)
        path = self._get_people_path(source_checksum)
        content = json.dumps(entry.to_dict(), indent=2)

        if self._github_client:
            rel_path = self._get_relative_path(path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Extract people from {source_checksum[:12]}",
            )
        else:
            # Local atomic write
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)

    def get_extracted_people(self, source_checksum: str) -> ExtractedPeople | None:
        """Retrieve extracted people for a given source document."""
        path = self._get_people_path(source_checksum)
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractedPeople.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save_extracted_organizations(self, source_checksum: str, organizations: List[str]) -> None:
        """Save extracted organizations for a given source document."""
        entry = ExtractedOrganizations(source_checksum=source_checksum, organizations=organizations)
        path = self._get_organizations_path(source_checksum)
        content = json.dumps(entry.to_dict(), indent=2)

        if self._github_client:
            rel_path = self._get_relative_path(path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Extract organizations from {source_checksum[:12]}",
            )
        else:
            # Local atomic write
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)

    def get_extracted_organizations(self, source_checksum: str) -> ExtractedOrganizations | None:
        """Retrieve extracted organizations for a given source document."""
        path = self._get_organizations_path(source_checksum)
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractedOrganizations.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save_extracted_concepts(self, source_checksum: str, concepts: List[str]) -> None:
        """Save extracted concepts for a given source document."""
        entry = ExtractedConcepts(source_checksum=source_checksum, concepts=concepts)
        path = self._get_concepts_path(source_checksum)
        content = json.dumps(entry.to_dict(), indent=2)

        if self._github_client:
            rel_path = self._get_relative_path(path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Extract concepts from {source_checksum[:12]}",
            )
        else:
            # Local atomic write
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)

    def get_extracted_concepts(self, source_checksum: str) -> ExtractedConcepts | None:
        """Retrieve extracted concepts for a given source document."""
        path = self._get_concepts_path(source_checksum)
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractedConcepts.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save_extracted_associations(self, source_checksum: str, associations: List[EntityAssociation]) -> None:
        """Save extracted associations for a given source document."""
        entry = ExtractedAssociations(source_checksum=source_checksum, associations=associations)
        path = self._get_associations_path(source_checksum)
        content = json.dumps(entry.to_dict(), indent=2)

        if self._github_client:
            rel_path = self._get_relative_path(path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Extract associations from {source_checksum[:12]}",
            )
        else:
            # Local atomic write
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)

    def get_extracted_associations(self, source_checksum: str) -> ExtractedAssociations | None:
        """Retrieve extracted associations for a given source document."""
        path = self._get_associations_path(source_checksum)
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractedAssociations.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save_extracted_profiles(self, source_checksum: str, profiles: List[EntityProfile]) -> None:
        """Save extracted profiles for a given source document."""
        entry = ExtractedProfiles(source_checksum=source_checksum, profiles=profiles)
        path = self._get_profiles_path(source_checksum)
        content = json.dumps(entry.to_dict(), indent=2)

        if self._github_client:
            rel_path = self._get_relative_path(path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Extract profiles from {source_checksum[:12]}",
            )
        else:
            # Local atomic write
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(path)

    def get_extracted_profiles(self, source_checksum: str) -> ExtractedProfiles | None:
        """Retrieve extracted profiles for a given source document."""
        path = self._get_profiles_path(source_checksum)
        if not path.exists():
            return None
        
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return ExtractedProfiles.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def _get_people_path(self, checksum: str) -> Path:
        """Get the path for the people file corresponding to a checksum."""
        # Use a sharded structure if needed, but flat is fine for now
        return self._people_dir / f"{checksum}.json"

    def _get_organizations_path(self, checksum: str) -> Path:
        """Get the path for the organizations file corresponding to a checksum."""
        return self._organizations_dir / f"{checksum}.json"

    def _get_concepts_path(self, checksum: str) -> Path:
        """Get the path for the concepts file corresponding to a checksum."""
        return self._concepts_dir / f"{checksum}.json"

    def _get_associations_path(self, checksum: str) -> Path:
        """Get the path for the associations file corresponding to a checksum."""
        return self._associations_dir / f"{checksum}.json"

    def _get_profiles_path(self, checksum: str) -> Path:
        """Get the path for the profiles file corresponding to a checksum."""
        return self._profiles_dir / f"{checksum}.json"


@dataclass(slots=True)
class EntityProfile:
    """Detailed profile of an entity."""
    
    name: str
    entity_type: str  # Person, Organization, Concept
    summary: str
    attributes: dict[str, Any] = field(default_factory=dict)
    mentions: List[str] = field(default_factory=list)
    confidence: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "entity_type": self.entity_type,
            "summary": self.summary,
            "attributes": self.attributes,
            "mentions": self.mentions,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EntityProfile":
        return cls(
            name=payload["name"],
            entity_type=payload["entity_type"],
            summary=payload["summary"],
            attributes=payload.get("attributes", {}),
            mentions=payload.get("mentions", []),
            confidence=payload.get("confidence", 1.0),
        )


@dataclass(slots=True)
class ExtractedProfiles:
    """List of entity profiles extracted from a source document."""
    
    source_checksum: str
    profiles: List[EntityProfile]
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_checksum": self.source_checksum,
            "profiles": [p.to_dict() for p in self.profiles],
            "extracted_at": self.extracted_at.isoformat(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExtractedProfiles":
        extracted_at = datetime.fromisoformat(payload["extracted_at"])
        return cls(
            source_checksum=payload["source_checksum"],
            profiles=[EntityProfile.from_dict(p) for p in payload["profiles"]],
            extracted_at=extracted_at,
            metadata=payload.get("metadata", {}),
        )


# =============================================================================
# Source Registry
# =============================================================================


def _url_hash(url: str) -> str:
    """Generate a consistent hash for a URL to use as filename."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


@dataclass(slots=True)
class SourceEntry:
    """Represents an authoritative source in the registry."""

    url: str  # Canonical URL
    name: str  # Human-readable name
    source_type: str  # "primary" | "derived" | "reference"
    status: str  # "active" | "deprecated" | "pending_review"
    last_verified: datetime  # Last successful access check
    added_at: datetime  # When source was registered
    added_by: str  # GitHub username or "system"

    # Approval tracking (Discussions-first workflow)
    proposal_discussion: int | None  # Discussion number where proposed
    implementation_issue: int | None  # Issue number for implementation

    # Credibility metadata
    credibility_score: float  # 0.0-1.0, based on evaluation
    is_official: bool  # Official/authoritative domain
    requires_auth: bool  # Requires authentication to access

    # Discovery metadata
    discovered_from: str | None  # Checksum of document where discovered
    parent_source_url: str | None  # URL of source that referenced this

    # Content metadata
    content_type: str  # "webpage" | "pdf" | "api" | "feed"
    update_frequency: str | None  # "daily" | "weekly" | "monthly" | "unknown"
    topics: List[str] = field(default_factory=list)
    notes: str = ""

    # Monitoring metadata (for change detection)
    last_content_hash: str | None = None  # SHA-256 of last acquired content
    last_etag: str | None = None  # HTTP ETag from last check
    last_modified_header: str | None = None  # Last-Modified header value
    last_checked: datetime | None = None  # When source was last probed
    check_failures: int = 0  # Consecutive check failures
    next_check_after: datetime | None = None  # Backoff: don't check before this

    # Site-wide crawl configuration
    is_crawlable: bool = False  # Enable site-wide crawling
    crawl_scope: str = "path"  # "path" | "host" | "domain"
    crawl_max_pages: int = 10000  # Max pages to acquire
    crawl_max_depth: int = 10  # Max link depth

    # Crawl state reference
    crawl_state_path: str | None = None  # Path to CrawlState file

    # Crawl statistics
    total_pages_discovered: int = 0
    total_pages_acquired: int = 0
    last_crawl_started: datetime | None = None
    last_crawl_completed: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "name": self.name,
            "source_type": self.source_type,
            "status": self.status,
            "last_verified": self.last_verified.isoformat(),
            "added_at": self.added_at.isoformat(),
            "added_by": self.added_by,
            "proposal_discussion": self.proposal_discussion,
            "implementation_issue": self.implementation_issue,
            "credibility_score": self.credibility_score,
            "is_official": self.is_official,
            "requires_auth": self.requires_auth,
            "discovered_from": self.discovered_from,
            "parent_source_url": self.parent_source_url,
            "content_type": self.content_type,
            "update_frequency": self.update_frequency,
            "topics": self.topics,
            "notes": self.notes,
            # Monitoring metadata
            "last_content_hash": self.last_content_hash,
            "last_etag": self.last_etag,
            "last_modified_header": self.last_modified_header,
            "last_checked": self.last_checked.isoformat() if self.last_checked else None,
            "check_failures": self.check_failures,
            "next_check_after": self.next_check_after.isoformat() if self.next_check_after else None,
            # Crawl configuration
            "is_crawlable": self.is_crawlable,
            "crawl_scope": self.crawl_scope,
            "crawl_max_pages": self.crawl_max_pages,
            "crawl_max_depth": self.crawl_max_depth,
            "crawl_state_path": self.crawl_state_path,
            # Crawl statistics
            "total_pages_discovered": self.total_pages_discovered,
            "total_pages_acquired": self.total_pages_acquired,
            "last_crawl_started": self.last_crawl_started.isoformat() if self.last_crawl_started else None,
            "last_crawl_completed": self.last_crawl_completed.isoformat() if self.last_crawl_completed else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "SourceEntry":
        # Handle backward compatibility: approval_issue -> implementation_issue
        implementation_issue = payload.get("implementation_issue")
        if implementation_issue is None:
            implementation_issue = payload.get("approval_issue")  # legacy field
        
        # Parse optional datetime fields for monitoring
        last_checked = None
        if payload.get("last_checked"):
            last_checked = datetime.fromisoformat(payload["last_checked"])
        
        next_check_after = None
        if payload.get("next_check_after"):
            next_check_after = datetime.fromisoformat(payload["next_check_after"])
        
        # Parse optional datetime fields for crawling
        last_crawl_started = None
        if payload.get("last_crawl_started"):
            last_crawl_started = datetime.fromisoformat(payload["last_crawl_started"])
        
        last_crawl_completed = None
        if payload.get("last_crawl_completed"):
            last_crawl_completed = datetime.fromisoformat(payload["last_crawl_completed"])
        
        return cls(
            url=payload["url"],
            name=payload["name"],
            source_type=payload["source_type"],
            status=payload["status"],
            last_verified=datetime.fromisoformat(payload["last_verified"]),
            added_at=datetime.fromisoformat(payload["added_at"]),
            added_by=payload["added_by"],
            proposal_discussion=payload.get("proposal_discussion"),
            implementation_issue=implementation_issue,
            credibility_score=payload.get("credibility_score", 0.0),
            is_official=payload.get("is_official", False),
            requires_auth=payload.get("requires_auth", False),
            discovered_from=payload.get("discovered_from"),
            parent_source_url=payload.get("parent_source_url"),
            content_type=payload.get("content_type", "webpage"),
            update_frequency=payload.get("update_frequency"),
            topics=payload.get("topics", []),
            notes=payload.get("notes", ""),
            # Monitoring metadata (with defaults for backward compatibility)
            last_content_hash=payload.get("last_content_hash"),
            last_etag=payload.get("last_etag"),
            last_modified_header=payload.get("last_modified_header"),
            last_checked=last_checked,
            check_failures=payload.get("check_failures", 0),
            next_check_after=next_check_after,
            # Crawl configuration (with defaults for backward compatibility)
            is_crawlable=payload.get("is_crawlable", False),
            crawl_scope=payload.get("crawl_scope", "path"),
            crawl_max_pages=payload.get("crawl_max_pages", 10000),
            crawl_max_depth=payload.get("crawl_max_depth", 10),
            crawl_state_path=payload.get("crawl_state_path"),
            # Crawl statistics
            total_pages_discovered=payload.get("total_pages_discovered", 0),
            total_pages_acquired=payload.get("total_pages_acquired", 0),
            last_crawl_started=last_crawl_started,
            last_crawl_completed=last_crawl_completed,
        )

    @property
    def url_hash(self) -> str:
        """Return the hash used for storage filename."""
        return _url_hash(self.url)


class SourceRegistry:
    """Manages storage of authoritative sources.

    When running in GitHub Actions, pass a GitHubStorageClient to persist
    writes via the GitHub API instead of the local filesystem.
    """

    def __init__(
        self,
        root: Path | None = None,
        github_client: "GitHubStorageClient | None" = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = root or _DEFAULT_KB_ROOT
        self.root = self.root if self.root.is_absolute() else self.root.resolve()
        self._github_client = github_client
        # Project root for computing relative paths (defaults to cwd)
        self._project_root = project_root or Path.cwd()
        utils.ensure_directory(self.root)
        self._sources_dir = self.root / "sources"
        utils.ensure_directory(self._sources_dir)
        self._registry_path = self._sources_dir / "registry.json"

    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to project root for GitHub API."""
        try:
            return str(path.relative_to(self._project_root))
        except ValueError:
            # Path is not under project root, use absolute path
            return str(path)

    def _get_source_path(self, url: str) -> Path:
        """Get the path for an individual source entry."""
        return self._sources_dir / f"{_url_hash(url)}.json"

    def _load_registry_index(self) -> dict[str, str]:
        """Load the registry index mapping URL hashes to URLs."""
        if not self._registry_path.exists():
            return {}
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            return data.get("sources", {})
        except (json.JSONDecodeError, KeyError):
            return {}

    def _save_registry_index(self, index: dict[str, str]) -> None:
        """Save the registry index."""
        data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources": index,
        }
        content = json.dumps(data, indent=2)

        if self._github_client:
            rel_path = self._get_relative_path(self._registry_path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message="Update source registry index",
            )
        else:
            tmp_path = self._registry_path.with_suffix(".json.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self._registry_path)

    def save_source(self, source: SourceEntry) -> None:
        """Save a source entry to storage."""
        path = self._get_source_path(source.url)
        source_content = json.dumps(source.to_dict(), indent=2)

        # Update registry index
        index = self._load_registry_index()
        index[source.url_hash] = source.url
        index_data = {
            "version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "sources": index,
        }
        index_content = json.dumps(index_data, indent=2)

        if self._github_client:
            # Batch commit both files together
            source_rel = self._get_relative_path(path)
            index_rel = self._get_relative_path(self._registry_path)
            self._github_client.commit_files_batch(
                files=[
                    (source_rel, source_content),
                    (index_rel, index_content),
                ],
                message=f"Update source: {source.name}",
            )
        else:
            # Local atomic writes
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(source_content, encoding="utf-8")
            tmp_path.replace(path)

            tmp_idx = self._registry_path.with_suffix(".json.tmp")
            tmp_idx.write_text(index_content, encoding="utf-8")
            tmp_idx.replace(self._registry_path)

    def get_source(self, url: str) -> SourceEntry | None:
        """Retrieve a source entry by URL."""
        path = self._get_source_path(url)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SourceEntry.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def get_source_by_hash(self, url_hash: str) -> SourceEntry | None:
        """Retrieve a source entry by its URL hash."""
        path = self._sources_dir / f"{url_hash}.json"
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return SourceEntry.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def list_sources(
        self,
        status: str | None = None,
        source_type: str | None = None,
    ) -> List[SourceEntry]:
        """List all sources, optionally filtered by status or type."""
        sources: List[SourceEntry] = []
        index = self._load_registry_index()

        for url_hash in index:
            source = self.get_source_by_hash(url_hash)
            if source is None:
                continue
            if status is not None and source.status != status:
                continue
            if source_type is not None and source.source_type != source_type:
                continue
            sources.append(source)

        return sources

    def delete_source(self, url: str) -> bool:
        """Delete a source entry. Returns True if deleted, False if not found."""
        path = self._get_source_path(url)
        url_hash = _url_hash(url)

        if not path.exists():
            return False

        path.unlink()

        # Update registry index
        index = self._load_registry_index()
        if url_hash in index:
            del index[url_hash]
            self._save_registry_index(index)

        return True

    def source_exists(self, url: str) -> bool:
        """Check if a source is already registered."""
        return self._get_source_path(url).exists()

    def get_all_urls(self) -> List[str]:
        """Get all registered source URLs."""
        index = self._load_registry_index()
        return list(index.values())

