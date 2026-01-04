"""Tests for the LLM-based planner."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.integrations.github.models import (
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    GitHubModelsClient,
    FunctionCall,
    ToolCall as GitHubModelsToolCall,
)
from src.orchestration.llm import LLMPlanner, LLMPlannerError
from src.orchestration.missions import Mission
from src.orchestration.tools import ActionRisk, ToolDefinition, ToolRegistry, ToolResult
from src.orchestration.types import (
    AgentState,
    AgentStep,
    ExecutionContext,
    Thought,
    ThoughtType,
    ToolCall,
)


@pytest.fixture
def mock_models_client():
    """Mock GitHubModelsClient for testing."""
    return MagicMock(spec=GitHubModelsClient)


@pytest.fixture
def tool_registry():
    """Create a simple tool registry for testing."""
    registry = ToolRegistry()
    
    # Add a safe read-only tool
    registry.register_tool(
        ToolDefinition(
            name="get_issue_details",
            description="Fetch issue details from GitHub",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {"type": "integer"},
                },
                "required": ["issue_number"],
            },
            handler=lambda args: ToolResult(success=True, output={"title": "Test Issue"}),
            risk_level=ActionRisk.SAFE,
        )
    )
    
    # Add a mutation tool
    registry.register_tool(
        ToolDefinition(
            name="add_label",
            description="Add a label to an issue",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["issue_number", "label"],
            },
            handler=lambda args: ToolResult(success=True),
            risk_level=ActionRisk.REVIEW,
        )
    )
    
    return registry


@pytest.fixture
def simple_mission():
    """Create a simple mission for testing."""
    return Mission(
        id="test_mission",
        goal="Classify and label issue #42",
        max_steps=10,
        constraints=["Only use approved tools", "Provide clear reasoning"],
        success_criteria=["Issue has appropriate label"],
        allowed_tools=["get_issue_details", "add_label"],
    )


def test_llm_planner_initialization(mock_models_client, tool_registry):
    """LLMPlanner initializes with required components."""
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    assert planner._models_client == mock_models_client
    assert planner._tool_registry == tool_registry
    assert planner._max_tokens == 4000
    assert planner._temperature == 0.7
    assert planner._conversation_history == []


def test_llm_planner_custom_params(mock_models_client, tool_registry):
    """LLMPlanner accepts custom parameters."""
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
        max_tokens=2000,
        temperature=0.5,
    )
    
    assert planner._max_tokens == 2000
    assert planner._temperature == 0.5


def test_plan_next_first_step(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner generates appropriate prompt for first step."""
    # Mock LLM response suggesting a tool call
    mock_response = ChatCompletionResponse(
        id="test",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Let me fetch the issue details first",
                    tool_calls=(
                        GitHubModelsToolCall(
                            id="call_123",
                            type="function",
                            function=FunctionCall(
                                name="get_issue_details",
                                arguments='{"issue_number": 42}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.return_value = mock_response
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(inputs={"issue_number": 42}),
        steps=tuple(),
    )
    
    thought = planner.plan_next(state)
    
    # Verify the thought is an action with tool call
    assert thought.type == ThoughtType.ACTION
    assert thought.tool_call is not None
    assert thought.tool_call.name == "get_issue_details"
    assert thought.tool_call.arguments == {"issue_number": 42}
    
    # Verify LLM was called with appropriate context
    assert mock_models_client.chat_completion.called
    call_kwargs = mock_models_client.chat_completion.call_args[1]
    
    messages = call_kwargs["messages"]
    assert len(messages) == 2  # system + user
    assert messages[0]["role"] == "system"
    assert "Classify and label issue #42" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Begin mission" in messages[1]["content"]


def test_plan_next_with_history(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner includes execution history in prompts."""
    # Mock LLM response suggesting finish
    mock_response = ChatCompletionResponse(
        id="test",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Mission complete - issue has been labeled",
                    tool_calls=None,
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.return_value = mock_response
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    # Create state with previous steps
    previous_steps = (
        AgentStep(
            thought=Thought(
                content="Fetching issue",
                type=ThoughtType.ACTION,
                tool_call=ToolCall(name="get_issue_details", arguments={"issue_number": 42}),
            ),
            result=ToolResult(success=True, output={"title": "Bug report"}),
        ),
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(inputs={"issue_number": 42}),
        steps=previous_steps,
    )
    
    thought = planner.plan_next(state)
    
    # Verify thought is finish
    assert thought.type == ThoughtType.FINISH
    assert "complete" in thought.content.lower()
    
    # Verify history was included in prompt
    call_kwargs = mock_models_client.chat_completion.call_args[1]
    messages = call_kwargs["messages"]
    user_message = [m for m in messages if m["role"] == "user"][-1]
    assert "Progress so far" in user_message["content"]
    assert "get_issue_details" in user_message["content"]


def test_plan_next_validates_tool_allowed(mock_models_client, tool_registry):
    """LLMPlanner rejects tools not allowed by mission."""
    # Create mission that only allows one tool
    restricted_mission = Mission(
        id="restricted",
        goal="Just get issue details",
        max_steps=5,
        allowed_tools=["get_issue_details"],  # add_label not allowed
    )
    
    # Mock LLM trying to call disallowed tool
    mock_response = ChatCompletionResponse(
        id="test",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Adding label",
                    tool_calls=(
                        GitHubModelsToolCall(
                            id="call_456",
                            type="function",
                            function=FunctionCall(
                                name="add_label",
                                arguments='{"issue_number": 42, "label": "bug"}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.return_value = mock_response
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=restricted_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    with pytest.raises(LLMPlannerError, match="not allowed"):
        planner.plan_next(state)


def test_plan_next_handles_invalid_json_arguments(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner raises error on malformed tool arguments."""
    # Mock LLM response with invalid JSON
    mock_response = ChatCompletionResponse(
        id="test",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                        GitHubModelsToolCall(
                            id="call_789",
                            type="function",
                            function=FunctionCall(
                                name="get_issue_details",
                                arguments='{"issue_number": invalid}',  # Not valid JSON
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.return_value = mock_response
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    with pytest.raises(LLMPlannerError, match="Invalid JSON"):
        planner.plan_next(state)


def test_plan_next_handles_empty_response(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner raises error when LLM returns no choices."""
    mock_response = ChatCompletionResponse(
        id="test",
        model="gpt-4o-mini",
        choices=tuple(),  # Empty choices
    )
    
    mock_models_client.chat_completion.return_value = mock_response
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    with pytest.raises(LLMPlannerError, match="no choices"):
        planner.plan_next(state)


def test_build_system_prompt_includes_mission_context(tool_registry, simple_mission):
    """System prompt includes mission goal, constraints, and criteria."""
    planner = LLMPlanner(
        models_client=MagicMock(spec=GitHubModelsClient),
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    prompt = planner._build_system_prompt(state)
    
    assert "Classify and label issue #42" in prompt
    assert "Only use approved tools" in prompt
    assert "Provide clear reasoning" in prompt
    assert "Issue has appropriate label" in prompt
    assert "get_issue_details" in prompt
    assert "add_label" in prompt
    assert "10 steps" in prompt


def test_conversation_history_tracks_interactions(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner maintains conversation history across calls."""
    # First call - tool action
    response1 = ChatCompletionResponse(
        id="test1",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Fetching issue",
                    tool_calls=(
                        GitHubModelsToolCall(
                            id="call_001",
                            type="function",
                            function=FunctionCall(
                                name="get_issue_details",
                                arguments='{"issue_number": 42}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    # Second call - finish (must include explicit finish marker)
    response2 = ChatCompletionResponse(
        id="test2",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="FINISH: All tasks completed successfully",
                    tool_calls=None,
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.side_effect = [response1, response2]
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state1 = AgentState(
        mission=simple_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    # First call
    planner.plan_next(state1)
    assert len(planner._conversation_history) == 2  # user + assistant
    
    # Second call
    state2 = state1.with_step(
        AgentStep(
            thought=Thought(
                content="Fetching",
                type=ThoughtType.ACTION,
                tool_call=ToolCall(name="get_issue_details", arguments={"issue_number": 42}),
            ),
            result=ToolResult(success=True),
        )
    )
    
    planner.plan_next(state2)
    assert len(planner._conversation_history) == 4  # 2 user + 2 assistant


def test_plan_next_rejects_ambiguous_finish(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner raises error after retries when LLM never provides tool call or explicit finish."""
    # Response without tool call and without explicit completion signal
    mock_response = ChatCompletionResponse(
        id="test",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="I need to check the issue details first.",
                    tool_calls=None,
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.return_value = mock_response
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    with pytest.raises(LLMPlannerError, match="without a tool call or explicit finish signal"):
        planner.plan_next(state)
    
    # Verify retries happened (initial + MAX_CLARIFICATION_RETRIES)
    assert mock_models_client.chat_completion.call_count == 3


def test_plan_next_retries_and_succeeds(mock_models_client, tool_registry, simple_mission):
    """LLMPlanner retries when LLM responds without action, then succeeds on retry."""
    # First response - ambiguous (no tool call, no finish marker)
    ambiguous_response = ChatCompletionResponse(
        id="test1",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Let me think about this...",
                    tool_calls=None,
                ),
            ),
        ),
    )
    
    # Second response - proper tool call
    tool_response = ChatCompletionResponse(
        id="test2",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Fetching issue details",
                    tool_calls=(
                        GitHubModelsToolCall(
                            id="call_001",
                            type="function",
                            function=FunctionCall(
                                name="get_issue_details",
                                arguments='{"issue_number": 42}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    mock_models_client.chat_completion.side_effect = [ambiguous_response, tool_response]
    
    planner = LLMPlanner(
        models_client=mock_models_client,
        tool_registry=tool_registry,
    )
    
    state = AgentState(
        mission=simple_mission,
        context=ExecutionContext(),
        steps=tuple(),
    )
    
    thought = planner.plan_next(state)
    
    # Should succeed on retry
    assert thought.type == ThoughtType.ACTION
    assert thought.tool_call.name == "get_issue_details"
    assert mock_models_client.chat_completion.call_count == 2


def test_get_openai_tool_schemas_format(tool_registry):
    """ToolRegistry returns tools in OpenAI function calling format."""
    schemas = tool_registry.get_openai_tool_schemas()
    
    assert len(schemas) == 2
    
    # Verify format
    for schema in schemas:
        assert schema["type"] == "function"
        assert "function" in schema
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]
    
    # Verify specific tools
    names = [s["function"]["name"] for s in schemas]
    assert "get_issue_details" in names
    assert "add_label" in names
