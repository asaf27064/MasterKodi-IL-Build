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


def _scan_text(text, rel, leaks):
    """Apply the setting-credential checks to one XML blob."""
    for m in SETTING.finditer(text):
        sid, val = m.group(1), m.group(2).strip()
        if not val or val.lower() in ('true', 'false', '0', '1', 'default'):
            continue
        if sid in PUBLIC_SETTING:
            continue
        if USER_CRED.match(sid):
            leaks.append((rel, sid, val[:10], 'known user-auth id'))
        elif CREDISH.search(sid) and TOKEN_SHAPE.match(val):
            leaks.append((rel, sid, val[:10], 'token-shaped value under a credential-ish id'))


def _scan_bundles(bundles_dir, leaks):
    """Scan every settings XML INSIDE each install-bundle zip. Bundles are
    repacked from a base captured off a live box and copy 'kept-as-was' content
    verbatim -- exactly where a harvested login can ride along into the published
    artifact without ever touching the committed config the dir-scan covers."""
    import zipfile
    n = 0
    for z in sorted(glob.glob(os.path.join(bundles_dir, '*.zip'))):
        try:
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith('.xml'):
                        continue
                    n += 1
                    try:
                        text = zf.read(name).decode('utf-8', 'replace')
                    except Exception:
                        continue
                    _scan_text(text, '%s!%s' % (os.path.basename(z), name), leaks)
        except Exception as e:
            print('  (could not read bundle %s: %s)' % (z, e), file=sys.stderr)
    return n


def main():
    args = [a for a in sys.argv[1:]]
    bundles_dir = None
    if '--bundles' in args:
        i = args.index('--bundles')
        bundles_dir = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    root = args[0] if args else '.'
    leaks = []
    scanned = 0
    for base in SCAN_DIRS:
        for f in glob.glob(os.path.join(root, base, '**', '*.xml'), recursive=True):
            scanned += 1
            rel = os.path.relpath(f, root).replace(os.sep, '/')
            _scan_text(io.open(f, encoding='utf-8', errors='replace').read(), rel, leaks)
    where = '/'.join(SCAN_DIRS)
    if bundles_dir:
        scanned += _scan_bundles(bundles_dir, leaks)
        where += ' + bundles'
    if leaks:
        print('CREDENTIAL LEAK -- user auth in a shipped settings XML:', file=sys.stderr)
        for f, sid, v, why in leaks:
            print('  %s : %s = %s...  (%s)' % (f, sid, v, why), file=sys.stderr)
        return 1
    print('[check_no_credentials] clean: scanned %d XML file(s) across %s' % (scanned, where))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
