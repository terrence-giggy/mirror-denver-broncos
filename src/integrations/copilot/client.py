"""GitHub Models API client for LLM-based agent reasoning."""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatMessage:
    """A message in a conversation."""

    role: str  # "system", "user", "assistant", or "tool"
    content: str
    tool_calls: tuple[ToolCall, ...] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class ToolCall:
    """A function call made by the LLM."""

    id: str
    type: str  # "function"
    function: FunctionCall


@dataclass(frozen=True)
class FunctionCall:
    """Details of a function call."""

    name: str
    arguments: str  # JSON string


@dataclass(frozen=True)
class ChatCompletionResponse:
    """Response from a chat completion API call."""

    id: str
    model: str
    choices: tuple[Choice, ...]
    usage: Usage | None = None


@dataclass(frozen=True)
class Choice:
    """A single response choice."""

    index: int
    message: ChatMessage
    finish_reason: str | None = None


@dataclass(frozen=True)
class Usage:
    """Token usage information."""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CopilotClientError(Exception):
    """Error communicating with GitHub Models API."""


class RateLimitError(CopilotClientError):
    """Rate limit exceeded - request can be retried after delay."""
    
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class CopilotClient:
    """Client for GitHub Models API (OpenAI-compatible endpoint).
    
    This client interfaces with GitHub's Models API for LLM chat completions
    with function calling support, used by the agent planner for reasoning.
    """

    DEFAULT_API_URL = "https://models.inference.ai.azure.com"
    DEFAULT_MODEL = "gpt-4o"  # GitHub Copilot tuned GPT-4o variant with 128k context
    DEFAULT_MAX_OUTPUT_TOKENS = 4000  # Max completion tokens
    DEFAULT_TEMPERATURE = 0.7
    
    # Retry defaults for rate limiting
    DEFAULT_MAX_RETRIES = 5
    DEFAULT_INITIAL_BACKOFF = 2.0  # seconds
    DEFAULT_MAX_BACKOFF = 120.0  # 2 minutes max wait
    DEFAULT_BACKOFF_MULTIPLIER = 2.0

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_url: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        timeout: int = 60,
        max_retries: int | None = None,
        initial_backoff: float | None = None,
        max_backoff: float | None = None,
    ):
        """Initialize GitHub Models API client.
        
        Args:
            api_key: GitHub token with Models API access. Defaults to GITHUB_TOKEN env var.
            api_url: Base URL for the API. Defaults to Azure OpenAI endpoint.
            model: Default model to use. Defaults to gpt-4o-mini.
            max_tokens: Default maximum tokens for completions.
            temperature: Default sampling temperature (0.0-1.0).
            timeout: Request timeout in seconds.
            max_retries: Maximum number of retry attempts for rate limits (default: 5).
            initial_backoff: Initial backoff delay in seconds (default: 2.0).
            max_backoff: Maximum backoff delay in seconds (default: 120.0).
        """
        self.api_key = api_key or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not self.api_key:
            raise CopilotClientError(
                "GitHub token required. Set GH_TOKEN or GITHUB_TOKEN environment variable "
                "or pass api_key parameter."
            )
        
        self.api_url = (api_url or self.DEFAULT_API_URL).rstrip("/")
        self.model = model or self.DEFAULT_MODEL
        self.max_tokens = max_tokens or self.DEFAULT_MAX_OUTPUT_TOKENS
        self.temperature = temperature or self.DEFAULT_TEMPERATURE
        self.timeout = timeout
        
        # Retry configuration
        self.max_retries = max_retries if max_retries is not None else self.DEFAULT_MAX_RETRIES
        self.initial_backoff = initial_backoff or self.DEFAULT_INITIAL_BACKOFF
        self.max_backoff = max_backoff or self.DEFAULT_MAX_BACKOFF

    def chat_completion(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        tools: Sequence[Mapping[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatCompletionResponse:
        """Create a chat completion with optional function calling.
        
        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: List of tool/function definitions for function calling.
            model: Model to use (overrides default).
            max_tokens: Maximum tokens (overrides default).
            temperature: Sampling temperature (overrides default).
            
        Returns:
            ChatCompletionResponse with the model's response.
            
        Raises:
            CopilotClientError: If the API request fails.
        """
        url = f"{self.api_url}/chat/completions"
        
        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": list(messages),
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature or self.temperature,
        }
        
        if tools:
            payload["tools"] = list(tools)
            payload["tool_choice"] = "auto"
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        return self._request_with_retry(url, payload, headers)

    def _request_with_retry(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> ChatCompletionResponse:
        """Execute request with exponential backoff retry for rate limits.
        
        Args:
            url: API endpoint URL.
            payload: Request JSON payload.
            headers: Request headers.
            
        Returns:
            ChatCompletionResponse on success.
            
        Raises:
            RateLimitError: If rate limit exceeded and retries exhausted.
            CopilotClientError: For other API errors.
        """
        last_exception: Exception | None = None
        backoff = self.initial_backoff
        
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                
                try:
                    data = response.json()
                except json.JSONDecodeError as exc:
                    raise CopilotClientError(f"Invalid JSON response: {exc}") from exc
                
                return self._parse_response(data)
                
            except requests.RequestException as exc:
                # Check if this is a rate limit error
                if hasattr(exc, "response") and exc.response is not None:
                    if exc.response.status_code == 429:
                        retry_after = self._parse_retry_after(exc.response)
                        wait_time = retry_after if retry_after else backoff
                        
                        # Cap wait time at max_backoff
                        wait_time = min(wait_time, self.max_backoff)
                        
                        if attempt < self.max_retries:
                            logger.warning(
                                "Rate limit hit (attempt %d/%d). Waiting %.1f seconds before retry.",
                                attempt + 1,
                                self.max_retries + 1,
                                wait_time,
                            )
                            time.sleep(wait_time)
                            # Exponential backoff for next attempt
                            backoff = min(backoff * self.DEFAULT_BACKOFF_MULTIPLIER, self.max_backoff)
                            last_exception = exc
                            continue
                        else:
                            # Exhausted retries
                            error_msg = self._build_error_message(exc)
                            raise RateLimitError(
                                f"Rate limit exceeded after {self.max_retries + 1} attempts: {error_msg}",
                                retry_after=retry_after,
                            ) from exc
                
                # Non-rate-limit error - don't retry
                error_msg = self._build_error_message(exc)
                raise CopilotClientError(error_msg) from exc
        
        # Should not reach here, but handle edge case
        if last_exception:
            raise CopilotClientError(f"Request failed after retries: {last_exception}") from last_exception
        raise CopilotClientError("Request failed unexpectedly")

    def _parse_retry_after(self, response: requests.Response) -> float | None:
        """Parse Retry-After header from response.
        
        Args:
            response: HTTP response object.
            
        Returns:
            Seconds to wait, or None if header not present/parseable.
        """
        retry_after = response.headers.get("Retry-After")
        if not retry_after:
            # Try to extract from response body
            try:
                data = response.json()
                # Handle GitHub Models API format
                message = data.get("message", "") or data.get("details", "")
                if "wait" in message.lower():
                    import re
                    match = re.search(r"wait\s+(\d+)\s*second", message, re.IGNORECASE)
                    if match:
                        return float(match.group(1))
            except Exception:
                pass
            return None
        
        try:
            # Retry-After can be seconds or HTTP date
            return float(retry_after)
        except ValueError:
            # Attempt to parse as HTTP date (not common for this API)
            return None

    def _build_error_message(self, exc: requests.RequestException) -> str:
        """Build descriptive error message from request exception."""
        error_msg = f"GitHub Models API request failed: {exc}"
        if hasattr(exc, "response") and exc.response is not None:
            try:
                error_data = exc.response.json()
                if "error" in error_data:
                    error_msg = f"{error_msg} - {error_data['error']}"
                elif "message" in error_data:
                    error_msg = f"{error_msg} - {error_data}"
            except Exception:
                pass
        return error_msg

    def _parse_response(self, data: dict[str, Any]) -> ChatCompletionResponse:
        """Parse API response into structured objects."""
        choices = []
        
        for choice_data in data.get("choices", []):
            message_data = choice_data.get("message", {})
            
            # Parse tool calls if present
            tool_calls = None
            if "tool_calls" in message_data and message_data["tool_calls"]:
                parsed_calls = []
                for tc in message_data["tool_calls"]:
                    parsed_calls.append(
                        ToolCall(
                            id=tc["id"],
                            type=tc["type"],
                            function=FunctionCall(
                                name=tc["function"]["name"],
                                arguments=tc["function"]["arguments"],
                            ),
                        )
                    )
                tool_calls = tuple(parsed_calls)
            
            message = ChatMessage(
                role=message_data.get("role", "assistant"),
                content=message_data.get("content") or "",
                tool_calls=tool_calls,
                tool_call_id=message_data.get("tool_call_id"),
                name=message_data.get("name"),
            )
            
            choices.append(
                Choice(
                    index=choice_data.get("index", 0),
                    message=message,
                    finish_reason=choice_data.get("finish_reason"),
                )
            )
        
        usage = None
        if "usage" in data:
            usage = Usage(
                prompt_tokens=data["usage"].get("prompt_tokens", 0),
                completion_tokens=data["usage"].get("completion_tokens", 0),
                total_tokens=data["usage"].get("total_tokens", 0),
            )
        
        return ChatCompletionResponse(
            id=data.get("id", ""),
            model=data.get("model", ""),
            choices=tuple(choices),
            usage=usage,
        )
