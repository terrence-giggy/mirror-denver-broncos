"""Tests for organization extraction logic."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from src.integrations.github.models import GitHubModelsClient, ChatCompletionResponse, Choice, ChatMessage
from src.knowledge.storage import KnowledgeGraphStorage, ExtractedOrganizations
from src.knowledge.extraction import OrganizationExtractor, process_document_organizations
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


def test_org_extractor_extract_organizations(mock_client):
    extractor = OrganizationExtractor(mock_client)
    
    # Mock response
    mock_message = ChatMessage(role="assistant", content='["Acme Corp", "Global Tech"]')
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    orgs = extractor.extract_organizations("Acme Corp acquired Global Tech.")
    
    assert orgs == ["Acme Corp", "Global Tech"]
    mock_client.chat_completion.assert_called_once()


def test_process_document_organizations(mock_client, mock_storage, mock_kb_storage):
    extractor = OrganizationExtractor(mock_client)
    
    # Setup artifact - create a single file
    checksum = "org123checksum"
    doc_path = mock_storage.root / "doc.md"
    doc_path.write_text("Acme Corp is a great company.", encoding="utf-8")
    
    entry = ManifestEntry(
        source="test.pdf",
        checksum=checksum,
        parser="pdf",
        artifact_path="doc.md",
        processed_at=datetime.now(timezone.utc),
        metadata={"artifact_type": "file"},
    )
    
    # Mock extraction
    mock_message = ChatMessage(role="assistant", content='["Acme Corp"]')
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    orgs = process_document_organizations(entry, mock_storage, mock_kb_storage, extractor)
    
    assert orgs == ["Acme Corp"]
    
    # Verify storage
    stored = mock_kb_storage.get_extracted_organizations(checksum)
    assert stored is not None
    assert stored.organizations == ["Acme Corp"]
    assert stored.source_checksum == checksum


def test_kb_storage_organizations_persistence(tmp_path):
    storage = KnowledgeGraphStorage(tmp_path / "kb")
    checksum = "test-org-checksum"
    orgs = ["Org A", "Org B"]
    
    storage.save_extracted_organizations(checksum, orgs)
    
    loaded = storage.get_extracted_organizations(checksum)
    assert loaded is not None
    assert loaded.organizations == orgs
    assert loaded.source_checksum == checksum
