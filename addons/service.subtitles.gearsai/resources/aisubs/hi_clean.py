# -*- coding: utf-8 -*-
# Hearing-impaired (SDH) cleaning, done per CUE rather than per line.
#
# The old cleaner (engine.remove_hi_subs) worked line by line on the finished
# file, so three kinds of noise reached the screen (S.W.A.T. Exiles S01E01,
# pool entry bf920fd91e52, 2026-09-28):
#   * a tag split over two lines ("[screams, clears" / "throat] Almost got it.")
#     never matched either regex on its own;
#   * an English song line lost only its "♪" and kept the English lyric;
#   * speaker labels ("MAN:", "ETHAN:") were never removed.
# Worse, the translation prompt told the model to drop annotations, and it often
# dropped the WHOLE cue -- translate.py then kept the English, tag and all.
#
# So the English is cleaned BEFORE it reaches the model (clean_english), and the
# finished file is cleaned cue by cue (clean_srt_text). Pure Python, no Kodi
# imports, so it can be tested outside Kodi. Mirrors stripHi in the Nuvio
# build's aisubs.ts, plus the one thing Kodi needs that Nuvio does not: the
# Hebrew lines here are in Kodi VISUAL order (rtl.fix_line moves a leading
# dialogue "-" to the END of the line), so dash handling looks at both ends.

import re

_HEB = re.compile(u'[֐-׿]')
_TAGS_RE = re.compile(r'\[[^\]]*\]|\([^)]*\)')
_SPEAKER = re.compile(r"^((?:<[^>]+>)*\s*-?\s*)[A-Z][A-Z0-9 .'&-]*[A-Z0-9]:\s+")
_SITE_AD = re.compile(r'opensubtitles|osdb\.link|\.srt\b|subtitles by|'
                      r'synced (?:and|&) corrected|addic7ed', re.I)
_NOTES = re.compile(u'[♪♫]')
_EMPTY_PAIR = re.compile(r'<(font|i|b|u)\b[^>]*>\s*</\1>', re.I)
_ANY_TAG = re.compile(r'<[^>]+>')
_LEAD_DASH = re.compile(r'^((?:<[^>]+>)*)\s*-+\s*')
_TRAIL_DASH = re.compile(r'\s*-+\s*((?:</[^>]+>)*)$')


def has_hebrew(text):
    return bool(_HEB.search(text or ''))


def _is_dialogue_line(line):
    core = _ANY_TAG.sub('', line).strip()
    if core.startswith('-'):
        return True
    # visual order: the dash rtl.fix_line moved to the end of a Hebrew line
    return has_hebrew(core) and core.endswith('-') and not core.endswith('--')


def _tidy(ln):
    """Whitespace a removed tag or note leaves behind: doubled spaces, a space
    just inside <i>..</i>, and (visual order) a space before the dialogue dash
    rtl.fix_line put at the end of a Hebrew line."""
    ln = re.sub(r'\s{2,}', ' ', ln).strip()
    ln = re.sub(r'^((?:<[^>]+>)+)\s+', r'\1', ln)
    ln = re.sub(r'\s+((?:</[^>]+>)+)$', r'\1', ln)
    if has_hebrew(ln):
        ln = re.sub(r'\s+(-+(?:</[^>]+>)*)$', r'\1', ln)
    return ln


def strip_hi(lines, hebrew):
    """Clean ONE cue. Returns its remaining lines; [] means drop the cue.

    hebrew=False: an English cue as the model gets it. Lyric lines are kept
    (the model translates them); only tags, speaker labels and site ads go.
    hebrew=True: a finished cue as the viewer gets it. A lyric line with no
    Hebrew is an untranslated English lyric and is dropped; notes are stripped
    from Hebrew lines."""
    text = '\n'.join(lines or [])
    if not text.strip():
        return []
    if _SITE_AD.search(text):
        return []
    dialogue = sum(1 for ln in lines if _is_dialogue_line(ln)) > 1
    # join first: a tag split over two lines matches as one
    kept = [_SPEAKER.sub(r'\1', ln) for ln in _TAGS_RE.sub('', text).split('\n')]
    if hebrew:
        kept = [_NOTES.sub('', ln) for ln in kept if not _NOTES.search(ln) or has_hebrew(ln)]
    out = []
    for ln in kept:
        prev = None
        while prev != ln:          # nested empties: <font><i></i></font>
            prev, ln = ln, _EMPTY_PAIR.sub('', ln)
        ln = _tidy(ln)
        if re.sub(u'[\\s\\-:.,…]', '', _ANY_TAG.sub('', ln)):
            out.append(ln)
    if dialogue and len(out) == 1:
        ln = _LEAD_DASH.sub(r'\1', out[0])
        if has_hebrew(ln):
            ln = _TRAIL_DASH.sub(r'\1', ln)
        out[0] = ln
    return out


def clean_english(lines):
    return strip_hi(lines, False)


def clean_hebrew(lines):
    return strip_hi(lines, True)


def has_hi_tags(text):
    """True when an SRT body carries anything clean_srt_text would remove."""
    if not text:
        return False
    if _TAGS_RE.search(text) or _NOTES.search(text) or _SITE_AD.search(text):
        return True
    return any(_SPEAKER.match(ln) for ln in text.splitlines())


def clean_srt_text(text, hebrew=None):
    """Clean a whole SRT file cue by cue, drop emptied cues and renumber.

    hebrew=None decides from the file: lyric lines are only dropped when most
    cues are Hebrew, so a cleaned ENGLISH file keeps its lyrics.
    Returns None when the text is not parseable SRT (the caller keeps its old
    behaviour for ASS/SSA/SUB)."""
    from . import srt
    entries = srt.parse(text)
    if not entries:
        return None
    if hebrew is None:
        heb = sum(1 for e in entries if has_hebrew(e.text))
        hebrew = heb >= len(entries) * 0.3
    out = []
    for e in entries:
        e.lines = strip_hi(e.lines, hebrew)
        if e.lines:
            out.append(e)
    return srt.serialize(out)
