# -*- coding: utf-8 -*-
# Timestamp-based subtitle aligner (fail-open fallback for resync.py).
#
# resync.py re-times by TEXT (align English-A<->English-B line by line, then
# stamp our Hebrew onto B's exact cues). That's the best result, but it needs a
# clean English-A anchor + a text-alignable English-B. When that isn't confident
# (reworded subs, different cut, missing anchor), this module offers a cruder but
# robust fallback: shift the Hebrew sub's OWN timestamps by a global OFFSET so
# they line up with an ORACLE whose timing is correct for the playing release
# (any language -- we only read the timecodes, never the text).
#
# Approach: treat each sub as an "event train" of start times, coarse-scan the
# offset that lands the most Hebrew events on top of an oracle event (tolerance
# window), then refine to the MEDIAN of the matched deltas. Offset only (no
# scale) -- a constant shift is by far the common case and can't over-fit.
#
# Everything is wrapped so ANY problem returns None and the caller delivers the
# original sub unchanged. Pure stdlib, no xbmc.

import bisect

from . import srt


def _tc_to_sec(tc):
    # "HH:MM:SS,mmm" -> float seconds
    hh, mm, rest = tc.split(':')
    ss, ms = rest.replace('.', ',').split(',')
    return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0


def _sec_to_tc(sec):
    if sec < 0:
        sec = 0.0
    ms = int(round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return '{0:02d}:{1:02d}:{2:02d},{3:03d}'.format(h, m, s, ms)


def _starts(entries):
    out = []
    for e in entries:
        try:
            out.append(_tc_to_sec(e.start))
        except Exception:
            pass
    out.sort()
    return out


def estimate_offset(src, oracle, max_off=45.0, step=0.2, tol=0.20, min_events=12):
    """Best global offset (seconds) to add to `src` starts so they align to
    `oracle` starts. Returns dict{offset, confidence, matched} or None."""
    if len(src) < min_events or len(oracle) < min_events:
        return None
    # O(1) membership: bucket oracle times at `tol` granularity (+/-1 bucket).
    inv = 1.0 / tol
    buckets = set()
    for t in oracle:
        b = int(t * inv)
        buckets.add(b); buckets.add(b - 1); buckets.add(b + 1)

    best_off, best_cnt = 0.0, -1
    steps = int((2 * max_off) / step) + 1
    for i in range(steps):
        off = -max_off + i * step
        cnt = 0
        for t in src:
            if int((t + off) * inv) in buckets:
                cnt += 1
        if cnt > best_cnt:
            best_cnt, best_off = cnt, off

    conf = best_cnt / float(len(src))
    # Refine: median of (nearest_oracle - src) for events matched at best_off.
    deltas = []
    for t in src:
        shifted = t + best_off
        j = bisect.bisect_left(oracle, shifted)
        for k in (j - 1, j):
            if 0 <= k < len(oracle):
                d = oracle[k] - shifted
                if abs(d) <= tol:
                    deltas.append(d)
                    break
    refined = best_off
    if deltas:
        deltas.sort()
        refined = best_off + deltas[len(deltas) // 2]
    return {'offset': refined, 'confidence': conf, 'matched': best_cnt}


def _shift_entries(entries, offset):
    for e in entries:
        try:
            e.start = _sec_to_tc(_tc_to_sec(e.start) + offset)
            e.end = _sec_to_tc(_tc_to_sec(e.end) + offset)
        except Exception:
            pass
    return entries


def align(hebrew_srt, oracle_srt, min_confidence=0.55, max_abs_offset=45.0):
    """Shift `hebrew_srt`'s timestamps to match `oracle_srt`'s timing.
    Returns {'ok', 'srt', 'confidence', 'offset'} or None (fail-open)."""
    try:
        heb = srt.parse(hebrew_srt)
        ora = srt.parse(oracle_srt)
        if not heb or not ora:
            return None
        est = estimate_offset(_starts(heb), _starts(ora))
        if not est:
            return None
        off = est['offset']
        if est['confidence'] < min_confidence or abs(off) > max_abs_offset:
            return None
        # A near-zero offset means it was already synced -- nothing to do.
        if abs(off) < 0.05:
            return {'ok': True, 'srt': hebrew_srt, 'confidence': est['confidence'], 'offset': 0.0}
        out = srt.serialize(_shift_entries(heb, off))
        return {'ok': True, 'srt': out, 'confidence': est['confidence'], 'offset': off}
    except Exception:
        return None
