"""Convenience helpers for registering orchestration tools."""

from __future__ import annotations

from .github import (
    register_github_mutation_tools,
    register_github_pr_tools,
    register_github_read_only_tools,
)
from .parsing import register_parsing_tools
from .extraction import register_extraction_tools
from .discussion_tools import register_all_discussion_tools as register_discussion_tools
from .setup import register_setup_tools
from .source_curator import register_source_curator_tools

__all__ = [
	"register_github_read_only_tools",
	"register_github_pr_tools",
	"register_github_mutation_tools",
	"register_parsing_tools",
    "register_extraction_tools",
    "register_discussion_tools",
    "register_setup_tools",
    "register_source_curator_tools",
]
