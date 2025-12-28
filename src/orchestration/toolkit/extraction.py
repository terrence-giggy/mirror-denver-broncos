
"""Extraction tools for the agent."""

from __future__ import annotations

from typing import Any, Mapping

from src.integrations.copilot import CopilotClient
from src.knowledge.storage import KnowledgeGraphStorage
from src.orchestration.tools import ToolDefinition
from src.parsing.config import load_parsing_config
from src.knowledge.extraction import (
    PersonExtractor, 
    OrganizationExtractor,
    ProfileExtractor,
    process_document,
    process_document_organizations,
    process_document_profiles,
)
from src.parsing.storage import ParseStorage
from src.orchestration.tools import ToolRegistry

from ._github_context import resolve_github_client


def register_extraction_tools(registry: ToolRegistry) -> None:
    """Register extraction tools with the provided registry."""
    toolkit = ExtractionToolkit()
    for tool in toolkit.get_tools():
        registry.register_tool(tool)


class ExtractionToolkit:
    """Toolkit for extracting information from documents."""

    def __init__(self) -> None:
        # Initialize with defaults
        config = load_parsing_config(None)
        self.storage = ParseStorage(config.output_root)
        
        # KnowledgeGraphStorage with GitHub API support for Actions
        github_client = resolve_github_client()
        self.kb_storage = KnowledgeGraphStorage(github_client=github_client)
        
        # Client will be initialized on first use or we can try now
        # Ideally we share the client but for now we create a new one
        self.client = CopilotClient()
        self.extractor = PersonExtractor(self.client)
        self.org_extractor = OrganizationExtractor(self.client)
        self.profile_extractor = ProfileExtractor(self.client)

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="extract_people_from_document",
                description="Extract person names from a parsed document using its checksum.",
                parameters={
                    "type": "object",
                    "properties": {
                        "checksum": {
                            "type": "string",
                            "description": "Checksum of the document to process.",
                        },
                    },
                    "required": ["checksum"],
                },
                handler=self._extract_people,
            ),
            ToolDefinition(
                name="extract_organizations_from_document",
                description="Extract organization names from a parsed document using its checksum.",
                parameters={
                    "type": "object",
                    "properties": {
                        "checksum": {
                            "type": "string",
                            "description": "Checksum of the document to process.",
                        },
                    },
                    "required": ["checksum"],
                },
                handler=self._extract_organizations,
            ),
            ToolDefinition(
                name="extract_profiles_from_document",
                description="Extract detailed profiles for entities from a parsed document using its checksum.",
                parameters={
                    "type": "object",
                    "properties": {
                        "checksum": {
                            "type": "string",
                            "description": "Checksum of the document to process.",
                        },
                    },
                    "required": ["checksum"],
                },
                handler=self._extract_profiles,
            ),
        ]

    def _extract_people(self, args: Mapping[str, Any]) -> Any:
        checksum = args["checksum"]
        
        entry = self.storage.manifest().get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found in manifest."
            
        if entry.status != "completed":
            return f"Error: Document {checksum} is not successfully parsed (status: {entry.status})."

        try:
            people = process_document(
                entry,
                self.storage,
                self.kb_storage,
                self.extractor,
            )
            return {
                "status": "success",
                "extracted_count": len(people),
                "people": people,
            }
        except Exception as exc:
            return f"Error during extraction: {exc}"

    def _extract_organizations(self, args: Mapping[str, Any]) -> Any:
        checksum = args["checksum"]
        
        entry = self.storage.manifest().get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found in manifest."
            
        if entry.status != "completed":
            return f"Error: Document {checksum} is not successfully parsed (status: {entry.status})."

        try:
            organizations = process_document_organizations(
                entry,
                self.storage,
                self.kb_storage,
                self.org_extractor,
            )
            return {
                "status": "success",
                "extracted_count": len(organizations),
                "organizations": organizations,
            }
        except Exception as exc:
            return f"Error during extraction: {exc}"

    def _extract_profiles(self, args: Mapping[str, Any]) -> Any:
        checksum = args["checksum"]
        
        entry = self.storage.manifest().get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found in manifest."
            
        if entry.status != "completed":
            return f"Error: Document {checksum} is not successfully parsed (status: {entry.status})."

        try:
            profiles = process_document_profiles(
                entry,
                self.storage,
                self.kb_storage,
                self.profile_extractor,
            )
            return {
                "status": "success",
                "extracted_count": len(profiles),
                "profiles": [p.to_dict() for p in profiles],
            }
        except Exception as exc:
            return f"Error during extraction: {exc}"
