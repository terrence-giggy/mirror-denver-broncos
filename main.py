#!/usr/bin/env python3

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

# Load environment variables from .env file if it exists
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    load_dotenv(_env_file)

from src.cli.commands.agent import (
    register_commands as register_agent_commands,
)
from src.cli.commands.discussions import (
    register_commands as register_discussion_commands,
)
from src.cli.commands.extraction import (
    register_commands as register_extraction_commands,
)
from src.cli.commands.github import (
    register_commands as register_github_commands,
)
from src.cli.commands.parse import (
    register_commands as register_parse_commands,
)
from src.cli.commands.pipeline import (
    register_commands as register_pipeline_commands,
)
from src.cli.commands.setup import (
    register_commands as register_setup_commands,
)
from src.cli.commands.sources import (
    register_commands as register_source_commands,
)
from src.cli.commands.sync import (
    register_commands as register_sync_commands,
)


def _build_command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m main",
        description=(
            "Automation entry point for GitHub issue workflows and agent orchestration."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True
    register_github_commands(subparsers)
    register_agent_commands(subparsers)
    register_parse_commands(subparsers)
    register_extraction_commands(subparsers)
    register_discussion_commands(subparsers)
    register_setup_commands(subparsers)
    register_source_commands(subparsers)
    register_sync_commands(subparsers)
    register_pipeline_commands(subparsers)
    return parser


def _dispatch(args: argparse.Namespace) -> int:
    handler = getattr(args, "func", None)
    if handler is None:  # pragma: no cover - defensive guard
        raise ValueError("No handler registered for parsed arguments.")
    return handler(args)


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)

    command_parser = _build_command_parser()
    args = command_parser.parse_args(raw_args)
    return _dispatch(args)


if __name__ == "__main__":
    raise SystemExit(main())
