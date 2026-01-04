"""Tests for the GitHubModelsClient GitHub Models API integration."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, call

import pytest

from src.integrations.github.models import (
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    GitHubModelsClient,
    GitHubModelsError,
    FunctionCall,
    RateLimitError,
    ToolCall,
    Usage,
)


def test_copilot_client_requires_api_key():
    """GitHubModelsClient raises error if no API key is provided."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(GitHubModelsError, match="GitHub token required"):
            GitHubModelsClient()


def test_copilot_client_uses_env_token():
    """GitHubModelsClient reads GITHUB_TOKEN from environment."""
    with patch.dict("os.environ", {"GITHUB_TOKEN": "test_token", "GH_TOKEN": ""}, clear=True):
        client = GitHubModelsClient()
        assert client.api_key == "test_token"


def test_copilot_client_explicit_token():
    """GitHubModelsClient accepts explicit API key parameter."""
    client = GitHubModelsClient(api_key="explicit_token")
    assert client.api_key == "explicit_token"


def test_copilot_client_defaults():
    """GitHubModelsClient sets appropriate defaults."""
    client = GitHubModelsClient(api_key="test")
    assert client.model == "gpt-4o"
    assert client.max_tokens == 4000
    assert client.temperature == 0.7
    assert client.timeout == 60


def test_copilot_client_custom_values():
    """GitHubModelsClient accepts custom configuration."""
    client = GitHubModelsClient(
        api_key="test",
        model="gpt-4o",
        max_tokens=8000,
        temperature=0.5,
        timeout=120,
    )
    assert client.model == "gpt-4o"
    assert client.max_tokens == 8000
    assert client.temperature == 0.5
    assert client.timeout == 120


def test_chat_completion_simple_message():
    """GitHubModelsClient can handle a simple chat completion."""
    mock_response = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "Hello! How can I help you?",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 8,
            "total_tokens": 18,
        },
    }
    
    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = mock_response
        mock_post.return_value.raise_for_status = MagicMock()
        
        client = GitHubModelsClient(api_key="test")
        response = client.chat_completion([
            {"role": "user", "content": "Hello"}
        ])
        
        assert response.id == "chatcmpl-123"
        assert response.model == "gpt-4o-mini"
        assert len(response.choices) == 1
        assert response.choices[0].message.content == "Hello! How can I help you?"
        assert response.usage is not None
        assert response.usage.total_tokens == 18


def test_chat_completion_with_tool_call():
    """GitHubModelsClient parses tool/function calls correctly."""
    mock_response = {
        "id": "chatcmpl-456",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_abc123",
                            "type": "function",
                            "function": {
                                "name": "get_issue_details",
                                "arguments": '{"issue_number": 42}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
    }
    
    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = mock_response
        mock_post.return_value.raise_for_status = MagicMock()
        
        client = GitHubModelsClient(api_key="test")
        response = client.chat_completion(
            messages=[{"role": "user", "content": "Get issue 42"}],
            tools=[{
                "type": "function",
                "function": {
                    "name": "get_issue_details",
                    "description": "Fetch issue details",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "issue_number": {"type": "integer"},
                        },
                    },
                },
            }],
        )
        
        assert len(response.choices) == 1
        message = response.choices[0].message
        assert message.tool_calls is not None
        assert len(message.tool_calls) == 1
        
        tool_call = message.tool_calls[0]
        assert tool_call.id == "call_abc123"
        assert tool_call.type == "function"
        assert tool_call.function.name == "get_issue_details"
        assert tool_call.function.arguments == '{"issue_number": 42}'


def test_chat_completion_sends_correct_payload():
    """GitHubModelsClient sends properly formatted request."""
    with patch("requests.post") as mock_post:
        mock_post.return_value.json.return_value = {
            "id": "test",
            "model": "gpt-4o-mini",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        }
        mock_post.return_value.raise_for_status = MagicMock()
        
        client = GitHubModelsClient(api_key="test_token", model="gpt-4o")
        
        tools = [{
            "type": "function",
            "function": {"name": "test_tool", "description": "Test"},
        }]
        
        client.chat_completion(
            messages=[{"role": "user", "content": "test"}],
            tools=tools,
            max_tokens=2000,
            temperature=0.9,
        )
        
        # Verify the request was made correctly
        assert mock_post.called
        call_kwargs = mock_post.call_args[1]
        
        assert call_kwargs["headers"]["Authorization"] == "Bearer test_token"
        assert call_kwargs["headers"]["Content-Type"] == "application/json"
        
        payload = call_kwargs["json"]
        assert payload["model"] == "gpt-4o"
        assert payload["messages"] == [{"role": "user", "content": "test"}]
        assert payload["max_tokens"] == 2000
        assert payload["temperature"] == 0.9
        assert payload["tools"] == tools
        assert payload["tool_choice"] == "auto"


def test_chat_completion_handles_http_error():
    """GitHubModelsClient raises error on HTTP failure."""
    import requests
    
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.RequestException("HTTP 401")
        mock_post.return_value = mock_response
        
        client = GitHubModelsClient(api_key="test")
        
        with pytest.raises(GitHubModelsError, match="GitHub Models API request failed"):
            client.chat_completion([{"role": "user", "content": "test"}])


def test_chat_completion_handles_json_decode_error():
    """GitHubModelsClient raises error on invalid JSON response."""
    with patch("requests.post") as mock_post:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError("bad", "", 0)
        mock_post.return_value = mock_response
        
        client = GitHubModelsClient(api_key="test")
        
        with pytest.raises(GitHubModelsError, match="Invalid JSON response"):
            client.chat_completion([{"role": "user", "content": "test"}])


def test_parse_response_handles_missing_fields():
    """GitHubModelsClient handles API responses with missing optional fields."""
    client = GitHubModelsClient(api_key="test")
    
    # Minimal response
    minimal_response = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                }
            }
        ]
    }
    
    result = client._parse_response(minimal_response)
    
    assert result.id == ""
    assert result.model == ""
    assert len(result.choices) == 1
    assert result.choices[0].message.content == ""
    assert result.choices[0].message.tool_calls is None
    assert result.usage is None


# Rate limit retry tests


def test_copilot_client_retry_defaults():
    """GitHubModelsClient has sensible retry defaults."""
    client = GitHubModelsClient(api_key="test")
    assert client.max_retries == 5
    assert client.initial_backoff == 2.0
    assert client.max_backoff == 120.0


def test_copilot_client_custom_retry_config():
    """GitHubModelsClient accepts custom retry configuration."""
    client = GitHubModelsClient(
        api_key="test",
        max_retries=3,
        initial_backoff=1.0,
        max_backoff=60.0,
    )
    assert client.max_retries == 3
    assert client.initial_backoff == 1.0
    assert client.max_backoff == 60.0


def test_copilot_client_zero_retries():
    """GitHubModelsClient can be configured with zero retries."""
    client = GitHubModelsClient(api_key="test", max_retries=0)
    assert client.max_retries == 0


def test_rate_limit_retry_success_after_one_retry():
    """GitHubModelsClient retries on 429 and succeeds on second attempt."""
    import requests
    
    mock_response_success = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Success"}}],
    }
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {"Retry-After": "1"}
    rate_limit_response.json.return_value = {"message": "Rate limit exceeded"}
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    success_response = MagicMock()
    success_response.status_code = 200
    success_response.json.return_value = mock_response_success
    success_response.raise_for_status = MagicMock()
    
    with patch("requests.post") as mock_post, patch("time.sleep") as mock_sleep:
        mock_post.side_effect = [rate_limit_response, success_response]
        
        client = GitHubModelsClient(api_key="test", max_retries=3, initial_backoff=1.0)
        result = client.chat_completion([{"role": "user", "content": "test"}])
        
        assert result.choices[0].message.content == "Success"
        assert mock_post.call_count == 2
        mock_sleep.assert_called_once_with(1.0)  # Retry-After header value


def test_rate_limit_exhausted_retries():
    """GitHubModelsClient raises RateLimitError after exhausting retries."""
    import requests
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {}
    rate_limit_response.json.return_value = {"message": "Rate limit exceeded"}
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    with patch("requests.post") as mock_post, patch("time.sleep"):
        mock_post.return_value = rate_limit_response
        
        client = GitHubModelsClient(api_key="test", max_retries=2, initial_backoff=0.1)
        
        with pytest.raises(RateLimitError, match="Rate limit exceeded after 3 attempts"):
            client.chat_completion([{"role": "user", "content": "test"}])
        
        # Should have tried 3 times (initial + 2 retries)
        assert mock_post.call_count == 3


def test_rate_limit_exponential_backoff():
    """GitHubModelsClient uses exponential backoff between retries."""
    import requests
    
    mock_response_success = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Success"}}],
    }
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {}  # No Retry-After header
    rate_limit_response.json.return_value = {"message": "Rate limit exceeded"}
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    success_response = MagicMock()
    success_response.json.return_value = mock_response_success
    success_response.raise_for_status = MagicMock()
    
    with patch("requests.post") as mock_post, patch("time.sleep") as mock_sleep:
        # Fail twice, succeed on third
        mock_post.side_effect = [rate_limit_response, rate_limit_response, success_response]
        
        client = GitHubModelsClient(api_key="test", max_retries=3, initial_backoff=2.0)
        result = client.chat_completion([{"role": "user", "content": "test"}])
        
        assert result.choices[0].message.content == "Success"
        # First backoff: 2.0, second backoff: 4.0 (exponential)
        assert mock_sleep.call_count == 2
        calls = mock_sleep.call_args_list
        assert calls[0] == call(2.0)
        assert calls[1] == call(4.0)


def test_rate_limit_respects_retry_after_header():
    """GitHubModelsClient uses Retry-After header when present."""
    import requests
    
    mock_response_success = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Success"}}],
    }
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {"Retry-After": "45"}  # Server says wait 45 seconds
    rate_limit_response.json.return_value = {"message": "Rate limit"}
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    success_response = MagicMock()
    success_response.json.return_value = mock_response_success
    success_response.raise_for_status = MagicMock()
    
    with patch("requests.post") as mock_post, patch("time.sleep") as mock_sleep:
        mock_post.side_effect = [rate_limit_response, success_response]
        
        client = GitHubModelsClient(api_key="test", max_retries=2, initial_backoff=2.0)
        result = client.chat_completion([{"role": "user", "content": "test"}])
        
        assert result.choices[0].message.content == "Success"
        mock_sleep.assert_called_once_with(45.0)


def test_rate_limit_caps_wait_at_max_backoff():
    """GitHubModelsClient caps wait time at max_backoff."""
    import requests
    
    mock_response_success = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Success"}}],
    }
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {"Retry-After": "300"}  # Server says wait 5 minutes
    rate_limit_response.json.return_value = {"message": "Rate limit"}
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    success_response = MagicMock()
    success_response.json.return_value = mock_response_success
    success_response.raise_for_status = MagicMock()
    
    with patch("requests.post") as mock_post, patch("time.sleep") as mock_sleep:
        mock_post.side_effect = [rate_limit_response, success_response]
        
        # max_backoff of 60 seconds
        client = GitHubModelsClient(api_key="test", max_retries=2, max_backoff=60.0)
        result = client.chat_completion([{"role": "user", "content": "test"}])
        
        assert result.choices[0].message.content == "Success"
        # Should cap at 60 seconds, not wait 300
        mock_sleep.assert_called_once_with(60.0)


def test_rate_limit_parses_wait_from_message():
    """GitHubModelsClient extracts wait time from error message body."""
    import requests
    
    mock_response_success = {
        "id": "chatcmpl-123",
        "model": "gpt-4o-mini",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": "Success"}}],
    }
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {}  # No Retry-After header
    rate_limit_response.json.return_value = {
        "message": "Rate limit exceeded. Please wait 43 seconds before retrying."
    }
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    success_response = MagicMock()
    success_response.json.return_value = mock_response_success
    success_response.raise_for_status = MagicMock()
    
    with patch("requests.post") as mock_post, patch("time.sleep") as mock_sleep:
        mock_post.side_effect = [rate_limit_response, success_response]
        
        client = GitHubModelsClient(api_key="test", max_retries=2)
        result = client.chat_completion([{"role": "user", "content": "test"}])
        
        assert result.choices[0].message.content == "Success"
        mock_sleep.assert_called_once_with(43.0)


def test_non_rate_limit_error_not_retried():
    """GitHubModelsClient does not retry non-429 errors."""
    import requests
    
    error_response = MagicMock()
    error_response.status_code = 500
    error_response.headers = {}
    error_response.json.return_value = {"error": "Internal server error"}
    error_response.raise_for_status.side_effect = requests.HTTPError(
        "500 Internal Server Error", response=error_response
    )
    
    with patch("requests.post") as mock_post, patch("time.sleep") as mock_sleep:
        mock_post.return_value = error_response
        
        client = GitHubModelsClient(api_key="test", max_retries=3)
        
        with pytest.raises(GitHubModelsError, match="GitHub Models API request failed"):
            client.chat_completion([{"role": "user", "content": "test"}])
        
        # Should NOT retry - only one attempt
        assert mock_post.call_count == 1
        mock_sleep.assert_not_called()


def test_rate_limit_error_has_retry_after():
    """RateLimitError includes retry_after value when available."""
    import requests
    
    rate_limit_response = MagicMock()
    rate_limit_response.status_code = 429
    rate_limit_response.headers = {"Retry-After": "30"}
    rate_limit_response.json.return_value = {"message": "Rate limit"}
    rate_limit_response.raise_for_status.side_effect = requests.HTTPError(
        "429 Too Many Requests", response=rate_limit_response
    )
    
    with patch("requests.post") as mock_post, patch("time.sleep"):
        mock_post.return_value = rate_limit_response
        
        client = GitHubModelsClient(api_key="test", max_retries=0)
        
        with pytest.raises(RateLimitError) as exc_info:
            client.chat_completion([{"role": "user", "content": "test"}])
        
        assert exc_info.value.retry_after == 30.0
