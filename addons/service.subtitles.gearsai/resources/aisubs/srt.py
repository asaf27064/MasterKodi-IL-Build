# -*- coding: utf-8 -*-
# Minimal, forgiving SRT parser/serializer. No third-party deps.
#
# Real-world SRT files are messy: BOMs, CRLF/LF/CR mixed, blank-line
# gaps, missing trailing numbers, occasional non-numeric index lines,
# and trailing junk. We parse leniently (anchor on the timecode line,
# not the index) and re-serialise to clean, canonical SRT so Gemini
# sees a consistent shape and Kodi always gets valid output.

import re

# "00:01:02,345 --> 00:01:05,678"  (also tolerate '.' as ms separator)
_TIMECODE_RE = re.compile(
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*'
    r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})'
)


class Entry(object):
    __slots__ = ('index', 'start', 'end', 'lines')

    def __init__(self, index, start, end, lines):
        self.index = index          # int, re-numbered 1..N on output
        self.start = start          # raw timecode string "HH:MM:SS,mmm"
        self.end = end
        self.lines = lines          # list[str] of text lines (no trailing newlines)

    @property
    def text(self):
        return '\n'.join(self.lines)


def _normalise_tc(h, m, s, ms):
    # zero-pad to canonical HH:MM:SS,mmm
    return '{0:02d}:{1:02d}:{2:02d},{3:03d}'.format(
        int(h), int(m), int(s), int(ms.ljust(3, '0')[:3]))


def parse(raw):
    """Parse SRT text into a list[Entry]. Lenient: anchors on the
    timecode line. Index lines and blank-line spacing are advisory."""
    if raw is None:
        return []
    # Strip BOM, normalise newlines.
    if raw.startswith('﻿'):
        raw = raw[1:]
    raw = raw.replace('\r\n', '\n').replace('\r', '\n')
    lines = raw.split('\n')

    entries = []
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        tc = _TIMECODE_RE.search(line)
        if not tc:
            i += 1
            continue
        start = _normalise_tc(*tc.group(1, 2, 3, 4))
        end = _normalise_tc(*tc.group(5, 6, 7, 8))
        # Collect text lines until a blank line or the next timecode.
        text_lines = []
        i += 1
        while i < n:
            nxt = lines[i]
            if nxt.strip() == '':
                i += 1
                break
            if _TIMECODE_RE.search(nxt):
                # next entry started without a blank separator; don't consume
                break
            # A lone integer right before a timecode is the next index line;
            # peek to avoid swallowing it as text.
            if nxt.strip().isdigit() and (i + 1) < n and _TIMECODE_RE.search(lines[i + 1]):
                break
            text_lines.append(nxt.rstrip())
            i += 1
        entries.append(Entry(len(entries) + 1, start, end, text_lines))

    return entries


def serialize(entries):
    """Render list[Entry] back to canonical SRT text (LF newlines,
    1-based contiguous indices, blank line between entries)."""
    out = []
    for idx, e in enumerate(entries, 1):
        out.append(str(idx))
        out.append('{0} --> {1}'.format(e.start, e.end))
        body = e.lines if e.lines else ['']
        out.extend(body)
        out.append('')  # blank separator
    return '\n'.join(out).rstrip('\n') + '\n'


def to_blocks(entries):
    """Render entries as the numbered blocks we feed the model, e.g.

        12
        00:01:02,345 --> 00:01:05,678
        Hello there.

    Returns a single string. The model is told to preserve numbers +
    timecodes verbatim and only change the text."""
    out = []
    for e in entries:
        out.append(str(e.index))
        out.append('{0} --> {1}'.format(e.start, e.end))
        out.extend(e.lines if e.lines else [''])
        out.append('')
    return '\n'.join(out).rstrip('\n') + '\n'


def parse_model_blocks(text):
    """Parse the model's SRT-shaped reply back into {index: [lines]}.
    Keyed by the entry NUMBER the model echoed, so we can map
    translations back onto our original entries even if the model
    drops/reorders a few. Resilient to missing blank lines."""
    if not text:
        return {}
    if text.startswith('﻿'):
        text = text[1:]
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = text.split('\n')
    result = {}
    i = 0
    n = len(lines)
    while i < n:
        # Find an index line (integer) followed by a timecode line.
        s = lines[i].strip()
        if s.isdigit() and (i + 1) < n and _TIMECODE_RE.search(lines[i + 1]):
            num = int(s)
            i += 2  # skip index + timecode
            body = []
            while i < n:
                nxt = lines[i]
                if nxt.strip() == '':
                    i += 1
                    break
                if nxt.strip().isdigit() and (i + 1) < n and _TIMECODE_RE.search(lines[i + 1]):
                    break
                body.append(nxt.rstrip())
                i += 1
            result[num] = body
        else:
            i += 1
    return result
