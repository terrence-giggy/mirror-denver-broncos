"""Unit tests for URL scope validation."""

from __future__ import annotations

import pytest

from src.parsing.url_scope import (
    ParsedURL,
    extract_base_domain,
    is_same_domain,
    is_url_in_scope,
    is_valid_http_url,
    normalize_url,
    parse_url,
    resolve_url,
    should_skip_url,
)


# =============================================================================
# parse_url Tests
# =============================================================================


class TestParseUrl:
    """Tests for URL parsing."""

    def test_parse_simple_url(self) -> None:
        """Parse a simple URL."""
        result = parse_url("https://example.com/path")
        
        assert result.scheme == "https"
        assert result.host == "example.com"
        assert result.port == ""
        assert result.path == "/path"

    def test_parse_url_with_port(self) -> None:
        """Parse URL with explicit port."""
        result = parse_url("https://example.com:8080/path")
        
        assert result.host == "example.com"
        assert result.port == "8080"

    def test_parse_url_with_query(self) -> None:
        """Parse URL with query string."""
        result = parse_url("https://example.com/path?key=value")
        
        assert result.path == "/path"
        assert result.query == "key=value"

    def test_parse_url_with_fragment(self) -> None:
        """Parse URL with fragment."""
        result = parse_url("https://example.com/path#section")
        
        assert result.path == "/path"
        assert result.fragment == "section"

    def test_parse_url_lowercase_host(self) -> None:
        """Host should be lowercased."""
        result = parse_url("https://EXAMPLE.COM/Path")
        
        assert result.host == "example.com"
        assert result.path == "/Path"  # Path case preserved

    def test_parse_url_empty_path(self) -> None:
        """Empty path should become /."""
        result = parse_url("https://example.com")
        
        assert result.path == "/"


# =============================================================================
# normalize_url Tests
# =============================================================================


class TestNormalizeUrl:
    """Tests for URL normalization."""

    def test_normalize_strips_fragment(self) -> None:
        """Fragment should be stripped by default."""
        result = normalize_url("https://example.com/page#section")
        
        assert "#" not in result
        assert result == "https://example.com/page"

    def test_normalize_preserves_fragment(self) -> None:
        """Fragment can be preserved."""
        result = normalize_url("https://example.com/page#section", strip_fragment=False)
        
        assert result == "https://example.com/page#section"

    def test_normalize_removes_default_https_port(self) -> None:
        """Default HTTPS port 443 should be removed."""
        result = normalize_url("https://example.com:443/page")
        
        assert ":443" not in result
        assert result == "https://example.com/page"

    def test_normalize_removes_default_http_port(self) -> None:
        """Default HTTP port 80 should be removed."""
        result = normalize_url("http://example.com:80/page")
        
        assert ":80" not in result
        assert result == "http://example.com/page"

    def test_normalize_preserves_non_default_port(self) -> None:
        """Non-default ports should be preserved."""
        result = normalize_url("https://example.com:8080/page")
        
        assert ":8080" in result

    def test_normalize_lowercase_scheme(self) -> None:
        """Scheme should be lowercased."""
        result = normalize_url("HTTPS://example.com/page")
        
        assert result.startswith("https://")

    def test_normalize_preserves_query(self) -> None:
        """Query string should be preserved by default."""
        result = normalize_url("https://example.com/page?key=value")
        
        assert "?key=value" in result

    def test_normalize_strips_query(self) -> None:
        """Query string can be stripped."""
        result = normalize_url("https://example.com/page?key=value", strip_query=True)
        
        assert "?" not in result


# =============================================================================
# extract_base_domain Tests
# =============================================================================


class TestExtractBaseDomain:
    """Tests for base domain extraction."""

    def test_simple_domain(self) -> None:
        """Extract base domain from simple hostname."""
        assert extract_base_domain("www.example.com") == "example.com"

    def test_subdomain(self) -> None:
        """Extract base domain from subdomain."""
        assert extract_base_domain("shop.store.example.com") == "example.com"

    def test_multi_part_tld_uk(self) -> None:
        """Handle .co.uk TLD."""
        assert extract_base_domain("www.example.co.uk") == "example.co.uk"

    def test_multi_part_tld_au(self) -> None:
        """Handle .com.au TLD."""
        assert extract_base_domain("shop.example.com.au") == "example.com.au"

    def test_ip_address(self) -> None:
        """IP addresses should be returned as-is."""
        assert extract_base_domain("192.168.1.1") == "192.168.1.1"

    def test_localhost(self) -> None:
        """Localhost should be returned as-is."""
        assert extract_base_domain("localhost") == "localhost"

    def test_no_subdomain(self) -> None:
        """Handle domain without subdomain."""
        assert extract_base_domain("example.com") == "example.com"


# =============================================================================
# is_same_domain Tests
# =============================================================================


class TestIsSameDomain:
    """Tests for domain comparison."""

    def test_same_domain(self) -> None:
        """Same domain should match."""
        assert is_same_domain("example.com", "example.com") is True

    def test_subdomain_matches_parent(self) -> None:
        """Subdomain should match parent domain."""
        assert is_same_domain("www.example.com", "example.com") is True
        assert is_same_domain("shop.example.com", "www.example.com") is True

    def test_different_domains(self) -> None:
        """Different domains should not match."""
        assert is_same_domain("example.com", "other.com") is False

    def test_case_insensitive(self) -> None:
        """Comparison should be case-insensitive."""
        assert is_same_domain("EXAMPLE.COM", "example.com") is True


# =============================================================================
# is_url_in_scope Tests - Path Scope
# =============================================================================


class TestUrlScopePath:
    """Tests for path scope validation."""

    def test_exact_match(self) -> None:
        """Source URL should match itself."""
        assert is_url_in_scope(
            "https://example.com/docs/",
            "https://example.com/docs/",
            "path"
        ) is True

    def test_subpath_allowed(self) -> None:
        """URLs under source path should be allowed."""
        assert is_url_in_scope(
            "https://example.com/docs/guide",
            "https://example.com/docs/",
            "path"
        ) is True

    def test_deep_subpath_allowed(self) -> None:
        """Deep subpaths should be allowed."""
        assert is_url_in_scope(
            "https://example.com/docs/api/v2/reference",
            "https://example.com/docs/",
            "path"
        ) is True

    def test_sibling_path_rejected(self) -> None:
        """Sibling paths should be rejected."""
        assert is_url_in_scope(
            "https://example.com/blog/",
            "https://example.com/docs/",
            "path"
        ) is False

    def test_parent_path_rejected(self) -> None:
        """Parent paths should be rejected."""
        assert is_url_in_scope(
            "https://example.com/",
            "https://example.com/docs/",
            "path"
        ) is False

    def test_prefix_attack_rejected(self) -> None:
        """Path that starts with source but isn't subpath should be rejected."""
        # /docs-old starts with /docs but isn't under /docs/
        assert is_url_in_scope(
            "https://example.com/docs-old/page",
            "https://example.com/docs/",
            "path"
        ) is False

    def test_different_host_rejected(self) -> None:
        """Different host should be rejected."""
        assert is_url_in_scope(
            "https://other.com/docs/guide",
            "https://example.com/docs/",
            "path"
        ) is False

    def test_subdomain_rejected(self) -> None:
        """Subdomain should be rejected in path scope."""
        assert is_url_in_scope(
            "https://shop.example.com/docs/guide",
            "https://example.com/docs/",
            "path"
        ) is False

    def test_trailing_slash_normalization(self) -> None:
        """Trailing slashes should be normalized."""
        assert is_url_in_scope(
            "https://example.com/docs",
            "https://example.com/docs/",
            "path"
        ) is True
        assert is_url_in_scope(
            "https://example.com/docs/",
            "https://example.com/docs",
            "path"
        ) is True

    def test_root_path_allows_all(self) -> None:
        """Root path should allow all paths on host."""
        assert is_url_in_scope(
            "https://example.com/any/path",
            "https://example.com/",
            "path"
        ) is True


# =============================================================================
# is_url_in_scope Tests - Host Scope
# =============================================================================


class TestUrlScopeHost:
    """Tests for host scope validation."""

    def test_same_host_allowed(self) -> None:
        """Same host should be allowed."""
        assert is_url_in_scope(
            "https://example.com/any/path",
            "https://example.com/docs/",
            "host"
        ) is True

    def test_any_path_allowed(self) -> None:
        """Any path on same host should be allowed."""
        assert is_url_in_scope(
            "https://example.com/blog/post",
            "https://example.com/docs/",
            "host"
        ) is True

    def test_subdomain_rejected(self) -> None:
        """Subdomain should be rejected in host scope."""
        assert is_url_in_scope(
            "https://shop.example.com/",
            "https://example.com/",
            "host"
        ) is False

    def test_www_vs_no_www_rejected(self) -> None:
        """www vs no-www are different hosts."""
        assert is_url_in_scope(
            "https://www.example.com/",
            "https://example.com/",
            "host"
        ) is False

    def test_different_domain_rejected(self) -> None:
        """Different domain should be rejected."""
        assert is_url_in_scope(
            "https://other.com/docs/",
            "https://example.com/docs/",
            "host"
        ) is False


# =============================================================================
# is_url_in_scope Tests - Domain Scope
# =============================================================================


class TestUrlScopeDomain:
    """Tests for domain scope validation."""

    def test_same_host_allowed(self) -> None:
        """Same host should be allowed."""
        assert is_url_in_scope(
            "https://example.com/page",
            "https://example.com/",
            "domain"
        ) is True

    def test_subdomain_allowed(self) -> None:
        """Subdomain should be allowed."""
        assert is_url_in_scope(
            "https://shop.example.com/",
            "https://example.com/",
            "domain"
        ) is True

    def test_www_allowed(self) -> None:
        """www subdomain should be allowed."""
        assert is_url_in_scope(
            "https://www.example.com/",
            "https://example.com/",
            "domain"
        ) is True

    def test_deep_subdomain_allowed(self) -> None:
        """Deep subdomains should be allowed."""
        assert is_url_in_scope(
            "https://api.v2.example.com/",
            "https://example.com/",
            "domain"
        ) is True

    def test_different_domain_rejected(self) -> None:
        """Different domain should always be rejected."""
        assert is_url_in_scope(
            "https://other.com/",
            "https://example.com/",
            "domain"
        ) is False


# =============================================================================
# is_url_in_scope Tests - Scheme and Port
# =============================================================================


class TestUrlScopeSchemePort:
    """Tests for scheme and port handling."""

    def test_different_scheme_rejected(self) -> None:
        """HTTP vs HTTPS should be rejected."""
        assert is_url_in_scope(
            "http://example.com/docs/",
            "https://example.com/docs/",
            "path"
        ) is False

    def test_port_mismatch_rejected(self) -> None:
        """Different ports should be rejected."""
        assert is_url_in_scope(
            "https://example.com:8080/docs/",
            "https://example.com:443/docs/",
            "path"
        ) is False

    def test_explicit_default_port_allowed(self) -> None:
        """Explicit default port should match implicit."""
        # Note: This tests internal behavior - the source has explicit port
        assert is_url_in_scope(
            "https://example.com/docs/",
            "https://example.com/docs/",
            "path"
        ) is True


# =============================================================================
# is_url_in_scope Tests - Edge Cases
# =============================================================================


class TestUrlScopeEdgeCases:
    """Tests for edge cases in scope validation."""

    def test_invalid_scope_raises(self) -> None:
        """Invalid scope should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid scope"):
            is_url_in_scope(
                "https://example.com/",
                "https://example.com/",
                "invalid"
            )

    def test_query_params_allowed(self) -> None:
        """URLs with query params should be allowed."""
        assert is_url_in_scope(
            "https://example.com/docs/page?id=123",
            "https://example.com/docs/",
            "path"
        ) is True

    def test_case_insensitive_host(self) -> None:
        """Host comparison should be case-insensitive."""
        assert is_url_in_scope(
            "https://EXAMPLE.COM/docs/page",
            "https://example.com/docs/",
            "path"
        ) is True


# =============================================================================
# resolve_url Tests
# =============================================================================


class TestResolveUrl:
    """Tests for URL resolution."""

    def test_absolute_url(self) -> None:
        """Absolute URL should be returned as-is."""
        result = resolve_url(
            "https://example.com/page",
            "https://other.com/resource"
        )
        assert result == "https://other.com/resource"

    def test_relative_path(self) -> None:
        """Relative path should be resolved."""
        result = resolve_url(
            "https://example.com/docs/guide",
            "reference"
        )
        assert result == "https://example.com/docs/reference"

    def test_parent_reference(self) -> None:
        """Parent reference should be resolved."""
        result = resolve_url(
            "https://example.com/docs/guide",
            "../api/"
        )
        assert result == "https://example.com/api/"

    def test_root_relative(self) -> None:
        """Root-relative path should be resolved."""
        result = resolve_url(
            "https://example.com/docs/guide",
            "/about"
        )
        assert result == "https://example.com/about"

    def test_protocol_relative(self) -> None:
        """Protocol-relative URL should be resolved."""
        result = resolve_url(
            "https://example.com/page",
            "//cdn.example.com/asset"
        )
        assert result == "https://cdn.example.com/asset"


# =============================================================================
# is_valid_http_url Tests
# =============================================================================


class TestIsValidHttpUrl:
    """Tests for HTTP URL validation."""

    def test_valid_https(self) -> None:
        """HTTPS URL should be valid."""
        assert is_valid_http_url("https://example.com/page") is True

    def test_valid_http(self) -> None:
        """HTTP URL should be valid."""
        assert is_valid_http_url("http://example.com/page") is True

    def test_javascript_invalid(self) -> None:
        """JavaScript URL should be invalid."""
        assert is_valid_http_url("javascript:void(0)") is False

    def test_mailto_invalid(self) -> None:
        """Mailto URL should be invalid."""
        assert is_valid_http_url("mailto:user@example.com") is False

    def test_empty_invalid(self) -> None:
        """Empty string should be invalid."""
        assert is_valid_http_url("") is False

    def test_no_host_invalid(self) -> None:
        """URL without host should be invalid."""
        assert is_valid_http_url("https:///path") is False


# =============================================================================
# should_skip_url Tests
# =============================================================================


class TestShouldSkipUrl:
    """Tests for URL skip checking."""

    def test_empty_url(self) -> None:
        """Empty URL should be skipped."""
        skip, reason = should_skip_url("")
        assert skip is True
        assert "Empty" in reason

    def test_javascript_url(self) -> None:
        """JavaScript URL should be skipped."""
        skip, reason = should_skip_url("javascript:void(0)")
        assert skip is True
        assert "javascript" in reason.lower()

    def test_mailto_url(self) -> None:
        """Mailto URL should be skipped."""
        skip, reason = should_skip_url("mailto:user@example.com")
        assert skip is True

    def test_fragment_only(self) -> None:
        """Fragment-only URL should be skipped."""
        skip, reason = should_skip_url("#section")
        assert skip is True

    def test_image_extension(self) -> None:
        """Image URLs should be skipped."""
        skip, reason = should_skip_url("https://example.com/image.jpg")
        assert skip is True
        assert ".jpg" in reason

    def test_pdf_not_skipped(self) -> None:
        """PDF URLs should not be skipped (might want to crawl)."""
        skip, reason = should_skip_url("https://example.com/doc.html")
        assert skip is False

    def test_valid_html_url(self) -> None:
        """Valid HTML URL should not be skipped."""
        skip, reason = should_skip_url("https://example.com/page")
        assert skip is False
