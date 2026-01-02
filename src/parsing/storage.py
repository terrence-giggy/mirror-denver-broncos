"""Persistence helpers for parsed document artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import utils
from .base import ParsedDocument
from .markdown import document_to_markdown

if TYPE_CHECKING:
    from src.integrations.github.storage import GitHubStorageClient

_MANIFEST_VERSION = 1
_DEFAULT_MANIFEST = "manifest.json"


@dataclass(slots=True)
class ManifestEntry:
    source: str
    checksum: str
    parser: str
    artifact_path: str
    processed_at: datetime
    status: str = "completed"
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "source": self.source,
            "checksum": self.checksum,
            "parser": self.parser,
            "artifact_path": self.artifact_path,
            "processed_at": self.processed_at.isoformat(),
            "status": self.status,
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ManifestEntry":
        processed_at = datetime.fromisoformat(payload["processed_at"])
        return cls(
            source=payload["source"],
            checksum=payload["checksum"],
            parser=payload["parser"],
            artifact_path=payload["artifact_path"],
            processed_at=processed_at,
            status=payload.get("status", "completed"),
            metadata=payload.get("metadata", {}),
        )


@dataclass(slots=True)
class Manifest:
    version: int = _MANIFEST_VERSION
    entries: dict[str, ManifestEntry] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "entries": [entry.to_dict() for entry in self.entries.values()],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Manifest":
        version = payload.get("version", _MANIFEST_VERSION)
        entries_payload = payload.get("entries", [])
        entries = {
            item["checksum"]: ManifestEntry.from_dict(item)
            for item in entries_payload
        }
        return cls(version=version, entries=entries)

    def get(self, checksum: str) -> ManifestEntry | None:
        return self.entries.get(checksum)

    def upsert(self, entry: ManifestEntry) -> None:
        self.entries[entry.checksum] = entry


class ParseStorage:
    """Manages storage of parsed document artifacts.

    When running in GitHub Actions, pass a GitHubStorageClient to persist
    writes via the GitHub API instead of the local filesystem.
    """

    def __init__(
        self,
        root: Path,
        *,
        manifest_filename: str = _DEFAULT_MANIFEST,
        github_client: "GitHubStorageClient | None" = None,
        project_root: Path | None = None,
    ) -> None:
        self.root = Path(root)
        self.root = self.root if self.root.is_absolute() else self.root.resolve()
        self._manifest_filename = manifest_filename
        self._github_client = github_client
        # Project root for computing relative paths (defaults to cwd)
        self._project_root = project_root or Path.cwd()
        # Defer manifest writes for batching (GitHub API efficiency)
        self._defer_manifest_writes = False
        self._manifest_dirty = False
        # Defer content file writes for batching (GitHub API efficiency)
        self._defer_content_writes = False
        self._pending_content_files: list[tuple[Path, str]] = []
        utils.ensure_directory(self.root)
        self._manifest = self._load_manifest()

    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to project root for GitHub API."""
        try:
            return str(path.relative_to(self._project_root))
        except ValueError:
            # Path is not under project root, use absolute path
            return str(path)

    @property
    def manifest_path(self) -> Path:
        return self.root / self._manifest_filename

    def manifest(self) -> Manifest:
        return self._manifest

    def should_process(self, checksum: str) -> bool:
        entry = self._manifest.get(checksum)
        if entry is None:
            return True
        return entry.status != "completed"
    
    def begin_batch(self) -> None:
        """Start batching manifest and content writes to reduce GitHub API commits.
        
        Call this before processing multiple documents, then call flush_all()
        when done to write all changes in a single commit.
        """
        self._defer_manifest_writes = True
        self._defer_content_writes = True
        self._manifest_dirty = False
        self._pending_content_files = []
    
    def flush_manifest(self) -> None:
        """Write pending manifest changes if any exist.
        
        This should be called after begin_batch() and document processing to
        commit all accumulated manifest changes in a single write.
        
        Note: This only flushes the manifest file. For content files, use flush_all().
        """
        if self._manifest_dirty:
            self._write_manifest()
            self._manifest_dirty = False
        self._defer_manifest_writes = False
    
    def flush_all(self) -> None:
        """Write all pending changes (content files + manifest).
        
        This commits all accumulated content files and the manifest in a single
        batch commit when using GitHub client. Uses PR branch if available.
        """
        # Flush content files first
        if self._pending_content_files:
            if self._github_client:
                # Batch commit all pending files to PR branch
                github_files = [
                    (self._get_relative_path(path), content)
                    for path, content in self._pending_content_files
                ]
                self._github_client.commit_files_batch(
                    files=github_files,
                    message=f"Add parsed content ({len(github_files)} files)",
                    use_pr_branch=True,
                )
            else:
                # Write to local filesystem
                for path, content in self._pending_content_files:
                    _write_atomic_text(path, content)
            
            self._pending_content_files = []
        
        self._defer_content_writes = False
        
        # Then flush manifest
        self.flush_manifest()

    def record_entry(self, entry: ManifestEntry) -> None:
        self._manifest.upsert(entry)
        self._manifest_dirty = True
        if not self._defer_manifest_writes:
            self._write_manifest()

    def persist_document(self, document: ParsedDocument) -> ManifestEntry:
        """Write the document to disk and record a manifest entry."""

        checksum = document.checksum
        processed_at = document.created_at
        artifact_dir, _ = self._prepare_artifact_directory(
            document.target.source,
            checksum,
            processed_at=processed_at,
        )

        # Clean up existing segment files (only for local filesystem)
        if not self._github_client:
            for existing in artifact_dir.glob("*.md"):
                if existing.name == "index.md":
                    continue
                if existing.name.startswith(("page-", "segment-")):
                    existing.unlink(missing_ok=True)

        page_unit = _determine_segment_unit(document)
        total_segments = len(document.segments)
        page_files: list[str] = []

        # Collect all files to write (for batching with GitHub API)
        files_to_write: list[tuple[Path, str]] = []

        for index, segment in enumerate(document.segments, start=1):
            normalized = segment.strip("\n")
            if not normalized:
                continue

            page_filename = f"{page_unit}-{index:03d}.md"
            page_path = artifact_dir / page_filename

            page_doc = ParsedDocument(
                target=document.target,
                checksum=document.checksum,
                parser_name=document.parser_name,
            )
            page_doc.created_at = document.created_at
            page_doc.metadata = {
                "page_unit": page_unit,
                "page_number": index,
                "page_total": total_segments,
            }
            page_doc.warnings = []
            page_doc.add_segment(normalized)

            page_content = document_to_markdown(page_doc)
            files_to_write.append((page_path, page_content))
            page_files.append(page_filename)

        index_path = artifact_dir / "index.md"

        index_doc = ParsedDocument(
            target=document.target,
            checksum=document.checksum,
            parser_name=document.parser_name,
        )
        index_doc.created_at = document.created_at
        index_doc.metadata = {
            "artifact_type": "page-directory",
            "page_unit": page_unit,
            "segments_total": total_segments,
        }
        index_doc.warnings = list(document.warnings)

        if page_files:
            label = "Page" if page_unit == "page" else "Segment"
            listing_lines = [f"# {label}s", ""]
            for position, filename in enumerate(page_files, start=1):
                listing_lines.append(f"- [{label} {position}](./{filename})")
            index_doc.add_segment("\n".join(listing_lines))
        elif document.is_empty():
            index_doc.add_segment("_No textual content was extracted from this document._")

        index_content = document_to_markdown(index_doc)
        files_to_write.append((index_path, index_content))

        # Write all files (local or GitHub)
        if self._defer_content_writes:
            # Accumulate files for batch commit
            self._pending_content_files.extend(files_to_write)
        elif self._github_client:
            # Immediate batch write via GitHub API to PR branch
            github_files = [
                (self._get_relative_path(path), content)
                for path, content in files_to_write
            ]
            if github_files:
                self._github_client.commit_files_batch(
                    files=github_files,
                    message=f"Add parsed content: {document.target.source[:80]}",
                    use_pr_branch=True,
                )
        else:
            # Write to local filesystem
            for path, content in files_to_write:
                _write_atomic_text(path, content)

        metadata = dict(document.metadata)
        metadata.update(
            {
                "artifact_type": "page-directory",
                "segments_total": total_segments,
                "page_unit": page_unit,
            }
        )
        entry = ManifestEntry(
            source=document.target.source,
            checksum=checksum,
            parser=document.parser_name,
            artifact_path=self.relative_artifact_path(index_path),
            processed_at=processed_at,
            status="empty" if document.is_empty() else "completed",
            metadata=metadata,
        )

        self.record_entry(entry)
        return entry

    def make_artifact_path(
        self,
        source: str,
        checksum: str,
        *,
        processed_at: datetime | None = None,
        suffix: str = ".md",
    ) -> Path:
        directory, base_name = self._prepare_artifact_directory(
            source,
            checksum,
            processed_at=processed_at,
        )
        if not suffix:
            return directory
        filename = f"{base_name}{suffix}" if suffix.startswith(".") else suffix
        return directory / filename

    def make_artifact_directory(
        self,
        source: str,
        checksum: str,
        *,
        processed_at: datetime | None = None,
    ) -> Path:
        directory, _ = self._prepare_artifact_directory(
            source,
            checksum,
            processed_at=processed_at,
        )
        return directory

    def relative_artifact_path(self, absolute_path: Path) -> str:
        return str(absolute_path.relative_to(self.root))

    def _load_manifest(self) -> Manifest:
        path = self.manifest_path
        if not path.exists():
            return Manifest()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Manifest.from_dict(raw)

    def _write_manifest(self) -> None:
        payload = self._manifest.to_dict()
        content = json.dumps(payload, indent=2, sort_keys=True)

        if self._github_client:
            rel_path = self._get_relative_path(self.manifest_path)
            # Use PR branch for content acquisition commits
            self._github_client.commit_to_pr_branch(
                path=rel_path,
                content=content,
                message="Update parsed document manifest",
            )
        else:
            tmp_path = self.manifest_path.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.replace(self.manifest_path)

    def _prepare_artifact_directory(
        self,
        source: str,
        checksum: str,
        *,
        processed_at: datetime | None = None,
    ) -> tuple[Path, str]:
        processed_at = processed_at or datetime.now(timezone.utc)
        year_folder = processed_at.strftime("%Y")
        slug = utils.slugify(source)
        fingerprint = checksum[:12] or utils.stable_checksum_for_source(source)[:12]
        base_name = f"{slug}-{fingerprint}"
        directory = self.root / year_folder / base_name
        utils.ensure_directory(directory)
        return directory, base_name


def _write_atomic_text(path: Path, content: str) -> None:
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _determine_segment_unit(document: ParsedDocument) -> str:
    if document.parser_name == "pdf":
        return "page"
    if document.metadata.get("page_count"):
        return "page"
    return "segment"
