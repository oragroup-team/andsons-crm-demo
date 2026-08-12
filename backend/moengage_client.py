"""MoEngage connector - real Analytics Dashboards API, no mock/fallback data.

Auth: HTTP Basic, base64("<workspace_id>:<data_api_key>"), obtained from the
MoEngage dashboard at Settings -> Account -> APIs (a "Data API" key, not the
same as the write-side ingestion API key). Base URL is per data-center
(api-01..06 or api-101.moengage.com) - the workspace's dashboard URL
(dashboard-0X.moengage.com) tells you which one.

MoEngage's Analytics API is dashboard/chart-based, not a free-form "give me
campaign X's stats" query: you first build a dashboard + chart in the
MoEngage UI, then this client pulls that chart's data by (dashboard_id,
chart_id). There is no way to fetch arbitrary campaign performance without a
chart existing for it - that's a real MoEngage platform constraint, not a
limitation of this client. Charts to pull are configured via MOENGAGE_CHARTS
(see get_configured_chart_snapshots below) so whoever owns the MoEngage
dashboard controls what's exposed here, without a code change.

Same hard-fail philosophy as the BigQuery connection in analytics_agent.py:
if credentials aren't set, functions raise a clear RuntimeError rather than
returning empty/fake data. Callers (insight_agent.py) treat "not configured"
as "skip this data source and say so", never as "pretend it's empty".
"""
import base64
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger("moengage_client")

_TIMEOUT_SECONDS = 15


def is_configured() -> bool:
    return bool(
        os.environ.get("MOENGAGE_WORKSPACE_ID")
        and os.environ.get("MOENGAGE_DATA_API_KEY")
        and os.environ.get("MOENGAGE_DC")
    )


def _base_url() -> str:
    dc = os.environ.get("MOENGAGE_DC", "").strip()
    if not dc:
        raise RuntimeError(
            "MOENGAGE_DC is not set (e.g. '05' for the Singapore data center - check your MoEngage "
            "dashboard URL, dashboard-0X.moengage.com tells you X)."
        )
    return f"https://api-{dc}.moengage.com"


def _auth_header() -> dict:
    workspace_id = os.environ.get("MOENGAGE_WORKSPACE_ID", "")
    data_api_key = os.environ.get("MOENGAGE_DATA_API_KEY", "")
    if not workspace_id or not data_api_key:
        raise RuntimeError(
            "MOENGAGE_WORKSPACE_ID / MOENGAGE_DATA_API_KEY are not set. MoEngage integration requires "
            "a Data API key from Settings -> Account -> APIs in the MoEngage dashboard."
        )
    token = base64.b64encode(f"{workspace_id}:{data_api_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _get(path: str, params: Optional[dict] = None) -> dict:
    url = f"{_base_url()}{path}"
    resp = requests.get(url, headers=_auth_header(), params=params, timeout=_TIMEOUT_SECONDS)
    if resp.status_code == 401:
        raise RuntimeError("MoEngage rejected the request (401) - check MOENGAGE_WORKSPACE_ID/MOENGAGE_DATA_API_KEY.")
    resp.raise_for_status()
    return resp.json()


def list_dashboards() -> list:
    """GET /v5/analytics/dashboards - workspace-level (public) dashboards only."""
    return _get("/v5/analytics/dashboards").get("data", [])


def get_dashboard_charts(dashboard_id: str) -> list:
    """GET /v5/analytics/dashboards/{dashboard_id} - the charts on one dashboard."""
    return _get(f"/v5/analytics/dashboards/{dashboard_id}").get("data", [])


def get_chart_data(dashboard_id: str, chart_id: str) -> dict:
    """GET /v5/analytics/dashboards/{dashboard_id}/charts/{chart_id} - a single
    chart's data. Shape varies by analysis type (Behavior/Funnels/Retention/
    User/Session and Source) - callers should treat `data` as opaque and let
    the LLM summarize it rather than assuming specific fields."""
    return _get(f"/v5/analytics/dashboards/{dashboard_id}/charts/{chart_id}")


def _configured_chart_refs() -> list:
    """Parse MOENGAGE_CHARTS, a comma-separated list of
    'label:dashboard_id:chart_id' triples naming which real charts (already
    built by whoever owns the MoEngage dashboard) this app is allowed to
    pull, e.g.:
    MOENGAGE_CHARTS=campaign_engagement:63ede292b4c6a68b18c2c93f:63ef1f824da10b4fd96c6e3b,segment_overview:...
    """
    raw = os.environ.get("MOENGAGE_CHARTS", "")
    refs = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            logger.warning("Skipping malformed MOENGAGE_CHARTS entry (expected label:dashboard_id:chart_id): %r", entry)
            continue
        label, dashboard_id, chart_id = parts
        refs.append({"label": label, "dashboard_id": dashboard_id, "chart_id": chart_id})
    return refs


def get_configured_chart_snapshots() -> list:
    """Pull every chart named in MOENGAGE_CHARTS. Returns a list of
    {"label", "data", "error"} - a single chart failing (bad id, chart
    deleted) doesn't take down the others; the error is reported per-chart
    so a caller can say "X wasn't available" instead of silently omitting
    it. Returns [] (not an error) if MOENGAGE_CHARTS isn't set - configuring
    Data API credentials is enough to connect, chart selection is separate."""
    snapshots = []
    for ref in _configured_chart_refs():
        try:
            data = get_chart_data(ref["dashboard_id"], ref["chart_id"])
            snapshots.append({"label": ref["label"], "data": data, "error": None})
        except Exception as exc:  # noqa: BLE001 - reported per-chart, not raised
            logger.warning("MoEngage chart %r failed: %s", ref["label"], exc)
            snapshots.append({"label": ref["label"], "data": None, "error": str(exc)})
    return snapshots
