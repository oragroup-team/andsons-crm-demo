"""Tests for ova_email/agents/insight_agent.py - OVA-specific analytics client."""
import pytest
from unittest.mock import patch, MagicMock
from agents import insight_agent


class TestOVAContainsIdentifyingInfo:
    """Test OVA's PII detection (email + SG phone)."""

    def test_email_detection(self):
        """Email blocks."""
        assert insight_agent._contains_identifying_info("Email: test@example.com") is True

    def test_sg_phone_detection(self):
        """Singapore phone blocks."""
        assert insight_agent._contains_identifying_info("Phone 91234567") is True
        assert insight_agent._contains_identifying_info("+6581234567") is True

    def test_no_pii(self):
        """Clean text passes."""
        assert insight_agent._contains_identifying_info("No sensitive info") is False


class TestOVAInvestigatePatient:
    """Test OVA investigate_patient() - calls analytics_client over HTTP."""

    @patch("agents.insight_agent.ask_analytics")
    def test_found_and_verified(self, mock_ask):
        """Patient lookup via HTTP analytics service."""
        mock_ask.return_value = {
            "answer": "Patient in early contraception journey.",
            "verified": True,
            "sql_query": "SELECT ...",
        }

        result = insight_agent.investigate_patient("order_555")

        assert result["found"] is True
        assert result["blocked_for_pii"] is False
        assert "PERSONALIZATION LOOKUP" in result["brief_text"]
        # Verify analytics_client was called (not local BigQuery)
        mock_ask.assert_called_once()
        call_args = mock_ask.call_args
        assert "OVA" in call_args[0][0]  # OVA brand prefix in question

    @patch("agents.insight_agent.ask_analytics")
    def test_ova_brand_prefix_in_question(self, mock_ask):
        """OVA questions always state the brand explicitly."""
        mock_ask.return_value = {
            "answer": "Some data.",
            "verified": False,
        }

        insight_agent.investigate_patient("some_id")

        call_args = mock_ask.call_args
        question = call_args[0][0]
        assert "OVA" in question
        assert "Singapore" in question  # Market also prefixed


class TestOVAInvestigate:
    """Test OVA investigate() - business signal via HTTP."""

    @patch("agents.insight_agent.ask_analytics")
    def test_ova_question_branding(self, mock_ask):
        """OVA investigate always prefixes brand/market."""
        mock_ask.return_value = {
            "answer": "Finding.",
            "verified": True,
            "sql_query": "SELECT ...",
            "moengage_used": False,
        }

        insight_agent.investigate("Is EC revenue declining?")

        call_args = mock_ask.call_args
        question = call_args[0][0]
        assert "OVA" in question
        assert "Singapore" in question

    @patch("agents.insight_agent.ask_analytics")
    def test_uses_http_analytics_client(self, mock_ask):
        """investigate() calls analytics_client, not local BigQuery."""
        mock_ask.return_value = {
            "answer": "Result.",
            "verified": True,
            "sql_query": None,
            "moengage_used": False,
        }

        result = insight_agent.investigate("Question")

        # Should use the mocked analytics_client, not try BigQuery
        mock_ask.assert_called_once()
        assert result["bigquery_verified"] is True
