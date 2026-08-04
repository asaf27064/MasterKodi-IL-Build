# -*- coding: utf-8 -*-
"""Validate every shipped TMDb Helper widget against tmdbhelper's own routes.

Asaf saw a TMDb error on the Shield 2026-08-04. The cause was a widget asking
for `info=upcoming&tmdb_type=tv` -- but tmdbhelper declares that route as
`types = ('movie', )`, because TMDb has /movie/upcoming and NO /tv/upcoming.
Every load of that widget failed.

Rather than grep for that one pairing, this reads tmdbhelper's directory
classes (each declares `params = {'info': ...}` plus a `types` tuple) and
checks EVERY info/tmdb_type pair we ship in config/, config-variants/ and
config-variants-piers/ against them. Covers all four skins and both engines.

Exit 1 on any mismatch, so CI catches a bad widget before a device does.
"""

import ast
import glob
import html
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASEDIR = os.path.join(
    REPO, 'addons', 'plugin.video.themoviedb.helper', 'resources',
    'tmdbhelper', 'lib', 'items', 'directories', 'base')
SCAN_ROOTS = ('config', 'config-variants', 'config-variants-piers')

URL_RE = re.compile(r'plugin://plugin\.video\.themoviedb\.helper/[^"\'<>\s]*')
INFO_RE = re.compile(r'[?&]info=([a-zA-Z0-9_]+)')
TYPE_RE = re.compile(r'[?&]tmdb_type=([a-zA-Z]+)')


def _const(node):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


def declared_routes():
    """info name -> set of tmdb_type values tmdbhelper accepts for it.

    A class may inherit `types` from another class in the same package
    (e.g. BaseDirItemTMDbAllNetworks(BaseDirItemTMDbAllStudios)), so resolve
    base classes after the first pass.
    """
    by_class = {}          # class name -> (info, types or None, base names)
    for path in sorted(glob.glob(os.path.join(BASEDIR, '*.py'))):
        tree = ast.parse(io.open(path, encoding='utf-8').read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            info = types = None
            for stmt in node.body:
                if not isinstance(stmt, ast.Assign) or len(stmt.targets) != 1:
                    continue
                target = stmt.targets[0]
                if not isinstance(target, ast.Name):
                    continue
                if target.id == 'types':
                    value = _const(stmt.value)
                    if value is not None:
                        types = set(value)
                elif target.id == 'params':
                    value = _const(stmt.value)
                    if isinstance(value, dict):
                        info = value.get('info')
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            by_class[node.name] = (info, types, bases)

    def resolve(name, depth=0):
        if depth > 8 or name not in by_class:
            return None
        types = by_class[name][1]
        if types is not None:
            return types
        for base in by_class[name][2]:
            found = resolve(base, depth + 1)
            if found is not None:
                return found
        return None

    routes = {}
    for name, (info, _types, _bases) in by_class.items():
        if not info:
            continue
        types = resolve(name)
        if types:
            routes.setdefault(info, set()).update(types)
    return routes


def known_routes():
    """Every info name tmdbhelper can route at all.

    consts.py ROUTE_NOID / ROUTE_TMDBID are the real registry; the basedir
    classes only cover routes that also appear as browsable menu items, so
    a valid route like `discover` exists here and nowhere else.
    """
    path = os.path.join(
        REPO, 'addons', 'plugin.video.themoviedb.helper', 'resources',
        'tmdbhelper', 'lib', 'addon', 'consts.py')
    tree = ast.parse(io.open(path, encoding='utf-8').read())
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        if not target.id.startswith('ROUTE_'):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key in node.value.keys:
            value = _const(key)
            if isinstance(value, str):
                names.add(value)
    return names


def shipped_widgets():
    """(relpath, info, tmdb_type) for every widget url we ship."""
    for root in SCAN_ROOTS:
        pattern = os.path.join(REPO, root, '**', '*')
        for path in glob.glob(pattern, recursive=True):
            if not os.path.isfile(path):
                continue
            try:
                text = io.open(path, encoding='utf-8', errors='replace').read()
            except Exception:
                continue
            if 'themoviedb.helper' not in text:
                continue
            rel = os.path.relpath(path, REPO).replace('\\', '/')
            for url in URL_RE.findall(text):
                url = html.unescape(url)
                info = INFO_RE.search(url)
                if not info:
                    continue
                tmdb_type = TYPE_RE.search(url)
                yield rel, info.group(1), tmdb_type.group(1) if tmdb_type else None


def main():
    routes = declared_routes()
    known = known_routes()
    if len(routes) < 20 or len(known) < 20:
        print('ERROR: only %d routes parsed from tmdbhelper -- parser is '
              'out of step with the addon, refusing to pass' % len(routes))
        return 1

    checked = 0
    bad = set()
    for rel, info, tmdb_type in shipped_widgets():
        checked += 1
        allowed = routes.get(info)
        if allowed is None:
            # dir_* are static menu directories, not typed list routes; and a
            # route present in consts but with no basedir class (e.g.
            # `discover`) is valid and simply declares no type constraint
            if info.startswith('dir_') or info in known:
                continue
            bad.add((rel, info, tmdb_type or '-', 'no such info route'))
        elif None in allowed:
            # route declares an untyped entry -- it does not constrain tmdb_type
            continue
        elif tmdb_type and tmdb_type not in allowed:
            bad.add((rel, info, tmdb_type,
                     'valid types: %s' % ', '.join(sorted(str(t) for t in allowed))))

    print('tmdbhelper routes parsed : %d' % len(routes))
    print('shipped widget urls      : %d' % checked)
    if not bad:
        print('\nOK - every shipped widget uses a tmdb_type the route supports')
        return 0

    print('\n%d INVALID widget(s):' % len(bad))
    for rel, info, tmdb_type, why in sorted(bad):
        print('  %s\n      info=%s  tmdb_type=%s  (%s)' % (rel, info, tmdb_type, why))
    return 1


if __name__ == '__main__':
    sys.exit(main())
