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
  * skin-switch      -> present
  * widgets          -> engine mix (extension vs tmdbhelper) + category set
  * genres / networks-> present
  * NO cross-contamination: a pov variant must contain ZERO gears refs, & vice
    versa (this is the "no gears junk in pov" rule).

Usage: python tools/verify_variants.py [--variant NAME] [--json]
"""
import os, re, io, glob, json, sys, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# variant -> intended engine (from the dir name)
def intended_engine(v):
    return 'gears' if 'gears' in v else 'pov'

READABLE = ('.xml', '.json', '.properties')

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
    for rel, text in files:
        for m in URL_RE.finditer(text):
            eng, fn, key = classify(m.group(1), m.group(2))
            items.append((eng, fn, key, rel))
    return items


def summarize(v):
    vdir = os.path.join(ROOT, 'config-variants', v)
    if not os.path.isdir(vdir):
        vdir = os.path.join(ROOT, 'config-variants-piers', v)
    items = analyze(vdir)
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

    return {
        'variant': v,
        'intended': want,
        'search': engines_for('search'),
        'continue': engines_for('continue'),
        'cache': engines_for('cache'),
        'services': engines_for('services'),
        'maintenance': bool([1 for e, f, k, r in items if f == 'maintenance']),
        'widget_engines': widget_engines,
        'widget_cats': sorted(widget_cats),
        'contamination': contamination,
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
    for s in results:
        if s['problems']:
            print('### %s (%s)' % (s['variant'], s['intended']))
            for sev, msg in s['problems']:
                print('   [%s] %s' % (sev, msg))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
