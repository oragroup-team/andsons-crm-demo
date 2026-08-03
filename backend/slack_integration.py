"""Slack integration - lets office coworkers ask analytics questions and
generate CRM emails directly from Slack via slash commands, for testing.

Slash commands must be acknowledged within 3 seconds, so each command
immediately returns a short placeholder response and does the real work
(LLM calls, which can take several seconds) in a background thread, then
posts the final result to Slack's one-time `response_url` when it's done.

No bot token is needed for this - a slash command's `response_url` lets the
app post back without any OAuth install/scopes, so the only Slack-side
secret required is the app's Signing Secret (used to verify a request
genuinely came from Slack, not an impersonator hitting these endpoints).
"""
import logging
import os
import threading

import requests
from slack_sdk.signature import SignatureVerifier

logger = logging.getLogger("slack_integration")


def _signing_secret() -> str:
    return os.environ.get("SLACK_SIGNING_SECRET", "")


def slack_configured() -> bool:
    return bool(_signing_secret())


def verify_slack_request(request) -> bool:
    """Confirm a request actually came from Slack (HMAC-signed with the
    app's Signing Secret) before doing any work on its behalf."""
    secret = _signing_secret()
    if not secret:
        logger.warning("SLACK_SIGNING_SECRET not set - rejecting Slack request.")
        return False
    verifier = SignatureVerifier(secret)
    return verifier.is_valid_request(request.get_data(), request.headers)


def post_result_to_slack(response_url: str, payload: dict) -> None:
    try:
        requests.post(response_url, json=payload, timeout=10)
    except Exception:
        logger.exception("Failed to post result back to Slack response_url.")


def run_in_background(target, *args, **kwargs) -> None:
    thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
    thread.start()


# --- Slack Block Kit formatting --------------------------------------------


def format_email_blocks(result: dict, flow_name: str, first_name: str) -> list:
    email = result["email"]
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{flow_name} -> {first_name}"},
        }
    ]

    if email.get("hero_image_url"):
        blocks.append(
            {
                "type": "image",
                "image_url": email["hero_image_url"],
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
    retries = result.get("retries_used", 0)
    blocks.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"{status} - {retries} automatic revision(s)"}],
        }
    )

    return blocks


def format_analytics_blocks(result: dict, question: str) -> list:
    verified_text = "grounded in query result" if result.get("verified") else "unverified"
    source = "live BigQuery" if result.get("data_source") == "bigquery" else "mock data"

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
