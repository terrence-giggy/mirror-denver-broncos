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

    def record_entry(self, entry: ManifestEntry) -> None:
        self._manifest.upsert(entry)
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

        for existing in artifact_dir.glob("*.md"):
            if existing.name == "index.md":
                continue
            if existing.name.startswith(("page-", "segment-")):
                existing.unlink(missing_ok=True)

        page_unit = _determine_segment_unit(document)
        total_segments = len(document.segments)
        page_files: list[str] = []

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

            _write_atomic_text(page_path, document_to_markdown(page_doc))
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

        _write_atomic_text(index_path, document_to_markdown(index_doc))

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
            self._github_client.commit_file(
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
