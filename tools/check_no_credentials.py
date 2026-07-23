#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail the build if a USER credential is committed in a config-variant.

A variant is captured from a live box, so a personal debrid/trakt token can get
baked into pov/settings.xml (this happened: a real TorBox tb.token shipped in
estuary/af3/nimbus-pov). POV's own PUBLIC app defaults (tmdb_read_token,
trakt.client_id/secret) are fine -- everyone ships the same ones. This guards
only the per-user auth fields."""
import io, os, re, sys, glob

USER_CRED = re.compile(r'^(tb\.token|rd\.token|pm\.token|ad\.token|oc\.token|premiumize\.token|'
                       r'trakt\.token|trakt\.refresh|trakt\.usertoken|trakt\.user|trakt\.expires|'
                       r'OS_USER_API_KEY_VALUE|KT_enc_pass|OSpass.*)$', re.I)

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else '.'
    leaks = []
    for base in ('config-variants', 'config-variants-piers'):
        for f in glob.glob(os.path.join(root, base, '**', '*.xml'), recursive=True):
            t = io.open(f, encoding='utf-8', errors='replace').read()
            for m in re.finditer(r'<setting id="([^"]+)"[^>]*>([^<]+)</setting>', t):
                sid, val = m.group(1), m.group(2).strip()
                if val and val.lower() not in ('true', 'false', '0', '1') and USER_CRED.match(sid):
                    leaks.append((os.path.relpath(f, root).replace(os.sep, '/'), sid, val[:10]))
    if leaks:
        print('CREDENTIAL LEAK -- user auth committed in a variant:', file=sys.stderr)
        for f, sid, v in leaks:
            print('  %s : %s = %s...' % (f, sid, v), file=sys.stderr)
        return 1
    print('[check_no_credentials] clean: no user credentials in any variant')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
