"""Browser-based rendering for JavaScript-heavy web pages.

This module provides Playwright-based rendering to extract content from
pages that require JavaScript execution. It's used as a fallback when
static HTML extraction via trafilatura yields insufficient content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from playwright.sync_api import Page

WaitUntilEvent = Literal["commit", "domcontentloaded", "load", "networkidle"]

logger = logging.getLogger(__name__)

# Minimum content length to consider extraction successful
MIN_CONTENT_LENGTH = 100

# Default timeouts in milliseconds
DEFAULT_NAVIGATION_TIMEOUT = 30000
DEFAULT_WAIT_TIMEOUT = 5000


class RenderingError(Exception):
    """Raised when browser rendering fails."""


@dataclass(slots=True, frozen=True)
class RenderedPage:
    """Result of rendering a page with a browser."""
    
    url: str
    final_url: str
    html: str
    title: str | None = None
    user_agent: str | None = None
    
    @property
    def content_length(self) -> int:
        """Return the length of the rendered HTML."""
        return len(self.html)


def is_playwright_available() -> bool:
    """Check if Playwright and browsers are available."""
    try:
        from playwright.sync_api import sync_playwright
        # Just check if the module is importable
        return True
    except ImportError:
        return False


def render_page(
    url: str,
    *,
    user_agent: str,
    headless: bool = True,
    timeout: int = DEFAULT_NAVIGATION_TIMEOUT,
    wait_until: WaitUntilEvent = "networkidle",
    wait_after_load: int = 0,
) -> RenderedPage:
    """Render a page using Playwright and return the HTML content.
    
    This function launches a headless browser, navigates to the URL,
    waits for the page to fully load (including JavaScript execution),
    and returns the rendered HTML.
    
    Args:
        url: The URL to render.
        user_agent: User agent string for the browser context.
        headless: Whether to run the browser in headless mode.
        timeout: Navigation timeout in milliseconds.
        wait_until: When to consider navigation complete.
            Options: "load", "domcontentloaded", "networkidle", "commit"
        wait_after_load: Additional milliseconds to wait after page load.
        
    Returns:
        RenderedPage with the rendered HTML content.
        
    Raises:
        RenderingError: If rendering fails.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    except ImportError as e:
        raise RenderingError(
            "Playwright is not installed. Install with: pip install playwright && playwright install chromium"
        ) from e
    
    try:
        with sync_playwright() as p:
            browser_args = []
            browser_args.extend([
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-web-security",
            ])
            
            # Launch browser
            browser = p.chromium.launch(
                headless=headless,
                args=browser_args if browser_args else None,
            )
            
            # Create context with user agent and stealth options
            context_options = {
                "user_agent": user_agent,
                "viewport": {"width": 1920, "height": 1080},
                "java_script_enabled": True,
                "bypass_csp": False,
                "ignore_https_errors": True,
                "extra_http_headers": {
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br, zstd",
                    "Cache-Control": "max-age=0",
                    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "Sec-Ch-Ua-Mobile": "?0",
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1",
                }
            }
            
            context = browser.new_context(**context_options)
            page = context.new_page()
            
            # Set timeout
            page.set_default_timeout(timeout)
            
            try:
                # Navigate to URL
                response = page.goto(url, wait_until=wait_until)
                
                if response is None:
                    raise RenderingError(f"No response received for URL: {url}")
                
                if response.status >= 400:
                    raise RenderingError(
                        f"HTTP {response.status} error for URL: {url}"
                    )
                
                # Optional additional wait for dynamic content
                if wait_after_load > 0:
                    page.wait_for_timeout(wait_after_load)
                
                # Get rendered content
                html = page.content()
                title = page.title()
                final_url = page.url
                
                return RenderedPage(
                    url=url,
                    final_url=final_url,
                    html=html,
                    title=title,
                    user_agent=user_agent,
                )
                
            except PlaywrightTimeout as e:
                raise RenderingError(f"Timeout rendering URL: {url}") from e
            finally:
                context.close()
                browser.close()
                
    except RenderingError:
        raise
    except Exception as e:
        raise RenderingError(f"Failed to render URL '{url}': {e}") from e


def render_and_extract_text(
    url: str,
    *,
    user_agent: str,
    headless: bool = True,
    timeout: int = DEFAULT_NAVIGATION_TIMEOUT,
) -> tuple[str, RenderedPage]:
    """Render a page and extract text content using trafilatura.
    
    This is a convenience function that combines rendering with
    trafilatura extraction.
    
    Args:
        url: The URL to render and extract.
        user_agent: User agent string for the browser context.
        headless: Whether to run headless.
        timeout: Navigation timeout in milliseconds.
        
    Returns:
        Tuple of (extracted_text, rendered_page).
        
    Raises:
        RenderingError: If rendering fails.
    """
    import trafilatura
    
    rendered = render_page(url, user_agent=user_agent, headless=headless, timeout=timeout)
    
    extracted = trafilatura.extract(
        rendered.html,
        url=rendered.final_url,
    )
    
    return extracted or "", rendered


def needs_rendering(html: str, extracted_text: str | None) -> bool:
    """Determine if a page likely needs JavaScript rendering.
    
    Heuristics used:
    - Extraction returned None or very little text
    - HTML contains common SPA framework indicators
    - HTML has minimal visible text but lots of script tags
    
    Args:
        html: The raw HTML content.
        extracted_text: Text extracted by trafilatura (may be None).
        
    Returns:
        True if the page likely needs JS rendering.
    """
    # If extraction failed completely
    if extracted_text is None:
        return True
    
    # If extracted text is very short
    if len(extracted_text.strip()) < MIN_CONTENT_LENGTH:
        return True
    
    # Check for SPA framework indicators in HTML
    spa_indicators = [
        'id="root"',
        'id="app"',
        'id="__next"',  # Next.js
        'id="__nuxt"',  # Nuxt.js
        "ng-app",  # Angular
        "data-reactroot",
        "data-v-",  # Vue.js
        "__NEXT_DATA__",
        "__NUXT__",
    ]
    
    html_lower = html.lower()
    for indicator in spa_indicators:
        if indicator.lower() in html_lower:
            # SPA detected, but check if we got meaningful content anyway
            if len(extracted_text.strip()) < 500:
                return True
    
    return False
