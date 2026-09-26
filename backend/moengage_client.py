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
_MAX_RATE_LIMIT_RETRIES = 6  # see _request_with_retry - real, live-confirmed need:
# a parallel account-wide crawl (dump_all_flow_stats.py, 150 real flows / 6
# workers) hit real 429s on /v5/flows/{id} well before the whole batch was
# through - 2 retries wasn't enough to ride that out, 6 was.
_CACHE_TTL_SECONDS = 900  # 15 min - see module docstring


def _request_with_retry(method: str, url: str, **kwargs) -> requests.Response:
    """Shared retry wrapper for every real MoEngage HTTP call in this
    client. Two real, live-confirmed failure modes get retried with
    backoff: a transient timeout/connection error, and MoEngage's own
    real per-endpoint rate limiting (429) - confirmed live not just on
    the documented 100-calls/min Campaign Stats endpoint, but on
    /v5/flows/{id} too, the hard way (a parallel 150-flow crawl silently
    dropped ~40 flows to unretried 429s before this existed). A 429
    honors a real Retry-After header when MoEngage sends one, otherwise
    backs off 20s flat. Every other status (400/401/404/...) is returned
    as-is, unexamined - the right error message differs per endpoint
    (which env var a 401 should point callers at), so that's left to each
    caller, same as before this helper existed."""
    last_exc = None
    for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=_TIMEOUT_SECONDS, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_exc = exc
            if attempt < _MAX_RETRIES:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        if resp.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
            wait = float(resp.headers.get("Retry-After", 20))
            logger.warning("%s %s rate-limited (429) - waiting %.0fs before retrying (attempt %d/%d).",
                            method, url, wait, attempt + 1, _MAX_RATE_LIMIT_RETRIES)
            time.sleep(wait)
            continue
        return resp
    raise last_exc  # unreachable, satisfies type checkers

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


# Every real MoEngage brand/workspace this project has credentials for, as
# of 2026-09-24 (see backend/.env.example's own comment block for the full
# real list and where each came from) - AS_SG is the plain, unprefixed
# MOENGAGE_* vars (the original single workspace, confirmed live to be
# andSons SG), every other brand uses its own {BRAND}_MOENGAGE_* vars (same
# convention as warehouse_client.py's {BRAND}_DB_* vars, deliberately
# reusing the identical brand key set: AS_SG, AS_MY, AS_PH, OVA_SG, OVA_MY,
# OVA_PH, plus MODERN_MOLECULES - a real workspace name from MoEngage
# itself that doesn't map onto any existing brand key, kept as its own).
MOENGAGE_BRANDS = ["AS_SG", "AS_MY", "AS_PH", "OVA_SG", "OVA_MY", "OVA_PH", "MODERN_MOLECULES"]


def _creds_for_brand(brand: Optional[str]) -> dict:
    """Real credentials for one real brand's MoEngage workspace - AS_SG (or
    brand=None, same thing) reads the original plain MOENGAGE_* vars for
    backward compatibility (every caller written before multi-brand
    support keeps working unchanged); every other real brand reads its own
    {BRAND}_MOENGAGE_* vars. Never fabricates a value - a brand with no
    credentials configured gets empty strings here, and the functions that
    consume this raise their own clear RuntimeError, same as always."""
    prefix = "" if (not brand or brand == "AS_SG") else f"{brand}_"
    return {
        "workspace_id": os.environ.get(f"{prefix}MOENGAGE_WORKSPACE_ID", ""),
        "data_api_key": os.environ.get(f"{prefix}MOENGAGE_DATA_API_KEY", ""),
        "campaign_api_key": os.environ.get(f"{prefix}MOENGAGE_CAMPAIGN_API_KEY", ""),
        "dc": os.environ.get(f"{prefix}MOENGAGE_DC", "").strip(),
    }


def brand_configured(brand: Optional[str]) -> bool:
    """True if this real brand has a genuinely complete credential set
    (workspace id + at least one of the two real API keys + a data
    center) - used to decide which brands a multi-workspace pull (see
    moengage_export/daily_flow_tracker.py) can actually attempt, rather
    than failing loudly mid-run on one missing credential."""
    creds = _creds_for_brand(brand)
    return bool(creds["workspace_id"] and creds["dc"] and (creds["data_api_key"] or creds["campaign_api_key"]))


def is_configured(brand: Optional[str] = None) -> bool:
    creds = _creds_for_brand(brand)
    return bool(creds["workspace_id"] and creds["data_api_key"] and creds["dc"])


def _base_url(brand: Optional[str] = None) -> str:
    dc = _creds_for_brand(brand)["dc"]
    if not dc:
        raise RuntimeError(
            "MOENGAGE_DC is not set (e.g. '05' for the Singapore data center - check your MoEngage "
            "dashboard URL, dashboard-0X.moengage.com tells you X)."
        )
    return f"https://api-{dc}.moengage.com"


def _auth_header(brand: Optional[str] = None) -> dict:
    creds = _creds_for_brand(brand)
    workspace_id, data_api_key = creds["workspace_id"], creds["data_api_key"]
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
    return brand_configured(None) and bool(_creds_for_brand(None)["campaign_api_key"])


def _campaign_auth_headers(brand: Optional[str] = None) -> dict:
    creds = _creds_for_brand(brand)
    workspace_id, campaign_api_key = creds["workspace_id"], creds["campaign_api_key"]
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
            resp = _request_with_retry("POST", url, headers=headers, json=payload)
            if resp.status_code == 401:
                raise RuntimeError(
                    "MoEngage rejected the Campaigns Search request (401) - check "
                    "MOENGAGE_WORKSPACE_ID/MOENGAGE_CAMPAIGN_API_KEY."
                )
            resp.raise_for_status()
            batch = resp.json()
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


_CAMPAIGN_STATS_ID_LIMIT = 10  # real, documented server-side max for campaign_ids per call
_CAMPAIGN_STATS_MAX_DAYS = 30  # real, documented server-side max date-range span per call


def get_campaign_stats(
    campaign_ids: list,
    start_date: str,
    end_date: str,
    attribution_type: str = "VIEW_THROUGH",
    metric_type: str = "TOTAL",
    brand: Optional[str] = None,
) -> dict:
    """POST /core-services/v1/campaign-stats - the ONE real MoEngage surface
    that returns actual message performance numbers (sent/delivered/opened/
    adjusted_open/click, plus conversion_goal_stats with real revenue) for a
    specific set of real campaign_ids - confirmed live. This is genuinely
    different from every other function in this module: search_campaigns()
    (Campaigns Search API) returns config only, get_flow() (Flows API)
    returns structure only, and neither the Analytics Dashboards API nor the
    Funnels API can be pointed at an arbitrary campaign_id at all. A flow's
    own ACTION nodes (get_flow()'s structure) already carry the real
    campaign_id each one sends - that's the bridge from "flow node" to
    "real performance numbers" this function provides.

    Reuses the Campaigns auth (_campaign_auth_headers - MOENGAGE_CAMPAIGN_API_KEY
    + MOE-APPKEY, confirmed live same as search_campaigns/search_flows).

    Real, documented server-side limits, both enforced by chunking here
    rather than left for the caller to hit: max 10 campaign_ids per call
    (extra ids beyond that are silently split into further calls and
    merged), and max 30-day span per call (a longer start/end range is
    split into consecutive <=30-day windows and the per-campaign
    performance_stats/conversion_goal_stats/delivery_funnel counts are
    summed across them - rates like ctr/open_rate are NOT summable, so
    per-window rate fields are dropped from the merged result; recompute
    a rate from the summed counts if you need one).

    attribution_type/metric_type are passed straight through - MoEngage's
    own real, documented enum values (VIEW_THROUGH/CLICK_THROUGH/
    IN_SESSION/TOTAL_CONVERSIONS/CLICK_CONVERSIONS for attribution_type,
    TOTAL/UNIQUE for metric_type). Match whatever the MoEngage UI is set
    to if you need the numbers to line up with what a human sees there.

    Returns {campaign_id: <real per-campaign response, keyed by platform ->
    locale -> variation>} - the real nested shape MoEngage returns, summed
    across date-range chunks when chunking was needed. Callers should
    reach into variations.all_variations.performance_stats /
    .conversion_goal_stats for the numbers, same as MoEngage's own UI."""
    if not campaign_ids:
        return {}

    def _date_chunks(start: str, end: str) -> list:
        from datetime import datetime, timedelta

        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        chunks = []
        cursor = start_dt
        while cursor <= end_dt:
            chunk_end = min(cursor + timedelta(days=_CAMPAIGN_STATS_MAX_DAYS - 1), end_dt)
            chunks.append((cursor.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
            cursor = chunk_end + timedelta(days=1)
        return chunks

    def _sum_into(target: dict, source: dict) -> None:
        """Merges one date-chunk's real numeric fields into the running
        total in place - int/float fields are summed, everything else
        (goal_name, non-numeric) is kept from whichever chunk set it
        first. Rate fields get summed too here but are deleted from the
        final merged dict below since a summed rate is meaningless."""
        for key, value in source.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                target[key] = target.get(key, 0) + value
            elif isinstance(value, dict):
                _sum_into(target.setdefault(key, {}), value)
            elif key not in target:
                target[key] = value

    _RATE_FIELD_SUFFIXES = ("_rate", "rate", "ctr", "ctor")

    def _strip_unsummable_rates(node) -> None:
        """Rates (ctr/open_rate/delivery_rate/cvr/...) don't survive
        summing across date chunks - only applied when chunking actually
        happened (a single-chunk call returns MoEngage's real rate values
        untouched)."""
        if isinstance(node, dict):
            for key in list(node.keys()):
                if isinstance(node[key], dict):
                    _strip_unsummable_rates(node[key])
                elif isinstance(node[key], (int, float)) and any(
                    key == suf or key.endswith(suf) for suf in _RATE_FIELD_SUFFIXES
                ):
                    del node[key]

    url = f"{_base_url(brand)}/core-services/v1/campaign-stats"
    headers = _campaign_auth_headers(brand)
    date_chunks = _date_chunks(start_date, end_date)

    merged: dict = {}
    for chunk_start, chunk_end in date_chunks:
        for i in range(0, len(campaign_ids), _CAMPAIGN_STATS_ID_LIMIT):
            id_batch = campaign_ids[i : i + _CAMPAIGN_STATS_ID_LIMIT]
            payload = {
                "request_id": str(uuid.uuid4()),
                "campaign_ids": id_batch,
                "start_date": chunk_start,
                "end_date": chunk_end,
                "attribution_type": attribution_type,
                "metric_type": metric_type,
            }
            # Real, documented limit: 100 calls/min per workspace on this
            # endpoint - a bulk, many-campaign export can realistically hit
            # it, unlike the single-flow case this client was first built
            # for; _request_with_retry handles the 429 backoff/retry.
            resp = _request_with_retry("POST", url, headers=headers, json=payload)
            if resp.status_code == 401:
                raise RuntimeError(
                    "MoEngage rejected the Campaign Stats request (401) - check "
                    "MOENGAGE_WORKSPACE_ID/MOENGAGE_CAMPAIGN_API_KEY."
                )
            resp.raise_for_status()
            resp_json = resp.json()

            for campaign_id, entries in resp_json.get("data", {}).items():
                target_entries = merged.setdefault(campaign_id, [])
                for j, entry in enumerate(entries):
                    if j >= len(target_entries):
                        target_entries.append({})
                    _sum_into(target_entries[j], entry)

    if len(date_chunks) > 1:
        for entries in merged.values():
            for entry in entries:
                _strip_unsummable_rates(entry)

    return merged


def search_flows(name: Optional[str] = None, status: Optional[list] = None, limit: int = 20, brand: Optional[str] = None) -> list:
    """POST /v5/flows/search - real MoEngage Flows (early-access API), the
    flow's own metadata only (name/status/version/tags) - NOT its
    structure or any performance numbers, see get_flow()/the module-level
    note below for why stats aren't available here at all. Reuses the
    Campaigns auth (confirmed live: works with MOENGAGE_CAMPAIGN_API_KEY,
    matching the real MoEngage docs - "Flows reuse the Campaigns
    permissions"). One page only (`limit`, real confirmed server-side max
    100 - see list_all_flows() below for "give me every real flow", which
    this function does NOT do on its own)."""
    url = f"{_base_url(brand)}/v5/flows/search"
    headers = _campaign_auth_headers(brand)
    payload = {"limit": limit}
    if name:
        payload["name"] = name
    if status:
        payload["status"] = status
    resp = _request_with_retry("POST", url, headers=headers, json=payload)
    resp.raise_for_status()
    return resp.json().get("data", {}).get("flows", [])


def list_all_flows(status: Optional[list] = None, brand: Optional[str] = None) -> list:
    """Every real flow in the account, fully paginated - confirmed live:
    /v5/flows/search returns real cursor pagination (`has_more` +
    `next_cursor` in `data`, undocumented in the endpoint's own written
    spec but present in every real response), capped at the real
    server-side max of 100 per page. 150 real flows confirmed live in this
    workspace across ACTIVE/PAUSED/STOPPED/RETIRED/DRAFT - `status` (same
    real enum search_flows takes) filters server-side same as there; omit
    it for genuinely every flow regardless of status."""
    url = f"{_base_url(brand)}/v5/flows/search"
    headers = _campaign_auth_headers(brand)
    all_flows = []
    cursor = None
    while True:
        payload = {"limit": 100}
        if status:
            payload["status"] = status
        if cursor:
            payload["cursor"] = cursor
        resp = _request_with_retry("POST", url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        all_flows.extend(data.get("flows", []))
        if not data.get("has_more") or not data.get("next_cursor"):
            break
        cursor = data["next_cursor"]
    return all_flows


def get_flow(flow_id: str, version_no: Optional[int] = None, brand: Optional[str] = None) -> dict:
    """GET /v5/flows/{flow_id} - a real flow's full structure: every node
    (trigger/condition/split/branch/action/control), its real config
    (trigger event filters, condition event filters, campaign_ids), and
    branching (child_stage_ids). CONFIRMED LIVE (read the real OpenAPI
    spec directly, not assumed): this endpoint has NO performance/stats
    fields anywhere - no entered/exited/drop-off/conversion numbers, at
    any depth. There is no documented MoEngage API that returns a flow's
    own node-level stats (the "Entered/Current/Drops/Exits" numbers shown
    in the MoEngage UI's flow canvas) - moengage_export/flow_funnel_
    approximation.py exists specifically to approximate that gap using
    this real structure plus the separate Funnels Query API below."""
    url = f"{_base_url(brand)}/v5/flows/{flow_id}"
    headers = _campaign_auth_headers(brand)
    params = {"version_no": version_no} if version_no else None
    resp = _request_with_retry("GET", url, headers=headers, params=params)
    if resp.status_code == 401:
        raise RuntimeError(
            "MoEngage rejected the Flows request (401) - check "
            "MOENGAGE_WORKSPACE_ID/MOENGAGE_CAMPAIGN_API_KEY."
        )
    resp.raise_for_status()
    return resp.json().get("data", {})


def register_funnel_query(payload: dict) -> str:
    """POST /v5/analytics/funnels - registers an async Funnels analysis
    (step-by-step conversion across an ordered event sequence YOU define -
    this is a general-purpose analysis tool, not tied to any flow_id).
    Confirmed live: uses the same auth as the Analytics Dashboards API
    above (_auth_header, MOENGAGE_DATA_API_KEY), not the Campaigns key
    Flows/Campaigns Search need. Returns the real request_id to poll."""
    url = f"{_base_url()}/v5/analytics/funnels"
    headers = _auth_header()
    headers["Content-Type"] = "application/json"
    resp = requests.post(url, headers=headers, json=payload, timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()["data"]["request_id"]


def get_query_status(request_id: str) -> str:
    """GET /v5/analytics/query/{request_id}/status - PENDING/PROCESSING
    while still running, SUCCESSFUL or FAILED when done."""
    url = f"{_base_url()}/v5/analytics/query/{request_id}/status"
    resp = requests.get(url, headers=_auth_header(), timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()["data"]["status"]


def get_query_results(request_id: str) -> list:
    """GET /v5/analytics/query/{request_id}/results - the resolved series
    for a SUCCESSFUL query. Same real row shape as the Analytics
    Dashboards API's chart data (step/metric/granularity/splitby/...) -
    confirmed live, these two APIs share the same underlying result
    format."""
    url = f"{_base_url()}/v5/analytics/query/{request_id}/results"
    resp = requests.get(url, headers=_auth_header(), timeout=_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json().get("data", [])


def run_funnel_query(payload: dict, poll_interval: float = 3.0, max_wait: float = 120.0) -> list:
    """Submit + poll + fetch in one call - the real async workflow every
    Analytics Query endpoint requires, wrapped for the common case of
    just wanting the final rows. Raises RuntimeError on FAILED or on
    exceeding max_wait (a real query completed in a few seconds on every
    live test so far; max_wait is generous headroom, not a tuned SLA)."""
    request_id = register_funnel_query(payload)
    waited = 0.0
    while waited < max_wait:
        status = get_query_status(request_id)
        if status == "SUCCESSFUL":
            return get_query_results(request_id)
        if status == "FAILED":
            raise RuntimeError(f"Funnel query {request_id} failed.")
        time.sleep(poll_interval)
        waited += poll_interval
    raise RuntimeError(f"Funnel query {request_id} did not complete within {max_wait}s.")


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
