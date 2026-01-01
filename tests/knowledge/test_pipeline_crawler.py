"""Tests for src/knowledge/pipeline/crawler.py."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.knowledge.pipeline.crawler import (
    AcquisitionResult,
    CrawlerResult,
    _content_hash,
    _get_domain,
    acquire_single_page,
)


# --- Mock objects for testing ---


@dataclass
class MockSourceEntry:
    """Minimal mock of SourceEntry for testing."""
    
    name: str
    url: str
    source_type: str = "primary"
    status: str = "active"
    update_frequency: str = "daily"
    last_content_hash: str | None = None
    scope_boundary: str = "page"


class TestAcquisitionResult:
    """Tests for AcquisitionResult dataclass."""
    
    def test_successful_result(self):
        """Successful acquisition has expected fields."""
        result = AcquisitionResult(
            source_url="https://example.com",
            success=True,
            content_hash="abc123",
            content_path="/path/to/file.md",
            pages_acquired=1,
        )
        
        assert result.success is True
        assert result.content_hash == "abc123"
        assert result.error is None
    
    def test_failed_result(self):
        """Failed acquisition has error message."""
        result = AcquisitionResult(
            source_url="https://example.com",
            success=False,
            error="Network timeout",
        )
        
        assert result.success is False
        assert result.error == "Network timeout"
        assert result.content_hash is None


class TestCrawlerResult:
    """Tests for CrawlerResult dataclass."""
    
    def test_default_values(self):
        """CrawlerResult has correct defaults."""
        result = CrawlerResult()
        
        assert result.sources_processed == 0
        assert result.successful == []
        assert result.failed == []
        assert result.pages_total == 0
    
    def test_to_dict(self):
        """to_dict returns correct summary."""
        result = CrawlerResult(
            sources_processed=3,
            pages_total=15,
        )
        result.successful.append(AcquisitionResult("https://a.com", success=True))
        result.successful.append(AcquisitionResult("https://b.com", success=True))
        result.failed.append(AcquisitionResult("https://c.com", success=False, error="Error"))
        
        d = result.to_dict()
        
        assert d["sources_processed"] == 3
        assert d["successful"] == 2
        assert d["failed"] == 1
        assert d["pages_total"] == 15


class TestContentHash:
    """Tests for _content_hash function."""
    
    def test_consistent_hash(self):
        """Same content produces same hash."""
        content = "Hello, world!"
        
        hash1 = _content_hash(content)
        hash2 = _content_hash(content)
        
        assert hash1 == hash2
    
    def test_different_content_different_hash(self):
        """Different content produces different hash."""
        hash1 = _content_hash("Hello")
        hash2 = _content_hash("World")
        
        assert hash1 != hash2
    
    def test_is_sha256(self):
        """Hash is SHA-256 format (64 hex chars)."""
        content = "Test content"
        
        hash_value = _content_hash(content)
        
        assert len(hash_value) == 64
        assert all(c in "0123456789abcdef" for c in hash_value)
    
    def test_matches_hashlib(self):
        """Hash matches direct hashlib calculation."""
        content = "Test content"
        
        expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
        actual = _content_hash(content)
        
        assert actual == expected


class TestGetDomain:
    """Tests for _get_domain function."""
    
    def test_extracts_domain(self):
        """Extracts domain from URL."""
        assert _get_domain("https://example.com/page") == "example.com"
    
    def test_strips_www(self):
        """Removes www prefix."""
        assert _get_domain("https://www.example.com/page") == "example.com"
    
    def test_preserves_subdomain(self):
        """Keeps non-www subdomains."""
        assert _get_domain("https://blog.example.com") == "blog.example.com"
    
    def test_lowercase(self):
        """Normalizes to lowercase."""
        assert _get_domain("https://EXAMPLE.COM") == "example.com"


class TestAcquireSinglePage:
    """Tests for acquire_single_page function."""
    
    def test_successful_acquisition(self):
        """Successful fetch returns result with hash."""
        source = MockSourceEntry(name="test", url="https://example.com")
        mock_storage = MagicMock()
        mock_storage.store.return_value = MagicMock(path="/evidence/test.md")
        
        mock_document = MagicMock()
        mock_document.title = "Test Page"
        
        with patch("src.knowledge.pipeline.crawler.WebParser") as mock_parser_cls:
            mock_parser = MagicMock()
            mock_parser.extract.return_value = mock_document
            mock_parser.to_markdown.return_value = "# Test Content"
            mock_parser_cls.return_value = mock_parser
            
            result = acquire_single_page(source, mock_storage, delay_seconds=0)
        
        assert result.success is True
        assert result.content_hash is not None
        assert result.error is None
    
    def test_failed_acquisition(self):
        """Failed fetch returns error."""
        source = MockSourceEntry(name="test", url="https://example.com")
        mock_storage = MagicMock()
        
        with patch("src.knowledge.pipeline.crawler.WebParser") as mock_parser_cls:
            mock_parser = MagicMock()
            mock_parser.extract.side_effect = Exception("Network error")
            mock_parser_cls.return_value = mock_parser
            
            result = acquire_single_page(source, mock_storage, delay_seconds=0)
        
        assert result.success is False
        assert "Network error" in result.error
    
    def test_applies_politeness_delay(self):
        """Delay is applied before fetch."""
        source = MockSourceEntry(name="test", url="https://example.com")
        mock_storage = MagicMock()
        mock_storage.store.return_value = MagicMock(path="/evidence/test.md")
        
        mock_document = MagicMock()
        mock_document.title = "Test"
        
        with patch("src.knowledge.pipeline.crawler.WebParser") as mock_parser_cls:
            mock_parser = MagicMock()
            mock_parser.extract.return_value = mock_document
            mock_parser.to_markdown.return_value = "Content"
            mock_parser_cls.return_value = mock_parser
            
            with patch("time.sleep") as mock_sleep:
                acquire_single_page(source, mock_storage, delay_seconds=2.0)
                
                mock_sleep.assert_called_once_with(2.0)
    
    def test_stores_content(self):
        """Content is stored via storage."""
        source = MockSourceEntry(name="test", url="https://example.com")
        mock_storage = MagicMock()
        mock_storage.store.return_value = MagicMock(path="/evidence/test.md")
        
        mock_document = MagicMock()
        mock_document.title = "Test Title"
        
        with patch("src.knowledge.pipeline.crawler.WebParser") as mock_parser_cls:
            mock_parser = MagicMock()
            mock_parser.extract.return_value = mock_document
            mock_parser.to_markdown.return_value = "# Content"
            mock_parser_cls.return_value = mock_parser
            
            acquire_single_page(source, mock_storage, delay_seconds=0)
        
        mock_storage.store.assert_called_once()
        call_kwargs = mock_storage.store.call_args[1]
        assert call_kwargs["content"] == "# Content"
        assert call_kwargs["source_url"] == "https://example.com"
        assert call_kwargs["title"] == "Test Title"


class TestCrawlerIntegration:
    """Integration-style tests verifying crawler behavior."""
    
    def test_result_aggregates_successes_and_failures(self):
        """CrawlerResult correctly aggregates outcomes."""
        result = CrawlerResult()
        
        # Simulate mixed outcomes
        result.successful.append(AcquisitionResult("https://a.com", success=True, pages_acquired=5))
        result.successful.append(AcquisitionResult("https://b.com", success=True, pages_acquired=3))
        result.failed.append(AcquisitionResult("https://c.com", success=False, error="Timeout"))
        result.sources_processed = 3
        result.pages_total = 8
        
        d = result.to_dict()
        
        assert d["sources_processed"] == 3
        assert d["successful"] == 2
        assert d["failed"] == 1
        assert d["pages_total"] == 8
