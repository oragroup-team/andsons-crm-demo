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

import requests
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

logger = logging.getLogger("slack_integration")

_MENTION_RE = re.compile(r"^\s*<@[A-Z0-9]+>\s*[:,]?\s*", re.IGNORECASE)


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


def run_in_background(target, *args, **kwargs) -> None:
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    thread.start()


def strip_mention(text: str) -> str:
    """Remove the leading '<@BOTID>' Slack renders at the start of an
    app_mention event's text, leaving just what the person actually said."""
    return _MENTION_RE.sub("", text or "").strip()


def download_slack_file(file_info: dict, bot_token: str) -> bytes:
    """Download an uploaded file's real bytes from Slack. `url_private` (on
    the `files` array of an app_mention event) is only fetchable with the
    bot's own token in the Authorization header - a plain GET (e.g. what a
    browser would do while logged into Slack) gets an HTML login page back,
    not the file. Needs the `files:read` scope on the bot. Raises on
    anything other than a real 200, so a bad/expired token surfaces as a
    clear error instead of silently "parsing" an HTML error page as data."""
    resp = requests.get(
        file_info["url_private"], headers={"Authorization": f"Bearer {bot_token}"}, timeout=20
    )
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


# --- In-memory per-thread session state (email feedback loops) -------------
# Keyed by (channel, thread_ts). Resets on process restart - fine for a demo
# on Cloud Run, which scales to zero on idle anyway.

_EMAIL_SESSIONS: dict = {}


def get_email_session(channel: str, thread_ts: str) -> dict:
    return _EMAIL_SESSIONS.get((channel, thread_ts))


def save_email_session(channel: str, thread_ts: str, session: dict) -> None:
    _EMAIL_SESSIONS[(channel, thread_ts)] = session


# --- In-memory per-thread conversation history (analytics bot context) -----
# Same tradeoff as above. Capped per-thread so a very long-running thread
# doesn't grow the prompt unboundedly.

_ANALYTICS_SESSIONS: dict = {}
_ANALYTICS_HISTORY_LIMIT = 6


def get_analytics_history(channel: str, thread_ts: str) -> list:
    return _ANALYTICS_SESSIONS.get((channel, thread_ts), [])


def append_analytics_exchange(channel: str, thread_ts: str, question: str, answer: str) -> None:
    key = (channel, thread_ts)
    history = _ANALYTICS_SESSIONS.get(key, [])
    history.append({"question": question, "answer": answer})
    _ANALYTICS_SESSIONS[key] = history[-_ANALYTICS_HISTORY_LIMIT:]


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
    source = "live BigQuery"

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
