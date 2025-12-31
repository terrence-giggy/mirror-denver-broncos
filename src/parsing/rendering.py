"""Browser-based rendering for JavaScript-heavy web pages.

This module provides Playwright-based rendering to extract content from
pages that require JavaScript execution. It's used as a fallback when
static HTML extraction via trafilatura yields insufficient content.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import Page

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
    headless: bool = True,
    timeout: int = DEFAULT_NAVIGATION_TIMEOUT,
    wait_until: str = "networkidle",
    wait_after_load: int = 0,
    user_agent: str | None = None,
) -> RenderedPage:
    """Render a page using Playwright and return the HTML content.
    
    This function launches a headless browser, navigates to the URL,
    waits for the page to fully load (including JavaScript execution),
    and returns the rendered HTML.
    
    Args:
        url: The URL to render.
        headless: Whether to run the browser in headless mode.
        timeout: Navigation timeout in milliseconds.
        wait_until: When to consider navigation complete.
            Options: "load", "domcontentloaded", "networkidle", "commit"
        wait_after_load: Additional milliseconds to wait after page load.
        user_agent: Custom user agent string.
        
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
            # Configure browser launch arguments
            browser_args = []
            # Anti-detection: disable automation flags and set realistic args
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
            
            # Create context with optional user agent and stealth options
            context_options = {}
            
            # Set realistic user agent if not provided
            if not user_agent:
                context_options["user_agent"] = (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/134.0.0.0 Safari/537.36"
                )
            else:
                context_options["user_agent"] = user_agent
            
            # Set realistic viewport
            context_options["viewport"] = {"width": 1920, "height": 1080}
            
            # Additional anti-detection context options
            context_options["java_script_enabled"] = True
            context_options["bypass_csp"] = False
            context_options["ignore_https_errors"] = True
            
            context = browser.new_context(**context_options)
            page = context.new_page()
            
            # Enhanced anti-detection script
            stealth_js = """
            // Remove webdriver property
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            
            // Add chrome object for better Chrome impersonation
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            
            // Override plugins to appear non-headless
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    return [
                        {name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer'},
                        {name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                        {name: 'Native Client', description: '', filename: 'internal-nacl-plugin'}
                    ];
                }
            });
            
            // Set realistic language
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            
            // Override permissions
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = (parameters) => (
                parameters.name === 'notifications' ?
                    Promise.resolve({ state: Notification.permission }) :
                    originalQuery(parameters)
            );
            
            // Set platform
            Object.defineProperty(navigator, 'platform', {
                get: () => 'Win32'
            });
            
            // Set hardware concurrency
            Object.defineProperty(navigator, 'hardwareConcurrency', {
                get: () => 8
            });
            """
            page.add_init_script(stealth_js)
            
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
    headless: bool = True,
    timeout: int = DEFAULT_NAVIGATION_TIMEOUT,
) -> tuple[str, RenderedPage]:
    """Render a page and extract text content using trafilatura.
    
    This is a convenience function that combines rendering with
    trafilatura extraction.
    
    Args:
        url: The URL to render and extract.
        headless: Whether to run headless.
        timeout: Navigation timeout in milliseconds.
        
    Returns:
        Tuple of (extracted_text, rendered_page).
        
    Raises:
        RenderingError: If rendering fails.
    """
    import trafilatura
    
    rendered = render_page(url, headless=headless, timeout=timeout)
    
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
