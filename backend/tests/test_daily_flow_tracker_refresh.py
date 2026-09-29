"""Tests for backend/moengage_export/daily_flow_tracker.py's rolling-
refresh fix.

Real, live-caught incident this fixes (2026-09-29, Bryan Chang): the daily
cron only ever pulled "yesterday" ONCE and never re-checked it - but real
MoEngage Campaign Stats for a given calendar day keep accruing for a day
or two afterward (delayed/retried sends), so an already-recorded day's
stored numbers could go stale relative to what MoEngage's own frontend
shows later (confirmed live: a stored day showed 7 WhatsApp sent / 3
emails sent while the live MoEngage dashboard showed 10 / 4 for the exact
same real day).
"""
import sys
import os
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "moengage_export"))

import pandas as pd
import daily_flow_tracker as dft


class TestBuildTrackerWithRefresh:
    """build_tracker_with_refresh must produce ONE row-set PER real
    calendar day (never one blended multi-day aggregate), matching
    backfill_history's own per-day granularity - this is what lets
    write_history's upsert-by-day-key correct a stale day without
    disturbing any other day's already-correct numbers."""

    @patch("daily_flow_tracker.mc")
    @patch("daily_flow_tracker.discover_base_rows")
    @patch("daily_flow_tracker._fill_stats_for_window")
    def test_pulls_one_window_per_day_not_one_blended_range(self, mock_fill, mock_discover, mock_mc):
        mock_mc._creds_for_brand.return_value = {"workspace_id": "ws_1"}
        mock_discover.return_value = [{"campaign_id": "c1", "flow_id": "f1"}]
        mock_fill.return_value = pd.DataFrame([{"flow_name": "X", "sent": 1}])

        dft.build_tracker_with_refresh(3, status=None, brand="OVA_SG")

        # Exactly 3 calls, each a SINGLE-day window (start == end), never one
        # call spanning all 3 days as a single blended range.
        assert mock_fill.call_count == 3
        for call in mock_fill.call_args_list:
            base_rows, start_str, end_str = call[0][0], call[0][1], call[0][2]
            assert start_str == end_str, "each refreshed day must be its own single-day window, not a blended range"

    @patch("daily_flow_tracker.mc")
    @patch("daily_flow_tracker.discover_base_rows")
    @patch("daily_flow_tracker._fill_stats_for_window")
    def test_discovery_happens_once_not_per_day(self, mock_fill, mock_discover, mock_mc):
        """Flow/node discovery (the slow ~150-flow call) must happen ONCE,
        reused across every refreshed day - not re-discovered per day."""
        mock_mc._creds_for_brand.return_value = {"workspace_id": "ws_1"}
        mock_discover.return_value = [{"campaign_id": "c1", "flow_id": "f1"}]
        mock_fill.return_value = pd.DataFrame([{"flow_name": "X", "sent": 1}])

        dft.build_tracker_with_refresh(5, status=None, brand="AS_SG")

        mock_discover.assert_called_once()

    @patch("daily_flow_tracker.mc")
    @patch("daily_flow_tracker.discover_base_rows")
    def test_no_flows_returns_empty(self, mock_discover, mock_mc):
        mock_mc._creds_for_brand.return_value = {"workspace_id": "ws_1"}
        mock_discover.return_value = []

        result = dft.build_tracker_with_refresh(3, status=None, brand="OVA_SG")

        assert result.empty

    @patch("daily_flow_tracker.mc")
    @patch("daily_flow_tracker.discover_base_rows")
    @patch("daily_flow_tracker._fill_stats_for_window")
    def test_empty_days_are_skipped_not_included(self, mock_fill, mock_discover, mock_mc):
        """A day with no real rows (empty DataFrame) contributes nothing -
        never a spurious blank row in the combined output."""
        mock_mc._creds_for_brand.return_value = {"workspace_id": "ws_1"}
        mock_discover.return_value = [{"campaign_id": "c1", "flow_id": "f1"}]
        mock_fill.side_effect = [
            pd.DataFrame(columns=dft._TRACKER_COLUMNS),  # empty day
            pd.DataFrame([{"flow_name": "X", "sent": 5}]),  # real day
        ]

        result = dft.build_tracker_with_refresh(2, status=None, brand="OVA_SG")

        assert len(result) == 1
        assert result.iloc[0]["sent"] == 5


class TestRunBackwardCompatibility:
    """run()'s default refresh_days_back=0 must preserve the EXACT prior
    single-aggregate-window behavior for any caller that doesn't opt in -
    the CLI (main()) and any future non-cron caller must never be silently
    affected by this fix."""

    @patch("daily_flow_tracker.mc")
    @patch("daily_flow_tracker.build_tracker")
    @patch("daily_flow_tracker.build_tracker_with_refresh")
    @patch("daily_flow_tracker.write_history")
    @patch("daily_flow_tracker.build_war_room_sheet")
    @patch("daily_flow_tracker._war_room_target_reference_df")
    def test_default_uses_old_build_tracker(
        self, mock_war_room_ref, mock_war_room, mock_write_history, mock_refresh, mock_build, mock_mc, tmp_path,
    ):
        mock_mc.MOENGAGE_BRANDS = ["AS_SG"]
        mock_mc.brand_configured.return_value = True
        mock_build.return_value = pd.DataFrame([{"flow_name": "X", "sent": 1}])
        mock_write_history.return_value = pd.DataFrame([{"flow_name": "X", "sent": 1}])
        mock_war_room.return_value = pd.DataFrame()
        mock_war_room_ref.return_value = pd.DataFrame()

        out_prefix = str(tmp_path / "out")
        history_path = str(tmp_path / "history.csv")
        dft.run(days=1, status=None, out_prefix=out_prefix, history_path=history_path)

        mock_build.assert_called_once()
        mock_refresh.assert_not_called()

    @patch("daily_flow_tracker.mc")
    @patch("daily_flow_tracker.build_tracker")
    @patch("daily_flow_tracker.build_tracker_with_refresh")
    @patch("daily_flow_tracker.write_history")
    @patch("daily_flow_tracker.build_war_room_sheet")
    @patch("daily_flow_tracker._war_room_target_reference_df")
    def test_refresh_days_back_uses_new_function(
        self, mock_war_room_ref, mock_war_room, mock_write_history, mock_refresh, mock_build, mock_mc, tmp_path,
    ):
        mock_mc.MOENGAGE_BRANDS = ["AS_SG"]
        mock_mc.brand_configured.return_value = True
        mock_refresh.return_value = pd.DataFrame([{"flow_name": "X", "sent": 1}])
        mock_write_history.return_value = pd.DataFrame([{"flow_name": "X", "sent": 1}])
        mock_war_room.return_value = pd.DataFrame()
        mock_war_room_ref.return_value = pd.DataFrame()

        out_prefix = str(tmp_path / "out")
        history_path = str(tmp_path / "history.csv")
        dft.run(days=1, status=None, out_prefix=out_prefix, history_path=history_path, refresh_days_back=3)

        mock_refresh.assert_called_once_with(3, None, brand="AS_SG")
        mock_build.assert_not_called()
