"""Web page parser implementation using trafilatura."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import requests
from requests import Response, Session
from requests.exceptions import RequestException
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
_DEFAULT_USER_AGENT = "speculum-principum-web-parser/1.0"

# Minimum content length to trigger rendering fallback
_MIN_CONTENT_FOR_SUCCESS = 100


@dataclass(slots=True)
class WebParser:
    """Concrete :class:`DocumentParser` for HTML sources and URLs.
    
    Supports optional JavaScript rendering via Playwright when static
    extraction yields insufficient content. Enable with `enable_rendering=True`.
    """

    name: str = "web"
    timeout: float = 10.0
    delay_seconds: float = 0.0
    wait_callback: Callable[[ParseTarget], None] | None = None
    user_agent: str = _DEFAULT_USER_AGENT
    enable_rendering: bool = False
    rendering_timeout: int = 30000  # milliseconds
    _session: Session | None = field(default=None, init=False, repr=False)

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
        self._apply_rate_limit(target)
        response = self._fetch(target.source)
        fetched_at = datetime.now(timezone.utc)
        media_type = _clean_content_type(response.headers.get("Content-Type"))

        document_target = ParseTarget(
            source=target.source,
            is_remote=True,
            media_type=media_type,
        )
        checksum = utils.sha256_bytes(response.content)
        document = ParsedDocument(target=document_target, checksum=checksum, parser_name=self.name)

        document.metadata.update(
            {
                "status_code": response.status_code,
                "fetched_at": fetched_at.isoformat(),
                "url": target.source,
                "final_url": response.url,
                "content_type": media_type,
                "encoding": response.encoding,
                "content_length": _response_content_length(response),
            }
        )

        # Try static extraction first
        self._populate_segments(document, response.text, document_target)
        
        # Check if we need rendering fallback
        if self.enable_rendering and self._needs_rendering_fallback(document, response.text):
            self._try_rendering_fallback(document, target, fetched_at)
        
        return document

    def _needs_rendering_fallback(self, document: ParsedDocument, html: str) -> bool:
        """Determine if we should try JavaScript rendering."""
        # If we got enough content, no need for rendering
        extracted_chars = document.metadata.get("extracted_characters", 0)
        if extracted_chars >= _MIN_CONTENT_FOR_SUCCESS:
            return False
        
        # Check for SPA indicators
        from .rendering import needs_rendering
        extracted_text = "\n".join(document.segments) if document.segments else None
        return needs_rendering(html, extracted_text)

    def _try_rendering_fallback(
        self, 
        document: ParsedDocument, 
        target: ParseTarget,
        fetched_at: datetime,
    ) -> None:
        """Attempt to extract content using browser rendering."""
        from .rendering import render_page, RenderingError, is_playwright_available
        
        if not is_playwright_available():
            document.warnings.append(
                "JavaScript rendering unavailable: Playwright not installed"
            )
            return
        
        logger.info("Static extraction insufficient, trying browser rendering for %s", target.source)
        
        try:
            rendered = render_page(
                target.source,
                headless=True,
                timeout=self.rendering_timeout,
                user_agent=self.user_agent,
            )
            
            # Update metadata with rendering info
            document.metadata["rendered"] = True
            document.metadata["rendered_at"] = datetime.now(timezone.utc).isoformat()
            document.metadata["final_url"] = rendered.final_url
            if rendered.title:
                document.metadata["title"] = rendered.title
            
            # Update checksum to reflect rendered content
            document.checksum = utils.sha256_bytes(rendered.html.encode("utf-8"))
            
            # Clear previous extraction and re-extract from rendered HTML
            document.segments.clear()
            document.warnings = [w for w in document.warnings if "No extractable" not in w and "empty extraction" not in w]
            
            # Re-populate segments from rendered HTML
            rendered_target = ParseTarget(
                source=rendered.final_url,
                is_remote=True,
                media_type="text/html",
            )
            self._populate_segments(document, rendered.html, rendered_target)
            
            if document.segments:
                logger.info(
                    "Browser rendering extracted %d characters from %s",
                    document.metadata.get("extracted_characters", 0),
                    target.source,
                )
            else:
                document.warnings.append(
                    "Browser rendering completed but extraction still yielded no content"
                )
                
        except RenderingError as e:
            document.warnings.append(f"Browser rendering failed: {e}")
            logger.warning("Rendering fallback failed for %s: %s", target.source, e)

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

    def _fetch(self, url: str) -> Response:
        session = self._ensure_session()
        headers = {"User-Agent": self.user_agent}
        try:
            response = session.get(url, timeout=self.timeout, headers=headers)
        except RequestException as exc:  # pragma: no cover - network failure path
            raise ParserError(f"Failed to fetch URL '{url}': {exc}") from exc

        if response.status_code >= 400:
            raise ParserError(f"Received HTTP {response.status_code} for URL '{url}'")

        return response

    def _ensure_session(self) -> Session:
        if self._session is None:
            self._session = requests.Session()
        return self._session

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


def _clean_content_type(value: str | None) -> str | None:
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower() or None


def _decode_html(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore"), "unknown"


def _response_content_length(response: Response) -> int:
    header_value = response.headers.get("Content-Length")
    if header_value and header_value.isdigit():
        return int(header_value)
    return len(response.content)


def _sleep(seconds: float) -> None:
    from time import sleep

    sleep(max(seconds, 0.0))


def _rewrite_key_value_tables(html: str) -> str:
    if BeautifulSoup is None:
        return html

    soup = BeautifulSoup(html, "html.parser")
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
