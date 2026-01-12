"""CLI commands for agent runtime operations."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

from src.orchestration.agent import AgentRuntime, MissionEvaluator, EvaluationResult
from src.orchestration.missions import load_mission, create_ephemeral_mission, Mission
from src.orchestration.monitoring import AgentMonitor
from src.orchestration.llm import LLMPlanner
from src.orchestration.safety import SafetyValidator
from src.orchestration.tools import ToolRegistry
from src.orchestration.types import ExecutionContext, MissionStatus, AgentStep, ToolResult
from src.integrations.github.models import GitHubModelsClient
from src.integrations.github.issues import (
    DEFAULT_API_URL,
    GitHubIssueError,
    resolve_repository,
    resolve_token,
    fetch_issue,
    fetch_issue_comments,
    AGENT_RESPONSE_TAG,
)
from src.integrations.github.search_issues import GitHubIssueSearcher



def register_commands(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register agent CLI commands.
    
    Args:
        subparsers: Subparser action from main argument parser
    """
    # Main agent command with subcommands
    agent_parser = subparsers.add_parser(
        "agent",
        help="Agent runtime operations and monitoring",
    )
    
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", metavar="COMMAND")
    agent_subparsers.required = True
    
    # agent run
    _register_run_command(agent_subparsers)
    
    # agent list-missions
    _register_list_missions_command(agent_subparsers)
    
    # agent status
    _register_status_command(agent_subparsers)
    
    # agent history
    _register_history_command(agent_subparsers)
    
    # agent explain
    _register_explain_command(agent_subparsers)


def _register_run_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register 'agent run' command."""
    parser = subparsers.add_parser(
        "run",
        help="Execute a mission",
    )
    parser.add_argument(
        "--mission",
        required=True,
        help="Path to mission YAML file or mission ID in config/missions/",
    )
    parser.add_argument(
        "--input",
        action="append",
        default=[],
        help="Mission input in key=value format (can be specified multiple times)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate execution without making any changes",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Enable interactive approval prompts",
    )
    parser.add_argument(
        "--planner",
        choices=["llm"],
        default="llm",
        help="Planner to use: 'llm' for GitHub Models API (default)",
    )
    parser.add_argument(
        "--model",
        help="LLM model to use (default: gpt-4o-mini)",
    )
    parser.add_argument(
        "--output",
        help="Write execution transcript to file (JSON). Defaults to reports/transcripts/tmp/<mission_id>.json",
    )
    parser.add_argument(
        "--db",
        default="agent_metrics.db",
        help="Path to metrics database (default: agent_metrics.db)",
    )
    parser.set_defaults(func=run_mission_cli)


def _register_list_missions_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register 'agent list-missions' command."""
    parser = subparsers.add_parser(
        "list-missions",
        help="List available missions",
    )
    parser.add_argument(
        "--path",
        default="config/missions",
        help="Path to missions directory (default: config/missions/)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    parser.set_defaults(func=list_missions_cli)


def _register_status_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register 'agent status' command."""
    parser = subparsers.add_parser(
        "status",
        help="Check agent health and performance",
    )
    parser.add_argument(
        "--db",
        default="agent_metrics.db",
        help="Path to metrics database (default: agent_metrics.db)",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=24,
        help="Hours of history to analyze (default: 24)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format",
    )
    parser.set_defaults(func=status_cli)


def _register_history_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register 'agent history' command."""
    parser = subparsers.add_parser(
        "history",
        help="View recent mission executions",
    )
    parser.add_argument(
        "--db",
        default="agent_metrics.db",
        help="Path to metrics database (default: agent_metrics.db)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Number of missions to show (default: 10)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format",
    )
    parser.set_defaults(func=history_cli)


def _register_explain_command(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register 'agent explain' command."""
    parser = subparsers.add_parser(
        "explain",
        help="Show detailed execution trace for a mission",
    )
    parser.add_argument(
        "--mission-id",
        required=True,
        help="Mission execution ID to explain",
    )
    parser.add_argument(
        "--transcript",
        help="Path to execution transcript JSON file",
    )
    parser.set_defaults(func=explain_cli)


class SimpleEvaluator(MissionEvaluator):
    """Simple evaluator that validates mission success without controlling termination.
    
    This evaluator is designed to work with autonomous LLM planners that decide
    when to finish via FINISH thoughts. It validates whether work was successful
    but does NOT prematurely stop the mission mid-execution.
    """

    def __init__(self, *, required_tools: Sequence[str] | None = None) -> None:
        self._required_tools = tuple(required_tools or ())

    def evaluate(self, mission: Mission, steps: Sequence[AgentStep], context: ExecutionContext) -> EvaluationResult:
        """Validate if the mission accomplished its goals.
        
        The agent runtime calls this evaluator in two contexts:
        1. After each tool execution (mid-loop): Always returns complete=False to allow
           the planner to continue reasoning
        2. After FINISH thought or max steps: Returns complete=True if we have successful work
        
        This design lets LLM planners control their own completion while still validating
        that the mission succeeded.
        """
        # Count successful tool executions
        successful_steps = [s for s in steps if s.result and s.result.success]

        missing_required_reason = self._missing_required_successes(steps)
        if missing_required_reason:
            return EvaluationResult(complete=False, reason=missing_required_reason)

        if successful_steps:
            # We have successful work - validation passes
            summary = f"Successfully executed {len(successful_steps)} action(s)"
            return EvaluationResult(complete=True, reason=summary)

        # No successful work - validation fails
        return EvaluationResult(complete=False, reason="No successful actions completed")

    def _missing_required_successes(self, steps: Sequence[AgentStep]) -> str | None:
        """Return a failure reason if any required tools did not succeed."""

        if not self._required_tools:
            return None

        completed: set[str] = set()
        last_attempt: dict[str, ToolResult | None] = {}

        for step in steps:
            call = step.thought.tool_call
            if call is None:
                continue
            name = call.name
            if name not in self._required_tools:
                continue
            last_attempt[name] = step.result
            if step.result and step.result.success:
                completed.add(name)

        missing_tools = [name for name in self._required_tools if name not in completed]
        if not missing_tools:
            return None

        messages: list[str] = []
        for name in missing_tools:
            result = last_attempt.get(name)
            if result is None:
                messages.append(f"{name} was not executed successfully.")
            elif result.error:
                messages.append(f"{name} failed: {result.error}")
            else:
                messages.append(f"{name} did not complete successfully.")

        return "; ".join(messages)


def _build_mission_evaluator(mission: Mission) -> MissionEvaluator:
    """Return an evaluator configured for the given mission."""

    if mission.id == "kb_extraction_full":
        return SimpleEvaluator(required_tools=("assign_issue_to_copilot",))
    return SimpleEvaluator()


def _check_agent_tag(token: str, repository: str, issue_number: int, api_url: str) -> bool:
    """Check if the last message in the issue has the agent tag."""
    try:
        comments = fetch_issue_comments(
            token=token,
            repository=repository,
            issue_number=issue_number,
            api_url=api_url,
        )
        
        last_body = ""
        if comments:
            last_comment = comments[-1]
            last_body = str(last_comment.get("body", ""))
        else:
            issue = fetch_issue(
                token=token,
                repository=repository,
                issue_number=issue_number,
                api_url=api_url,
            )
            last_body = str(issue.get("body", ""))
        
        return AGENT_RESPONSE_TAG in last_body
    except GitHubIssueError:
        return False


def _prepare_agent_inputs(raw_inputs: dict[str, Any]) -> tuple[dict[str, Any], bool, str | None]:
    """Normalize mission inputs and resolve convenience values like 'auto'."""

    inputs = dict(raw_inputs)

    def _normalize_labels(raw_value: Any) -> list[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, str):
            return [label for label in (part.strip() for part in raw_value.split(",")) if label]
        if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes)):
            labels: list[str] = []
            for item in raw_value:
                if not isinstance(item, str):
                    raise ValueError("Label filters must be strings.")
                stripped = item.strip()
                if stripped:
                    labels.append(stripped)
            return labels
        raise ValueError("Label filters must be provided as a comma-separated string or sequence of strings.")

    required_labels = _normalize_labels(inputs.get("required_labels"))
    exclude_labels = _normalize_labels(inputs.get("exclude_labels"))
    inputs["required_labels"] = required_labels
    inputs["exclude_labels"] = exclude_labels

    value = inputs.get("issue_number")

    if value is None:
        return inputs, False, None

    if isinstance(value, int):
        if value < 1:
            raise ValueError("issue_number input must be >= 1.")

        # Check for agent response tag
        try:
            repository_arg = inputs.get("repository")
            token_arg = inputs.get("token")
            api_url_arg = inputs.get("api_url")

            repository = resolve_repository(str(repository_arg) if repository_arg else None)
            token = resolve_token(str(token_arg) if token_arg else None)
            api_url = str(api_url_arg) if api_url_arg else DEFAULT_API_URL

            if _check_agent_tag(token, repository, value, api_url):
                return inputs, True, f"Skipping issue #{value} as the last message was from the agent."
        except GitHubIssueError:
            pass

        return inputs, False, None

    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("issue_number input cannot be empty.")

        if trimmed.lower() != "auto":
            try:
                parsed = int(trimmed)
            except ValueError as exc:
                raise ValueError("issue_number input must be an integer or 'auto'.") from exc
            if parsed < 1:
                raise ValueError("issue_number input must be >= 1.")
            inputs["issue_number"] = parsed
            return inputs, False, None

        repository_arg = inputs.get("repository")
        token_arg = inputs.get("token")
        api_url_arg = inputs.get("api_url")

        repository = resolve_repository(str(repository_arg) if repository_arg else None)
        token = resolve_token(str(token_arg) if token_arg else None)
        api_url = str(api_url_arg) if api_url_arg else DEFAULT_API_URL

        searcher = GitHubIssueSearcher(token=token, repository=repository, api_url=api_url)
        if required_labels:
            results = searcher.search_with_label_filters(
                required_labels=required_labels,
                excluded_labels=exclude_labels,
                limit=1,
                sort="created",
                order="asc",
            )
        else:
            results = searcher.search_unlabeled(limit=1, order="asc")

        if not results:
            if required_labels:
                label_text = ", ".join(required_labels)
                message = f"No open issues found with required labels ({label_text}); skipping mission."
            else:
                message = "No open unlabeled issues found; skipping mission."
            return inputs, True, message

        selection = results[0]

        if _check_agent_tag(token, repository, selection.number, api_url):
            return inputs, True, f"Skipping auto-selected issue #{selection.number} as the last message was from the agent."

        inputs["issue_number"] = selection.number
        inputs["auto_issue_selected"] = True
        inputs.setdefault("auto_issue_url", selection.url)

        if required_labels:
            label_text = ", ".join(required_labels)
            notice = (
                f"Auto-selected issue #{selection.number} matching labels ({label_text}): {selection.url}"
            )
        else:
            notice = f"Auto-selected open unlabeled issue #{selection.number}: {selection.url}"
        return inputs, False, notice

    raise ValueError("issue_number input must be an integer or the string 'auto'.")


def run_mission_cli(args: argparse.Namespace) -> int:
    """Execute a mission.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    # Load mission
    mission_arg = args.mission
    mission_path = Path(mission_arg)
    
    if mission_path.is_absolute() and mission_path.exists():
        # Absolute path to file
        try:
            mission = load_mission(mission_path)
        except Exception as e:
            print(f"error: Failed to load mission: {e}", file=sys.stderr)
            return 1
    elif (Path("config/missions") / mission_arg).with_suffix(".yaml").exists():
        # Relative path in config/missions
        try:
            mission = load_mission((Path("config/missions") / mission_arg).with_suffix(".yaml"))
        except Exception as e:
            print(f"error: Failed to load mission: {e}", file=sys.stderr)
            return 1
    else:
        # Treat as ephemeral goal
        mission = create_ephemeral_mission(goal=mission_arg)
        print(f"Created ephemeral mission with goal: {mission.goal}")

    # Parse inputs
    inputs = {}
    for input_str in args.input:
        if "=" not in input_str:
            print(f"error: Invalid input format: {input_str} (expected key=value)", file=sys.stderr)
            return 1
        key, value = input_str.split("=", 1)
        inputs[key] = value

    try:
        inputs, skip_mission, info_message = _prepare_agent_inputs(inputs)
    except GitHubIssueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if info_message:
        print(info_message)

    if skip_mission:
        return 0
    
    context = ExecutionContext(inputs=inputs)
    
    # Initialize components
    registry = ToolRegistry()
    
    # Register tools based on mission requirements
    from src.orchestration.toolkit import (
        register_github_mutation_tools,
        register_github_pr_tools,
        register_github_read_only_tools,
        register_parsing_tools,
        register_extraction_tools,
        register_discussion_tools,
        register_setup_tools,
        register_source_curator_tools,
    )
    
    # Register read-only GitHub tools (always safe)
    register_github_read_only_tools(registry)
    
    # Register mutation tools for labeling, commenting, etc.
    register_github_mutation_tools(registry)
    
    # Register other tools as needed
    # TODO: Make this configurable based on mission definition
    register_github_pr_tools(registry)
    register_parsing_tools(registry)
    register_extraction_tools(registry)
    register_discussion_tools(registry)
    register_setup_tools(registry)
    register_source_curator_tools(registry)
    
    # Choose planner based on flag
    planner_type = args.planner
    planner_model = args.model
    
    # If model not specified in args, try to load from config
    if not planner_model:
        from src.config import get_config
        planner_model = get_config().model
        print(f"Using configured model: {planner_model}")

    try:
        models_client = GitHubModelsClient(model=planner_model)
        planner = LLMPlanner(
            models_client=models_client,
            tool_registry=registry,
        )
        planner_model = models_client.model  # Get actual model used
        print(f"Using LLM planner with model: {models_client.model}")
    except Exception as e:
        print(f"error: Failed to initialize LLM planner: {e}", file=sys.stderr)
        print("Tip: Set GITHUB_TOKEN environment variable with GitHub Models API access", file=sys.stderr)
        return 1
    
    validator = SafetyValidator()
    evaluator = _build_mission_evaluator(mission)
    
    print(f"Starting mission: {mission.id}")
    print(f"Goal: {mission.goal}")
    if args.dry_run:
        print("Mode: DRY RUN (no mutations will be executed)")
    if args.interactive:
        print("Mode: INTERACTIVE (approval prompts enabled)")
    print()
    
    # Execute mission
    start_time = time.time()
    runtime = AgentRuntime(
        planner=planner,
        tools=registry,
        safety=validator,
        evaluator=evaluator,
    )
    
    try:
        outcome = runtime.execute_mission(mission, context)
        duration = time.time() - start_time
        
        # Record metrics
        monitor = AgentMonitor(db_path=Path(args.db))
        mission_id = f"{mission.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        monitor.record_mission(
            outcome=outcome,
            mission_id=mission_id,
            mission_type=mission.id,
            duration=duration,
        )
        monitor.close()
        
        # Output results
        print()
        print(f"Mission completed: {outcome.status.value}")
        print(f"Steps executed: {len(outcome.steps)}")
        print(f"Duration: {duration:.2f}s")
        
        # Show step-by-step breakdown
        if outcome.steps:
            print("\nExecution trace:")
            for i, step in enumerate(outcome.steps, 1):
                thought = step.thought
                result = step.result
                print(f"\n  Step {i}:")
                if thought.content:
                    print(f"    Reasoning: {thought.content}")
                if thought.tool_call:
                    print(f"    Tool: {thought.tool_call.name}")
                    print(f"    Arguments: {json.dumps(thought.tool_call.arguments, indent=6)}")
                if result:
                    if result.success:
                        print(f"    Result: ✓ Success")
                        if result.output and not isinstance(result.output, (dict, list)):
                            print(f"    Output: {str(result.output)[:200]}")
                    else:
                        print(f"    Result: ✗ Failed")
                        if result.error:
                            print(f"    Error: {result.error}")
        
        if outcome.summary:
            print(f"\nSummary: {outcome.summary}")
        
        # Write transcript
        # Auto-generate filename in reports/transcripts/tmp/ if not specified
        output_path = args.output
        if not output_path:
            transcript_dir = Path("reports/transcripts/tmp")
            transcript_dir.mkdir(parents=True, exist_ok=True)
            output_path = transcript_dir / f"{mission_id}.json"
        
        transcript = {
            "mission_id": mission_id,
            "mission": {
                "id": mission.id,
                "goal": mission.goal,
            },
            "planner": {
                "type": planner_type,
                "model": planner_model,
            },
            "status": outcome.status.value,
            "duration_seconds": duration,
            "steps": [
                {
                    "thought": step.thought.content,
                    "type": step.thought.type.value,
                    "tool_call": {
                        "name": step.thought.tool_call.name,
                        "arguments": step.thought.tool_call.arguments,
                    } if step.thought.tool_call else None,
                    "result": {
                        "success": step.result.success,
                        "output": step.result.output,
                        "error": step.result.error,
                    } if step.result else None,
                }
                for step in outcome.steps
            ],
            "summary": outcome.summary,
        }
        
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(transcript, f, indent=2)
        print(f"\nTranscript written to: {output_path}")
        
        return 0 if outcome.status == MissionStatus.SUCCEEDED else 1
        
    except Exception as e:
        duration = time.time() - start_time
        print(f"\nerror: Mission failed with exception: {e}", file=sys.stderr)
        
        # Record failure
        monitor = AgentMonitor(db_path=Path(args.db))
        mission_id = f"{mission.id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        from src.orchestration.types import MissionOutcome
        
        failed_outcome = MissionOutcome(
            status=MissionStatus.FAILED,
            steps=[],
            summary=str(e),
        )
        monitor.record_mission(
            outcome=failed_outcome,
            mission_id=mission_id,
            mission_type=mission.id,
            duration=duration,
        )
        monitor.close()
        
        return 1


def list_missions_cli(args: argparse.Namespace) -> int:
    """List available missions.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success)
    """
    missions_path = Path(args.path)
    
    if not missions_path.exists():
        print(f"error: Missions directory not found: {missions_path}", file=sys.stderr)
        return 1
    
    # Find all mission YAML files
    mission_files = list(missions_path.glob("**/*.yaml"))
    
    missions_data = []
    for file_path in sorted(mission_files):
        try:
            mission = load_mission(file_path)  # noqa: S301
            missions_data.append({
                "id": mission.id,
                "path": str(file_path),
                "goal": mission.goal[:80] + "..." if len(mission.goal) > 80 else mission.goal,
                "max_steps": mission.max_steps,
                "requires_approval": mission.requires_approval,
            })
        except Exception:  # noqa: S110
            # Skip invalid mission files
            continue
    
    if args.format == "json":
        print(json.dumps(missions_data, indent=2))
    else:
        # Table format
        if not missions_data:
            print("No missions found")
            return 0
        
        print(f"Found {len(missions_data)} mission(s):\n")
        for mission_data in missions_data:
            print(f"ID: {mission_data['id']}")
            print(f"  Path: {mission_data['path']}")
            print(f"  Goal: {mission_data['goal']}")
            print(f"  Max Steps: {mission_data['max_steps']}")
            print(f"  Requires Approval: {mission_data['requires_approval']}")
            print()
    
    return 0


def status_cli(args: argparse.Namespace) -> int:
    """Check agent health and performance.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success)
    """
    db_path = Path(args.db)
    
    if not db_path.exists():
        print(f"No metrics database found at {db_path}")
        print("Run some missions first to generate metrics.")
        return 0
    
    monitor = AgentMonitor(db_path=db_path)
    health = monitor.check_health(lookback_hours=args.lookback_hours)
    
    if args.format == "json":
        health_data = {
            "status": health.status.value,
            "total_missions": health.total_missions,
            "success_count": health.success_count,
            "failure_count": health.failure_count,
            "blocked_count": health.blocked_count,
            "avg_duration_seconds": health.avg_duration,
            "recent_errors": health.recent_errors,
            "recommendations": health.recommendations,
        }
        print(json.dumps(health_data, indent=2))
    else:
        # Text format
        print(f"Agent Health Status: {health.status.value.upper()}")
        print(f"Analysis Period: Last {args.lookback_hours} hours")
        print()
        print(f"Total Missions: {health.total_missions}")
        print(f"  Succeeded: {health.success_count}")
        print(f"  Failed: {health.failure_count}")
        print(f"  Blocked: {health.blocked_count}")
        print(f"  Success Rate: {health.success_count / health.total_missions * 100:.1f}%"
              if health.total_missions > 0 else "  Success Rate: N/A")
        print()
        print(f"Average Duration: {health.avg_duration:.2f}s")
        print()
        
        if health.recent_errors:
            print("Recent Errors:")
            for error in health.recent_errors:
                print(f"  - {error}")
            print()
        
        print("Recommendations:")
        for rec in health.recommendations:
            print(f"  - {rec}")
    
    monitor.close()
    return 0


def history_cli(args: argparse.Namespace) -> int:
    """View recent mission executions.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success)
    """
    db_path = Path(args.db)
    
    if not db_path.exists():
        print(f"No metrics database found at {db_path}")
        print("Run some missions first to generate history.")
        return 0
    
    monitor = AgentMonitor(db_path=db_path)
    missions = monitor.get_recent_missions(limit=args.limit)
    
    if args.format == "json":
        print(json.dumps(missions, indent=2))
    else:
        # Table format
        if not missions:
            print("No mission history found")
            monitor.close()
            return 0
        
        print(f"Recent {len(missions)} mission(s):\n")
        for mission in missions:
            timestamp = mission["timestamp"]
            print(f"[{timestamp}] {mission['mission_type']}")
            print(f"  ID: {mission['mission_id']}")
            print(f"  Status: {mission['status']}")
            print(f"  Duration: {mission['duration_seconds']:.2f}s")
            print(f"  Steps: {mission['step_count']}, Tool Calls: {mission['tool_call_count']}")
            if mission.get("error_message"):
                print(f"  Error: {mission['error_message']}")
            print()
    
    monitor.close()
    return 0


def explain_cli(args: argparse.Namespace) -> int:
    """Show detailed execution trace for a mission.
    
    Args:
        args: Parsed command-line arguments
        
    Returns:
        Exit code (0 for success, 1 for failure)
    """
    if not args.transcript:
        print("error: --transcript is required for explain command", file=sys.stderr)
        print("Use --output when running missions to save transcripts", file=sys.stderr)
        return 1
    
    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        print(f"error: Transcript file not found: {transcript_path}", file=sys.stderr)
        return 1
    
    try:
        with open(transcript_path, encoding="utf-8") as f:
            transcript = json.load(f)
    except Exception as exc:
        print(f"error: Failed to load transcript: {exc}", file=sys.stderr)
        return 1
    
    # Display detailed trace
    print(f"Mission ID: {transcript['mission_id']}")
    print(f"Mission Type: {transcript['mission']['id']}")
    print(f"Goal: {transcript['mission']['goal']}")
    print(f"Status: {transcript['status']}")
    print(f"Duration: {transcript['duration_seconds']:.2f}s")
    print()
    print("Execution Trace:")
    print("=" * 80)
    
    for i, step in enumerate(transcript['steps'], 1):
        print(f"\nStep {i}:")
        print(f"  Thought: {step['thought']}")
        print(f"  Type: {step['type']}")
        
        if step.get('tool_call'):
            tool_call = step['tool_call']
            print(f"  Tool: {tool_call['name']}")
            print(f"  Arguments: {json.dumps(tool_call['arguments'], indent=4)}")
        
        if step.get('result'):
            result = step['result']
            print(f"  Success: {result['success']}")
            if result.get('error'):
                print(f"  Error: {result['error']}")
            elif result.get('output'):
                output_str = json.dumps(result['output'], indent=4) if isinstance(result['output'], dict) else str(result['output'])
                print(f"  Output: {output_str[:200]}{'...' if len(output_str) > 200 else ''}")
    
    if transcript.get('summary'):
        print()
        print("=" * 80)
        print(f"Summary: {transcript['summary']}")
    
    return 0
