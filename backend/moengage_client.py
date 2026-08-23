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
limitation of this client.

By default, EVERY chart on EVERY workspace-level dashboard is pulled (see
get_all_chart_snapshots) - there's no manual per-chart allowlist to keep up
to date. At this workspace's real scale that's 29 dashboards / ~138 charts,
so fetches are parallelized (ThreadPoolExecutor) and cached for
_CACHE_TTL_SECONDS, since re-hitting ~170 endpoints on every single Slack
question would be slow and needless - the underlying MoEngage data doesn't
change meaningfully within a few minutes.

Same hard-fail philosophy as the BigQuery connection in analytics_agent.py:
if credentials aren't set, functions raise a clear RuntimeError rather than
returning empty/fake data. Callers (insight_agent.py) treat "not configured"
as "skip this data source and say so", never as "pretend it's empty".
"""
import base64
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

import requests

logger = logging.getLogger("moengage_client")

_TIMEOUT_SECONDS = 25
_MAX_WORKERS = 6  # higher concurrency measurably increases MoEngage read-timeout
# rate at this workspace's scale (~1/3 of 137 charts timed out at 16 workers) -
# this is empirically the more reliable tradeoff, not just a slower one.
_MAX_RETRIES = 2
_CACHE_TTL_SECONDS = 900  # 15 min - see module docstring

_cache = {"snapshots": None, "fetched_at": 0.0}
_catalog_cache = {"refs": None, "fetched_at": 0.0}
_catalog_cache_lock = threading.Lock()
# Each Slack @-mention is handled on its own background thread (run_in_background
# in slack_integration.py) - without a lock, two questions arriving close
# together with an expired/cold cache would both kick off a full ~138-chart
# fetch simultaneously (wasted duplicate work, not a correctness bug, but a
# real inefficiency at real traffic). This serializes the fetch itself while
# still serving already-cached data lock-free (the common case).
_cache_lock = threading.Lock()


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


def campaigns_api_configured() -> bool:
    """The Campaigns Search API (core-services/v1/campaigns/search) is a
    REAL, SEPARATE MoEngage surface from the Analytics Dashboards API above
    - genuinely different data, a different key, and a different auth
    shape, not just an alternate route to the same thing. It returns real
    campaign CONFIGURATION (audience targeting/segmentation filters,
    control-group setup and percentage, the real UTM params MoEngage
    itself assigns, conversion goal definitions) for EVERY real campaign in
    the account (confirmed live: 895 real campaigns, 885 of them one-time
    sends the Analytics Dashboards charts and the Flows report CSV export
    both structurally exclude - neither of those only ever covered
    automated Flow-triggered touchpoints). It does NOT return performance
    numbers (sent/delivered/opens/clicks/revenue) at all - confirmed live,
    those fields don't exist anywhere in a real response. Needs
    MOENGAGE_CAMPAIGN_API_KEY (Settings -> Account -> APIs -> "Campaign
    report/Business events/..." tile - a different key from
    MOENGAGE_DATA_API_KEY, which only works for the Analytics Dashboards
    API above)."""
    return bool(
        os.environ.get("MOENGAGE_WORKSPACE_ID")
        and os.environ.get("MOENGAGE_CAMPAIGN_API_KEY")
        and os.environ.get("MOENGAGE_DC")
    )


def _campaign_auth_headers() -> dict:
    workspace_id = os.environ.get("MOENGAGE_WORKSPACE_ID", "")
    campaign_api_key = os.environ.get("MOENGAGE_CAMPAIGN_API_KEY", "")
    if not workspace_id or not campaign_api_key:
        raise RuntimeError(
            "MOENGAGE_WORKSPACE_ID / MOENGAGE_CAMPAIGN_API_KEY are not set. The Campaigns Search API "
            "requires a separate Campaign API key from Settings -> Account -> APIs in the MoEngage "
            "dashboard (not the same key as MOENGAGE_DATA_API_KEY)."
        )
    token = base64.b64encode(f"{workspace_id}:{campaign_api_key}".encode()).decode()
    # Real, confirmed-live requirement, not documented anywhere obvious:
    # this endpoint rejects a request with only the Basic auth header
    # (401 "MOE-APPKEY missing in Authentication Header") - it also needs
    # the workspace id repeated as its own MOE-APPKEY header.
    return {"Authorization": f"Basic {token}", "MOE-APPKEY": workspace_id, "Content-Type": "application/json"}


_CAMPAIGNS_CACHE_TTL_SECONDS = 900  # 15 min - same reasoning as the chart snapshot cache
_campaigns_cache = {"campaigns": None, "fetched_at": 0.0}
_campaigns_cache_lock = threading.Lock()
_CAMPAIGN_SEARCH_PAGE_LIMIT = 15  # real, confirmed-live server-side max ("limit value is too long")


def search_campaigns(force_refresh: bool = False) -> list:
    """POST /core-services/v1/campaigns/search - every real campaign in the
    account (895 confirmed live, far more than either the Analytics
    Dashboards charts or the Flows report CSV export cover - see
    campaigns_api_configured()'s docstring for why). Paginated server-side
    at a real max of 15 per page - confirmed live, a higher limit is
    rejected outright, not silently capped - so a full fetch is ~60
    sequential calls; cached for _CAMPAIGNS_CACHE_TTL_SECONDS, same
    reasoning as get_all_chart_snapshots, so repeated questions in the same
    session don't re-paginate the whole account every time. Returns the
    REAL, UNMODIFIED response objects (real campaign config: basic_details,
    control_group_details, segmentation_details, utm_params,
    conversion_goal_details, campaign_content, etc.) - callers decide what
    to extract; this function's only job is getting the real data, not
    shaping it for a particular downstream use (e.g. the BigQuery loader in
    moengage_export/ strips the large real HTML email body out of
    campaign_content before loading, which is a loading-time decision, not
    a fetching-time one)."""
    now = time.time()
    if not force_refresh and _campaigns_cache["campaigns"] is not None and (now - _campaigns_cache["fetched_at"]) < _CAMPAIGNS_CACHE_TTL_SECONDS:
        return _campaigns_cache["campaigns"]

    with _campaigns_cache_lock:
        now = time.time()
        if not force_refresh and _campaigns_cache["campaigns"] is not None and (now - _campaigns_cache["fetched_at"]) < _CAMPAIGNS_CACHE_TTL_SECONDS:
            return _campaigns_cache["campaigns"]

        url = f"{_base_url()}/core-services/v1/campaigns/search"
        headers = _campaign_auth_headers()
        all_campaigns = []
        page = 1
        while True:
            payload = {"page": page, "limit": _CAMPAIGN_SEARCH_PAGE_LIMIT, "request_id": str(uuid.uuid4())}
            last_exc = None
            batch = None
            for attempt in range(_MAX_RETRIES + 1):
                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT_SECONDS)
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                    last_exc = exc
                    if attempt < _MAX_RETRIES:
                        time.sleep(1.5 * (attempt + 1))
                        continue
                    raise
                if resp.status_code == 401:
                    raise RuntimeError(
                        "MoEngage rejected the Campaigns Search request (401) - check "
                        "MOENGAGE_WORKSPACE_ID/MOENGAGE_CAMPAIGN_API_KEY."
                    )
                resp.raise_for_status()
                batch = resp.json()
                break
            if batch is None:
                raise last_exc  # unreachable, satisfies type checkers
            if not batch:
                break
            all_campaigns.extend(batch)
            if len(batch) < _CAMPAIGN_SEARCH_PAGE_LIMIT:
                break
            page += 1

        _campaigns_cache["campaigns"] = all_campaigns
        _campaigns_cache["fetched_at"] = now
        logger.info("Fetched %d real campaigns from the Campaigns Search API (%d pages).", len(all_campaigns), page)
        return all_campaigns


def _get(path: str, params: Optional[dict] = None) -> dict:
    """A read timeout under concurrent load is common at this workspace's
    scale (empirically ~1/3 of requests at high concurrency) and usually
    transient, so a couple of quick retries meaningfully improves the real
    success rate - a genuine failure (404, bad auth) still raises straight
    away, only timeouts/connection errors are retried."""
    url = f"{_base_url()}{path}"
    headers = _auth_header()
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=_TIMEOUT_SECONDS)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        if resp.status_code == 401:
            raise RuntimeError("MoEngage rejected the request (401) - check MOENGAGE_WORKSPACE_ID/MOENGAGE_DATA_API_KEY.")
        resp.raise_for_status()
        return resp.json()
    raise last_exc  # unreachable, satisfies type checkers


def list_dashboards() -> list:
    """GET /v5/analytics/dashboards - workspace-level (public) dashboards only."""
    return _get("/v5/analytics/dashboards").get("data", [])


def get_dashboard_charts(dashboard_id: str) -> list:
    """GET /v5/analytics/dashboards/{dashboard_id}/charts - the charts on one
    dashboard (id + name only, not their data - see get_chart_data)."""
    return _get(f"/v5/analytics/dashboards/{dashboard_id}/charts").get("data", {}).get("chart_ids", [])


def get_chart_data(dashboard_id: str, chart_id: str) -> dict:
    """GET /v5/analytics/dashboards/{dashboard_id}/charts/{chart_id} - a single
    chart's data. Shape varies by analysis type (Behavior/Funnels/Retention/
    User/Session and Source) - callers should treat `data` as opaque and let
    the LLM summarize it rather than assuming specific fields."""
    return _get(f"/v5/analytics/dashboards/{dashboard_id}/charts/{chart_id}")


def _list_all_chart_refs() -> list:
    """Every (dashboard, chart) pair across every workspace-level dashboard,
    fetched in parallel - the 29-way dashboard listing call is itself fast,
    it's this fan-out (one call per dashboard) that benefits from threading."""
    dashboards = list_dashboards()
    refs = []

    def _charts_for(dashboard: dict) -> list:
        try:
            charts = get_dashboard_charts(dashboard["_id"])
        except Exception as exc:  # noqa: BLE001 - one bad dashboard shouldn't drop the rest
            logger.warning("Could not list charts for dashboard %r: %s", dashboard.get("name"), exc)
            return []
        return [
            {
                "dashboard_id": dashboard["_id"],
                "dashboard_name": dashboard.get("name") or dashboard["_id"],
                "chart_id": c["_id"],
                "chart_name": c.get("name") or c["_id"],
            }
            for c in charts
        ]

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        for result in pool.map(_charts_for, dashboards):
            refs.extend(result)
    return refs


def list_chart_catalog(force_refresh: bool = False) -> list:
    """The real MoEngage 'schema' - every real dashboard+chart NAME across
    the workspace (no chart data, just what exists and what it's called),
    the direct MoEngage analogy to BigQuery's own schema-inspection tools
    (sql_db_list_tables/sql_db_schema) that the SQL agent already uses to
    reason about what's genuinely available before writing a query. A
    thin, cached public wrapper around _list_all_chart_refs() - confirmed
    live to cost ~7s (vs ~40-50s for the full per-chart data fetch), cheap
    enough to genuinely reason over on every question rather than needing
    a coarse pre-gate to avoid paying for it. Cached like
    get_all_chart_snapshots (chart NAMES change even less often than chart
    DATA, so the same TTL is a safe, conservative choice, not a compromise)."""
    now = time.time()
    if not force_refresh and _catalog_cache["refs"] is not None and (now - _catalog_cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _catalog_cache["refs"]

    with _catalog_cache_lock:
        now = time.time()
        if not force_refresh and _catalog_cache["refs"] is not None and (now - _catalog_cache["fetched_at"]) < _CACHE_TTL_SECONDS:
            return _catalog_cache["refs"]

        refs = _list_all_chart_refs()
        _catalog_cache["refs"] = refs
        _catalog_cache["fetched_at"] = now
        return refs


def get_all_chart_snapshots(force_refresh: bool = False) -> list:
    """Pull EVERY chart's real data from EVERY workspace-level dashboard -
    the full ~138-chart set, not a curated subset. Returns a list of
    {"label" (dashboard - chart), "data", "error"} - one chart failing
    (deleted, permission change) doesn't drop the rest, and is reported
    rather than silently omitted. Cached for _CACHE_TTL_SECONDS so repeated
    questions in the same few minutes don't re-fetch ~170 endpoints every
    time; pass force_refresh=True to bypass that."""
    now = time.time()
    if not force_refresh and _cache["snapshots"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
        return _cache["snapshots"]

    with _cache_lock:
        # Re-check inside the lock: another thread may have just finished
        # populating the cache while this one was waiting on it.
        now = time.time()
        if not force_refresh and _cache["snapshots"] is not None and (now - _cache["fetched_at"]) < _CACHE_TTL_SECONDS:
            return _cache["snapshots"]

        refs = _list_all_chart_refs()
        snapshots = []

        def _fetch(ref: dict) -> dict:
            label = f"{ref['dashboard_name']} - {ref['chart_name']}"
            try:
                data = get_chart_data(ref["dashboard_id"], ref["chart_id"])
                return {"label": label, "data": data.get("data"), "error": None}
            except Exception as exc:  # noqa: BLE001 - reported per-chart, not raised
                logger.warning("MoEngage chart %r failed: %s", label, exc)
                return {"label": label, "data": None, "error": str(exc)}

        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
            futures = [pool.submit(_fetch, ref) for ref in refs]
            for future in as_completed(futures):
                snapshots.append(future.result())

        _cache["snapshots"] = snapshots
        _cache["fetched_at"] = now
        return snapshots
