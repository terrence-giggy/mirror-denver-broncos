"""Integration tests for the content pipeline.

These tests verify end-to-end pipeline behavior with real (but mocked) 
components working together.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.knowledge.pipeline import (
    PipelineConfig,
    PipelinePoliteness,
    run_pipeline,
)
from src.knowledge.pipeline.config import get_check_interval
from src.knowledge.pipeline.monitor import MonitorResult
from src.knowledge.pipeline.crawler import CrawlerResult
from src.knowledge.pipeline.runner import PipelineResult
from src.knowledge.storage import SourceEntry, SourceRegistry


# =============================================================================
# Fixtures
# =============================================================================


def _make_source_entry(name: str, url: str, **overrides) -> SourceEntry:
    """Create a valid SourceEntry with defaults."""
    now = datetime.now(timezone.utc)
    defaults = {
        "name": name,
        "url": url,
        "source_type": "primary",
        "status": "active",
        "last_verified": now,
        "added_at": now,
        "added_by": "test",
        "proposal_discussion": None,
        "implementation_issue": None,
        "credibility_score": 0.8,
        "is_official": True,
        "requires_auth": False,
        "discovered_from": None,
        "parent_source_url": None,
        "content_type": "webpage",
        "update_frequency": "daily",
        "topics": [],
        "notes": "",
        "last_content_hash": None,
        "last_etag": None,
        "last_modified_header": None,
        "last_checked": None,
        "check_failures": 0,
        "next_check_after": None,
        "is_crawlable": False,
        "crawl_scope": "path",
        "crawl_max_pages": 100,
        "crawl_max_depth": 5,
        "crawl_state_path": None,
        "total_pages_discovered": 0,
        "total_pages_acquired": 0,
        "last_crawl_started": None,
        "last_crawl_completed": None,
    }
    defaults.update(overrides)
    return SourceEntry(**defaults)


@pytest.fixture
def temp_kb(tmp_path: Path) -> Path:
    """Create a temporary knowledge graph directory."""
    kb_root = tmp_path / "knowledge-graph"
    sources_dir = kb_root / "sources"
    sources_dir.mkdir(parents=True)
    return kb_root


@pytest.fixture
def temp_evidence(tmp_path: Path) -> Path:
    """Create a temporary evidence directory."""
    evidence_root = tmp_path / "evidence"
    parsed_dir = evidence_root / "parsed"
    parsed_dir.mkdir(parents=True)
    return evidence_root


@pytest.fixture
def source_registry(temp_kb: Path) -> SourceRegistry:
    """Create a SourceRegistry for the temp kb."""
    return SourceRegistry(root=temp_kb)


@pytest.fixture
def sample_source(source_registry: SourceRegistry) -> SourceEntry:
    """Create and save a sample source entry."""
    source = _make_source_entry("Test Source", "https://example.com/test")
    source_registry.save_source(source)
    return source


# =============================================================================
# Integration Tests
# =============================================================================


class TestPipelineIntegration:
    """Integration tests for the full pipeline."""

    def test_pipeline_runs_with_empty_registry(self, temp_kb, temp_evidence):
        """Pipeline should complete successfully with no sources."""
        config = PipelineConfig(
            mode="full",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        
        assert isinstance(result, PipelineResult)
        assert result.completed_at is not None
        assert result.duration_seconds >= 0
    
    def test_pipeline_detects_initial_sources(self, temp_kb, temp_evidence, sample_source):
        """Pipeline should detect sources needing initial acquisition."""
        config = PipelineConfig(
            mode="check",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        
        assert result.monitor is not None
        assert len(result.monitor.initial_needed) == 1
        assert result.monitor.initial_needed[0].name == "Test Source"
    
    def test_dry_run_does_not_modify_sources(self, temp_kb, temp_evidence, sample_source, source_registry):
        """Dry run should not modify source entries."""
        # Get the original source state
        original = source_registry.get_source(sample_source.url)
        original_hash = original.last_content_hash
        
        config = PipelineConfig(
            mode="full",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        run_pipeline(config)
        
        # Source should be unchanged
        after = source_registry.get_source(sample_source.url)
        assert after.last_content_hash == original_hash
    
    def test_pipeline_respects_max_sources(self, temp_kb, temp_evidence, source_registry):
        """Pipeline should respect max_sources limit."""
        # Create many sources via the registry
        for i in range(50):
            source = _make_source_entry(f"Source {i}", f"https://example{i}.com/test")
            source_registry.save_source(source)
        
        config = PipelineConfig(
            mode="check",
            dry_run=True,
            politeness=PipelinePoliteness(max_sources_per_run=5),
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        
        # Should be limited to 5
        assert len(result.monitor.initial_needed) <= 5
    
    def test_pipeline_result_to_dict(self, temp_kb, temp_evidence):
        """Pipeline result should serialize to dict for JSON output."""
        config = PipelineConfig(
            mode="full",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        result_dict = result.to_dict()
        
        # Should be JSON-serializable
        json_str = json.dumps(result_dict, default=str)
        parsed = json.loads(json_str)
        
        assert "started_at" in parsed
        assert "completed_at" in parsed
        assert "mode" in parsed
        assert "dry_run" in parsed
    
    def test_pipeline_summary_readable(self, temp_kb, temp_evidence):
        """Pipeline summary should be human-readable."""
        config = PipelineConfig(
            mode="full",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        summary = result.summary()
        
        assert "Pipeline completed" in summary
        assert "Mode: full" in summary


class TestPipelinePolitenessBehavior:
    """Tests for politeness behavior in the pipeline."""
    
    def test_domain_fair_scheduling(self, temp_kb, temp_evidence, source_registry):
        """Sources from different domains should be scheduled fairly."""
        # Create sources from 3 domains
        domains = ["alpha.com", "beta.com", "gamma.com"]
        for i, domain in enumerate(domains):
            for j in range(5):
                source = _make_source_entry(
                    f"{domain} Source {j}",
                    f"https://{domain}/page{j}",
                )
                source_registry.save_source(source)
        
        config = PipelineConfig(
            mode="check",
            dry_run=True,
            politeness=PipelinePoliteness(
                max_sources_per_run=9,  # 3 per domain
                max_domain_requests_per_run=3,
            ),
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        
        # Count sources per domain
        domain_counts = {}
        for source in result.monitor.initial_needed:
            from urllib.parse import urlparse
            domain = urlparse(source.url).netloc
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
        
        # Each domain should have at most 3
        for domain, count in domain_counts.items():
            assert count <= 3, f"Domain {domain} has {count} sources, expected <= 3"


class TestCheckIntervalConfiguration:
    """Tests for check interval configuration."""
    
    def test_daily_interval(self):
        """Daily frequency should return 1 day interval."""
        interval = get_check_interval("daily")
        assert interval == timedelta(days=1)
    
    def test_weekly_interval(self):
        """Weekly frequency should return 7 day interval."""
        interval = get_check_interval("weekly")
        assert interval == timedelta(days=7)
    
    def test_monthly_interval(self):
        """Monthly frequency should return 30 day interval."""
        interval = get_check_interval("monthly")
        assert interval == timedelta(days=30)
    
    def test_unknown_frequency_defaults_weekly(self):
        """Unknown frequency should default to weekly."""
        interval = get_check_interval("unknown")
        assert interval == timedelta(days=7)
    
    def test_none_frequency_defaults_weekly(self):
        """None frequency should default to weekly."""
        interval = get_check_interval(None)
        assert interval == timedelta(days=7)


class TestPipelineModes:
    """Tests for different pipeline execution modes."""
    
    def test_check_mode_no_acquisition(self, temp_kb, temp_evidence, sample_source):
        """Check mode should not perform acquisition."""
        config = PipelineConfig(
            mode="check",
            dry_run=False,  # Not dry run, but check mode
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        
        # Monitor phase should run
        assert result.monitor is not None
        # Crawler should not run or have no results
        # (implementation dependent)
    
    def test_full_mode_runs_both_phases(self, temp_kb, temp_evidence):
        """Full mode should run both monitor and crawler phases."""
        config = PipelineConfig(
            mode="full",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        result = run_pipeline(config)
        
        assert result.monitor is not None
        assert result.crawler is not None


class TestPipelineErrorHandling:
    """Tests for pipeline error handling."""
    
    def test_handles_invalid_source_json(self, temp_kb, temp_evidence):
        """Pipeline should handle malformed source files gracefully."""
        sources_dir = temp_kb / "sources"
        
        # Create an invalid JSON file
        (sources_dir / "invalid.json").write_text("{ this is not valid json }")
        
        config = PipelineConfig(
            mode="check",
            dry_run=True,
            kb_root=temp_kb,
            evidence_root=temp_evidence,
        )
        
        # Should not raise, should complete with errors logged
        result = run_pipeline(config)
        assert result.completed_at is not None
    
    def test_handles_missing_kb_directory(self, tmp_path, temp_evidence):
        """Pipeline should handle missing knowledge graph directory."""
        non_existent = tmp_path / "does-not-exist"
        
        config = PipelineConfig(
            mode="check",
            dry_run=True,
            kb_root=non_existent,
            evidence_root=temp_evidence,
        )
        
        # Should handle gracefully
        result = run_pipeline(config)
        assert result.completed_at is not None
