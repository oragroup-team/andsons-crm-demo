"""Renders a generated OVA email as a PNG matching the team's real
stakeholder-confirmed template (a real, full-length OVA "Mounjaro Program"
onboarding email, reviewed in full): four fixed zones, per the stakeholder's
own words - "Logo -> Hero banner & CTA -> Body content -> Footer & Closing
CTA":

1. LOGO - a full-width solid dark-purple bar, white "ova" wordmark centred.
2. HERO BANNER & CTA - bone background: centred headline, one subcopy line,
   a centred CTA button, then the hero photo cropped into a CIRCLE on a
   soft lavender backdrop (not a rectangular card - a real, deliberate
   difference from the previous template).
3. BODY CONTENT - the one deliberately FLEXIBLE zone. The reference email
   itself mixes several different content modules (a plain text paragraph,
   a standalone product-shot image, a full-width dark divider bar acting as
   a section header, a 3-column icon-grid, a two-column photo+text module,
   a dark rounded callout card, a checklist card) rather than repeating one
   fixed shape - so this renderer draws body content from an ordered list
   of typed BLOCKS the Copywriter chooses and orders per email, not a fixed
   set of fields. See BLOCK TYPES below.
4. FOOTER & CLOSING CTA - a full-width, edge-to-edge dark-purple block:
   left-aligned heading, left-aligned checkmark bullets, an outlined
   button, and a second photo bleeding off the right edge. This is fixed/
   mandatory on every email, matching the reference - the standard legal
   footer (disclaimer, unsubscribe, copyright) renders below it in plain
   text, unchanged from before.

BLOCK TYPES (body content, zone 3) - see copywriter_agent.py's Block union
for the exact schema each one expects:
  text            - one or more plain paragraphs, no card
  image_spotlight - one standalone bank photo, no text overlay
  divider_header  - full-width dark bar, centred white text, section header
  icon_grid       - exactly 3 white cards, an icon + one short caption each
  two_column      - a bank photo on one side, heading+paragraph(+icon rows)
                    on the other
  callout_card    - a dark rounded card, centred white heading + body
  checklist_card  - a white rounded card, checkmarked lines with dividers
  price_card      - unchanged from the previous template: light card,
                    label + big price + one button - only when a flow
                    genuinely has a real price decision to make (the
                    reference email itself has no price card; this stays
                    available for flows that actually need one, e.g. a
                    package-choice moment)

Hero selection is bank-only (image_bank.py, curated from the real
`01_OVA_Rebrand_2025` asset folder). A single email can now use MORE THAN
ONE bank photo (the top hero, the closing-footer photo, and any
image_spotlight/two_column blocks in between) - copywriter_agent.py is
responsible for making sure every image used within one email is visually
distinct from every other image in that SAME email, on top of the existing
cross-touchpoint uniqueness rule.

Brand tokens are the real OVA BRAND RESTAGE (May 2025) colours: bone
`#F2F0E9` ground, purple `#3D1A49` for headlines/buttons/dark blocks. Fonts
are the real brand pairing: DM Sans for headlines, Space Grotesk for body.

Pure Pillow. Renders content only - never invents copy.
"""
import os
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from image_bank import HERO_BANK

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(BACKEND_DIR, "static", "fonts")
HERO_DIR = os.path.join(BACKEND_DIR, "static", "hero_images")

# Real OVA brand palette (OVA BRAND RESTAGE May 2025, "Our colour palette").
COLOR_GROUND = "#F2F0E9"      # bone (page background)
COLOR_CARD = "#F7F4EF"        # a hair lighter than the page - light cards
COLOR_BRAND = "#3D1A49"       # purple - headlines, buttons, dark blocks
COLOR_BODY = "#4A3F52"        # softened purple-grey for longer body copy
COLOR_DARK = "#221C26"        # near-black, for the price figure
COLOR_MUTED = "#8A8290"       # subcopy, footer, muted labels
COLOR_WHITE = "#FFFFFF"
COLOR_LAVENDER = "#E4D3EF"    # soft lavender hero backdrop, tinted from the brand purple
COLOR_ICON_BADGE_BG = "#EFE7F2"  # light lavender badge behind an icon
COLOR_RULE = "#E1DAD3"

SCALE = 2
CANVAS_WIDTH = 600 * SCALE
MARGIN = 28 * SCALE
CONTENT_WIDTH = CANVAS_WIDTH - 2 * MARGIN

_DM_REGULAR = os.path.join(FONT_DIR, "DMSans-Regular.ttf")
_DM_MEDIUM = os.path.join(FONT_DIR, "DMSans-Medium.ttf")
_DM_BOLD = os.path.join(FONT_DIR, "DMSans-Bold.ttf")
_SG_REGULAR = os.path.join(FONT_DIR, "SpaceGrotesk-Regular.ttf")
_SG_MEDIUM = os.path.join(FONT_DIR, "SpaceGrotesk-Medium.ttf")
_SG_BOLD = os.path.join(FONT_DIR, "SpaceGrotesk-Bold.ttf")


def _s(px: int) -> int:
    return px * SCALE


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, _s(size))


def _headline_font(size: int) -> ImageFont.FreeTypeFont:
    return _font(_DM_BOLD, size)


def _body_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return _font(_SG_BOLD if bold else _SG_REGULAR, size)


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


def _draw_wrapped(draw, text, x, y, font, max_width, fill, line_height=None, align="left") -> int:
    line_height = line_height or int(font.size * 1.5)
    for line in _wrap(draw, text, font, max_width):
        line_w = draw.textlength(line, font=font)
        lx = x + (max_width - line_w) / 2 if align == "center" else x
        draw.text((lx, y), line, font=font, fill=fill)
        y += line_height
    return y


def _measure_wrapped_height(draw, text, font, max_width, line_height=None) -> int:
    line_height = line_height or int(font.size * 1.5)
    return len(_wrap(draw, text, font, max_width)) * line_height


def _draw_button(draw, canvas_width, y, label, font, fill=COLOR_BRAND, text_fill=COLOR_WHITE, x_center=None, width=None) -> int:
    """A real rounded pill CTA button. Centred on `x_center` (defaults to
    the full canvas centre) so it can be re-used inside a narrower column
    (e.g. the dark closing footer's left-aligned block)."""
    pad_x, pad_y = _s(26), _s(13)
    btn_w = draw.textlength(label, font=font) + 2 * pad_x
    btn_h = font.size + 2 * pad_y
    center = x_center if x_center is not None else canvas_width / 2
    btn_x = center - btn_w / 2
    draw.rounded_rectangle([btn_x, y, btn_x + btn_w, y + btn_h], radius=btn_h / 2, fill=fill)
    draw.text((btn_x + pad_x, y + pad_y - _s(1)), label, font=font, fill=text_fill)
    return y + btn_h


def _draw_button_left(draw, x, y, label, font, fill, text_fill) -> int:
    """Same as _draw_button but left-aligned at `x` - used by the dark
    closing footer, which is left-aligned rather than centred."""
    pad_x, pad_y = _s(26), _s(13)
    btn_w = draw.textlength(label, font=font) + 2 * pad_x
    btn_h = font.size + 2 * pad_y
    draw.rounded_rectangle([x, y, x + btn_w, y + btn_h], radius=btn_h / 2, outline=fill, width=max(1, _s(2)))
    draw.text((x + pad_x, y + pad_y - _s(1)), label, font=font, fill=fill)
    return y + btn_h


def _draw_icon(draw, kind, cx, cy, r, color, bg):
    """kind: 0=crescent moon, 1=trend line, 2=ring, 3=checkmark, 4=arrow-down
    (cycled by index). Simple brand-neutral line-icon glyphs, not a literal
    copy of any reference's own icon set."""
    kind = kind % 5
    if kind == 0:
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)
        offset = r * 0.55
        draw.ellipse([cx - r + offset, cy - r, cx + r + offset, cy + r], fill=bg)
    elif kind == 1:
        lw = max(1, int(r * 0.3))
        draw.line([(cx - r, cy + r * 0.4), (cx - r * 0.2, cy - r * 0.3), (cx + r * 0.3, cy + r * 0.1), (cx + r, cy - r * 0.6)], fill=color, width=lw, joint="curve")
    elif kind == 2:
        lw = max(1, int(r * 0.32))
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=lw)
    elif kind == 3:
        lw = max(1, int(r * 0.3))
        draw.line([(cx - r * 0.7, cy), (cx - r * 0.15, cy + r * 0.55), (cx + r * 0.75, cy - r * 0.6)], fill=color, width=lw, joint="curve")
    else:
        lw = max(1, int(r * 0.28))
        draw.line([(cx, cy - r), (cx, cy + r * 0.7)], fill=color, width=lw)
        draw.line([(cx - r * 0.5, cy + r * 0.2), (cx, cy + r * 0.8), (cx + r * 0.5, cy + r * 0.2)], fill=color, width=lw, joint="curve")


def _checkmark_icon(draw, cx, cy, r, color):
    lw = max(1, int(r * 0.32))
    draw.line([(cx - r * 0.7, cy), (cx - r * 0.15, cy + r * 0.55), (cx + r * 0.75, cy - r * 0.6)], fill=color, width=lw, joint="curve")


def _cover_crop(photo: Image.Image, width: int, height: int, top_bias: float = 0.5) -> Image.Image:
    """Centre-crops+resizes a photo to exactly fill width x height (a
    'cover' fit, never distorted/stretched). top_bias controls where the
    vertical crop window sits when the source needs its height trimmed:
    0.5 (default) is a pure centre crop, right for generic body-content
    images (product shots, icons) where the subject is genuinely centred.

    Real, live-caught bug this parameter fixes for hero PHOTOS OF PEOPLE
    specifically (see _draw_hero_rect/_draw_hero_circle, which now pass a
    low top_bias): every real portrait in the hero bank is shot with
    headroom ABOVE the head, standard portrait-photography convention -
    a pure 50/50 centre crop on a tall source needing a much shorter/
    squarer target keeps only the vertical middle, landing on the torso
    and cropping the head off entirely (confirmed live: exactly this
    happened with confidentease.jpg and warmlook.jpg). A low top_bias
    keeps most of the top of the frame (head + face + some torso)
    instead, sacrificing the lower body/legs - the right trade for a
    hero photo, wrong for a generic centred product shot, hence this
    being a per-call parameter, not a new default for every caller."""
    target_ratio = width / height
    src_ratio = photo.width / photo.height
    if src_ratio > target_ratio:
        new_width = int(photo.height * target_ratio)
        left = (photo.width - new_width) // 2
        photo = photo.crop((left, 0, left + new_width, photo.height))
    else:
        new_height = int(photo.width / target_ratio)
        top = int((photo.height - new_height) * top_bias)
        photo = photo.crop((0, top, photo.width, top + new_height))
    return photo.resize((width, height), Image.LANCZOS)


def _load_hero(hero_key: str) -> Optional[Image.Image]:
    path = os.path.join(HERO_DIR, f"{hero_key}.jpg")
    if not os.path.isfile(path):
        return None
    return Image.open(path).convert("RGB")


def _draw_hero_rect(canvas: Image.Image, hero_key: str, x0: int, y: int, width: int, target_h: int, radius: int = 10) -> int:
    """A rounded-rect photo inset on a lavender backdrop - used by
    image_spotlight and two_column blocks. Fails soft (blank lavender) if
    the file is somehow missing."""
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([x0, y, x0 + width, y + target_h], radius=_s(radius), fill=COLOR_LAVENDER)
    photo = _load_hero(hero_key)
    if photo:
        photo = _cover_crop(photo, width, target_h, top_bias=0.08)
        mask = Image.new("L", (width, target_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, width, target_h], radius=_s(radius), fill=255)
        canvas.paste(photo, (x0, y), mask)
    return y + target_h


def _draw_hero_circle(canvas: Image.Image, hero_key: str, cx: int, cy: int, r: int) -> None:
    """The zone-2 hero treatment: a real bank photo cropped into a circle
    of radius r, centred at (cx, cy), on a slightly larger soft-lavender
    circular backdrop - matches the reference template's real hero
    treatment (a real, deliberate difference from the old rectangular
    hero card)."""
    draw = ImageDraw.Draw(canvas)
    backdrop_r = int(r * 1.18)
    draw.ellipse([cx - backdrop_r, cy - backdrop_r, cx + backdrop_r, cy + backdrop_r], fill=COLOR_LAVENDER)
    photo = _load_hero(hero_key)
    if photo:
        d = r * 2
        photo = _cover_crop(photo, d, d, top_bias=0.08)
        mask = Image.new("L", (d, d), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, d, d], fill=255)
        canvas.paste(photo, (cx - r, cy - r), mask)


# === Body-content block renderers ==========================================
# Each function draws one block starting at `y` and returns the new `y`.
# All operate on the full CONTENT_WIDTH (MARGIN to CANVAS_WIDTH-MARGIN)
# unless noted otherwise (divider_header and the closing footer bleed edge
# to edge instead).

def _block_text(canvas, draw, y, block, first_name) -> int:
    font = _body_font(14)
    for para in block.get("paragraphs") or []:
        y = _draw_wrapped(draw, para.replace("[name]", first_name), MARGIN, y, font, CONTENT_WIDTH, COLOR_BODY, line_height=int(font.size * 1.55), align="center")
        y += _s(14)
    return y


def _block_image_spotlight(canvas, draw, y, block, first_name) -> int:
    hero_key = block.get("image")
    if not hero_key or hero_key == "none":
        return y
    width = int(CONTENT_WIDTH * 0.62)
    x0 = MARGIN + (CONTENT_WIDTH - width) // 2
    height = int(width * 0.62)
    y = _draw_hero_rect(canvas, hero_key, x0, y, width, height, radius=12)
    return y + _s(20)


def _block_divider_header(canvas, draw, y, block, first_name) -> int:
    text = block.get("text", "")
    font = _body_font(13, bold=True)
    bar_h = _s(56)
    draw.rectangle([0, y, CANVAS_WIDTH, y + bar_h], fill=COLOR_BRAND)
    icon_r = _s(9)
    text_w = draw.textlength(text, font=font)
    cx = CANVAS_WIDTH / 2
    cy = y + bar_h / 2
    draw.text((cx - text_w / 2, cy - font.size / 2 - _s(2)), text, font=font, fill=COLOR_WHITE)
    for side in (-1, 1):
        icon_cx = cx + side * (text_w / 2 + _s(30))
        _draw_icon(draw, 4, icon_cx, cy, icon_r, COLOR_WHITE, COLOR_BRAND)
    return y + bar_h + _s(24)


def _block_icon_grid(canvas, draw, y, block, first_name) -> int:
    items = (block.get("items") or [])[:3]
    if not items:
        return y
    if block.get("intro"):
        intro_font = _body_font(13)
        y = _draw_wrapped(draw, block["intro"], MARGIN, y, intro_font, CONTENT_WIDTH, COLOR_BODY, line_height=int(intro_font.size * 1.5), align="center")
        y += _s(16)
    gap = _s(14)
    col_w = (CONTENT_WIDTH - gap * (len(items) - 1)) // len(items)
    cap_font = _body_font(12, bold=True)
    badge_r = _s(20)
    pad_top = _s(22)
    cap_max_h = _measure_wrapped_height(draw, "X\nX", cap_font, col_w - _s(16), int(cap_font.size * 1.35))
    card_h = pad_top + badge_r * 2 + _s(14) + cap_max_h + _s(18)
    for i, item in enumerate(items):
        cx0 = MARGIN + i * (col_w + gap)
        draw.rounded_rectangle([cx0, y, cx0 + col_w, y + card_h], radius=_s(12), fill=COLOR_CARD)
        badge_cx, badge_cy = cx0 + col_w / 2, y + pad_top + badge_r
        draw.ellipse([badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r], outline=COLOR_BRAND, width=max(1, _s(2)))
        _draw_icon(draw, i, badge_cx, badge_cy, badge_r * 0.5, COLOR_BRAND, COLOR_CARD)
        cap_y = badge_cy + badge_r + _s(14)
        heading = item.get("heading", "") if isinstance(item, dict) else item.heading
        _draw_wrapped(draw, heading, cx0 + _s(8), cap_y, cap_font, col_w - _s(16), COLOR_BRAND, line_height=int(cap_font.size * 1.35), align="center")
    return y + card_h + _s(24)


def _block_two_column(canvas, draw, y, block, first_name) -> int:
    image_side = block.get("image_side", "left")
    gap = _s(20)
    img_w = int(CONTENT_WIDTH * 0.36)
    text_w = CONTENT_WIDTH - img_w - gap
    text_x = MARGIN + (img_w + gap if image_side == "left" else 0)
    img_x = MARGIN + (0 if image_side == "left" else text_w + gap)

    head_font = _headline_font(17)
    para_font = _body_font(13)
    icon_items = block.get("icon_items") or []
    head_h = _measure_wrapped_height(draw, block.get("heading", ""), head_font, text_w, int(head_font.size * 1.3))
    para_h = _measure_wrapped_height(draw, block.get("paragraph", ""), para_font, text_w, int(para_font.size * 1.55))
    icon_row_h = _s(30)
    text_h = head_h + _s(10) + para_h + _s(14) + len(icon_items) * icon_row_h
    img_h = max(int(img_w * 1.15), text_h)

    ty = y
    ty = _draw_wrapped(draw, block.get("heading", ""), text_x, ty, head_font, text_w, COLOR_BRAND, line_height=int(head_font.size * 1.3))
    ty += _s(10)
    ty = _draw_wrapped(draw, block.get("paragraph", "").replace("[name]", first_name), text_x, ty, para_font, text_w, COLOR_BODY, line_height=int(para_font.size * 1.55))
    ty += _s(14)
    icon_font = _body_font(13)
    for i, entry in enumerate(icon_items):
        text = entry if isinstance(entry, str) else entry.get("text", "")
        r = _s(11)
        icon_cx, icon_cy = text_x + r, ty + r
        draw.ellipse([icon_cx - r, icon_cy - r, icon_cx + r, icon_cy + r], fill=COLOR_ICON_BADGE_BG)
        _draw_icon(draw, i, icon_cx, icon_cy, r * 0.55, COLOR_BRAND, COLOR_ICON_BADGE_BG)
        draw.text((text_x + r * 2 + _s(10), ty), text, font=icon_font, fill=COLOR_BODY)
        ty += icon_row_h

    hero_key = block.get("image")
    if hero_key and hero_key != "none":
        _draw_hero_rect(canvas, hero_key, img_x, y, img_w, img_h, radius=14)

    return y + img_h + _s(24)


def _block_callout_card(canvas, draw, y, block, first_name) -> int:
    pad = _s(24)
    head_font = _body_font(14, bold=True)
    body_font = _body_font(13)
    head_h = _measure_wrapped_height(draw, block.get("heading", ""), head_font, CONTENT_WIDTH - 2 * pad, int(head_font.size * 1.4))
    body_h = _measure_wrapped_height(draw, block.get("body", ""), body_font, CONTENT_WIDTH - 2 * pad, int(body_font.size * 1.5))
    card_h = pad + head_h + _s(8) + body_h + pad
    draw.rounded_rectangle([MARGIN, y, CANVAS_WIDTH - MARGIN, y + card_h], radius=_s(14), fill=COLOR_BRAND)
    cy = y + pad
    cy = _draw_wrapped(draw, block.get("heading", ""), MARGIN + pad, cy, head_font, CONTENT_WIDTH - 2 * pad, COLOR_WHITE, line_height=int(head_font.size * 1.4), align="center")
    cy += _s(8)
    _draw_wrapped(draw, block.get("body", ""), MARGIN + pad, cy, body_font, CONTENT_WIDTH - 2 * pad, "#E4D3EF", line_height=int(body_font.size * 1.5), align="center")
    return y + card_h + _s(24)


def _block_checklist_card(canvas, draw, y, block, first_name) -> int:
    if block.get("heading"):
        head_font = _headline_font(18)
        y = _draw_wrapped(draw, block["heading"], MARGIN, y, head_font, CONTENT_WIDTH, COLOR_BRAND, line_height=int(head_font.size * 1.3), align="center")
        y += _s(10)
    if block.get("subtext"):
        sub_font = _body_font(13)
        y = _draw_wrapped(draw, block["subtext"], MARGIN, y, sub_font, CONTENT_WIDTH, COLOR_MUTED, line_height=int(sub_font.size * 1.5), align="center")
        y += _s(18)

    items = block.get("items") or []
    pad = _s(22)
    item_font = _body_font(13)
    row_h = _s(44)
    card_h = pad * 2 + row_h * len(items) - (row_h - int(item_font.size * 1.5)) if items else 0
    card_h = pad + sum(max(row_h, _measure_wrapped_height(draw, it, item_font, CONTENT_WIDTH - 2 * pad - _s(36), int(item_font.size * 1.4))) for it in items) + pad
    draw.rounded_rectangle([MARGIN, y, CANVAS_WIDTH - MARGIN, y + card_h], radius=_s(14), fill=COLOR_CARD)
    iy = y + pad
    for i, item in enumerate(items):
        r = _s(11)
        icon_cx, icon_cy = MARGIN + pad + r, iy + r
        draw.ellipse([icon_cx - r, icon_cy - r, icon_cx + r, icon_cy + r], outline=COLOR_BRAND, width=max(1, _s(2)))
        _checkmark_icon(draw, icon_cx, icon_cy, r * 0.6, COLOR_BRAND)
        text_x = MARGIN + pad + r * 2 + _s(12)
        row_text_h = _draw_wrapped(draw, item, text_x, iy, item_font, CONTENT_WIDTH - 2 * pad - _s(36), COLOR_BODY, line_height=int(item_font.size * 1.4)) - iy
        row_used_h = max(row_h, row_text_h)
        iy += row_used_h
        if i < len(items) - 1:
            draw.line([(MARGIN + pad, iy - _s(6)), (CANVAS_WIDTH - MARGIN - pad, iy - _s(6))], fill=COLOR_RULE, width=max(1, _s(1)))
    y = y + card_h + _s(20)
    if block.get("closing_note"):
        note_font = _body_font(13)
        y = _draw_wrapped(draw, block["closing_note"], MARGIN, y, note_font, CONTENT_WIDTH, COLOR_MUTED, line_height=int(note_font.size * 1.5), align="center")
        y += _s(20)
    return y


def _block_price_card(canvas, draw, y, block, first_name) -> int:
    """A light card, label + big price figure + optional smaller caption +
    one button. Only used when a flow genuinely has a real price decision
    (the reference email itself has no price card)."""
    pad = _s(24)
    label_font = _body_font(12)
    price_font = _headline_font(28)
    caption_font = _body_font(12)
    cta_font = _body_font(13, bold=True)
    label = block.get("label", "")
    price = block.get("value", "")
    caption = block.get("caption")
    cta = block.get("cta_text", "")
    card_h = pad + int(label_font.size * 1.6) + int(price_font.size * 1.4)
    if caption:
        card_h += int(caption_font.size * 1.6)
    card_h += _s(16) + (cta_font.size + _s(26)) + pad
    draw.rounded_rectangle([MARGIN, y, CANVAS_WIDTH - MARGIN, y + card_h], radius=_s(14), fill=COLOR_CARD)
    inner_y = y + pad
    label_w = draw.textlength(label, font=label_font)
    draw.text(((CANVAS_WIDTH - label_w) / 2, inner_y), label, font=label_font, fill=COLOR_MUTED)
    inner_y += int(label_font.size * 1.6)
    price_w = draw.textlength(price, font=price_font)
    draw.text(((CANVAS_WIDTH - price_w) / 2, inner_y), price, font=price_font, fill=COLOR_DARK)
    inner_y += int(price_font.size * 1.4)
    if caption:
        caption_w = draw.textlength(caption, font=caption_font)
        draw.text(((CANVAS_WIDTH - caption_w) / 2, inner_y), caption, font=caption_font, fill=COLOR_MUTED)
        inner_y += int(caption_font.size * 1.6)
    inner_y += _s(16)
    _draw_button(draw, CANVAS_WIDTH, inner_y, cta, cta_font)
    return y + card_h + _s(24)


_BLOCK_RENDERERS = {
    "text": _block_text,
    "image_spotlight": _block_image_spotlight,
    "divider_header": _block_divider_header,
    "icon_grid": _block_icon_grid,
    "two_column": _block_two_column,
    "callout_card": _block_callout_card,
    "checklist_card": _block_checklist_card,
    "price_card": _block_price_card,
}


def render_email_image(content: dict, first_name: str) -> Image.Image:
    """content: the dict shape generate_email()/run_*_pipeline() produce.
    Returns a PIL Image (RGB) matching the real stakeholder-confirmed
    4-zone template (Logo -> Hero banner & CTA -> Body content (flexible
    blocks) -> Footer & Closing CTA)."""
    canvas = Image.new("RGB", (CANVAS_WIDTH, _s(7000)), COLOR_GROUND)
    draw = ImageDraw.Draw(canvas)

    # === ZONE 1: Logo - full-width dark bar, white wordmark ============
    logo_h = _s(70)
    draw.rectangle([0, 0, CANVAS_WIDTH, logo_h], fill=COLOR_BRAND)
    wordmark_font = _headline_font(20)
    wm_w = draw.textlength("ova", font=wordmark_font)
    draw.text(((CANVAS_WIDTH - wm_w) / 2, (logo_h - wordmark_font.size) / 2 - _s(2)), "ova", font=wordmark_font, fill=COLOR_WHITE)
    y = logo_h + _s(36)

    # === ZONE 2: Hero banner & CTA =======================================
    headline_font = _headline_font(25)
    intro_font = _body_font(14)
    cta_font = _body_font(14, bold=True)

    y = _draw_wrapped(draw, content.get("subject", ""), MARGIN, y, headline_font, CONTENT_WIDTH, COLOR_BRAND, line_height=int(headline_font.size * 1.3), align="center")
    y += _s(14)
    if content.get("intro"):
        y = _draw_wrapped(draw, content["intro"].replace("[name]", first_name), MARGIN, y, intro_font, CONTENT_WIDTH, COLOR_BODY, line_height=int(intro_font.size * 1.5), align="center")
        y += _s(20)
    y = _draw_button(draw, CANVAS_WIDTH, y, content.get("cta_text", ""), cta_font)
    y += _s(36)

    hero_key = content.get("hero")
    if hero_key and hero_key != "none":
        hero_r = _s(105)
        _draw_hero_circle(canvas, hero_key, CANVAS_WIDTH // 2, y + hero_r, hero_r)
        draw = ImageDraw.Draw(canvas)  # re-bind after paste()
        y += int(hero_r * 1.18) * 2 + _s(30)
    else:
        y += _s(10)

    # === ZONE 3: Body content - flexible, ordered blocks =================
    for block in content.get("blocks") or []:
        block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        renderer = _BLOCK_RENDERERS.get(block_type)
        if renderer is None:
            continue
        block_dict = block if isinstance(block, dict) else block.model_dump()
        y = renderer(canvas, draw, y, block_dict, first_name)

    y += _s(10)

    # === ZONE 4: Footer & Closing CTA - edge-to-edge dark block ==========
    closing_heading = content.get("closing_heading")
    if closing_heading:
        pad = _s(30)
        closing_image = content.get("closing_image")
        has_closing_image = bool(closing_image and closing_image != "none")
        # Full-width text when there's no second photo (a genuine, common
        # case now that closing_image is optional - see copywriter_agent.py)
        # rather than always leaving a truncated column for an image that
        # was never going to be drawn.
        text_w = int(CANVAS_WIDTH * 0.56) if has_closing_image else CONTENT_WIDTH
        closing_head_font = _headline_font(21)
        bullet_font = _body_font(13)
        cta_font2 = _body_font(13, bold=True)
        bullets = content.get("closing_bullets") or []

        head_h = _measure_wrapped_height(draw, closing_heading, closing_head_font, text_w, int(closing_head_font.size * 1.3))
        bullet_row_h = _s(38)
        bullets_h = sum(max(bullet_row_h, _measure_wrapped_height(draw, b, bullet_font, text_w - _s(36), int(bullet_font.size * 1.4))) for b in bullets)
        btn_h = cta_font2.size + _s(26)
        block_h = pad + head_h + _s(18) + bullets_h + _s(22) + btn_h + pad
        if has_closing_image:
            block_h = max(block_h, _s(260))

        block_top = y
        draw.rectangle([0, block_top, CANVAS_WIDTH, block_top + block_h], fill=COLOR_BRAND)
        ty = block_top + pad
        ty = _draw_wrapped(draw, closing_heading, MARGIN, ty, closing_head_font, text_w, COLOR_WHITE, line_height=int(closing_head_font.size * 1.3))
        ty += _s(18)
        for i, b in enumerate(bullets):
            r = _s(11)
            icon_cx, icon_cy = MARGIN + r, ty + r
            draw.ellipse([icon_cx - r, icon_cy - r, icon_cx + r, icon_cy + r], fill=COLOR_WHITE)
            _checkmark_icon(draw, icon_cx, icon_cy, r * 0.55, COLOR_BRAND)
            row_h = _draw_wrapped(draw, b, MARGIN + r * 2 + _s(12), ty, bullet_font, text_w - r * 2 - _s(12), COLOR_WHITE, line_height=int(bullet_font.size * 1.4)) - ty
            ty += max(bullet_row_h, row_h)
        ty += _s(22)
        _draw_button_left(draw, MARGIN, ty, content.get("closing_cta_text", content.get("cta_text", "")), cta_font2, COLOR_WHITE, COLOR_WHITE)

        if has_closing_image:
            img_w = CANVAS_WIDTH - (MARGIN + text_w + _s(10))
            photo = _load_hero(closing_image)
            if photo:
                photo = _cover_crop(photo, img_w, block_h)
                canvas.paste(photo, (CANVAS_WIDTH - img_w, block_top), None)
                draw = ImageDraw.Draw(canvas)

        y = block_top + block_h + _s(30)

    # === Standard legal footer (unchanged - plain text, compliance) ======
    footer_font = _body_font(11)
    if content.get("footer_disclaimer"):
        y = _draw_wrapped(draw, content["footer_disclaimer"], MARGIN, y, footer_font, CONTENT_WIDTH, COLOR_MUTED, line_height=int(footer_font.size * 1.65), align="center")
        y += _s(26)

    icon_size = _s(30)
    gap = _s(12)
    total_w = icon_size * 2 + gap
    ix = (CANVAS_WIDTH - total_w) / 2
    for i in range(2):
        draw.rounded_rectangle([ix, y, ix + icon_size, y + icon_size], radius=_s(8), fill=COLOR_BRAND)
        _draw_icon(draw, i, ix + icon_size / 2, y + icon_size / 2, icon_size * 0.22, COLOR_WHITE, COLOR_BRAND)
        ix += icon_size + gap
    y += icon_size + _s(24)

    legal_font = _body_font(11)
    legal_text = (
        "The content of this email is for informational purposes only and does not constitute medical "
        "advice. Please consult a qualified healthcare professional before making any health-related decisions."
    )
    y = _draw_wrapped(draw, legal_text, MARGIN, y, legal_font, CONTENT_WIDTH, COLOR_MUTED, line_height=int(legal_font.size * 1.6), align="center")
    y += _s(18)

    signup_text = "You received this because you signed up at getova.com.sg."
    y = _draw_wrapped(draw, signup_text, MARGIN, y, footer_font, CONTENT_WIDTH, COLOR_MUTED, line_height=int(footer_font.size * 1.6), align="center")
    y += _s(16)

    unsub_text = "Unsubscribe | Privacy Policy"
    unsub_w = draw.textlength(unsub_text, font=footer_font)
    draw.text(((CANVAS_WIDTH - unsub_w) / 2, y), unsub_text, font=footer_font, fill=COLOR_BRAND)
    y += int(footer_font.size * 1.8)

    copyright_text = "Copyright © 2026 OVA. All rights reserved."
    copyright_w = draw.textlength(copyright_text, font=footer_font)
    draw.text(((CANVAS_WIDTH - copyright_w) / 2, y), copyright_text, font=footer_font, fill=COLOR_MUTED)
    y += int(footer_font.size * 1.8) + _s(20)

    return canvas.crop((0, 0, CANVAS_WIDTH, y))
