"""Link extraction from HTML content.

This module provides functionality to extract and normalize links from HTML
documents for use in the site crawler.

Features:
- Extract links from <a href>, <link href>, and other link elements
- Resolve relative URLs against the base URL
- Filter out non-HTTP URLs (javascript:, mailto:, etc.)
- Normalize URLs for consistent comparison
- Track link context (anchor text, rel attributes)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import List, Set
from urllib.parse import urljoin, urlparse

from src.parsing.url_scope import (
    is_valid_http_url,
    normalize_url,
    should_skip_url,
)


@dataclass
class ExtractedLink:
    """Represents a link extracted from HTML content.
    
    Attributes:
        url: The absolute, normalized URL
        anchor_text: The text content of the link (if available)
        rel: The rel attribute value (e.g., "nofollow", "external")
        tag: The HTML tag the link came from (e.g., "a", "link")
        is_nofollow: Whether the link has rel="nofollow"
    """
    url: str
    anchor_text: str = ""
    rel: str = ""
    tag: str = "a"
    
    @property
    def is_nofollow(self) -> bool:
        """Check if the link has rel="nofollow"."""
        return "nofollow" in self.rel.lower()


class LinkExtractor(HTMLParser):
    """HTML parser that extracts links from content.
    
    Usage:
        extractor = LinkExtractor("https://example.com/page")
        extractor.feed(html_content)
        links = extractor.get_links()
    """
    
    # Tags and attributes that contain links
    LINK_ATTRS = {
        "a": "href",
        "link": "href",
        "area": "href",
    }
    
    def __init__(self, base_url: str):
        """Initialize the link extractor.
        
        Args:
            base_url: The URL of the page being parsed (for resolving relative URLs)
        """
        super().__init__()
        self.base_url = base_url
        self._links: List[ExtractedLink] = []
        self._seen_urls: Set[str] = set()
        self._current_anchor_text: List[str] = []
        self._current_link_url: str | None = None
        self._current_link_rel: str = ""
        self._in_anchor = False
    
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Handle opening tags."""
        attrs_dict = {k: v or "" for k, v in attrs}
        
        if tag == "a":
            href = attrs_dict.get("href", "")
            if href:
                self._in_anchor = True
                self._current_link_url = href
                self._current_link_rel = attrs_dict.get("rel", "")
                self._current_anchor_text = []
        
        elif tag == "base":
            # Update base URL if <base> tag is found
            href = attrs_dict.get("href", "")
            if href:
                self.base_url = urljoin(self.base_url, href)
        
        elif tag in self.LINK_ATTRS:
            href = attrs_dict.get(self.LINK_ATTRS[tag], "")
            if href:
                self._add_link(href, tag=tag, rel=attrs_dict.get("rel", ""))
    
    def handle_endtag(self, tag: str) -> None:
        """Handle closing tags."""
        if tag == "a" and self._in_anchor:
            if self._current_link_url:
                anchor_text = " ".join(self._current_anchor_text).strip()
                self._add_link(
                    self._current_link_url,
                    anchor_text=anchor_text,
                    tag="a",
                    rel=self._current_link_rel,
                )
            self._in_anchor = False
            self._current_link_url = None
            self._current_link_rel = ""
            self._current_anchor_text = []
    
    def handle_data(self, data: str) -> None:
        """Handle text content."""
        if self._in_anchor:
            self._current_anchor_text.append(data)
    
    def _add_link(
        self,
        href: str,
        anchor_text: str = "",
        tag: str = "a",
        rel: str = "",
    ) -> None:
        """Add a link to the collection after validation and normalization."""
        # Skip empty hrefs
        if not href or not href.strip():
            return
        
        href = href.strip()
        
        # Check if URL should be skipped
        skip, reason = should_skip_url(href)
        if skip:
            return
        
        # Resolve relative URLs
        try:
            absolute_url = urljoin(self.base_url, href)
        except Exception:
            return
        
        # Validate the resolved URL
        if not is_valid_http_url(absolute_url):
            return
        
        # Normalize the URL
        normalized = normalize_url(absolute_url, strip_fragment=True)
        
        # Skip duplicates
        if normalized in self._seen_urls:
            return
        
        self._seen_urls.add(normalized)
        self._links.append(ExtractedLink(
            url=normalized,
            anchor_text=anchor_text,
            rel=rel,
            tag=tag,
        ))
    
    def get_links(self) -> List[ExtractedLink]:
        """Get all extracted links.
        
        Returns:
            List of ExtractedLink objects
        """
        return self._links.copy()
    
    def get_urls(self) -> List[str]:
        """Get just the URLs (convenience method).
        
        Returns:
            List of URL strings
        """
        return [link.url for link in self._links]


def extract_links(html: str, base_url: str) -> List[ExtractedLink]:
    """Extract all links from HTML content.
    
    This is a convenience function that creates a LinkExtractor and
    returns the extracted links.
    
    Args:
        html: The HTML content to parse
        base_url: The URL of the page (for resolving relative URLs)
        
    Returns:
        List of ExtractedLink objects
        
    Example:
        >>> html = '<html><body><a href="/page">Link</a></body></html>'
        >>> links = extract_links(html, "https://example.com/")
        >>> links[0].url
        'https://example.com/page'
    """
    extractor = LinkExtractor(base_url)
    try:
        extractor.feed(html)
    except Exception:
        # HTML parser can raise on malformed HTML
        pass
    return extractor.get_links()


def extract_urls(html: str, base_url: str) -> List[str]:
    """Extract just the URLs from HTML content.
    
    This is a convenience function for when you only need the URLs.
    
    Args:
        html: The HTML content to parse
        base_url: The URL of the page (for resolving relative URLs)
        
    Returns:
        List of URL strings
    """
    return [link.url for link in extract_links(html, base_url)]


def filter_links_by_scope(
    links: List[ExtractedLink],
    source_url: str,
    scope: str,
) -> tuple[List[ExtractedLink], List[ExtractedLink]]:
    """Filter links by scope, returning in-scope and out-of-scope lists.
    
    Args:
        links: List of ExtractedLink objects to filter
        source_url: The source URL defining the crawl boundary
        scope: Scope constraint - "path", "host", or "domain"
        
    Returns:
        Tuple of (in_scope_links, out_of_scope_links)
    """
    from src.parsing.url_scope import is_url_in_scope
    
    in_scope: List[ExtractedLink] = []
    out_of_scope: List[ExtractedLink] = []
    
    for link in links:
        if is_url_in_scope(link.url, source_url, scope):
            in_scope.append(link)
        else:
            out_of_scope.append(link)
    
    return in_scope, out_of_scope


def extract_title(html: str) -> str | None:
    """Extract the page title from HTML content.
    
    Args:
        html: The HTML content to parse
        
    Returns:
        The page title, or None if not found
    """
    # Simple regex-based extraction for efficiency
    match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def count_links(html: str, base_url: str, source_url: str, scope: str) -> tuple[int, int]:
    """Count total links and in-scope links in HTML content.
    
    This is useful for page statistics without storing all the links.
    
    Args:
        html: The HTML content to parse
        base_url: The URL of the page (for resolving relative URLs)
        source_url: The source URL defining the crawl boundary
        scope: Scope constraint
        
    Returns:
        Tuple of (total_links, in_scope_links)
    """
    links = extract_links(html, base_url)
    in_scope, _ = filter_links_by_scope(links, source_url, scope)
    return len(links), len(in_scope)
