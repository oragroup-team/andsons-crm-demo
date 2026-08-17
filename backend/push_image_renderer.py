"""Renders a generated push-notification touchpoint as a PNG mockup of a
real phone lock-screen notification banner - the same idea as
email_image_renderer.py/whatsapp_image_renderer.py, applied to the third
real andSons channel (confirmed live: the actual MoEngage workspace uses
only Email and Push, never WhatsApp, for every flow except P1's - see
flows.py's module docstring). Replicates the well-known, generic iOS-style
notification card convention (app icon, app name, timestamp, bold title,
body line) - not a brand invention, since the point is showing exactly
what a customer's lock screen actually looks like.

Pure Pillow, same approach and bundled font as the other two renderers.
Renders content only - every string drawn here comes from the push
touchpoint content the Copywriter already produced and the Sweeper already
checked; this module only lays it out visually.
"""
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BACKEND_DIR, "static", "fonts", "Roboto-Variable.ttf")

# --- Real lock-screen tokens - a blurred wallpaper-style backdrop behind a
# frosted-glass notification card, matching the generic modern-smartphone
# lock-screen convention (not tied to any one OS's exact chrome). ---
COLOR_BG_TOP = "#2b2440"
COLOR_BG_BOTTOM = "#1a1530"
COLOR_CARD = "#ffffffd9"  # translucent white, "frosted glass" look
COLOR_TEXT = "#1c1c1e"
COLOR_MUTED = "#6e6e73"
COLOR_ACCENT = "#963e21"  # andSons accent - just the app icon glyph
COLOR_CLOCK = "#ffffff"

SCALE = 2
CANVAS_WIDTH = 360 * SCALE
CARD_MARGIN = 14 * SCALE
CARD_PADDING = 14 * SCALE
CARD_RADIUS = 18 * SCALE
ICON_SIZE = 34 * SCALE


def _s(px: int) -> int:
    return px * SCALE


def _font(size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(FONT_PATH, size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
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


def _draw_lock_wallpaper(canvas: Image.Image) -> None:
    """A simple vertical-gradient wallpaper with soft blurred blobs, giving
    the "blurred photo behind the lock screen" look without needing a real
    photo asset - the notification card is the actual content, this is
    just backdrop texture."""
    w, h = canvas.size
    base = Image.new("RGB", (w, h), COLOR_BG_TOP)
    draw = ImageDraw.Draw(base)
    top = tuple(int(COLOR_BG_TOP[i:i+2], 16) for i in (1, 3, 5))
    bottom = tuple(int(COLOR_BG_BOTTOM[i:i+2], 16) for i in (1, 3, 5))
    for y in range(h):
        t = y / max(h - 1, 1)
        color = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        draw.line([(0, y), (w, y)], fill=color)

    blobs = Image.new("RGB", (w, h), (0, 0, 0))
    bdraw = ImageDraw.Draw(blobs)
    bdraw.ellipse([-w * 0.2, -h * 0.1, w * 0.6, h * 0.35], fill=(150, 62, 33))
    bdraw.ellipse([w * 0.4, h * 0.5, w * 1.2, h * 0.95], fill=(70, 40, 110))
    blobs = blobs.filter(ImageFilter.GaussianBlur(radius=_s(60)))
    base = Image.blend(base, blobs, 0.35)
    canvas.paste(base, (0, 0))


def _draw_app_icon(canvas: Image.Image, x: int, y: int, size: int) -> None:
    """Small rounded-square app icon with an "A" glyph - the one place
    brand identity shows on an otherwise generic notification card."""
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([x, y, x + size, y + size], radius=size * 0.28, fill=COLOR_ACCENT)
    letter_font = _font(int(size * 0.55), "Bold")
    letter = "A"
    lw = draw.textlength(letter, font=letter_font)
    draw.text((x + (size - lw) / 2, y + size * 0.18), letter, font=letter_font, fill="#ffffff")


def render_push_image(content: dict, timing: str = "") -> Image.Image:
    """content: {"title", "body"} - the same dict shape as generate_flow()'s
    push touchpoint content. Returns a PIL Image (RGB) ready to save as
    PNG, styled as a real phone lock-screen notification card."""
    title = content.get("title", "")
    body = content.get("body", "")

    canvas = Image.new("RGB", (CANVAS_WIDTH, _s(560)), COLOR_BG_TOP)
    _draw_lock_wallpaper(canvas)
    draw = ImageDraw.Draw(canvas)

    # --- Lock-screen clock, above the notification (generic big time
    # display, matches every modern phone's lock screen convention). ---
    clock_font = _font(_s(52), "Bold")
    date_font = _font(_s(13), "Medium")
    clock_text = "9:41"
    clock_w = draw.textlength(clock_text, font=clock_font)
    y = _s(28)
    draw.text(((CANVAS_WIDTH - clock_w) / 2, y), clock_text, font=clock_font, fill=COLOR_CLOCK)
    y += _s(60)
    date_text = "Monday, 17 August"
    date_w = draw.textlength(date_text, font=date_font)
    draw.text(((CANVAS_WIDTH - date_w) / 2, y), date_text, font=date_font, fill=COLOR_CLOCK)
    y += _s(40)

    # --- Notification card (frosted glass) ---
    card_x0 = CARD_MARGIN
    card_x1 = CANVAS_WIDTH - CARD_MARGIN
    content_width = (card_x1 - card_x0) - 2 * CARD_PADDING - ICON_SIZE - _s(10)

    title_font = _font(_s(15), "Bold")
    app_font = _font(_s(12), "Medium")
    time_font = _font(_s(12), "Regular")
    body_font = _font(_s(14), "Regular")

    title_lines = _wrap(draw, title, title_font, content_width)
    body_lines = _wrap(draw, body, body_font, content_width)

    header_h = int(app_font.size * 1.3)
    line_h = int(body_font.size * 1.35)
    text_block_h = header_h + _s(2) + len(title_lines) * int(title_font.size * 1.3) + _s(2) + len(body_lines) * line_h
    card_h = max(text_block_h, ICON_SIZE) + 2 * CARD_PADDING

    card_y0 = y
    card_y1 = card_y0 + card_h

    # Frosted glass card - draw on an RGBA overlay so it's translucent
    # against the wallpaper underneath, then composite.
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    card_rgba = tuple(int(COLOR_CARD[i:i+2], 16) for i in (1, 3, 5)) + (int(COLOR_CARD[7:9], 16),)
    odraw.rounded_rectangle([card_x0, card_y0, card_x1, card_y1], radius=CARD_RADIUS, fill=card_rgba)
    canvas.paste(Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB"), (0, 0))
    draw = ImageDraw.Draw(canvas)

    icon_x = card_x0 + CARD_PADDING
    icon_y = card_y0 + CARD_PADDING
    _draw_app_icon(canvas, icon_x, icon_y, ICON_SIZE)
    draw = ImageDraw.Draw(canvas)

    text_x = icon_x + ICON_SIZE + _s(10)
    text_y = card_y0 + CARD_PADDING

    header_label = "andSons"
    draw.text((text_x, text_y), header_label, font=app_font, fill=COLOR_MUTED)
    time_label = f"{timing}" if timing else "now"
    time_w = draw.textlength(time_label, font=time_font)
    draw.text((card_x1 - CARD_PADDING - time_w, text_y - _s(1)), time_label, font=time_font, fill=COLOR_MUTED)
    text_y += header_h + _s(2)

    for line in title_lines:
        draw.text((text_x, text_y), line, font=title_font, fill=COLOR_TEXT)
        text_y += int(title_font.size * 1.3)
    text_y += _s(2)
    for line in body_lines:
        draw.text((text_x, text_y), line, font=body_font, fill=COLOR_TEXT)
        text_y += line_h

    return canvas.crop((0, 0, CANVAS_WIDTH, card_y1 + _s(24)))
