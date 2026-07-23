#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail the build if a per-USER credential is committed in a shipped settings XML.

A config is captured from a live box, so a personal debrid/Trakt token can get
baked into a settings.xml (this happened: a real TorBox tb.token shipped in
estuary/af3/nimbus-pov). That leak lived in config-variants/, but the SAME
capture could land in config/ or overlays/ and the old scanner (config-variants
only) would miss it -- so we scan every place a captured settings.xml ships.

Two checks per <setting id="X">value</setting>:
  (A) X is a known user-auth id (tb.token, trakt.token, ...) with a real value.
  (B) X is not a known-public setting, LOOKS credential-ish by name, AND its
      value is token-shaped (UUID / 32+ hex) -- catches a cred hidden under a
      renamed id.

Public app defaults (tmdb_read_token, trakt.client_id/secret) ship the same in
every client BY DESIGN and are allowlisted. NOTE: this guards captured USER
credentials, not the intentional public shared keys hardcoded in source (those
must ship in the client and can never be secret -- a broad code scan is nothing
but false positives: upstream TMDB/fanart/Trakt app keys, API path fragments,
dict keys). Keep those out of committed config, not out of client code.
"""
import io, os, re, sys, glob

SCAN_DIRS = ('config-variants', 'config-variants-piers', 'config', 'overlays', 'overlays-piers')

# per-USER auth ids: never ship a value
USER_CRED = re.compile(r'^(tb\.token|rd\.token|pm\.token|ad\.token|oc\.token|premiumize\.token|'
                       r'trakt\.token|trakt\.refresh|trakt\.usertoken|trakt\.user|trakt\.expires|'
                       r'tmdb\.username|tmdb\.sessionid|'
                       r'OS_USER_API_KEY_VALUE|KT_enc_pass|OSpass.*)$', re.I)

# ids that are public-by-design (same value in every client) -> never a leak
PUBLIC_SETTING = {
    'tmdb_read_token', 'trakt.client_id', 'trakt.client_secret',
    'tmdb.api.key', 'fanarttv.api.key',
}

# name LOOKS like a credential (for the heuristic (B) check)
CREDISH = re.compile(r'(token|secret|passw|apikey|api_key|sessionid|userkey|auth)', re.I)
# value LOOKS like a token: a UUID, or 32+ hex, or a long opaque blob
TOKEN_SHAPE = re.compile(r'^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                         r'|[0-9a-f]{32,}'
                         r'|[A-Za-z0-9_\-]{40,})$', re.I)

SETTING = re.compile(r'<setting id="([^"]+)"[^>]*>([^<]+)</setting>')


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    leaks = []
    scanned = 0
    for base in SCAN_DIRS:
        for f in glob.glob(os.path.join(root, base, '**', '*.xml'), recursive=True):
            scanned += 1
            t = io.open(f, encoding='utf-8', errors='replace').read()
            for m in SETTING.finditer(t):
                sid, val = m.group(1), m.group(2).strip()
                if not val or val.lower() in ('true', 'false', '0', '1', 'default'):
                    continue
                if sid in PUBLIC_SETTING:
                    continue
                rel = os.path.relpath(f, root).replace(os.sep, '/')
                if USER_CRED.match(sid):
                    leaks.append((rel, sid, val[:10], 'known user-auth id'))
                elif CREDISH.search(sid) and TOKEN_SHAPE.match(val):
                    leaks.append((rel, sid, val[:10], 'token-shaped value under a credential-ish id'))
    if leaks:
        print('CREDENTIAL LEAK -- user auth committed in a shipped settings XML:', file=sys.stderr)
        for f, sid, v, why in leaks:
            print('  %s : %s = %s...  (%s)' % (f, sid, v, why), file=sys.stderr)
        return 1
    print('[check_no_credentials] clean: scanned %d XML file(s) across %s'
          % (scanned, '/'.join(SCAN_DIRS)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
