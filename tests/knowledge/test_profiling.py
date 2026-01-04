"""Tests for entity profile extraction."""

import json
from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from src.integrations.github.models import GitHubModelsClient, ChatCompletionResponse, Choice, ChatMessage
from src.knowledge.storage import KnowledgeGraphStorage, EntityProfile, ExtractedProfiles
from src.knowledge.extraction import ProfileExtractor, process_document_profiles
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


def test_profile_extractor_extract_profiles(mock_client):
    extractor = ProfileExtractor(mock_client)
    
    # Mock response
    profile_data = [
        {
            "name": "John Doe",
            "entity_type": "Person",
            "summary": "A software engineer.",
            "attributes": {"role": "Engineer", "age": 30},
            "mentions": ["John Doe is a software engineer."],
            "confidence": 0.95
        }
    ]
    mock_message = ChatMessage(role="assistant", content=json.dumps(profile_data))
    mock_choice = Choice(index=0, message=mock_message)
    mock_response = ChatCompletionResponse(
        id="test-id",
        model="gpt-4o-mini",
        choices=(mock_choice,),
    )
    mock_client.chat_completion.return_value = mock_response
    
    profiles = extractor.extract_profiles("John Doe is a software engineer.", ["John Doe"])
    
    assert len(profiles) == 1
    assert profiles[0].name == "John Doe"
    assert profiles[0].entity_type == "Person"
    assert profiles[0].summary == "A software engineer."
    assert profiles[0].attributes == {"role": "Engineer", "age": 30}
    
    mock_client.chat_completion.assert_called_once()


def test_profile_extractor_aggregation(mock_client):
    extractor = ProfileExtractor(mock_client)
    
    # Mock aggregation logic manually since we can't easily mock multiple calls with different responses in a simple way 
    # without side_effect, but let's test the private method directly for aggregation logic.
    
    p1 = EntityProfile(
        name="John Doe",
        entity_type="Person",
        summary="Part 1.",
        attributes={"role": "Engineer"},
        mentions=["Mention 1"],
        confidence=0.9
    )
    p2 = EntityProfile(
        name="John Doe",
        entity_type="Person",
        summary="Part 2.",
        attributes={"location": "NY"},
        mentions=["Mention 2"],
        confidence=0.8
    )
    
    aggregated = extractor._aggregate_profiles([p1, p2])
    
    assert len(aggregated) == 1
    p = aggregated[0]
    assert p.name == "John Doe"
    assert "Part 1." in p.summary
    assert "Part 2." in p.summary
    assert p.attributes == {"role": "Engineer", "location": "NY"}
    assert len(p.mentions) == 2
    assert p.confidence == pytest.approx(0.85)


def test_kb_storage_profiles(tmp_path):
    storage = KnowledgeGraphStorage(tmp_path / "kb")
    checksum = "test-checksum"
    profiles = [
        EntityProfile(
            name="Test Entity",
            entity_type="Organization",
            summary="A test org.",
            attributes={},
            mentions=[],
            confidence=1.0
        )
    ]
    
    storage.save_extracted_profiles(checksum, profiles)
    
    loaded = storage.get_extracted_profiles(checksum)
    assert loaded is not None
    assert len(loaded.profiles) == 1
    assert loaded.profiles[0].name == "Test Entity"
    assert loaded.source_checksum == checksum
