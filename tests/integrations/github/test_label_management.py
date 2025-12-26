"""Unit tests for repository label management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.integrations.github.issues import (
    REQUIRED_LABELS,
    LabelInfo,
    create_label,
    ensure_required_labels,
    get_repository_labels,
    update_label,
    GitHubIssueError,
)


# =============================================================================
# Tests for LabelInfo and REQUIRED_LABELS
# =============================================================================


class TestLabelInfo:
    """Tests for the LabelInfo dataclass."""

    def test_label_info_is_frozen(self):
        """LabelInfo should be immutable."""
        label = LabelInfo(name="test", color="ff0000", description="Test label")
        with pytest.raises(AttributeError):
            label.name = "changed"

    def test_required_labels_contains_expected(self):
        """REQUIRED_LABELS should contain all expected labels."""
        label_names = {lbl.name for lbl in REQUIRED_LABELS}
        expected = {"setup", "question", "source-approved", "source-proposal", "wontfix"}
        assert label_names == expected

    def test_required_labels_have_colors(self):
        """All required labels should have valid colors."""
        for label in REQUIRED_LABELS:
            assert label.color
            # Color should be 6 hex chars
            assert len(label.color) == 6
            assert all(c in "0123456789abcdef" for c in label.color.lower())


# =============================================================================
# Tests for get_repository_labels
# =============================================================================


class TestGetRepositoryLabels:
    """Tests for get_repository_labels function."""

    @patch("src.integrations.github.issues.request.urlopen")
    def test_returns_label_list(self, mock_urlopen):
        """Should return a list of label dictionaries."""
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps([
            {"name": "bug", "color": "d73a4a", "description": "Something isn't working"},
            {"name": "enhancement", "color": "a2eeef", "description": "New feature"},
        ]).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_repository_labels(
            token="test-token",
            repository="owner/repo",
        )

        assert len(result) == 2
        assert result[0]["name"] == "bug"
        assert result[1]["name"] == "enhancement"

    @patch("src.integrations.github.issues.request.urlopen")
    def test_handles_empty_list(self, mock_urlopen):
        """Should handle repository with no labels."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"[]"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        result = get_repository_labels(
            token="test-token",
            repository="owner/repo",
        )

        assert result == []


# =============================================================================
# Tests for create_label
# =============================================================================


class TestCreateLabel:
    """Tests for create_label function."""

    @patch("src.integrations.github.issues.request.urlopen")
    def test_creates_label(self, mock_urlopen):
        """Should create a label with correct parameters."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        create_label(
            token="test-token",
            repository="owner/repo",
            name="new-label",
            color="ff0000",
            description="A new label",
        )

        # Verify the request was made
        mock_urlopen.assert_called_once()
        request_obj = mock_urlopen.call_args[0][0]
        assert "repos/owner/repo/labels" in request_obj.full_url
        assert request_obj.method == "POST"

    def test_requires_name(self):
        """Should raise error if name is empty."""
        with pytest.raises(GitHubIssueError, match="name must be provided"):
            create_label(
                token="test-token",
                repository="owner/repo",
                name="",
                color="ff0000",
            )

    def test_requires_color(self):
        """Should raise error if color is empty."""
        with pytest.raises(GitHubIssueError, match="color must be provided"):
            create_label(
                token="test-token",
                repository="owner/repo",
                name="test",
                color="",
            )

    @patch("src.integrations.github.issues.request.urlopen")
    def test_strips_hash_from_color(self, mock_urlopen):
        """Should strip # prefix from color."""
        mock_response = MagicMock()
        mock_response.read.return_value = b"{}"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        create_label(
            token="test-token",
            repository="owner/repo",
            name="test",
            color="#ff0000",
        )

        # Verify color in payload doesn't have #
        request_obj = mock_urlopen.call_args[0][0]
        payload = json.loads(request_obj.data.decode())
        assert payload["color"] == "ff0000"


# =============================================================================
# Tests for ensure_required_labels
# =============================================================================


class TestEnsureRequiredLabels:
    """Tests for ensure_required_labels function."""

    @patch("src.integrations.github.issues.create_label")
    @patch("src.integrations.github.issues.get_repository_labels")
    def test_creates_missing_labels(self, mock_get_labels, mock_create_label):
        """Should create labels that don't exist."""
        # Mock no existing labels
        mock_get_labels.return_value = []

        result = ensure_required_labels(
            token="test-token",
            repository="owner/repo",
        )

        # Should have created all required labels
        assert len(result["created"]) == len(REQUIRED_LABELS)
        assert len(result["existing"]) == 0
        assert mock_create_label.call_count == len(REQUIRED_LABELS)

    @patch("src.integrations.github.issues.create_label")
    @patch("src.integrations.github.issues.get_repository_labels")
    def test_skips_existing_labels(self, mock_get_labels, mock_create_label):
        """Should skip labels that already exist."""
        # Mock some existing labels
        mock_get_labels.return_value = [
            {"name": "setup", "color": "0e8a16", "description": ""},
            {"name": "question", "color": "d876e3", "description": ""},
        ]

        result = ensure_required_labels(
            token="test-token",
            repository="owner/repo",
        )

        # Should have created only missing labels
        assert "setup" in result["existing"]
        assert "question" in result["existing"]
        assert len(result["existing"]) == 2
        assert len(result["created"]) == len(REQUIRED_LABELS) - 2

    @patch("src.integrations.github.issues.create_label")
    @patch("src.integrations.github.issues.get_repository_labels")
    def test_all_labels_exist(self, mock_get_labels, mock_create_label):
        """Should not create anything if all labels exist."""
        # Mock all labels exist
        mock_get_labels.return_value = [
            {"name": lbl.name, "color": lbl.color, "description": lbl.description}
            for lbl in REQUIRED_LABELS
        ]

        result = ensure_required_labels(
            token="test-token",
            repository="owner/repo",
        )

        assert len(result["created"]) == 0
        assert len(result["existing"]) == len(REQUIRED_LABELS)
        mock_create_label.assert_not_called()

    @patch("src.integrations.github.issues.create_label")
    @patch("src.integrations.github.issues.get_repository_labels")
    def test_case_insensitive_matching(self, mock_get_labels, mock_create_label):
        """Should match labels case-insensitively."""
        # Mock labels with different case
        mock_get_labels.return_value = [
            {"name": "SETUP", "color": "0e8a16", "description": ""},
            {"name": "Question", "color": "d876e3", "description": ""},
        ]

        result = ensure_required_labels(
            token="test-token",
            repository="owner/repo",
        )

        # Should recognize existing labels despite case
        assert len(result["existing"]) == 2
        assert len(result["created"]) == len(REQUIRED_LABELS) - 2
