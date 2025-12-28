"""MCP server for Copilot coding agent content acquisition.

This module provides a Model Context Protocol (MCP) server that enables the
Copilot coding agent to fetch content from external sources. MCP servers run
OUTSIDE the agent firewall, providing unrestricted network access.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# MCP protocol uses JSON-RPC 2.0 over stdio
# We implement a minimal server that handles the required methods


@dataclass(slots=True)
class FetchResult:
    """Result of fetching content from a URL."""
    
    url: str
    success: bool
    content: str | None = None
    content_hash: str | None = None
    title: str | None = None
    error: str | None = None
    fetched_at: str | None = None
    final_url: str | None = None
    content_type: str | None = None
    status_code: int | None = None
    
    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


def fetch_source_content(url: str, max_content_length: int = 100000) -> FetchResult:
    """Fetch and extract content from a URL.
    
    This function runs in the MCP server context, which is OUTSIDE the
    Copilot agent firewall. It can access any URL without restrictions.
    
    Args:
        url: The URL to fetch content from.
        max_content_length: Maximum characters to return (default 100k).
        
    Returns:
        FetchResult with extracted content or error details.
    """
    try:
        import requests
        import trafilatura
    except ImportError as e:
        return FetchResult(
            url=url,
            success=False,
            error=f"Missing dependency: {e}. Install with: pip install requests trafilatura",
        )
    
    fetched_at = datetime.now(timezone.utc).isoformat()
    
    try:
        response = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "speculum-principum-evidence-acquisition/1.0"},
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as e:
        return FetchResult(
            url=url,
            success=False,
            error=f"HTTP request failed: {e}",
            fetched_at=fetched_at,
        )
    
    # Extract main content using trafilatura
    extracted = trafilatura.extract(
        response.text,
        include_comments=False,
        include_tables=True,
        output_format="markdown",
    )
    
    if not extracted:
        # Fallback: return raw text truncated
        extracted = response.text[:max_content_length]
        if len(response.text) > max_content_length:
            extracted += f"\n\n[Content truncated at {max_content_length} characters]"
    
    # Compute content hash
    content_hash = hashlib.sha256(response.content).hexdigest()
    
    # Extract title if possible
    title = None
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(response.text, "html.parser")
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
    except Exception:
        pass  # Title extraction is optional
    
    # Truncate content if needed
    if len(extracted) > max_content_length:
        extracted = extracted[:max_content_length]
        extracted += f"\n\n[Content truncated at {max_content_length} characters]"
    
    return FetchResult(
        url=url,
        success=True,
        content=extracted,
        content_hash=content_hash,
        title=title,
        fetched_at=fetched_at,
        final_url=response.url,
        content_type=response.headers.get("Content-Type"),
        status_code=response.status_code,
    )


def check_source_headers(url: str) -> dict[str, Any]:
    """Check HTTP headers for a URL without fetching full content.
    
    Useful for change detection (ETag, Last-Modified comparison).
    
    Args:
        url: The URL to check.
        
    Returns:
        Dict with header information or error details.
    """
    try:
        import requests
    except ImportError:
        return {"success": False, "error": "requests library not installed"}
    
    try:
        response = requests.head(
            url,
            timeout=10,
            headers={"User-Agent": "speculum-principum-monitor/1.0"},
            allow_redirects=True,
        )
        
        return {
            "success": True,
            "url": url,
            "final_url": response.url,
            "status_code": response.status_code,
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    except requests.RequestException as e:
        return {
            "success": False,
            "url": url,
            "error": str(e),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


# MCP Server Implementation
# Follows the Model Context Protocol specification

TOOLS = [
    {
        "name": "fetch_source_content",
        "description": (
            "Fetch and extract main content from a URL. Returns markdown-formatted "
            "content, content hash, and metadata. Use this to acquire evidence from "
            "registered sources. This tool has unrestricted network access."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch content from.",
                },
                "max_content_length": {
                    "type": "integer",
                    "description": "Maximum characters to return (default 100000).",
                    "default": 100000,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "check_source_headers",
        "description": (
            "Check HTTP headers for a URL without fetching full content. "
            "Returns ETag, Last-Modified, and other headers for change detection. "
            "This is a lightweight operation for monitoring source updates."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to check headers for.",
                },
            },
            "required": ["url"],
        },
    },
]


def handle_request(request: dict[str, Any]) -> dict[str, Any]:
    """Handle an MCP JSON-RPC request."""
    method = request.get("method", "")
    request_id = request.get("id")
    params = request.get("params", {})
    
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": "evidence-acquisition",
                    "version": "1.0.0",
                },
            },
        }
    
    elif method == "notifications/initialized":
        # Notification, no response needed
        return None
    
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": TOOLS,
            },
        }
    
    elif method == "tools/call":
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        
        if tool_name == "fetch_source_content":
            result = fetch_source_content(
                url=arguments["url"],
                max_content_length=arguments.get("max_content_length", 100000),
            )
            content = json.dumps(result.to_dict(), indent=2)
        elif tool_name == "check_source_headers":
            result = check_source_headers(url=arguments["url"])
            content = json.dumps(result, indent=2)
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unknown tool: {tool_name}",
                },
            }
        
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": content,
                    }
                ],
            },
        }
    
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": f"Method not found: {method}",
            },
        }


def run_server() -> None:
    """Run the MCP server, reading from stdin and writing to stdout."""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            
            request = json.loads(line)
            response = handle_request(request)
            
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
                
        except json.JSONDecodeError:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Parse error",
                },
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    run_server()
