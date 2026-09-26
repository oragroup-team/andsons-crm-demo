"""OVA Email - a Slack @-mention bot, @OvaEmail, with the same real
capability set as the andSons backend's own @andSons Email bot
(backend/app.py's slack_events_email handler): natural-language flow
requests, full multi-touchpoint flow generation (Copywriter -> Sweeper ->
Feedback loop, every real touchpoint rendered as a branded image and
posted to the thread), per-touchpoint and structural revision from plain
English feedback in the same thread, CSV/Excel file-attachment context,
a reference-image "follow this template" mode, and "APPROVED" ->
self-contained HTML export (combined + one file per step).

This file is a standalone Flask app - a separate service from the andSons
backend, per the explicit request to keep OVA's brand-specific agent in
its own folder. It reuses the andSons backend's own `vendor/` (third-party
pip packages only - langchain, pydantic, anthropic, groq, flask, slack_sdk,
PIL, etc.) via sys.path rather than a second multi-hundred-MB vendor
install, since none of OVA's actual application code lives there - see
README.md's Setup section for how to point this at your own vendor/ instead
if you'd rather this be fully independent.

REAL, DELIBERATE SCOPE CUT vs. the andSons bot: no "insight-driven" email
grounded in live BigQuery/MoEngage data - OVA_SG_CRM_AGENT_SCOPE.md SS8A/
SS8D are explicit, current blocking gaps (no OVA SG BigQuery access, no OVA
SG MoEngage workspace credentials). A request that reads as insight-driven
still gets a real draft - see agents/feedback_node.py's module docstring -
just honestly grounded in this flow's own real catalog data, not a
fabricated live finding.
"""
import logging
import os
import re
import sys
from datetime import datetime, timezone
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# Reuse the andSons backend's vendor/ for third-party packages only (see
# module docstring) - inserted AFTER this app's own directory so `import
# categories`/`import flows`/`agents.*` etc. always resolve to THIS folder's
# own OVA-specific modules first, never andSons' same-named ones.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ANDSONS_VENDOR = os.path.abspath(os.path.join(_THIS_DIR, "..", "backend", "vendor"))
sys.path.insert(0, _THIS_DIR)
if os.path.isdir(_ANDSONS_VENDOR):
    sys.path.append(_ANDSONS_VENDOR)

from flask import Flask, jsonify, request  # noqa: E402
from flask_cors import CORS  # noqa: E402

from agents.copywriter_agent import parse_email_request  # noqa: E402
from agents.template_agent import analyze_template_image  # noqa: E402
from agents.visual_qa_agent import review_image  # noqa: E402
from categories import DEFAULT_CATEGORY, VALID_CATEGORY_SLUGS  # noqa: E402
from agents.feedback_node import (  # noqa: E402
    apply_target_cadence,
    resolve_flow_for_request,
    resolve_touchpoint_reference,
    revise_flow_touchpoint,
    revise_flow_touchpoints,
    run_flow_pipeline,
    run_insight_flow_pipeline,
    _resolve_structural_request,
)
from email_image_renderer import render_email_image  # noqa: E402
from file_context import summarize_files  # noqa: E402
from flows import FLOW_BY_SLUG, VALID_FLOW_SLUGS  # noqa: E402
from flow_html_export import render_flow_html, render_step_html  # noqa: E402
from whatsapp_image_renderer import render_whatsapp_image  # noqa: E402
from slack_integration import (  # noqa: E402
    clear_pending_email_request,
    download_slack_file,
    format_flow_intro,
    format_flow_touchpoint_caption,
    get_email_session,
    get_pending_email_request,
    is_retry,
    post_file,
    post_message,
    post_rendered_email,
    run_in_background,
    save_email_session,
    save_pending_email_request,
    strip_mention,
    verify_slack_request,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ova_email.app")

# Real customer names are never available to a Slack bot - see the andSons
# backend's app.py for the identical reasoning. NAME is a deliberate
# placeholder token, swapped in by whatever system actually sends the email.
NAME_PLACEHOLDER = "NAME"

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
CORS(app)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "ova-email"})


def _collect_uploaded_file_context(event: dict, bot_token: str) -> tuple:
    files = [f for f in (event.get("files") or []) if not (f.get("mimetype") or "").startswith("image/")]
    if not files:
        return "", []
    downloaded = []
    errors = []
    for file_info in files:
        try:
            content = download_slack_file(file_info, bot_token)
            downloaded.append((file_info.get("name", "upload"), content))
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(f"{file_info.get('name', 'upload')}: couldn't download ({exc})")
    summary, parse_errors = summarize_files(downloaded)
    return summary, errors + parse_errors


def _collect_template_reference(event: dict, bot_token: str) -> tuple:
    images = [f for f in (event.get("files") or []) if (f.get("mimetype") or "").startswith("image/")]
    if not images:
        return "", []
    descriptions = []
    errors = []
    for file_info in images:
        name = file_info.get("name", "upload")
        try:
            content = download_slack_file(file_info, bot_token)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            errors.append(f"{name}: couldn't download ({exc})")
            continue
        description = analyze_template_image(content, mimetype=file_info.get("mimetype") or "image/png")
        descriptions.append(f"[Reference image {name!r}]\n{description}" if len(images) > 1 else description)
    return "\n\n".join(descriptions), errors


_PENDING_TEMPLATE_MARKER = "[TEMPLATE_REFERENCE]\n"


def _split_pending_template_reference(pending_texts: list) -> tuple:
    templates = [t[len(_PENDING_TEMPLATE_MARKER):] for t in pending_texts if t.startswith(_PENDING_TEMPLATE_MARKER)]
    remaining = [t for t in pending_texts if not t.startswith(_PENDING_TEMPLATE_MARKER)]
    return "\n\n".join(templates), remaining


def _pending_to_save(pending_texts: list, text: str, template_reference: Optional[str]) -> list:
    saved = pending_texts + [text]
    if template_reference:
        saved.append(f"{_PENDING_TEMPLATE_MARKER}{template_reference}")
    return saved


# Matches "APPROVED" anywhere in the message, case-insensitive, real word
# boundary - see the andSons backend's app.py for the identical reasoning.
_APPROVED_RE = re.compile(r"\bapproved\b", re.IGNORECASE)


def _handle_flow_approval(bot_token: str, channel: str, thread_ts: str, session: dict) -> None:
    flow = FLOW_BY_SLUG.get(session["flow_name"])
    flow_label = flow["label"] if flow else session["flow_name"]
    touchpoints = sorted(session["touchpoints"], key=lambda t: t["n"])
    total = len(touchpoints)
    approved_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    try:
        combined_bytes = render_flow_html(flow_label, touchpoints, approved_at)
    except Exception as exc:  # noqa: BLE001 - a rendering bug must never look like a silent no-op
        logger.exception("Failed to render the approved-flow HTML export for %s", session["flow_name"])
        post_message(bot_token, channel, thread_ts=thread_ts, text=f"Approved, but the HTML export failed to build ({exc}).")
        return
    post_file(
        bot_token, channel, thread_ts, combined_bytes, f"{session['flow_name']}_approved.html",
        f"Approved - here's the full {flow_label} flow as one file, plus each step's own file below.",
    )

    for t in touchpoints:
        try:
            step_bytes = render_step_html(flow_label, t, total, approved_at)
        except Exception as exc:  # noqa: BLE001 - one bad step's file must not stop the rest from uploading
            logger.exception("Failed to render step %d's own HTML export for %s", t["n"], session["flow_name"])
            post_message(bot_token, channel, thread_ts=thread_ts, text=f"Step {t['n']}'s own HTML file failed to build ({exc}).")
            continue
        filename = f"{session['flow_name']}_step{t['n']}_{t['channel']}.html"
        post_file(bot_token, channel, thread_ts, step_bytes, filename, f"Step {t['n']}/{total} - {t['channel'].capitalize()}.")


_TOUCHPOINT_FEEDBACK_RE = re.compile(r"^\s*(?:step\s*)?(\d+)\s*[:.\-]\s*(.+)$", re.IGNORECASE | re.DOTALL)


def _parse_touchpoint_feedback(text: str) -> Optional[tuple]:
    match = _TOUCHPOINT_FEEDBACK_RE.match(text or "")
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip()


def _post_flow_result(bot_token: str, channel: str, thread_ts: str, flow_result: dict, insight: Optional[dict] = None) -> None:
    touchpoints = flow_result["touchpoints"]
    post_message(
        bot_token, channel, thread_ts=thread_ts,
        text=format_flow_intro(flow_result["flow_name"], len(touchpoints), insight=insight, crm_brief=flow_result.get("crm_brief")),
    )
    for touchpoint in touchpoints:
        _post_flow_touchpoint(bot_token, channel, thread_ts, flow_result["flow_name"], touchpoint, len(touchpoints))


def _post_flow_touchpoint(bot_token: str, channel: str, thread_ts: str, flow_name: str, touchpoint: dict, total: int) -> None:
    if touchpoint.get("content") is None:
        post_message(
            bot_token, channel, thread_ts=thread_ts,
            text=(
                f"*Step {touchpoint['n']}/{total} - {touchpoint['channel'].capitalize()} - {touchpoint['timing']}*\n"
                f"Couldn't generate this one after retrying. Reply \"{touchpoint['n']}: try again\" in this thread to retry just this step."
            ),
        )
        return

    if touchpoint["channel"] == "email":
        image = render_email_image(touchpoint["content"], NAME_PLACEHOLDER)
        filename = f"{flow_name}_step{touchpoint['n']}_email.png"
    else:  # whatsapp
        image = render_whatsapp_image(touchpoint["content"], timing=touchpoint["timing"])
        filename = f"{flow_name}_step{touchpoint['n']}_whatsapp.png"

    visual_qa = review_image(image, channel=touchpoint["channel"])
    caption = format_flow_touchpoint_caption(touchpoint, total, visual_qa=visual_qa)
    post_rendered_email(bot_token, channel, thread_ts, image, filename, caption)


def _repost_flow(bot_token: str, channel: str, thread_ts: str, flow_name: str, touchpoints: list, header_text: str) -> None:
    post_message(bot_token, channel, thread_ts=thread_ts, text=header_text)
    for touchpoint in touchpoints:
        _post_flow_touchpoint(bot_token, channel, thread_ts, flow_name, touchpoint, len(touchpoints))


@app.route("/slack/events/ova-email", methods=["POST"])
def slack_events_ova_email():
    data = request.get_json(silent=True) or {}
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")})

    if not verify_slack_request(request, secret=os.environ.get("SLACK_OVA_EMAIL_SIGNING_SECRET", "")):
        return "Invalid request signature", 403
    if is_retry(request):
        return "", 200

    event = data.get("event", {})
    if event.get("type") != "app_mention" or event.get("bot_id"):
        return "", 200

    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = strip_mention(event.get("text", ""))
    bot_token = os.environ.get("SLACK_OVA_EMAIL_BOT_TOKEN", "")

    def _work():
        try:
            file_context, file_errors = _collect_uploaded_file_context(event, bot_token)
            for err in file_errors:
                post_message(bot_token, channel, thread_ts=thread_ts, text=f"Couldn't read {err}")
            if file_context:
                post_message(bot_token, channel, thread_ts=thread_ts, text="Read the attached file, factoring it in now...")

            template_reference, template_errors = _collect_template_reference(event, bot_token)
            for err in template_errors:
                post_message(bot_token, channel, thread_ts=thread_ts, text=f"Couldn't read {err}")
            if template_reference:
                post_message(bot_token, channel, thread_ts=thread_ts, text="Reading the attached image as a visual template to follow...")

            session = get_email_session(channel, thread_ts)
            if session:
                if _APPROVED_RE.search(text):
                    _handle_flow_approval(bot_token, channel, thread_ts, session)
                    return

                session_category = session.get("category", DEFAULT_CATEGORY)
                feedback_text = text
                if file_context:
                    feedback_text = (text + "\n\n" if text else "") + ("Also take this uploaded file into account:\n" + file_context)
                if template_reference:
                    feedback_text = (feedback_text + "\n\n" if feedback_text else "") + "An image was attached asking to follow its visual template."

                prior_feedback_history = session.get("feedback_history", [])
                structural = _resolve_structural_request(feedback_text, session["touchpoints"], session["flow_name"], feedback_history=prior_feedback_history)

                if structural["changed"]:
                    result = apply_target_cadence(
                        session["flow_name"], session["touchpoints"], structural["target_cadence"], feedback_text,
                        feedback_history=prior_feedback_history, category=session_category, template_reference=template_reference or None,
                    )
                    save_email_session(channel, thread_ts, {"flow_name": session["flow_name"], "touchpoints": result["touchpoints"], "feedback_history": result["feedback_history"], "category": session_category})
                    _repost_flow(bot_token, channel, thread_ts, session["flow_name"], result["touchpoints"], f"Updated the flow - it's now {len(result['touchpoints'])} step(s). Reposting the updated sequence below.")
                    return

                parsed = _parse_touchpoint_feedback(feedback_text)
                if parsed is not None:
                    touchpoint_n, touchpoint_feedback = parsed
                else:
                    ref = resolve_touchpoint_reference(feedback_text, session["touchpoints"])
                    resolved_ns = ref["touchpoint_ns"]

                    if len(resolved_ns) > 1:
                        result = revise_flow_touchpoints(
                            session["flow_name"], session["touchpoints"], resolved_ns, feedback_text,
                            feedback_history=session.get("feedback_history", []), category=session_category, template_reference=template_reference or None,
                        )
                        save_email_session(channel, thread_ts, {"flow_name": session["flow_name"], "touchpoints": result["touchpoints"], "feedback_history": result["feedback_history"], "category": session_category})
                        for revised_touchpoint in result["revised"]:
                            _post_flow_touchpoint(bot_token, channel, thread_ts, session["flow_name"], revised_touchpoint, len(result["touchpoints"]))
                        return

                    if resolved_ns:
                        touchpoint_n, touchpoint_feedback = resolved_ns[0], feedback_text
                    elif ref["candidate_ns"]:
                        steps = ", ".join(str(n) for n in ref["candidate_ns"])
                        post_message(bot_token, channel, thread_ts=thread_ts, text=f"Steps {steps} could all match that - which one did you mean? (reply with the step number, e.g. \"{ref['candidate_ns'][0]}: {feedback_text}\")")
                        return
                    else:
                        total = len(session["touchpoints"])
                        post_message(bot_token, channel, thread_ts=thread_ts, text=f"This flow has {total} step(s) and I couldn't tell which one that's about. Tell me which one to revise, e.g. \"2: make this shorter\".")
                        return

                try:
                    result = revise_flow_touchpoint(
                        session["flow_name"], session["touchpoints"], touchpoint_n, touchpoint_feedback,
                        feedback_history=session.get("feedback_history", []), category=session_category, template_reference=template_reference or None,
                    )
                except ValueError as exc:
                    post_message(bot_token, channel, thread_ts=thread_ts, text=str(exc))
                    return

                save_email_session(channel, thread_ts, {"flow_name": session["flow_name"], "touchpoints": result["touchpoints"], "feedback_history": result["feedback_history"], "category": session_category})
                _post_flow_touchpoint(bot_token, channel, thread_ts, session["flow_name"], result["touchpoint"], len(result["touchpoints"]))
                return

            pending_texts_raw = get_pending_email_request(channel, thread_ts)
            pending_template_reference, pending_texts = _split_pending_template_reference(pending_texts_raw)
            combined_text = "\n".join(t for t in (pending_texts + [text]) if t)
            template_reference = "\n\n".join(x for x in (pending_template_reference, template_reference) if x) or None

            if not combined_text and file_context:
                intent = {"mode": "insight", "flow_name": None, "signal_question": None, "category": DEFAULT_CATEGORY}
            elif not combined_text and template_reference:
                intent = {"mode": "direct", "flow_name": None, "signal_question": None, "category": DEFAULT_CATEGORY}
            else:
                intent = parse_email_request(combined_text)

            if intent["mode"] == "unclear":
                clear_pending_email_request(channel, thread_ts)
                post_message(
                    bot_token, channel, thread_ts=thread_ts,
                    text="I can draft an OVA Singapore flow's real touchpoints (e.g. \"write the missed refill "
                    "email\") or investigate a business signal and draft one. Try naming a category "
                    f"({', '.join(VALID_CATEGORY_SLUGS)}) or a flow if I couldn't tell.",
                )
                return

            if intent["mode"] == "insight":
                question = intent["signal_question"] or combined_text or "Review the attached data and identify what needs addressing."
                result = run_insight_flow_pipeline(
                    question, flow_name=intent["flow_name"], file_context=file_context, raw_request=combined_text,
                    category=intent["category"], template_reference=template_reference or None,
                )
                if result["needs_flow_clarification"]:
                    save_pending_email_request(channel, thread_ts, _pending_to_save(pending_texts, text, template_reference))
                    post_message(
                        bot_token, channel, thread_ts=thread_ts,
                        text=(
                            f"{result['brief']['bigquery_answer']}\n\n"
                            "None of our real flows clearly fit sending an email for this - tell me which one "
                            "to frame it as: " + ", ".join(sorted(VALID_FLOW_SLUGS))
                        ),
                    )
                    return
                clear_pending_email_request(channel, thread_ts)
                save_email_session(channel, thread_ts, {"flow_name": result["flow_name"], "touchpoints": result["touchpoints"], "feedback_history": [], "category": intent["category"]})
                _post_flow_result(bot_token, channel, thread_ts, result, insight=result.get("insight_brief"))
                return

            resolved_flow_name = resolve_flow_for_request(combined_text, intent["flow_name"])

            if not resolved_flow_name:
                save_pending_email_request(channel, thread_ts, _pending_to_save(pending_texts, text, template_reference))
                image_note = " (got the image - I'll apply it once I know which flow this is for)" if template_reference else ""
                post_message(
                    bot_token, channel, thread_ts=thread_ts,
                    text=f"I need to know which flow this is for{image_note} (e.g. \"contraception missed refill\", "
                    "\"EC fast response\", \"weight loss package offer\") - or describe what's going on and I'll design one.",
                )
                return

            clear_pending_email_request(channel, thread_ts)
            result = run_flow_pipeline(resolved_flow_name, file_context=file_context, raw_request=combined_text, category=intent["category"], template_reference=template_reference or None)
            save_email_session(channel, thread_ts, {"flow_name": resolved_flow_name, "touchpoints": result["touchpoints"], "feedback_history": [], "category": intent["category"]})
            _post_flow_result(bot_token, channel, thread_ts, result)
        except Exception as exc:  # noqa: BLE001 - logged in full, only a clean message goes to Slack
            logger.exception("Error generating email for channel=%s thread_ts=%s", channel, thread_ts)
            post_message(bot_token, channel, thread_ts=thread_ts, text=f"Something went wrong drafting that ({type(exc).__name__}) - try rephrasing, or check the server logs if it keeps happening.")

    run_in_background(_work)
    return "", 200


if __name__ == "__main__":
    # Cloud Run injects PORT and requires the app to bind to it; FLASK_PORT
    # remains the local-dev override (see the andSons backend's app.py for
    # the identical reasoning - this bit us there first).
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5002)))
    app.run(host="0.0.0.0", port=port, debug=False)
