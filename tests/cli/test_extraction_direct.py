"""Tests for extraction-direct command."""

import pytest
from unittest.mock import Mock, patch, MagicMock

from src.cli.commands.extraction_direct import (
    extract_directly,
    _parse_checksum_from_issue_body,
    _format_extraction_stats,
)


class TestParseChecksumFromIssueBody:
    """Test checksum parsing from issue body."""

    def test_parse_checksum_with_marker(self):
        """Should extract checksum from HTML comment marker."""
        body = """
Some text
<!-- checksum:abc123def456 -->
More text
"""
        result = _parse_checksum_from_issue_body(body)
        assert result == "abc123def456"

    def test_parse_checksum_no_marker(self):
        """Should return None if no marker found."""
        body = "Some text without a checksum marker"
        result = _parse_checksum_from_issue_body(body)
        assert result is None

    def test_parse_checksum_none_body(self):
        """Should return None for None body."""
        result = _parse_checksum_from_issue_body(None)
        assert result is None


class TestFormatExtractionStats:
    """Test extraction statistics formatting."""

    def test_format_with_all_counts(self):
        """Should format all entity counts."""
        results = {
            "people": {"status": "success", "extracted_count": 5},
            "organizations": {"status": "success", "extracted_count": 3},
            "concepts": {"status": "success", "extracted_count": 24},
            "associations": {"status": "success", "extracted_count": 7},
        }
        formatted = _format_extraction_stats(results)
        
        assert "5" in formatted  # people count
        assert "3" in formatted  # org count
        assert "24" in formatted  # concept count
        assert "7" in formatted  # association count
        assert "39" in formatted  # total count (5+3+24+7)

    def test_format_with_zero_counts(self):
        """Should handle zero counts."""
        results = {
            "people": {"status": "success", "extracted_count": 0},
            "organizations": {"status": "success", "extracted_count": 0},
            "concepts": {"status": "success", "extracted_count": 0},
            "associations": {"status": "success", "extracted_count": 0},
        }
        formatted = _format_extraction_stats(results)
        
        assert "**People:** 0" in formatted
        assert "**Total Entities:** 0" in formatted


class TestExtractDirectly:
    """Test the main extraction function."""

    @patch("src.cli.commands.extraction_direct.fetch_issue")
    @patch("src.cli.commands.extraction_direct.ExtractionToolkit")
    @patch("src.cli.commands.extraction_direct.post_comment")
    @patch("src.cli.commands.extraction_direct.add_labels")
    @patch("src.cli.commands.extraction_direct.remove_label")
    @patch("src.cli.commands.extraction_direct.update_issue")
    def test_pr_creation_error_fails_job(
        self,
        mock_update,
        mock_remove_label,
        mock_add_labels,
        mock_post_comment,
        mock_toolkit_class,
        mock_fetch_issue,
    ):
        """Should fail the job (return 1) if PR creation fails with error status."""
        # Setup mocks
        mock_fetch_issue.return_value = {
            "body": "<!-- checksum:abc123 -->\nTest issue",
        }
        
        # Mock toolkit instance and its methods
        mock_toolkit = MagicMock()
        mock_toolkit_class.return_value = mock_toolkit
        
        # Assessment returns substantive
        mock_toolkit._assess_document.return_value = {
            "status": "success",
            "is_substantive": True,
            "reason": "Contains valuable content",
            "confidence": 0.9,
        }
        
        # All extractions succeed
        mock_toolkit._extract_people.return_value = {
            "status": "success",
            "extracted_count": 5,
        }
        mock_toolkit._extract_organizations.return_value = {
            "status": "success",
            "extracted_count": 3,
        }
        mock_toolkit._extract_concepts.return_value = {
            "status": "success",
            "extracted_count": 10,
        }
        mock_toolkit._extract_associations.return_value = {
            "status": "success",
            "extracted_count": 2,
        }
        
        # Mark complete succeeds
        mock_toolkit._mark_complete.return_value = {
            "status": "success",
        }
        
        # PR creation FAILS
        mock_toolkit._create_pr.return_value = {
            "status": "error",
            "message": "Failed to create PR: resolve_token() missing 1 required positional argument: 'explicit_token'",
        }
        
        # Run extraction
        result = extract_directly(
            issue_number=123,
            repository="owner/repo",
            token="test_token",
        )
        
        # Should return 1 (failure)
        assert result == 1
        
        # Should post error comment
        mock_post_comment.assert_called()
        error_call = [call for call in mock_post_comment.call_args_list if "Failed to create pull request" in str(call)]
        assert len(error_call) > 0
        
        # Should add extraction-error label
        error_label_calls = [
            call for call in mock_add_labels.call_args_list 
            if "extraction-error" in str(call)
        ]
        assert len(error_label_calls) > 0

    @patch("src.cli.commands.extraction_direct.fetch_issue")
    @patch("src.cli.commands.extraction_direct.ExtractionToolkit")
    @patch("src.cli.commands.extraction_direct.post_comment")
    @patch("src.cli.commands.extraction_direct.add_labels")
    @patch("src.cli.commands.extraction_direct.remove_label")
    @patch("src.cli.commands.extraction_direct.update_issue")
    def test_pr_creation_skip_succeeds(
        self,
        mock_update,
        mock_remove_label,
        mock_add_labels,
        mock_post_comment,
        mock_toolkit_class,
        mock_fetch_issue,
    ):
        """Should succeed (return 0) if PR creation is skipped (local mode)."""
        # Setup mocks
        mock_fetch_issue.return_value = {
            "body": "<!-- checksum:abc123 -->\nTest issue",
        }
        
        mock_toolkit = MagicMock()
        mock_toolkit_class.return_value = mock_toolkit
        
        mock_toolkit._assess_document.return_value = {
            "status": "success",
            "is_substantive": True,
            "reason": "Contains valuable content",
            "confidence": 0.9,
        }
        
        mock_toolkit._extract_people.return_value = {"status": "success", "extracted_count": 5}
        mock_toolkit._extract_organizations.return_value = {"status": "success", "extracted_count": 3}
        mock_toolkit._extract_concepts.return_value = {"status": "success", "extracted_count": 10}
        mock_toolkit._extract_associations.return_value = {"status": "success", "extracted_count": 2}
        mock_toolkit._mark_complete.return_value = {"status": "success"}
        
        # PR creation SKIPPED (not in GitHub Actions)
        mock_toolkit._create_pr.return_value = {
            "status": "skip",
            "message": "Not running in GitHub Actions - changes saved locally only",
        }
        
        # Run extraction
        result = extract_directly(
            issue_number=123,
            repository="owner/repo",
            token="test_token",
        )
        
        # Should return 0 (success)
        assert result == 0
        
        # Should add extraction-complete label (not error)
        complete_label_calls = [
            call for call in mock_add_labels.call_args_list 
            if "extraction-complete" in str(call)
        ]
        assert len(complete_label_calls) > 0
        
        # Should close issue
        mock_update.assert_called_once_with(
            token="test_token",
            repository="owner/repo",
            issue_number=123,
            state="closed",
        )

    @patch("src.cli.commands.extraction_direct.fetch_issue")
    @patch("src.cli.commands.extraction_direct.ExtractionToolkit")
    @patch("src.cli.commands.extraction_direct.post_comment")
    @patch("src.cli.commands.extraction_direct.add_labels")
    @patch("src.cli.commands.extraction_direct.remove_label")
    @patch("src.cli.commands.extraction_direct.update_issue")
    def test_pr_creation_success(
        self,
        mock_update,
        mock_remove_label,
        mock_add_labels,
        mock_post_comment,
        mock_toolkit_class,
        mock_fetch_issue,
    ):
        """Should succeed (return 0) if PR is created successfully."""
        # Setup mocks
        mock_fetch_issue.return_value = {
            "body": "<!-- checksum:abc123 -->\nTest issue",
        }
        
        mock_toolkit = MagicMock()
        mock_toolkit_class.return_value = mock_toolkit
        
        mock_toolkit._assess_document.return_value = {
            "status": "success",
            "is_substantive": True,
            "reason": "Contains valuable content",
            "confidence": 0.9,
        }
        
        mock_toolkit._extract_people.return_value = {"status": "success", "extracted_count": 5}
        mock_toolkit._extract_organizations.return_value = {"status": "success", "extracted_count": 3}
        mock_toolkit._extract_concepts.return_value = {"status": "success", "extracted_count": 10}
        mock_toolkit._extract_associations.return_value = {"status": "success", "extracted_count": 2}
        mock_toolkit._mark_complete.return_value = {"status": "success"}
        
        # PR creation SUCCESS
        mock_toolkit._create_pr.return_value = {
            "status": "success",
            "pr_number": 456,
            "pr_url": "https://github.com/owner/repo/pull/456",
        }
        
        # Run extraction
        result = extract_directly(
            issue_number=123,
            repository="owner/repo",
            token="test_token",
        )
        
        # Should return 0 (success)
        assert result == 0
        
        # Should post completion comment with PR link
        mock_post_comment.assert_called()
        comment_call = mock_post_comment.call_args_list[-1]  # Last comment
        comment_body = comment_call[1]["body"]
        assert "#456" in comment_body or "456" in comment_body
        
        # Should close issue
        mock_update.assert_called_once_with(
            token="test_token",
            repository="owner/repo",
            issue_number=123,
            state="closed",
        )
