"""Renders a generated email as an actual branded PNG image - not Slack
Block Kit text fragments - so it looks like a real HTML email when posted
to Slack (per Thalia's feedback: "It has to look like this... Like how
it's gonna be when it's launched by email").

Reuses the exact brand tokens already defined in frontend/src/index.css
(--accent #963e21, --text #2a2422, --text-muted #6b6058, --border #e6ddd2,
steps-block bg #faf7f2) so the Slack preview and the web demo's EmailCard
match, not two independently-guessed designs.

Pure Pillow (no headless browser) - deliberately, to keep the Docker image
light on Cloud Run. The bundled variable font (static/fonts/Roboto-Variable.ttf,
Apache-2.0) is used at fixed weights via set_variation_by_name rather than
relying on the deploy environment happening to have a system font installed.

Renders content only - never invents copy. Every string drawn here comes
from the same EmailContent the Copywriter already produced and the Sweeper
already checked; this module only lays it out visually.
"""
import os
import re
import textwrap
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from image_bank import HERO_BANK

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BACKEND_DIR, "static", "fonts", "Roboto-Variable.ttf")
HERO_DIR = os.path.join(BACKEND_DIR, "static", "hero_images")

# --- Brand tokens, copied from frontend/src/index.css :root ------------
COLOR_TEXT = "#2a2422"
COLOR_MUTED = "#6b6058"
COLOR_BORDER = "#e6ddd2"
COLOR_ACCENT = "#963e21"
COLOR_STEPS_BG = "#faf7f2"
COLOR_WHITE = "#ffffff"

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _normalize_background_color(value: Optional[str]) -> str:
    """Validates the Copywriter's EmailContent.background_color before it
    ever reaches PIL - never trust a raw model-generated string directly
    into an image call (same validate-before-trust principle used
    throughout this codebase for other LLM-produced values, e.g.
    analytics_agent's category/flow-family normalizers). A malformed value
    (missing, not a real hex code, stray whitespace) falls back to the
    normal brand background rather than crashing the whole render or
    passing PIL a string it can't parse - a rare bad value should degrade
    silently to the default look, not take down the entire touchpoint."""
    if not value:
        return COLOR_WHITE
    candidate = value.strip()
    if not _HEX_COLOR_RE.match(candidate):
        return COLOR_WHITE
    return candidate

# Rendered at 2x a "standard" 600px email width, then left at that native
# resolution (not downsampled) - Slack fits the display size to its message
# pane regardless of source pixel count, so the extra real resolution shows
# up as crisper text/photo detail on modern (retina/high-DPI) screens
# instead of a soft, visibly-upscaled 1x render.
SCALE = 2
CANVAS_WIDTH = 600 * SCALE
MARGIN = 36 * SCALE
CONTENT_WIDTH = CANVAS_WIDTH - 2 * MARGIN
HERO_HEIGHT = 320 * SCALE


def _s(px: int) -> int:
    """Scale a logical (1x) pixel value up to the render's actual SCALE."""
    return px * SCALE


def _font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass  # falls back to the font's default instance - still renders fine
    return f


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
    lines = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            trial = f"{current} {word}"
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def _draw_wrapped(draw, text, x, y, font, max_width, fill, line_height=None) -> int:
    line_height = line_height or int(font.size * 1.45)
    for line in _wrap(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _draw_check(draw, x, y_top, size, color, width=None):
    """Draws a checkmark as two line strokes rather than a Unicode glyph -
    the bundled font doesn't actually have a clean check glyph (renders as
    a broken/missing-glyph box), so this guarantees a consistent look
    regardless of font coverage. Returns the mark's width."""
    width = width or max(2, int(size * 0.16))
    p1 = (x, y_top + size * 0.5)
    p2 = (x + size * 0.35, y_top + size * 0.85)
    p3 = (x + size, y_top + size * 0.05)
    draw.line([p1, p2], fill=color, width=width, joint="curve")
    draw.line([p2, p3], fill=color, width=width, joint="curve")
    return size


def _draw_icon_document(draw, cx, cy, size, color):
    """Plan/review icon: a small document outline with a few lines of text."""
    w, h = size * 0.7, size * 0.9
    x0, y0 = cx - w / 2, cy - h / 2
    lw = max(1, int(size * 0.08))
    draw.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=size * 0.08, outline=color, width=lw)
    for frac in (0.32, 0.52, 0.72):
        ly = y0 + h * frac
        draw.line([(x0 + w * 0.18, ly), (x0 + w * 0.82, ly)], fill=color, width=lw)


def _draw_icon_check_circle(draw, cx, cy, size, color):
    """Confirm/payment icon: a circle outline with a checkmark inside."""
    r = size * 0.45
    lw = max(1, int(size * 0.08))
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=lw)
    check_size = size * 0.5
    _draw_check(draw, cx - check_size / 2, cy - check_size / 2, check_size, color, width=lw)


def _draw_icon_box(draw, cx, cy, size, color):
    """Shipping/delivery icon: a package body with an open flap on top and
    a tape line down the middle - reads as an actual box, unlike a plain
    cross-divided rectangle (which just looks like a 4-pane grid)."""
    w, h = size * 0.7, size * 0.55
    x0, y0 = cx - w / 2, cy - h / 2 + size * 0.1
    lw = max(1, int(size * 0.08))
    draw.rectangle([x0, y0, x0 + w, y0 + h], outline=color, width=lw)
    flap_peak = (cx, y0 - size * 0.22)
    draw.line([(x0, y0), flap_peak], fill=color, width=lw, joint="curve")
    draw.line([(x0 + w, y0), flap_peak], fill=color, width=lw, joint="curve")
    draw.line([(cx, y0), (cx, y0 + h)], fill=color, width=lw)


# Keyword -> icon. Deliberately NOT a fixed "step 1/2/3 always gets icon
# X" mapping - these three icons only make sense when a step is actually
# ABOUT reviewing/confirming, paying, or shipping (true for e.g. P1's real
# "review plan -> pay -> discreet delivery" steps, matching the real sent-
# email template) - a flow like quiz_recovery has nothing to do with any
# of these, so its steps correctly fall through to the numbered badge
# instead of a wrong icon forced onto unrelated content.
_ICON_KEYWORDS = [
    # "payment" itself is compliance-banned in most flows' copy (see
    # copywriter_agent.py's PAYMENT-FRAMING BAN), so the real generated
    # text says "start/confirm your treatment", "takes a minute", etc.
    # instead - matched here, not just the literal word "payment".
    (_draw_icon_check_circle, (
        "pay", "cost", "atome", "checkout", "confirm your treatment", "start your treatment",
        "takes a minute", "get started", "complete your order", "confirm your cart",
    )),
    (_draw_icon_box, ("ship", "deliver", "arrive", "discreet", "packag", "post", "send it to you")),
    (_draw_icon_document, ("review", "plan", "finalised", "finalized")),
]


def _pick_step_icon(step_text: str):
    t = step_text.lower()
    for icon_fn, keywords in _ICON_KEYWORDS:
        if any(k in t for k in keywords):
            return icon_fn
    return None


def _draw_rich_wrapped(draw, runs, x, y, font, max_width, line_height=None) -> int:
    """Like _draw_wrapped, but runs is a list of (text, color, underline)
    spans drawn inline with mixed styling (e.g. a sentence with one styled
    "link" phrase in it) - Pillow has no native rich-text support, so this
    wraps word-by-word across run boundaries, tracking which run (and
    therefore which color/underline) each word belongs to."""
    line_height = line_height or int(font.size * 1.45)
    space_w = draw.textlength(" ", font=font)

    words = []  # (word_text, color, underline)
    for text, color, underline in runs:
        for word in text.split(" "):
            if word:
                words.append((word, color, underline))

    # Pass 1: lay out each word's position first (no drawing yet) - needed
    # so contiguous underlined words can be joined into ONE continuous
    # underline stroke per line, instead of one short stroke per word (which
    # reads as a dashed/broken line under a multi-word phrase).
    placed = []  # (word, color, underline, word_x, word_y, word_w)
    cursor_x, cursor_y = x, y
    for word, color, underline in words:
        word_w = draw.textlength(word, font=font)
        if cursor_x != x and cursor_x + word_w > x + max_width:
            cursor_y += line_height
            cursor_x = x
        placed.append((word, color, underline, cursor_x, cursor_y, word_w))
        cursor_x += word_w + space_w

    for word, color, underline, wx, wy, word_w in placed:
        draw.text((wx, wy), word, font=font, fill=color)

    # Pass 2: draw one underline per contiguous run of underlined words that
    # share the same line (same wy) and color.
    i = 0
    while i < len(placed):
        _, color, underline, wx, wy, word_w = placed[i]
        if not underline:
            i += 1
            continue
        run_start_x = wx
        run_end_x = wx + word_w
        j = i + 1
        while j < len(placed) and placed[j][2] and placed[j][4] == wy and placed[j][1] == color:
            run_end_x = placed[j][3] + placed[j][5]
            j += 1
        draw.line([(run_start_x, wy + font.size + 2), (run_end_x, wy + font.size + 2)], fill=color, width=1)
        i = j

    return (placed[-1][4] if placed else y) + line_height


def _draw_hero(canvas: Image.Image, hero_key: str, hero_headline: Optional[str], y: int) -> int:
    path = os.path.join(HERO_DIR, f"{hero_key}.jpg")
    if not os.path.isfile(path):
        return y  # bank inconsistency - skip the hero rather than crash the whole render
    photo = Image.open(path).convert("RGB")

    # Baked heroes (HERO_BANK[key]["baked_headline"] is not None) already
    # have their headline burned into the JPEG file itself, at its own
    # custom position - typically bottom-anchored, like the overlay this
    # renderer draws for raw heroes. A pure center-crop can clip straight
    # into that baked-in text, so baked heroes crop from the top instead
    # (keeping the full bottom of the source image); raw heroes have no
    # baked text to protect, so a center-crop is fine for them.
    is_baked = HERO_BANK.get(hero_key, {}).get("baked_headline") is not None

    target_ratio = CANVAS_WIDTH / HERO_HEIGHT
    src_ratio = photo.width / photo.height
    if src_ratio > target_ratio:
        new_width = int(photo.height * target_ratio)
        left = (photo.width - new_width) // 2
        photo = photo.crop((left, 0, left + new_width, photo.height))
    else:
        new_height = int(photo.width / target_ratio)
        top = (photo.height - new_height) if is_baked else (photo.height - new_height) // 2
        photo = photo.crop((0, top, photo.width, top + new_height))
    photo = photo.resize((CANVAS_WIDTH, HERO_HEIGHT), Image.LANCZOS)
    canvas.paste(photo, (0, y))

    if hero_headline and not is_baked:
        # Bottom-up dark gradient (matches .hero-headline's CSS: linear-gradient
        # to top, rgba(0,0,0,0.6) -> transparent) so white overlay text stays
        # legible regardless of the photo underneath.
        band_height = _s(130)
        alpha = np.linspace(160, 0, band_height, dtype=np.uint8).reshape(-1, 1)
        gradient = np.tile(alpha, (1, CANVAS_WIDTH))
        overlay = Image.new("RGBA", (CANVAS_WIDTH, band_height), (0, 0, 0, 0))
        overlay.putalpha(Image.fromarray(gradient, mode="L"))
        canvas.paste(overlay, (0, y + HERO_HEIGHT - band_height), overlay)

        draw = ImageDraw.Draw(canvas)
        headline_font = _font(_s(26), "Bold")
        draw.text((MARGIN, y + HERO_HEIGHT - _s(54)), hero_headline, font=headline_font, fill=COLOR_WHITE)

    return y + HERO_HEIGHT


def render_email_image(content: dict, first_name: str) -> Image.Image:
    """content: the same dict shape as generate_email()['content'] /
    run_email_pipeline()['email']. Returns a PIL Image (RGB) ready to save
    as PNG/JPEG - caller decides where to write it."""
    # Oversized canvas, cropped to actual content height at the end - the
    # simplest way to lay out genuinely variable-length email content
    # without a separate two-pass height-measurement engine.
    page_background = _normalize_background_color(content.get("background_color"))
    canvas = Image.new("RGB", (CANVAS_WIDTH, _s(2400)), page_background)
    draw = ImageDraw.Draw(canvas)
    y = 0

    # --- Logo header ("&sons" wordmark - the real andSons logotype; body
    # copy still always says "andSons", per brand rules) - centered, matching
    # the real sent-email template Thalia shared. ---
    logo_font = _font(_s(28), "Bold")
    logo_w = draw.textlength("&sons", font=logo_font)
    draw.text(((CANVAS_WIDTH - logo_w) / 2, _s(24)), "&sons", font=logo_font, fill=COLOR_TEXT)
    y = _s(24) + _s(40)
    draw.line([(0, y), (CANVAS_WIDTH, y)], fill=COLOR_BORDER, width=_s(1))
    y += _s(1)

    # --- Hero (optional) ---
    hero_key = content.get("hero")
    if hero_key and hero_key != "none" and content.get("hero_image_url"):
        y = _draw_hero(canvas, hero_key, content.get("hero_headline"), y)

    y += _s(28)
    draw = ImageDraw.Draw(canvas)  # re-bind after any paste() calls above

    # --- Greeting + opening lines ---
    body_font = _font(_s(16), "Regular")
    draw.text((MARGIN, y), f"Hi {first_name},", font=body_font, fill=COLOR_TEXT)
    y += int(_s(16) * 1.45) + _s(10)

    for line in content.get("opening_lines") or []:
        y = _draw_wrapped(draw, line.replace("[name]", first_name), MARGIN, y, body_font, CONTENT_WIDTH, COLOR_TEXT)
        y += _s(10)

    # --- What happens next (numbered circle badges instead of icons - on
    # brand, no icon asset library needed) ---
    steps = content.get("what_happens_next")
    marker_style_text = (content.get("step_marker_style") or "").lower()
    # The Copywriter writes step_marker_style as free-form English (its own
    # understanding of a reviewer's request), not a fixed choice - this is
    # this renderer's own best-effort read of that free text into one of
    # the few marker shapes it actually knows how to draw with PIL, not a
    # menu the Copywriter picks from.
    force_bullets = any(w in marker_style_text for w in ("bullet", "dot"))
    force_numbers = "number" in marker_style_text and not force_bullets
    if steps:
        box_top = y + _s(6)
        box_padding = _s(20)
        badge_r = _s(12)
        step_font = _font(_s(15), "Regular")
        title_font = _font(_s(13), "SemiBold")
        num_font = _font(_s(12), "Bold")

        text_x = MARGIN + box_padding + badge_r * 2 + _s(14)
        text_width = CONTENT_WIDTH - 2 * box_padding - badge_r * 2 - _s(14)

        # Pass 1: measure only (same wrap logic the actual draw uses below)
        # so the box background can be drawn BEFORE the content - drawing it
        # after would paint over the badges/text.
        inner_y = box_top + box_padding + int(_s(13) * 1.6) + _s(8)
        for step in steps:
            step_lines = _wrap(draw, step, step_font, text_width)
            step_height = len(step_lines) * int(step_font.size * 1.45)
            inner_y = max(inner_y + step_height, inner_y + badge_r * 2 + _s(6)) + _s(10)
        box_bottom = inner_y + box_padding - _s(10)

        draw.rounded_rectangle(
            [MARGIN, box_top, CANVAS_WIDTH - MARGIN, box_bottom], radius=_s(10), fill=COLOR_STEPS_BG
        )

        # Pass 2: actually draw title, badges, and step text on top of the box.
        inner_y = box_top + box_padding
        draw.text((MARGIN + box_padding, inner_y), "WHAT HAPPENS NEXT", font=title_font, fill=COLOR_MUTED)
        inner_y += int(_s(13) * 1.6) + _s(8)

        for i, step in enumerate(steps, start=1):
            badge_cy = inner_y + _s(10)
            badge_cx = MARGIN + box_padding + badge_r
            icon_fn = None if (force_bullets or force_numbers) else _pick_step_icon(step)
            if icon_fn:
                # A content-appropriate icon (matches the real sent-email
                # template's line-icon style) - no filled circle backdrop.
                icon_fn(draw, badge_cx, badge_cy, badge_r * 1.9, COLOR_ACCENT)
            elif force_bullets:
                # A reviewer explicitly asked for plain bullets - a small
                # filled dot, no number inside it.
                dot_r = badge_r * 0.35
                draw.ellipse(
                    [badge_cx - dot_r, badge_cy - dot_r, badge_cx + dot_r, badge_cy + dot_r],
                    fill=COLOR_ACCENT,
                )
            else:
                # No icon genuinely fits this step's content (or a reviewer
                # explicitly asked for numbers) - a plain numbered badge.
                draw.ellipse(
                    [badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r],
                    fill=COLOR_ACCENT,
                )
                num_text = str(i)
                num_w = draw.textlength(num_text, font=num_font)
                draw.text((badge_cx - num_w / 2, badge_cy - _s(8)), num_text, font=num_font, fill=COLOR_WHITE)

            step_bottom = _draw_wrapped(draw, step, text_x, inner_y, step_font, text_width, COLOR_TEXT)
            inner_y = max(step_bottom, badge_cy + badge_r + _s(6)) + _s(10)

        y = box_bottom + _s(20)

    # --- Gentle truth line ---
    if content.get("gentle_truth_line"):
        y = _draw_wrapped(draw, content["gentle_truth_line"], MARGIN, y, body_font, CONTENT_WIDTH, COLOR_TEXT)
        y += _s(20)

    # --- CTA button (flush left by default, matching the golden template -
    # a human reviewer can ask to reposition it; the Copywriter judges the
    # actual position being asked for and records it as
    # content["cta_position"], a 0.0 (left)-1.0 (right) fraction, rather
    # than this renderer deciding between a fixed set of named positions -
    # demo-only scope, not part of the real andSons Copywriter spec) ---
    cta_text = content.get("cta_text", "")
    cta_font = _font(_s(15), "SemiBold")
    btn_padding_x, btn_padding_y = _s(28), _s(14)
    btn_w = draw.textlength(cta_text, font=cta_font) + 2 * btn_padding_x
    btn_h = cta_font.size + 2 * btn_padding_y
    cta_position = content.get("cta_position", 0.0) or 0.0
    available_x = CANVAS_WIDTH - 2 * MARGIN - btn_w
    btn_x = MARGIN + available_x * cta_position
    draw.rounded_rectangle([btn_x, y, btn_x + btn_w, y + btn_h], radius=_s(6), fill=COLOR_ACCENT)
    draw.text((btn_x + btn_padding_x, y + btn_padding_y - _s(2)), cta_text, font=cta_font, fill=COLOR_WHITE)
    y += btn_h + _s(24)

    # --- Trust line (checkmark-prefixed, centered - matches the real sent-
    # email template: "check Doctor-led plan   check Clinically studied
    # check Discreet delivery", not the plain " · "-joined string the
    # Copywriter outputs) ---
    if content.get("trust_line"):
        trust_font = _font(_s(13), "Regular")
        items = [seg.strip() for seg in content["trust_line"].split("·") if seg.strip()]
        check_size = int(trust_font.size * 0.6)
        check_gap = int(trust_font.size * 0.3)
        item_gap = int(trust_font.size * 1.4)

        item_widths = [check_size + check_gap + draw.textlength(item, font=trust_font) for item in items]
        total_w = sum(item_widths) + item_gap * (len(items) - 1)

        cursor_x = (CANVAS_WIDTH - total_w) / 2
        text_y_offset = (check_size - trust_font.size) / 2  # vertically center check against the text
        for item, item_w in zip(items, item_widths):
            _draw_check(draw, cursor_x, y - text_y_offset, check_size, COLOR_MUTED)
            draw.text((cursor_x + check_size + check_gap, y), item, font=trust_font, fill=COLOR_MUTED)
            cursor_x += item_w + item_gap
        y += int(_s(13) * 1.6) + _s(20)

    # --- Signature ---
    draw.text((MARGIN, y), "The andSons team", font=body_font, fill=COLOR_TEXT)
    y += int(_s(16) * 1.45) + _s(20)

    # --- Footer ---
    draw.line([(MARGIN, y), (CANVAS_WIDTH - MARGIN, y)], fill=COLOR_BORDER, width=_s(1))
    y += _s(16)
    footer_font = _font(_s(12), "Regular")

    # WhatsApp CS line as one inline sentence with the WhatsApp phrase styled
    # like a link (matches the real sent-email template) instead of a bare
    # "WhatsApp customer service" label line - same real WhatsApp contact,
    # just phrased as a sentence rather than a standalone label.
    runs = [
        ("Questions about your plan or your order? Chat with our ", COLOR_MUTED, False),
        ("customer service team on WhatsApp", COLOR_ACCENT, True),
        (".", COLOR_MUTED, False),
    ]
    footer_line_height = int(_s(12) * 1.6)
    y = _draw_rich_wrapped(draw, runs, MARGIN, y, footer_font, CONTENT_WIDTH, line_height=footer_line_height)

    # The address line is the one part of the footer a human reviewer can
    # toggle via feedback (EmailContent.include_address) - WhatsApp CS above
    # and Unsubscribe below are never optional regardless of this flag.
    footer_lines = []
    if content.get("include_address", True):
        footer_lines.append("andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522")
    footer_lines.append("Unsubscribe")
    for line in footer_lines:
        draw.text((MARGIN, y), line, font=footer_font, fill=COLOR_MUTED)
        y += int(_s(12) * 1.6)

    y += _s(24)
    return canvas.crop((0, 0, CANVAS_WIDTH, y))
