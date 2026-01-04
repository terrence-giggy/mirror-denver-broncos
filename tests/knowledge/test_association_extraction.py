
import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest
from src.integrations.github.models import GitHubModelsClient
from src.knowledge.extraction import AssociationExtractor
from src.knowledge.storage import EntityAssociation, KnowledgeGraphStorage


@pytest.fixture
def mock_client():
    client = MagicMock(spec=GitHubModelsClient)
    return client


@pytest.fixture
def temp_kb_root(tmp_path):
    return tmp_path / "kb"


def test_association_extractor_extracts_associations(mock_client):
    # Setup mock response
    mock_response = Mock()
    mock_response.choices = [
        Mock(message=Mock(content=json.dumps([
            {
                "source": "Alice Smith",
                "target": "Acme Corp",
                "source_type": "Person",
                "target_type": "Organization",
                "relationship": "CEO",
                "evidence": "Alice Smith is the CEO of Acme Corp.",
                "confidence": 0.95
            },
            {
                "source": "Bob Jones",
                "target": "Beta Inc",
                "source_type": "Person",
                "target_type": "Organization",
                "relationship": "Employee",
                "evidence": "Bob Jones works at Beta Inc.",
                "confidence": 0.8
            }
        ])))
    ]
    mock_client.chat_completion.return_value = mock_response

    extractor = AssociationExtractor(mock_client)
    associations = extractor.extract_associations("Some text")

    assert len(associations) == 2
    assert associations[0].source == "Alice Smith"
    assert associations[0].target == "Acme Corp"
    assert associations[0].relationship == "CEO"
    assert associations[0].evidence == "Alice Smith is the CEO of Acme Corp."
    assert associations[0].confidence == 0.95


def test_association_extractor_handles_empty_response(mock_client):
    mock_response = Mock()
    mock_response.choices = []
    mock_client.chat_completion.return_value = mock_response

    extractor = AssociationExtractor(mock_client)
    associations = extractor.extract_associations("Some text")

    assert associations == []


def test_association_extractor_handles_malformed_json(mock_client):
    mock_response = Mock()
    mock_response.choices = [
        Mock(message=Mock(content="Not JSON"))
    ]
    mock_client.chat_completion.return_value = mock_response

    extractor = AssociationExtractor(mock_client)
    associations = extractor.extract_associations("Some text")

    assert associations == []


def test_association_extractor_uses_hints(mock_client):
    mock_response = Mock()
    mock_response.choices = [
        Mock(message=Mock(content="[]"))
    ]
    mock_client.chat_completion.return_value = mock_response

    extractor = AssociationExtractor(mock_client)
    extractor.extract_associations(
        "Some text", 
        people_hints=["Alice"], 
        org_hints=["Acme"]
    )

    # Verify that hints were included in the prompt
    call_args = mock_client.chat_completion.call_args
    messages = call_args.kwargs["messages"]
    system_prompt = messages[0]["content"]
    
    assert "Known People: Alice" in system_prompt
    assert "Known Organizations: Acme" in system_prompt


def test_storage_saves_and_retrieves_associations(temp_kb_root):
    storage = KnowledgeGraphStorage(temp_kb_root)
    checksum = "abc123hash"
    associations = [
        EntityAssociation(
            source="Alice",
            target="Acme",
            source_type="Person",
            target_type="Organization",
            relationship="Lead",
            evidence="Alice leads Acme.",
            confidence=0.9
        )
    ]

    storage.save_extracted_associations(checksum, associations)
    
    loaded = storage.get_extracted_associations(checksum)
    assert loaded is not None
    assert loaded.source_checksum == checksum
    assert len(loaded.associations) == 1
    assert loaded.associations[0].source == "Alice"
    assert loaded.associations[0].target == "Acme"
    assert loaded.associations[0].relationship == "Lead"
    assert loaded.associations[0].evidence == "Alice leads Acme."
    assert loaded.associations[0].confidence == 0.9


def test_storage_returns_none_for_missing_associations(temp_kb_root):
    storage = KnowledgeGraphStorage(temp_kb_root)
    assert storage.get_extracted_associations("missing") is None
