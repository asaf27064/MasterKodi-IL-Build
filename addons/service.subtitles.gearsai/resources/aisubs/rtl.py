# -*- coding: utf-8 -*-
# RTL rendering fix for Hebrew subtitle lines.
#
# Kodi's subtitle renderer lays out lines with an LTR base direction,
# so neutral characters at the EDGES of a Hebrew line land on the wrong
# side: a trailing "." or "?" shows on the right, and a leading dialogue
# "-" shows on the left -- the reverse of what Hebrew readers expect.
#
# The robust fix used by the build's existing DarkSubs add-on (which the
# user is happy with) is VISUAL REORDERING: move the line-final
# punctuation block to the FRONT of the text, and move a leading dialogue
# hyphen to the END. Stored that way, the renderer's bidi pass displays
# it correctly: hyphen on the right, sentence, punctuation on the left.
#
# We mirror that exact algorithm so our AI translations render identically
# to DarkSubs. Only LINE-EDGE neutrals are touched; internal punctuation
# is left alone (it sits between Hebrew runs and renders fine).

import re

# Trailing punctuation block. "…" is the single ellipsis char, distinct
# from "...".
_TRAILING_PUNCT = re.compile(r'[.,?!:;…]+$')
_HYPHEN = '-'

_HEB_LO, _HEB_HI = 0x0590, 0x05FF


def _has_hebrew(text):
    for ch in text:
        if _HEB_LO <= ord(ch) <= _HEB_HI:
            return True
    return False


def fix_line(text):
    """Reorder one line for correct RTL rendering. No-op for lines with
    no Hebrew (e.g. pure English, numbers, timecodes)."""
    if not text or not _has_hebrew(text):
        return text

    # Preserve <i>..</i> styling tags by fixing the inner text only.
    # Most subtitle lines wrap the whole line, so handle the common case:
    # strip a leading <i> and trailing </i>, fix, re-wrap.
    prefix, suffix = '', ''
    stripped = text
    if stripped.startswith('<i>'):
        prefix, stripped = '<i>', stripped[3:]
    if stripped.endswith('</i>'):
        suffix, stripped = '</i>', stripped[:-4]

    stripped = stripped.rstrip()
    starts_hyphen = stripped.startswith(_HYPHEN)
    if starts_hyphen:
        stripped = stripped.lstrip(_HYPHEN)

    m = _TRAILING_PUNCT.search(stripped)
    if m:
        stripped = m.group(0) + stripped[:m.start()]

    if starts_hyphen:
        stripped = stripped + _HYPHEN

    return prefix + stripped + suffix


def fix_lines(lines):
    """Apply fix_line to a list of text lines."""
    return [fix_line(ln) for ln in lines]
