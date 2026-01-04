"""Tests for person extraction logic."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from src.integrations.github.models import GitHubModelsClient, ChatCompletionResponse, Choice, ChatMessage
from src.knowledge.storage import KnowledgeGraphStorage, ExtractedPeople
from src.knowledge.extraction import PersonExtractor, process_document
from src.parsing.storage import ManifestEntry, ParseStorage


@pytest.fixture
def mock_client():
    client = Mock(spec=GitHubModelsClient)
    return client


@pytest.fixture
def mock_storage(tmp_path):
    storage = Mock(spec=ParseStorage)
    storage.root = tmp_path / "parsing"
    storage.root.mkdir()
    return storage


@pytest.fixture
def mock_kb_storage(tmp_path):
    return KnowledgeGraphStorage(tmp_path / "kb")


def test_extractor_extract_people(mock_client):
    extractor = PersonExtractor(mock_client)
    
    # Mock response
    mock_message = ChatMessage(role="assistant", content='["John Doe", "Jane Smith"]')
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    people = extractor.extract_people("John Doe met Jane Smith.")
    
    assert people == ["John Doe", "Jane Smith"]
    mock_client.chat_completion.assert_called_once()


def test_extractor_empty_response(mock_client):
    extractor = PersonExtractor(mock_client)
    
    mock_message = ChatMessage(role="assistant", content='[]')
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    people = extractor.extract_people("No one here.")
    
    assert people == []


def test_extractor_handles_markdown_json(mock_client):
    extractor = PersonExtractor(mock_client)
    
    content = '```json\n["Alice", "Bob"]\n```'
    mock_message = ChatMessage(role="assistant", content=content)
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    people = extractor.extract_people("Alice and Bob.")
    
    assert people == ["Alice", "Bob"]


def test_process_document(mock_client, mock_storage, mock_kb_storage):
    extractor = PersonExtractor(mock_client)
    
    # Setup artifact - create a page-directory structure
    checksum = "abc123checksum"
    doc_dir = mock_storage.root / "2023" / "doc-abc123checksum"
    doc_dir.mkdir(parents=True)
    
    # Create index.md
    index_path = doc_dir / "index.md"
    index_path.write_text("# Index\n", encoding="utf-8")
    
    # Create page files
    page1 = doc_dir / "page-001.md"
    page1.write_text("Content with John Doe.", encoding="utf-8")
    page2 = doc_dir / "page-002.md"
    page2.write_text("More content with Jane Smith.", encoding="utf-8")
    
    entry = ManifestEntry(
        source="test.pdf",
        checksum=checksum,
        parser="pdf",
        artifact_path=str(index_path.relative_to(mock_storage.root)),
        processed_at=datetime.now(timezone.utc),
        metadata={"artifact_type": "page-directory"},
    )
    
    # Mock extraction
    mock_message = ChatMessage(role="assistant", content='["John Doe", "Jane Smith"]')
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    people = process_document(entry, mock_storage, mock_kb_storage, extractor)
    
    assert people == ["John Doe", "Jane Smith"]
    
    # Verify storage
    stored = mock_kb_storage.get_extracted_people(checksum)
    assert stored is not None
    assert stored.people == ["John Doe", "Jane Smith"]
    assert stored.source_checksum == checksum


def test_kb_storage_persistence(tmp_path):
    storage = KnowledgeGraphStorage(tmp_path / "kb")
    checksum = "test-checksum"
    people = ["Person A", "Person B"]
    
    storage.save_extracted_people(checksum, people)
    
    loaded = storage.get_extracted_people(checksum)
    assert loaded is not None
    assert loaded.people == people
    assert loaded.source_checksum == checksum
