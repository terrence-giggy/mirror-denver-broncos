"""Content pipeline for unified source monitoring and acquisition.

This module provides a programmatic, LLM-free implementation of the content
pipeline that combines:
1. Source monitoring (change detection)
2. Content acquisition (crawling/fetching)

The pipeline enforces politeness constraints including domain-aware scheduling,
rate limiting, and staggered execution across workflow runs.

Usage:
    from src.knowledge.pipeline import run_pipeline, PipelineConfig
    
    config = PipelineConfig(max_sources_per_run=20)
    result = run_pipeline(config)
"""

from .config import PipelineConfig, PipelinePoliteness
from .runner import run_pipeline, PipelineResult
from .scheduler import DomainScheduler, ScheduledSource

__all__ = [
    # Config
    "PipelineConfig",
    "PipelinePoliteness",
    # Runner
    "run_pipeline",
    "PipelineResult",
    # Scheduler
    "DomainScheduler",
    "ScheduledSource",
]
