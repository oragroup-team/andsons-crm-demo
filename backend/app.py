import _vendor_path  # noqa: F401  — must be first, see _vendor_path.py

import logging
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from agents.analytics_agent import ask_analytics
from agents.copywriter_agent import parse_email_request
from agents.visual_qa_agent import review_image
from agents.feedback_node import (
    resolve_touchpoint_reference,
    revise_flow_touchpoint,
    revise_with_feedback,
    run_email_pipeline,
    run_flow_pipeline,
    run_insight_flow_pipeline,
)
from email_image_renderer import render_email_image
from file_context import summarize_files
from flows import VALID_FLOW_SLUGS
from whatsapp_image_renderer import render_whatsapp_image
from push_image_renderer import render_push_image
from slack_integration import (
    append_analytics_exchange,
    clear_pending_email_request,
    download_slack_file,
    format_analytics_blocks,
    format_email_blocks,
    format_flow_intro,
    format_flow_touchpoint_caption,
    get_analytics_history,
    get_email_session,
    get_pending_email_request,
    is_retry,
    post_message,
    post_rendered_email,
    post_result_to_slack,
    run_in_background,
    save_email_session,
    save_pending_email_request,
    strip_mention,
    verify_slack_request,
)

# Real customer names are never available to the Slack bots (there's no
# customer context to look one up from) - NAME is a deliberate placeholder
# token, the same idea as MoEngage's own %%FIRST_NAME%% merge tag, swapped
# in by whatever system actually sends the email. Asking Slack users for a
# name added a whole extra back-and-forth for information nobody there
# actually has.
NAME_PLACEHOLDER = "NAME"

logger = logging.getLogger("app")

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend"))
FRONTEND_DIST = os.path.join(FRONTEND_DIR, "dist")


def ensure_frontend_built():
    """Build the React frontend on first run (if it hasn't been built yet)
    so app.py can serve it directly — no separate `npm run dev` needed."""
    if os.path.isdir(FRONTEND_DIST) and os.listdir(FRONTEND_DIST):
        return

    npm = shutil.which("npm")
    if npm is None:
        print(
            "WARNING: npm not found on PATH — skipping automatic frontend build.\n"
            "Run `cd frontend && npm install && npm run build` manually, then restart app.py,\n"
            "or run `npm run dev` in frontend/ for the standalone dev server instead.",
            file=sys.stderr,
        )
        return

    print("No frontend build found — building it now (first run only, may take a minute)...")
    if not os.path.isdir(os.path.join(FRONTEND_DIR, "node_modules")):
        subprocess.run([npm, "install"], cwd=FRONTEND_DIR, check=True)
    subprocess.run([npm, "run", "build"], cwd=FRONTEND_DIR, check=True)
    print("Frontend built.")


ensure_frontend_built()

app = Flask(__name__, static_folder=FRONTEND_DIST, static_url_path="")
CORS(app)

VALID_FLOWS = set(VALID_FLOW_SLUGS)


@app.route("/generate-email", methods=["POST"])
def generate_email_endpoint():
    data = request.get_json(silent=True) or {}
    flow_name = data.get("flow_name")
    first_name = data.get("first_name")

    if not flow_name or flow_name not in VALID_FLOWS:
        return jsonify({"error": f"flow_name must be one of {sorted(VALID_FLOWS)}"}), 400
    if not first_name or not isinstance(first_name, str):
        return jsonify({"error": "first_name is required"}), 400

    try:
        result = run_email_pipeline(flow_name, first_name.strip())
    except Exception as exc:  # noqa: BLE001 — surfaced to the demo UI
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@app.route("/revise-email", methods=["POST"])
def revise_email_endpoint():
    data = request.get_json(silent=True) or {}
    flow_name = data.get("flow_name")
    first_name = data.get("first_name")
    feedback = data.get("feedback")
    previous_rendered_text = data.get("previous_rendered_text")
    feedback_history = data.get("feedback_history") or []

    if not flow_name or flow_name not in VALID_FLOWS:
        return jsonify({"error": f"flow_name must be one of {sorted(VALID_FLOWS)}"}), 400
    if not first_name or not isinstance(first_name, str):
        return jsonify({"error": "first_name is required"}), 400
    if not feedback or not isinstance(feedback, str) or not feedback.strip():
        return jsonify({"error": "feedback is required"}), 400
    if not isinstance(feedback_history, list):
        return jsonify({"error": "feedback_history must be a list of strings"}), 400

    try:
        result = revise_with_feedback(
            flow_name,
            first_name.strip(),
            feedback.strip(),
            previous_rendered_text=previous_rendered_text,
            feedback_history=feedback_history,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the demo UI
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@app.route("/ask", methods=["POST"])
def ask_endpoint():
    data = request.get_json(silent=True) or {}
    question = data.get("question")

    if not question or not isinstance(question, str):
        return jsonify({"error": "question is required"}), 400

    try:
        result = ask_analytics(question.strip())
    except Exception as exc:  # noqa: BLE001 — surfaced to the demo UI
        return jsonify({"error": str(exc)}), 500

    return jsonify(result)


@app.route("/hero-images/<path:filename>")
def hero_image(filename):
    return send_from_directory(os.path.join(BACKEND_DIR, "static", "hero_images"), filename)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


# --- Slack slash commands (for office testing) ---
# Both endpoints must ack within Slack's 3-second window, so the real work
# happens in a background thread and the final result is posted to Slack's
# one-time `response_url` when it's ready.


@app.route("/slack/generate-email", methods=["POST"])
def slack_generate_email():
    if not verify_slack_request(request):
        return "Invalid request signature", 403

    text = (request.form.get("text") or "").strip()
    response_url = request.form.get("response_url")
    parts = text.split(maxsplit=1)

    if len(parts) < 2:
        return jsonify(
            {
                "response_type": "ephemeral",
                "text": "Usage: `/andsons-email <flow_slug> <first_name>`\nFlows: " + ", ".join(sorted(VALID_FLOWS)),
            }
        )

    flow_name, first_name = parts[0], parts[1]
    if flow_name not in VALID_FLOWS:
        return jsonify(
            {
                "response_type": "ephemeral",
                "text": f"Unknown flow `{flow_name}`. Valid flows: " + ", ".join(sorted(VALID_FLOWS)),
            }
        )

    def _work():
        try:
            result = run_email_pipeline(flow_name, first_name)
            blocks = format_email_blocks(result, flow_name, first_name)
            post_result_to_slack(response_url, {"response_type": "in_channel", "blocks": blocks})
        except Exception as exc:  # noqa: BLE001 — surfaced back to Slack
            post_result_to_slack(
                response_url, {"response_type": "ephemeral", "text": f"Error generating email: {exc}"}
            )

    run_in_background(_work)
    return jsonify({"response_type": "ephemeral", "text": f"Generating the {flow_name} email for {first_name}..."})


@app.route("/slack/analytics", methods=["POST"])
def slack_analytics():
    if not verify_slack_request(request):
        return "Invalid request signature", 403

    question = (request.form.get("text") or "").strip()
    response_url = request.form.get("response_url")

    if not question:
        return jsonify({"response_type": "ephemeral", "text": "Usage: `/andsons-ask <question>`"})

    def _work():
        try:
            result = ask_analytics(question)
            blocks = format_analytics_blocks(result, question)
            post_result_to_slack(response_url, {"response_type": "in_channel", "blocks": blocks})
        except Exception as exc:  # noqa: BLE001 — surfaced back to Slack
            post_result_to_slack(
                response_url, {"response_type": "ephemeral", "text": f"Error answering question: {exc}"}
            )

    run_in_background(_work)
    return jsonify({"response_type": "ephemeral", "text": "Looking that up..."})


# --- Slack @-mention bots (Events API, two separate Slack apps) ---
# Unlike slash commands, there's no response_url for events - replies go
# through chat.postMessage with each app's own bot token. Feedback on an
# email draft is tracked by Slack thread (in-memory), so replying to the
# bot in the same thread continues that draft instead of starting a new one.


def _collect_uploaded_file_context(event: dict, bot_token: str) -> tuple:
    """Download and summarize every CSV/Excel file attached to an
    app_mention event. Returns (summary_text, error_messages) - a file that
    fails to download or parse is reported, never silently dropped, but one
    bad file doesn't block the others."""
    files = event.get("files") or []
    if not files:
        return "", []

    downloaded = []
    errors = []
    for file_info in files:
        try:
            content = download_slack_file(file_info, bot_token)
            downloaded.append((file_info.get("name", "upload"), content))
        except Exception as exc:  # noqa: BLE001 — reported, not swallowed
            errors.append(f"{file_info.get('name', 'upload')}: couldn't download ({exc})")

    summary, parse_errors = summarize_files(downloaded)
    return summary, errors + parse_errors


_TOUCHPOINT_FEEDBACK_RE = re.compile(r"^\s*(?:step\s*)?(\d+)\s*[:.\-]\s*(.+)$", re.IGNORECASE | re.DOTALL)


def _parse_touchpoint_feedback(text: str) -> Optional[tuple]:
    """A reply in a flow thread revises ONE touchpoint, so it must say
    which one (e.g. "2: make this shorter" or "step 3: drop the price") -
    unlike the old single-email flow, there's no single implicit target to
    fall back to. Returns (touchpoint_n, feedback_text) or None if the
    reply doesn't start with a step number."""
    match = _TOUCHPOINT_FEEDBACK_RE.match(text or "")
    if not match:
        return None
    return int(match.group(1)), match.group(2).strip()


def _post_flow_result(bot_token: str, channel: str, thread_ts: str, flow_result: dict, insight: Optional[dict] = None) -> None:
    """Post every real touchpoint of a generated flow into the thread, in
    cadence order, each labelled with its step/channel/timing - this is
    the actual fix for 'answer with all the emails and WhatsApp messages
    in one go' instead of a single email."""
    touchpoints = flow_result["touchpoints"]
    post_message(
        bot_token, channel, thread_ts=thread_ts,
        text=format_flow_intro(
            flow_result["flow_name"], len(touchpoints), insight=insight, crm_brief=flow_result.get("crm_brief"),
        ),
    )
    for touchpoint in touchpoints:
        _post_flow_touchpoint(bot_token, channel, thread_ts, flow_result["flow_name"], touchpoint, len(touchpoints))


def _post_flow_touchpoint(bot_token: str, channel: str, thread_ts: str, flow_name: str, touchpoint: dict, total: int) -> None:
    """Post one touchpoint - as a rendered image normally, or (if content
    generation exhausted its retries - see copywriter_agent.generate_flow's
    RuntimeError handling) as a plain text notice instead of crashing on a
    None content/rendered_text, so the rest of the flow still posts fine
    and this one step stays retryable with a normal reply."""
    if touchpoint.get("content") is None:
        post_message(
            bot_token, channel, thread_ts=thread_ts,
            text=(
                f"*Step {touchpoint['n']}/{total} - {touchpoint['channel'].capitalize()} - "
                f"{touchpoint['timing']}*\nCouldn't generate this one after retrying. Reply "
                f"\"{touchpoint['n']}: try again\" in this thread to retry just this step."
            ),
        )
        return

    if touchpoint["channel"] == "email":
        image = render_email_image(touchpoint["content"], NAME_PLACEHOLDER)
        filename = f"{flow_name}_step{touchpoint['n']}_email.png"
    elif touchpoint["channel"] == "whatsapp":
        image = render_whatsapp_image(touchpoint["content"], timing=touchpoint["timing"])
        filename = f"{flow_name}_step{touchpoint['n']}_whatsapp.png"
    else:  # push
        image = render_push_image(touchpoint["content"], timing=touchpoint["timing"])
        filename = f"{flow_name}_step{touchpoint['n']}_push.png"

    # Visual QA: the one check in this pipeline that looks at the actual
    # rendered image instead of text - catches things the Sweeper's
    # text-only reasoning can't (cramped layout, a hero that doesn't
    # actually sit right once rendered, broken spacing). Informational,
    # like the real system - never blocks the send, just surfaces the note.
    visual_qa = review_image(image, channel=touchpoint["channel"])

    caption = format_flow_touchpoint_caption(touchpoint, total, visual_qa=visual_qa)
    post_rendered_email(bot_token, channel, thread_ts, image, filename, caption)


@app.route("/slack/events/email", methods=["POST"])
def slack_events_email():
    data = request.get_json(silent=True) or {}
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")})

    if not verify_slack_request(request, secret=os.environ.get("SLACK_EMAIL_SIGNING_SECRET", "")):
        return "Invalid request signature", 403
    if is_retry(request):
        return "", 200

    event = data.get("event", {})
    if event.get("type") != "app_mention" or event.get("bot_id"):
        return "", 200

    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    text = strip_mention(event.get("text", ""))
    bot_token = os.environ.get("SLACK_EMAIL_BOT_TOKEN", "")

    def _work():
        try:
            file_context, file_errors = _collect_uploaded_file_context(event, bot_token)
            for err in file_errors:
                post_message(bot_token, channel, thread_ts=thread_ts, text=f"Couldn't read {err}")
            if file_context:
                post_message(
                    bot_token, channel, thread_ts=thread_ts,
                    text="Read the attached file, factoring it in now...",
                )

            session = get_email_session(channel, thread_ts)
            if session:
                feedback_text = text
                if file_context:
                    feedback_text = (text + "\n\n" if text else "") + (
                        "Also take this uploaded file into account:\n" + file_context
                    )

                parsed = _parse_touchpoint_feedback(feedback_text)
                if parsed is not None:
                    touchpoint_n, touchpoint_feedback = parsed
                else:
                    # No explicit "N: ..." prefix - don't just bounce the
                    # question back. Real bug this fixes: a reply like
                    # "move the button to the right" already identifies
                    # which touchpoint it's about via what it actually
                    # says, once compared against each touchpoint's real
                    # content - resolve that first, and only fall back to
                    # asking when it genuinely can't be determined.
                    ref = resolve_touchpoint_reference(feedback_text, session["touchpoints"])
                    if ref["touchpoint_n"] is not None:
                        touchpoint_n, touchpoint_feedback = ref["touchpoint_n"], feedback_text
                    elif ref["candidate_ns"]:
                        steps = ", ".join(str(n) for n in ref["candidate_ns"])
                        post_message(
                            bot_token, channel, thread_ts=thread_ts,
                            text=f"Steps {steps} could all match that - which one did you mean? "
                            f"(reply with the step number, e.g. \"{ref['candidate_ns'][0]}: {feedback_text}\")",
                        )
                        return
                    else:
                        total = len(session["touchpoints"])
                        post_message(
                            bot_token, channel, thread_ts=thread_ts,
                            text=f"This flow has {total} step(s) and I couldn't tell which one that's about. "
                            "Tell me which one to revise, e.g. \"2: make this shorter\".",
                        )
                        return

                try:
                    result = revise_flow_touchpoint(
                        session["flow_name"], session["touchpoints"], touchpoint_n, touchpoint_feedback,
                        feedback_history=session.get("feedback_history", []),
                    )
                except ValueError as exc:
                    post_message(bot_token, channel, thread_ts=thread_ts, text=str(exc))
                    return

                save_email_session(
                    channel, thread_ts,
                    {
                        "flow_name": session["flow_name"],
                        "touchpoints": result["touchpoints"],
                        "feedback_history": result["feedback_history"],
                    },
                )
                _post_flow_touchpoint(
                    bot_token, channel, thread_ts, session["flow_name"], result["touchpoint"], len(result["touchpoints"]),
                )
                return

            # Merge with whatever this thread has ALREADY said, if a flow
            # hasn't resolved yet - fixes a real bug: each reply used to be
            # classified from ONLY its own text, so "write me a cart-abandon
            # email" (flow unclear) -> "OTC cart abandon" (a reply with no
            # other context of its own) reset to square one instead of
            # completing the original request.
            pending_texts = get_pending_email_request(channel, thread_ts)
            combined_text = "\n".join(t for t in (pending_texts + [text]) if t)

            if not combined_text and file_context:
                intent = {"mode": "insight", "flow_name": None, "signal_question": None}
            else:
                intent = parse_email_request(combined_text)

            if intent["mode"] == "unclear":
                # Not a code failure - a real message that isn't an email
                # request at all (e.g. "what flows are live in MoEngage")
                # can make the classifier refuse structured output outright.
                # Give a clean, on-brand redirect instead of leaking the raw
                # API error into Slack. Clears pending context too, so it
                # doesn't keep dragging an off-topic message into future
                # classification attempts in this thread.
                clear_pending_email_request(channel, thread_ts)
                post_message(
                    bot_token, channel, thread_ts=thread_ts,
                    text="I can draft an andSons flow's real touchpoints (e.g. \"write the winback flow\") "
                    "or investigate a business signal and draft one (e.g. \"OTC sales are down, write "
                    "something to fix it\"). For general questions about flows or performance, try "
                    "@andSons Analytics instead.",
                )
                return

            if intent["mode"] == "insight":
                question = intent["signal_question"] or combined_text or "Review the attached data and identify what needs addressing."
                result = run_insight_flow_pipeline(question, flow_name=intent["flow_name"], file_context=file_context)
                if result["needs_flow_clarification"]:
                    save_pending_email_request(channel, thread_ts, pending_texts + [text])
                    post_message(
                        bot_token, channel, thread_ts=thread_ts,
                        text=(
                            f"Here's what the data shows: {result['brief']['bigquery_answer']}\n\n"
                            "But none of our real flows clearly fit sending an email for this - tell me "
                            "which one to frame it as: " + ", ".join(sorted(VALID_FLOW_SLUGS))
                        ),
                    )
                    return
                clear_pending_email_request(channel, thread_ts)
                save_email_session(
                    channel, thread_ts,
                    {
                        "flow_name": result["flow_name"],
                        "touchpoints": result["touchpoints"],
                        "feedback_history": [],
                    },
                )
                _post_flow_result(bot_token, channel, thread_ts, result, insight=result.get("insight_brief"))
                return

            if not intent["flow_name"]:
                save_pending_email_request(channel, thread_ts, pending_texts + [text])
                post_message(
                    bot_token, channel, thread_ts=thread_ts,
                    text="I need to know which flow this is for (e.g. \"plan not purchased\", "
                    "\"cart abandon\", \"consult no-show\", \"winback\") - or describe what's going on "
                    "(e.g. \"OTC serum sales are down\") and I'll investigate and pick one.",
                )
                return

            clear_pending_email_request(channel, thread_ts)
            result = run_flow_pipeline(intent["flow_name"], file_context=file_context)
            save_email_session(
                channel, thread_ts,
                {
                    "flow_name": intent["flow_name"],
                    "touchpoints": result["touchpoints"],
                    "feedback_history": [],
                },
            )
            _post_flow_result(bot_token, channel, thread_ts, result)
        except Exception as exc:  # noqa: BLE001 — logged in full, only a clean message goes to Slack
            logger.exception("Error generating email for channel=%s thread_ts=%s", channel, thread_ts)
            post_message(
                bot_token, channel, thread_ts=thread_ts,
                text=f"Something went wrong drafting that ({type(exc).__name__}) - try rephrasing, or "
                "check the server logs if it keeps happening.",
            )

    run_in_background(_work)
    return "", 200


@app.route("/slack/events/analytics", methods=["POST"])
def slack_events_analytics():
    data = request.get_json(silent=True) or {}
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data.get("challenge", "")})

    if not verify_slack_request(request, secret=os.environ.get("SLACK_ANALYTICS_SIGNING_SECRET", "")):
        return "Invalid request signature", 403
    if is_retry(request):
        return "", 200

    event = data.get("event", {})
    if event.get("type") != "app_mention" or event.get("bot_id"):
        return "", 200

    channel = event.get("channel")
    thread_ts = event.get("thread_ts") or event.get("ts")
    question = strip_mention(event.get("text", ""))
    bot_token = os.environ.get("SLACK_ANALYTICS_BOT_TOKEN", "")
    has_files = bool(event.get("files"))

    if not question and not has_files:
        post_message(
            bot_token, channel, thread_ts=thread_ts,
            text="Ask me a question about andSons orders, revenue, or marketing spend - you can also "
            "attach a CSV/Excel file and I'll factor it in.",
        )
        return "", 200

    def _work():
        try:
            file_context, file_errors = _collect_uploaded_file_context(event, bot_token)
            for err in file_errors:
                post_message(bot_token, channel, thread_ts=thread_ts, text=f"Couldn't read {err}")

            effective_question = question or "Summarize the attached file and point out anything notable."
            history = get_analytics_history(channel, thread_ts)
            result = ask_analytics(effective_question, conversation_history=history, file_context=file_context)
            append_analytics_exchange(channel, thread_ts, effective_question, result["answer"])
            blocks = format_analytics_blocks(result, effective_question)
            post_message(bot_token, channel, thread_ts=thread_ts, blocks=blocks)
        except Exception as exc:  # noqa: BLE001 — logged in full, only a clean message goes to Slack
            logger.exception("Error answering analytics question for channel=%s thread_ts=%s", channel, thread_ts)
            post_message(
                bot_token, channel, thread_ts=thread_ts,
                text=f"Something went wrong answering that ({type(exc).__name__}) - try rephrasing, or "
                "check the server logs if it keeps happening.",
            )

    run_in_background(_work)
    return "", 200


# --- Serve the built React frontend (so app.py alone serves the whole demo) ---


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    if path:
        candidate = os.path.join(app.static_folder or "", path)
        if os.path.isfile(candidate):
            return send_from_directory(app.static_folder, path)

    index_path = os.path.join(app.static_folder or "", "index.html")
    if os.path.isfile(index_path):
        return send_from_directory(app.static_folder, "index.html")

    return (
        "Frontend build not found. Run `cd frontend && npm install && npm run build`, "
        "then restart app.py — or run `npm run dev` in frontend/ for the dev server.",
        200,
    )


if __name__ == "__main__":
    # Cloud Run (and most PaaS hosts) inject PORT and require the app to bind
    # to it; FLASK_PORT remains the local-dev override, defaulting to 5001.
    port = int(os.environ.get("PORT", os.environ.get("FLASK_PORT", 5001)))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    print(f"\nandSons CRM demo running at http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=debug)
