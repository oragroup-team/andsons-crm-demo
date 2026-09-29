"""Tests for backend/agents/insight_agent.py"""
import pytest
from unittest.mock import patch, MagicMock
from agents import insight_agent


class TestContainsIdentifyingInfo:
    """Test the PII detection regex guards."""

    def test_email_detection(self):
        """Email addresses should be blocked."""
        assert insight_agent._contains_identifying_info("Contact: user@example.com") is True
        assert insight_agent._contains_identifying_info("No email here") is False

    def test_singapore_phone_detection(self):
        """Singapore phone numbers should be blocked."""
        # Various SG phone formats
        assert insight_agent._contains_identifying_info("Call 91234567") is True
        assert insight_agent._contains_identifying_info("Phone: 81234567") is True
        assert insight_agent._contains_identifying_info("+6591234567") is True
        assert insight_agent._contains_identifying_info("+65 9123 4567") is True
        assert insight_agent._contains_identifying_info("No phone") is False


class TestInvestigatePatient:
    """Test investigate_patient() behavior - blocking PII, handling success/failure."""

    @patch("agents.insight_agent.ask_analytics")
    def test_found_and_verified(self, mock_ask):
        """Successful patient lookup with verified data."""
        mock_ask.return_value = {
            "answer": "Patient is in early lifecycle, no prior orders, eligible for onboarding.",
            "verified": True,
            "sql_query": "SELECT ...",
        }
        result = insight_agent.investigate_patient("customer_123")

        assert result["found"] is True
        assert result["blocked_for_pii"] is False
        assert "PERSONALIZATION LOOKUP" in result["brief_text"]
        assert result["bigquery_verified"] is True

    @patch("agents.insight_agent.ask_analytics")
    def test_blocked_for_pii_email(self, mock_ask):
        """PII guard blocks email addresses in response."""
        mock_ask.return_value = {
            "answer": "Patient email is john@example.com and is in lifecycle X.",
            "verified": True,
        }
        result = insight_agent.investigate_patient("customer_123")

        assert result["found"] is False
        assert result["blocked_for_pii"] is True
        assert "directly-identifying" in result["brief_text"]

    @patch("agents.insight_agent.ask_analytics")
    def test_blocked_for_pii_phone(self, mock_ask):
        """PII guard blocks phone numbers in response."""
        mock_ask.return_value = {
            "answer": "Patient phone +6591234567 is on file, lifecycle is X.",
            "verified": True,
        }
        result = insight_agent.investigate_patient("customer_123")

        assert result["found"] is False
        assert result["blocked_for_pii"] is True

    @patch("agents.insight_agent.ask_analytics")
    def test_not_verified(self, mock_ask):
        """Unverified lookup fails gracefully."""
        mock_ask.return_value = {
            "answer": "Could not find this identifier in any system.",
            "verified": False,
        }
        result = insight_agent.investigate_patient("invalid_id")

        assert result["found"] is False
        assert result["blocked_for_pii"] is False
        assert "did not return a verified" in result["brief_text"]

    @patch("agents.insight_agent.ask_analytics")
    def test_empty_answer(self, mock_ask):
        """Empty answer is treated as no finding."""
        mock_ask.return_value = {
            "answer": "",
            "verified": False,
        }
        result = insight_agent.investigate_patient("some_id")

        assert result["found"] is False
        assert result["blocked_for_pii"] is False


class TestInvestigate:
    """Test investigate() - business signal investigation."""

    @patch("agents.insight_agent.ask_analytics")
    def test_verified_finding(self, mock_ask):
        """Verified business signal produces a proper brief."""
        mock_ask.return_value = {
            "answer": "Winback revenue is down 15% this month.",
            "verified": True,
            "sql_query": "SELECT SUM(revenue) ...",
            "moengage_used": False,
        }
        result = insight_agent.investigate("Is winback revenue declining?")

        assert "BUSINESS SIGNAL" in result["brief_text"]
        assert "Is winback revenue declining?" in result["brief_text"]
        assert "BIGQUERY FINDING" in result["brief_text"]
        assert result["bigquery_verified"] is True

    @patch("agents.insight_agent.ask_analytics")
    def test_unverified_finding(self, mock_ask):
        """Unverified finding is noted."""
        mock_ask.return_value = {
            "answer": "Approximately 20% decline based on sample data.",
            "verified": False,
            "sql_query": None,
            "moengage_used": False,
        }
        result = insight_agent.investigate("Is revenue declining?")

        assert "could not be fully verified" in result["brief_text"]
        assert result["bigquery_verified"] is False

    @patch("agents.insight_agent.ask_analytics")
    def test_moengage_note_included(self, mock_ask):
        """MoEngage usage is noted in the brief."""
        mock_ask.return_value = {
            "answer": "Open rates are up 5%.",
            "verified": True,
            "sql_query": "SELECT ...",
            "moengage_used": True,
        }
        result = insight_agent.investigate("What are our open rates?")

        assert "MoEngage" in result["brief_text"]
        assert "engagement data" in result["brief_text"]

    @patch("agents.insight_agent.ask_analytics")
    def test_file_context_included(self, mock_ask):
        """Uploaded file context is folded into the brief."""
        mock_ask.return_value = {
            "answer": "Revenue was SGD 5000 this month.",
            "verified": True,
            "sql_query": "SELECT ...",
            "moengage_used": False,
        }
        result = insight_agent.investigate(
            "What was revenue?",
            file_context="Customer cohort: Premium, LTV>$500"
        )

        assert "Premium" in result["brief_text"]
        assert "500" in result["brief_text"]
