"""Tests for moengage_dump_context.py's test/duplicate-flow exclusion.

Real, live-confirmed contamination risk this guards against: 23 of 393
real flow names across the account are test/duplicate artifacts, several
sitting right next to a near-identically-named real production flow (e.g.
"Internal Testing - Replenishment_ED_RX_OF" alongside the real production
"Replenishment_ED_RX_OF", both real, both currently live) - exactly the
kind of near-collision an LLM flow-selection call could conflate or
double-select, silently blending test traffic into a real business
answer without anyone asking for that.
"""
from moengage_dump_context import _is_test_or_duplicate_flow_name, _QUESTION_WANTS_TEST_FLOWS_RE


class TestIsTestOrDuplicateFlowName:
    def test_internal_testing_prefix_detected(self):
        assert _is_test_or_duplicate_flow_name("Internal Testing - Replenishment_ED_RX_OF") is True
        assert _is_test_or_duplicate_flow_name("internal testing - Prescription Renewal_SC_Before 14D_Updated") is True

    def test_duplicate_prefix_detected(self):
        assert _is_test_or_duplicate_flow_name("Duplicate - Abandon Cart - WL") is True
        assert _is_test_or_duplicate_flow_name("TEST_Duplicate - Payment_Failed") is True

    def test_testing_substring_detected(self):
        assert _is_test_or_duplicate_flow_name("Testing_WL_Program_New Paitent Path Flow") is True

    def test_real_production_flows_not_flagged(self):
        """The exact real flows from this incident must never be
        misidentified as test flows - that would hide real data."""
        assert _is_test_or_duplicate_flow_name("Replenishment_ED_RX_OF") is False
        assert _is_test_or_duplicate_flow_name("Abandon Cart - WL") is False
        assert _is_test_or_duplicate_flow_name("No show consultation") is False
        assert _is_test_or_duplicate_flow_name("WL Replenishment Nudge (Weekly Injectables)") is False


class TestQuestionWantsTestFlows:
    def test_question_mentioning_test_opts_in(self):
        assert _QUESTION_WANTS_TEST_FLOWS_RE.search("How did our internal testing flows perform?")
        assert _QUESTION_WANTS_TEST_FLOWS_RE.search("Check the duplicate flow's numbers")

    def test_normal_business_question_does_not_opt_in(self):
        assert not _QUESTION_WANTS_TEST_FLOWS_RE.search(
            "For ASSG No show consultation Flow, on the 24th of September, how many entries were there?"
        )
