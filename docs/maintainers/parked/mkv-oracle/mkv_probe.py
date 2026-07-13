# -*- coding: utf-8 -*-
"""Pure-Python Matroska/WebM oracle probe (HTTP range reads, no ffmpeg).

Samples the START timestamps (and text) of an embedded TEXT subtitle track at a
few points across the file WITHOUT downloading it -- reads only the EBML header,
Info/Tracks/Cues index, and a handful of clusters via Range requests. The sampled
cues become a timing "oracle" that sync_align uses to snap an external Hebrew sub
onto the video's true timing.

Fail-open by contract: any parse/network issue -> None.
"""
import struct
import ssl
from urllib.request import Request, urlopen

_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# --- Matroska element IDs ---
ID_SEGMENT      = 0x18538067
ID_SEEKHEAD     = 0x114D9B74
ID_SEEK         = 0x4DBB
ID_SEEKID       = 0x53AB
ID_SEEKPOS      = 0x53AC
ID_INFO         = 0x1549A966
ID_TSSCALE      = 0x2AD7B1
ID_DURATION     = 0x4489
ID_TRACKS       = 0x1654AE6B
ID_TRACKENTRY   = 0xAE
ID_TRACKNUM     = 0xD7
ID_TRACKTYPE    = 0x83
ID_CODECID      = 0x86
ID_LANGUAGE     = 0x22B59C
ID_LANG_BCP47   = 0x22B59D
ID_CUES         = 0x1C53BB6B
ID_CUEPOINT     = 0xBB
ID_CUETIME      = 0xB3
ID_CUETRACKPOS  = 0xB7
ID_CUECLUSTER   = 0xF1
ID_CLUSTER      = 0x1F43B675
ID_CLUSTERTS    = 0xE7
ID_SIMPLEBLOCK  = 0xA3
ID_BLOCKGROUP   = 0xA0
ID_BLOCK        = 0xA1

TRACKTYPE_SUBTITLE = 0x11


class _Reader:
    """Byte source backed by HTTP Range requests, with a tiny local cache."""
    def __init__(self, url, timeout=30):
        self.url = url
        self.timeout = timeout
        self.total = None
        self.bytes_read = 0

    def get(self, offset, length):
        if length <= 0:
            return b''
        end = offset + length - 1
        req = Request(self.url, headers={
            'Range': 'bytes=%d-%d' % (offset, end),
            'User-Agent': 'MasterKodi-MKVProbe',
        })
        with urlopen(req, timeout=self.timeout, context=_SSL_CTX) as r:
            if self.total is None:
                cr = r.headers.get('Content-Range')
                if cr and '/' in cr:
                    try: self.total = int(cr.rsplit('/', 1)[1])
                    except Exception: pass
                if self.total is None:
                    cl = r.headers.get('Content-Length')
                    if cl:
                        try: self.total = int(cl)
                        except Exception: pass
            data = r.read()
        self.bytes_read += len(data)
        return data


def _vint(buf, pos, keep_marker):
    """Read an EBML variable-length integer at buf[pos].
    keep_marker=True for element IDs (keep the length-marker bits), False for
    sizes/values (strip it). Returns (value, new_pos) or (None, pos)."""
    if pos >= len(buf):
        return None, pos
    first = buf[pos]
    if first == 0:
        return None, pos
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        mask >>= 1
        length += 1
    if length > 8 or pos + length > len(buf):
        return None, pos
    if keep_marker:
        val = 0
        for i in range(length):
            val = (val << 8) | buf[pos + i]
    else:
        val = first & (mask - 1)
        for i in range(1, length):
            val = (val << 8) | buf[pos + i]
        # all-ones size = "unknown"
        allones = (1 << (7 * length)) - 1
        if val == allones:
            val = None
    return val, pos + length


def _uint(b):
    v = 0
    for x in b:
        v = (v << 8) | x
    return v


def _float(b):
    if len(b) == 4:
        return struct.unpack('>f', b)[0]
    if len(b) == 8:
        return struct.unpack('>d', b)[0]
    return float(_uint(b))


def _iter_elements(buf, start=0, end=None):
    """Yield (elem_id, data_start_in_buf, size) for children in buf[start:end].
    size may be None (unknown). Stops at buffer end."""
    if end is None:
        end = len(buf)
    pos = start
    while pos < end:
        eid, p2 = _vint(buf, pos, True)
        if eid is None:
            break
        size, p3 = _vint(buf, p2, False)
        if p3 > end:
            break
        yield eid, p3, size
        if size is None:
            break
        pos = p3 + size


def parse(url, prefer_langs=('heb', 'he', 'eng', 'en'), windows=(0.2, 0.45, 0.7),
          per_window_bytes=3 * 1024 * 1024, debug=False):
    """Return {'track': n, 'lang':..., 'codec':..., 'cues':[(sec,text),...],
    'bytes_read': n} or None."""
    try:
        rd = _Reader(url)
        head = rd.get(0, 200)
        # locate Segment
        seg_id = None
        for eid, dstart, size in _iter_elements(head):
            if eid == 0x1A45DFA3:   # EBML header -> skip its body
                if size is None:
                    return None
                continue
            if eid == ID_SEGMENT:
                seg_id = (dstart, size)
                break
        if not seg_id:
            # header may be bigger; pull more and retry once
            head = rd.get(0, 4096)
            for eid, dstart, size in _iter_elements(head):
                if eid == ID_SEGMENT:
                    seg_id = (dstart, size)
                    break
        if not seg_id:
            return None
        seg_data_start = seg_id[0]

        # Walk top-level Segment children by streaming ~2MB from seg start; the
        # Info/Tracks/SeekHead/Cues all sit at the head of the Segment.
        top = rd.get(seg_data_start, 2 * 1024 * 1024)
        info = {'tsscale': 1000000, 'duration': None}
        tracks = []
        cues = []           # (cue_time_scaled, cluster_pos_rel_segment)
        seek = {}           # id -> pos (rel segment)

        def abs_off(rel):   # SeekPosition/CueClusterPosition are rel to seg data
            return seg_data_start + rel

        for eid, dstart, size in _iter_elements(top, 0, len(top)):
            if size is None:
                break
            body = top[dstart:dstart + size] if dstart + size <= len(top) else None
            if eid == ID_INFO and body is not None:
                for cid, cds, csz in _iter_elements(body):
                    if csz is None: break
                    cb = body[cds:cds + csz]
                    if cid == ID_TSSCALE: info['tsscale'] = _uint(cb)
                    elif cid == ID_DURATION: info['duration'] = _float(cb)
            elif eid == ID_TRACKS and body is not None:
                for cid, cds, csz in _iter_elements(body):
                    if csz is None: break
                    if cid != ID_TRACKENTRY: continue
                    te = body[cds:cds + csz]
                    t = {'num': None, 'type': None, 'codec': '', 'lang': 'und'}
                    for tid, tds, tsz in _iter_elements(te):
                        if tsz is None: break
                        tb = te[tds:tds + tsz]
                        if tid == ID_TRACKNUM: t['num'] = _uint(tb)
                        elif tid == ID_TRACKTYPE: t['type'] = _uint(tb)
                        elif tid == ID_CODECID: t['codec'] = tb.decode('ascii', 'replace')
                        elif tid in (ID_LANGUAGE, ID_LANG_BCP47):
                            t['lang'] = tb.decode('ascii', 'replace').strip('\x00')
                    if t['type'] == TRACKTYPE_SUBTITLE:
                        tracks.append(t)
            elif eid == ID_SEEKHEAD and body is not None:
                for cid, cds, csz in _iter_elements(body):
                    if csz is None: break
                    if cid != ID_SEEK: continue
                    sb = body[cds:cds + csz]
                    sid = None; spos = None
                    for kid, kds, ksz in _iter_elements(sb):
                        if ksz is None: break
                        kb = sb[kds:kds + ksz]
                        if kid == ID_SEEKID: sid = _uint(kb)
                        elif kid == ID_SEEKPOS: spos = _uint(kb)
                    if sid is not None and spos is not None:
                        seek[sid] = spos
            elif eid == ID_CUES and body is not None:
                _parse_cues(body, cues)

        # text subtitle tracks only (S_TEXT/UTF8, S_TEXT/ASS, SubRip)
        def is_text(c): return 'S_TEXT' in c or 'UTF8' in c or 'ASS' in c or 'SSA' in c
        text_tracks = [t for t in tracks if is_text(t['codec'])]
        if not text_tracks:
            return None

        # pick preferred language track
        chosen = None
        for lang in prefer_langs:
            for t in text_tracks:
                if t['lang'].lower().startswith(lang):
                    chosen = t; break
            if chosen: break
        if not chosen:
            chosen = text_tracks[0]

        # Cues from SeekHead if not inline
        if not cues and ID_CUES in seek:
            cbuf = rd.get(abs_off(seek[ID_CUES]), 2 * 1024 * 1024)
            for eid, dstart, size in _iter_elements(cbuf):
                if eid == ID_CUES and size is not None:
                    _parse_cues(cbuf[dstart:dstart + size], cues)
                    break
        if not cues:
            return {'track': chosen['num'], 'lang': chosen['lang'],
                    'codec': chosen['codec'], 'cues': [], 'bytes_read': rd.bytes_read,
                    'note': 'no cues index'}

        cues.sort()
        tsscale = info['tsscale'] or 1000000
        # total duration in scaled ticks (for choosing sample windows)
        dur_ticks = info['duration']
        if dur_ticks:
            max_ct = dur_ticks
        else:
            max_ct = cues[-1][0]

        sampled = []
        for frac in windows:
            target = max_ct * frac
            # nearest cue at or before target
            pos_rel = None
            for ct, cpos in cues:
                if ct <= target:
                    pos_rel = cpos
                else:
                    break
            if pos_rel is None:
                pos_rel = cues[0][1]
            cluster_abs = abs_off(pos_rel)
            chunk = rd.get(cluster_abs, per_window_bytes)
            _extract_sub_cues(chunk, chosen['num'], tsscale, sampled)

        # de-dup + sort
        seen = set(); out = []
        for sec, text in sorted(sampled):
            k = round(sec, 2)
            if k in seen: continue
            seen.add(k); out.append((sec, text))
        return {'track': chosen['num'], 'lang': chosen['lang'],
                'codec': chosen['codec'], 'cues': out,
                'all_tracks': [(t['num'], t['lang'], t['codec']) for t in text_tracks],
                'bytes_read': rd.bytes_read, 'tsscale': tsscale}
    except Exception as e:
        if debug:
            import traceback; traceback.print_exc()
        return None


def _parse_cues(buf, out):
    for cid, cds, csz in _iter_elements(buf):
        if csz is None: break
        if cid != ID_CUEPOINT: continue
        cp = buf[cds:cds + csz]
        ctime = None; cpos = None
        for kid, kds, ksz in _iter_elements(cp):
            if ksz is None: break
            kb = cp[kds:kds + ksz]
            if kid == ID_CUETIME: ctime = _uint(kb)
            elif kid == ID_CUETRACKPOS:
                for jid, jds, jsz in _iter_elements(kb):
                    if jsz is None: break
                    if jid == ID_CUECLUSTER:
                        cpos = _uint(kb[jds:jds + jsz])
        if ctime is not None and cpos is not None:
            out.append((ctime, cpos))


def _extract_sub_cues(chunk, track_num, tsscale, out):
    """Scan clusters in `chunk`, pull SimpleBlock/Block start times for track_num."""
    for eid, dstart, size in _iter_elements(chunk):
        if eid != ID_CLUSTER:
            continue
        cl_end = dstart + size if size is not None else len(chunk)
        cl_end = min(cl_end, len(chunk))
        cluster_ts = 0
        pos = dstart
        while pos < cl_end:
            cid, p2 = _vint(chunk, pos, True)
            if cid is None: break
            csz, p3 = _vint(chunk, p2, False)
            if csz is None or p3 + csz > cl_end:
                break
            if cid == ID_CLUSTERTS:
                cluster_ts = _uint(chunk[p3:p3 + csz])
            elif cid == ID_SIMPLEBLOCK:
                _block_time(chunk, p3, csz, track_num, cluster_ts, tsscale, out)
            elif cid == ID_BLOCKGROUP:
                # find inner Block
                gp = p3; gend = p3 + csz
                while gp < gend:
                    bid, b2 = _vint(chunk, gp, True)
                    if bid is None: break
                    bsz, b3 = _vint(chunk, b2, False)
                    if bsz is None or b3 + bsz > gend: break
                    if bid == ID_BLOCK:
                        _block_time(chunk, b3, bsz, track_num, cluster_ts, tsscale, out)
                    gp = b3 + bsz
            pos = p3 + csz


def _block_time(buf, start, size, track_num, cluster_ts, tsscale, out):
    tn, p = _vint(buf, start, False)
    if tn is None or tn != track_num:
        return
    if p + 2 > start + size:
        return
    rel = struct.unpack('>h', buf[p:p + 2])[0]
    ticks = cluster_ts + rel
    sec = (ticks * tsscale) / 1e9
    text = buf[p + 3:start + size]      # skip int16 ts + flags byte
    try:
        txt = text.decode('utf-8', 'replace').replace('\x00', '').strip()
    except Exception:
        txt = ''
    out.append((sec, txt))


if __name__ == '__main__':
    import sys, json
    url = sys.argv[1]
    r = parse(url, debug=True)
    if not r:
        print('PROBE FAILED'); sys.exit(1)
    print('track=%s lang=%s codec=%s  bytes_read=%.1f MB  cues=%d'
          % (r['track'], r['lang'], r['codec'], r['bytes_read'] / 1048576.0, len(r['cues'])))
    print('other text tracks:', r.get('all_tracks'))
    for sec, txt in r['cues'][:15]:
        print('  %8.3f  %s' % (sec, txt[:50]))
