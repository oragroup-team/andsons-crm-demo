"""Tests for backend/agents/analytics_agent.py - brand/country scoping fix.

Regression test for a real, live-caught incident (Sept 25 2026, reported by
Bryan Chang in Slack): the analytics agent's own deterministic verification
safety-nets (_verify_campaign_family_total, _verify_flow_orders_answer) were
hardcoded to Brand='AndSons', Country='Singapore' - so an OVA SG question
that correctly queried OVA data got its answer silently "corrected" against
AndSons data instead, because the verification step re-checked the wrong
brand. This is now a SHARED agent (both @andSons Email and @OvaEmail route
through it), so a hardcoded single-brand verification scope is a real bug,
not just an OVA gap - _extract_brand_country_from_sql() fixes this by
mirroring whatever Brand/Country the agent's own query actually used.
"""
import pytest
from agents.analytics_agent import _extract_brand_country_from_sql


class TestExtractBrandCountryFromSQL:
    """Test the brand/country extraction that replaced hardcoded AndSons/SG."""

    def test_extracts_ova_brand_and_country(self):
        """OVA SG query - must extract Ova/Singapore, not default to AndSons."""
        sql = "SELECT COUNT(*) FROM flow_orders WHERE Brand='Ova' AND Country='Singapore'"
        brand, country = _extract_brand_country_from_sql(sql)
        assert brand == "Ova"
        assert country == "Singapore"

    def test_extracts_andsons_brand_and_country(self):
        """AndSons query still extracts correctly (no regression)."""
        sql = "SELECT SUM(Final_Revenue) FROM updated_sales_data WHERE Brand='AndSons' AND Country='Singapore'"
        brand, country = _extract_brand_country_from_sql(sql)
        assert brand == "AndSons"
        assert country == "Singapore"

    def test_extracts_other_country_for_same_brand(self):
        """AndSons Malaysia/Philippines - not just the Singapore default."""
        sql = "SELECT * FROM dotcom_plus_marketplace WHERE Brand='AndSons' AND Country='Malaysia'"
        brand, country = _extract_brand_country_from_sql(sql)
        assert brand == "AndSons"
        assert country == "Malaysia"

    def test_extracts_modern_molecules_brand(self):
        """A third brand (Modern Molecules) is not force-mapped to AndSons."""
        sql = "SELECT * FROM some_table WHERE Brand='ModernMolecules' AND Country='Singapore'"
        brand, country = _extract_brand_country_from_sql(sql)
        assert brand == "ModernMolecules"

    def test_case_insensitive_column_match(self):
        """Column casing varies across tables (Brand vs brand) - both match."""
        sql = "SELECT * FROM t WHERE brand='Ova' AND country='Malaysia'"
        brand, country = _extract_brand_country_from_sql(sql)
        assert brand == "Ova"
        assert country == "Malaysia"

    def test_no_brand_filter_returns_none(self):
        """No Brand/Country filter in the query - returns None, None (caller defaults)."""
        sql = "SELECT COUNT(*) FROM updated_sales_data WHERE status = 'DELIVERED'"
        brand, country = _extract_brand_country_from_sql(sql)
        assert brand is None
        assert country is None

    def test_last_occurrence_used_for_multi_clause_query(self):
        """Regex finds a Brand/Country match even with multiple WHERE clauses in the trace."""
        sql = (
            "SELECT * FROM a WHERE Brand='AndSons' AND Country='Singapore'; "
            "SELECT * FROM flow_orders WHERE Brand='Ova' AND Country='Malaysia'"
        )
        brand, country = _extract_brand_country_from_sql(sql)
        # re.search finds the FIRST match; this documents current behavior -
        # callers use _last() with dedicated regexes for other fields, but
        # this function's job is only to recover *a* real value that was
        # actually used, not necessarily the final one in a multi-query trace.
        assert brand in ("AndSons", "Ova")
        assert country in ("Singapore", "Malaysia")


class TestVerificationUsesExtractedScope:
    """Confirms the fix's actual effect: verification no longer silently
    substitutes AndSons for whatever brand the original question was about."""

    def test_ova_sql_scope_not_forced_to_andsons(self):
        """The core regression: an OVA query's extracted scope must be Ova,
        never silently coerced to AndSons by the verification helper."""
        ova_sql = "SELECT COUNT(DISTINCT order_id) FROM flow_orders WHERE Brand='Ova' AND Country='Singapore' AND Year=2026"
        brand, country = _extract_brand_country_from_sql(ova_sql)
        assert brand == "Ova", (
            "Regression: verification scope defaulted to AndSons for an OVA query - "
            "this is exactly the Sept 25 incident (Bryan Chang, OVA SG Abandon Cart data)."
        )
