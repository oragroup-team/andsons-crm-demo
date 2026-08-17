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
`email_sessions` or `analytics_sessions`, with the doc ID built from both
so it's directly addressable without a query.
"""
import logging
import os
from typing import Optional

from google.cloud import firestore

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

_client: Optional[firestore.Client] = None


def _get_client() -> firestore.Client:
    global _client
    if _client is None:
        project = os.environ.get("FIRESTORE_PROJECT_ID", _DEFAULT_PROJECT)
        _client = firestore.Client(project=project)
    return _client


def _doc_id(channel: str, thread_ts: str) -> str:
    # Firestore document IDs can't contain "/" - Slack channel/ts values
    # never do, but guard against it rather than let a write fail obscurely.
    return f"{channel}__{thread_ts}".replace("/", "_")


def get_email_session(channel: str, thread_ts: str) -> Optional[dict]:
    try:
        doc = _get_client().collection("email_sessions").document(_doc_id(channel, thread_ts)).get()
        return doc.to_dict() if doc.exists else None
    except Exception:
        logger.exception("Failed to read email session from Firestore - treating as no session.")
        return None


def save_email_session(channel: str, thread_ts: str, session: dict) -> None:
    try:
        _get_client().collection("email_sessions").document(_doc_id(channel, thread_ts)).set(session)
    except Exception:
        logger.exception("Failed to save email session to Firestore - this draft's feedback thread will not persist.")


def get_analytics_history(channel: str, thread_ts: str) -> list:
    try:
        doc = _get_client().collection("analytics_sessions").document(_doc_id(channel, thread_ts)).get()
        return doc.to_dict().get("history", []) if doc.exists else []
    except Exception:
        logger.exception("Failed to read analytics history from Firestore - treating as no history.")
        return []


def append_analytics_exchange(channel: str, thread_ts: str, question: str, answer: str) -> None:
    try:
        ref = _get_client().collection("analytics_sessions").document(_doc_id(channel, thread_ts))
        history = get_analytics_history(channel, thread_ts)
        history.append({"question": question, "answer": answer})
        ref.set({"history": history[-_ANALYTICS_HISTORY_LIMIT:]})
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
        doc = _get_client().collection("pending_email_requests").document(_doc_id(channel, thread_ts)).get()
        return doc.to_dict().get("texts", []) if doc.exists else []
    except Exception:
        logger.exception("Failed to read pending email request from Firestore - treating as no prior context.")
        return []


def save_pending_email_request(channel: str, thread_ts: str, texts: list) -> None:
    try:
        _get_client().collection("pending_email_requests").document(_doc_id(channel, thread_ts)).set({"texts": texts})
    except Exception:
        logger.exception("Failed to save pending email request to Firestore - this thread may re-ask for info already given.")


def clear_pending_email_request(channel: str, thread_ts: str) -> None:
    try:
        _get_client().collection("pending_email_requests").document(_doc_id(channel, thread_ts)).delete()
    except Exception:
        logger.exception("Failed to clear pending email request from Firestore (non-fatal - it'll just get overwritten next time).")
