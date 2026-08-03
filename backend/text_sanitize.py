"""Shared defensive text cleanup, used by both the Copywriter and Analytics
agents. Prompt instructions alone don't reliably stop a model from slipping
in markdown or "smart" typographic characters (em-dashes, curly quotes, non-
breaking hyphens/spaces), so strip them here too rather than trusting the
prompt to be followed perfectly - this also enforces the real andSons house
rule "never use em-dashes or long dashes as punctuation in customer copy".

Every character class below is built from \\uXXXX escapes in plain
(non-raw) strings, never literal glyphs, so this file's meaning never
depends on an editor/terminal/pipe round-tripping some invisible character
correctly.
"""
import re

_ZERO_WIDTH_CHARS = "​‌‍﻿"
_UNICODE_SPACE_CHARS = (
    "             "
    "  　"
)
# Includes the non-breaking hyphen (U+2011), which looks identical to a
# plain hyphen but is a distinct "special character" some models insert.
_DASH_CHARS = "‐‑‒–—―−﹘﹣－"
_SINGLE_QUOTE_CHARS = "‘’‚‛"
_DOUBLE_QUOTE_CHARS = "“”„‟"
_ELLIPSIS_CHAR = "…"
_BULLET_CHARS = "•‣⁃●▪◦・"

_ZERO_WIDTH_RE = re.compile("[" + _ZERO_WIDTH_CHARS + "]")
_UNICODE_SPACE_RE = re.compile("[" + _UNICODE_SPACE_CHARS + "]")
_DASH_RE = re.compile("[" + _DASH_CHARS + "]")
_SINGLE_QUOTE_RE = re.compile("[" + _SINGLE_QUOTE_CHARS + "]")
_DOUBLE_QUOTE_RE = re.compile("[" + _DOUBLE_QUOTE_CHARS + "]")
_ELLIPSIS_RE = re.compile(_ELLIPSIS_CHAR)
_BULLET_RE = re.compile("[" + _BULLET_CHARS + "]")

_MARKDOWN_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|`)")
_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*]\s+", re.MULTILINE)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def sanitize_text(text: str) -> str:
    """Normalize stray Unicode punctuation/spacing and strip markdown from
    model output, so customer copy never carries an em-dash, curly quote,
    non-breaking hyphen, or bold-markdown artefact."""
    if not text:
        return text
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _UNICODE_SPACE_RE.sub(" ", text)
    text = _DASH_RE.sub("-", text)
    text = _SINGLE_QUOTE_RE.sub("'", text)
    text = _DOUBLE_QUOTE_RE.sub('"', text)
    text = _ELLIPSIS_RE.sub("...", text)
    text = _BULLET_RE.sub("-", text)
    text = _MARKDOWN_HEADING_RE.sub("", text)
    text = _MARKDOWN_BULLET_RE.sub("", text)
    text = _MARKDOWN_EMPHASIS_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()
