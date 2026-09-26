"""Renders a generated OVA WhatsApp touchpoint as an actual PNG mockup of a
real WhatsApp Business "Call-To-Action" template message - the same idea as
email_image_renderer.py for emails, applied to OVA's other real channel.
Ported mechanically from the andSons backend's whatsapp_image_renderer.py -
this renders WhatsApp's OWN real dark-mode app chrome (colours, bubble
style), not an OVA-owned design, so nothing here needed to change except
the default link-preview title/domain fallbacks.

Pure Pillow, same approach and bundled font as email_image_renderer.py.
Renders content only - every string drawn here comes from the WhatsApp
touchpoint content the Copywriter already produced and the Sweeper already
checked; this module only lays it out visually.
"""
import os

from PIL import Image, ImageDraw, ImageFont

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_PATH = os.path.join(BACKEND_DIR, "static", "fonts", "Roboto-Variable.ttf")

# --- Real WhatsApp dark-mode UI tokens (the app's own actual palette, not
# an OVA brand token - matches the real WhatsApp app, identical to the
# andSons renderer this was ported from). ---
COLOR_BG = "#0b141a"           # WhatsApp dark-mode chat background
COLOR_DOODLE = "#182229"       # faint wallpaper doodle tint
COLOR_BUBBLE = "#202c33"       # dark-mode incoming message bubble
COLOR_CARD = "#2a3942"         # link-preview card, one shade lighter than the bubble
COLOR_TEXT = "#e9edef"
COLOR_MUTED = "#8696a0"
COLOR_LINK_GREEN = "#00a884"
COLOR_DIVIDER = "#384147"
COLOR_BUTTON_BG = "#2a3942"
COLOR_DATE_CHIP_BG = "#182229"

SCALE = 2
CANVAS_WIDTH = 380 * SCALE
MARGIN = 12 * SCALE
CARD_PADDING = 12 * SCALE
CARD_RADIUS = 8 * SCALE


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


def _draw_wrapped(draw, text, x, y, font, max_width, fill, line_height=None) -> int:
    line_height = line_height or int(font.size * 1.4)
    for line in _wrap(draw, text, font, max_width):
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _draw_doodle_background(canvas: Image.Image) -> None:
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    step = _s(58)
    for row, y in enumerate(range(_s(20), h, step)):
        for col, x in enumerate(range(_s(20), w, step)):
            if (row + col) % 3 == 0:
                continue
            shape = (row * 7 + col * 3) % 4
            cx, cy = x + (step // 3 if row % 2 else 0), y
            r = _s(6)
            if shape == 0:
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=COLOR_DOODLE, width=_s(1))
            elif shape == 1:
                draw.rectangle([cx - r, cy - r, cx + r, cy + r], outline=COLOR_DOODLE, width=_s(1))
            elif shape == 2:
                draw.line([(cx - r, cy), (cx + r, cy)], fill=COLOR_DOODLE, width=_s(1))
                draw.line([(cx, cy - r), (cx, cy + r)], fill=COLOR_DOODLE, width=_s(1))
            else:
                draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill=COLOR_DOODLE, width=_s(1))
                draw.line([(cx - r, cy + r), (cx + r, cy - r)], fill=COLOR_DOODLE, width=_s(1))


def _draw_link_icon(draw: ImageDraw.ImageDraw, x: int, y: int, size: int, color: str) -> None:
    lw = max(1, int(size * 0.18))
    draw.arc([x, y + size * 0.15, x + size * 0.6, y + size * 0.75], start=40, end=220, fill=color, width=lw)
    draw.arc([x + size * 0.4, y + size * 0.25, x + size, y + size * 0.85], start=220, end=40, fill=color, width=lw)


def render_whatsapp_image(content: dict, timing: str = "") -> Image.Image:
    """content: {"link_title", "hook_line", "body", "cta_text"} - the same
    dict shape as generate_flow()'s WhatsApp touchpoint content. Returns a
    PIL Image (RGB) ready to save as PNG, styled as a real WABA Call-To-
    Action template message (dark WhatsApp, link-preview card, bold hook
    line, one CTA button)."""
    link_title = content.get("link_title", "OVA")
    hook_line = content.get("hook_line", "")
    body_text = content.get("body", "")
    cta_text = content.get("cta_text", "")

    canvas = Image.new("RGB", (CANVAS_WIDTH, _s(900)), COLOR_BG)
    _draw_doodle_background(canvas)
    draw = ImageDraw.Draw(canvas)

    date_label = f"Sends {timing}" if timing else "Today"
    chip_font = _font(_s(11), "Medium")
    chip_w = draw.textlength(date_label, font=chip_font) + _s(20)
    chip_h = _s(24)
    chip_x = (CANVAS_WIDTH - chip_w) / 2
    y = _s(16)
    draw.rounded_rectangle([chip_x, y, chip_x + chip_w, y + chip_h], radius=chip_h / 2, fill=COLOR_DATE_CHIP_BG)
    draw.text((chip_x + _s(10), y + _s(5)), date_label, font=chip_font, fill=COLOR_MUTED)
    y += chip_h + _s(18)

    bubble_x0 = MARGIN
    bubble_x1 = CANVAS_WIDTH - MARGIN
    bubble_width = bubble_x1 - bubble_x0
    content_width = bubble_width - 2 * CARD_PADDING

    title_font = _font(_s(14), "Bold")
    domain_font = _font(_s(12), "Regular")
    hook_font = _font(_s(14), "Bold")
    body_font = _font(_s(14), "Regular")
    read_more_font = _font(_s(13), "Medium")
    time_font = _font(_s(11), "Regular")
    button_font = _font(_s(14), "Medium")

    title_lines = _wrap(draw, link_title, title_font, content_width)
    hook_wrapped = _wrap(draw, hook_line, hook_font, content_width)
    rest_wrapped = _wrap(draw, body_text, body_font, content_width) if body_text else []

    line_h = int(body_font.size * 1.4)
    card_height = CARD_PADDING + len(title_lines) * int(title_font.size * 1.3) + _s(6) + int(domain_font.size * 1.3) + CARD_PADDING
    message_block_height = len(hook_wrapped) * line_h + (len(rest_wrapped) * line_h if rest_wrapped else 0)
    read_more_row_h = int(read_more_font.size * 1.6)
    button_row_h = _s(46)

    bubble_y0 = y
    bubble_y1 = (
        bubble_y0 + card_height + _s(10) + message_block_height + _s(8)
        + read_more_row_h + _s(8) + button_row_h + CARD_PADDING
    )

    draw.rounded_rectangle([bubble_x0, bubble_y0, bubble_x1, bubble_y1], radius=_s(10), fill=COLOR_BUBBLE)

    card_y0 = bubble_y0
    card_y1 = card_y0 + card_height
    draw.rounded_rectangle([bubble_x0, card_y0, bubble_x1, card_y1], radius=_s(10), fill=COLOR_CARD)
    draw.rectangle([bubble_x0, card_y0 + _s(10), bubble_x1, card_y1], fill=COLOR_CARD)

    ty = card_y0 + CARD_PADDING
    tx = bubble_x0 + CARD_PADDING
    for line in title_lines:
        draw.text((tx, ty), line, font=title_font, fill=COLOR_TEXT)
        ty += int(title_font.size * 1.3)
    ty += _s(4)
    _draw_link_icon(draw, tx, ty, _s(12), COLOR_MUTED)
    draw.text((tx + _s(16), ty - _s(1)), "getova.com.sg", font=domain_font, fill=COLOR_MUTED)

    my = card_y1 + _s(10)
    mx = bubble_x0 + CARD_PADDING
    for line in hook_wrapped:
        draw.text((mx, my), line, font=hook_font, fill=COLOR_TEXT)
        my += line_h
    for line in rest_wrapped:
        draw.text((mx, my), line, font=body_font, fill=COLOR_TEXT)
        my += line_h

    my += _s(4)
    draw.text((mx, my), "Read more", font=read_more_font, fill=COLOR_LINK_GREEN)
    ts_text = "2:34 PM"
    ts_w = draw.textlength(ts_text, font=time_font)
    draw.text((bubble_x1 - CARD_PADDING - ts_w, my + _s(2)), ts_text, font=time_font, fill=COLOR_MUTED)
    my += read_more_row_h + _s(4)

    draw.line([(bubble_x0, my), (bubble_x1, my)], fill=COLOR_DIVIDER, width=_s(1))
    button_y0 = my
    button_y1 = bubble_y1
    draw.rounded_rectangle([bubble_x0, button_y0, bubble_x1, button_y1], radius=_s(10), fill=COLOR_BUTTON_BG)
    draw.rectangle([bubble_x0, button_y0, bubble_x1, button_y0 + _s(10)], fill=COLOR_BUTTON_BG)

    btn_label = cta_text
    btn_w = draw.textlength(btn_label, font=button_font)
    icon_size = _s(13)
    total_w = icon_size + _s(8) + btn_w
    btn_x = (CANVAS_WIDTH - total_w) / 2
    btn_cy = (button_y0 + button_y1) / 2
    icon_x, icon_y = btn_x, btn_cy - icon_size / 2
    draw.rectangle(
        [icon_x, icon_y + icon_size * 0.25, icon_x + icon_size * 0.75, icon_y + icon_size],
        outline=COLOR_LINK_GREEN, width=_s(1),
    )
    draw.line([(icon_x + icon_size * 0.45, icon_y + icon_size * 0.55), (icon_x + icon_size, icon_y)], fill=COLOR_LINK_GREEN, width=_s(1))
    draw.line([(icon_x + icon_size * 0.6, icon_y), (icon_x + icon_size, icon_y)], fill=COLOR_LINK_GREEN, width=_s(1))
    draw.line([(icon_x + icon_size, icon_y), (icon_x + icon_size, icon_y + icon_size * 0.4)], fill=COLOR_LINK_GREEN, width=_s(1))
    draw.text((btn_x + icon_size + _s(8), btn_cy - button_font.size / 2 - _s(1)), btn_label, font=button_font, fill=COLOR_LINK_GREEN)

    y = bubble_y1 + _s(20)
    return canvas.crop((0, 0, CANVAS_WIDTH, int(y)))
