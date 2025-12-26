"""Integration tests for LLM planner with agent runtime."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.copilot.client import (
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    CopilotClient,
    FunctionCall,
    ToolCall as CopilotToolCall,
)
from src.orchestration.agent import AgentRuntime, MissionEvaluator, EvaluationResult
from src.orchestration.llm import LLMPlanner
from src.orchestration.missions import Mission
from src.orchestration.safety import SafetyValidator
from src.orchestration.tools import ActionRisk, ToolDefinition, ToolRegistry, ToolResult
from src.orchestration.types import AgentStep, ExecutionContext, MissionStatus


class SimpleEvaluator:
        """Minimal evaluator that marks mission complete when expected tool calls are made."""
        
        def evaluate(self, mission, steps, context):
            # Check if we have successfully executed the expected tools
            # For triage mission: get_issue_details and add_label
            success_steps = [step for step in steps if step.result is not None and step.result.success]
            
            # Mission succeeds if we have at least 2 successful tool executions
            complete = len(success_steps) >= 2
            reason = "Mission goals achieved" if complete else None
            return EvaluationResult(complete=complete, reason=reason)


@pytest.fixture
def mock_copilot_client():
    """Mock CopilotClient for integration tests."""
    return MagicMock(spec=CopilotClient)


@pytest.fixture
def tool_registry():
    """Create tool registry with test tools."""
    registry = ToolRegistry()
    
    # Read-only tool
    registry.register_tool(
        ToolDefinition(
            name="get_issue_details",
            description="Fetch GitHub issue details",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {"type": "integer"},
                },
                "required": ["issue_number"],
            },
            handler=lambda args: ToolResult(
                success=True,
                output={
                    "number": args["issue_number"],
                    "title": "Test Issue",
                    "body": "This is a test issue for KB extraction",
                    "labels": [],
                }
            ),
            risk_level=ActionRisk.SAFE,
        )
    )
    
    # Mutation tool
    registry.register_tool(
        ToolDefinition(
            name="add_label",
            description="Add label to GitHub issue",
            parameters={
                "type": "object",
                "properties": {
                    "issue_number": {"type": "integer"},
                    "label": {"type": "string"},
                },
                "required": ["issue_number", "label"],
            },
            handler=lambda args: ToolResult(success=True, output={"label_added": args["label"]}),
            risk_level=ActionRisk.REVIEW,
        )
    )
    
    return registry


def test_llm_planner_executes_simple_mission(mock_copilot_client, tool_registry):
    """LLM planner successfully executes a simple 2-step mission."""
    
    # Mission: get issue details, then add label
    mission = Mission(
        id="test_triage",
        goal="Triage issue #42 by checking details and adding kb-extraction label",
        max_steps=5,
        constraints=["Only modify labels"],
        success_criteria=["Issue has kb-extraction label"],
    )
    
    # Mock LLM responses
    response1 = ChatCompletionResponse(
        id="call1",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="I'll fetch the issue details first",
                    tool_calls=(
                        CopilotToolCall(
                            id="call_1",
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
    
    response2 = ChatCompletionResponse(
        id="call2",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Based on the issue body, I'll add kb-extraction label",
                    tool_calls=(
                        CopilotToolCall(
                            id="call_2",
                            type="function",
                            function=FunctionCall(
                                name="add_label",
                                arguments='{"issue_number": 42, "label": "kb-extraction"}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    response3 = ChatCompletionResponse(
        id="call3",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Mission complete - issue #42 has been triaged and labeled",
                    tool_calls=None,
                ),
            ),
        ),
    )
    
    mock_copilot_client.chat_completion.side_effect = [response1, response2, response3]
    
    # Create planner and runtime
    planner = LLMPlanner(
        copilot_client=mock_copilot_client,
        tool_registry=tool_registry,
    )
    
    runtime = AgentRuntime(
        planner=planner,
        tools=tool_registry,
        safety=SafetyValidator(),
        evaluator=SimpleEvaluator(),
    )
    
    # Execute mission
    context = ExecutionContext(inputs={"issue_number": 42})
    outcome = runtime.execute_mission(mission, context)
    
    # Verify success
    assert outcome.status == MissionStatus.SUCCEEDED
    assert len(outcome.steps) == 2  # Two tool executions (get_issue_details, add_label)
    
    # Verify step 1: get_issue_details
    assert outcome.steps[0].thought.tool_call is not None
    assert outcome.steps[0].thought.tool_call.name == "get_issue_details"
    assert outcome.steps[0].result is not None
    assert outcome.steps[0].result.success
    
    # Verify step 2: add_label
    assert outcome.steps[1].thought.tool_call is not None
    assert outcome.steps[1].thought.tool_call.name == "add_label"
    assert outcome.steps[1].result is not None
    assert outcome.steps[1].result.success


def test_llm_planner_handles_tool_errors(mock_copilot_client, tool_registry):
    """LLM planner adapts when a tool execution fails."""
    
    mission = Mission(
        id="test_error_handling",
        goal="Handle errors gracefully",
        max_steps=5,
    )
    
    # Mock failing tool
    failing_registry = ToolRegistry()
    failing_registry.register_tool(
        ToolDefinition(
            name="failing_tool",
            description="A tool that fails",
            parameters={"type": "object", "properties": {}},
            handler=lambda args: ToolResult(success=False, error="Network error"),
            risk_level=ActionRisk.SAFE,
        )
    )
    
    # LLM tries failing tool, then finishes
    response1 = ChatCompletionResponse(
        id="call1",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Attempting action",
                    tool_calls=(
                        CopilotToolCall(
                            id="call_1",
                            type="function",
                            function=FunctionCall(
                                name="failing_tool",
                                arguments='{}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    response2 = ChatCompletionResponse(
        id="call2",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="FINISH: Cannot proceed due to network error",
                    tool_calls=None,
                ),
            ),
        ),
    )
    
    mock_copilot_client.chat_completion.side_effect = [response1, response2]
    
    # Use an evaluator that doesn't mark complete after failed steps
    class ErrorHandlingEvaluator:
        def evaluate(self, mission, steps, context):
            # Not complete until we see the finish thought reflected in multiple attempts
            return EvaluationResult(complete=False, reason=None)
    
    planner = LLMPlanner(
        copilot_client=mock_copilot_client,
        tool_registry=failing_registry,
    )
    
    runtime = AgentRuntime(
        planner=planner,
        tools=failing_registry,
        safety=SafetyValidator(),
        evaluator=ErrorHandlingEvaluator(),
    )
    
    outcome = runtime.execute_mission(mission, ExecutionContext())
    
    # Agent should try the failing tool once, then give up
    assert len(outcome.steps) == 1  # Only the failed tool execution
    assert outcome.steps[0].result is not None
    assert outcome.steps[0].result.success is False
    assert outcome.steps[0].result.error is not None
    assert "error" in outcome.steps[0].result.error.lower()


def test_llm_planner_respects_max_steps(mock_copilot_client, tool_registry):
    """LLM planner stops at max_steps limit."""
    
    mission = Mission(
        id="test_max_steps",
        goal="Test step limit",
        max_steps=2,
    )
    
    # LLM keeps suggesting actions
    response = ChatCompletionResponse(
        id="call",
        model="gpt-4o-mini",
        choices=(
            Choice(
                index=0,
                message=ChatMessage(
                    role="assistant",
                    content="Fetching issue",
                    tool_calls=(
                        CopilotToolCall(
                            id="call_x",
                            type="function",
                            function=FunctionCall(
                                name="get_issue_details",
                                arguments='{"issue_number": 1}',
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    
    mock_copilot_client.chat_completion.return_value = response
    
    planner = LLMPlanner(
        copilot_client=mock_copilot_client,
        tool_registry=tool_registry,
    )
    
    runtime = AgentRuntime(
        planner=planner,
        tools=tool_registry,
        safety=SafetyValidator(),
        evaluator=SimpleEvaluator(),
    )
    
    outcome = runtime.execute_mission(mission, ExecutionContext())
    
    # Should stop at exactly max_steps
    assert len(outcome.steps) == 2
    assert outcome.status == MissionStatus.SUCCEEDED  # Evaluator marks complete at max


def test_llm_planner_provides_context_to_llm(mock_copilot_client, tool_registry):
    """LLM planner includes mission context in prompts."""
    
    mission = Mission(
        id="test_context",
        goal="Test that context is provided",
        max_steps=2,
        constraints=["Use minimal API calls", "Be efficient"],
        success_criteria=["Task completed quickly"],
    )
    
    response = ChatCompletionResponse(
        id="call",
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
    
    mock_copilot_client.chat_completion.return_value = response
    
    planner = LLMPlanner(
        copilot_client=mock_copilot_client,
        tool_registry=tool_registry,
    )
    
    runtime = AgentRuntime(
        planner=planner,
        tools=tool_registry,
        safety=SafetyValidator(),
        evaluator=SimpleEvaluator(),
    )
    
    runtime.execute_mission(mission, ExecutionContext(inputs={"test": "value"}))
    
    # Verify LLM was called with proper context
    assert mock_copilot_client.chat_completion.called
    call_kwargs = mock_copilot_client.chat_completion.call_args[1]
    
    messages = call_kwargs["messages"]
    system_msg = messages[0]
    
    assert system_msg["role"] == "system"
    assert "Test that context is provided" in system_msg["content"]
    assert "Use minimal API calls" in system_msg["content"]
    assert "Be efficient" in system_msg["content"]
    assert "Task completed quickly" in system_msg["content"]
    
    # Verify user message includes inputs
    user_msg = messages[1]
    assert user_msg["role"] == "user"
    assert "test" in user_msg["content"] or "value" in user_msg["content"]


def test_llm_planner_conversation_history_persists(mock_copilot_client, tool_registry):
    """LLM planner maintains conversation history across steps."""
    
    mission = Mission(
        id="test_history",
        goal="Test conversation history",
        max_steps=3,
    )
    
    responses = [
        ChatCompletionResponse(
            id=f"call{i}",
            model="gpt-4o-mini",
            choices=(
                Choice(
                    index=0,
                    message=ChatMessage(
                        role="assistant",
                        content=f"Step {i}" if i < 2 else "FINISH: All steps completed",
                        tool_calls=(
                            CopilotToolCall(
                                id=f"call_{i}",
                                type="function",
                                function=FunctionCall(
                                    name="get_issue_details",
                                    arguments='{"issue_number": 1}',
                                ),
                            ),
                        ) if i < 2 else None,
                    ),
                ),
            ),
        )
        for i in range(3)
    ]
    
    mock_copilot_client.chat_completion.side_effect = responses
    
    # Use evaluator that never marks complete so all 3 steps execute
    class NeverCompleteEvaluator:
        def evaluate(self, mission, steps, context):
            return EvaluationResult(complete=False, reason=None)
    
    planner = LLMPlanner(
        copilot_client=mock_copilot_client,
        tool_registry=tool_registry,
    )
    
    runtime = AgentRuntime(
        planner=planner,
        tools=tool_registry,
        safety=SafetyValidator(),
        evaluator=NeverCompleteEvaluator(),
    )
    
    runtime.execute_mission(mission, ExecutionContext())
    
    # Verify conversation history grew with each call
    calls = mock_copilot_client.chat_completion.call_args_list
    
    # First call: system + user
    assert len(calls[0][1]["messages"]) == 2
    
    # Second call: system + history + user (at least 4 messages)
    assert len(calls[1][1]["messages"]) >= 4
