"""Robots.txt parsing and compliance checking.

This module provides functionality to parse robots.txt files and check
whether URLs are allowed to be crawled according to the rules.

Features:
- Parse robots.txt files
- Check if a URL is allowed for a given user agent
- Handle wildcards and pattern matching
- Respect Crawl-delay directives
- Cache parsed robots.txt files

Reference: https://www.robotstxt.org/robotstxt.html
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List
from urllib.parse import urlparse


@dataclass
class RobotRule:
    """A single rule from robots.txt.
    
    Attributes:
        path: The path pattern (may contain * and $)
        allowed: True for Allow, False for Disallow
    """
    path: str
    allowed: bool
    
    def matches(self, url_path: str) -> bool:
        """Check if this rule matches the given URL path.
        
        Args:
            url_path: The path portion of the URL to check
            
        Returns:
            True if the rule pattern matches the path
        """
        pattern = self.path
        
        # Empty pattern matches nothing for Disallow, everything for Allow
        if not pattern:
            return self.allowed
        
        # Convert robots.txt pattern to regex
        # * matches any sequence of characters
        # $ at end means end of path
        regex_pattern = ""
        i = 0
        while i < len(pattern):
            char = pattern[i]
            if char == "*":
                regex_pattern += ".*"
            elif char == "$" and i == len(pattern) - 1:
                regex_pattern += "$"
            else:
                regex_pattern += re.escape(char)
            i += 1
        
        # If pattern doesn't end with $, allow prefix matching
        if not pattern.endswith("$"):
            regex_pattern = f"^{regex_pattern}"
        else:
            regex_pattern = f"^{regex_pattern}"
        
        try:
            return bool(re.match(regex_pattern, url_path))
        except re.error:
            # If regex is invalid, fall back to prefix matching
            return url_path.startswith(pattern.rstrip("$").replace("*", ""))


@dataclass
class RobotRuleset:
    """Rules for a specific user agent.
    
    Attributes:
        user_agent: The user agent pattern this ruleset applies to
        rules: List of rules in order of appearance
        crawl_delay: Crawl-delay value in seconds (if specified)
    """
    user_agent: str
    rules: List[RobotRule] = field(default_factory=list)
    crawl_delay: float | None = None
    
    def is_allowed(self, url_path: str) -> bool:
        """Check if a URL path is allowed by this ruleset.
        
        The longest matching rule takes precedence.
        If rules have equal length, Allow takes precedence.
        
        Args:
            url_path: The path portion of the URL to check
            
        Returns:
            True if the URL is allowed, False if disallowed
        """
        if not self.rules:
            return True
        
        # Find all matching rules
        matching_rules: List[tuple[int, RobotRule]] = []
        for rule in self.rules:
            if rule.matches(url_path):
                # Use pattern length as priority (longer = more specific)
                matching_rules.append((len(rule.path), rule))
        
        if not matching_rules:
            # No matching rules means allowed
            return True
        
        # Sort by length (descending), then by allowed (Allow > Disallow)
        matching_rules.sort(key=lambda x: (x[0], x[1].allowed), reverse=True)
        
        # Return the result of the highest priority rule
        return matching_rules[0][1].allowed


@dataclass
class RobotsTxt:
    """Parsed robots.txt file.
    
    Attributes:
        rulesets: Dictionary mapping user agent patterns to rulesets
        sitemaps: List of sitemap URLs found in the file
    """
    rulesets: Dict[str, RobotRuleset] = field(default_factory=dict)
    sitemaps: List[str] = field(default_factory=list)
    
    def get_ruleset(self, user_agent: str) -> RobotRuleset | None:
        """Get the ruleset for a specific user agent.
        
        Checks for exact match first, then wildcard (*), then returns None.
        
        Args:
            user_agent: The user agent to get rules for
            
        Returns:
            The matching RobotRuleset, or None if no rules apply
        """
        user_agent_lower = user_agent.lower()
        
        # Check for exact match (case-insensitive)
        for ua, ruleset in self.rulesets.items():
            if ua.lower() == user_agent_lower:
                return ruleset
        
        # Check for partial match (user agent contains the pattern)
        for ua, ruleset in self.rulesets.items():
            if ua != "*" and ua.lower() in user_agent_lower:
                return ruleset
        
        # Fall back to wildcard
        if "*" in self.rulesets:
            return self.rulesets["*"]
        
        return None
    
    def is_allowed(self, url: str, user_agent: str = "*") -> bool:
        """Check if a URL is allowed for crawling.
        
        Args:
            url: The full URL to check
            user_agent: The user agent string (default: *)
            
        Returns:
            True if the URL is allowed, False if disallowed
        """
        parsed = urlparse(url)
        path = parsed.path or "/"
        if parsed.query:
            path = f"{path}?{parsed.query}"
        
        ruleset = self.get_ruleset(user_agent)
        if ruleset is None:
            # No rules for this user agent means allowed
            return True
        
        return ruleset.is_allowed(path)
    
    def get_crawl_delay(self, user_agent: str = "*") -> float | None:
        """Get the crawl delay for a user agent.
        
        Args:
            user_agent: The user agent string
            
        Returns:
            Crawl delay in seconds, or None if not specified
        """
        ruleset = self.get_ruleset(user_agent)
        if ruleset:
            return ruleset.crawl_delay
        return None


def parse_robots_txt(content: str) -> RobotsTxt:
    """Parse a robots.txt file content.
    
    Args:
        content: The text content of the robots.txt file
        
    Returns:
        Parsed RobotsTxt object
    """
    robots = RobotsTxt()
    current_user_agents: List[str] = []
    current_rules: List[RobotRule] = []
    current_crawl_delay: float | None = None
    
    def finalize_group() -> None:
        """Save the current group of rules."""
        nonlocal current_user_agents, current_rules, current_crawl_delay
        
        for ua in current_user_agents:
            if ua not in robots.rulesets:
                robots.rulesets[ua] = RobotRuleset(
                    user_agent=ua,
                    rules=current_rules.copy(),
                    crawl_delay=current_crawl_delay,
                )
            else:
                # Append rules to existing ruleset
                robots.rulesets[ua].rules.extend(current_rules)
                if current_crawl_delay is not None:
                    robots.rulesets[ua].crawl_delay = current_crawl_delay
        
        current_user_agents = []
        current_rules = []
        current_crawl_delay = None
    
    lines = content.split("\n")
    
    for line in lines:
        # Remove comments
        comment_pos = line.find("#")
        if comment_pos != -1:
            line = line[:comment_pos]
        
        line = line.strip()
        if not line:
            continue
        
        # Parse directive
        if ":" not in line:
            continue
        
        directive, _, value = line.partition(":")
        directive = directive.strip().lower()
        value = value.strip()
        
        if directive == "user-agent":
            # If we have rules, finalize the previous group
            if current_rules:
                finalize_group()
            current_user_agents.append(value)
        
        elif directive == "disallow":
            if current_user_agents:
                current_rules.append(RobotRule(path=value, allowed=False))
        
        elif directive == "allow":
            if current_user_agents:
                current_rules.append(RobotRule(path=value, allowed=True))
        
        elif directive == "crawl-delay":
            try:
                current_crawl_delay = float(value)
            except ValueError:
                pass
        
        elif directive == "sitemap":
            if value:
                robots.sitemaps.append(value)
    
    # Finalize last group
    if current_user_agents:
        finalize_group()
    
    return robots


class RobotsChecker:
    """Caching robots.txt checker for crawling.
    
    This class caches parsed robots.txt files and provides a simple
    interface for checking if URLs are allowed.
    
    Usage:
        checker = RobotsChecker()
        checker.set_robots_txt("https://example.com/", robots_txt_content)
        if checker.is_allowed("https://example.com/page"):
            # crawl the page
    """
    
    def __init__(self, user_agent: str = "*"):
        """Initialize the robots checker.
        
        Args:
            user_agent: The user agent to use for checking (default: *)
        """
        self.user_agent = user_agent
        self._cache: Dict[str, RobotsTxt] = {}
    
    def _get_robots_key(self, url: str) -> str:
        """Get the cache key for a URL (scheme + host)."""
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    
    def set_robots_txt(self, base_url: str, content: str) -> RobotsTxt:
        """Set the robots.txt content for a site.
        
        Args:
            base_url: Any URL on the site
            content: The robots.txt file content
            
        Returns:
            The parsed RobotsTxt object
        """
        key = self._get_robots_key(base_url)
        robots = parse_robots_txt(content)
        self._cache[key] = robots
        return robots
    
    def get_robots_txt(self, url: str) -> RobotsTxt | None:
        """Get the cached robots.txt for a URL.
        
        Args:
            url: Any URL on the site
            
        Returns:
            The cached RobotsTxt, or None if not cached
        """
        key = self._get_robots_key(url)
        return self._cache.get(key)
    
    def is_allowed(self, url: str) -> bool:
        """Check if a URL is allowed by robots.txt.
        
        Args:
            url: The URL to check
            
        Returns:
            True if allowed (or no robots.txt cached), False if disallowed
        """
        robots = self.get_robots_txt(url)
        if robots is None:
            # No robots.txt cached, assume allowed
            return True
        
        return robots.is_allowed(url, self.user_agent)
    
    def get_crawl_delay(self, url: str) -> float | None:
        """Get the crawl delay for a URL's site.
        
        Args:
            url: Any URL on the site
            
        Returns:
            Crawl delay in seconds, or None if not specified
        """
        robots = self.get_robots_txt(url)
        if robots is None:
            return None
        
        return robots.get_crawl_delay(self.user_agent)
    
    def clear_cache(self) -> None:
        """Clear the robots.txt cache."""
        self._cache.clear()
