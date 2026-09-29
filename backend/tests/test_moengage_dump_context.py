"""Regression tests for backend/moengage_dump_context.py.

Real, live-caught incident this fixes (Sept 25 2026, OVA SG "Abandon Cart -
WL" flow, reported by Bryan Chang in Slack): @CRMAnalytics answered "6 sent"
for Sept 25 when the real MoEngage dashboard showed 3 attempted, 3 sent,
2 opened. Root cause: _TREND_METRIC_COLUMNS excluded "attempted" and
"opened" entirely, so a question about a PAST date (not the single most
recent daily pull) could never surface those two metrics from this
mechanism - forcing a fallback that landed on the wrong BigQuery table
(moengage_campaigns_email: a static Aug-20 snapshot with no Brand column).
Separately, _NODE_METRIC_COLUMNS never included failed/bounced/
failure_reasons at all, even though daily_flow_tracker.py genuinely
captures real named failure reasons per node per day.
"""
import pandas as pd
from moengage_dump_context import _latest_flow_text, _trend_text, _NODE_METRIC_COLUMNS, _TREND_METRIC_COLUMNS


class TestNodeMetricColumnsIncludeFailureData:
    """_NODE_METRIC_COLUMNS must cover failure/bounce counts, not just success metrics."""

    def test_failed_and_bounced_included(self):
        assert "failed" in _NODE_METRIC_COLUMNS
        assert "bounced" in _NODE_METRIC_COLUMNS

    def test_attempted_and_opened_included(self):
        assert "attempted" in _NODE_METRIC_COLUMNS
        assert "opened" in _NODE_METRIC_COLUMNS


class TestTrendMetricColumnsIncludeAttemptedOpened:
    """Regression: trend (any day other than the single latest pull) must be
    able to report attempted/opened - the exact gap that caused the Sept 25
    OVA SG wrong answer."""

    def test_attempted_in_trend(self):
        assert "attempted" in _TREND_METRIC_COLUMNS

    def test_opened_in_trend(self):
        assert "opened" in _TREND_METRIC_COLUMNS

    def test_failed_and_bounced_in_trend(self):
        assert "failed" in _TREND_METRIC_COLUMNS
        assert "bounced" in _TREND_METRIC_COLUMNS


class TestLatestFlowTextReportsRealNumbers:
    """End-to-end: rebuild the exact Sept 25 OVA SG scenario and confirm the
    text handed to the LLM matches the real MoEngage dashboard (3 attempted,
    3 sent, 3 delivered, 2 opened, SGD 380 revenue, 1 conversion) - not the
    wrong '6 sent' the agent originally reported."""

    def _make_row(self, **overrides):
        row = {
            "brand": "OVA_SG", "flow_name": "Abandon Cart - WL", "flow_status": "Published",
            "node_label": "Email #1: Abandon WL", "channel": "Email", "campaign_id": "camp_1",
            "attempted": 3, "sent": 3, "delivered": 3, "opened": 2, "adjusted_opened": 2,
            "clicked": 0, "failed": 0, "bounced": 0, "unsubscribed": 0, "complaints": 0,
            "conversions": 1, "revenue": 380.0, "failure_reasons": None,
        }
        row.update(overrides)
        return row

    def test_matches_real_dashboard_numbers(self):
        df = pd.DataFrame([self._make_row()])
        result = _latest_flow_text(df, "OVA_SG", "Abandon Cart - WL")

        assert "attempted=3" in result
        assert "sent=3" in result
        assert "delivered=3" in result
        assert "opened=2" in result
        assert "revenue=380" in result
        # The wrong number from the live incident must never appear
        assert "sent=6" not in result

    def test_failure_reasons_surfaced(self):
        """failure_reasons is a real text field - must appear when present,
        never silently dropped (the bug before this fix)."""
        df = pd.DataFrame([self._make_row(
            attempted=5, sent=3, failed=2, failure_reasons="mo_engage_suppression=2",
        )])
        result = _latest_flow_text(df, "OVA_SG", "Abandon Cart - WL")

        assert "failed=2" in result
        assert "mo_engage_suppression=2" in result
        assert "FAILURE REASONS" in result

    def test_no_failure_reasons_no_spurious_section(self):
        """When nothing failed, no FAILURE REASONS section is added at all."""
        df = pd.DataFrame([self._make_row()])
        result = _latest_flow_text(df, "OVA_SG", "Abandon Cart - WL")

        assert "FAILURE REASONS" not in result

    def test_brand_isolation_does_not_blend_other_brand(self):
        """Same flow_name existing under a different brand must never blend
        into this brand's totals - the same real name-collision guard
        gather_moengage_context's own docstring describes."""
        df = pd.DataFrame([
            self._make_row(brand="OVA_SG", sent=3, opened=2),
            self._make_row(brand="AS_SG", node_label="Email #1: Abandon HL", sent=100, opened=90, campaign_id="camp_2"),
        ])
        result = _latest_flow_text(df, "OVA_SG", "Abandon Cart - WL")

        assert "sent=3" in result
        assert "sent=100" not in result
        assert "opened=90" not in result


class TestTrendTextReportsPastDateMetrics:
    """The exact regression scenario: a question about Sept 25 specifically
    (not 'today'), asking for attempted/opened - must come from the trend
    section since Sept 25 is not the single most-recent pull day."""

    def test_past_date_shows_attempted_and_opened(self):
        history_df = pd.DataFrame([
            {
                "brand": "OVA_SG", "flow_name": "Abandon Cart - WL", "campaign_id": "camp_1",
                "date_range_start": "2026-09-24",
                "attempted": 4, "sent": 4, "delivered": 4, "opened": 1, "failed": 0, "bounced": 0,
                "conversions": 0, "revenue": 0.0,
            },
            {
                "brand": "OVA_SG", "flow_name": "Abandon Cart - WL", "campaign_id": "camp_1",
                "date_range_start": "2026-09-25",
                "attempted": 3, "sent": 3, "delivered": 3, "opened": 2, "failed": 0, "bounced": 0,
                "conversions": 1, "revenue": 380.0,
            },
        ])
        trend = _trend_text(history_df, "OVA_SG", "Abandon Cart - WL")

        # Sept 25's real attempted/opened numbers must be visible - this is
        # the exact data the LLM needs to correctly answer the Slack question
        # "on 25th September... how many were attempted... how many opened".
        assert "2026-09-25: attempted=3, sent=3, delivered=3, opened=2" in trend

    def test_empty_history_reports_plainly(self):
        trend = _trend_text(None, "OVA_SG", "Abandon Cart - WL")
        assert "no history recorded" in trend
