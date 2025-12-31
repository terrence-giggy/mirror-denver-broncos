"""Unit tests for crawl state storage."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.knowledge.crawl_state import (
    CrawlState,
    CrawlStateStorage,
    _source_hash,
    _url_hash,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_storage(tmp_path: Path) -> CrawlStateStorage:
    """Create a temporary crawl state storage."""
    return CrawlStateStorage(root=tmp_path)


@pytest.fixture
def sample_crawl_state() -> CrawlState:
    """Create a sample crawl state for testing."""
    return CrawlState.create_new(
        source_url="https://www.example.com/docs/",
        scope="path",
        max_pages=1000,
        max_depth=5,
        exclude_patterns=["*.pdf", "*/archive/*"],
    )


# =============================================================================
# Hash Function Tests
# =============================================================================


class TestHashFunctions:
    """Tests for hash functions."""

    def test_source_hash_consistent(self) -> None:
        """Source hash should be consistent for same URL."""
        url = "https://example.com/page"
        assert _source_hash(url) == _source_hash(url)

    def test_source_hash_different_for_different_urls(self) -> None:
        """Different URLs should produce different hashes."""
        url1 = "https://example.com/page1"
        url2 = "https://example.com/page2"
        assert _source_hash(url1) != _source_hash(url2)

    def test_source_hash_length(self) -> None:
        """Source hash should be 16 characters."""
        url = "https://example.com/some/long/path/to/document.html"
        assert len(_source_hash(url)) == 16

    def test_url_hash_consistent(self) -> None:
        """URL hash should be consistent for same URL."""
        url = "https://example.com/page"
        assert _url_hash(url) == _url_hash(url)

    def test_url_hash_is_full_sha256(self) -> None:
        """URL hash should be full SHA-256 (64 chars)."""
        url = "https://example.com/page"
        assert len(_url_hash(url)) == 64


# =============================================================================
# CrawlState Tests
# =============================================================================


class TestCrawlState:
    """Tests for CrawlState dataclass."""

    def test_create_new_initializes_correctly(self) -> None:
        """create_new should initialize state with source URL in frontier."""
        state = CrawlState.create_new(
            source_url="https://example.com/docs/",
            scope="path",
        )
        
        assert state.source_url == "https://example.com/docs/"
        assert state.scope == "path"
        assert state.status == "pending"
        assert state.frontier == ["https://example.com/docs/"]
        assert state.visited_count == 0
        assert len(state.visited_hashes) == 0

    def test_create_new_invalid_scope_raises(self) -> None:
        """create_new should raise for invalid scope."""
        with pytest.raises(ValueError, match="Invalid scope"):
            CrawlState.create_new(
                source_url="https://example.com/",
                scope="invalid",
            )

    def test_create_new_with_all_options(self) -> None:
        """create_new should accept all configuration options."""
        state = CrawlState.create_new(
            source_url="https://example.com/",
            scope="domain",
            max_pages=500,
            max_depth=3,
            exclude_patterns=["*.pdf"],
        )
        
        assert state.scope == "domain"
        assert state.max_pages == 500
        assert state.max_depth == 3
        assert state.exclude_patterns == ["*.pdf"]

    def test_to_dict_and_from_dict_roundtrip(self, sample_crawl_state: CrawlState) -> None:
        """State should serialize and deserialize correctly."""
        # Modify state to have more data
        sample_crawl_state.mark_started()
        sample_crawl_state.mark_url_visited("https://example.com/page1")
        sample_crawl_state.discovered_count = 10
        sample_crawl_state.in_scope_count = 8
        sample_crawl_state.out_of_scope_count = 2
        
        data = sample_crawl_state.to_dict()
        restored = CrawlState.from_dict(data)
        
        assert restored.source_url == sample_crawl_state.source_url
        assert restored.source_hash == sample_crawl_state.source_hash
        assert restored.scope == sample_crawl_state.scope
        assert restored.status == sample_crawl_state.status
        assert restored.visited_count == sample_crawl_state.visited_count
        assert restored.discovered_count == 10
        assert restored.in_scope_count == 8
        assert restored.out_of_scope_count == 2

    def test_mark_started(self, sample_crawl_state: CrawlState) -> None:
        """mark_started should set status and timestamps."""
        assert sample_crawl_state.started_at is None
        
        sample_crawl_state.mark_started()
        
        assert sample_crawl_state.status == "crawling"
        assert sample_crawl_state.started_at is not None
        assert sample_crawl_state.last_activity is not None

    def test_mark_started_preserves_start_time(self, sample_crawl_state: CrawlState) -> None:
        """mark_started should preserve original start time on resume."""
        sample_crawl_state.mark_started()
        original_start = sample_crawl_state.started_at
        
        sample_crawl_state.mark_paused()
        sample_crawl_state.mark_started()
        
        assert sample_crawl_state.started_at == original_start

    def test_mark_paused(self, sample_crawl_state: CrawlState) -> None:
        """mark_paused should set status to paused."""
        sample_crawl_state.mark_started()
        sample_crawl_state.mark_paused()
        
        assert sample_crawl_state.status == "paused"

    def test_mark_completed(self, sample_crawl_state: CrawlState) -> None:
        """mark_completed should set status and completed_at."""
        sample_crawl_state.mark_started()
        sample_crawl_state.mark_completed()
        
        assert sample_crawl_state.status == "completed"
        assert sample_crawl_state.completed_at is not None

    def test_is_url_visited(self, sample_crawl_state: CrawlState) -> None:
        """is_url_visited should track visited URLs."""
        url = "https://example.com/page"
        
        assert not sample_crawl_state.is_url_visited(url)
        
        sample_crawl_state.mark_url_visited(url)
        
        assert sample_crawl_state.is_url_visited(url)

    def test_mark_url_visited_increments_count(self, sample_crawl_state: CrawlState) -> None:
        """mark_url_visited should increment visited_count."""
        assert sample_crawl_state.visited_count == 0
        
        sample_crawl_state.mark_url_visited("https://example.com/page1")
        assert sample_crawl_state.visited_count == 1
        
        sample_crawl_state.mark_url_visited("https://example.com/page2")
        assert sample_crawl_state.visited_count == 2

    def test_add_to_frontier_new_url(self, sample_crawl_state: CrawlState) -> None:
        """add_to_frontier should add new URLs."""
        url = "https://example.com/new-page"
        
        result = sample_crawl_state.add_to_frontier(url)
        
        assert result is True
        assert url in sample_crawl_state.frontier

    def test_add_to_frontier_visited_url(self, sample_crawl_state: CrawlState) -> None:
        """add_to_frontier should reject visited URLs."""
        url = "https://example.com/visited"
        sample_crawl_state.mark_url_visited(url)
        
        result = sample_crawl_state.add_to_frontier(url)
        
        assert result is False

    def test_add_to_frontier_duplicate_url(self, sample_crawl_state: CrawlState) -> None:
        """add_to_frontier should reject duplicate URLs."""
        url = "https://example.com/duplicate"
        sample_crawl_state.add_to_frontier(url)
        
        result = sample_crawl_state.add_to_frontier(url)
        
        assert result is False

    def test_pop_frontier(self, sample_crawl_state: CrawlState) -> None:
        """pop_frontier should return URLs in FIFO order."""
        # Initial frontier has source URL
        first = sample_crawl_state.pop_frontier()
        assert first == "https://www.example.com/docs/"
        
        # Add more URLs
        sample_crawl_state.add_to_frontier("https://example.com/page1")
        sample_crawl_state.add_to_frontier("https://example.com/page2")
        
        assert sample_crawl_state.pop_frontier() == "https://example.com/page1"
        assert sample_crawl_state.pop_frontier() == "https://example.com/page2"

    def test_pop_frontier_empty(self, sample_crawl_state: CrawlState) -> None:
        """pop_frontier should return None when empty."""
        sample_crawl_state.frontier.clear()
        
        assert sample_crawl_state.pop_frontier() is None

    def test_frontier_size(self, sample_crawl_state: CrawlState) -> None:
        """frontier_size should include overflow count."""
        sample_crawl_state.frontier = ["url1", "url2", "url3"]
        sample_crawl_state.frontier_overflow_count = 100
        
        assert sample_crawl_state.frontier_size == 103

    def test_is_complete_when_completed(self, sample_crawl_state: CrawlState) -> None:
        """is_complete should be True when status is completed."""
        sample_crawl_state.mark_completed()
        
        assert sample_crawl_state.is_complete is True

    def test_is_complete_when_frontier_empty(self, sample_crawl_state: CrawlState) -> None:
        """is_complete should be True when frontier is empty."""
        sample_crawl_state.frontier.clear()
        sample_crawl_state.frontier_overflow_count = 0
        
        assert sample_crawl_state.is_complete is True

    def test_is_complete_when_max_pages_reached(self, sample_crawl_state: CrawlState) -> None:
        """is_complete should be True when max_pages reached."""
        sample_crawl_state.max_pages = 10
        sample_crawl_state.visited_count = 10
        
        assert sample_crawl_state.is_complete is True


# =============================================================================
# CrawlStateStorage Tests
# =============================================================================


class TestCrawlStateStorage:
    """Tests for CrawlStateStorage."""

    def test_save_and_load_state(
        self,
        temp_storage: CrawlStateStorage,
        sample_crawl_state: CrawlState,
    ) -> None:
        """State should save and load correctly."""
        sample_crawl_state.mark_started()
        sample_crawl_state.mark_url_visited("https://example.com/page1")
        
        temp_storage.save_state(sample_crawl_state)
        loaded = temp_storage.load_state(sample_crawl_state.source_url)
        
        assert loaded is not None
        assert loaded.source_url == sample_crawl_state.source_url
        assert loaded.status == sample_crawl_state.status
        assert loaded.visited_count == sample_crawl_state.visited_count

    def test_load_state_not_found(self, temp_storage: CrawlStateStorage) -> None:
        """load_state should return None for non-existent state."""
        result = temp_storage.load_state("https://nonexistent.com/")
        
        assert result is None

    def test_load_state_by_hash(
        self,
        temp_storage: CrawlStateStorage,
        sample_crawl_state: CrawlState,
    ) -> None:
        """load_state_by_hash should work correctly."""
        temp_storage.save_state(sample_crawl_state)
        
        loaded = temp_storage.load_state_by_hash(sample_crawl_state.source_hash)
        
        assert loaded is not None
        assert loaded.source_url == sample_crawl_state.source_url

    def test_save_state_with_frontier_overflow(
        self,
        temp_storage: CrawlStateStorage,
    ) -> None:
        """Large frontiers should overflow to separate file."""
        state = CrawlState.create_new(
            source_url="https://example.com/",
            scope="host",
        )
        
        # Add more URLs than MAX_FRONTIER_IN_MEMORY
        for i in range(1500):
            state.frontier.append(f"https://example.com/page{i}")
        
        temp_storage.save_state(state)
        
        # Check overflow file exists
        overflow_path = temp_storage._get_frontier_overflow_path(state.source_hash)
        assert overflow_path.exists()
        
        # Load and verify all URLs are restored
        loaded = temp_storage.load_state(state.source_url)
        assert loaded is not None
        # Original 1 URL + 1500 added = 1501 total
        assert len(loaded.frontier) == 1501

    def test_delete_state(
        self,
        temp_storage: CrawlStateStorage,
        sample_crawl_state: CrawlState,
    ) -> None:
        """delete_state should remove the state directory."""
        temp_storage.save_state(sample_crawl_state)
        
        result = temp_storage.delete_state(sample_crawl_state.source_url)
        
        assert result is True
        assert temp_storage.load_state(sample_crawl_state.source_url) is None

    def test_delete_state_not_found(self, temp_storage: CrawlStateStorage) -> None:
        """delete_state should return False for non-existent state."""
        result = temp_storage.delete_state("https://nonexistent.com/")
        
        assert result is False

    def test_list_crawls(self, temp_storage: CrawlStateStorage) -> None:
        """list_crawls should return all crawl states."""
        state1 = CrawlState.create_new("https://example1.com/", scope="path")
        state1.mark_started()
        
        state2 = CrawlState.create_new("https://example2.com/", scope="host")
        state2.mark_completed()
        
        temp_storage.save_state(state1)
        temp_storage.save_state(state2)
        
        all_crawls = temp_storage.list_crawls()
        assert len(all_crawls) == 2

    def test_list_crawls_with_status_filter(self, temp_storage: CrawlStateStorage) -> None:
        """list_crawls should filter by status."""
        state1 = CrawlState.create_new("https://example1.com/", scope="path")
        state1.mark_started()
        
        state2 = CrawlState.create_new("https://example2.com/", scope="host")
        state2.mark_completed()
        
        temp_storage.save_state(state1)
        temp_storage.save_state(state2)
        
        crawling = temp_storage.list_crawls(status="crawling")
        assert len(crawling) == 1
        assert crawling[0].source_url == "https://example1.com/"
        
        completed = temp_storage.list_crawls(status="completed")
        assert len(completed) == 1
        assert completed[0].source_url == "https://example2.com/"

    def test_state_exists(
        self,
        temp_storage: CrawlStateStorage,
        sample_crawl_state: CrawlState,
    ) -> None:
        """state_exists should check correctly."""
        assert not temp_storage.state_exists(sample_crawl_state.source_url)
        
        temp_storage.save_state(sample_crawl_state)
        
        assert temp_storage.state_exists(sample_crawl_state.source_url)

    def test_visited_hashes_preserved(
        self,
        temp_storage: CrawlStateStorage,
        sample_crawl_state: CrawlState,
    ) -> None:
        """Visited URL hashes should be preserved across save/load."""
        sample_crawl_state.mark_url_visited("https://example.com/page1")
        sample_crawl_state.mark_url_visited("https://example.com/page2")
        sample_crawl_state.mark_url_visited("https://example.com/page3")
        
        temp_storage.save_state(sample_crawl_state)
        loaded = temp_storage.load_state(sample_crawl_state.source_url)
        
        assert loaded is not None
        assert loaded.is_url_visited("https://example.com/page1")
        assert loaded.is_url_visited("https://example.com/page2")
        assert loaded.is_url_visited("https://example.com/page3")
        assert not loaded.is_url_visited("https://example.com/page4")
