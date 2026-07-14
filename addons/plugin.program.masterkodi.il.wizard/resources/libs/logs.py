# -*- coding: utf-8 -*-
"""Collect Kodi logs, SCRUB secrets, upload, and show a shareable link.

Wired to the power-menu "שלח לוגים" item (mode=send_logs). The point is to let a
user hand a maintainer (or me) a link to their logs in one click -- especially on
Android, where reading the log file off the device is a pain.

Everything sensitive (debrid/Trakt/Gemini tokens, stream URLs with tokens, emails,
IPs, API keys) is redacted before upload. Public build keys (mdblist/omdb) don't
matter but get caught by the generic key patterns anyway.
"""
import os
import re
import json
import time

import xbmc
import xbmcgui
import xbmcvfs

from urllib.request import Request, urlopen

MAX_BYTES = 380 * 1024                       # keep under paste-host limits; tail wins

_SCRUB = [
    # key/token = value  (settings dumps, headers)
    (re.compile(r'(?i)\b(access_token|refresh_token|client_secret|api[_-]?key|'
                r'apikey|token|secret|password|passwd|auth|authorization|bearer)\b'
                r'(["\'=:> ]{1,4})[A-Za-z0-9._\-]{6,}'), r'\1\2<redacted>'),
    # any ...?token=/&key=/&api_key= in a URL
    (re.compile(r'(?i)([?&](?:token|api_key|apikey|key|auth|access_token)=)[^\s"\'&]+'),
     r'\1<redacted>'),
    # TorBox / debrid stream links: /dld/<uuid>?token=<uuid>
    (re.compile(r'/dld/[0-9A-Fa-f\-]{8,}\?token=[0-9A-Fa-f\-]+'), '/dld/<redacted>'),
    (re.compile(r'\bidb_[A-Za-z0-9]{10,}\b'), 'idb_<redacted>'),
    # emails + IPs
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), '<email>'),
    (re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'), '<ip>'),
]


def _scrub(text):
    for pat, repl in _SCRUB:
        try:
            text = pat.sub(repl, text)
        except Exception:
            pass
    return text


def _read(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            return fh.read()
    except Exception:
        return ''


def _collect():
    base = xbmcvfs.translatePath('special://logpath/')
    parts = []
    for fn in ('kodi.log', 'kodi.old.log'):
        p = os.path.join(base, fn)
        if os.path.isfile(p):
            parts.append('=================== %s ===================\n%s'
                         % (fn, _scrub(_read(p))))
    combined = ('\n\n'.join(parts)).strip()
    if len(combined) > MAX_BYTES:
        combined = '...(older lines truncated)...\n' + combined[-MAX_BYTES:]
    return combined


def _post(url, data, headers, timeout=30):
    req = Request(url, data=data, headers=headers)
    return urlopen(req, timeout=timeout).read().decode('utf-8', 'replace')


def _upload(text):
    body = text.encode('utf-8', 'replace')
    # 1) Kodi's own paste host (hastebin API): {"key": "..."} -> /raw/<key>
    try:
        resp = _post('https://paste.kodi.tv/documents', body,
                     {'User-Agent': 'MasterKodiIL', 'Content-Type': 'text/plain'})
        key = (json.loads(resp) or {}).get('key')
        if key:
            return 'https://paste.kodi.tv/%s' % key
    except Exception:
        pass
    # 2) dpaste fallback (form POST) -> returns the URL as plain text
    try:
        from urllib.parse import urlencode
        form = urlencode({'content': text, 'syntax': 'text', 'expiry_days': '14'}).encode('utf-8')
        resp = _post('https://dpaste.com/api/v2/', form,
                     {'User-Agent': 'MasterKodiIL',
                      'Content-Type': 'application/x-www-form-urlencoded'})
        u = resp.strip()
        if u.startswith('http'):
            return u
    except Exception:
        pass
    return None


def send_logs():
    dialog = xbmcgui.Dialog()
    prog = xbmcgui.DialogProgress()
    try:
        prog.create('MasterKodi IL', '[COLOR cyan]אוסף ומעלה לוגים...[/COLOR]')
        prog.update(30)
        text = _collect()
        if not text:
            prog.close()
            dialog.ok('MasterKodi IL', 'לא נמצאו קבצי לוג')
            return
        prog.update(65, '[COLOR cyan]מעלה...[/COLOR]')
        url = _upload(text)
        prog.close()
        if url:
            xbmc.log('[wizard] logs uploaded: %s' % url, xbmc.LOGINFO)
            dialog.ok('הלוגים הועלו',
                      'שלח את הקישור הזה לתמיכה:\n\n[COLOR lime][B]%s[/B][/COLOR]\n\n'
                      '(מידע רגיש כמו טוקנים הוסתר אוטומטית)' % url)
        else:
            dialog.ok('MasterKodi IL',
                      '[COLOR red]העלאת הלוגים נכשלה[/COLOR]\nבדוק חיבור לאינטרנט ונסה שוב.')
    except Exception as e:
        try:
            prog.close()
        except Exception:
            pass
        xbmc.log('[wizard] send_logs error: %s' % e, xbmc.LOGERROR)
        try:
            xbmcgui.Dialog().ok('MasterKodi IL', 'שגיאה בשליחת הלוגים')
        except Exception:
            pass
