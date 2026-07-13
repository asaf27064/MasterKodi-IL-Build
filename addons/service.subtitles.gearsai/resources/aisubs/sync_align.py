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


def _scale_shift_entries(entries, scale, offset):
    for e in entries:
        try:
            e.start = _sec_to_tc(_tc_to_sec(e.start) * scale + offset)
            e.end = _sec_to_tc(_tc_to_sec(e.end) * scale + offset)
        except Exception:
            pass
    return entries


def align_to_anchors(hebrew_srt, anchor_secs, tol=0.35, max_abs_offset=90.0,
                     min_matched=5, min_ratio=0.6):
    """Align `hebrew_srt` to a SPARSE oracle: a few correct start-times sampled
    from the playing file's embedded subtitle track (mkv_probe). Fits a linear
    map new = scale*old + offset. Robust with as few as ~6 anchors because they
    span the whole film. Returns {'ok','srt','offset','scale','confidence'} or
    None (fail-open). Anchors that also survive a framerate change (scale~0.96 /
    1.04) are accepted; wild scales are rejected as bad matches."""
    try:
        anchors = sorted(set(float(a) for a in (anchor_secs or []) if a and a > 0))
        if len(anchors) < min_matched:
            return None
        heb = srt.parse(hebrew_srt)
        starts = _starts(heb)
        if len(starts) < min_matched:
            return None

        # Candidate global scales: 1.0 (pure offset, the common case) + the standard
        # subtitle framerate-conversion ratios (PAL<->NTSC/film). For each we vote the
        # best offset of the anchors against scale*hebrew, then keep the global best.
        _fps = (24.0, 23.976, 25.0, 29.97, 30.0)
        scales = {1.0}
        for a in _fps:
            for b in _fps:
                if a != b:
                    scales.add(round(a / b, 6))
        inv = 1.0 / tol
        step = 0.1
        steps = int((2 * max_abs_offset) / step) + 1

        best_scale, best_off, best_cnt = 1.0, 0.0, -1
        for sc in scales:
            if not (0.90 <= sc <= 1.10):
                continue
            hb = set()
            for t in starts:
                b = int((t * sc) * inv); hb.add(b); hb.add(b - 1); hb.add(b + 1)
            for i in range(steps):
                off = -max_abs_offset + i * step
                cnt = 0
                for a in anchors:
                    if int((a - off) * inv) in hb:
                        cnt += 1
                if cnt > best_cnt:
                    best_cnt, best_off, best_scale = cnt, off, sc
        if best_cnt < min_matched or best_cnt < min_ratio * len(anchors):
            return None

        # Pair each anchor with its nearest scaled-Hebrew start. First pass uses a
        # wide tolerance (the coarse offset can be up to `tol` off); we then fit,
        # re-pair against the FITTED line at tight tol, and refit -- a 2-pass polish.
        def _pair(pred, pair_tol):
            out = []
            for a in anchors:
                target = (a - pred[1]) / pred[0]   # invert y=scale*x+offset -> x
                j = bisect.bisect_left(starts, target)
                best = None
                for k in (j - 1, j):
                    if 0 <= k < len(starts):
                        d = abs(pred[0] * starts[k] + pred[1] - a)
                        if d <= pair_tol and (best is None or d < best[0]):
                            best = (d, starts[k])
                if best:
                    out.append((best[1], a))   # (x=raw heb_start, y=true_time)
            return out

        def _fit(pairs):
            n = len(pairs)
            sx = sum(p[0] for p in pairs); sy = sum(p[1] for p in pairs)
            sxx = sum(p[0] * p[0] for p in pairs); sxy = sum(p[0] * p[1] for p in pairs)
            denom = n * sxx - sx * sx
            if abs(denom) < 1e-6:
                return best_scale, (sy - best_scale * sx) / n
            sc = (n * sxy - sx * sy) / denom
            return sc, (sy - sc * sx) / n

        pred = (best_scale, best_off)
        pairs = _pair(pred, 2.2 * tol)
        if len(pairs) < min_matched:
            return None
        scale, offset = _fit(pairs)
        pairs = _pair((scale, offset), tol)          # re-pair on fitted line, tight
        if len(pairs) < min_matched:
            return None
        scale, offset = _fit(pairs)

        # Guard: reject implausible scales (accept PAL/NTSC framerate ratios).
        if not (0.90 <= scale <= 1.10):
            return None
        # Residual check: matched pairs must fit the line tightly.
        resid = [abs(y - (scale * x + offset)) for x, y in pairs]
        if sorted(resid)[len(resid) // 2] > tol:
            return None

        conf = best_cnt / float(len(anchors))
        if abs(scale - 1.0) < 1e-4 and abs(offset) < 0.05:
            return {'ok': True, 'srt': hebrew_srt, 'confidence': conf, 'offset': 0.0, 'scale': 1.0}
        out = srt.serialize(_scale_shift_entries(heb, scale, offset))
        return {'ok': True, 'srt': out, 'confidence': conf, 'offset': offset, 'scale': scale}
    except Exception:
        return None
