"""Extraction logic using GitHub Models."""

from __future__ import annotations

import json
from typing import List

from src.integrations.github.models import GitHubModelsClient, GitHubModelsError
from src.knowledge.storage import EntityAssociation, EntityProfile, KnowledgeGraphStorage
from src.parsing.base import ParsedDocument
from src.parsing.storage import ManifestEntry, ParseStorage


# Max tokens per chunk (leaving room for system prompt and response)
_MAX_CHUNK_TOKENS = 6000
# Rough estimate: 1 token ~= 4 characters
_CHARS_PER_TOKEN = 4


class ExtractionError(RuntimeError):
    """Raised when extraction fails."""



class BaseExtractor:
    """Base class for entity extraction using LLM."""

    def __init__(self, client: GitHubModelsClient) -> None:
        self.client = client

    def extract(self, text: str) -> List[str]:
        """Extract entities from the provided text."""
        if not text.strip():
            return []

        # Check if text needs chunking
        max_chars = _MAX_CHUNK_TOKENS * _CHARS_PER_TOKEN
        if len(text) > max_chars:
            return self._extract_chunked(text, max_chars)
        
        return self._extract_from_chunk(text)

    def _extract_from_chunk(self, text: str) -> List[str]:
        """Extract entities from a single chunk of text. Must be implemented by subclasses."""
        raise NotImplementedError

    def _extract_chunked(self, text: str, chunk_size: int) -> List[str]:
        """Extract entities from text by processing it in chunks and deduplicating."""
        # Split text into chunks at paragraph boundaries when possible
        chunks = []
        current_chunk = ""
        
        for paragraph in text.split("\n\n"):
            if len(current_chunk) + len(paragraph) + 2 < chunk_size:
                current_chunk += "\n\n" + paragraph if current_chunk else paragraph
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = paragraph
        
        if current_chunk:
            chunks.append(current_chunk)
        
        # Extract from each chunk
        all_entities = []
        for chunk in chunks:
            try:
                entities = self._extract_from_chunk(chunk)
                all_entities.extend(entities)
            except ExtractionError:
                # Continue with other chunks if one fails
                continue
        
        # Deduplicate while preserving order
        seen = set()
        unique_entities = []
        for name in all_entities:
            # Normalize for comparison
            normalized = name.strip().lower()
            if normalized not in seen:
                seen.add(normalized)
                unique_entities.append(name)
        
        return unique_entities

    def _call_llm(self, system_prompt: str, text: str) -> List[str]:
        """Helper to call LLM and parse JSON response."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        try:
            response = self.client.chat_completion(
                messages=messages,
                temperature=0.1,  # Low temperature for deterministic output
                max_tokens=2000,
            )
        except CopilotClientError as exc:
            raise ExtractionError(f"LLM call failed: {exc}") from exc

        if not response.choices:
            raise ExtractionError("No response from LLM")

        content = response.choices[0].message.content or "[]"
        
        # Clean up potential markdown code blocks
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        
        try:
            data = json.loads(content)
            if not isinstance(data, list):
                raise ExtractionError("LLM did not return a JSON array")
            return [str(item) for item in data if isinstance(item, (str, int, float))]
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"Failed to parse LLM response as JSON: {content}") from exc


class PersonExtractor(BaseExtractor):
    """Extracts person names from text using an LLM."""

    def extract_people(self, text: str) -> List[str]:
        """Extract a list of person names from the provided text."""
        return self.extract(text)

    def _extract_from_chunk(self, text: str) -> List[str]:
        """Extract people from a single chunk of text."""
        system_prompt = (
            "You are an expert entity extractor. Your task is to extract all unique person names "
            "from the provided text. Return ONLY a JSON array of strings. "
            "Do not include titles (Mr., Dr.) unless necessary for disambiguation. "
            "Normalize names to 'First Last' format where possible. "
            "If no people are found, return an empty array []."
        )
        return self._call_llm(system_prompt, text)


class OrganizationExtractor(BaseExtractor):
    """Extracts organization names from text using an LLM."""

    def extract_organizations(self, text: str) -> List[str]:
        """Extract a list of organization names from the provided text."""
        return self.extract(text)

    def _extract_from_chunk(self, text: str) -> List[str]:
        """Extract organizations from a single chunk of text."""
        system_prompt = (
            "You are an expert entity extractor. Extract all organization names "
            "from the text including: companies, institutions, governments, "
            "military units, and other formal groups. Return ONLY a JSON array. "
            "Normalize names (e.g., 'The World Bank' not 'the world bank'). "
            "Include historical organizations. If none found, return []."
        )
        return self._call_llm(system_prompt, text)


class ConceptExtractor(BaseExtractor):
    """Extracts concepts from text using an LLM."""

    def extract_concepts(self, text: str) -> List[str]:
        """Extract a list of concepts from the provided text."""
        return self.extract(text)

    def _extract_from_chunk(self, text: str) -> List[str]:
        """Extract concepts from a single chunk of text."""
        system_prompt = (
            "You are an expert entity extractor. Extract all key concepts, themes, "
            "definitions, and abstract ideas from the text. "
            "Return ONLY a JSON array of strings. "
            "Focus on capturing the core ideas and terminology used in the text. "
            "Avoid summarizing the entire document; instead, list specific concepts. "
            "Normalize concepts to a standard form where possible (e.g., 'The Social Contract' -> 'Social Contract'). "
            "If no concepts are found, return []."
        )
        return self._call_llm(system_prompt, text)
    
    
class AssociationExtractor(BaseExtractor):
    """Extracts associations between people and organizations from text using an LLM."""

    def extract_associations(
        self, 
        text: str, 
        people_hints: List[str] | None = None, 
        org_hints: List[str] | None = None,
        concept_hints: List[str] | None = None
    ) -> List[EntityAssociation]:
        """Extract a list of associations from the provided text."""
        # We override the base extract method because we return objects, not strings
        if not text.strip():
            return []

        # Check if text needs chunking
        max_chars = _MAX_CHUNK_TOKENS * _CHARS_PER_TOKEN
        if len(text) > max_chars:
            return self._extract_chunked_associations(text, max_chars, people_hints, org_hints, concept_hints)
        
        return self._extract_from_chunk_associations(text, people_hints, org_hints, concept_hints)

    def _extract_from_chunk_associations(
        self, 
        text: str, 
        people_hints: List[str] | None = None, 
        org_hints: List[str] | None = None,
        concept_hints: List[str] | None = None
    ) -> List[EntityAssociation]:
        """Extract associations from a single chunk of text."""
        
        hints_str = ""
        if people_hints:
            hints_str += f"\nKnown People: {', '.join(people_hints)}"
        if org_hints:
            hints_str += f"\nKnown Organizations: {', '.join(org_hints)}"
        if concept_hints:
            hints_str += f"\nKnown Concepts: {', '.join(concept_hints)}"

        system_prompt = (
            "You are an expert entity extractor. Extract associations between entities (People, Organizations, Concepts) "
            "from the text. Return ONLY a JSON array of objects with these fields: "
            "'source' (name of first entity), 'target' (name of second entity), "
            "'source_type' (Person, Organization, or Concept), 'target_type' (Person, Organization, or Concept), "
            "'relationship' (e.g., 'Author of', 'Member of', 'Related to', 'Opposed to'), "
            "'evidence' (a brief quote from the text supporting this), and 'confidence' (0.0 to 1.0). "
            "Normalize names. If no associations found, return []."
            f"{hints_str}"
            "\nUse the known entities as a guide, but only extract associations explicitly supported by the text."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        try:
            response = self.client.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
            )
        except CopilotClientError:
            return []

        if not response.choices:
            return []

        content = response.choices[0].message.content or "[]"
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        
        if content.endswith("```"):
            content = content[:-3]
        
        try:
            data = json.loads(content)
            if not isinstance(data, list):
                return []
            
            associations = []
            for item in data:
                if isinstance(item, dict):
                    try:
                        associations.append(EntityAssociation.from_dict(item))
                    except (KeyError, TypeError):
                        continue
            return associations
        except json.JSONDecodeError:
            return []

    def _extract_chunked_associations(
        self, 
        text: str, 
        chunk_size: int,
        people_hints: List[str] | None = None,
        org_hints: List[str] | None = None,
        concept_hints: List[str] | None = None
    ) -> List[EntityAssociation]:
        """Extract associations from text by processing it in chunks."""
        # Similar to base _extract_chunked but for objects
        chunks = []
        current_chunk = ""
        
        for paragraph in text.split("\n\n"):
            if len(current_chunk) + len(paragraph) + 2 < chunk_size:
                current_chunk += "\n\n" + paragraph if current_chunk else paragraph
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = paragraph
        
        if current_chunk:
            chunks.append(current_chunk)
        
        all_associations = []
        for chunk in chunks:
            all_associations.extend(self._extract_from_chunk_associations(chunk, people_hints, org_hints, concept_hints))
        
        # Deduplicate based on source+target+relationship
        seen = set()
        unique_associations = []
        for assoc in all_associations:
            key = (assoc.source.lower(), assoc.target.lower(), assoc.relationship.lower())
            if key not in seen:
                seen.add(key)
                unique_associations.append(assoc)
        
        return unique_associations


def read_document_content(entry: ManifestEntry, storage: ParseStorage) -> str:
    """Read the full text content of a parsed document."""
    # The artifact path in manifest is relative to storage root
    artifact_path = storage.root / entry.artifact_path
    
    if not artifact_path.exists():
        raise ExtractionError(f"Artifact not found: {artifact_path}")

    # If it's a directory (page-directory), read all pages
    # If it's a file, read it directly
    full_text = ""
    
    # Check if this is a page-directory artifact type
    is_page_directory = entry.metadata.get("artifact_type") == "page-directory"
    
    if is_page_directory:
        # For page-directory, artifact_path points to index.md
        # We need to read from the parent directory
        directory = artifact_path.parent
        if not directory.exists():
            raise ExtractionError(f"Page directory not found: {directory}")
        
        # Read all pages except index.md
        pages = sorted([p for p in directory.glob("*.md") if p.name != "index.md"])
        full_text = "\n\n".join([p.read_text(encoding="utf-8") for p in pages])
    elif artifact_path.is_dir():
        # Legacy: artifact_path is a directory itself
        pages = sorted([p for p in artifact_path.glob("*.md") if p.name != "index.md"])
        full_text = "\n\n".join([p.read_text(encoding="utf-8") for p in pages])
    else:
        # It's a single file
        full_text = artifact_path.read_text(encoding="utf-8")

    return full_text


def process_document(
    entry: ManifestEntry,
    storage: ParseStorage,
    kb_storage: KnowledgeGraphStorage,
    extractor: PersonExtractor,
) -> List[str]:
    """Process a parsed document to extract people and save to KB."""
    full_text = read_document_content(entry, storage)

    if not full_text.strip():
        return []

    # Extract people
    people = extractor.extract_people(full_text)

    # Save to KB
    kb_storage.save_extracted_people(entry.checksum, people)

    return people


def process_document_organizations(
    entry: ManifestEntry,
    storage: ParseStorage,
    kb_storage: KnowledgeGraphStorage,
    extractor: OrganizationExtractor,
) -> List[str]:
    """Process a parsed document to extract organizations and save to KB."""
    full_text = read_document_content(entry, storage)

    if not full_text.strip():
        return []

    # Extract organizations
    organizations = extractor.extract_organizations(full_text)

    # Save to KB
    kb_storage.save_extracted_organizations(entry.checksum, organizations)

    return organizations


def process_document_concepts(
    entry: ManifestEntry,
    storage: ParseStorage,
    kb_storage: KnowledgeGraphStorage,
    extractor: ConceptExtractor,
) -> List[str]:
    """Process a parsed document to extract concepts and save to KB."""
    full_text = read_document_content(entry, storage)

    if not full_text.strip():
        return []

    # Extract concepts
    concepts = extractor.extract_concepts(full_text)

    # Save to KB
    kb_storage.save_extracted_concepts(entry.checksum, concepts)

    return concepts

    
def process_document_associations(
    entry: ManifestEntry,
    storage: ParseStorage,
    kb_storage: KnowledgeGraphStorage,
    extractor: AssociationExtractor,
) -> List[EntityAssociation]:
    """Process a parsed document to extract associations and save to KB."""
    full_text = read_document_content(entry, storage)

    if not full_text.strip():
        return []

    # Load hints from KB
    people_hints = []
    extracted_people = kb_storage.get_extracted_people(entry.checksum)
    if extracted_people:
        people_hints = extracted_people.people

    org_hints = []
    extracted_orgs = kb_storage.get_extracted_organizations(entry.checksum)
    if extracted_orgs:
        org_hints = extracted_orgs.organizations

    concept_hints = []
    extracted_concepts = kb_storage.get_extracted_concepts(entry.checksum)
    if extracted_concepts:
        concept_hints = extracted_concepts.concepts

    # Extract associations
    associations = extractor.extract_associations(full_text, people_hints, org_hints, concept_hints)

    # Save to KB
    kb_storage.save_extracted_associations(entry.checksum, associations)

    return associations
   
    
    
class ProfileExtractor(BaseExtractor):
    """Extracts detailed profiles for entities from text using an LLM."""

    def extract_profiles(
        self, 
        text: str, 
        entities: List[str]
    ) -> List[EntityProfile]:
        """Extract profiles for the provided entities from the text."""
        if not text.strip() or not entities:
            return []

        # Check if text needs chunking
        max_chars = _MAX_CHUNK_TOKENS * _CHARS_PER_TOKEN
        if len(text) > max_chars:
            return self._extract_chunked_profiles(text, max_chars, entities)
        
        return self._extract_from_chunk_profiles(text, entities)

    def _extract_from_chunk_profiles(
        self, 
        text: str, 
        entities: List[str]
    ) -> List[EntityProfile]:
        """Extract profiles from a single chunk of text."""
        
        entities_str = ", ".join(entities)
        system_prompt = (
            "You are an expert analyst. Create detailed profiles for the following entities based ONLY on the provided text: "
            f"{entities_str}. "
            "Return ONLY a JSON array of objects with these fields: "
            "'name' (entity name), 'entity_type' (Person, Organization, or Concept), "
            "'summary' (concise summary of the entity's role/definition in this text), "
            "'attributes' (dictionary of key-value pairs for specific details like age, role, location, dates), "
            "'mentions' (list of specific quotes or context where the entity appears), "
            "and 'confidence' (0.0 to 1.0). "
            "If an entity is not mentioned in the text, do not include it in the output."
        )
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ]

        try:
            response = self.client.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
            )
        except CopilotClientError:
            return []

        if not response.choices:
            return []

        content = response.choices[0].message.content or "[]"
        content = content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        
        try:
            data = json.loads(content)
            if not isinstance(data, list):
                return []
            
            profiles = []
            for item in data:
                if isinstance(item, dict):
                    try:
                        profiles.append(EntityProfile.from_dict(item))
                    except (KeyError, TypeError):
                        continue
            return profiles
        except json.JSONDecodeError:
            return []

    def _extract_chunked_profiles(
        self, 
        text: str, 
        chunk_size: int,
        entities: List[str]
    ) -> List[EntityProfile]:
        """Extract profiles from text by processing it in chunks and aggregating."""
        chunks = []
        current_chunk = ""
        
        for paragraph in text.split("\n\n"):
            if len(current_chunk) + len(paragraph) + 2 < chunk_size:
                current_chunk += "\n\n" + paragraph if current_chunk else paragraph
            else:
                if current_chunk:
                    chunks.append(current_chunk)
                current_chunk = paragraph
        
        if current_chunk:
            chunks.append(current_chunk)
        
        all_profiles = []
        for chunk in chunks:
            all_profiles.extend(self._extract_from_chunk_profiles(chunk, entities))
        
        return self._aggregate_profiles(all_profiles)

    def _aggregate_profiles(self, profiles: List[EntityProfile]) -> List[EntityProfile]:
        """Aggregate multiple profile fragments for the same entity."""
        merged = {}
        for p in profiles:
            if p.name not in merged:
                merged[p.name] = p
            else:
                existing = merged[p.name]
                # Merge summary (simple concatenation for now, could be LLM summarized)
                existing.summary += " " + p.summary
                # Merge attributes
                existing.attributes.update(p.attributes)
                # Merge mentions
                existing.mentions.extend(p.mentions)
                # Average confidence
                existing.confidence = (existing.confidence + p.confidence) / 2
        
        return list(merged.values())


def process_document_profiles(
    entry: ManifestEntry,
    storage: ParseStorage,
    kb_storage: KnowledgeGraphStorage,
    extractor: ProfileExtractor,
) -> List[EntityProfile]:
    """Process a parsed document to extract profiles and save to KB."""
    full_text = read_document_content(entry, storage)

    if not full_text.strip():
        return []

    # Gather all known entities for this document to profile
    entities = set()
    
    people = kb_storage.get_extracted_people(entry.checksum)
    if people:
        entities.update(people.people)
        
    orgs = kb_storage.get_extracted_organizations(entry.checksum)
    if orgs:
        entities.update(orgs.organizations)
        
    concepts = kb_storage.get_extracted_concepts(entry.checksum)
    if concepts:
        entities.update(concepts.concepts)

    if not entities:
        return []

    # Extract profiles
    profiles = extractor.extract_profiles(full_text, list(entities))

    # Save to KB
    kb_storage.save_extracted_profiles(entry.checksum, profiles)

    return profiles
