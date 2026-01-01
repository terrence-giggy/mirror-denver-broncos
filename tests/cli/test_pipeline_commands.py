"""Unit tests for pipeline CLI commands."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from src.cli.commands.pipeline import (
    register_commands,
    pipeline_run_cli,
    pipeline_check_cli,
    pipeline_acquire_cli,
    pipeline_status_cli,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def parser() -> argparse.ArgumentParser:
    """Create a parser with pipeline commands registered."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    register_commands(subparsers)
    return parser


@pytest.fixture
def mock_pipeline_result():
    """Create a mock PipelineResult."""
    from src.knowledge.pipeline.runner import PipelineResult
    from src.knowledge.pipeline.monitor import MonitorResult
    from src.knowledge.pipeline.crawler import CrawlerResult
    
    result = PipelineResult(mode="full", dry_run=True)
    result.monitor = MonitorResult(sources_checked=5)
    result.crawler = CrawlerResult(sources_processed=3, pages_total=10)
    return result


# =============================================================================
# Tests for command registration
# =============================================================================


class TestRegisterCommands:
    """Tests for command registration."""
    
    def test_pipeline_command_registered(self, parser):
        """Pipeline command should be registered."""
        args = parser.parse_args(["pipeline", "run", "--dry-run"])
        assert args.command == "pipeline"
        assert args.pipeline_command == "run"
        assert args.dry_run is True
    
    def test_run_subcommand(self, parser):
        """Run subcommand should have expected args."""
        args = parser.parse_args([
            "pipeline", "run",
            "--max-sources", "10",
            "--max-per-domain", "2",
            "--min-interval", "3.0",
            "--dry-run",
        ])
        assert args.max_sources == 10
        assert args.max_per_domain == 2
        assert args.min_interval == 3.0
        assert args.dry_run is True
    
    def test_check_subcommand(self, parser):
        """Check subcommand should have expected args."""
        args = parser.parse_args([
            "pipeline", "check",
            "--max-sources", "50",
            "--dry-run",
        ])
        assert args.pipeline_command == "check"
        assert args.max_sources == 50
    
    def test_acquire_subcommand(self, parser):
        """Acquire subcommand should have expected args."""
        args = parser.parse_args([
            "pipeline", "acquire",
            "--source-url", "https://example.com",
        ])
        assert args.pipeline_command == "acquire"
        assert args.source_url == "https://example.com"
    
    def test_status_subcommand(self, parser):
        """Status subcommand should have expected args."""
        args = parser.parse_args([
            "pipeline", "status",
            "--due-only",
        ])
        assert args.pipeline_command == "status"
        assert args.due_only is True
    
    def test_json_output_flag(self, parser):
        """All subcommands should support --json."""
        for subcmd in ["run", "check", "acquire", "status"]:
            args = parser.parse_args(["pipeline", subcmd, "--json"])
            assert args.output_json is True


# =============================================================================
# Tests for pipeline run command
# =============================================================================


class TestPipelineRunCli:
    """Tests for pipeline run CLI handler."""
    
    def test_returns_zero_on_success(self, mock_pipeline_result, tmp_path):
        """Should return 0 on successful run."""
        args = argparse.Namespace(
            dry_run=True,
            output_json=False,
            max_sources=20,
            max_per_domain=3,
            min_interval=5.0,
            force_fresh=False,
            no_crawl=False,
            max_pages_per_crawl=100,
            kb_root=tmp_path / "kb",
            evidence_root=tmp_path / "evidence",
        )
        
        with patch("src.knowledge.pipeline.run_pipeline") as mock_run:
            mock_run.return_value = mock_pipeline_result
            
            result = pipeline_run_cli(args)
        
        assert result == 0
        mock_run.assert_called_once()
    
    def test_returns_one_on_errors(self, tmp_path):
        """Should return 1 when errors occur."""
        from src.knowledge.pipeline.runner import PipelineResult
        from src.knowledge.pipeline.monitor import MonitorResult
        
        error_result = PipelineResult(mode="full")
        error_result.monitor = MonitorResult()
        error_result.monitor.errors.append((MagicMock(name="test"), "Connection failed"))
        
        args = argparse.Namespace(
            dry_run=True,
            output_json=False,
            max_sources=20,
            max_per_domain=3,
            min_interval=5.0,
            force_fresh=False,
            no_crawl=False,
            max_pages_per_crawl=100,
            kb_root=tmp_path / "kb",
            evidence_root=tmp_path / "evidence",
        )
        
        with patch("src.knowledge.pipeline.run_pipeline") as mock_run:
            mock_run.return_value = error_result
            
            result = pipeline_run_cli(args)
        
        assert result == 1
    
    def test_json_output(self, mock_pipeline_result, tmp_path, capsys):
        """Should output valid JSON with --json flag."""
        args = argparse.Namespace(
            dry_run=True,
            output_json=True,
            max_sources=20,
            max_per_domain=3,
            min_interval=5.0,
            force_fresh=False,
            no_crawl=False,
            max_pages_per_crawl=100,
            kb_root=tmp_path / "kb",
            evidence_root=tmp_path / "evidence",
        )
        
        with patch("src.knowledge.pipeline.run_pipeline") as mock_run:
            mock_run.return_value = mock_pipeline_result
            
            pipeline_run_cli(args)
        
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        
        assert "mode" in output
        assert output["dry_run"] is True


# =============================================================================
# Tests for pipeline check command
# =============================================================================


class TestPipelineCheckCli:
    """Tests for pipeline check CLI handler."""
    
    def test_check_mode_is_set(self, mock_pipeline_result, tmp_path):
        """Should configure mode=check."""
        args = argparse.Namespace(
            dry_run=False,
            output_json=False,
            max_sources=50,
            max_per_domain=5,
            kb_root=tmp_path / "kb",
            evidence_root=tmp_path / "evidence",
        )
        
        with patch("src.knowledge.pipeline.run_pipeline") as mock_run:
            mock_run.return_value = mock_pipeline_result
            
            pipeline_check_cli(args)
        
        # Verify the config passed to run_pipeline
        call_args = mock_run.call_args[0][0]
        assert call_args.mode == "check"


# =============================================================================
# Tests for pipeline acquire command
# =============================================================================


class TestPipelineAcquireCli:
    """Tests for pipeline acquire CLI handler."""
    
    def test_acquire_mode_is_set(self, mock_pipeline_result, tmp_path):
        """Should configure mode=acquire."""
        args = argparse.Namespace(
            dry_run=False,
            output_json=False,
            max_sources=10,
            source_url=None,
            force_fresh=False,
            no_crawl=False,
            max_pages_per_crawl=100,
            kb_root=tmp_path / "kb",
            evidence_root=tmp_path / "evidence",
        )
        
        with patch("src.knowledge.pipeline.run_pipeline") as mock_run:
            mock_run.return_value = mock_pipeline_result
            
            pipeline_acquire_cli(args)
        
        call_args = mock_run.call_args[0][0]
        assert call_args.mode == "acquire"


# =============================================================================
# Tests for pipeline status command
# =============================================================================


class TestPipelineStatusCli:
    """Tests for pipeline status CLI handler."""
    
    def test_status_with_no_sources(self, tmp_path, capsys):
        """Should handle empty registry gracefully."""
        # Create empty kb directory
        kb_path = tmp_path / "kb"
        kb_path.mkdir(parents=True)
        
        args = argparse.Namespace(
            output_json=False,
            kb_root=kb_path,
            due_only=False,
            pending_only=False,
        )
        
        result = pipeline_status_cli(args)
        
        assert result == 0
        captured = capsys.readouterr()
        assert "Total active sources: 0" in captured.out
    
    def test_status_json_output(self, tmp_path, capsys):
        """Should output valid JSON."""
        kb_path = tmp_path / "kb"
        kb_path.mkdir(parents=True)
        
        args = argparse.Namespace(
            output_json=True,
            kb_root=kb_path,
            due_only=False,
            pending_only=False,
        )
        
        pipeline_status_cli(args)
        
        captured = capsys.readouterr()
        output = json.loads(captured.out)
        
        assert "timestamp" in output
        assert "total_active_sources" in output
        assert "sources" in output


# =============================================================================
# Integration-style tests
# =============================================================================


class TestPipelineCliIntegration:
    """Integration tests for the pipeline CLI."""
    
    def test_full_command_parsing(self, parser):
        """Should parse a complete command line."""
        args = parser.parse_args([
            "pipeline", "run",
            "--dry-run",
            "--max-sources", "15",
            "--max-per-domain", "2",
            "--min-interval", "10",
            "--json",
        ])
        
        assert args.command == "pipeline"
        assert args.pipeline_command == "run"
        assert args.dry_run is True
        assert args.max_sources == 15
        assert args.max_per_domain == 2
        assert args.min_interval == 10.0
        assert args.output_json is True
    
    def test_defaults_are_sensible(self, parser):
        """Default values should be reasonable."""
        args = parser.parse_args(["pipeline", "run"])
        
        assert args.max_sources == 20
        assert args.max_per_domain == 3
        assert args.min_interval == 5.0
        assert args.dry_run is False
