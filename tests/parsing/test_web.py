"""Tests for the web parser implementation."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.parsing import registry
from src.parsing.base import ParseTarget, ParserError
from src.parsing.rendering import RenderedPage, RenderingError
from src.parsing.web import WebParser, web_parser


def _sample_html(title: str = "Sample Title", body: str = "Hello world") -> str:
    return f"""
    <html>
      <head><title>{title}</title></head>
      <body>
        <article>
          <h1>{title}</h1>
          <p>{body}</p>
        </article>
      </body>
    </html>
    """.strip()


def _mock_rendered_page(url: str, html: str, title: str | None = None) -> RenderedPage:
    """Create a mock RenderedPage for testing."""
    return RenderedPage(
        url=url,
        final_url=url,
        html=html,
        title=title,
    )


class TestWebParserRemote:
    """Tests for remote URL extraction using Playwright."""

    def test_extracts_remote_content(self) -> None:
        """WebParser extracts content from rendered HTML."""
        url = "https://example.com/article"
        html = _sample_html(body="Remote content body")
        mock_rendered = _mock_rendered_page(url, html, title="Sample Title")

        parser = WebParser()
        target = ParseTarget(source=url, is_remote=True)

        with patch("src.parsing.rendering.is_playwright_available", return_value=True):
            with patch("src.parsing.rendering.render_page", return_value=mock_rendered):
                assert parser.detect(target)
                document = parser.extract(target)

        assert document.segments
        combined = "\n".join(document.segments)
        assert "Remote content body" in combined
        assert document.metadata["content_type"] == "text/html"
        assert document.metadata["rendered"] is True
        assert document.metadata["final_url"] == url
        assert document.metadata["title"] == "Sample Title"

        markdown = parser.to_markdown(document)
        assert "Remote content body" in markdown

    def test_handles_rendering_error(self) -> None:
        """WebParser raises ParserError when Playwright rendering fails."""
        url = "https://example.com/bad"
        parser = WebParser()
        target = ParseTarget(source=url, is_remote=True)

        with patch("src.parsing.rendering.is_playwright_available", return_value=True):
            with patch(
                "src.parsing.rendering.render_page",
                side_effect=RenderingError("HTTP 500 error"),
            ):
                with pytest.raises(ParserError) as exc_info:
                    parser.extract(target)
                
                assert "HTTP 500 error" in str(exc_info.value)

    def test_raises_error_when_playwright_unavailable(self) -> None:
        """WebParser raises ParserError when Playwright is not installed."""
        url = "https://example.com/article"
        parser = WebParser()
        target = ParseTarget(source=url, is_remote=True)

        with patch("src.parsing.rendering.is_playwright_available", return_value=False):
            with pytest.raises(ParserError) as exc_info:
                parser.extract(target)
            
            assert "Playwright is required" in str(exc_info.value)

    def test_registration_supports_remote_targets(self) -> None:
        """Registry correctly routes remote HTML targets to WebParser."""
        url = "https://example.com/registry"
        html = _sample_html(body="Registry content")
        mock_rendered = _mock_rendered_page(url, html)

        target = ParseTarget(source=url, is_remote=True)
        parser = registry.require_parser(target)
        assert parser.name == web_parser.name

        with patch("src.parsing.rendering.is_playwright_available", return_value=True):
            with patch("src.parsing.rendering.render_page", return_value=mock_rendered):
                document = parser.extract(target)

        assert any("Registry content" in segment for segment in document.segments)

    def test_passes_user_agent_to_render_page(self) -> None:
        """WebParser passes its user_agent to render_page."""
        url = "https://example.com/ua-test"
        html = _sample_html(body="UA test")
        mock_rendered = _mock_rendered_page(url, html)
        custom_ua = "CustomBot/1.0"

        parser = WebParser(user_agent=custom_ua)
        target = ParseTarget(source=url, is_remote=True)

        with patch("src.parsing.rendering.is_playwright_available", return_value=True):
            with patch("src.parsing.rendering.render_page", return_value=mock_rendered) as mock_render:
                parser.extract(target)
                
                mock_render.assert_called_once()
                call_kwargs = mock_render.call_args.kwargs
                assert call_kwargs["user_agent"] == custom_ua


class TestWebParserLocal:
    """Tests for local HTML file extraction."""

    def test_extracts_local_file(self, tmp_path) -> None:
        """WebParser extracts content from local HTML files."""
        html_path = tmp_path / "sample.html"
        html_path.write_text(_sample_html(body="Local file body"), encoding="utf-8")

        parser = WebParser()
        target = ParseTarget(source=str(html_path))

        assert parser.detect(target)

        document = parser.extract(target)

        assert any("Local file body" in segment for segment in document.segments)
        assert document.metadata["file_size"] == html_path.stat().st_size
        assert document.metadata["content_type"] in {None, "text/html", "application/xhtml+xml"}

    def test_warns_when_extraction_is_empty(self, monkeypatch, tmp_path) -> None:
        """WebParser adds warning when trafilatura yields no content."""
        html_path = tmp_path / "empty.html"
        html_path.write_text("<html><body></body></html>", encoding="utf-8")

        parser = WebParser()
        target = ParseTarget(source=str(html_path))

        def _fake_extract(_html: str, url: str | None = None) -> str | None:
            return None

        monkeypatch.setattr("trafilatura.extract", _fake_extract)

        document = parser.extract(target)

        assert not document.segments
        assert any("No extractable text" in warning for warning in document.warnings)


class TestWebParserDetection:
    """Tests for file/URL detection logic."""

    def test_detects_remote_url(self) -> None:
        """WebParser detects HTTP URLs as valid targets."""
        parser = WebParser()
        
        assert parser.detect(ParseTarget(source="https://example.com", is_remote=True))
        assert parser.detect(ParseTarget(source="http://example.com", is_remote=True))
        assert not parser.detect(ParseTarget(source="/local/file.txt", is_remote=False))

    def test_detects_local_html_by_suffix(self, tmp_path) -> None:
        """WebParser detects local files by .html suffix."""
        parser = WebParser()
        
        html_file = tmp_path / "page.html"
        html_file.write_text("<html></html>")
        
        htm_file = tmp_path / "page.htm"
        htm_file.write_text("<html></html>")
        
        txt_file = tmp_path / "page.txt"
        txt_file.write_text("not html")
        
        assert parser.detect(ParseTarget(source=str(html_file)))
        assert parser.detect(ParseTarget(source=str(htm_file)))
        assert not parser.detect(ParseTarget(source=str(txt_file)))
