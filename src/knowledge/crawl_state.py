"""Crawl state management for site-wide crawling.

This module provides data structures and persistence for tracking the state
of a site-wide crawl across workflow runs. The CrawlState captures:
- URL frontier (queue of URLs to visit)
- Visited URLs (for deduplication)
- Statistics (discovered, in-scope, out-of-scope, failed counts)
- Configuration (scope, max pages, max depth)

The state is designed for resumable execution - workflows can save state,
exit, and continue later from where they left off.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Set

from src import paths
from src.parsing import utils

if TYPE_CHECKING:
    from src.integrations.github.storage import GitHubStorageClient


def _source_hash(url: str) -> str:
    """Generate a consistent hash for a source URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def _url_hash(url: str) -> str:
    """Generate a consistent hash for a URL (used for deduplication)."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


@dataclass
class CrawlState:
    """Persistent state for a site-wide crawl.
    
    This dataclass tracks all state needed to resume a crawl across
    workflow runs. The frontier is kept in memory (up to 1000 URLs),
    with overflow written to a separate file.
    
    Attributes:
        source_url: The source URL defining the crawl boundary root
        source_hash: SHA-256 hash of the source URL (first 16 chars)
        scope: Crawl scope constraint - "path", "host", or "domain"
        status: Current crawl status - "pending", "crawling", "paused", "completed"
        started_at: When the crawl was first started
        last_activity: When the last page was processed
        completed_at: When the crawl finished (if completed)
        frontier: Active queue of URLs to visit (max 1000 in memory)
        frontier_overflow_count: Count of URLs in overflow file
        visited_count: Total pages successfully fetched
        visited_hashes: Set of URL hashes for deduplication
        discovered_count: Total URLs found via link extraction
        in_scope_count: URLs that passed scope filter
        out_of_scope_count: URLs rejected by scope filter
        skipped_count: URLs skipped (robots.txt, patterns, etc.)
        failed_count: Failed fetches
        max_pages: Safety limit for total pages
        max_depth: Maximum link depth from source URL
        exclude_patterns: fnmatch patterns to exclude URLs
        content_root: Path to content storage directory
        registry_path: Path to page registry file
    """
    
    source_url: str
    source_hash: str
    scope: str  # "path" | "host" | "domain"
    
    # Status
    status: str = "pending"  # "pending" | "crawling" | "paused" | "completed"
    started_at: datetime | None = None
    last_activity: datetime | None = None
    completed_at: datetime | None = None
    
    # URL Frontier (URLs to visit)
    frontier: List[str] = field(default_factory=list)
    frontier_overflow_count: int = 0
    
    # Visited tracking
    visited_count: int = 0
    visited_hashes: Set[str] = field(default_factory=set)
    
    # Statistics
    discovered_count: int = 0
    in_scope_count: int = 0
    out_of_scope_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    
    # Configuration
    max_pages: int = 10000
    max_depth: int = 10
    exclude_patterns: List[str] = field(default_factory=list)
    
    # Storage paths (relative to knowledge-graph root)
    content_root: str = ""
    registry_path: str = ""
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize state to dictionary for JSON storage."""
        return {
            "source_url": self.source_url,
            "source_hash": self.source_hash,
            "scope": self.scope,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "frontier": self.frontier,
            "frontier_overflow_count": self.frontier_overflow_count,
            "visited_count": self.visited_count,
            "visited_hashes": list(self.visited_hashes),
            "discovered_count": self.discovered_count,
            "in_scope_count": self.in_scope_count,
            "out_of_scope_count": self.out_of_scope_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "max_pages": self.max_pages,
            "max_depth": self.max_depth,
            "exclude_patterns": self.exclude_patterns,
            "content_root": self.content_root,
            "registry_path": self.registry_path,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CrawlState":
        """Deserialize state from dictionary."""
        started_at = None
        if data.get("started_at"):
            started_at = datetime.fromisoformat(data["started_at"])
        
        last_activity = None
        if data.get("last_activity"):
            last_activity = datetime.fromisoformat(data["last_activity"])
        
        completed_at = None
        if data.get("completed_at"):
            completed_at = datetime.fromisoformat(data["completed_at"])
        
        return cls(
            source_url=data["source_url"],
            source_hash=data["source_hash"],
            scope=data["scope"],
            status=data.get("status", "pending"),
            started_at=started_at,
            last_activity=last_activity,
            completed_at=completed_at,
            frontier=data.get("frontier", []),
            frontier_overflow_count=data.get("frontier_overflow_count", 0),
            visited_count=data.get("visited_count", 0),
            visited_hashes=set(data.get("visited_hashes", [])),
            discovered_count=data.get("discovered_count", 0),
            in_scope_count=data.get("in_scope_count", 0),
            out_of_scope_count=data.get("out_of_scope_count", 0),
            skipped_count=data.get("skipped_count", 0),
            failed_count=data.get("failed_count", 0),
            max_pages=data.get("max_pages", 10000),
            max_depth=data.get("max_depth", 10),
            exclude_patterns=data.get("exclude_patterns", []),
            content_root=data.get("content_root", ""),
            registry_path=data.get("registry_path", ""),
        )
    
    @classmethod
    def create_new(
        cls,
        source_url: str,
        scope: str = "path",
        max_pages: int = 10000,
        max_depth: int = 10,
        exclude_patterns: list[str] | None = None,
    ) -> "CrawlState":
        """Create a new crawl state for a source URL.
        
        Args:
            source_url: The source URL defining the crawl boundary
            scope: Scope constraint - "path", "host", or "domain"
            max_pages: Maximum pages to crawl (safety limit)
            max_depth: Maximum link depth from source
            exclude_patterns: fnmatch patterns to exclude URLs
            
        Returns:
            A new CrawlState initialized with the source URL in the frontier
        """
        if scope not in ("path", "host", "domain"):
            raise ValueError(f"Invalid scope: {scope}. Must be 'path', 'host', or 'domain'")
        
        source_hash = _source_hash(source_url)
        
        state = cls(
            source_url=source_url,
            source_hash=source_hash,
            scope=scope,
            status="pending",
            frontier=[source_url],
            max_pages=max_pages,
            max_depth=max_depth,
            exclude_patterns=exclude_patterns or [],
            content_root=f"crawls/{source_hash}/content",
            registry_path=f"crawls/{source_hash}/registry.json",
        )
        
        return state
    
    def mark_started(self) -> None:
        """Mark the crawl as started."""
        now = datetime.now(timezone.utc)
        if self.started_at is None:
            self.started_at = now
        self.status = "crawling"
        self.last_activity = now
    
    def mark_paused(self) -> None:
        """Mark the crawl as paused (can be resumed)."""
        self.status = "paused"
        self.last_activity = datetime.now(timezone.utc)
    
    def mark_completed(self) -> None:
        """Mark the crawl as completed."""
        now = datetime.now(timezone.utc)
        self.status = "completed"
        self.completed_at = now
        self.last_activity = now
    
    def is_url_visited(self, url: str) -> bool:
        """Check if a URL has already been visited."""
        return _url_hash(url) in self.visited_hashes
    
    def mark_url_visited(self, url: str) -> None:
        """Mark a URL as visited."""
        self.visited_hashes.add(_url_hash(url))
        self.visited_count += 1
        self.last_activity = datetime.now(timezone.utc)
    
    def add_to_frontier(self, url: str) -> bool:
        """Add a URL to the frontier if not already visited.
        
        Returns:
            True if the URL was added, False if already visited
        """
        if self.is_url_visited(url):
            return False
        
        # Check if URL is already in frontier
        if url in self.frontier:
            return False
        
        self.frontier.append(url)
        return True
    
    def pop_frontier(self) -> str | None:
        """Pop the next URL from the frontier.
        
        Returns:
            The next URL to visit, or None if frontier is empty
        """
        if not self.frontier:
            return None
        return self.frontier.pop(0)
    
    @property
    def frontier_size(self) -> int:
        """Total frontier size including overflow."""
        return len(self.frontier) + self.frontier_overflow_count
    
    @property
    def is_complete(self) -> bool:
        """Check if the crawl is complete (frontier empty or limits reached)."""
        if self.status == "completed":
            return True
        if self.frontier_size == 0:
            return True
        if self.visited_count >= self.max_pages:
            return True
        return False


class CrawlStateStorage:
    """Manages persistence of crawl states.
    
    When running in GitHub Actions, pass a GitHubStorageClient to persist
    writes via the GitHub API instead of the local filesystem.
    """
    
    # Maximum URLs to keep in memory frontier (rest goes to overflow)
    MAX_FRONTIER_IN_MEMORY = 1000
    
    def __init__(
        self,
        root: Path | None = None,
        github_client: "GitHubStorageClient | None" = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = root or paths.get_knowledge_graph_root()
        self.root = self.root if self.root.is_absolute() else self.root.resolve()
        self._github_client = github_client
        self._project_root = project_root or Path.cwd()
        self._crawls_dir = self.root / "crawls"
        utils.ensure_directory(self._crawls_dir)
    
    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to project root for GitHub API."""
        try:
            return str(path.relative_to(self._project_root))
        except ValueError:
            return str(path)
    
    def _get_state_dir(self, source_hash: str) -> Path:
        """Get the directory for a crawl state."""
        return self._crawls_dir / source_hash
    
    def _get_state_path(self, source_hash: str) -> Path:
        """Get the path for the crawl state file."""
        return self._get_state_dir(source_hash) / "crawl_state.json"
    
    def _get_frontier_overflow_path(self, source_hash: str) -> Path:
        """Get the path for the frontier overflow file."""
        return self._get_state_dir(source_hash) / "frontier_overflow.jsonl"
    
    def save_state(self, state: CrawlState) -> None:
        """Save crawl state to storage.
        
        If the frontier exceeds MAX_FRONTIER_IN_MEMORY, excess URLs are
        written to a separate overflow file.
        """
        state_dir = self._get_state_dir(state.source_hash)
        utils.ensure_directory(state_dir)
        
        state_path = self._get_state_path(state.source_hash)
        overflow_path = self._get_frontier_overflow_path(state.source_hash)
        
        # Handle frontier overflow
        frontier_to_save = state.frontier
        overflow_urls: List[str] = []
        
        if len(state.frontier) > self.MAX_FRONTIER_IN_MEMORY:
            frontier_to_save = state.frontier[:self.MAX_FRONTIER_IN_MEMORY]
            overflow_urls = state.frontier[self.MAX_FRONTIER_IN_MEMORY:]
        
        # Create a copy of state with truncated frontier for serialization
        state_data = state.to_dict()
        state_data["frontier"] = frontier_to_save
        state_data["frontier_overflow_count"] = len(overflow_urls)
        
        state_content = json.dumps(state_data, indent=2)
        
        if self._github_client:
            files_to_commit = [(self._get_relative_path(state_path), state_content)]
            
            if overflow_urls:
                overflow_content = "\n".join(json.dumps({"url": url}) for url in overflow_urls)
                files_to_commit.append((self._get_relative_path(overflow_path), overflow_content))
            
            self._github_client.commit_files_batch(
                files=files_to_commit,
                message=f"Update crawl state for {state.source_hash[:8]}",
            )
        else:
            # Local atomic write for state
            tmp_path = state_path.with_suffix(".json.tmp")
            tmp_path.write_text(state_content, encoding="utf-8")
            tmp_path.replace(state_path)
            
            # Write overflow if present
            if overflow_urls:
                overflow_content = "\n".join(json.dumps({"url": url}) for url in overflow_urls)
                tmp_overflow = overflow_path.with_suffix(".jsonl.tmp")
                tmp_overflow.write_text(overflow_content, encoding="utf-8")
                tmp_overflow.replace(overflow_path)
            elif overflow_path.exists():
                # Remove overflow file if no longer needed
                overflow_path.unlink()
    
    def load_state(self, source_url: str) -> CrawlState | None:
        """Load crawl state for a source URL.
        
        Args:
            source_url: The source URL to load state for
            
        Returns:
            The loaded CrawlState, or None if not found
        """
        source_hash = _source_hash(source_url)
        return self.load_state_by_hash(source_hash)
    
    def load_state_by_hash(self, source_hash: str) -> CrawlState | None:
        """Load crawl state by its hash.
        
        Args:
            source_hash: The source hash to load state for
            
        Returns:
            The loaded CrawlState, or None if not found
        """
        state_path = self._get_state_path(source_hash)
        
        if not state_path.exists():
            return None
        
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            state = CrawlState.from_dict(data)
            
            # Load overflow URLs if present
            overflow_path = self._get_frontier_overflow_path(source_hash)
            if overflow_path.exists() and state.frontier_overflow_count > 0:
                overflow_lines = overflow_path.read_text(encoding="utf-8").strip().split("\n")
                for line in overflow_lines:
                    if line:
                        url_data = json.loads(line)
                        state.frontier.append(url_data["url"])
                state.frontier_overflow_count = 0  # All URLs now in memory
            
            return state
        except (json.JSONDecodeError, KeyError):
            return None
    
    def delete_state(self, source_url: str) -> bool:
        """Delete crawl state for a source URL.
        
        Args:
            source_url: The source URL to delete state for
            
        Returns:
            True if deleted, False if not found
        """
        source_hash = _source_hash(source_url)
        state_dir = self._get_state_dir(source_hash)
        
        if not state_dir.exists():
            return False
        
        # Delete all files in the state directory
        import shutil
        shutil.rmtree(state_dir)
        return True
    
    def list_crawls(self, status: str | None = None) -> List[CrawlState]:
        """List all crawl states.
        
        Args:
            status: Optional status filter ("pending", "crawling", "paused", "completed")
            
        Returns:
            List of CrawlState objects
        """
        states: List[CrawlState] = []
        
        if not self._crawls_dir.exists():
            return states
        
        for state_dir in self._crawls_dir.iterdir():
            if not state_dir.is_dir():
                continue
            
            state_path = state_dir / "crawl_state.json"
            if not state_path.exists():
                continue
            
            try:
                data = json.loads(state_path.read_text(encoding="utf-8"))
                state = CrawlState.from_dict(data)
                
                if status is None or state.status == status:
                    states.append(state)
            except (json.JSONDecodeError, KeyError):
                continue
        
        return states
    
    def state_exists(self, source_url: str) -> bool:
        """Check if crawl state exists for a source URL."""
        source_hash = _source_hash(source_url)
        return self._get_state_path(source_hash).exists()
