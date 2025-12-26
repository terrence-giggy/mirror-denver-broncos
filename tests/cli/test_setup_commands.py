"""Unit tests for setup CLI commands."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.cli.commands.setup import validate_setup
from src.integrations.github.discussions import DiscussionCategory


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_token() -> str:
    return "test-token-12345"


@pytest.fixture
def mock_repository() -> str:
    return "test-org/test-repo"


def _mock_all_categories_exist(token, repository, category_name):
    """Mock helper that returns a category for all required categories."""
    return DiscussionCategory(
        id=f"CAT_{category_name}",
        name=category_name,
        slug=category_name.lower(),
        is_answerable=False
    )


def _mock_all_labels_exist():
    """Mock helper that returns all required labels."""
    return [
        {"name": "setup", "color": "0e8a16", "description": ""},
        {"name": "question", "color": "d876e3", "description": ""},
        {"name": "source-approved", "color": "0052cc", "description": ""},
        {"name": "source-proposal", "color": "fbca04", "description": ""},
        {"name": "wontfix", "color": "ffffff", "description": ""},
    ]


# =============================================================================
# Tests for validate_setup
# =============================================================================


class TestValidateSetup:
    """Tests for repository setup validation."""
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_fully_valid_setup(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should pass all checks for properly configured repo."""
        # Mock UPSTREAM_REPO variable
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock repository details
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream", "research"],
            "template_repository": {
                "full_name": "org/upstream-repo"
            }
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert result["valid"] is True
        assert len(result["issues"]) == 0
        assert len(result["warnings"]) == 0
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_missing_upstream_repo_var(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should detect missing UPSTREAM_REPO variable."""
        # Mock missing variable
        mock_get_var.return_value = None
        
        # Mock repository details
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert result["valid"] is False
        assert any("UPSTREAM_REPO" in issue for issue in result["issues"])
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_fork_rejected(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should reject repository that is a fork."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock fork repository
        mock_get_details.return_value = {
            "fork": True,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert result["valid"] is False
        assert any("fork" in issue.lower() for issue in result["issues"])
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_missing_topic_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn about missing speculum-downstream topic."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock repository without required topic
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["other-topic"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        assert any("topic" in warning.lower() for warning in result["warnings"])
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_no_template_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn if repo not created from template."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock repository without template
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": None
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        assert any("template" in warning.lower() for warning in result["warnings"])
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_template_mismatch_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn if template doesn't match UPSTREAM_REPO."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock repository with different template
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {
                "full_name": "org/different-template"
            }
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        assert any("differs" in warning.lower() for warning in result["warnings"])
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_api_error_handling(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should handle API errors gracefully."""
        # Mock API error
        mock_get_var.side_effect = Exception("API Error")
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert result["valid"] is False
        assert any("Could not verify" in issue for issue in result["issues"])
    
    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_multiple_issues(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should detect multiple configuration issues."""
        # Mock missing variable
        mock_get_var.return_value = None
        
        # Mock fork without topic
        mock_get_details.return_value = {
            "fork": True,
            "topics": [],
            "template_repository": None
        }
        
        # Mock Sources category missing
        mock_discussions.get_category_by_name.return_value = None
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert result["valid"] is False
        assert len(result["issues"]) >= 2  # UPSTREAM_REPO + fork
        assert len(result["warnings"]) >= 2  # missing topic + missing Sources category

    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_missing_sources_category_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn if required discussion categories are missing."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock valid repository
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all categories missing
        mock_discussions.get_category_by_name.return_value = None
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        # Should warn about missing categories
        category_warning = next(
            (w for w in result["warnings"] if "Missing discussion categories" in w), None
        )
        assert category_warning is not None
        assert "Sources" in category_warning
        assert "People" in category_warning
        assert "Organizations" in category_warning

    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_partial_categories_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should only warn about specific missing categories."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock valid repository
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock only Sources category exists
        def mock_get_category(token, repository, category_name):
            if category_name == "Sources":
                return DiscussionCategory(
                    id="CAT123", name="Sources", slug="sources", is_answerable=False
                )
            return None
        
        mock_discussions.get_category_by_name.side_effect = mock_get_category
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        category_warning = next(
            (w for w in result["warnings"] if "Missing discussion categories" in w), None
        )
        assert category_warning is not None
        assert "Sources" not in category_warning  # Sources exists
        assert "People" in category_warning  # People missing
        assert "Organizations" in category_warning  # Organizations missing

    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_all_categories_exist(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should not warn when all required categories exist."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock valid repository
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all categories exist
        def mock_get_category(token, repository, category_name):
            return DiscussionCategory(
                id=f"CAT_{category_name}", 
                name=category_name, 
                slug=category_name.lower(), 
                is_answerable=False
            )
        
        mock_discussions.get_category_by_name.side_effect = mock_get_category
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        # Should have no category-related warnings
        category_warnings = [w for w in result["warnings"] if "categories" in w.lower()]
        assert len(category_warnings) == 0

    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_discussions_api_error_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn if discussion category check fails (e.g., Discussions not enabled)."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock valid repository
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock Discussions API error (e.g., Discussions not enabled)
        mock_discussions.get_category_by_name.side_effect = Exception("Discussions not enabled")
        
        # Mock all labels exist
        mock_get_labels.return_value = _mock_all_labels_exist()
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        assert any("discussion categories" in warning.lower() for warning in result["warnings"])

    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_missing_labels_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn if required labels are missing."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock valid repository
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock only some labels exist (missing source-approved and source-proposal)
        mock_get_labels.return_value = [
            {"name": "setup", "color": "0e8a16", "description": ""},
            {"name": "question", "color": "d876e3", "description": ""},
            {"name": "wontfix", "color": "ffffff", "description": ""},
        ]
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        label_warning = next(
            (w for w in result["warnings"] if "Missing labels" in w), None
        )
        assert label_warning is not None
        assert "source-approved" in label_warning
        assert "source-proposal" in label_warning

    @patch('src.cli.commands.setup.get_repository_labels')
    @patch('src.cli.commands.setup.github_discussions')
    @patch('src.cli.commands.setup.get_repository_details')
    @patch('src.cli.commands.setup.get_repository_variable')
    @patch('builtins.print')
    def test_labels_api_error_warning(
        self, mock_print, mock_get_var, mock_get_details, mock_discussions, mock_get_labels, mock_repository, mock_token
    ):
        """Should warn if labels check fails."""
        mock_get_var.return_value = "org/upstream-repo"
        
        # Mock valid repository
        mock_get_details.return_value = {
            "fork": False,
            "topics": ["speculum-downstream"],
            "template_repository": {"full_name": "org/upstream-repo"}
        }
        
        # Mock all discussion categories exist
        mock_discussions.get_category_by_name.side_effect = _mock_all_categories_exist
        
        # Mock labels API error
        mock_get_labels.side_effect = Exception("API Error")
        
        result = validate_setup(mock_repository, mock_token)
        
        assert len(result["warnings"]) > 0
        assert any("labels" in warning.lower() for warning in result["warnings"])
