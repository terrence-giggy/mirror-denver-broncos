"""Unit tests for page registry storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.knowledge.page_registry import (
    PageBatch,
    PageEntry,
    PageRegistry,
    _content_shard,
    _url_hash,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_registry(tmp_path: Path) -> PageRegistry:
    """Create a temporary page registry."""
    return PageRegistry(root=tmp_path)


@pytest.fixture
def sample_page_entry() -> PageEntry:
    """Create a sample page entry for testing."""
    return PageEntry.create_pending(
        url="https://example.com/docs/guide",
        source_url="https://example.com/docs/",
        discovered_from="https://example.com/docs/index.html",
        link_depth=1,
    )


@pytest.fixture
def source_hash() -> str:
    """Get a sample source hash for testing."""
    from src.knowledge.crawl_state import _source_hash
    return _source_hash("https://example.com/docs/")


# =============================================================================
# Hash Function Tests
# =============================================================================


class TestHashFunctions:
    """Tests for hash functions."""

    def test_url_hash_consistent(self) -> None:
        """URL hash should be consistent for same URL."""
        url = "https://example.com/page"
        assert _url_hash(url) == _url_hash(url)

    def test_url_hash_is_full_sha256(self) -> None:
        """URL hash should be full SHA-256 (64 chars)."""
        url = "https://example.com/page"
        assert len(_url_hash(url)) == 64

    def test_content_shard(self) -> None:
        """Content shard should be first hex character."""
        # Hash starting with 'a'
        assert _content_shard("a1b2c3d4") == "a"
        assert _content_shard("f9e8d7c6") == "f"
        assert _content_shard("0123456789") == "0"


# =============================================================================
# PageEntry Tests
# =============================================================================


class TestPageEntry:
    """Tests for PageEntry dataclass."""

    def test_create_pending(self) -> None:
        """create_pending should initialize entry correctly."""
        entry = PageEntry.create_pending(
            url="https://example.com/page",
            source_url="https://example.com/",
            discovered_from="https://example.com/index.html",
            link_depth=2,
        )
        
        assert entry.url == "https://example.com/page"
        assert entry.source_url == "https://example.com/"
        assert entry.discovered_from == "https://example.com/index.html"
        assert entry.link_depth == 2
        assert entry.status == "pending"
        assert entry.url_hash == _url_hash("https://example.com/page")

    def test_create_pending_defaults(self) -> None:
        """create_pending should have sensible defaults."""
        entry = PageEntry.create_pending(
            url="https://example.com/page",
            source_url="https://example.com/",
        )
        
        assert entry.discovered_from is None
        assert entry.link_depth == 0

    def test_to_dict_and_from_dict_roundtrip(self, sample_page_entry: PageEntry) -> None:
        """Page entry should serialize and deserialize correctly."""
        data = sample_page_entry.to_dict()
        restored = PageEntry.from_dict(data)
        
        assert restored.url == sample_page_entry.url
        assert restored.url_hash == sample_page_entry.url_hash
        assert restored.source_url == sample_page_entry.source_url
        assert restored.discovered_from == sample_page_entry.discovered_from
        assert restored.link_depth == sample_page_entry.link_depth
        assert restored.status == sample_page_entry.status

    def test_mark_fetched(self, sample_page_entry: PageEntry) -> None:
        """mark_fetched should update all fetch fields."""
        sample_page_entry.mark_fetched(
            http_status=200,
            content_type="text/html",
            content_hash="abc123def456",
            content_path="crawls/abc/content/a/abc123def456/content.md",
            content_size=1024,
            extracted_chars=500,
            title="Example Page",
            outgoing_links_count=10,
            outgoing_links_in_scope=8,
        )
        
        assert sample_page_entry.status == "fetched"
        assert sample_page_entry.fetched_at is not None
        assert sample_page_entry.http_status == 200
        assert sample_page_entry.content_type == "text/html"
        assert sample_page_entry.content_hash == "abc123def456"
        assert sample_page_entry.content_size == 1024
        assert sample_page_entry.extracted_chars == 500
        assert sample_page_entry.title == "Example Page"
        assert sample_page_entry.outgoing_links_count == 10
        assert sample_page_entry.outgoing_links_in_scope == 8

    def test_mark_failed(self, sample_page_entry: PageEntry) -> None:
        """mark_failed should update error fields."""
        sample_page_entry.mark_failed(
            error_message="Connection timeout",
            http_status=None,
        )
        
        assert sample_page_entry.status == "failed"
        assert sample_page_entry.fetched_at is not None
        assert sample_page_entry.error_message == "Connection timeout"

    def test_mark_failed_with_http_status(self, sample_page_entry: PageEntry) -> None:
        """mark_failed should capture HTTP error status."""
        sample_page_entry.mark_failed(
            error_message="Not Found",
            http_status=404,
        )
        
        assert sample_page_entry.status == "failed"
        assert sample_page_entry.http_status == 404

    def test_mark_skipped(self, sample_page_entry: PageEntry) -> None:
        """mark_skipped should update status and reason."""
        sample_page_entry.mark_skipped(reason="Blocked by robots.txt")
        
        assert sample_page_entry.status == "skipped"
        assert sample_page_entry.fetched_at is not None
        assert sample_page_entry.error_message == "Blocked by robots.txt"


# =============================================================================
# PageBatch Tests
# =============================================================================


class TestPageBatch:
    """Tests for PageBatch dataclass."""

    def test_batch_creation(self) -> None:
        """Batch should initialize correctly."""
        batch = PageBatch(
            batch_number=0,
            source_hash="abc123",
        )
        
        assert batch.batch_number == 0
        assert batch.source_hash == "abc123"
        assert batch.pages == []
        assert not batch.is_full

    def test_batch_add_page(self, sample_page_entry: PageEntry) -> None:
        """add_page should add pages to batch."""
        batch = PageBatch(batch_number=0, source_hash="abc123")
        
        result = batch.add_page(sample_page_entry)
        
        assert result is True
        assert len(batch.pages) == 1

    def test_batch_is_full(self) -> None:
        """is_full should be True when at capacity."""
        batch = PageBatch(batch_number=0, source_hash="abc123", max_pages=2)
        
        assert not batch.is_full
        
        batch.add_page(PageEntry.create_pending("https://example.com/1", "https://example.com/"))
        assert not batch.is_full
        
        batch.add_page(PageEntry.create_pending("https://example.com/2", "https://example.com/"))
        assert batch.is_full

    def test_batch_add_page_when_full(self) -> None:
        """add_page should return False when batch is full."""
        batch = PageBatch(batch_number=0, source_hash="abc123", max_pages=1)
        batch.add_page(PageEntry.create_pending("https://example.com/1", "https://example.com/"))
        
        result = batch.add_page(
            PageEntry.create_pending("https://example.com/2", "https://example.com/")
        )
        
        assert result is False
        assert len(batch.pages) == 1

    def test_batch_to_dict_and_from_dict(self, sample_page_entry: PageEntry) -> None:
        """Batch should serialize and deserialize correctly."""
        batch = PageBatch(batch_number=5, source_hash="abc123")
        batch.add_page(sample_page_entry)
        
        data = batch.to_dict()
        restored = PageBatch.from_dict(data)
        
        assert restored.batch_number == 5
        assert restored.source_hash == "abc123"
        assert len(restored.pages) == 1
        assert restored.pages[0].url == sample_page_entry.url


# =============================================================================
# PageRegistry Tests
# =============================================================================


class TestPageRegistry:
    """Tests for PageRegistry."""

    def test_save_and_get_page(
        self,
        temp_registry: PageRegistry,
        sample_page_entry: PageEntry,
        source_hash: str,
    ) -> None:
        """Page should save and load correctly."""
        temp_registry.save_page(sample_page_entry, source_hash)
        
        loaded = temp_registry.get_page(sample_page_entry.url, source_hash)
        
        assert loaded is not None
        assert loaded.url == sample_page_entry.url
        assert loaded.status == sample_page_entry.status

    def test_get_page_not_found(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """get_page should return None for non-existent page."""
        result = temp_registry.get_page("https://nonexistent.com/page", source_hash)
        
        assert result is None

    def test_get_page_by_hash(
        self,
        temp_registry: PageRegistry,
        sample_page_entry: PageEntry,
        source_hash: str,
    ) -> None:
        """get_page_by_hash should work correctly."""
        temp_registry.save_page(sample_page_entry, source_hash)
        
        loaded = temp_registry.get_page_by_hash(sample_page_entry.url_hash, source_hash)
        
        assert loaded is not None
        assert loaded.url == sample_page_entry.url

    def test_save_page_update_existing(
        self,
        temp_registry: PageRegistry,
        sample_page_entry: PageEntry,
        source_hash: str,
    ) -> None:
        """Saving existing page should update it."""
        temp_registry.save_page(sample_page_entry, source_hash)
        
        # Update the page
        sample_page_entry.mark_fetched(
            http_status=200,
            content_type="text/html",
            content_hash="updated_hash",
            content_path="path/to/content",
            content_size=2048,
        )
        temp_registry.save_page(sample_page_entry, source_hash)
        
        loaded = temp_registry.get_page(sample_page_entry.url, source_hash)
        
        assert loaded is not None
        assert loaded.status == "fetched"
        assert loaded.content_hash == "updated_hash"

    def test_save_pages_batch(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """save_pages_batch should save multiple pages efficiently."""
        pages = [
            PageEntry.create_pending(f"https://example.com/page{i}", "https://example.com/")
            for i in range(10)
        ]
        
        temp_registry.save_pages_batch(pages, source_hash)
        
        # Verify all pages saved
        for page in pages:
            loaded = temp_registry.get_page(page.url, source_hash)
            assert loaded is not None
            assert loaded.url == page.url

    def test_save_pages_batch_empty(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """save_pages_batch should handle empty list."""
        temp_registry.save_pages_batch([], source_hash)
        # Should not raise

    def test_batch_overflow_creates_new_batch(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """Pages should overflow into new batches."""
        # Save more pages than batch size
        pages = [
            PageEntry.create_pending(f"https://example.com/page{i}", "https://example.com/")
            for i in range(600)  # More than BATCH_SIZE (500)
        ]
        
        temp_registry.save_pages_batch(pages, source_hash)
        
        # Check that two batch files exist
        batch0_path = temp_registry._get_batch_path(source_hash, 0)
        batch1_path = temp_registry._get_batch_path(source_hash, 1)
        
        assert batch0_path.exists()
        assert batch1_path.exists()
        
        # Verify all pages retrievable
        for i in range(600):
            loaded = temp_registry.get_page(f"https://example.com/page{i}", source_hash)
            assert loaded is not None

    def test_iterate_pages(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """iterate_pages should yield all pages."""
        pages = [
            PageEntry.create_pending(f"https://example.com/page{i}", "https://example.com/")
            for i in range(5)
        ]
        temp_registry.save_pages_batch(pages, source_hash)
        
        iterated = list(temp_registry.iterate_pages(source_hash))
        
        assert len(iterated) == 5
        urls = {p.url for p in iterated}
        assert urls == {f"https://example.com/page{i}" for i in range(5)}

    def test_iterate_pages_across_batches(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """iterate_pages should yield pages from all batches."""
        pages = [
            PageEntry.create_pending(f"https://example.com/page{i}", "https://example.com/")
            for i in range(600)
        ]
        temp_registry.save_pages_batch(pages, source_hash)
        
        iterated = list(temp_registry.iterate_pages(source_hash))
        
        assert len(iterated) == 600

    def test_get_pages_by_status(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """get_pages_by_status should filter correctly."""
        pending = PageEntry.create_pending("https://example.com/pending", "https://example.com/")
        
        fetched = PageEntry.create_pending("https://example.com/fetched", "https://example.com/")
        fetched.mark_fetched(
            http_status=200,
            content_type="text/html",
            content_hash="hash",
            content_path="path",
            content_size=100,
        )
        
        failed = PageEntry.create_pending("https://example.com/failed", "https://example.com/")
        failed.mark_failed("Error")
        
        temp_registry.save_pages_batch([pending, fetched, failed], source_hash)
        
        pending_pages = temp_registry.get_pages_by_status(source_hash, "pending")
        assert len(pending_pages) == 1
        assert pending_pages[0].url == "https://example.com/pending"
        
        fetched_pages = temp_registry.get_pages_by_status(source_hash, "fetched")
        assert len(fetched_pages) == 1
        
        failed_pages = temp_registry.get_pages_by_status(source_hash, "failed")
        assert len(failed_pages) == 1

    def test_get_stats(
        self,
        temp_registry: PageRegistry,
        source_hash: str,
    ) -> None:
        """get_stats should return correct counts."""
        pages = [
            PageEntry.create_pending("https://example.com/1", "https://example.com/"),
            PageEntry.create_pending("https://example.com/2", "https://example.com/"),
        ]
        
        # Mark one as fetched
        pages[1].mark_fetched(
            http_status=200,
            content_type="text/html",
            content_hash="hash",
            content_path="path",
            content_size=100,
        )
        
        temp_registry.save_pages_batch(pages, source_hash)
        
        stats = temp_registry.get_stats(source_hash)
        
        assert stats["total"] == 2
        assert stats["pending"] == 1
        assert stats["fetched"] == 1
        assert stats["failed"] == 0
        assert stats["skipped"] == 0

    def test_page_exists(
        self,
        temp_registry: PageRegistry,
        sample_page_entry: PageEntry,
        source_hash: str,
    ) -> None:
        """page_exists should check correctly."""
        assert not temp_registry.page_exists(sample_page_entry.url, source_hash)
        
        temp_registry.save_page(sample_page_entry, source_hash)
        
        assert temp_registry.page_exists(sample_page_entry.url, source_hash)

    def test_registry_index_updated(
        self,
        temp_registry: PageRegistry,
        sample_page_entry: PageEntry,
        source_hash: str,
    ) -> None:
        """Registry index should be updated when pages are saved."""
        temp_registry.save_page(sample_page_entry, source_hash)
        
        registry_path = temp_registry._get_registry_path(source_hash)
        assert registry_path.exists()
        
        data = json.loads(registry_path.read_text())
        assert "url_to_batch" in data
        assert sample_page_entry.url_hash in data["url_to_batch"]
