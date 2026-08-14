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

CANVAS_WIDTH = 600
MARGIN = 36
CONTENT_WIDTH = CANVAS_WIDTH - 2 * MARGIN
HERO_HEIGHT = 320


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
        band_height = 130
        alpha = np.linspace(160, 0, band_height, dtype=np.uint8).reshape(-1, 1)
        gradient = np.tile(alpha, (1, CANVAS_WIDTH))
        overlay = Image.new("RGBA", (CANVAS_WIDTH, band_height), (0, 0, 0, 0))
        overlay.putalpha(Image.fromarray(gradient, mode="L"))
        canvas.paste(overlay, (0, y + HERO_HEIGHT - band_height), overlay)

        draw = ImageDraw.Draw(canvas)
        headline_font = _font(26, "Bold")
        draw.text((MARGIN, y + HERO_HEIGHT - 54), hero_headline, font=headline_font, fill=COLOR_WHITE)

    return y + HERO_HEIGHT


def render_email_image(content: dict, first_name: str) -> Image.Image:
    """content: the same dict shape as generate_email()['content'] /
    run_email_pipeline()['email']. Returns a PIL Image (RGB) ready to save
    as PNG/JPEG - caller decides where to write it."""
    # Oversized canvas, cropped to actual content height at the end - the
    # simplest way to lay out genuinely variable-length email content
    # without a separate two-pass height-measurement engine.
    canvas = Image.new("RGB", (CANVAS_WIDTH, 2400), COLOR_WHITE)
    draw = ImageDraw.Draw(canvas)
    y = 0

    # --- Logo header ("&sons" wordmark - the real andSons logotype; body
    # copy still always says "andSons", per brand rules) ---
    logo_font = _font(28, "Bold")
    draw.text((MARGIN, 24), "&sons", font=logo_font, fill=COLOR_TEXT)
    y = 24 + 40
    draw.line([(0, y), (CANVAS_WIDTH, y)], fill=COLOR_BORDER, width=1)
    y += 1

    # --- Hero (optional) ---
    hero_key = content.get("hero")
    if hero_key and hero_key != "none" and content.get("hero_image_url"):
        y = _draw_hero(canvas, hero_key, content.get("hero_headline"), y)

    y += 28
    draw = ImageDraw.Draw(canvas)  # re-bind after any paste() calls above

    # --- Greeting + opening lines ---
    body_font = _font(16, "Regular")
    draw.text((MARGIN, y), f"Hi {first_name},", font=body_font, fill=COLOR_TEXT)
    y += int(16 * 1.45) + 10

    for line in content.get("opening_lines") or []:
        y = _draw_wrapped(draw, line.replace("[name]", first_name), MARGIN, y, body_font, CONTENT_WIDTH, COLOR_TEXT)
        y += 10

    # --- What happens next (numbered circle badges instead of icons - on
    # brand, no icon asset library needed) ---
    steps = content.get("what_happens_next")
    if steps:
        box_top = y + 6
        box_padding = 20
        badge_r = 12
        step_font = _font(15, "Regular")
        title_font = _font(13, "SemiBold")
        num_font = _font(12, "Bold")

        text_x = MARGIN + box_padding + badge_r * 2 + 14
        text_width = CONTENT_WIDTH - 2 * box_padding - badge_r * 2 - 14

        # Pass 1: measure only (same wrap logic the actual draw uses below)
        # so the box background can be drawn BEFORE the content - drawing it
        # after would paint over the badges/text.
        inner_y = box_top + box_padding + int(13 * 1.6) + 8
        for step in steps:
            step_lines = _wrap(draw, step, step_font, text_width)
            step_height = len(step_lines) * int(step_font.size * 1.45)
            inner_y = max(inner_y + step_height, inner_y + badge_r * 2 + 6) + 10
        box_bottom = inner_y + box_padding - 10

        draw.rounded_rectangle(
            [MARGIN, box_top, CANVAS_WIDTH - MARGIN, box_bottom], radius=10, fill=COLOR_STEPS_BG
        )

        # Pass 2: actually draw title, badges, and step text on top of the box.
        inner_y = box_top + box_padding
        draw.text((MARGIN + box_padding, inner_y), "WHAT HAPPENS NEXT", font=title_font, fill=COLOR_MUTED)
        inner_y += int(13 * 1.6) + 8

        for i, step in enumerate(steps, start=1):
            badge_cy = inner_y + 10
            badge_cx = MARGIN + box_padding + badge_r
            draw.ellipse(
                [badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r],
                fill=COLOR_ACCENT,
            )
            num_text = str(i)
            num_w = draw.textlength(num_text, font=num_font)
            draw.text((badge_cx - num_w / 2, badge_cy - 8), num_text, font=num_font, fill=COLOR_WHITE)

            step_bottom = _draw_wrapped(draw, step, text_x, inner_y, step_font, text_width, COLOR_TEXT)
            inner_y = max(step_bottom, badge_cy + badge_r + 6) + 10

        y = box_bottom + 20

    # --- Gentle truth line ---
    if content.get("gentle_truth_line"):
        y = _draw_wrapped(draw, content["gentle_truth_line"], MARGIN, y, body_font, CONTENT_WIDTH, COLOR_TEXT)
        y += 20

    # --- CTA button ---
    cta_text = content.get("cta_text", "")
    cta_font = _font(15, "SemiBold")
    btn_padding_x, btn_padding_y = 28, 14
    btn_w = draw.textlength(cta_text, font=cta_font) + 2 * btn_padding_x
    btn_h = cta_font.size + 2 * btn_padding_y
    draw.rounded_rectangle([MARGIN, y, MARGIN + btn_w, y + btn_h], radius=6, fill=COLOR_ACCENT)
    draw.text((MARGIN + btn_padding_x, y + btn_padding_y - 2), cta_text, font=cta_font, fill=COLOR_WHITE)
    y += btn_h + 24

    # --- Trust line ---
    if content.get("trust_line"):
        trust_font = _font(13, "Regular")
        draw.text((MARGIN, y), content["trust_line"], font=trust_font, fill=COLOR_MUTED)
        y += int(13 * 1.6) + 20

    # --- Signature ---
    draw.text((MARGIN, y), "The andSons team", font=body_font, fill=COLOR_TEXT)
    y += int(16 * 1.45) + 20

    # --- Footer ---
    draw.line([(MARGIN, y), (CANVAS_WIDTH - MARGIN, y)], fill=COLOR_BORDER, width=1)
    y += 16
    footer_font = _font(12, "Regular")
    footer_lines = [
        "WhatsApp customer service",
        "andSons Pte. Ltd., 1 Fusionopolis Place, #17-10, Galaxis, Singapore 138522",
        "Unsubscribe",
    ]
    for line in footer_lines:
        fill = COLOR_ACCENT if line == "WhatsApp customer service" else COLOR_MUTED
        draw.text((MARGIN, y), line, font=footer_font, fill=fill)
        y += int(12 * 1.6)

    y += 24
    return canvas.crop((0, 0, CANVAS_WIDTH, y))
