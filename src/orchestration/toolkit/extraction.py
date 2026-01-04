
"""Extraction tools for the agent."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from src.integrations.github.models import GitHubModelsClient
from src.integrations.github.issues import resolve_repository, resolve_token
from src.integrations.github.pull_requests import create_pull_request
from src.integrations.github.storage import commit_file
from src.knowledge.storage import KnowledgeGraphStorage
from src.orchestration.tools import ToolDefinition
from src.parsing.config import load_parsing_config
from src.knowledge.extraction import (
    PersonExtractor, 
    OrganizationExtractor,
    ProfileExtractor,
    ConceptExtractor,
    AssociationExtractor,
    process_document,
    process_document_organizations,
    process_document_profiles,
    process_document_concepts,
    process_document_associations,
)
from src.parsing.storage import ParseStorage
from src.orchestration.tools import ToolRegistry
from src.paths import get_knowledge_graph_root

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
        self.client = GitHubModelsClient()
        
        # Create a mini model client for simple tasks (cheaper)
        self.mini_client = GitHubModelsClient(model="gpt-4o-mini")
        
        self.extractor = PersonExtractor(self.client)
        self.org_extractor = OrganizationExtractor(self.client)
        self.profile_extractor = ProfileExtractor(self.client)
        self.concept_extractor = ConceptExtractor(self.client)
        self.association_extractor = AssociationExtractor(self.client)

    def get_tools(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="assess_document_value",
                description="Use LLM to assess if a document contains substantive, extractable content. Returns assessment and reasoning.",
                parameters={
                    "type": "object",
                    "properties": {
                        "checksum": {
                            "type": "string",
                            "description": "Checksum of the document to assess.",
                        },
                    },
                    "required": ["checksum"],
                },
                handler=self._assess_document,
            ),
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
                name="extract_concepts_from_document",
                description="Extract key concepts and themes from a parsed document using its checksum.",
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
                handler=self._extract_concepts,
            ),
            ToolDefinition(
                name="extract_associations_from_document",
                description="Extract relationships between people, organizations, and concepts from a parsed document. Use after extracting other entity types.",
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
                handler=self._extract_associations,
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
            ToolDefinition(
                name="mark_extraction_complete",
                description="Mark a document as extraction_complete in the manifest to prevent re-queuing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "checksum": {
                            "type": "string",
                            "description": "Checksum of the document to mark as complete.",
                        },
                    },
                    "required": ["checksum"],
                },
                handler=self._mark_complete,
            ),
            ToolDefinition(
                name="mark_extraction_skipped",
                description="Mark a document as extraction_skipped in the manifest with a reason.",
                parameters={
                    "type": "object",
                    "properties": {
                        "checksum": {
                            "type": "string",
                            "description": "Checksum of the document to mark as skipped.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Reason for skipping the document.",
                        },
                    },
                    "required": ["checksum", "reason"],
                },
                handler=self._mark_skipped,
            ),
            ToolDefinition(
                name="create_extraction_pull_request",
                description="Create a pull request with all extraction changes. Use in GitHub Actions to persist changes.",
                parameters={
                    "type": "object",
                    "properties": {
                        "branch_name": {
                            "type": "string",
                            "description": "Name of the branch to create (e.g., 'extraction/doc-abc123').",
                        },
                        "pr_title": {
                            "type": "string",
                            "description": "Title for the pull request.",
                        },
                        "pr_body": {
                            "type": "string",
                            "description": "Body/description for the pull request.",
                        },
                        "base_branch": {
                            "type": "string",
                            "description": "Base branch to merge into (defaults to 'main').",
                        },
                    },
                    "required": ["branch_name", "pr_title", "pr_body"],
                },
                handler=self._create_pr,
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

    def _assess_document(self, args: Mapping[str, Any]) -> Any:
        """Use LLM (mini model) to assess if document has substantive content."""
        checksum = args["checksum"]
        
        entry = self.storage.manifest().get(checksum)
        if not entry:
            return {"status": "error", "message": f"Document with checksum {checksum} not found."}
            
        if entry.status != "completed":
            return {"status": "error", "message": f"Document {checksum} not parsed (status: {entry.status})."}

        try:
            # Read the parsed content using the extraction helper
            from src.knowledge.extraction import read_document_content
            content = read_document_content(entry, self.storage)
            if not content or len(content.strip()) < 50:
                return {
                    "status": "skip",
                    "is_substantive": False,
                    "reason": "Document content too short or empty",
                    "confidence": 1.0,
                }
            
            # Use mini model for cheap assessment
            system_prompt = """You are a content assessment expert. Determine if a document contains substantive, extractable content.

SKIP if the document is:
- A navigation/menu page with only links
- An error page (404, access denied, etc.)
- Pure boilerplate without real content
- A redirect or placeholder page
- Just metadata without actual content

EXTRACT if the document contains:
- Real information about people, organizations, events, or concepts
- Substantive text, analysis, or discussion
- Actual content worth extracting entities from

Return ONLY a JSON object with:
{
  "is_substantive": true/false,
  "reason": "Brief explanation of your decision",
  "confidence": 0.0-1.0
}"""

            user_prompt = f"Assess this document content (first 3000 chars):\n\n{content[:3000]}"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            
            response = self.mini_client.chat_completion(
                messages=messages,
                temperature=0.1,
                max_tokens=300,
            )
            
            if not response.choices:
                return {"status": "error", "message": "No response from LLM"}
            
            result_text = response.choices[0].message.content.strip()
            
            # Parse JSON response
            import json
            try:
                result = json.loads(result_text)
                return {
                    "status": "success",
                    "is_substantive": result.get("is_substantive", False),
                    "reason": result.get("reason", ""),
                    "confidence": result.get("confidence", 0.5),
                }
            except json.JSONDecodeError:
                # Fallback: look for true/false in response
                is_substantive = "true" in result_text.lower()
                return {
                    "status": "success",
                    "is_substantive": is_substantive,
                    "reason": result_text,
                    "confidence": 0.7,
                }
                
        except Exception as exc:
            return {"status": "error", "message": f"Assessment failed: {exc}"}

    def _extract_concepts(self, args: Mapping[str, Any]) -> Any:
        """Extract concepts from document."""
        checksum = args["checksum"]
        
        entry = self.storage.manifest().get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found in manifest."
            
        if entry.status != "completed":
            return f"Error: Document {checksum} is not successfully parsed (status: {entry.status})."

        try:
            concepts = process_document_concepts(
                entry,
                self.storage,
                self.kb_storage,
                self.concept_extractor,
            )
            return {
                "status": "success",
                "extracted_count": len(concepts),
                "concepts": concepts,
            }
        except Exception as exc:
            return f"Error during extraction: {exc}"

    def _extract_associations(self, args: Mapping[str, Any]) -> Any:
        """Extract associations between entities from document."""
        checksum = args["checksum"]
        
        entry = self.storage.manifest().get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found in manifest."
            
        if entry.status != "completed":
            return f"Error: Document {checksum} is not successfully parsed (status: {entry.status})."

        try:
            associations = process_document_associations(
                entry,
                self.storage,
                self.kb_storage,
                self.association_extractor,
            )
            return {
                "status": "success",
                "extracted_count": len(associations),
                "associations": [a.to_dict() for a in associations],
            }
        except Exception as exc:
            return f"Error during extraction: {exc}"

    def _mark_complete(self, args: Mapping[str, Any]) -> Any:
        """Mark document as extraction complete."""
        checksum = args["checksum"]
        
        manifest = self.storage.manifest()
        entry = manifest.get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found."
        
        try:
            # Update manifest entry metadata
            entry.metadata["extraction_status"] = "extraction_complete"
            entry.metadata["extraction_completed_at"] = datetime.now(timezone.utc).isoformat()
            self.storage.record_entry(entry)
            
            return {
                "status": "success",
                "message": f"Marked document {checksum} as extraction_complete",
            }
        except Exception as exc:
            return f"Error marking complete: {exc}"

    def _mark_skipped(self, args: Mapping[str, Any]) -> Any:
        """Mark document as extraction skipped with reason."""
        checksum = args["checksum"]
        reason = args["reason"]
        
        manifest = self.storage.manifest()
        entry = manifest.get(checksum)
        if not entry:
            return f"Error: Document with checksum {checksum} not found."
        
        try:
            # Update manifest entry metadata
            entry.metadata["extraction_status"] = "extraction_skipped"
            entry.metadata["extraction_skip_reason"] = reason
            entry.metadata["extraction_skipped_at"] = datetime.now(timezone.utc).isoformat()
            self.storage.record_entry(entry)
            
            return {
                "status": "success",
                "message": f"Marked document {checksum} as extraction_skipped",
                "reason": reason,
            }
        except Exception as exc:
            return f"Error marking skipped: {exc}"

    def _create_pr(self, args: Mapping[str, Any]) -> Any:
        """Create a pull request with extraction changes (for GitHub Actions)."""
        branch_name = args["branch_name"]
        pr_title = args["pr_title"]
        pr_body = args["pr_body"]
        base_branch = args.get("base_branch", "main")
        
        try:
            # Get GitHub credentials from environment
            token = resolve_token()
            repository = resolve_repository()
            
            if not token or not repository:
                return {
                    "status": "skip",
                    "message": "Not running in GitHub Actions - changes saved locally only",
                }
            
            # Create the pull request
            # Note: The actual file changes should already be committed via GitHubStorageClient
            # This just creates the PR for the branch that was created during extraction
            pr_data = create_pull_request(
                token=token,
                repository=repository,
                title=pr_title,
                body=pr_body,
                head=branch_name,
                base=base_branch,
            )
            
            return {
                "status": "success",
                "pr_number": pr_data.get("number"),
                "pr_url": pr_data.get("html_url"),
                "message": f"Created PR #{pr_data.get('number')}",
            }
            
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Failed to create PR: {exc}",
            }
