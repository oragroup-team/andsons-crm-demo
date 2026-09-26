"""Renders an approved flow's real touchpoints (email/WhatsApp - the same
two real channels andSons uses) as self-contained HTML files, each step
shown exactly as the real image already posted to Slack - the same
rendering functions, the same bytes, just embedded as base64 data URIs
instead of uploaded as separate PNGs. Triggered by a human typing
"APPROVED" anywhere in a flow's Slack thread (see app.py's app_mention
handler) - a real, portable, shareable artifact of the approved flow, not
just images scattered across a Slack thread that scroll away.

render_flow_html() builds the ONE combined file with every step;
render_step_html() builds a single step's own standalone file - both are
uploaded on approval (see app.py._handle_flow_approval), so a reviewer
who only wants one specific step doesn't have to open the combined file
to get it. Both share the exact same per-step rendering (_render_step_
block) so neither can ever show a step differently from the other.

No external assets, no network calls to render the file - each .html
someone can open straight from their downloads folder, forward, or
archive, exactly like the demo's own EmailCard preview does in spirit,
just packaged as static files instead of a live page.
"""
import base64
import html
import io
import logging

from email_image_renderer import render_email_image
from whatsapp_image_renderer import render_whatsapp_image

logger = logging.getLogger("flow_html_export")

NAME_PLACEHOLDER = "NAME"

_PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{flow_label} - Approved Flow</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", Roboto, Arial, sans-serif; background: #f5f2ec; margin: 0; padding: 40px 20px; color: #2a2422; }}
  .header {{ max-width: 720px; margin: 0 auto 32px; }}
  .header h1 {{ font-size: 22px; margin: 0 0 4px; }}
  .header .meta {{ color: #6b6058; font-size: 14px; }}
  .step {{ max-width: 720px; margin: 0 auto 40px; background: #fff; border: 1px solid #e6ddd2; border-radius: 12px; overflow: hidden; }}
  .step .step-label {{ padding: 16px 20px; border-bottom: 1px solid #e6ddd2; }}
  .step .step-label .title {{ font-weight: 600; font-size: 15px; }}
  .step .step-label .intent {{ color: #6b6058; font-size: 13px; margin-top: 4px; }}
  .step .step-label .status {{ font-size: 12px; margin-top: 8px; display: inline-block; padding: 2px 8px; border-radius: 4px; }}
  .status.passed {{ background: #e3f0e3; color: #2f6d2f; }}
  .status.review {{ background: #f6e3d8; color: #96491f; }}
  .step img {{ display: block; width: 100%; height: auto; }}
</style>
</head>
<body>
<div class="header">
  <h1>{flow_label}</h1>
  <div class="meta">{step_count} touchpoint(s) - approved {approved_at}</div>
</div>
{steps_html}
</body>
</html>
"""

_STEP_TEMPLATE = """<div class="step">
  <div class="step-label">
    <div class="title">Step {n}/{total} - {channel_label} - {timing}</div>
    <div class="intent">{intent}</div>
    <span class="status {status_class}">{status_text}</span>
  </div>
  <img src="data:image/png;base64,{image_b64}" alt="Step {n} - {channel_label}">
</div>
"""

_FAILED_STEP_TEMPLATE = """<div class="step">
  <div class="step-label">
    <div class="title">Step {n}/{total} - {channel_label} - {timing}</div>
    <div class="intent">{message}</div>
  </div>
</div>
"""


def _render_touchpoint_image(touchpoint: dict):
    """Same channel dispatch and same rendering calls app.py's own
    _post_flow_touchpoint() already uses - this export must show exactly
    the same image already posted to Slack, never a re-imagined one."""
    channel = touchpoint["channel"]
    if channel == "email":
        return render_email_image(touchpoint["content"], NAME_PLACEHOLDER)
    return render_whatsapp_image(touchpoint["content"], timing=touchpoint["timing"])


def _render_step_block(t: dict, total: int) -> str:
    """One step's real HTML block (the image + its label) - shared by both
    render_flow_html() (all steps on one page) and render_step_html() (one
    step on its own page), so the two never drift into showing slightly
    different things for what should be the identical step. A touchpoint
    that has no content (exhausted its generation retries) or fails to
    render gets a plain notice instead - one bad step must never break the
    whole export, same fail-soft principle as
    app.py._post_flow_touchpoint()."""
    channel_label = html.escape(t["channel"].capitalize())
    timing = html.escape(str(t.get("timing", "")))

    if t.get("content") is None:
        return _FAILED_STEP_TEMPLATE.format(
            n=t["n"], total=total, channel_label=channel_label, timing=timing,
            message="Couldn't generate this step - nothing to show.",
        )

    try:
        image = _render_touchpoint_image(t)
    except Exception as exc:  # noqa: BLE001 - one bad step must not break the whole export
        logger.exception("Failed to render step %d for HTML export", t["n"])
        return _FAILED_STEP_TEMPLATE.format(
            n=t["n"], total=total, channel_label=channel_label, timing=timing,
            message=f"Rendering failed ({html.escape(str(exc))}).",
        )

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    image_b64 = base64.b64encode(buf.getvalue()).decode()
    # A carried-over touchpoint (never regenerated by a structural edit)
    # keeps whatever real pass/fail flag it already had - never invents a
    # failure just because the flag happens to be absent.
    passed = t.get("passed", True)
    return _STEP_TEMPLATE.format(
        n=t["n"], total=total, channel_label=channel_label, timing=timing,
        intent=html.escape(str(t.get("intent", ""))),
        status_class="passed" if passed else "review",
        status_text="Passed brand QA" if passed else "Needs human review",
        image_b64=image_b64,
    )


def render_flow_html(flow_label: str, touchpoints: list, approved_at: str) -> bytes:
    """Builds one self-contained HTML file (base64-embedded images, no
    external asset references) showing EVERY real touchpoint in a flow, in
    order, exactly as already rendered/posted to Slack."""
    ordered = sorted(touchpoints, key=lambda t: t["n"])
    total = len(ordered)
    steps_html = "\n".join(_render_step_block(t, total) for t in ordered)
    page = _PAGE_TEMPLATE.format(
        flow_label=html.escape(flow_label), step_count=total, approved_at=html.escape(approved_at),
        steps_html=steps_html,
    )
    return page.encode("utf-8")


def render_step_html(flow_label: str, touchpoint: dict, total: int, approved_at: str) -> bytes:
    """Builds one self-contained HTML file for a SINGLE real touchpoint -
    same rendering, same styling, same step block render_flow_html() uses
    for this exact step, just on its own page. Used alongside
    render_flow_html() (never instead of it) so a reviewer who only wants
    one specific step's file doesn't have to open the combined one."""
    step_html = _render_step_block(touchpoint, total)
    page = _PAGE_TEMPLATE.format(
        flow_label=html.escape(f"{flow_label} - Step {touchpoint['n']}/{total}"),
        step_count=1, approved_at=html.escape(approved_at),
        steps_html=step_html,
    )
    return page.encode("utf-8")
