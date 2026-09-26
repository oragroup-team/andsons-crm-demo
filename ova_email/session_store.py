"""Persistent, cross-instance session storage for the Slack @-mention bots
(email feedback threads, analytics conversation history) - backed by
Firestore, not an in-memory dict.

Why this exists: the original implementation kept sessions in a plain
module-level dict. That works fine for a single long-lived process, but
breaks down hard on Cloud Run:
- Every deploy replaces the running instance(s) entirely - an in-memory
  dict is wiped on every single redeploy, and this service gets redeployed
  often during active development.
- Cloud Run can (and does, maxScale=20 here) run multiple concurrent
  instances - there is no guarantee two requests in the same Slack thread
  land on the same instance, so even between deploys the dict on instance A
  is invisible to instance B.
Both of these showed up live as "the bot has no memory of the previous
draft" even though the code logic itself was correct - the session data
just weren't there to find. Firestore is shared across every instance and
every deploy, which fixes this at the actual architectural level rather
than papering over it (e.g. pinning to a single instance would still lose
everything on every redeploy).

Collection layout: one document per (channel, thread_ts) pair, in
`ova_email_sessions` or `ova_analytics_sessions`, with the doc ID built from
both so it's directly addressable without a query. Ported from the andSons
backend's session_store.py unchanged except for one deliberate difference:
every collection name here carries an `ova_` prefix. Real reason, not
cosmetic - this app can share the andSons backend's own Firestore project
(same `FIRESTORE_PROJECT_ID`), but `ova_learned_rules` and
`ova_synthesized_flows` in particular are SHARED, brand-wide documents, not
per-thread ones - without the prefix, a standing rule distilled from OVA
feedback would silently leak into andSons' Copywriter prompts (and vice
versa) the next time either bot ran, since both would otherwise read/write
the exact same `learned_rules`/`standing_rules` document.

REST, NOT the google-cloud-firestore client library (found live, 2026-08-25):
every single call through google.cloud.firestore.Client - get, set, and
delete alike - was failing with "400 Invalid database id %28default%29"
(the URL-encoded form of "(default)", this project's real, correctly-
provisioned database - confirmed via `gcloud firestore databases list`).
Root-caused by direct comparison: a raw REST call to the exact same
document path, same project, same credentials, works perfectly (confirmed
locally with a real get/set/delete round-trip); only the Python client
library's gRPC transport mishandles the "(default)" database segment when
building the x-goog-request-params request metadata. Every function below
was silently degrading to its safe fallback (no session, no history, no
rules) on EVERY call, in production, not just in local testing - this is
the real cause behind "the bot has no memory of what we already said in
this thread" reports. Talking to the Firestore REST API directly via
google-auth's AuthorizedSession (still using the exact same Application
Default Credentials resolution and FIRESTORE_PROJECT_ID override as
before) sidesteps the broken gRPC path entirely.
"""
import logging
import os
from typing import Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession

logger = logging.getLogger("session_store")

_ANALYTICS_HISTORY_LIMIT = 6

# The credentials used here (GOOGLE_APPLICATION_CREDENTIALS, the same
# BigQuery service account key) are a cross-project grant - the key file's
# OWN embedded project (ora-bigquery, where that service account "lives")
# is not where this app's Firestore database actually is. Application
# Default Credentials resolves the project from the credential file by
# default, which pointed the client at the wrong project entirely -
# FIRESTORE_PROJECT_ID makes the actual target project explicit instead of
# relying on that inference. Defaults to this app's own Cloud Run project.
_DEFAULT_PROJECT = "crm-mail-automation-dev"

# Real bug this scope fixes (found live, minutes after the REST rewrite
# below shipped): google.auth.default() with NO scopes argument works fine
# locally against a developer's own `gcloud auth application-default
# login` credentials (those already carry a broad default scope set), but
# in Cloud Run - where GOOGLE_APPLICATION_CREDENTIALS points at the
# service-account JSON key instead - it attaches NO scope at all to
# service-account credentials unless one is explicitly requested,
# producing "invalid_scope: Invalid OAuth scope or ID token audience
# provided" on every single call. The gRPC client library used to attach
# this internally; talking to the REST API directly means this app has to
# ask for it itself now.
_FIRESTORE_SCOPES = ["https://www.googleapis.com/auth/datastore"]

_session: Optional[AuthorizedSession] = None
_base_url: Optional[str] = None


def _get_session() -> AuthorizedSession:
    global _session, _base_url
    if _session is None:
        project = os.environ.get("FIRESTORE_PROJECT_ID", _DEFAULT_PROJECT)
        credentials, _ = google.auth.default(scopes=_FIRESTORE_SCOPES)
        _session = AuthorizedSession(credentials)
        _base_url = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents"
    return _session


def _doc_url(collection: str, doc_id: str) -> str:
    _get_session()  # ensures _base_url is populated
    return f"{_base_url}/{collection}/{doc_id}"


def _doc_id(channel: str, thread_ts: str) -> str:
    # Firestore document IDs can't contain "/" - Slack channel/ts values
    # never do, but guard against it rather than let a write fail obscurely.
    return f"{channel}__{thread_ts}".replace("/", "_")


# --- Firestore REST <-> plain Python value conversion ------------------
# The REST API represents every field as a typed wrapper object
# ({"stringValue": ...}, {"arrayValue": {"values": [...]}}, etc.) instead
# of a plain JSON value - the google-cloud-firestore client normally hides
# this. These two functions are the whole of that translation, recursively,
# for the plain str/int/float/bool/None/list/dict shapes this app's
# sessions actually use.


def _to_value(v):
    if v is None:
        return {"nullValue": None}
    if isinstance(v, bool):
        return {"booleanValue": v}
    if isinstance(v, int):
        return {"integerValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v}
    if isinstance(v, list):
        return {"arrayValue": {"values": [_to_value(x) for x in v]}}
    if isinstance(v, dict):
        return {"mapValue": {"fields": {k: _to_value(x) for k, x in v.items()}}}
    return {"stringValue": str(v)}  # last resort - never raise on an odd type


def _from_value(v: dict):
    if "nullValue" in v:
        return None
    if "booleanValue" in v:
        return v["booleanValue"]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "doubleValue" in v:
        return v["doubleValue"]
    if "stringValue" in v:
        return v["stringValue"]
    if "arrayValue" in v:
        return [_from_value(x) for x in v.get("arrayValue", {}).get("values", [])]
    if "mapValue" in v:
        return {k: _from_value(x) for k, x in v.get("mapValue", {}).get("fields", {}).items()}
    if "timestampValue" in v:
        return v["timestampValue"]
    return None


def _get_doc(collection: str, doc_id: str) -> Optional[dict]:
    """Real GET - returns the doc's fields as a plain dict, or None if it
    doesn't exist. Raises on any other real error (caller's try/except
    handles logging + fallback, same pattern as every function below)."""
    resp = _get_session().get(_doc_url(collection, doc_id))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    fields = resp.json().get("fields", {})
    return {k: _from_value(v) for k, v in fields.items()}


def _set_doc(collection: str, doc_id: str, data: dict) -> None:
    """Real PATCH with no updateMask - this fully replaces the document's
    fields, the same overwrite semantics as the old client's .set()."""
    payload = {"fields": {k: _to_value(v) for k, v in data.items()}}
    resp = _get_session().patch(_doc_url(collection, doc_id), json=payload)
    resp.raise_for_status()


def _delete_doc(collection: str, doc_id: str) -> None:
    resp = _get_session().delete(_doc_url(collection, doc_id))
    if resp.status_code != 404:  # already gone is fine, not an error
        resp.raise_for_status()


def get_email_session(channel: str, thread_ts: str) -> Optional[dict]:
    try:
        return _get_doc("ova_email_sessions", _doc_id(channel, thread_ts))
    except Exception:
        logger.exception("Failed to read email session from Firestore - treating as no session.")
        return None


def save_email_session(channel: str, thread_ts: str, session: dict) -> None:
    try:
        _set_doc("ova_email_sessions", _doc_id(channel, thread_ts), session)
    except Exception:
        logger.exception("Failed to save email session to Firestore - this draft's feedback thread will not persist.")


def get_analytics_history(channel: str, thread_ts: str) -> list:
    try:
        doc = _get_doc("ova_analytics_sessions", _doc_id(channel, thread_ts))
        return (doc or {}).get("history", [])
    except Exception:
        logger.exception("Failed to read analytics history from Firestore - treating as no history.")
        return []


def append_analytics_exchange(channel: str, thread_ts: str, question: str, answer: str) -> None:
    try:
        history = get_analytics_history(channel, thread_ts)
        history.append({"question": question, "answer": answer})
        _set_doc("ova_analytics_sessions", _doc_id(channel, thread_ts), {"history": history[-_ANALYTICS_HISTORY_LIMIT:]})
    except Exception:
        logger.exception("Failed to save analytics exchange to Firestore - this thread's context will not persist.")


# --- Pending (not-yet-resolved) email requests ------------------------------
# Real bug this fixes: before a draft exists, each reply in a thread was
# classified from ONLY its own text - "write me an abandon-cart email" ->
# (missing flow) -> "plan not purchased" (a reply with no name/flow
# reference of its own) -> re-classified alone, resolves nothing -> asks
# again -> ping-pongs forever. This accumulates every message in the thread
# so far (until a flow resolves into a real draft), so classification always
# runs against the FULL conversation, not one isolated fragment of it.


def get_pending_email_request(channel: str, thread_ts: str) -> list:
    try:
        doc = _get_doc("ova_pending_email_requests", _doc_id(channel, thread_ts))
        return (doc or {}).get("texts", [])
    except Exception:
        logger.exception("Failed to read pending email request from Firestore - treating as no prior context.")
        return []


def save_pending_email_request(channel: str, thread_ts: str, texts: list) -> None:
    try:
        _set_doc("ova_pending_email_requests", _doc_id(channel, thread_ts), {"texts": texts})
    except Exception:
        logger.exception("Failed to save pending email request to Firestore - this thread may re-ask for info already given.")


def clear_pending_email_request(channel: str, thread_ts: str) -> None:
    try:
        _delete_doc("ova_pending_email_requests", _doc_id(channel, thread_ts))
    except Exception:
        logger.exception("Failed to clear pending email request from Firestore (non-fatal - it'll just get overwritten next time).")


# --- Learned rules ------------------------------------------------------
# Real pipeline capability this mirrors: the n8n workflow's "Skill
# Distiller" turns every piece of human feedback into a standing rule in
# a sheet tab called Learned_Rules, which every agent's prompt reads on
# every future run ("Compile Lessons"). This system had no equivalent at
# all - every session started from zero, with no memory of any past
# correction. One shared document (not one per thread) since a rule
# learned from one flow's feedback should apply to every future flow,
# same as the real system's single shared sheet.
_LEARNED_RULES_DOC = "standing_rules"
_MAX_LEARNED_RULES = 50  # oldest evicted first - keeps the prompt injection bounded


def get_learned_rules() -> list:
    try:
        doc = _get_doc("ova_learned_rules", _LEARNED_RULES_DOC)
        return (doc or {}).get("rules", [])
    except Exception:
        logger.exception("Failed to read learned rules from Firestore - treating as no standing rules yet.")
        return []


def add_learned_rule(rule: str) -> None:
    try:
        rules = get_learned_rules()
        rules.append(rule)
        _set_doc("ova_learned_rules", _LEARNED_RULES_DOC, {"rules": rules[-_MAX_LEARNED_RULES:]})
    except Exception:
        logger.exception("Failed to save a learned rule to Firestore - this correction won't carry forward to future runs.")


def set_learned_rules(rules: list) -> None:
    """Bulk replace the whole standing-rules list - used for maintenance
    (removing a bad/dead/duplicate rule) rather than the normal one-at-a-
    time append add_learned_rule() does."""
    try:
        _set_doc("ova_learned_rules", _LEARNED_RULES_DOC, {"rules": rules[-_MAX_LEARNED_RULES:]})
    except Exception:
        logger.exception("Failed to overwrite learned rules in Firestore.")


# --- Synthesized flows ---------------------------------------------------
# Real bug this fixes, live-caught: head_of_crm_agent.synthesize_flow_for_
# signal() used to only ever register a brand-new flow it designed into
# THIS process's own in-memory FLOW_BY_SLUG dict - Cloud Run runs multiple
# concurrent instances (and recycles them over time, same as the session-
# storage problem this file's own module docstring already documents), so
# a synthesized flow created on one instance was invisible to every other
# one. A person asking to revise that exact flow minutes later landed on
# a different instance and got a bare KeyError. This mirrors the session-
# storage fix exactly, just for the flow catalog instead of a per-thread
# session - see flows.py's _LazyFlowCatalog for the read side, which
# checks here transparently on a cache miss.


def get_synthesized_flow(slug: str) -> Optional[dict]:
    try:
        return _get_doc("ova_synthesized_flows", slug)
    except Exception:
        logger.exception("Failed to read synthesized flow %r from Firestore.", slug)
        return None


def save_synthesized_flow(slug: str, flow: dict) -> None:
    try:
        _set_doc("ova_synthesized_flows", slug, flow)
    except Exception:
        logger.exception("Failed to save synthesized flow %r to Firestore - it will only exist on this instance.", slug)
