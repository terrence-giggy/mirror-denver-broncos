"""LLM-based planner using GitHub Models API for autonomous agent reasoning."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Dict, List

from src.integrations.github.models import GitHubModelsClient, GitHubModelsError

from .planner import Planner
from .types import AgentState, Thought, ThoughtType, ToolCall

if TYPE_CHECKING:
    from .tools import ToolRegistry


class LLMPlannerError(Exception):
    """Error during LLM-based planning."""


class LLMPlanner(Planner):
    """Planner powered by GitHub Models API for autonomous reasoning.

    This planner uses an LLM to analyze the mission state and decide what
    action to take next, enabling true autonomous agent behavior rather than
    following predetermined scripts.
    """

    def __init__(
        self,
        *,
        models_client: GitHubModelsClient,
        tool_registry: ToolRegistry,
        max_tokens: int = 4000,
        temperature: float = 0.7,
    ):
        """Initialize LLM planner with GitHub Models client.

        Args:
            models_client: GitHub Models API client for LLM calls.
            tool_registry: Registry of available tools for function calling.
            max_tokens: Token limit per LLM call.
            temperature: Sampling temperature (0.0-1.0).
        """
        self._models_client = models_client
        self._tool_registry = tool_registry
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._conversation_history: List[Dict[str, Any]] = []

    # Maximum retries when LLM responds without tool call or explicit finish
    MAX_CLARIFICATION_RETRIES = 2

    def plan_next(self, state: AgentState) -> Thought:
        """Use LLM to determine the next action based on mission state.

        Args:
            state: Current mission execution state.

        Returns:
            Thought containing either a tool call or finish signal.

        Raises:
            LLMPlannerError: If LLM call fails or response is invalid.
        """
        if not state.steps:
            # New mission run detected, reset conversation memory
            self._conversation_history = []

        system_prompt = self._build_system_prompt(state)
        user_prompt = self._build_user_prompt(state)
        user_message: Dict[str, Any] = {"role": "user", "content": user_prompt}

        tools = self._tool_registry.get_openai_tool_schemas()
        if state.mission.allowed_tools is not None:
            allowed = set(state.mission.allowed_tools)
            tools = [
                tool
                for tool in tools
                if tool["function"]["name"] in allowed
            ]

        messages = self._build_messages(system_prompt, state, user_message)

        # Retry loop for when LLM responds without a tool call or finish signal
        for attempt in range(self.MAX_CLARIFICATION_RETRIES + 1):
            try:
                response = self._models_client.chat_completion(
                    messages=messages,
                    tools=tools if tools else None,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                )
            except GitHubModelsError as exc:
                raise LLMPlannerError(f"LLM call failed: {exc}") from exc

            try:
                thought = self._parse_response(response, state)
                
                # Success - update conversation history and return
                assistant_message_dict = self._message_to_dict(response.choices[0].message)
                self._conversation_history.append(dict(user_message))
                self._conversation_history.append(assistant_message_dict)
                # Keep a rolling window to avoid unbounded growth
                if len(self._conversation_history) > 40:
                    self._conversation_history = self._conversation_history[-40:]

                return thought
                
            except LLMPlannerError as exc:
                if "without a tool call or explicit finish signal" not in str(exc):
                    raise  # Re-raise other errors
                    
                if attempt >= self.MAX_CLARIFICATION_RETRIES:
                    raise  # Exhausted retries
                    
                # Add clarification request to messages and retry
                assistant_content = response.choices[0].message.content or ""
                messages.append({"role": "assistant", "content": assistant_content})
                messages.append({
                    "role": "user",
                    "content": (
                        "Please use a tool to take action. If you have completed all success criteria, "
                        "respond with 'FINISH:' followed by a summary. Otherwise, call the appropriate tool."
                    ),
                })
        
        # Should not reach here, but satisfy type checker
        raise LLMPlannerError("Unexpected state in plan_next retry loop")

    def _build_system_prompt(self, state: AgentState) -> str:
        """Create system prompt with mission context and instructions."""
        mission = state.mission

        tool_names = [tool.name for tool in self._tool_registry]

        if mission.allowed_tools is not None:
            tool_names = [t for t in tool_names if mission.is_tool_allowed(t)]

        can_post_comment = "post_comment" in tool_names
        label_tools = {"add_label", "add_labels", "remove_label"}
        can_modify_labels = any(tool in label_tools for tool in tool_names)
        mutation_tools = {
            "close_issue",
            "reopen_issue",
            "lock_issue",
            "unlock_issue",
            "update_issue_title",
            "update_issue_body",
            "merge_pr",
            "request_review",
        }
        has_mutation_tools = (
            can_post_comment
            or can_modify_labels
            or any(tool in mutation_tools for tool in tool_names)
        )

        available_tools_str = ", ".join(tool_names) if tool_names else "None"

        prompt_parts = [
            "You are an autonomous GitHub repository management agent.",
            "",
            f"Mission Goal: {mission.goal}",
            "",
        ]

        if mission.constraints:
            prompt_parts.append("Constraints:")
            for constraint in mission.constraints:
                prompt_parts.append(f"- {constraint}")
            prompt_parts.append("")

        if mission.success_criteria:
            prompt_parts.append("Success Criteria:")
            for criterion in mission.success_criteria:
                prompt_parts.append(f"- {criterion}")
            prompt_parts.append("")

        prompt_parts.extend([
            f"Available Tools: {available_tools_str}",
            "",
            "Instructions:",
        ])

        instruction_lines = [
            "Analyze the current state and mission goal carefully",
            "Review all success criteria - you must complete ALL of them, not just some",
            "Take concrete actions with available tools to satisfy each criterion",
            "ALWAYS use a tool call to take action - do not just describe what you would do",
        ]

        if can_modify_labels:
            instruction_lines.append(
                "For triage missions: categorize issues by adding appropriate labels"
            )
        else:
            instruction_lines.append(
                "For triage missions without label tools: note recommended labels in your final response"
            )

        if can_post_comment:
            instruction_lines.append(
                "Document your analysis by posting comments when that will help collaborators"
            )
        else:
            instruction_lines.append(
                "Document your analysis in the final mission report; do not attempt to post comments"
            )

        instruction_lines.append(
            "When ALL success criteria are met, signal completion by starting your response with 'FINISH:'"
        )

        for index, instruction in enumerate(instruction_lines, start=1):
            prompt_parts.append(f"{index}. {instruction}")
        prompt_parts.append("")

        prompt_parts.append("IMPORTANT:")

        important_lines = ["- Retrieving information is just the first step"]

        if has_mutation_tools:
            important_lines.extend([
                "- You must analyze AND take action (add labels, post recommendations, etc.)",
                "- Don't just describe what you would do - actually do it using the available tools",
            ])
        else:
            important_lines.extend([
                "- Stay within the mission's read-only constraints",
                "- Provide clear recommendations in your final response instead of modifying GitHub artifacts",
            ])

        prompt_parts.extend(important_lines)
        prompt_parts.append("")

        prompt_parts.extend([
            f"You have a maximum of {mission.max_steps} steps to complete this mission.",
            f"Current step: {len(state.steps) + 1} of {mission.max_steps}",
        ])

        return "\n".join(prompt_parts)

    def _build_messages(
        self,
        system_prompt: str,
        state: AgentState,
        user_message: Dict[str, Any],
    ) -> list[Dict[str, Any]]:
        """Build message history in OpenAI format from state.

        Args:
            system_prompt: System-level instructions.
            state: Current mission state with execution history.
            user_message: Prompt delivered for the next action request.

        Returns:
            List of messages formatted for OpenAI chat completions API.
        """
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if state.steps:
            inputs_str = json.dumps(state.context.inputs, indent=2) if state.context.inputs else "{}"
            messages.append({
                "role": "user",
                "content": f"Begin mission. Inputs:\n{inputs_str}\n\nWhat's your first action?"
            })

            for i, step in enumerate(state.steps, 1):
                thought = step.thought
                result = step.result

                tool_call_id: str | None = None

                if thought.tool_call:
                    tool_call_id = f"call_{i}"
                    assistant_payload: Dict[str, Any] = {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": thought.tool_call.name,
                                "arguments": json.dumps(thought.tool_call.arguments),
                            },
                        }],
                    }
                    if thought.content:
                        assistant_payload["content"] = thought.content
                    messages.append(assistant_payload)
                else:
                    content = thought.content or "Continuing mission."
                    messages.append({"role": "assistant", "content": content})

                if result:
                    if result.success:
                        if result.output is not None:
                            try:
                                tool_output = json.dumps(result.output, default=str)
                            except TypeError:
                                tool_output = str(result.output)
                        else:
                            tool_output = "Success"
                    else:
                        tool_output = result.error or "Error executing tool"

                    tool_message: Dict[str, Any] = {
                        "role": "tool",
                        "content": tool_output,
                    }
                    if tool_call_id is not None:
                        tool_message["tool_call_id"] = tool_call_id
                    messages.append(tool_message)

        messages.append(user_message)

        return messages

    def _build_user_prompt(self, state: AgentState) -> str:
        """Create user prompt with execution history and context."""
        if not state.steps:
            # First step - provide mission inputs
            inputs_str = json.dumps(state.context.inputs, indent=2) if state.context.inputs else "{}"
            return f"Begin mission. Inputs:\n{inputs_str}\n\nWhat's your first action?"

        # Subsequent steps - summarize progress
        prompt_parts = ["Progress so far:"]

        for i, step in enumerate(state.steps, 1):
            thought = step.thought
            result = step.result

            # Describe the action taken
            if thought.tool_call:
                action_desc = f"Step {i}: Called {thought.tool_call.name}"
                if thought.content and thought.content != f"Calling {thought.tool_call.name}":
                    action_desc = f"{action_desc} ({thought.content})"
            else:
                action_desc = f"Step {i}: {thought.content}"

            prompt_parts.append(action_desc)

            # Describe the result
            if result:
                if result.success:
                    if result.output:
                        output_preview = str(result.output)[:200]
                        if len(str(result.output)) > 200:
                            output_preview += "..."
                        prompt_parts.append(f"  Result: {output_preview}")
                    else:
                        prompt_parts.append("  Result: Success")
                else:
                    error_msg = result.error or "Unknown error"
                    prompt_parts.append(f"  Error: {error_msg}")

        prompt_parts.extend(["", "What's your next action?"])

        return "\n".join(prompt_parts)

    def _parse_response(self, response, state: AgentState) -> Thought:
        """Convert LLM response to a Thought.

        Args:
            response: ChatCompletionResponse from the API.
            state: Current mission state for validation.

        Returns:
            Thought with either a tool call or finish signal.

        Raises:
            LLMPlannerError: If response cannot be parsed or is invalid.
        """
        if not response.choices:
            raise LLMPlannerError("LLM response contains no choices")

        message = response.choices[0].message

        # Check for function/tool call
        if message.tool_calls and len(message.tool_calls) > 0:
            tool_call_data = message.tool_calls[0]
            function = tool_call_data.function

            # Parse arguments
            try:
                arguments = json.loads(function.arguments)
            except json.JSONDecodeError as exc:
                raise LLMPlannerError(
                    f"Invalid JSON in tool arguments: {function.arguments}"
                ) from exc

            # Validate tool is allowed
            if not state.mission.is_tool_allowed(function.name):
                raise LLMPlannerError(
                    f"Tool '{function.name}' is not allowed for this mission"
                )

            # Create thought with tool call
            content = message.content or f"Calling {function.name}"
            return Thought(
                content=content,
                type=ThoughtType.ACTION,
                tool_call=ToolCall(
                    name=function.name,
                    arguments=arguments,
                ),
            )

        # No tool call - check if LLM explicitly signaled completion
        content = message.content or ""
        if self._is_explicit_finish(content):
            return Thought(
                content=content or "Mission complete",
                type=ThoughtType.FINISH,
            )

        # LLM responded without a tool call and without explicit finish signal.
        # This typically means it's "thinking out loud" without taking action.
        # Raise an error so the caller can retry or handle gracefully.
        raise LLMPlannerError(
            "LLM responded without a tool call or explicit finish signal. "
            "To complete the mission, include 'FINISH:' in your response. "
            f"Response was: {content[:200]}"
        )

    def _is_explicit_finish(self, content: str) -> bool:
        """Determine if the response content explicitly signals mission completion.

        Looks for explicit markers like 'FINISH:', 'Mission complete', etc.
        This prevents ambiguous responses from prematurely ending missions.
        """
        if not content:
            return False

        content_lower = content.lower()

        # Explicit finish markers that indicate intentional completion
        explicit_markers = [
            "finish:",
            "mission complete",
            "mission accomplished",
            "all success criteria",
            "successfully completed all",
            "all tasks completed",
            "all criteria met",
        ]

        return any(marker in content_lower for marker in explicit_markers)

    def _message_to_dict(self, message) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"role": message.role}
        if message.content:
            payload["content"] = message.content
        if message.name:
            payload["name"] = message.name
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.tool_calls:
            calls: List[Dict[str, Any]] = []
            for call in message.tool_calls:
                calls.append({
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                })
            payload["tool_calls"] = calls
        return payload
