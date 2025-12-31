"""Web page parser implementation using Playwright and trafilatura."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import trafilatura
try:  # pragma: no cover - optional dependency fallback
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - executed only if dependency missing
    BeautifulSoup = None

from . import utils
from .base import ParsedDocument, ParseTarget, ParserError
from .markdown import document_to_markdown
from .registry import registry

logger = logging.getLogger(__name__)

_HTML_SUFFIXES = (".html", ".htm", ".xhtml")
_HTML_MEDIA_TYPES = ("text/html", "application/xhtml+xml")


@dataclass(slots=True)
class WebParser:
    """Concrete :class:`DocumentParser` for HTML sources and URLs.
    
    Uses Playwright browser rendering for all remote URL fetching to ensure
    accurate extraction from JavaScript-rendered pages. Local HTML files are
    parsed directly without browser rendering.
    """

    name: str = "web"
    timeout: int = 30000  # milliseconds for Playwright navigation
    delay_seconds: float = 0.0
    wait_callback: Callable[[ParseTarget], None] | None = None
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    def detect(self, target: ParseTarget) -> bool:
        is_url = utils.is_http_url(target.source)
        if target.is_remote or is_url:
            return is_url
        try:
            path = target.to_path()
        except ValueError:
            return False
        if not path.exists() or not path.is_file():
            return False
        if path.suffix.lower() in _HTML_SUFFIXES:
            return True
        media_type = (target.media_type or utils.guess_media_type(path) or "").lower()
        return media_type in _HTML_MEDIA_TYPES

    def extract(self, target: ParseTarget) -> ParsedDocument:
        is_url = utils.is_http_url(target.source)
        if target.is_remote or is_url:
            return self._extract_remote(target)
        return self._extract_local(target)

    def to_markdown(self, document: ParsedDocument) -> str:
        return document_to_markdown(document)

    def _extract_remote(self, target: ParseTarget) -> ParsedDocument:
        """Extract content from a remote URL using Playwright browser rendering."""
        from .rendering import render_page, RenderingError, is_playwright_available
        
        self._apply_rate_limit(target)
        fetched_at = datetime.now(timezone.utc)
        
        if not is_playwright_available():
            raise ParserError(
                "Playwright is required for remote URL parsing. "
                "Install with: pip install playwright && playwright install chromium"
            )
        
        logger.info("Fetching %s with browser rendering", target.source)
        
        try:
            rendered = render_page(
                target.source,
                user_agent=self.user_agent,
                headless=True,
                timeout=self.timeout,
            )
        except RenderingError as e:
            raise ParserError(f"Failed to fetch URL '{target.source}': {e}") from e
        
        document_target = ParseTarget(
            source=target.source,
            is_remote=True,
            media_type="text/html",
        )
        checksum = utils.sha256_bytes(rendered.html.encode("utf-8"))
        document = ParsedDocument(target=document_target, checksum=checksum, parser_name=self.name)
        
        document.metadata.update(
            {
                "fetched_at": fetched_at.isoformat(),
                "url": target.source,
                "final_url": rendered.final_url,
                "content_type": "text/html",
                "content_length": rendered.content_length,
                "rendered": True,
                "user_agent": rendered.user_agent,
            }
        )
        if rendered.title:
            document.metadata["title"] = rendered.title
        
        # Extract text content from rendered HTML
        self._populate_segments(document, rendered.html, document_target)
        
        if document.segments:
            logger.info(
                "Extracted %d characters from %s",
                document.metadata.get("extracted_characters", 0),
                target.source,
            )
        
        return document

    def _extract_local(self, target: ParseTarget) -> ParsedDocument:
        path = self._require_local_file(target)
        checksum = utils.sha256_path(path)
        raw = path.read_bytes()
        html, encoding = _decode_html(raw)

        document = ParsedDocument(target=target, checksum=checksum, parser_name=self.name)
        media_type = (target.media_type or utils.guess_media_type(path))
        document.metadata.update(
            {
                "file_size": path.stat().st_size,
                "content_type": media_type,
                "encoding": encoding,
            }
        )

        self._populate_segments(document, html, target)
        return document

    def _populate_segments(self, document: ParsedDocument, html: str, target: ParseTarget) -> None:
        normalized_html = _rewrite_key_value_tables(html)
        extracted = trafilatura.extract(
            normalized_html,
            url=target.source if target.is_remote else None,
        )
        if not extracted:
            document.warnings.append("No extractable text found in HTML content")
            return

        segments = [block.strip() for block in extracted.split("\n\n") if block.strip()]
        if not segments:
            document.warnings.append("HTML content yielded empty extraction")
            return

        document.extend_segments(segments)
        document.metadata.setdefault("extracted_characters", len(extracted))

    def _apply_rate_limit(self, target: ParseTarget) -> None:
        if self.wait_callback is not None:
            self.wait_callback(target)
        elif self.delay_seconds > 0:
            _sleep(self.delay_seconds)

    @staticmethod
    def _require_local_file(target: ParseTarget) -> Path:
        if target.is_remote:
            raise ParserError("Web parser expects local HTML files to be marked as non-remote")
        try:
            path = target.to_path()
        except ValueError as exc:  # pragma: no cover - defensive branch
            raise ParserError(str(exc)) from exc
        if not path.exists():
            raise ParserError(f"HTML file '{path}' does not exist")
        if not path.is_file():
            raise ParserError(f"HTML target '{path}' is not a file")
        return path


def _decode_html(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore"), "unknown"


def _sleep(seconds: float) -> None:
    from time import sleep

    sleep(max(seconds, 0.0))


def _rewrite_key_value_tables(html: str) -> str:
    if BeautifulSoup is None:
        return html

    soup = BeautifulSoup(html, "html.parser")
    
    # Remove aria-hidden elements before extraction (these are hidden from screen readers
    # and should not be included in text extraction)
    for hidden in soup.find_all(attrs={"aria-hidden": "true"}):
        hidden.decompose()
    
    # Remove elements with common "hide" CSS classes
    for hidden in soup.find_all(class_=lambda c: c and any(
        hide_pattern in c for hide_pattern in ["--hide", "hidden", "visually-hidden", "sr-only"]
    )):
        # Only remove if it looks like a visibility utility class
        hidden.decompose()
    
    tables = soup.find_all("table")
    for table in tables:
        rows = table.find_all("tr")
        if not rows or len(rows) > 20:
            continue

        key_value_pairs: list[tuple[str, list[str]]] = []
        for row in rows:
            cells = [cell for cell in row.find_all(["th", "td"], recursive=False)]
            if len(cells) != 2 or cells[0].name != "th" or cells[1].name != "td":
                key_value_pairs = []
                break

            label = _normalize_whitespace(cells[0].get_text(" ", strip=True))
            values = [
                _normalize_whitespace(token)
                for token in cells[1].stripped_strings
                if _normalize_whitespace(token)
            ]
            if not label or not values:
                key_value_pairs = []
                break
            key_value_pairs.append((label, values))

        if not key_value_pairs:
            continue

        wrapper = soup.new_tag("div", attrs={"class": "normalized-key-value"})
        for label, values in key_value_pairs:
            section = soup.new_tag("div", attrs={"class": "normalized-key-value__item"})
            label_tag = soup.new_tag("p")
            strong = soup.new_tag("strong")
            strong.string = f"{label}:"
            label_tag.append(strong)

            if len(values) == 1:
                label_tag.append(f" {values[0]}")
                section.append(label_tag)
            else:
                section.append(label_tag)
                list_tag = soup.new_tag("ul")
                for value in values:
                    item_tag = soup.new_tag("li")
                    item_tag.string = value
                    list_tag.append(item_tag)
                section.append(list_tag)

            wrapper.append(section)

        table.replace_with(wrapper)

    return str(soup)


def _normalize_whitespace(value: str) -> str:
    return " ".join(value.split())


web_parser = WebParser()
registry.register_parser(
    web_parser,
    media_types=_HTML_MEDIA_TYPES,
    suffixes=_HTML_SUFFIXES,
    priority=6,
    replace=True,
)

__all__ = ["WebParser", "web_parser"]
