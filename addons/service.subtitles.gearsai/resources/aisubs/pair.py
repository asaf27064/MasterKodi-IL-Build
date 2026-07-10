# -*- coding: utf-8 -*-
# Pair the Gemini API key from a phone/another device.
#
# Kodi on a TV box has an awful on-screen keyboard; pasting a 50-char key
# is painful. Instead we run a tiny local HTTP server, show its URL as a
# QR code on the TV, and the user opens it on their phone and pastes the
# key into a simple form. We poll for the submitted key, validate it, and
# save it to settings.
#
# Both a LAN URL (http://<lan-ip>:<port>) and the localhost URL are shown:
# on a phone with cellular-only / no LAN, localhost won't help, but on the
# same Wi-Fi the LAN URL works. On a PC the user can also just open
# localhost in a browser on the same machine.
#
# Pure stdlib (http.server, socket, threading). QR image is fetched from a
# public QR renderer for the (non-sensitive) LAN URL; if that fails we
# still show the URL as text.

import os
import socket
import threading
import time

try:
    import http.server
    import socketserver
    import urllib.parse
    import urllib.request
except ImportError:
    http = None

from . import kodi_utils

try:
    import xbmcgui
    import xbmc
except ImportError:
    xbmcgui = xbmc = None


_FORM_HTML = u"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MasterKodi AI - חיבור מפתח</title>
<style>
body{{font-family:sans-serif;background:#111;color:#eee;margin:0;padding:24px;direction:rtl}}
.card{{max-width:520px;margin:0 auto;background:#1c1c1c;border-radius:14px;padding:24px}}
h1{{font-size:20px;margin:0 0 8px}} p{{color:#aaa;line-height:1.5}}
input{{width:100%;box-sizing:border-box;font-size:16px;padding:14px;border-radius:10px;
border:1px solid #444;background:#000;color:#fff;direction:ltr;text-align:left}}
button{{width:100%;font-size:18px;padding:14px;margin-top:14px;border:0;border-radius:10px;
background:#2b7;color:#022;font-weight:bold}}
a{{color:#5cf}}
</style></head><body><div class="card">
<h1>MasterKodi AI - חיבור מפתח Gemini</h1>
<p>הדבק כאן את מפתח ה-API מ-<a href="https://aistudio.google.com/apikey" target="_blank">aistudio.google.com/apikey</a>:</p>
<form method="POST" action="/submit">
<input name="key" placeholder="AIza... או AQ.Ab8..." autocomplete="off" autocapitalize="off" spellcheck="false">
<button type="submit">שלח ל-Kodi</button>
</form></div></body></html>"""

_THANKS_HTML = u"""<!doctype html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<style>body{{font-family:sans-serif;background:#111;color:#7e7;text-align:center;padding:60px}}</style>
</head><body><h1>✓ המפתח נשלח ל-Kodi</h1><p>אפשר לסגור את הדף.</p></body></html>"""


def _lan_ip():
    """Best-effort primary LAN IP (no traffic actually sent)."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        return s.getsockname()[0]
    except Exception:
        return '127.0.0.1'
    finally:
        if s:
            try: s.close()
            except Exception: pass


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('0.0.0.0', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Shared(object):
    key = None


def _make_handler(shared):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # silence

        def _send(self, body, code=200):
            data = body.encode('utf-8')
            self.send_response(code)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._send(_FORM_HTML)

        def do_POST(self):
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length).decode('utf-8', 'replace')
                fields = urllib.parse.parse_qs(raw)
                key = (fields.get('key', [''])[0] or '').strip()
                if key:
                    shared.key = key
                self._send(_THANKS_HTML)
            except Exception:
                self._send(_THANKS_HTML)
    return Handler


def _fetch_qr(url, dest):
    """Fetch a QR PNG for `url` from a public renderer. Returns dest on
    success else ''. The encoded data is a LAN address -- not sensitive."""
    try:
        api = 'https://api.qrserver.com/v1/create-qr-code/?size=480x480&data=' + \
              urllib.parse.quote(url, safe='')
        raw = urllib.request.urlopen(api, timeout=10).read()
        with open(dest, 'wb') as f:
            f.write(raw)
        return dest if os.path.getsize(dest) > 0 else ''
    except Exception as e:
        kodi_utils.log('QR fetch failed: {0}'.format(e))
        return ''


class _PairWindow(xbmcgui.WindowDialog if xbmcgui else object):
    """Full-screen dialog: QR image + the URLs + a status line."""
    def __init__(self, qr_path, lan_url, local_url):
        if not xbmcgui:
            return
        self._status = None
        cx = 1920 // 2
        # background dimmer
        bg = xbmcgui.ControlImage(0, 0, 1920, 1080, '')
        bg.setColorDiffuse('0xE0000000')
        self.addControl(bg)
        title = xbmcgui.ControlLabel(0, 90, 1920, 60, 'MasterKodi AI - חיבור מפתח Gemini',
                                     alignment=0x00000002 | 0x00000004, font='font30')
        self.addControl(title)
        if qr_path:
            qr = xbmcgui.ControlImage(cx - 240, 180, 480, 480, qr_path)
            self.addControl(qr)
        info = ('סרוק את הקוד בטלפון, או היכנס לכתובת:\n'
                '[B]{0}[/B]\n(או מאותו מחשב: {1})\n\nהדבק את מפתח ה-Gemini ושלח.'
                ).format(lan_url, local_url)
        lbl = xbmcgui.ControlLabel(cx - 500, 690, 1000, 200, info,
                                   alignment=0x00000002, font='font16')
        self.addControl(lbl)
        self._status = xbmcgui.ControlLabel(cx - 500, 900, 1000, 50, 'ממתין למפתח...',
                                            alignment=0x00000002 | 0x00000004, font='font16')
        self.addControl(self._status)

    def set_status(self, text):
        try:
            if self._status:
                self._status.setLabel(text)
        except Exception:
            pass

    def onAction(self, action):
        # Any back/escape closes.
        if action.getId() in (9, 10, 92, 216, 247, 257, 275, 61467, 61448):
            self.close()


def run_pairing(timeout_sec=300):
    """Show the QR/URL, run the server, poll for a key, validate + save.
    Returns the saved key or '' (cancelled / timed out)."""
    if not http or not xbmcgui:
        kodi_utils.ok_dialog('Pairing not available on this platform.')
        return ''

    shared = _Shared()
    port = _free_port()
    httpd = socketserver.TCPServer(('0.0.0.0', port), _make_handler(shared))
    httpd.timeout = 1
    t = threading.Thread(target=httpd.serve_forever)
    t.daemon = True
    t.start()

    lan = _lan_ip()
    lan_url = 'http://{0}:{1}'.format(lan, port)
    local_url = 'http://localhost:{0}'.format(port)

    qr_path = os.path.join(kodi_utils.profile_dir(), 'pair_qr.png')
    qr_path = _fetch_qr(lan_url, qr_path)

    win = _PairWindow(qr_path, lan_url, local_url)
    win.show()

    saved = ''
    from . import gemini
    monitor = xbmc.Monitor()
    start = time.time()
    try:
        while (time.time() - start) < timeout_sec:
            if monitor.waitForAbort(1):
                break
            if shared.key:
                win.set_status('בודק מפתח...')
                try:
                    model = kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)
                    matched = gemini.test_key(shared.key, model)
                    kodi_utils.set_setting('api_key', shared.key)
                    win.set_status('✓ חובר! ({0})'.format(matched))
                    saved = shared.key
                    xbmc.sleep(1500)
                    break
                except Exception as e:
                    win.set_status('מפתח נדחה, נסה שוב')
                    shared.key = None
    finally:
        try: httpd.shutdown()
        except Exception: pass
        try: win.close()
        except Exception: pass

    return saved
