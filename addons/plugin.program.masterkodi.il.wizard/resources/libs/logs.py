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
import uuid

import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs

from urllib.request import Request, urlopen

MAX_BYTES = 380 * 1024                       # keep under paste-host limits; tail wins

# Cloudflare pool Worker (same host the subtitle pool uses). A /v1/logs endpoint
# stores the upload to R2 under the device id and returns a readable URL.
CF_LOGS_URL = 'https://masterkodi-logs.asaf27064.workers.dev/v1/logs'
CF_LOGS_KEY = 'mk-76ed711408c449eda0c5a2d868720b0438e36309'  # shared build key (same as subtitle pool)


def _device():
    """Stable device id + human-readable metadata, so an uploaded log is
    attributable to a specific box (esp. useful across many Android installs)."""
    dev_id = ''
    try:
        d = xbmcvfs.translatePath('special://profile/addon_data/plugin.program.masterkodi.il.wizard/')
        if not os.path.isdir(d):
            os.makedirs(d)
        p = os.path.join(d, 'device_id')
        if os.path.isfile(p):
            dev_id = _read(p).strip()
        if not dev_id:
            dev_id = uuid.uuid4().hex[:12]
            with open(p, 'w', encoding='utf-8') as fh:
                fh.write(dev_id)
    except Exception:
        pass

    def lbl(x):
        try:
            return xbmc.getInfoLabel(x) or ''
        except Exception:
            return ''
    plat = 'unknown'
    for name in ('Android', 'Windows', 'Linux', 'OSX', 'IOS', 'Darwin'):
        try:
            if xbmc.getCondVisibility('System.Platform.%s' % name):
                plat = name
                break
        except Exception:
            pass
    try:
        build = xbmcaddon.Addon().getAddonInfo('version')
    except Exception:
        build = '?'
    return {
        'device_id': dev_id or '?',
        'name': lbl('System.FriendlyName'),
        'platform': plat,
        'kodi': lbl('System.BuildVersion').split(' ')[0],
        'wizard': build,
        'skin': lbl('System.CurrentSkin'),
        'time': lbl('System.Date') + ' ' + lbl('System.Time'),
    }


def _header(info):
    lines = ['======== MASTERKODI IL · LOG UPLOAD ========']
    for k in ('device_id', 'name', 'platform', 'kodi', 'wizard', 'skin', 'time'):
        lines.append('%-10s %s' % (k + ':', info.get(k, '')))
    lines.append('=============================================')
    return '\n'.join(lines) + '\n\n'

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


def _upload_cloudflare(text, info):
    """Store the log to the Cloudflare R2 folder via the pool Worker /v1/logs.
    Returns a readable URL or None (silently, until the Worker endpoint exists)."""
    try:
        body = text.encode('utf-8', 'replace')
        headers = {'User-Agent': 'MasterKodiIL', 'Content-Type': 'text/plain; charset=utf-8',
                   'X-Gears-Key': CF_LOGS_KEY, 'X-Device-Id': info.get('device_id', '?'),
                   'X-Platform': info.get('platform', '?')}
        resp = _post(CF_LOGS_URL, body, headers)
        try:
            data = json.loads(resp)
            return data.get('url') or data.get('read_url')
        except Exception:
            return resp.strip() if resp.strip().startswith('http') else None
    except Exception:
        return None


def _upload_paste(text):
    body = text.encode('utf-8', 'replace')
    # 1) Kodi's own paste host (hastebin API): {"key": "..."} -> /<key>
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
        prog.update(20)
        info = _device()
        text = _header(info) + _collect()
        if not text.strip():
            prog.close()
            dialog.ok('MasterKodi IL', 'לא נמצאו קבצי לוג')
            return
        prog.update(55, '[COLOR cyan]מעלה...[/COLOR]')
        cf = _upload_cloudflare(text, info)       # our storage (readable directly)
        prog.update(80)
        paste = _upload_paste(text)               # always-available public paste
        prog.close()
        url = cf or paste
        if url:
            xbmc.log('[wizard] logs uploaded id=%s cf=%s paste=%s'
                     % (info.get('device_id'), cf, paste), xbmc.LOGINFO)
            dialog.ok('הלוגים הועלו',
                      'מזהה מכשיר: [COLOR yellow]%s[/COLOR]\n\n'
                      'שלח את הקישור לתמיכה:\n[COLOR lime][B]%s[/B][/COLOR]\n\n'
                      '(מידע רגיש כמו טוקנים הוסתר אוטומטית)'
                      % (info.get('device_id', '?'), url))
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
