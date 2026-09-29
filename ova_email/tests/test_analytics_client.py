"""Tests for ova_email/analytics_client.py - HTTP bridge to shared analytics agent."""
import pytest
from unittest.mock import patch, MagicMock
import analytics_client


class TestAskAnalyticsHTTPClient:
    """Test analytics_client.ask_analytics() HTTP behavior."""

    @patch("analytics_client.requests.post")
    def test_success_response(self, mock_post):
        """Successful response from analytics service."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "answer": "OVA revenue was SGD 5000.",
            "sql_query": "SELECT SUM(revenue) ...",
            "verified": True,
            "data_source": "bigquery",
            "moengage_used": False,
        }
        mock_post.return_value = mock_response

        result = analytics_client.ask_analytics("What was OVA revenue?")

        assert result["answer"] == "OVA revenue was SGD 5000."
        assert result["verified"] is True
        assert result["sql_query"] is not None
        assert result["moengage_used"] is False

        # Verify HTTP call was made correctly
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "/ask" in call_args[0][0]
        assert call_args[1]["json"]["question"] == "What was OVA revenue?"

    @patch("analytics_client.requests.post")
    def test_http_error_response(self, mock_post):
        """HTTP error (non-200) response."""
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 500 Server Error")
        mock_post.return_value = mock_response

        result = analytics_client.ask_analytics("Question")

        # Should fail gracefully with unavailable message
        assert result["verified"] is False
        assert "could not be reached" in result["answer"]
        assert result["data_source"] == "unavailable"

    @patch("analytics_client.requests.post")
    def test_error_field_in_response(self, mock_post):
        """Service returns an error in the response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "error": "BigQuery query failed: permission denied"
        }
        mock_post.return_value = mock_response

        result = analytics_client.ask_analytics("Question")

        assert result["verified"] is False
        assert "could not be reached" in result["answer"]

    @patch("analytics_client.requests.post")
    def test_request_timeout(self, mock_post):
        """HTTP request times out."""
        mock_post.side_effect = Exception("Connection timeout")

        result = analytics_client.ask_analytics("Question")

        assert result["verified"] is False
        assert "could not be reached" in result["answer"]

    @patch("analytics_client.requests.post")
    def test_malformed_json_response(self, mock_post):
        """Service response is not valid JSON."""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_post.return_value = mock_response

        result = analytics_client.ask_analytics("Question")

        assert result["verified"] is False
        assert "could not be reached" in result["answer"]

    @patch("analytics_client.requests.post")
    def test_response_with_optional_fields_missing(self, mock_post):
        """Response missing some optional fields - should handle gracefully."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "answer": "Some answer.",
            # sql_query, verified, etc. missing
        }
        mock_post.return_value = mock_response

        result = analytics_client.ask_analytics("Question")

        # Should use defaults for missing fields
        assert result["answer"] == "Some answer."
        assert result["verified"] is False  # defaults to False
        assert result["sql_query"] is None  # defaults to None
        assert result["moengage_used"] is False

    @patch("analytics_client.requests.post")
    def test_timeout_config(self, mock_post):
        """Request uses generous timeout for long-running queries."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"answer": "Result", "verified": True}
        mock_post.return_value = mock_response

        analytics_client.ask_analytics("Complex question")

        # Check timeout parameter
        call_args = mock_post.call_args
        assert "timeout" in call_args[1]
        assert call_args[1]["timeout"] == 300.0  # 5 minutes
