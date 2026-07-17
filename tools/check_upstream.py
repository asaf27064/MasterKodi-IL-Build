#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Watch upstream for each overlaid addon and classify updates safe vs manual.

For every overlays/<id>/base.json we:
  1. find the latest upstream version (gears -> addons.xml; AF3 -> GitHub releases)
  2. if it's newer than base_version, download BOTH the current base and the new
     base, and look only at the files our overlay actually *replaces* (the ones
     that also exist in clean upstream -- our NEW files can't conflict):
       * if none of those changed between old and new upstream -> SAFE
         (the same overlay applies cleanly; just bump base_version)
       * if any changed or vanished upstream -> MANUAL
         (a human must re-merge those specific files into the overlay)

This is the same baseline-diff idea the old pov-modified-heb gears watcher used,
now unified here so both Hebrew addons are watched from the one repo that owns
the overlays.

Outputs a human summary to stdout and, if $GITHUB_OUTPUT is set, machine flags:
  has_update=true|false
  has_manual=true|false
  summary_file=<path to a markdown summary for the issue body>
"""

import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import EXCLUDE_DIRS, EXCLUDE_NAMES, EXCLUDE_EXTS  # noqa: E402

try:
    import requests
except ImportError:
    requests = None


def _get(url, is_json=False, headers=None):
    if requests:
        r = requests.get(url, timeout=60, headers=headers or {})
        r.raise_for_status()
        return r.json() if is_json else r.content
    import urllib.request
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    return json.loads(data) if is_json else data


def _semver(v):
    return tuple(int(x) for x in re.findall(r'\d+', v)[:4] or [0])


def _latest_gears(base):
    xml = _get(base['upstream_addons_xml']).decode('utf-8', 'replace')
    m = re.search(r'id="%s"\s+[^>]*version="([^"]+)"' % re.escape(base['addon_id']), xml)
    if not m:
        m = re.search(r'version="([^"]+)"\s+[^>]*id="%s"' % re.escape(base['addon_id']), xml)
    return m.group(1) if m else None


def _latest_af3(base):
    data = _get(base['upstream_releases_api'], is_json=True,
                headers={'Accept': 'application/vnd.github+json'})
    tag = data.get('tag_name', '')
    return tag.lstrip('v') or None


def _latest_version(base):
    if base.get('upstream_addons_xml'):
        return _latest_gears(base)
    if base.get('upstream_releases_api'):
        return _latest_af3(base)
    return None


def _skip(name):
    return name in EXCLUDE_NAMES or os.path.splitext(name)[1] in EXCLUDE_EXTS


def _clean_base_map(zip_bytes, base, version):
    """Map rel-path -> bytes for clean upstream at `version` (git junk stripped,
    top folder de-versioned)."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    names = [n for n in zf.namelist() if not n.endswith('/')]
    top = None
    if base.get('raw_top_folder'):
        top = base['raw_top_folder'].format(version=version).rstrip('/') + '/'
    else:
        tops = set(n.split('/')[0] for n in names)
        if len(tops) == 1:
            top = next(iter(tops)) + '/'
    out = {}
    for n in names:
        rel = n[len(top):] if (top and n.startswith(top)) else (None if top else n)
        if rel is None:
            continue
        parts = rel.split('/')
        if any(p in EXCLUDE_DIRS for p in parts[:-1]) or _skip(parts[-1]):
            continue
        out[rel] = zf.read(n)
    return out


def _overlay_replaced_files(overlay_dir):
    """rel-paths our overlay ships (relative to addon root)."""
    root = os.path.join(overlay_dir, 'files')
    out = []
    for r, d, fs in os.walk(root):
        d[:] = [x for x in d if x not in EXCLUDE_DIRS]
        for f in fs:
            if _skip(f):
                continue
            out.append(os.path.relpath(os.path.join(r, f), root).replace(os.sep, '/'))
    return out


def check_one(overlay_dir, target=None):
    """Compare the overlay's current base against upstream `target` (default:
    the auto-detected upstream latest) and classify the update SAFE/MANUAL."""
    base = json.load(open(os.path.join(overlay_dir, 'base.json'), encoding='utf-8'))
    aid = base['addon_id']
    # Overlays with no watchable upstream: local_committed (Nimbus - the base is
    # our own committed tree) and kodi_bundled (Estuary - the base ships inside
    # Kodi itself, refreshed manually with each Kodi version we adopt).
    if base.get('base_type') in ('local_committed', 'kodi_bundled'):
        return {'addon_id': aid, 'current': base.get('overlay_version', '?'),
                'latest': None, 'has_update': False, 'manual': [], 'safe': None,
                'skipped': 'no watchable upstream (%s)' % base['base_type']}
    # For most upstreams the release tag == the addon version (AF3, gears). For
    # skins where the release TAG differs from the bundled addon version (e.g.
    # Zephyr: tag v1.1.9 bundles the Omega 1.0.51 addon), compare the latest tag
    # against the tracked tag in `upstream_tag` -- otherwise base_version (1.0.51)
    # vs tag (1.1.9) always looks like an update and fires forever.
    cur = base.get('upstream_tag') or base['base_version']
    latest = target or _latest_version(base)
    res = {'addon_id': aid, 'current': cur, 'latest': latest,
           'has_update': False, 'manual': [], 'safe': None}
    if not latest:
        res['error'] = 'could not determine upstream latest'
        return res
    if _semver(latest) <= _semver(cur):
        return res
    res['has_update'] = True

    # Download both bases and diff only the files our overlay replaces.
    # Old base: prefer the committed local base (upstream may have deleted it);
    # new base: the upstream template (which serves the latest).
    up_fmt = base.get('upstream_zip_url') or base['base_zip_url']
    local = base.get('base_zip_local')
    if local:
        lp = os.path.join(overlay_dir, local)
        old_bytes = open(lp, 'rb').read() if os.path.isfile(lp) else _get(base['base_zip_url'].format(version=cur))
    else:
        old_bytes = _get(base['base_zip_url'].format(version=cur))
    old_map = _clean_base_map(old_bytes, base, cur)
    new_map = _clean_base_map(_get(up_fmt.format(version=latest)), base, latest)

    replaced = _overlay_replaced_files(overlay_dir)
    for rel in sorted(replaced):
        if rel not in old_map and rel not in new_map:
            continue  # a purely-new overlay file (e.g. kodirdil/, fonts/)
        o = old_map.get(rel)
        n = new_map.get(rel)
        if n is None:
            res['manual'].append((rel, 'removed upstream'))
        elif o is None:
            # upstream ADDED a file we also ship -> our version wins, but flag it
            res['manual'].append((rel, 'newly added upstream (overlay overrides)'))
        elif o != n:
            res['manual'].append((rel, 'changed upstream'))
    res['safe'] = (len(res['manual']) == 0)
    return res


# --------------------------------------------------------------------------- #
# plain-dep watch (alert-only)
# --------------------------------------------------------------------------- #
# Deps we ship in addons/ that have NO overlay/base.json, so the main watcher
# never sees them age. Alert-only: adoption is always a human decision (bump
# the tree in addons/, CI reships via the manifest). Sources are per-Kodi-branch
# where upstream offers one (jurialmunkey omega endpoint) so we never alert on
# versions meant for a newer Kodi.
_JURIAL_OMEGA = 'https://raw.githubusercontent.com/jurialmunkey/repository.jurialmunkey/master/omega/zips/addons.xml'
DEPS_WATCH = [
    # skin.nimbus is our committed FORK (no base.json) -> the overlay watcher
    # never sees ivarbrandt move; he publishes no releases, so watch master.
    ('skin.nimbus',
     'https://raw.githubusercontent.com/ivarbrandt/skin.nimbus/master/addon.xml', 'github_addon_xml'),
    ('plugin.video.themoviedb.helper', _JURIAL_OMEGA, 'addons_xml'),
    ('script.skinvariables', _JURIAL_OMEGA, 'addons_xml'),
    ('script.module.jurialmunkey', _JURIAL_OMEGA, 'addons_xml'),
    ('script.module.infotagger', _JURIAL_OMEGA, 'addons_xml'),
    ('script.skinshortcuts', 'https://mirrors.kodi.tv/addons/omega/script.skinshortcuts/', 'kodi_dir'),
    ('script.module.cocoscrapers',
     'https://raw.githubusercontent.com/not-coco-joe/repository.cocoscrapers/master/zips/addons.xml', 'addons_xml'),
    ('script.module.gearsscrapers',
     'https://raw.githubusercontent.com/unhingedthemes/zips/main/_zips/addons.xml', 'addons_xml'),
]


def _dep_latest(aid, url, kind):
    data = _get(url).decode('utf-8', 'replace')
    if kind == 'addons_xml':
        m = re.search(r'id="%s"[^>]*version="([^"]+)"' % re.escape(aid), data)
        if not m:
            m = re.search(r'version="([^"]+)"[^>]*id="%s"' % re.escape(aid), data)
        return m.group(1) if m else None
    if kind == 'github_addon_xml':
        m = re.search(r'<addon[^>]*?version="([0-9][0-9.]*)"', data, re.S)
        return m.group(1) if m else None
    if kind == 'kodi_dir':
        vers = re.findall(r'%s-([0-9][0-9.]*)\.zip' % re.escape(aid), data)
        return max(vers, key=_semver) if vers else None
    return None


def _dep_current(addons_root, aid):
    import xml.etree.ElementTree as ET
    try:
        return ET.parse(os.path.join(addons_root, aid, 'addon.xml')).getroot().get('version')
    except Exception:
        return None


def check_deps(addons_root='addons'):
    lines = ['# MasterKodi IL - dep watch (informational)', '']
    has_update = False
    for aid, url, kind in DEPS_WATCH:
        cur = _dep_current(addons_root, aid)
        if not cur:
            lines.append('- **%s**: not in addons/ (skipped)' % aid)
            continue
        try:
            latest = _dep_latest(aid, url, kind)
        except Exception as e:
            lines.append('- **%s**: error - %s' % (aid, e))
            continue
        if not latest:
            lines.append('- **%s**: could not determine upstream latest' % aid)
        elif _semver(latest) > _semver(cur):
            has_update = True
            lines.append('- **%s**: %s -> **%s available**' % (aid, cur, latest))
        else:
            lines.append('- **%s**: up to date (%s)' % (aid, cur))
    summary = '\n'.join(lines)
    print(summary)
    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        sf = os.path.join(os.getcwd(), 'upstream_summary_deps.md')
        with open(sf, 'w', encoding='utf-8') as fh:
            fh.write(summary + '\n')
        with open(out, 'a', encoding='utf-8') as fh:
            fh.write('has_update=%s\n' % ('true' if has_update else 'false'))
            fh.write('summary_file=%s\n' % sf)
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--deps':
        return check_deps()
    overlays_dir = sys.argv[1] if len(sys.argv) > 1 else 'overlays'
    results = []
    for name in sorted(os.listdir(overlays_dir)):
        odir = os.path.join(overlays_dir, name)
        if os.path.isfile(os.path.join(odir, 'base.json')):
            try:
                results.append(check_one(odir))
            except Exception as e:
                results.append({'addon_id': name, 'error': str(e), 'has_update': False, 'manual': []})

    lines = ['# MasterKodi IL - upstream watch', '']
    has_update = has_manual = False
    for r in results:
        if r.get('error'):
            lines.append('- **%s**: error - %s' % (r['addon_id'], r['error']))
            continue
        if not r['has_update']:
            lines.append('- **%s**: up to date (%s)' % (r['addon_id'], r['current']))
            continue
        has_update = True
        if r['safe']:
            lines.append('- **%s**: %s -> %s **SAFE** (bump `base_version`, no overlaid file changed)'
                         % (r['addon_id'], r['current'], r['latest']))
        else:
            has_manual = True
            lines.append('- **%s**: %s -> %s **MANUAL re-merge**:' % (r['addon_id'], r['current'], r['latest']))
            for rel, why in r['manual']:
                lines.append('    - `%s` - %s' % (rel, why))
    summary = '\n'.join(lines)
    print(summary)

    out = os.environ.get('GITHUB_OUTPUT')
    if out:
        # dir-specific name: the workflow runs this once per overlays dir
        # (overlays, overlays-piers) and each issue step reads its own file
        sf = os.path.join(os.getcwd(), 'upstream_summary_%s.md'
                          % os.path.basename(os.path.normpath(overlays_dir)))
        with open(sf, 'w', encoding='utf-8') as fh:
            fh.write(summary + '\n')
        with open(out, 'a', encoding='utf-8') as fh:
            fh.write('has_update=%s\n' % ('true' if has_update else 'false'))
            fh.write('has_manual=%s\n' % ('true' if has_manual else 'false'))
            fh.write('summary_file=%s\n' % sf)


if __name__ == '__main__':
    main()
