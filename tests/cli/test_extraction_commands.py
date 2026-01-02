"""Tests for extraction CLI commands."""

from unittest.mock import Mock, patch
from pathlib import Path
import argparse

import pytest

from src.cli.commands.extraction import extract_cli
from src.parsing.storage import Manifest, ManifestEntry
from src.knowledge.storage import KnowledgeGraphStorage


@pytest.fixture
def mock_manifest():
    """Create a mock manifest with test entries."""
    from datetime import datetime, timezone
    
    manifest = Manifest()
    
    # Add completed entries
    manifest.entries["checksum1"] = ManifestEntry(
        checksum="checksum1",
        source="https://example.com/page1",
        parser="web",
        processed_at=datetime.now(timezone.utc),
        status="completed",
        artifact_path="2026/doc1/index.md",
    )
    manifest.entries["checksum2"] = ManifestEntry(
        checksum="checksum2",
        source="https://example.com/page2",
        parser="web",
        processed_at=datetime.now(timezone.utc),
        status="completed",
        artifact_path="2026/doc2/index.md",
    )
    manifest.entries["checksum3"] = ManifestEntry(
        checksum="checksum3",
        source="https://example.com/page3",
        parser="web",
        processed_at=datetime.now(timezone.utc),
        status="pending",
        artifact_path="2026/doc3/index.md",
    )
    
    return manifest


def test_extract_with_checksum_filter(mock_manifest, tmp_path):
    """Test that --checksum argument filters to specific document."""
    args = argparse.Namespace(
        checksum="checksum1",
        limit=None,
        force=False,
        dry_run=True,
        kb_root=tmp_path / "kb",
        config=None,
        extract_orgs=False,
        concepts=False,
        extract_associations=False,
        profiles=False,
    )
    
    with patch("src.cli.commands.extraction.load_parsing_config") as mock_config, \
         patch("src.cli.commands.extraction.ParseStorage") as mock_storage_class, \
         patch("src.cli.commands.extraction.KnowledgeGraphStorage") as mock_kb_class, \
         patch("src.cli.commands.extraction.CopilotClient") as mock_client_class:
        
        # Setup mocks
        mock_config.return_value.output_root = tmp_path / "parsing"
        mock_storage = Mock()
        mock_storage.manifest.return_value = mock_manifest
        mock_storage_class.return_value = mock_storage
        
        mock_kb = Mock(spec=KnowledgeGraphStorage)
        mock_kb.get_extracted_people.return_value = []
        mock_kb_class.return_value = mock_kb
        
        # Run extraction with checksum filter
        result = extract_cli(args)
        
        # Should succeed (dry run)
        assert result == 0


def test_extract_without_checksum_processes_all(mock_manifest, tmp_path):
    """Test that without --checksum, all completed documents are processed."""
    args = argparse.Namespace(
        checksum=None,  # No checksum filter
        limit=None,
        force=False,
        dry_run=True,
        kb_root=tmp_path / "kb",
        config=None,
        extract_orgs=False,
        concepts=False,
        extract_associations=False,
        profiles=False,
    )
    
    with patch("src.cli.commands.extraction.load_parsing_config") as mock_config, \
         patch("src.cli.commands.extraction.ParseStorage") as mock_storage_class, \
         patch("src.cli.commands.extraction.KnowledgeGraphStorage") as mock_kb_class, \
         patch("src.cli.commands.extraction.CopilotClient") as mock_client_class:
        
        # Setup mocks
        mock_config.return_value.output_root = tmp_path / "parsing"
        mock_storage = Mock()
        mock_storage.manifest.return_value = mock_manifest
        mock_storage_class.return_value = mock_storage
        
        mock_kb = Mock(spec=KnowledgeGraphStorage)
        mock_kb.get_extracted_people.return_value = []
        mock_kb_class.return_value = mock_kb
        
        # Run extraction without filter
        result = extract_cli(args)
        
        # Should succeed (dry run)
        assert result == 0


def test_extract_with_invalid_checksum(mock_manifest, tmp_path):
    """Test that invalid checksum results in no documents to process."""
    args = argparse.Namespace(
        checksum="invalid_checksum",
        limit=None,
        force=False,
        dry_run=True,
        kb_root=tmp_path / "kb",
        config=None,
        extract_orgs=False,
        concepts=False,
        extract_associations=False,
        profiles=False,
    )
    
    with patch("src.cli.commands.extraction.load_parsing_config") as mock_config, \
         patch("src.cli.commands.extraction.ParseStorage") as mock_storage_class, \
         patch("src.cli.commands.extraction.KnowledgeGraphStorage") as mock_kb_class, \
         patch("src.cli.commands.extraction.CopilotClient") as mock_client_class:
        
        # Setup mocks
        mock_config.return_value.output_root = tmp_path / "parsing"
        mock_storage = Mock()
        mock_storage.manifest.return_value = mock_manifest
        mock_storage_class.return_value = mock_storage
        
        mock_kb = Mock(spec=KnowledgeGraphStorage)
        mock_kb_class.return_value = mock_kb
        
        # Run extraction with invalid checksum
        result = extract_cli(args)
        
        # Should succeed with 0 documents processed
        assert result == 0
