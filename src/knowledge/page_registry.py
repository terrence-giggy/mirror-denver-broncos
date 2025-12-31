"""Page registry for tracking crawled pages.

This module provides data structures and storage for tracking individual pages
discovered and fetched during a site-wide crawl. Pages are stored in batched
files to handle large crawls while staying under GitHub's file limits.

The PageEntry captures:
- URL and relationship to source
- Fetch status and timing
- Content metadata (hash, size, path)
- Page metadata (title, links)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, List

from src import paths
from src.parsing import utils

if TYPE_CHECKING:
    from src.integrations.github.storage import GitHubStorageClient


def _url_hash(url: str) -> str:
    """Generate a consistent SHA-256 hash for a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _content_shard(content_hash: str) -> str:
    """Get the shard directory for a content hash (first hex char)."""
    return content_hash[0]


@dataclass
class PageEntry:
    """Metadata for a single crawled page.
    
    Attributes:
        url: Full URL of the page
        url_hash: SHA-256 hash of the URL (for deduplication and filenames)
        source_url: The source URL this page belongs to (crawl boundary)
        discovered_from: URL that linked to this page (None for seed URL)
        link_depth: Number of hops from source URL (0 for seed)
        status: Page status - "pending", "fetched", "failed", "skipped"
        discovered_at: When the URL was first discovered
        fetched_at: When the page was successfully fetched
        http_status: HTTP response status code
        content_type: HTTP Content-Type header value
        error_message: Error message if fetch failed
        content_hash: SHA-256 hash of the fetched content
        content_path: Relative path to stored content file
        content_size: Size of content in bytes
        extracted_chars: Number of characters extracted from content
        title: Page title extracted from HTML
        outgoing_links_count: Total links found on this page
        outgoing_links_in_scope: Links within crawl scope
    """
    
    url: str
    url_hash: str
    
    # Relationship to source
    source_url: str
    discovered_from: str | None = None
    link_depth: int = 0
    
    # Status
    status: str = "pending"  # "pending" | "fetched" | "failed" | "skipped"
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    fetched_at: datetime | None = None
    
    # Fetch details
    http_status: int | None = None
    content_type: str | None = None
    error_message: str | None = None
    
    # Content (if fetched)
    content_hash: str | None = None
    content_path: str | None = None
    content_size: int | None = None
    extracted_chars: int | None = None
    
    # Page metadata
    title: str | None = None
    outgoing_links_count: int | None = None
    outgoing_links_in_scope: int | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize page entry to dictionary."""
        return {
            "url": self.url,
            "url_hash": self.url_hash,
            "source_url": self.source_url,
            "discovered_from": self.discovered_from,
            "link_depth": self.link_depth,
            "status": self.status,
            "discovered_at": self.discovered_at.isoformat(),
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "error_message": self.error_message,
            "content_hash": self.content_hash,
            "content_path": self.content_path,
            "content_size": self.content_size,
            "extracted_chars": self.extracted_chars,
            "title": self.title,
            "outgoing_links_count": self.outgoing_links_count,
            "outgoing_links_in_scope": self.outgoing_links_in_scope,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageEntry":
        """Deserialize page entry from dictionary."""
        fetched_at = None
        if data.get("fetched_at"):
            fetched_at = datetime.fromisoformat(data["fetched_at"])
        
        discovered_at = datetime.now(timezone.utc)
        if data.get("discovered_at"):
            discovered_at = datetime.fromisoformat(data["discovered_at"])
        
        return cls(
            url=data["url"],
            url_hash=data["url_hash"],
            source_url=data["source_url"],
            discovered_from=data.get("discovered_from"),
            link_depth=data.get("link_depth", 0),
            status=data.get("status", "pending"),
            discovered_at=discovered_at,
            fetched_at=fetched_at,
            http_status=data.get("http_status"),
            content_type=data.get("content_type"),
            error_message=data.get("error_message"),
            content_hash=data.get("content_hash"),
            content_path=data.get("content_path"),
            content_size=data.get("content_size"),
            extracted_chars=data.get("extracted_chars"),
            title=data.get("title"),
            outgoing_links_count=data.get("outgoing_links_count"),
            outgoing_links_in_scope=data.get("outgoing_links_in_scope"),
        )
    
    @classmethod
    def create_pending(
        cls,
        url: str,
        source_url: str,
        discovered_from: str | None = None,
        link_depth: int = 0,
    ) -> "PageEntry":
        """Create a new pending page entry.
        
        Args:
            url: The page URL
            source_url: The source URL (crawl boundary)
            discovered_from: URL that linked to this page
            link_depth: Number of hops from source
            
        Returns:
            A new PageEntry in pending status
        """
        return cls(
            url=url,
            url_hash=_url_hash(url),
            source_url=source_url,
            discovered_from=discovered_from,
            link_depth=link_depth,
            status="pending",
        )
    
    def mark_fetched(
        self,
        http_status: int,
        content_type: str,
        content_hash: str,
        content_path: str,
        content_size: int,
        extracted_chars: int | None = None,
        title: str | None = None,
        outgoing_links_count: int | None = None,
        outgoing_links_in_scope: int | None = None,
    ) -> None:
        """Mark the page as successfully fetched."""
        self.status = "fetched"
        self.fetched_at = datetime.now(timezone.utc)
        self.http_status = http_status
        self.content_type = content_type
        self.content_hash = content_hash
        self.content_path = content_path
        self.content_size = content_size
        self.extracted_chars = extracted_chars
        self.title = title
        self.outgoing_links_count = outgoing_links_count
        self.outgoing_links_in_scope = outgoing_links_in_scope
    
    def mark_failed(self, error_message: str, http_status: int | None = None) -> None:
        """Mark the page as failed to fetch."""
        self.status = "failed"
        self.fetched_at = datetime.now(timezone.utc)
        self.error_message = error_message
        self.http_status = http_status
    
    def mark_skipped(self, reason: str) -> None:
        """Mark the page as skipped (robots.txt, patterns, etc.)."""
        self.status = "skipped"
        self.fetched_at = datetime.now(timezone.utc)
        self.error_message = reason


@dataclass
class PageBatch:
    """A batch of page entries stored in a single file.
    
    Pages are batched to avoid creating too many small files and to stay
    under GitHub's directory file limits.
    """
    
    batch_number: int
    source_hash: str
    pages: List[PageEntry] = field(default_factory=list)
    
    # Batch capacity
    max_pages: int = 500
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize batch to dictionary."""
        return {
            "batch_number": self.batch_number,
            "source_hash": self.source_hash,
            "page_count": len(self.pages),
            "pages": [p.to_dict() for p in self.pages],
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PageBatch":
        """Deserialize batch from dictionary."""
        return cls(
            batch_number=data["batch_number"],
            source_hash=data["source_hash"],
            pages=[PageEntry.from_dict(p) for p in data.get("pages", [])],
        )
    
    @property
    def is_full(self) -> bool:
        """Check if the batch is at capacity."""
        return len(self.pages) >= self.max_pages
    
    def add_page(self, page: PageEntry) -> bool:
        """Add a page to the batch.
        
        Returns:
            True if added, False if batch is full
        """
        if self.is_full:
            return False
        self.pages.append(page)
        return True


class PageRegistry:
    """Manages storage of page entries for crawls.
    
    Pages are stored in batched files to handle large crawls. The registry
    maintains an index mapping URL hashes to batch numbers for fast lookups.
    
    Storage layout:
        knowledge-graph/crawls/{source_hash}/
            registry.json          # Index: url_hash -> batch_number
            pages_0000.json        # Batch 0: pages 0-499
            pages_0001.json        # Batch 1: pages 500-999
            ...
    """
    
    BATCH_SIZE = 500
    
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
    
    def _get_crawl_dir(self, source_hash: str) -> Path:
        """Get the directory for a crawl's pages."""
        return self._crawls_dir / source_hash
    
    def _get_registry_path(self, source_hash: str) -> Path:
        """Get the path for the page registry index."""
        return self._get_crawl_dir(source_hash) / "registry.json"
    
    def _get_batch_path(self, source_hash: str, batch_number: int) -> Path:
        """Get the path for a page batch file."""
        return self._get_crawl_dir(source_hash) / f"pages_{batch_number:04d}.json"
    
    def _load_registry_index(self, source_hash: str) -> dict[str, int]:
        """Load the registry index mapping URL hashes to batch numbers."""
        registry_path = self._get_registry_path(source_hash)
        
        if not registry_path.exists():
            return {}
        
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            return data.get("url_to_batch", {})
        except (json.JSONDecodeError, KeyError):
            return {}
    
    def _save_registry_index(
        self,
        source_hash: str,
        index: dict[str, int],
        current_batch: int,
    ) -> None:
        """Save the registry index."""
        registry_path = self._get_registry_path(source_hash)
        utils.ensure_directory(registry_path.parent)
        
        data = {
            "version": 1,
            "source_hash": source_hash,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "total_pages": len(index),
            "current_batch": current_batch,
            "url_to_batch": index,
        }
        content = json.dumps(data, indent=2)
        
        if self._github_client:
            rel_path = self._get_relative_path(registry_path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Update page registry for {source_hash[:8]}",
            )
        else:
            tmp_path = registry_path.with_suffix(".json.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(registry_path)
    
    def _load_batch(self, source_hash: str, batch_number: int) -> PageBatch | None:
        """Load a page batch from storage."""
        batch_path = self._get_batch_path(source_hash, batch_number)
        
        if not batch_path.exists():
            return None
        
        try:
            data = json.loads(batch_path.read_text(encoding="utf-8"))
            return PageBatch.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None
    
    def _save_batch(self, source_hash: str, batch: PageBatch) -> None:
        """Save a page batch to storage."""
        batch_path = self._get_batch_path(source_hash, batch.batch_number)
        utils.ensure_directory(batch_path.parent)
        
        content = json.dumps(batch.to_dict(), indent=2)
        
        if self._github_client:
            rel_path = self._get_relative_path(batch_path)
            self._github_client.commit_file(
                path=rel_path,
                content=content,
                message=f"Update page batch {batch.batch_number} for {source_hash[:8]}",
            )
        else:
            tmp_path = batch_path.with_suffix(".json.tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(batch_path)
    
    def _get_current_batch_number(self, source_hash: str) -> int:
        """Get the current batch number from the registry."""
        registry_path = self._get_registry_path(source_hash)
        
        if not registry_path.exists():
            return 0
        
        try:
            data = json.loads(registry_path.read_text(encoding="utf-8"))
            return data.get("current_batch", 0)
        except (json.JSONDecodeError, KeyError):
            return 0
    
    def save_page(self, page: PageEntry, source_hash: str) -> None:
        """Save a page entry to the registry.
        
        The page is added to the current batch. If the batch is full,
        a new batch is created.
        
        Args:
            page: The PageEntry to save
            source_hash: The source hash this page belongs to
        """
        crawl_dir = self._get_crawl_dir(source_hash)
        utils.ensure_directory(crawl_dir)
        
        # Load registry index
        index = self._load_registry_index(source_hash)
        current_batch_num = self._get_current_batch_number(source_hash)
        
        # Check if page already exists
        if page.url_hash in index:
            # Update existing page in its batch
            batch_num = index[page.url_hash]
            batch = self._load_batch(source_hash, batch_num)
            if batch:
                for i, p in enumerate(batch.pages):
                    if p.url_hash == page.url_hash:
                        batch.pages[i] = page
                        break
                self._save_batch(source_hash, batch)
            return
        
        # Load or create current batch
        batch = self._load_batch(source_hash, current_batch_num)
        if batch is None:
            batch = PageBatch(
                batch_number=current_batch_num,
                source_hash=source_hash,
            )
        
        # Check if batch is full
        if batch.is_full:
            current_batch_num += 1
            batch = PageBatch(
                batch_number=current_batch_num,
                source_hash=source_hash,
            )
        
        # Add page to batch
        batch.add_page(page)
        index[page.url_hash] = current_batch_num
        
        # Save batch and index
        if self._github_client:
            # Batch commit for efficiency
            batch_path = self._get_batch_path(source_hash, batch.batch_number)
            registry_path = self._get_registry_path(source_hash)
            
            batch_content = json.dumps(batch.to_dict(), indent=2)
            index_data = {
                "version": 1,
                "source_hash": source_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_pages": len(index),
                "current_batch": current_batch_num,
                "url_to_batch": index,
            }
            index_content = json.dumps(index_data, indent=2)
            
            self._github_client.commit_files_batch(
                files=[
                    (self._get_relative_path(batch_path), batch_content),
                    (self._get_relative_path(registry_path), index_content),
                ],
                message=f"Add page to registry for {source_hash[:8]}",
            )
        else:
            self._save_batch(source_hash, batch)
            self._save_registry_index(source_hash, index, current_batch_num)
    
    def save_pages_batch(self, pages: List[PageEntry], source_hash: str) -> None:
        """Save multiple pages to the registry efficiently.
        
        This is more efficient than calling save_page repeatedly as it
        minimizes file I/O and GitHub API calls.
        
        Args:
            pages: List of PageEntry objects to save
            source_hash: The source hash these pages belong to
        """
        if not pages:
            return
        
        crawl_dir = self._get_crawl_dir(source_hash)
        utils.ensure_directory(crawl_dir)
        
        # Load registry index
        index = self._load_registry_index(source_hash)
        current_batch_num = self._get_current_batch_number(source_hash)
        
        # Load current batch
        batch = self._load_batch(source_hash, current_batch_num)
        if batch is None:
            batch = PageBatch(
                batch_number=current_batch_num,
                source_hash=source_hash,
            )
        
        # Track modified batches
        modified_batches: dict[int, PageBatch] = {current_batch_num: batch}
        
        for page in pages:
            # Check if page already exists (update case)
            if page.url_hash in index:
                existing_batch_num = index[page.url_hash]
                if existing_batch_num not in modified_batches:
                    existing_batch = self._load_batch(source_hash, existing_batch_num)
                    if existing_batch:
                        modified_batches[existing_batch_num] = existing_batch
                
                if existing_batch_num in modified_batches:
                    for i, p in enumerate(modified_batches[existing_batch_num].pages):
                        if p.url_hash == page.url_hash:
                            modified_batches[existing_batch_num].pages[i] = page
                            break
                continue
            
            # Add to current batch or create new one
            if batch.is_full:
                current_batch_num += 1
                batch = PageBatch(
                    batch_number=current_batch_num,
                    source_hash=source_hash,
                )
                modified_batches[current_batch_num] = batch
            
            batch.add_page(page)
            index[page.url_hash] = current_batch_num
        
        # Save all modified batches
        if self._github_client:
            files_to_commit = []
            
            for batch_num, batch_obj in modified_batches.items():
                batch_path = self._get_batch_path(source_hash, batch_num)
                batch_content = json.dumps(batch_obj.to_dict(), indent=2)
                files_to_commit.append((self._get_relative_path(batch_path), batch_content))
            
            # Add registry index
            registry_path = self._get_registry_path(source_hash)
            index_data = {
                "version": 1,
                "source_hash": source_hash,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "total_pages": len(index),
                "current_batch": current_batch_num,
                "url_to_batch": index,
            }
            files_to_commit.append((self._get_relative_path(registry_path), json.dumps(index_data, indent=2)))
            
            self._github_client.commit_files_batch(
                files=files_to_commit,
                message=f"Batch update page registry for {source_hash[:8]}",
            )
        else:
            for batch_obj in modified_batches.values():
                self._save_batch(source_hash, batch_obj)
            self._save_registry_index(source_hash, index, current_batch_num)
    
    def get_page(self, url: str, source_hash: str) -> PageEntry | None:
        """Get a page entry by URL.
        
        Args:
            url: The page URL
            source_hash: The source hash this page belongs to
            
        Returns:
            The PageEntry if found, None otherwise
        """
        url_hash = _url_hash(url)
        return self.get_page_by_hash(url_hash, source_hash)
    
    def get_page_by_hash(self, url_hash: str, source_hash: str) -> PageEntry | None:
        """Get a page entry by URL hash.
        
        Args:
            url_hash: The URL hash
            source_hash: The source hash this page belongs to
            
        Returns:
            The PageEntry if found, None otherwise
        """
        index = self._load_registry_index(source_hash)
        
        if url_hash not in index:
            return None
        
        batch_num = index[url_hash]
        batch = self._load_batch(source_hash, batch_num)
        
        if batch is None:
            return None
        
        for page in batch.pages:
            if page.url_hash == url_hash:
                return page
        
        return None
    
    def iterate_pages(self, source_hash: str) -> Iterator[PageEntry]:
        """Iterate over all pages for a source.
        
        Args:
            source_hash: The source hash to iterate pages for
            
        Yields:
            PageEntry objects for each page
        """
        current_batch = self._get_current_batch_number(source_hash)
        
        for batch_num in range(current_batch + 1):
            batch = self._load_batch(source_hash, batch_num)
            if batch:
                yield from batch.pages
    
    def get_pages_by_status(
        self,
        source_hash: str,
        status: str,
    ) -> List[PageEntry]:
        """Get all pages with a specific status.
        
        Args:
            source_hash: The source hash to filter by
            status: The status to filter by ("pending", "fetched", "failed", "skipped")
            
        Returns:
            List of PageEntry objects matching the status
        """
        return [p for p in self.iterate_pages(source_hash) if p.status == status]
    
    def get_stats(self, source_hash: str) -> dict[str, int]:
        """Get statistics for a crawl.
        
        Args:
            source_hash: The source hash to get stats for
            
        Returns:
            Dictionary with counts by status
        """
        stats = {
            "total": 0,
            "pending": 0,
            "fetched": 0,
            "failed": 0,
            "skipped": 0,
        }
        
        for page in self.iterate_pages(source_hash):
            stats["total"] += 1
            if page.status in stats:
                stats[page.status] += 1
        
        return stats
    
    def page_exists(self, url: str, source_hash: str) -> bool:
        """Check if a page exists in the registry."""
        url_hash = _url_hash(url)
        index = self._load_registry_index(source_hash)
        return url_hash in index
