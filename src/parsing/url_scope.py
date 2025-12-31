"""URL scope validation for site crawling.

This module provides the core constraint logic for the site crawler - ensuring
that only URLs within the defined scope boundary are processed.

Scope Types:
- "path": Most restrictive - URL must be under the source path
- "host": Single host - URL must be on the exact same host  
- "domain": All subdomains - URL can be on any subdomain of the base domain

Examples:
    >>> is_url_in_scope("https://example.com/docs/guide", "https://example.com/docs/", "path")
    True
    >>> is_url_in_scope("https://example.com/blog/", "https://example.com/docs/", "path")
    False
    >>> is_url_in_scope("https://shop.example.com/", "https://example.com/", "domain")
    True
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse, urlunparse
from typing import NamedTuple


class ParsedURL(NamedTuple):
    """Parsed URL components for scope checking."""
    scheme: str
    host: str
    port: str
    path: str
    query: str
    fragment: str


def parse_url(url: str) -> ParsedURL:
    """Parse a URL into components for scope checking.
    
    Args:
        url: The URL to parse
        
    Returns:
        ParsedURL with normalized components
    """
    parsed = urlparse(url)
    
    # Extract port from netloc if present
    host = parsed.netloc
    port = ""
    if ":" in host:
        # Handle IPv6 addresses
        if host.startswith("["):
            # IPv6: [::1]:8080
            bracket_end = host.find("]")
            if bracket_end != -1 and bracket_end + 1 < len(host) and host[bracket_end + 1] == ":":
                port = host[bracket_end + 2:]
                host = host[:bracket_end + 1]
        else:
            # IPv4 or hostname: example.com:8080
            host, port = host.rsplit(":", 1)
    
    # Normalize: lowercase host, ensure path has leading slash
    host = host.lower()
    path = parsed.path or "/"
    
    return ParsedURL(
        scheme=parsed.scheme.lower(),
        host=host,
        port=port,
        path=path,
        query=parsed.query,
        fragment=parsed.fragment,
    )


def normalize_url(url: str, strip_fragment: bool = True, strip_query: bool = False) -> str:
    """Normalize a URL for consistent comparison.
    
    Normalization includes:
    - Lowercase scheme and host
    - Remove default ports (80 for http, 443 for https)
    - Optionally strip fragment
    - Optionally strip query string
    - Ensure path has leading slash
    
    Args:
        url: The URL to normalize
        strip_fragment: Whether to remove the fragment (default True)
        strip_query: Whether to remove the query string (default False)
        
    Returns:
        Normalized URL string
    """
    parsed = parse_url(url)
    
    # Remove default ports
    port = parsed.port
    if port:
        if (parsed.scheme == "http" and port == "80") or \
           (parsed.scheme == "https" and port == "443"):
            port = ""
    
    # Reconstruct netloc
    netloc = parsed.host
    if port:
        netloc = f"{netloc}:{port}"
    
    # Handle fragment and query
    fragment = "" if strip_fragment else parsed.fragment
    query = "" if strip_query else parsed.query
    
    return urlunparse((
        parsed.scheme,
        netloc,
        parsed.path,
        "",  # params
        query,
        fragment,
    ))


def extract_base_domain(host: str) -> str:
    """Extract the base domain from a hostname.
    
    This is a simplified implementation that handles common cases.
    For production use, consider using the `tldextract` library.
    
    Examples:
        >>> extract_base_domain("www.example.com")
        "example.com"
        >>> extract_base_domain("shop.store.example.co.uk")
        "example.co.uk"
        >>> extract_base_domain("localhost")
        "localhost"
    
    Args:
        host: The hostname to extract base domain from
        
    Returns:
        The base domain (registrable domain)
    """
    # Handle IP addresses
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
        return host
    
    # Handle IPv6
    if host.startswith("[") or ":" in host:
        return host
    
    # Handle localhost and simple hostnames
    if "." not in host:
        return host
    
    # Common multi-part TLDs (simplified list)
    multi_part_tlds = {
        "co.uk", "org.uk", "gov.uk", "ac.uk",
        "com.au", "org.au", "gov.au", "edu.au",
        "co.nz", "org.nz", "gov.nz",
        "co.jp", "or.jp", "go.jp",
        "com.br", "org.br", "gov.br",
        "co.in", "org.in", "gov.in",
    }
    
    parts = host.lower().split(".")
    
    # Check for multi-part TLD
    if len(parts) >= 3:
        potential_tld = ".".join(parts[-2:])
        if potential_tld in multi_part_tlds:
            # Return domain + multi-part TLD
            return ".".join(parts[-3:])
    
    # Standard case: return last two parts
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    
    return host


def is_same_domain(host1: str, host2: str) -> bool:
    """Check if two hosts share the same base domain.
    
    Args:
        host1: First hostname
        host2: Second hostname
        
    Returns:
        True if both hosts share the same base domain
    """
    return extract_base_domain(host1.lower()) == extract_base_domain(host2.lower())


def is_url_in_scope(
    url: str,
    source_url: str,
    scope: str,
) -> bool:
    """Determine if a URL is within the crawl scope of the source URL.
    
    This is the CORE constraint of the crawler - no URL outside the
    source boundary should ever be fetched.
    
    Args:
        url: The URL to check
        source_url: The source URL defining the crawl boundary
        scope: Scope constraint - "path", "host", or "domain"
        
    Returns:
        True if the URL is within scope, False otherwise
        
    Raises:
        ValueError: If scope is not one of "path", "host", "domain"
        
    Examples:
        Path scope (most restrictive):
        >>> is_url_in_scope("https://example.com/docs/guide", "https://example.com/docs/", "path")
        True
        >>> is_url_in_scope("https://example.com/blog/", "https://example.com/docs/", "path")
        False
        
        Host scope:
        >>> is_url_in_scope("https://example.com/anything", "https://example.com/docs/", "host")
        True
        >>> is_url_in_scope("https://shop.example.com/", "https://example.com/", "host")
        False
        
        Domain scope (least restrictive):
        >>> is_url_in_scope("https://shop.example.com/", "https://example.com/", "domain")
        True
    """
    if scope not in ("path", "host", "domain"):
        raise ValueError(f"Invalid scope: {scope}. Must be 'path', 'host', or 'domain'")
    
    source = parse_url(source_url)
    target = parse_url(url)
    
    # Must be same scheme (http/https)
    if target.scheme != source.scheme:
        return False
    
    # Port must match if specified
    if source.port and target.port != source.port:
        return False
    
    if scope == "path":
        # Most restrictive: same host AND path starts with source path
        if target.host != source.host:
            return False
        
        # Normalize paths for comparison
        source_path = source.path.rstrip("/")
        target_path = target.path.rstrip("/")
        
        # Empty source path means root, matches everything
        if not source_path or source_path == "":
            return True
        
        # Target path must be exactly source path or start with source path + /
        return target_path == source_path or target_path.startswith(source_path + "/")
    
    elif scope == "host":
        # Single host: exact hostname match
        return target.host == source.host
    
    elif scope == "domain":
        # All subdomains: base domain must match
        return is_same_domain(target.host, source.host)
    
    return False


def resolve_url(base_url: str, relative_url: str) -> str:
    """Resolve a relative URL against a base URL.
    
    Args:
        base_url: The base URL (usually the page where the link was found)
        relative_url: The relative or absolute URL to resolve
        
    Returns:
        The resolved absolute URL
        
    Examples:
        >>> resolve_url("https://example.com/docs/guide", "../api/")
        "https://example.com/api/"
        >>> resolve_url("https://example.com/docs/", "/about")
        "https://example.com/about"
    """
    return urljoin(base_url, relative_url)


def is_valid_http_url(url: str) -> bool:
    """Check if a URL is a valid HTTP/HTTPS URL.
    
    Args:
        url: The URL to validate
        
    Returns:
        True if the URL is valid HTTP/HTTPS, False otherwise
    """
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def should_skip_url(url: str) -> tuple[bool, str]:
    """Check if a URL should be skipped during crawling.
    
    Returns:
        Tuple of (should_skip, reason)
    """
    # Skip non-HTTP URLs
    if not url:
        return True, "Empty URL"
    
    parsed = urlparse(url)
    
    # Skip javascript: and mailto: URLs
    if parsed.scheme in ("javascript", "mailto", "tel", "data", "file"):
        return True, f"Non-HTTP scheme: {parsed.scheme}"
    
    # Skip fragment-only URLs
    if url.startswith("#"):
        return True, "Fragment-only URL"
    
    # Skip common non-page extensions
    skip_extensions = {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",  # Images
        ".mp4", ".webm", ".avi", ".mov", ".wmv",  # Video
        ".mp3", ".wav", ".ogg", ".flac",  # Audio
        ".zip", ".tar", ".gz", ".rar", ".7z",  # Archives
        ".exe", ".dmg", ".msi", ".deb", ".rpm",  # Executables
        ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",  # Web assets
    }
    
    path_lower = parsed.path.lower()
    for ext in skip_extensions:
        if path_lower.endswith(ext):
            return True, f"Skipped extension: {ext}"
    
    return False, ""


def filter_urls_by_scope(
    urls: list[str],
    source_url: str,
    scope: str = "path",
) -> list[str]:
    """Filter a list of URLs to only those within the scope boundary.
    
    This is a convenience function that applies is_url_in_scope to a list
    of URLs and returns only those that pass the scope check.
    
    Args:
        urls: List of URLs to filter
        source_url: The source URL defining the scope boundary
        scope: Scope constraint - "path", "host", or "domain"
        
    Returns:
        List of URLs that are within scope
        
    Example:
        >>> filter_urls_by_scope(
        ...     ["https://example.com/docs/guide", "https://example.com/blog/"],
        ...     "https://example.com/docs/",
        ...     "path"
        ... )
        ["https://example.com/docs/guide"]
    """
    result = []
    for url in urls:
        if is_url_in_scope(url, source_url, scope):
            result.append(url)
    return result
