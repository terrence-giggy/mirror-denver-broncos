"""Tests for pipeline configuration."""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.knowledge.pipeline.config import (
    PipelineConfig,
    PipelinePoliteness,
    CHECK_INTERVALS,
    get_check_interval,
)


class TestPipelinePoliteness:
    """Tests for PipelinePoliteness dataclass."""
    
    def test_default_values(self) -> None:
        """Test default politeness values are sensible."""
        politeness = PipelinePoliteness()
        
        assert politeness.min_domain_interval == timedelta(seconds=2)
        assert politeness.max_domain_requests_per_run == 10
        assert politeness.max_sources_per_run == 20
        assert politeness.max_total_requests_per_run == 100
        assert politeness.check_jitter_minutes == 60
        assert politeness.crawler_delay_seconds == 1.0
        assert politeness.respect_robots_crawl_delay is True
    
    def test_custom_values(self) -> None:
        """Test custom politeness values."""
        politeness = PipelinePoliteness(
            min_domain_interval=timedelta(seconds=5),
            max_sources_per_run=10,
            crawler_delay_seconds=2.0,
        )
        
        assert politeness.min_domain_interval == timedelta(seconds=5)
        assert politeness.max_sources_per_run == 10
        assert politeness.crawler_delay_seconds == 2.0
    
    def test_frozen_default_interval(self) -> None:
        """Test that default factory creates independent timedelta."""
        p1 = PipelinePoliteness()
        p2 = PipelinePoliteness()
        
        # They should have equal values
        assert p1.min_domain_interval == p2.min_domain_interval


class TestPipelineConfig:
    """Tests for PipelineConfig dataclass."""
    
    def test_default_values(self) -> None:
        """Test default config values."""
        config = PipelineConfig()
        
        assert config.dry_run is False
        assert config.create_issues is False
        assert config.mode == "full"
        assert config.kb_root is None
        assert config.evidence_root is None
        assert isinstance(config.politeness, PipelinePoliteness)
    
    def test_valid_modes(self) -> None:
        """Test that valid modes are accepted."""
        for mode in ("full", "check", "acquire"):
            config = PipelineConfig(mode=mode)
            assert config.mode == mode
    
    def test_invalid_mode_raises(self) -> None:
        """Test that invalid modes raise ValueError."""
        with pytest.raises(ValueError, match="Invalid mode"):
            PipelineConfig(mode="invalid")
    
    def test_dry_run_mode(self) -> None:
        """Test dry run configuration."""
        config = PipelineConfig(dry_run=True, mode="check")
        
        assert config.dry_run is True
        assert config.mode == "check"


class TestCheckIntervals:
    """Tests for check interval functions."""
    
    def test_known_frequencies(self) -> None:
        """Test intervals for known frequencies."""
        assert get_check_interval("frequent") == timedelta(hours=6)
        assert get_check_interval("daily") == timedelta(hours=24)
        assert get_check_interval("weekly") == timedelta(days=7)
        assert get_check_interval("monthly") == timedelta(days=30)
    
    def test_unknown_frequency(self) -> None:
        """Test fallback for unknown frequency."""
        assert get_check_interval("unknown") == timedelta(days=7)
        assert get_check_interval("garbage") == timedelta(days=7)
    
    def test_none_frequency(self) -> None:
        """Test fallback for None frequency."""
        assert get_check_interval(None) == timedelta(days=7)
    
    def test_check_intervals_dict(self) -> None:
        """Test CHECK_INTERVALS constant has expected keys."""
        expected_keys = {"frequent", "daily", "weekly", "monthly", "unknown"}
        assert set(CHECK_INTERVALS.keys()) == expected_keys
