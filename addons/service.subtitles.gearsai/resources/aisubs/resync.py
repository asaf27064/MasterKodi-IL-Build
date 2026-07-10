# -*- coding: utf-8 -*-
# Reference-based re-timing: move an existing Hebrew translation onto a
# DIFFERENT release without re-translating (and without spending Gemini
# quota).
#
# The Hebrew text of a film is identical across every release -- only the
# TIMING differs (offset + framerate, sometimes minor line splits). So if
# we already translated the film once, we can serve a perfectly-synced
# Hebrew sub for any other release by:
#
#   1. taking the English sub that ALREADY matches the new release B
#      (fetched from OpenSubtitles -- its timing is correct for B),
#   2. aligning it, line-by-line, against the English source we originally
#      translated (A) -- the dialogue text is ~identical, so this is a
#      reliable text match,
#   3. stamping our existing Hebrew (which is 1:1 with A) onto B's cues.
#
# Crucially we do NOT compute new timestamps -- we ADOPT B's real ones. So
# the result is exactly as well-synced as English-B is to release B, which
# is the very same source we'd otherwise translate. The only risk is
# mis-assigning a line, which the confidence score guards against: callers
# use the result only when confidence is high, else they translate fresh.

import difflib
import re

from . import srt

# Fraction of the new release's cues that must be confidently matched for
# the result to be trustworthy. Below this, the caller should translate
# fresh instead (different cut, bad source sub, etc.).
DEFAULT_MIN_CONFIDENCE = 0.85

# In a 'replace' block (lines that aren't identical between the two English
# subs), only treat a positional pair as the same cue if their texts are at
# least this similar. This is what stops a genuinely different cut -- where
# alignment fails and every pair is unrelated -- from being mapped
# positionally and producing false confidence. Reworded/re-split lines of
# the SAME dialogue score well above this; unrelated lines score far below.
REPLACE_MIN_RATIO = 0.55

_TAG_RE = re.compile(r'<[^>]+>')
_NONWORD_RE = re.compile(r'[^0-9a-zÀ-ɏ]+')


def _ratio(a, b):
    """Char-level similarity 0..1 of two already-normalised strings."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _norm(lines):
    """Normalise a cue's text for cross-release matching: drop tags,
    speaker hyphens, punctuation and case, collapse whitespace. Two
    English subs of the same film usually normalise identically."""
    s = ' '.join(lines or [])
    s = _TAG_RE.sub(' ', s)
    s = s.lower()
    s = _NONWORD_RE.sub(' ', s)
    return ' '.join(s.split())


def retime(hebrew_a, english_a, english_b, min_conf=DEFAULT_MIN_CONFIDENCE):
    """Re-time Hebrew (built from English A) onto release B's timing,
    using English B as the timing reference.

    Returns a dict:
        {srt, confidence, matched, total, ok}
    `ok` is True iff confidence >= min_conf AND we produced output. When
    not ok, `srt` is still returned (best-effort) but callers should
    prefer a fresh translation.
    """
    ha = srt.parse(hebrew_a)
    ea = srt.parse(english_a)
    eb = srt.parse(english_b)
    if not ha or not ea or not eb:
        return {'srt': None, 'confidence': 0.0, 'matched': 0,
                'total': len(eb), 'ok': False}

    # Hebrew A is 1:1 with English A (same entries, same order). Pair by
    # position; if counts drift slightly, pair up to the shorter length.
    heb_for_a = {}
    for i in range(min(len(ea), len(ha))):
        heb_for_a[i] = ha[i].lines

    na = [_norm(e.lines) for e in ea]
    nb = [_norm(e.lines) for e in eb]

    # Map English-A index -> English-B index via sequence alignment on the
    # normalised cue texts. autojunk off so long subs still align.
    a_to_b = {}
    sm = difflib.SequenceMatcher(None, na, nb, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            for k in range(i2 - i1):
                a_to_b[i1 + k] = j1 + k
        elif tag == 'replace':
            # Reworded / re-split lines that still occupy the same slot.
            # Map positionally over the overlap, but ONLY when the two texts
            # are actually similar -- otherwise this is a real divergence
            # (different cut) and a positional guess would be wrong.
            overlap = min(i2 - i1, j2 - j1)
            for k in range(overlap):
                ai, bj = i1 + k, j1 + k
                if _ratio(na[ai], nb[bj]) >= REPLACE_MIN_RATIO:
                    a_to_b[ai] = bj
        # 'delete' (A-only) and 'insert' (B-only) -> no mapping.

    # Invert to B-index -> Hebrew lines.
    heb_for_b = {}
    for ai, bj in a_to_b.items():
        lines = heb_for_a.get(ai)
        if lines:
            heb_for_b[bj] = lines

    # Build the output on B's cues/timing. Matched cues get Hebrew; the
    # few unmatched ones keep the English (a visible line beats a gap) and
    # count against confidence.
    out = []
    matched = 0
    for j, e in enumerate(eb):
        lines = heb_for_b.get(j)
        if lines:
            matched += 1
            out.append(srt.Entry(j + 1, e.start, e.end, list(lines)))
        else:
            out.append(srt.Entry(j + 1, e.start, e.end, list(e.lines)))

    total = len(eb)
    confidence = (matched / float(total)) if total else 0.0
    ok = confidence >= min_conf and matched > 0
    return {'srt': srt.serialize(out), 'confidence': confidence,
            'matched': matched, 'total': total, 'ok': ok}
