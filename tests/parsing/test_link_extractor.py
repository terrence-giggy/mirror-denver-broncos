"""Unit tests for link extraction and robots.txt parsing."""

from __future__ import annotations

import pytest

from src.parsing.link_extractor import (
    ExtractedLink,
    LinkExtractor,
    count_links,
    extract_links,
    extract_title,
    extract_urls,
    filter_links_by_scope,
)
from src.parsing.robots import (
    RobotRule,
    RobotRuleset,
    RobotsTxt,
    RobotsChecker,
    parse_robots_txt,
)


# =============================================================================
# ExtractedLink Tests
# =============================================================================


class TestExtractedLink:
    """Tests for ExtractedLink dataclass."""

    def test_is_nofollow_true(self) -> None:
        """Link with nofollow should report is_nofollow=True."""
        link = ExtractedLink(
            url="https://example.com/",
            rel="nofollow",
        )
        assert link.is_nofollow is True

    def test_is_nofollow_in_list(self) -> None:
        """Link with nofollow in list should report is_nofollow=True."""
        link = ExtractedLink(
            url="https://example.com/",
            rel="external nofollow noopener",
        )
        assert link.is_nofollow is True

    def test_is_nofollow_false(self) -> None:
        """Link without nofollow should report is_nofollow=False."""
        link = ExtractedLink(
            url="https://example.com/",
            rel="external",
        )
        assert link.is_nofollow is False


# =============================================================================
# LinkExtractor Tests
# =============================================================================


class TestLinkExtractor:
    """Tests for LinkExtractor HTML parser."""

    def test_extract_simple_link(self) -> None:
        """Extract a simple anchor link."""
        html = '<a href="/page">Link</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert links[0].url == "https://example.com/page"
        assert links[0].anchor_text == "Link"

    def test_extract_absolute_link(self) -> None:
        """Extract an absolute URL."""
        html = '<a href="https://other.com/page">Link</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert links[0].url == "https://other.com/page"

    def test_extract_relative_link(self) -> None:
        """Extract and resolve a relative URL."""
        html = '<a href="sibling">Link</a>'
        extractor = LinkExtractor("https://example.com/docs/guide")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert links[0].url == "https://example.com/docs/sibling"

    def test_extract_parent_relative_link(self) -> None:
        """Extract and resolve parent-relative URL."""
        html = '<a href="../other">Link</a>'
        extractor = LinkExtractor("https://example.com/docs/guide")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert links[0].url == "https://example.com/other"

    def test_skip_javascript_link(self) -> None:
        """JavaScript links should be skipped."""
        html = '<a href="javascript:void(0)">Click</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        assert len(extractor.get_links()) == 0

    def test_skip_mailto_link(self) -> None:
        """Mailto links should be skipped."""
        html = '<a href="mailto:user@example.com">Email</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        assert len(extractor.get_links()) == 0

    def test_skip_fragment_only(self) -> None:
        """Fragment-only links should be skipped."""
        html = '<a href="#section">Jump</a>'
        extractor = LinkExtractor("https://example.com/page")
        extractor.feed(html)
        
        assert len(extractor.get_links()) == 0

    def test_strip_fragment_from_url(self) -> None:
        """Fragments should be stripped from URLs."""
        html = '<a href="/page#section">Link</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert "#" not in links[0].url

    def test_deduplicate_links(self) -> None:
        """Duplicate URLs should be deduplicated."""
        html = '''
        <a href="/page">First</a>
        <a href="/page">Second</a>
        <a href="/page#section">Third</a>
        '''
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1

    def test_extract_with_rel_attribute(self) -> None:
        """Extract rel attribute from links."""
        html = '<a href="/page" rel="nofollow external">Link</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert "nofollow" in links[0].rel

    def test_extract_multiple_links(self) -> None:
        """Extract multiple links from HTML."""
        html = '''
        <a href="/page1">Link 1</a>
        <a href="/page2">Link 2</a>
        <a href="/page3">Link 3</a>
        '''
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 3

    def test_get_urls_convenience(self) -> None:
        """get_urls should return just URL strings."""
        html = '<a href="/page1">Link 1</a><a href="/page2">Link 2</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        urls = extractor.get_urls()
        assert urls == ["https://example.com/page1", "https://example.com/page2"]

    def test_handle_base_tag(self) -> None:
        """base tag should update base URL."""
        html = '''
        <head><base href="https://cdn.example.com/"></head>
        <body><a href="page">Link</a></body>
        '''
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert links[0].url == "https://cdn.example.com/page"

    def test_extract_link_tag(self) -> None:
        """Extract links from link elements."""
        html = '<link rel="stylesheet" href="/style.css">'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        # CSS files are filtered out by extension
        assert len(links) == 0

    def test_skip_image_urls(self) -> None:
        """Image URLs should be skipped."""
        html = '<a href="/image.jpg">Image</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        assert len(extractor.get_links()) == 0

    def test_complex_anchor_text(self) -> None:
        """Extract anchor text from complex content."""
        html = '<a href="/page">Click <strong>here</strong> for more</a>'
        extractor = LinkExtractor("https://example.com/")
        extractor.feed(html)
        
        links = extractor.get_links()
        assert len(links) == 1
        assert "Click" in links[0].anchor_text
        assert "here" in links[0].anchor_text
        assert "more" in links[0].anchor_text


# =============================================================================
# Convenience Function Tests
# =============================================================================


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_extract_links_function(self) -> None:
        """extract_links function should work correctly."""
        html = '<a href="/page">Link</a>'
        links = extract_links(html, "https://example.com/")
        
        assert len(links) == 1
        assert links[0].url == "https://example.com/page"

    def test_extract_urls_function(self) -> None:
        """extract_urls function should return URL strings."""
        html = '<a href="/page1">Link 1</a><a href="/page2">Link 2</a>'
        urls = extract_urls(html, "https://example.com/")
        
        assert urls == ["https://example.com/page1", "https://example.com/page2"]

    def test_extract_title(self) -> None:
        """extract_title should find page title."""
        html = '<html><head><title>Page Title</title></head></html>'
        title = extract_title(html)
        
        assert title == "Page Title"

    def test_extract_title_not_found(self) -> None:
        """extract_title should return None if no title."""
        html = '<html><head></head></html>'
        title = extract_title(html)
        
        assert title is None

    def test_extract_title_with_whitespace(self) -> None:
        """extract_title should strip whitespace."""
        html = '<title>  Page Title  </title>'
        title = extract_title(html)
        
        assert title == "Page Title"

    def test_filter_links_by_scope(self) -> None:
        """filter_links_by_scope should separate in-scope and out-of-scope."""
        links = [
            ExtractedLink(url="https://example.com/docs/page1"),
            ExtractedLink(url="https://example.com/blog/post"),
            ExtractedLink(url="https://example.com/docs/page2"),
            ExtractedLink(url="https://other.com/page"),
        ]
        
        in_scope, out_of_scope = filter_links_by_scope(
            links,
            "https://example.com/docs/",
            "path"
        )
        
        assert len(in_scope) == 2
        assert len(out_of_scope) == 2
        assert all("docs" in link.url for link in in_scope)

    def test_count_links(self) -> None:
        """count_links should return correct counts."""
        html = '''
        <a href="/docs/page1">In scope</a>
        <a href="/docs/page2">In scope</a>
        <a href="/blog/post">Out of scope</a>
        '''
        
        total, in_scope = count_links(
            html,
            "https://example.com/docs/guide",
            "https://example.com/docs/",
            "path"
        )
        
        assert total == 3
        assert in_scope == 2


# =============================================================================
# RobotRule Tests
# =============================================================================


class TestRobotRule:
    """Tests for RobotRule matching."""

    def test_exact_match(self) -> None:
        """Exact path match."""
        rule = RobotRule(path="/admin", allowed=False)
        
        assert rule.matches("/admin") is True
        assert rule.matches("/admin/") is True
        assert rule.matches("/admin/page") is True

    def test_prefix_match(self) -> None:
        """Prefix matching."""
        rule = RobotRule(path="/private/", allowed=False)
        
        assert rule.matches("/private/") is True
        assert rule.matches("/private/data") is True
        assert rule.matches("/public/") is False

    def test_wildcard_match(self) -> None:
        """Wildcard pattern matching."""
        rule = RobotRule(path="/page/*.html", allowed=False)
        
        assert rule.matches("/page/test.html") is True
        assert rule.matches("/page/dir/test.html") is True

    def test_end_anchor(self) -> None:
        """End anchor ($) matching."""
        rule = RobotRule(path="/page$", allowed=False)
        
        assert rule.matches("/page") is True
        assert rule.matches("/page/") is False
        assert rule.matches("/page/more") is False

    def test_empty_disallow(self) -> None:
        """Empty Disallow should not match anything."""
        rule = RobotRule(path="", allowed=False)
        
        assert rule.matches("/anything") is False


# =============================================================================
# RobotRuleset Tests
# =============================================================================


class TestRobotRuleset:
    """Tests for RobotRuleset."""

    def test_no_rules_allows_all(self) -> None:
        """No rules should allow everything."""
        ruleset = RobotRuleset(user_agent="*")
        
        assert ruleset.is_allowed("/any/path") is True

    def test_disallow_rule(self) -> None:
        """Disallow rule should block paths."""
        ruleset = RobotRuleset(
            user_agent="*",
            rules=[RobotRule(path="/admin", allowed=False)],
        )
        
        assert ruleset.is_allowed("/admin") is False
        assert ruleset.is_allowed("/admin/page") is False
        assert ruleset.is_allowed("/public") is True

    def test_allow_overrides_disallow(self) -> None:
        """More specific Allow should override Disallow."""
        ruleset = RobotRuleset(
            user_agent="*",
            rules=[
                RobotRule(path="/admin", allowed=False),
                RobotRule(path="/admin/public", allowed=True),
            ],
        )
        
        assert ruleset.is_allowed("/admin") is False
        assert ruleset.is_allowed("/admin/public") is True
        assert ruleset.is_allowed("/admin/private") is False

    def test_longer_rule_takes_precedence(self) -> None:
        """Longer (more specific) rules take precedence."""
        ruleset = RobotRuleset(
            user_agent="*",
            rules=[
                RobotRule(path="/", allowed=True),
                RobotRule(path="/admin", allowed=False),
            ],
        )
        
        assert ruleset.is_allowed("/page") is True
        assert ruleset.is_allowed("/admin") is False


# =============================================================================
# parse_robots_txt Tests
# =============================================================================


class TestParseRobotsTxt:
    """Tests for robots.txt parsing."""

    def test_parse_simple(self) -> None:
        """Parse simple robots.txt."""
        content = """
User-agent: *
Disallow: /admin
"""
        robots = parse_robots_txt(content)
        
        assert "*" in robots.rulesets
        assert robots.is_allowed("https://example.com/page") is True
        assert robots.is_allowed("https://example.com/admin") is False

    def test_parse_multiple_user_agents(self) -> None:
        """Parse robots.txt with multiple user agents."""
        content = """
User-agent: Googlebot
Disallow: /private

User-agent: *
Disallow: /admin
"""
        robots = parse_robots_txt(content)
        
        assert "Googlebot" in robots.rulesets
        assert "*" in robots.rulesets

    def test_parse_allow_rule(self) -> None:
        """Parse Allow directive."""
        content = """
User-agent: *
Disallow: /admin
Allow: /admin/public
"""
        robots = parse_robots_txt(content)
        
        assert robots.is_allowed("https://example.com/admin") is False
        assert robots.is_allowed("https://example.com/admin/public") is True

    def test_parse_crawl_delay(self) -> None:
        """Parse Crawl-delay directive."""
        content = """
User-agent: *
Crawl-delay: 10
Disallow: /admin
"""
        robots = parse_robots_txt(content)
        
        assert robots.get_crawl_delay("*") == 10.0

    def test_parse_sitemap(self) -> None:
        """Parse Sitemap directive."""
        content = """
User-agent: *
Disallow:

Sitemap: https://example.com/sitemap.xml
"""
        robots = parse_robots_txt(content)
        
        assert "https://example.com/sitemap.xml" in robots.sitemaps

    def test_parse_with_comments(self) -> None:
        """Comments should be ignored."""
        content = """
# This is a comment
User-agent: * # inline comment
Disallow: /admin
"""
        robots = parse_robots_txt(content)
        
        assert "*" in robots.rulesets
        assert robots.is_allowed("https://example.com/admin") is False

    def test_parse_empty_disallow(self) -> None:
        """Empty Disallow means allow all."""
        content = """
User-agent: *
Disallow:
"""
        robots = parse_robots_txt(content)
        
        assert robots.is_allowed("https://example.com/anything") is True


# =============================================================================
# RobotsTxt Tests
# =============================================================================


class TestRobotsTxt:
    """Tests for RobotsTxt."""

    def test_get_ruleset_exact_match(self) -> None:
        """get_ruleset should find exact user agent match."""
        robots = RobotsTxt()
        robots.rulesets["Googlebot"] = RobotRuleset(user_agent="Googlebot")
        robots.rulesets["*"] = RobotRuleset(user_agent="*")
        
        ruleset = robots.get_ruleset("Googlebot")
        assert ruleset is not None
        assert ruleset.user_agent == "Googlebot"

    def test_get_ruleset_wildcard_fallback(self) -> None:
        """get_ruleset should fall back to wildcard."""
        robots = RobotsTxt()
        robots.rulesets["*"] = RobotRuleset(user_agent="*")
        
        ruleset = robots.get_ruleset("UnknownBot")
        assert ruleset is not None
        assert ruleset.user_agent == "*"

    def test_get_ruleset_none(self) -> None:
        """get_ruleset should return None if no match."""
        robots = RobotsTxt()
        
        ruleset = robots.get_ruleset("AnyBot")
        assert ruleset is None

    def test_is_allowed_with_query(self) -> None:
        """is_allowed should include query string in check."""
        content = """
User-agent: *
Disallow: /search?
"""
        robots = parse_robots_txt(content)
        
        assert robots.is_allowed("https://example.com/search") is True
        assert robots.is_allowed("https://example.com/search?q=test") is False


# =============================================================================
# RobotsChecker Tests
# =============================================================================


class TestRobotsChecker:
    """Tests for RobotsChecker caching."""

    def test_set_and_check(self) -> None:
        """Set robots.txt and check URLs."""
        checker = RobotsChecker()
        checker.set_robots_txt(
            "https://example.com/",
            "User-agent: *\nDisallow: /admin"
        )
        
        assert checker.is_allowed("https://example.com/page") is True
        assert checker.is_allowed("https://example.com/admin") is False

    def test_no_robots_allows_all(self) -> None:
        """No cached robots.txt should allow all."""
        checker = RobotsChecker()
        
        assert checker.is_allowed("https://example.com/admin") is True

    def test_different_hosts(self) -> None:
        """Different hosts should have separate caches."""
        checker = RobotsChecker()
        checker.set_robots_txt(
            "https://example.com/",
            "User-agent: *\nDisallow: /admin"
        )
        checker.set_robots_txt(
            "https://other.com/",
            "User-agent: *\nDisallow: /private"
        )
        
        assert checker.is_allowed("https://example.com/admin") is False
        assert checker.is_allowed("https://example.com/private") is True
        assert checker.is_allowed("https://other.com/admin") is True
        assert checker.is_allowed("https://other.com/private") is False

    def test_get_crawl_delay(self) -> None:
        """Get crawl delay from cached robots.txt."""
        checker = RobotsChecker()
        checker.set_robots_txt(
            "https://example.com/",
            "User-agent: *\nCrawl-delay: 5"
        )
        
        assert checker.get_crawl_delay("https://example.com/page") == 5.0

    def test_clear_cache(self) -> None:
        """Cache should be clearable."""
        checker = RobotsChecker()
        checker.set_robots_txt(
            "https://example.com/",
            "User-agent: *\nDisallow: /admin"
        )
        
        checker.clear_cache()
        
        # No cached robots.txt means allowed
        assert checker.is_allowed("https://example.com/admin") is True

    def test_custom_user_agent(self) -> None:
        """Custom user agent should be used."""
        checker = RobotsChecker(user_agent="MyBot")
        checker.set_robots_txt(
            "https://example.com/",
            """
User-agent: MyBot
Disallow: /mybot-only

User-agent: *
Disallow: /admin
"""
        )
        
        assert checker.is_allowed("https://example.com/mybot-only") is False
        assert checker.is_allowed("https://example.com/admin") is True  # Uses MyBot rules
