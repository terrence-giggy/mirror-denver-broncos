"""Tests for the evidence acquisition MCP server."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


class TestFetchSourceContent:
    """Tests for the fetch_source_content function."""

    def test_successful_fetch(self) -> None:
        """Test successful content fetch from a URL."""
        from src.integrations.copilot.mcp_server import fetch_source_content

        mock_response = MagicMock()
        mock_response.text = "<html><head><title>Test Page</title></head><body><p>Test content</p></body></html>"
        mock_response.content = b"<html><head><title>Test Page</title></head><body><p>Test content</p></body></html>"
        mock_response.url = "https://example.com/page"
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}

        with patch("requests.get", return_value=mock_response):
            result = fetch_source_content("https://example.com/page")

        assert result.success is True
        assert result.url == "https://example.com/page"
        assert result.content_hash is not None
        assert result.status_code == 200
        assert result.error is None

    def test_network_error(self) -> None:
        """Test handling of network errors."""
        from src.integrations.copilot.mcp_server import fetch_source_content
        import requests

        with patch("requests.get", side_effect=requests.RequestException("Connection refused")):
            result = fetch_source_content("https://unreachable.example.com")

        assert result.success is False
        assert "Connection refused" in result.error
        assert result.content is None

    def test_content_truncation(self) -> None:
        """Test that content is truncated when exceeding max length."""
        from src.integrations.copilot.mcp_server import fetch_source_content

        long_content = "x" * 200
        mock_response = MagicMock()
        mock_response.text = f"<html><body>{long_content}</body></html>"
        mock_response.content = mock_response.text.encode()
        mock_response.url = "https://example.com"
        mock_response.status_code = 200
        mock_response.headers = {}

        with patch("requests.get", return_value=mock_response):
            with patch("trafilatura.extract", return_value=long_content):
                result = fetch_source_content("https://example.com", max_content_length=100)

        assert result.success is True
        assert len(result.content) <= 150  # 100 + truncation message buffer


class TestCheckSourceHeaders:
    """Tests for the check_source_headers function."""

    def test_successful_head_request(self) -> None:
        """Test successful HEAD request for headers."""
        from src.integrations.copilot.mcp_server import check_source_headers

        mock_response = MagicMock()
        mock_response.url = "https://example.com/page"
        mock_response.status_code = 200
        mock_response.headers = {
            "ETag": '"abc123"',
            "Last-Modified": "Wed, 25 Dec 2025 12:00:00 GMT",
            "Content-Type": "text/html",
            "Content-Length": "12345",
        }

        with patch("requests.head", return_value=mock_response):
            result = check_source_headers("https://example.com/page")

        assert result["success"] is True
        assert result["etag"] == '"abc123"'
        assert result["last_modified"] == "Wed, 25 Dec 2025 12:00:00 GMT"
        assert result["status_code"] == 200

    def test_head_request_error(self) -> None:
        """Test handling of HEAD request errors."""
        from src.integrations.copilot.mcp_server import check_source_headers
        import requests

        with patch("requests.head", side_effect=requests.Timeout("Request timed out")):
            result = check_source_headers("https://slow.example.com")

        assert result["success"] is False
        assert "Request timed out" in result["error"]


class TestMCPProtocol:
    """Tests for MCP JSON-RPC protocol handling."""

    def test_initialize_request(self) -> None:
        """Test MCP initialize method response."""
        from src.integrations.copilot.mcp_server import handle_request

        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        }

        response = handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 1
        assert "result" in response
        assert response["result"]["protocolVersion"] == "2024-11-05"
        assert "tools" in response["result"]["capabilities"]

    def test_tools_list_request(self) -> None:
        """Test MCP tools/list method response."""
        from src.integrations.copilot.mcp_server import handle_request

        request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }

        response = handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 2
        assert "result" in response
        tools = response["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "fetch_source_content" in tool_names
        assert "check_source_headers" in tool_names

    def test_tools_call_fetch(self) -> None:
        """Test MCP tools/call for fetch_source_content."""
        from src.integrations.copilot.mcp_server import handle_request

        mock_response = MagicMock()
        mock_response.text = "<html><body>Test</body></html>"
        mock_response.content = b"<html><body>Test</body></html>"
        mock_response.url = "https://example.com"
        mock_response.status_code = 200
        mock_response.headers = {"Content-Type": "text/html"}

        request = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "fetch_source_content",
                "arguments": {"url": "https://example.com"},
            },
        }

        with patch("requests.get", return_value=mock_response):
            response = handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 3
        assert "result" in response
        assert response["result"]["content"][0]["type"] == "text"
        
        # Parse the content to verify structure
        content = json.loads(response["result"]["content"][0]["text"])
        assert content["success"] is True

    def test_tools_call_unknown_tool(self) -> None:
        """Test MCP tools/call with unknown tool name."""
        from src.integrations.copilot.mcp_server import handle_request

        request = {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "nonexistent_tool",
                "arguments": {},
            },
        }

        response = handle_request(request)

        assert response["jsonrpc"] == "2.0"
        assert response["id"] == 4
        assert "error" in response
        assert response["error"]["code"] == -32601

    def test_unknown_method(self) -> None:
        """Test MCP with unknown method."""
        from src.integrations.copilot.mcp_server import handle_request

        request = {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "unknown/method",
            "params": {},
        }

        response = handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == -32601


class TestFetchResultDataclass:
    """Tests for the FetchResult dataclass."""

    def test_to_dict_excludes_none(self) -> None:
        """Test that to_dict excludes None values."""
        from src.integrations.copilot.mcp_server import FetchResult

        result = FetchResult(
            url="https://example.com",
            success=True,
            content="Test content",
            content_hash="abc123",
            title=None,  # Should be excluded
            error=None,  # Should be excluded
        )

        d = result.to_dict()

        assert "url" in d
        assert "success" in d
        assert "content" in d
        assert "title" not in d
        assert "error" not in d
