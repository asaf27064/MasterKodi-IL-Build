# -*- coding: utf-8 -*-
"""Every setting our config writes must still exist in the shipped addon.

This is the check that catches a SILENT upstream rename -- the failure mode a
3-way merge cannot see. POV 6.08.03 renamed `sort.watchlist` to
`sort.watchlist_movies` + `sort.watchlist_shows`: the merge had zero conflicts,
yet six of our config-variants were left writing a setting the addon no longer
declares, which would simply be ignored on every install.

Covers both engines:
  * POV   -- config-variants/<v>/pov/settings.xml   vs the addon's settings.xml
  * Gears -- the `gears_settings` block in config/config_policy.json  vs the
             addon's settings_cache.py registry

Unknown ids in `gears_settings_exclude` are reported as INFO, not failure: that
list means "never overwrite this id", so naming an id that does not exist is
inert belt-and-braces, not a bug.

Usage:  python tools/check_setting_ids.py        (exit 1 on any unknown id)
"""

import glob
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pov_declared():
    p = os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources', 'settings.xml')
    if not os.path.exists(p):
        return None
    return set(re.findall(r'<setting[^>]*\bid="([^"]+)"',
                          io.open(p, encoding='utf-8').read()))


def _gears_declared():
    p = os.path.join(REPO, 'addons', 'plugin.video.gears', 'resources', 'lib',
                     'caches', 'settings_cache.py')
    if not os.path.exists(p):
        return None
    return set(re.findall(r"'setting_id':\s*'([^']+)'",
                          io.open(p, encoding='utf-8').read()))


# POV builds some ids at runtime from a service name, so they are never
# declared in settings.xml -- e.g. modules/settings.py does
#   get_setting('store_torrent.%s' % debrid_service.lower())
DYNAMIC_POV = (re.compile(r'^store_torrent\.'), re.compile(r'^store_usenet\.'))


def main():
    pov, gears = _pov_declared(), _gears_declared()
    print('addon declares: pov=%s  gears=%s'
          % (len(pov) if pov else 'n/a', len(gears) if gears else 'n/a'))

    unknown = []
    info = []

    # --- POV: shipped settings.xml per variant
    checked = files = 0
    if pov:
        for path in glob.glob(os.path.join(REPO, 'config*', '**', 'settings.xml'),
                              recursive=True):
            rel = os.path.relpath(path, REPO).replace(os.sep, '/')
            if 'pov' not in rel.split('/'):
                continue
            files += 1
            for sid in re.findall(r'<setting[^>]*\bid="([^"]+)"',
                                  io.open(path, encoding='utf-8').read()):
                checked += 1
                if sid in pov or any(rx.match(sid) for rx in DYNAMIC_POV):
                    continue
                unknown.append(('pov', sid, rel))
    print('pov   : %d file(s), %d id(s) checked' % (files, checked))

    # --- Gears: the enforced-settings block in the config policy
    n_enforced = 0
    if gears:
        pol_path = os.path.join(REPO, 'config', 'config_policy.json')
        pol = json.load(io.open(pol_path, encoding='utf-8'))
        enforced = pol.get('gears_settings') or {}
        n_enforced = len(enforced)
        for sid in enforced:
            if sid not in gears:
                unknown.append(('gears', sid, 'config/config_policy.json:gears_settings'))
        for sid in (pol.get('gears_settings_exclude') or []):
            if sid not in gears:
                info.append(sid)
    print('gears : %d enforced setting(s) checked' % n_enforced)

    if info:
        print('\nINFO - ids in gears_settings_exclude that the addon does not declare '
              '(inert: the list only says "never overwrite"):')
        for sid in sorted(set(info)):
            print('   %s' % sid)

    if not unknown:
        print('\nOK - every setting our config writes exists in the shipped addon')
        return 0

    print('\n%d reference(s) to settings the addon does NOT declare:' % len(unknown))
    seen = set()
    for engine, sid, where in unknown:
        if (engine, sid) in seen:
            continue
        seen.add((engine, sid))
        n = sum(1 for e, s, _ in unknown if e == engine and s == sid)
        print('   [%-5s] %-34s in %d place(s), e.g. %s' % (engine, sid, n, where))
    print('\nEither upstream renamed/removed it (update our config) or it is dead '
          'config to delete.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
