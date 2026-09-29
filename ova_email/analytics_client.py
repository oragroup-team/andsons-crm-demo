"""HTTP client for the shared CRM Analytics agent.

REAL ARCHITECTURE DECISION: OvaEmail does NOT get its own copy of
analytics_agent.py (150KB, tightly coupled to BigQuery, MoEngage, and the
real per-brand warehouse credentials in backend/warehouse_client.py). There
is ONE analytics agent - the one already powering @andSons Analytics - and
both @andSons Email and @OvaEmail call into that SAME instance over HTTP,
via the andSons backend's existing `POST /ask` endpoint
(backend/app.py:ask_endpoint, unchanged - already takes {"question": "..."}
and returns ask_analytics()'s own result dict directly, so this client's
return shape matches backend/agents/analytics_agent.ask_analytics() exactly).

Why HTTP, not a Python import: OvaEmail is a genuinely separate Flask app /
Cloud Run service (ova_email/), with no BigQuery credentials, no MoEngage
workspace config, and none of the per-brand warehouse secrets configured in
its own environment - importing analytics_agent.py directly would either
crash on missing config or require duplicating every one of those real
credentials into a second service, doubling the surface area for exactly
the kind of drift (two copies of one complex, incident-hardened file
silently diverging) this codebase has been burned by before. One shared
service, one real set of credentials, called over the network, is the
lower-risk shape.

REAL, KNOWN LIMITATION (not fixed here - out of scope for a client file):
analytics_agent.py's own schema notes default every query to
`Brand = 'AndSons'` unless the question explicitly names another brand
("Ova" is a recognised value) - every question this client sends is
therefore built to say "OVA" explicitly, every time (see
agents/insight_agent.py). BUT a few of that file's own deterministic
verification safety-nets (`_verify_campaign_family_total`,
`_verify_flow_orders_answer`) hardcode `Brand='AndSons'` in their own
re-check SQL regardless of what the original question asked - if an OVA
question's answer happens to route through one of those specific
verification paths, the verification itself could silently re-check
against the wrong brand's data. This was flagged, not fixed, when this
client was added - fixing it means editing analytics_agent.py's own
verification internals with real, dedicated testing, which is a separate
piece of work from wiring the email agents up to it.
"""
import logging
import os

import requests

logger = logging.getLogger("analytics_client")

# The andSons backend - the one deployed instance of the analytics agent.
# Override via env var if the service is ever redeployed under a different
# URL (see ova_email/cloudrun-env.yaml / .env.example).
ANALYTICS_SERVICE_URL = os.environ.get(
    "ANALYTICS_SERVICE_URL", "https://andsons-crm-demo-762730591203.us-central1.run.app"
)

# A real, complex multi-table analytics question can genuinely take minutes
# (see the root deploy.sh's own comment on why /ask's timeout was raised to
# 900s) - this client's own timeout is set generously to match, rather than
# a shorter default that would fail a real, in-progress query as if it were
# broken.
_REQUEST_TIMEOUT_SECONDS = 300.0

_UNAVAILABLE_ANSWER = (
    "The shared analytics agent could not be reached just now, so this could not be checked "
    "against real data. Do not invent a number or finding in its place."
)


def ask_analytics(question: str) -> dict:
    """Same return shape as backend/agents/analytics_agent.ask_analytics():
    {"answer": str, "sql_query": Optional[str], "verified": bool,
    "data_source": str, "moengage_used": bool}. Fails soft (never raises) -
    a network/service problem returns a plain, honest "couldn't reach it"
    answer with verified=False, exactly like a query that genuinely
    couldn't be verified - callers already have to handle that case, so
    there's no new failure mode for them to special-case."""
    url = f"{ANALYTICS_SERVICE_URL.rstrip('/')}/ask"
    try:
        response = requests.post(url, json={"question": question}, timeout=_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        data = response.json()
    except Exception as exc:  # noqa: BLE001 - network/HTTP/JSON failure, never crash the caller
        logger.warning("Analytics service call failed (%s) - proceeding without a real finding.", exc)
        return {"answer": _UNAVAILABLE_ANSWER, "sql_query": None, "verified": False, "data_source": "unavailable", "moengage_used": False}

    if "error" in data:
        logger.warning("Analytics service returned an error: %s", data["error"])
        return {"answer": _UNAVAILABLE_ANSWER, "sql_query": None, "verified": False, "data_source": "unavailable", "moengage_used": False}

    return {
        "answer": data.get("answer", _UNAVAILABLE_ANSWER),
        "sql_query": data.get("sql_query"),
        "verified": bool(data.get("verified", False)),
        "data_source": data.get("data_source", "unknown"),
        "moengage_used": bool(data.get("moengage_used", False)),
    }
