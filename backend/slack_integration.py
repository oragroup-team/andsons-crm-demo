"""Slack integration - lets office coworkers use both agents directly from
Slack, for testing. Two interaction styles:

1. Slash commands (/andsons-email, /andsons-ask) - original, simplest style.
   No bot token needed; Slack's one-time `response_url` lets the app post
   back without any OAuth install/scopes.
2. @-mentioning a bot in a channel and talking to it in plain language -
   two separate Slack apps/bots (one for email generation, one for
   analytics), each with their own name, Signing Secret, and Bot Token.
   This style needs the Events API (app_mention) and a real bot token,
   since there's no response_url for events - replies go through
   chat.postMessage. Feedback on an email draft is tracked by Slack thread
   (thread_ts), in-memory, so replying in the same thread continues that
   draft instead of starting a new one.

Both styles ack within Slack's 3-second window and do the real work (LLM
calls, which can take several seconds) in a background thread.
"""
import io
import logging
import os
import re
import threading
from typing import Optional

import requests
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

logger = logging.getLogger("slack_integration")

_MENTION_RE = re.compile(r"\s*<@[A-Z0-9]+>\s*[:,]?\s*", re.IGNORECASE)


def _signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "")


def slack_configured() -> bool:
    return bool(_signing_secret())


def verify_slack_request(request, secret: str = None) -> bool:
    """Confirm a request actually came from Slack (HMAC-signed with the
    given Signing Secret, or SLACK_SIGNING_SECRET by default) before doing
    any work on its behalf."""
    secret = secret if secret is not None else _signing_secret()
    if not secret:
        logger.warning("No Slack signing secret configured - rejecting request.")
        return False
    verifier = SignatureVerifier(secret)
    return verifier.is_valid_request(request.get_data(), request.headers)


def post_result_to_slack(response_url: str, payload: dict) -> None:
    try:
        requests.post(response_url, json=payload, timeout=10)
    except Exception:
        logger.exception("Failed to post result back to Slack response_url.")


def post_message(bot_token: str, channel: str, thread_ts: str = None, text: str = "", blocks: list = None) -> None:
    """Post a message to a channel via chat.postMessage (needs a real bot
    token - used by the mention-based bots, which have no response_url)."""
    try:
        client = WebClient(token=bot_token)
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text or " ", blocks=blocks)
    except Exception:
        logger.exception("Failed to post message via chat.postMessage.")


def post_rendered_email(bot_token: str, channel: str, thread_ts: str, image, filename: str, comment: str) -> None:
    """Upload a rendered email PNG (a PIL Image, from email_image_renderer.py)
    directly to Slack via files_upload_v2, rather than serving it from a URL
    this app hosts. Cloud Run instances are stateless/ephemeral with no
    shared disk - a per-request generated file saved locally and referenced
    by URL (like the static hero-image bank, which works because those files
    are baked into the Docker image at build time) could easily 404 if a
    later request lands on a different instance. Uploading the bytes
    straight to Slack sidesteps that entirely - Slack hosts the image."""
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)
    try:
        client = WebClient(token=bot_token)
        client.files_upload_v2(
            channel=channel,
            thread_ts=thread_ts,
            file=buf,
            filename=filename,
            initial_comment=comment,
        )
    except Exception as exc:
        # A silent failure here (log only, no Slack message) is a real dead
        # end for whoever's waiting - they see nothing at all with no way to
        # know something went wrong (this is exactly what happened with the
        # missing files:write scope). Always leave a visible trace in the
        # thread, even if it's just "something broke" plus the draft text.
        logger.exception("Failed to upload rendered email image to Slack.")
        post_message(
            bot_token, channel, thread_ts=thread_ts,
            text=f"Drafted the email but couldn't post the image ({exc}). {comment}",
        )


def post_file(bot_token: str, channel: str, thread_ts: str, file_bytes: bytes, filename: str, comment: str) -> None:
    """Upload arbitrary file bytes to Slack via files_upload_v2 - same
    mechanism and same statelessness reasoning as post_rendered_email()
    above, generalized to raw bytes (e.g. flow_html_export.py's approved-
    flow HTML export) instead of a PIL Image specifically."""
    buf = io.BytesIO(file_bytes)
    try:
        client = WebClient(token=bot_token)
        client.files_upload_v2(
            channel=channel,
            thread_ts=thread_ts,
            file=buf,
            filename=filename,
            initial_comment=comment,
        )
    except Exception as exc:
        logger.exception("Failed to upload file to Slack.")
        post_message(
            bot_token, channel, thread_ts=thread_ts,
            text=f"Something went wrong exporting that ({exc}).",
        )


def run_in_background(target, *args, **kwargs) -> None:
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    thread.start()


def strip_mention(text: str) -> str:
    """Remove every '<@BOTID>' Slack renders wherever one appears in an
    app_mention event's text, leaving just what the person actually said -
    not just a leading one. Real, live-caught gap this fixes: a message
    ending in a second, trailing mention out of habit ("...add some icons
    etc @andSonsEmail") left that raw mention sitting in the text, which
    then leaked into a disambiguation prompt's own example reply."""
    return re.sub(r"\s+", " ", _MENTION_RE.sub("", text or "")).strip()


def download_slack_file(file_info: dict, bot_token: str) -> bytes:
    """Download an uploaded file's real bytes from Slack. Needs the
    `files:read` scope on the bot. Raises on anything other than a real
    200, so a bad/expired token surfaces as a clear error instead of
    silently "parsing" an HTML error page as data.

    Two real, live-caught fixes here, not hypothetical:
    - `url_private_download` (when Slack provides it - not every file
      object does) is used over `url_private` when present: confirmed
      live, an image upload's `url_private` 403'd via a redirect to a
      `<workspace>.slack.com/?redir=...` login-wall URL that a bearer
      token can't satisfy - that's a browser-session-oriented URL, not
      one meant for a bot's own token. `url_private_download` is Slack's
      actual field for exactly this use case.
    - Redirects are followed manually with the Authorization header
      re-attached on every hop, rather than relying on `requests`'s
      default auto-follow, which deliberately STRIPS the Authorization
      header on any redirect to a different host (a real security
      default in that library) - silently turning the follow-up request
      into an unauthenticated one and producing exactly the 403 above."""
    url = file_info.get("url_private_download") or file_info["url_private"]
    headers = {"Authorization": f"Bearer {bot_token}"}
    resp = requests.get(url, headers=headers, timeout=20, allow_redirects=False)
    redirect_hops = 0
    while resp.is_redirect and redirect_hops < 5:
        location = resp.headers.get("Location")
        if not location:
            break
        resp = requests.get(location, headers=headers, timeout=20, allow_redirects=False)
        redirect_hops += 1
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    if "text/html" in content_type:
        raise RuntimeError(
            f"Slack returned an HTML page instead of the file {file_info.get('name')!r} - the bot token "
            "likely lacks the files:read scope, or isn't a member of this channel."
        )
    return resp.content


def is_retry(request) -> bool:
    """Slack redelivers an event if it doesn't get a fast-enough ack; since
    our real work happens in a background thread after acking, a retried
    delivery would otherwise double-process and double-post. Skip it."""
    return bool(request.headers.get("X-Slack-Retry-Num"))


# --- Per-thread session state (email feedback loops, analytics history) ----
# Backed by Firestore (session_store.py), not an in-memory dict - Cloud Run
# runs multiple instances and redeploys often, and an in-memory dict is
# invisible across both. Re-exported here so app.py's existing imports don't
# need to change.

from session_store import (  # noqa: E402
    append_analytics_exchange,
    clear_pending_email_request,
    get_analytics_history,
    get_email_session,
    get_pending_email_request,
    save_email_session,
    save_pending_email_request,
)


# --- Slack Block Kit formatting --------------------------------------------


def _absolute_url(path: str) -> str:
    """Slack's image blocks are downloaded server-side, so a relative path
    like /hero-images/adjusting.jpg (fine in a browser, which resolves it
    against the page's own origin) fails with 'downloading image failed' -
    Slack has no such origin context. Unlike some PaaS hosts, Cloud Run
    doesn't auto-inject an env var pointing at the service's own URL, so
    PUBLIC_BASE_URL must be set explicitly (see .env.example / the Cloud Run
    service's env vars). Falls back to the path itself unchanged if unset
    (e.g. running purely locally)."""
    if path.startswith("http://") or path.startswith("https://"):
        return path
    base = os.environ.get("PUBLIC_BASE_URL")
    return f"{base.rstrip('/')}{path}" if base else path


def format_email_caption(result: dict, flow_name: str, first_name: str) -> str:
    """Plain-text caption for the rendered email image upload (see
    post_rendered_email) - files_upload_v2's initial_comment, not Block Kit,
    so this stays a single mrkdwn string rather than a blocks list."""
    lines = [f"*{flow_name} -> {first_name}*"]

    insight = result.get("insight_brief")
    if insight:
        note = f"_Why this draft:_ {insight['bigquery_answer']}"
        if not insight.get("bigquery_verified"):
            note += " (directional - not fully verified)"
        lines.append(note)

    status = "Passed brand QA" if result.get("passed") else "Needs human review"
    retries = result.get("retries_used")
    lines.append(f"{status} - {retries} automatic revision(s)" if retries else status)
    lines.append("Reply in this thread with feedback to revise this draft.")
    return "\n".join(lines)


def format_flow_intro(
    flow_name: str, total_touchpoints: int, insight: Optional[dict] = None, crm_brief: Optional[dict] = None,
) -> str:
    """Posted once, before the touchpoint images, so the thread reads as
    one coherent flow instead of N unexplained images landing in a row."""
    lines = [f"*{flow_name}* - the full real sequence, {total_touchpoints} touchpoint(s):"]
    if insight:
        note = f"_Why this flow:_ {insight['bigquery_answer']}"
        if not insight.get("bigquery_verified"):
            note += " (directional - not fully verified)"
        lines.append(note)
    if crm_brief:
        s = crm_brief["structured"]
        lines.append(
            f"_Head of CRM brief:_ {s['objective']}\n"
            f"KPI: {s['kpi']} · Segment: {s['segment']} · Angle: {s['offer_angle']}"
        )
    return "\n".join(lines)


def format_flow_touchpoint_caption(touchpoint: dict, total: int, visual_qa: Optional[dict] = None) -> str:
    """Plain-text caption for one touchpoint image in a multi-touchpoint
    flow post (see post_rendered_email) - files_upload_v2's initial_comment."""
    channel_label = {"email": "Email", "whatsapp": "WhatsApp", "push": "Push"}.get(touchpoint["channel"], touchpoint["channel"].capitalize())
    lines = [f"*Step {touchpoint['n']}/{total} - {channel_label} - {touchpoint['timing']}*"]
    lines.append(touchpoint["intent"])

    status = "Passed brand QA" if touchpoint.get("passed") else "Needs human review"
    lines.append(status)
    reasons = touchpoint.get("sweeper_reasons")
    if reasons and not touchpoint.get("passed"):
        lines.append("- " + "; ".join(reasons))

    if visual_qa and visual_qa["reviewed"] and not visual_qa["looks_good"]:
        lines.append(f"Visual QA: {visual_qa['note']}")

    content_note = (touchpoint.get("content") or {}).get("note")
    if content_note:
        lines.append(f"Note: {content_note}")

    if touchpoint["n"] == 1:
        lines.append("Reply with a step number and feedback (e.g. \"2: make this shorter\") to revise that touchpoint.")
    return "\n".join(lines)


def format_email_blocks(result: dict, flow_name: str, first_name: str) -> list:
    email = result["email"]
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{flow_name} -> {first_name}"},
        }
    ]

    insight = result.get("insight_brief")
    if insight:
        note = f"*Why this draft:* {insight['bigquery_answer']}"
        if not insight.get("bigquery_verified"):
            note += " (directional - not fully verified)"
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": note}})
        blocks.append({"type": "divider"})

    if email.get("hero_image_url"):
        blocks.append(
            {
                "type": "image",
                "image_url": _absolute_url(email["hero_image_url"]),
                "alt_text": email.get("hero_headline") or email.get("hero") or "hero image",
            }
        )
        if email.get("hero_headline"):
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": f"*Overlay headline:* {email['hero_headline']}"}],
                }
            )

    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Subject:* {email['subject']}\n_{email['preheader']}_"},
        }
    )
    blocks.append({"type": "divider"})

    body_lines = [f"Hi {first_name},", ""] + list(email.get("opening_lines") or [])
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(body_lines)}})

    if email.get("what_happens_next"):
        steps = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(email["what_happens_next"]))
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*What happens next*\n{steps}"}})

    if email.get("gentle_truth_line"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": email["gentle_truth_line"]}})

    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*CTA:* {email['cta_text']}"}})

    if email.get("trust_line"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": email["trust_line"]}]})

    status = "Passed brand QA" if result.get("passed") else "Needs human review"
    retries = result.get("retries_used")
    footer = f"{status} - {retries} automatic revision(s)" if retries else status
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "Reply in this thread with feedback to revise this draft."}],
        }
    )

    return blocks


def format_analytics_blocks(result: dict, question: str) -> list:
    verified_text = "grounded in query result" if result.get("verified") else "unverified"
    source = "live BigQuery + MoEngage" if result.get("moengage_used") else "live BigQuery"

    blocks = [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Q:* {question}\n\n{result['answer']}"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{verified_text} - source: {source}"}],
        },
    ]
    if result.get("sql_query"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"```{result['sql_query']}```"}})
    return blocks
