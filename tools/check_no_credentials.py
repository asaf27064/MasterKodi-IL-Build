#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail the build if a NEW, unreviewed credential appears in a shipped settings XML.

The threat is a config captured off a live box baking the USER's own login into a
settings.xml (this happened: a real TorBox tb.token shipped once). But the build
also DELIBERATELY ships shared service keys (MDBList, OMDB, the TMDB read token,
the Trakt app secret, ...) so it works out of the box for everyone -- those are
public by design and must NOT be flagged.

Rather than hand-maintain a fragile per-id allow/deny list, we BASELINE the
credentials currently in the repo (they are all intentional) into
tools/known_public_keys.txt, and flag only values that are NOT in that baseline
-- i.e. something newly introduced, which a human should eyeball. Empty settings
are always fine. Regenerate the baseline after intentionally adding/rotating a
shared key:  python tools/check_no_credentials.py --update-baseline

Also scans settings XML INSIDE install-bundle zips (--bundles DIR): the bundle
copies 'kept-as-was' content verbatim from a live-box base, so a harvested login
can ride into the published artifact even when committed config is clean.
"""
import io, os, re, sys, glob, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
BASELINE = os.path.join(HERE, 'known_public_keys.txt')
SCAN_DIRS = ('config-variants', 'config-variants-piers', 'config', 'overlays', 'overlays-piers')

# per-USER login ids: personal accounts that must never ship a value. Checked
# even when the value is short (a password needn't look token-shaped). They are
# all empty today, so none are in the baseline -- a future capture is caught.
PERSONAL_IDS = {
    # debrid: token AND account_id/client_id/refresh -- a shipped tb.account_id
    # slipped through because the old set + heuristic didn't cover account_id.
    'tb.token', 'tb.account_id', 'rd.token', 'rd.secret', 'rd.username',
    'rd.client_id', 'rd.refresh', 'pm.token', 'pm.account_id', 'ad.token',
    'ad.account_id', 'oc.token', 'oc.account_id', 'premiumize.token',
    'easynews_user', 'easynews_password',
    'trakt.token', 'trakt.refresh', 'trakt.usertoken', 'trakt.user', 'trakt_user',
    'trakt.expires',
    'tmdb.token', 'tmdb.username', 'tmdb.account_id', 'tmdb.session_account_id',
    'tmdb.session_id',
    'hebrew_subtitles.ktuvit_password', 'hebrew_subtitles.opensubtitles_apikey',
    'os_user_api_key_value', 'kt_enc_pass',
    # POV list-service / rating-db personal keys
    'mdblist.token', 'mdblist_user', 'rpdb_api_key',
    # TMDbHelper per-user OAuth tokens + personal API keys (underscore ids).
    # NB: the shared public app keys (tmdb_read_token, trakt.client_id/secret,
    # mdblist_api_key) are NOT here -- those are intentionally baselined.
    'tmdb_user_token', 'tmdb_user_token_access', 'trakt_token', 'trakt_token_access',
    'tvdb_token', 'tvdb_token_access', 'gemini_apikey', 'ratingposterdb_apikey',
    'fanarttv_clientkey', 'fanarttv_clientkey_access',
    'monitor_userlist', 'monitor_userslug',
}
PERSONAL_PAT = re.compile(r'^OSpass', re.I)
# a value whose ID structurally looks like a key/token/session/account binding
CREDISH = re.compile(r'(token|secret|passw|api_?key|session|account_id|client_id|'
                     r'client_secret|refresh|usertoken|userkey|auth|customer)', re.I)
SHAPE = re.compile(r'^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
                   r'|[A-Za-z0-9._-]{12,})$')
SETTING = re.compile(r'<setting id="([^"]+)"[^>]*>([^<]+)</setting>')
_SKIP_VALS = {'true', 'false', '0', '1', 'default'}


def _is_personal(sid):
    return sid.lower() in PERSONAL_IDS or bool(PERSONAL_PAT.match(sid))


def _cred_shaped(sid, val):
    if _is_personal(sid):
        return True
    return bool(CREDISH.search(sid)) and bool(SHAPE.match(val))


def _fp(sid, val):
    return '%s\t%s' % (sid.lower(), hashlib.sha256(val.encode('utf-8')).hexdigest()[:16])


def _iter_settings_text(text):
    for sid, val in SETTING.findall(text):
        val = val.strip()
        if not val or val.lower() in _SKIP_VALS:
            continue
        if _cred_shaped(sid, val):
            yield sid, val


def _collect_repo(root):
    """[(rel, sid, val)] for every credential-shaped, non-empty setting on disk."""
    out = []
    for base in SCAN_DIRS:
        for f in glob.glob(os.path.join(root, base, '**', '*.xml'), recursive=True):
            rel = os.path.relpath(f, root).replace(os.sep, '/')
            text = io.open(f, encoding='utf-8', errors='replace').read()
            for sid, val in _iter_settings_text(text):
                out.append((rel, sid, val))
    return out


# --- Python source scan: catch a hardcoded credential in shipped .py. The Ktuvit
# subs login lived directly in Python and XML-only scanning missed it. Tight by
# design: only a STRING-LITERAL assignment to a credential-NAMED variable, with a
# secret-SHAPED value -- so `x = get_setting(...)`, URLs, templates, headers, and
# human messages don't trip it. Intentional shared creds (e.g. the Ktuvit account)
# are baselined exactly like the shared XML keys.
PY_SCAN_DIRS = ('addons', 'overlays', 'overlays-piers')
# plain + ANNOTATED assignment: id = "v"  /  id: type = "v"
PY_ASSIGN = re.compile(r'''(?P<id>[A-Za-z_][A-Za-z0-9_]*)\s*(?::\s*[A-Za-z_][\w.\[\], ]*?)?\s*=\s*'''
                       r'''(?P<q>['"])(?P<val>[^'"\n]{6,})(?P=q)''')
# dict-literal entry: "id": "v"  (credential-named key -> string literal)
PY_DICT = re.compile(r'''(?P<q1>['"])(?P<id>[A-Za-z_][A-Za-z0-9_]*)(?P=q1)\s*:\s*'''
                     r'''(?P<q2>['"])(?P<val>[^'"\n]{6,})(?P=q2)''')
PY_ID_CRED = re.compile(r'(password|passwd|secret|token|api_?key|apikey|access_key|auth_key|email)', re.I)
B64ISH = re.compile(r'^[A-Za-z0-9+/]{16,}={0,2}$')
EMAILISH = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _py_cred(pid, val):
    if any(c in val for c in ('://', ' ', '{', '}', '%', '$')) or val.lower() in _SKIP_VALS:
        return False
    if 'email' in pid.lower():
        return bool(EMAILISH.match(val))
    return bool(SHAPE.match(val) or B64ISH.match(val))


def _collect_python(root):
    """[(rel, id, val)] for every credential-shaped hardcoded literal in shipped
    .py -- plain/annotated assignments AND dict-literal entries. A regex net for
    an ACCIDENTAL commit, not a determined-attacker boundary (bytes/obfuscation
    are out of scope); shipped creds are also baselined as intentional."""
    out = []
    for base in PY_SCAN_DIRS:
        for f in glob.glob(os.path.join(root, base, '**', '*.py'), recursive=True):
            rel = os.path.relpath(f, root).replace(os.sep, '/')
            try:
                text = io.open(f, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            for rx in (PY_ASSIGN, PY_DICT):
                for m in rx.finditer(text):
                    pid, val = m.group('id'), m.group('val').strip()
                    if PY_ID_CRED.search(pid) and _py_cred(pid, val):
                        out.append((rel, pid, val))
    return out


def _collect_bundles(bundles_dir):
    import zipfile
    out = []
    for z in sorted(glob.glob(os.path.join(bundles_dir, '*.zip'))):
        try:
            with zipfile.ZipFile(z) as zf:
                for name in zf.namelist():
                    if not name.lower().endswith('.xml'):
                        continue
                    try:
                        text = zf.read(name).decode('utf-8', 'replace')
                    except Exception:
                        continue
                    for sid, val in _iter_settings_text(text):
                        out.append(('%s!%s' % (os.path.basename(z), name), sid, val))
        except Exception as e:
            print('  (could not read bundle %s: %s)' % (z, e), file=sys.stderr)
    return out


def _load_baseline():
    known = set()
    if os.path.isfile(BASELINE):
        for line in io.open(BASELINE, encoding='utf-8'):
            line = line.split('#', 1)[0].strip()
            if line:
                known.add(line)
    return known


def _update_baseline(root):
    items = _collect_repo(root) + _collect_python(root)
    # REFUSE to bless a PERSONAL login (debrid/Trakt/account_id/...). --update-
    # baseline is for intentional SHARED keys only; blessing a personal cred would
    # permanently whitelist a leaked user login. Scrub it first.
    personal = [(r, sid, val) for (r, sid, val) in items if _is_personal(sid)]
    if personal:
        print('REFUSING --update-baseline: personal credential(s) present -- scrub, '
              'do NOT bless:', file=sys.stderr)
        for r, sid, v in personal:
            # location + id only -- never any of the value (this can run in CI)
            print('  %s : %s' % (r, sid), file=sys.stderr)
        return 1
    # id + value-HASH only in the committed baseline; do NOT write a value prefix
    # (even 6 chars of a real secret shouldn't land in a public file).
    lines = sorted(set('%s  # %s' % (_fp(sid, val), sid) for _r, sid, val in items))
    with io.open(BASELINE, 'w', encoding='utf-8', newline='\n') as fh:
        fh.write('# Baseline of intentional/public credentials currently shipped.\n'
                 '# <id-lowercase>\\t<sha256(value)[:16]>  # id\n'
                 '# Regenerate after adding/rotating a shared key:\n'
                 '#   python tools/check_no_credentials.py --update-baseline\n')
        for l in lines:
            fh.write(l + '\n')
    print('[check_no_credentials] baseline written: %d intentional credential(s)' % len(lines))
    return 0


def main():
    args = list(sys.argv[1:])
    if '--update-baseline' in args:
        args.remove('--update-baseline')
        return _update_baseline(args[0] if args else '.')
    bundles_dir = None
    if '--bundles' in args:
        i = args.index('--bundles')
        bundles_dir = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
    root = args[0] if args else '.'
    known = _load_baseline()
    found = _collect_repo(root) + _collect_python(root)
    where = '/'.join(SCAN_DIRS) + ' + *.py'
    if bundles_dir:
        found += _collect_bundles(bundles_dir)
        where += ' + bundles'
    leaks = [(rel, sid, val) for rel, sid, val in found if _fp(sid, val) not in known]
    if leaks:
        print('CREDENTIAL LEAK -- NEW/unreviewed credential in a shipped settings XML.', file=sys.stderr)
        print('If this value is an intentional shared key, add it with:', file=sys.stderr)
        print('  python tools/check_no_credentials.py --update-baseline', file=sys.stderr)
        for rel, sid, val in leaks:
            # print the LOCATION only, never any of the value -- this runs in CI
            # and its output is world-readable; echoing even a prefix of a live
            # credential into the build log is itself a leak.
            print('  %s : %s' % (rel, sid), file=sys.stderr)
        return 1
    print('[check_no_credentials] clean: %d credential-shaped value(s), all in baseline (%s)'
          % (len(found), where))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
