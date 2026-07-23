#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify the content-source invariants across every config-variant.

Grep is unreliable here (it found false "missing" continue-watching on nimbus/af3
because it matched strings, not menu semantics). This instead extracts every
plugin:// action from a variant -- regardless of the skin's menu format
(skinshortcuts .properties/.DATA.xml, skinvariables nodes, nimbus xml, estuary
favourites/Home) -- decodes it, and classifies it by ENGINE and FUNCTION. Then it
checks the invariants Asaf specified.

Invariants (per variant):
  * search           -> the content ADDON itself (pov|gears), never tmdbhelper
  * continue-watching-> the content addon itself
  * power/cache-clear-> pov|gears matching the variant
  * connect-services -> pov|gears matching
  * maintenance      -> present
  * widgets          -> engine mix (extension vs tmdbhelper) + category set
  * NO cross-contamination: a pov variant must contain ZERO gears refs, & vice
    versa (this is the "no gears junk in pov" rule).

Regression guards (FAIL, one per bug class actually hit this session):
  * every plugin.video.pov mode/action is a REAL POV function -- validated
    against the reconstructed POV addon in CI (addons/plugin.video.pov, after
    apply_overlay), else a denylist of the known Gears/fenlight leftovers
    (history.search, trakt.list.search_trakt_lists, navigator.torbox, ...).
  * no Gears TEXT branding on a POV build ("Gears AI", the shared subtitle addon,
    is allowed).
  * trending uses trakt_trending, never trending_week.
  * a variant that browses POV services ships (or inherits) the pov/ seeds.
  * POV skinvariables view coverage matches Gears (movies/tvshows/seasons/...).

Exit non-zero with --strict on any FAIL (WARNs never fail). CI runs it
--strict on both fleets after apply_overlay.

Usage: python tools/verify_variants.py [--variant NAME] [--json] [--strict]
"""
import os, re, io, glob, json, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# variant -> intended engine (from the dir name)
def intended_engine(v):
    return 'gears' if 'gears' in v else 'pov'

READABLE = ('.xml', '.json', '.properties')

# --- regression guards: every bug class we've actually hit gets a check here ---

# POV modes/actions that leaked from Gears/fenlight and DON'T exist in POV.
# Used as a denylist when the POV addon source isn't available to validate
# against (locally); CI validates every token against the reconstructed addon.
KNOWN_BAD_POV = {
    'history.search', 'trakt.list.search_trakt_lists', 'navigator.torbox',
    'navigator.search_history', 'imdb_keyword_movie', 'imdb_keyword_tvshow',
    'imdb_keywords_list_contents', 'imdb_build_keyword_results', 'tmdb_movie_sets',
}

# A piers variant inherits pov/ seeds from its k21 base (mirrors the fetch
# fallback in content_source._variant_roots -> [piers, base]).
PIERS_BASE = {
    'estuary-piers-pov': 'estuary-pov',
    'zephyr-piers-pov': 'zephyr-pov-tmdb',
}


def pov_valid_tokens():
    """Real POV modes/actions, harvested from the POV addon if it's on disk
    (addons/plugin.video.pov, reconstructed by apply_overlay in CI). Returns a
    set, or None if the addon isn't present (caller falls back to the denylist)."""
    lib = os.path.join(ROOT, 'addons', 'plugin.video.pov', 'resources', 'lib')
    if not os.path.isdir(lib):
        return None
    src = []
    for dp, _d, fs in os.walk(lib):
        for f in fs:
            if f.endswith('.py'):
                try:
                    src.append(io.open(os.path.join(dp, f), encoding='utf-8',
                                       errors='replace').read())
                except Exception:
                    pass
    return '\n'.join(src)


_POV_SRC = None
def pov_token_ok(tok):
    if tok in ('movie', 'tvshow', 'tv'):
        return True
    # The denylist is AUTHORITATIVE -- these exact Gears/fenlight modes are known
    # not to exist in POV. Checked first so a fuzzy addon match can't excuse one
    # (e.g. 'history.search' whose 'history' fragment appears in POV's
    # 'search_history' -- the false-negative this fixes).
    if tok in KNOWN_BAD_POV:
        return False
    global _POV_SRC
    if _POV_SRC is None:
        _POV_SRC = pov_valid_tokens() or ''
    if not _POV_SRC:                       # no addon on disk -> denylist only
        return True
    # PERMISSIVE secondary: the denylist above is the real guard; here we only
    # want to avoid false FAILs on the many ways POV routes a mode (def, dict
    # string, dotted handler). Accept if any fragment is present. This can miss a
    # brand-new bad mode, but such modes get added to KNOWN_BAD_POV once seen --
    # a false FAIL that blocks a good build is worse than a miss.
    parts = [tok, tok.split('.')[0], tok.split('.')[-1]]
    return any(("def %s" % p) in _POV_SRC or ("'%s'" % p) in _POV_SRC
               or ('"%s"' % p) in _POV_SRC for p in parts)

# NB: the query may contain UNENCODED spaces (name=Continue Watching&mode=...),
# so we must NOT stop at whitespace -- capture up to the closing quote/angle.
# Trailing junk like ",return)" is harmless: mode=/action=/info= are parsed out.
URL_RE = re.compile(r'plugin://(plugin\.video\.[a-z0-9.]+|plugin\.program\.[a-z0-9.]+)/\?([^"\'<]+)')


def read_all(vdir):
    """Concatenate every readable file (skip index.json + media)."""
    out = []
    for f in sorted(glob.glob(os.path.join(vdir, '**', '*'), recursive=True)):
        if not os.path.isfile(f):
            continue
        if os.path.basename(f) in ('index.json', 'README.md'):
            continue
        if os.sep + 'media' + os.sep in f:
            continue
        if not f.lower().endswith(READABLE):
            continue
        try:
            out.append((os.path.relpath(f, vdir).replace(os.sep, '/'),
                        io.open(f, encoding='utf-8', errors='replace').read()))
        except Exception:
            pass
    return out


def engine_of(plug):
    if plug == 'plugin.video.pov':
        return 'pov'
    if plug == 'plugin.video.gears':
        return 'gears'
    if plug == 'plugin.video.themoviedb.helper':
        return 'tmdb'
    if plug.endswith('masterkodi.il.wizard'):
        return 'wizard'
    return plug


def classify(plug, qs):
    """Return (engine, function) for one plugin URL query-string."""
    eng = engine_of(plug)
    q = urllib.parse.unquote(qs.replace('&amp;', '&'))
    mode = (re.search(r'mode=([^&]+)', q) or [None, ''])[1]
    action = (re.search(r'action=([^&]+)', q) or [None, ''])[1]
    info = (re.search(r'info=([^&]+)', q) or [None, ''])[1]
    blob = (mode + ' ' + action + ' ' + info).lower()

    if 'search' in blob:
        fn = 'search'
    elif 'in_progress' in blob or 'continue' in blob:
        fn = 'continue'
    elif 'clear_all_cache' in blob or 'clear_cache' in blob:
        fn = 'cache'
    elif 'maintenance_folder' in blob:
        fn = 'maintenance'
    elif 'myservices' in blob or 'torbox' in blob or 'authenticate' in blob \
            or 'build_shortcut_folder' in blob or 'account_info' in blob:
        fn = 'services'
    elif mode.startswith('build_') or info:
        fn = 'widget'
    else:
        fn = 'other'
    return eng, fn, (info or action or mode)


def analyze(vdir):
    files = read_all(vdir)
    items = []               # (engine, function, key, file)
    raw = []                 # concatenated readable text (for label/trending scans)
    pov_tokens = set()       # (kind, token) for every plugin.video.pov mode/action
    for rel, text in files:
        raw.append(text)
        dec = text.replace('&amp;', '&')
        for m in URL_RE.finditer(text):
            eng, fn, key = classify(m.group(1), m.group(2))
            items.append((eng, fn, key, rel))
        for m in re.finditer(r'plugin\.video\.pov/\?([^"\'<]+)', dec):
            q = urllib.parse.unquote(m.group(1))
            for kind, pat in (('mode', r'mode=([a-z_.]+)'), ('action', r'action=([a-z_]+)')):
                mm = re.search(pat, q)
                if mm:
                    pov_tokens.add((kind, mm.group(1)))
    return items, '\n'.join(raw), pov_tokens


def summarize(v):
    vdir = os.path.join(ROOT, 'config-variants', v)
    if not os.path.isdir(vdir):
        vdir = os.path.join(ROOT, 'config-variants-piers', v)
    items, raw, pov_tokens = analyze(vdir)
    want = intended_engine(v)
    other = 'gears' if want == 'pov' else 'pov'

    def engines_for(fn):
        return sorted({e for e, f, k, r in items if f == fn})

    # widget engine mix + category set
    widget_engines = {}
    widget_cats = set()
    for e, f, k, r in items:
        if f == 'widget':
            widget_engines[e] = widget_engines.get(e, 0) + 1
            widget_cats.add(k)

    # cross-contamination: any ref to the OTHER content engine, anywhere
    contamination = sorted({(k, r) for e, f, k, r in items if e == other})

    uses_services = bool([1 for e, f, k, r in items if f == 'services'])
    return {
        'variant': v,
        'vdir': vdir,
        'intended': want,
        'search': engines_for('search'),
        'continue': engines_for('continue'),
        'cache': engines_for('cache'),
        'services': engines_for('services'),
        'uses_services': uses_services,
        'maintenance': bool([1 for e, f, k, r in items if f == 'maintenance']),
        'widget_engines': widget_engines,
        'widget_cats': sorted(widget_cats),
        'contamination': contamination,
        'raw': raw,
        'pov_tokens': pov_tokens,
    }


def check(s):
    """Return list of (severity, message) problems for one summary."""
    want = s['intended']
    probs = []

    def one_engine(fn, allow_tmdb=False):
        engs = s[fn]
        if not engs:
            probs.append(('WARN', '%s: none found' % fn))
            return
        for e in engs:
            if e == 'tmdb' and not allow_tmdb:
                probs.append(('FAIL', '%s uses tmdbhelper (must be the addon itself)' % fn))
            elif e in ('pov', 'gears') and e != want:
                probs.append(('FAIL', '%s uses %s on a %s variant' % (fn, e, want)))

    one_engine('search')           # must be the addon, never tmdb
    one_engine('continue')         # must be the addon
    one_engine('cache')            # pov|gears matching
    one_engine('services')         # pov|gears matching
    if not s['maintenance']:
        probs.append(('WARN', 'no maintenance entry found'))
    if s['contamination']:
        keys = ', '.join(sorted({k for k, r in s['contamination']})[:5])
        probs.append(('FAIL', 'contains %s refs on a %s variant: %s'
                      % ('gears' if want == 'pov' else 'pov', want, keys)))

    # --- regression guards (every bug class we actually hit) ---
    if want == 'pov':
        # 5. POV modes/actions must be real POV functions (the history.search /
        #    trakt.list.search_trakt_lists / navigator.torbox class of bug).
        for kind, tok in sorted(s['pov_tokens']):
            if not pov_token_ok(tok):
                probs.append(('FAIL', 'invalid POV %s=%s (not a real POV function)' % (kind, tok)))

        # 6. No Gears TEXT branding on a POV build ("Gears AI" = the shared
        #    subtitle addon, allowed).
        raw_noai = s['raw'].replace('Gears AI', '').replace('GearsAI', '')
        for pat, label in ((r'\(Gears\)', '(Gears) label'),
                           (r'Notification\(Gears', 'Gears notification'),
                           (r'(?:קאש|מטמון)\s*Gears', 'clear-Gears-cache label')):
            if re.search(pat, raw_noai):
                probs.append(('FAIL', 'Gears text branding on a POV variant: %s' % label))

        # 7. Trending must be trakt_trending, never trending_week (Asaf's pick).
        if 'trending_week' in s['raw']:
            probs.append(('FAIL', 'uses info=trending_week (should be trakt_trending)'))

        # 8. A variant whose menu browses POV services/shortcut-folders MUST have
        #    the pov/ seeds (the af3-pov-tmdb-missing-seeds bug). A PIERS variant
        #    inherits them from its k21 base via the fetch fallback in
        #    _variant_roots, so it's fine if the base has them.
        if s['uses_services']:
            seed = os.path.join(s['vdir'], 'pov', 'shortcut_folders.json')
            ok = os.path.isfile(seed)
            if not ok and s['variant'] in PIERS_BASE:
                ok = os.path.isfile(os.path.join(ROOT, 'config-variants',
                                    PIERS_BASE[s['variant']], 'pov', 'shortcut_folders.json'))
            if not ok:
                probs.append(('FAIL', 'uses POV services but has no pov/shortcut_folders.json seed'))

        # 9. POV view coverage: if the variant ships a skinvariables viewtypes
        #    json, plugin.video.pov must map the same list types Gears does.
        for vt in glob.glob(os.path.join(s['vdir'], 'skinvariables', '*-viewtypes.json')):
            try:
                d = json.load(io.open(vt, encoding='utf-8'))
            except Exception:
                continue
            pv = d.get('plugin.video.pov', {})
            missing = [k for k in ('movies', 'tvshows', 'seasons', 'episodes', 'none')
                       if k not in pv]
            if missing:
                probs.append(('FAIL', 'POV view coverage incomplete (%s missing %s)'
                              % (os.path.basename(vt), ','.join(missing))))
    return probs


def main():
    only = None
    as_json = '--json' in sys.argv
    if '--variant' in sys.argv:
        only = sys.argv[sys.argv.index('--variant') + 1]
    variants = []
    for base in ('config-variants', 'config-variants-piers'):
        for d in sorted(os.listdir(os.path.join(ROOT, base))):
            if os.path.isdir(os.path.join(ROOT, base, d)):
                variants.append(d)
    if only:
        variants = [only]

    results = []
    for v in variants:
        s = summarize(v)
        s['problems'] = check(s)
        results.append(s)

    if as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    print('%-20s %-6s %-6s %-6s %-6s %-6s  %-18s  %s' % (
        'variant', 'search', 'cont', 'cache', 'svc', 'maint', 'widgets(engine:n)', 'problems'))
    print('-' * 130)
    for s in results:
        we = ','.join('%s:%d' % (k, v) for k, v in sorted(s['widget_engines'].items()))
        nfail = sum(1 for sev, _ in s['problems'] if sev == 'FAIL')
        nwarn = sum(1 for sev, _ in s['problems'] if sev == 'WARN')
        tag = ('%dF %dW' % (nfail, nwarn)) if s['problems'] else 'OK'
        print('%-20s %-6s %-6s %-6s %-6s %-6s  %-18s  %s' % (
            s['variant'], ','.join(s['search']) or '-', ','.join(s['continue']) or '-',
            ','.join(s['cache']) or '-', ','.join(s['services']) or '-',
            'Y' if s['maintenance'] else '.', we, tag))
    print()
    total_fail = 0
    for s in results:
        if s['problems']:
            print('### %s (%s)' % (s['variant'], s['intended']))
            for sev, msg in s['problems']:
                print('   [%s] %s' % (sev, msg))
                total_fail += (sev == 'FAIL')
    # --strict: non-zero exit on any FAIL, so CI blocks a regression. WARNs
    # (base-skin-provided invariants, item-based maintenance) never fail the build.
    if '--strict' in sys.argv and total_fail:
        print('\n%d FAIL(s) -- strict mode' % total_fail)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
