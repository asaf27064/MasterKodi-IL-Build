#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate index.json for every config-variant.

content_source.py used to carry HARD-CODED lists of the files each skin's apply
should fetch. Those lists drifted from what the variants actually ship and
silently lost files -- the wizard cannot list a directory over raw.githubusercontent,
so a missing name simply never got fetched and nobody noticed. Real damage:
  * zephyr-pov-tmdb ships 7 skinshortcuts files, the list named 5 -> the
    MAINTENANCE menu (thzvkh-1.DATA.xml) was never delivered and rendered empty.
  * the Kodi-21 zephyr branch never fetched skin-overrides/ at all -> the power
    menu kept calling plugin.video.gears/?mode=clear_all_cache on a POV box.

The index makes the variant itself the source of truth: whatever is committed in
the variant directory is what gets applied. Adding a file to a variant is now
enough -- no wizard change needed.

Destination placeholders resolved by the wizard at apply time:
  {addons} {addon_data} {userdata} {home} {skin}

Usage:  python tools/gen_variant_index.py [--check]
"""
import argparse
import json
import os
import sys

ROOTS = ('config-variants', 'config-variants-piers')

# subdir -> destination template, per skin family. '' = variant root.
def dest_for(variant, skin_id, rel):
    top = rel.split('/')[0]
    name = rel[len(top) + 1:] if '/' in rel else rel
    zephyr_piers = 'piers' in variant and 'zephyr' in variant

    if rel == 'favourites.xml':
        return '{userdata}/favourites.xml'
    if top == 'skinshortcuts':
        return '{addon_data}/script.skinshortcuts/' + name
    if top == 'themoviedb':
        return '{addon_data}/plugin.video.themoviedb.helper/' + name
    if top == 'nodes':
        return '{addon_data}/script.skinvariables/nodes/{skin}/' + name
    if top == 'skinvariables':
        return '{addon_data}/script.skinvariables/' + name
    if top == 'media':
        # NOT indexed: config_policy already ships media/genre_icons and
        # media/network_icons as content-neutral dirs inside the config zip, for
        # BOTH content sources. Indexing them would make the switcher fetch ~103
        # PNGs one HTTP request at a time to reproduce what the config bundle
        # already delivered in one download.
        return None
    if top.startswith('skin.'):                      # e.g. skin.zephyr/settings.xml
        return '{addon_data}/{skin}/' + name
    if top == 'skin-overrides':
        if zephyr_piers:
            return '{addons}/{skin}/shortcuts/' + name
        if 'af3' in variant or 'zephyr' in variant:
            return '{addons}/{skin}/1080i/' + name
        return '{addons}/{skin}/xml/' + name         # estuary / nimbus
    return None                                      # pov/, nimbus/ = special-cased


# Piers variants that REPLACE the base rather than overlay it. zephyr-piers-pov
# drives the skin's own shortcuts/menus.xml system, while the Omega zephyr
# variant drives script.skinshortcuts + 1080i/ overrides -- inheriting the base
# there would write Omega-era skin XML into the (different) Piers Zephyr skin.
# Everything else overlays, so the base still supplies what piers doesn't
# restate (e.g. Estuary's favourites.xml).
SELF_CONTAINED = {'zephyr-piers-pov'}

SKIN_OF = {
    'estuary': 'skin.estuary',
    'nimbus': 'skin.nimbus',
    'af3': 'skin.arctic.fuse.3',
    'zephyr': 'skin.arctic.zephyr.2.resurrection.mod',
}


def skin_for(variant):
    for k, v in SKIN_OF.items():
        if variant.startswith(k):
            return v
    return None


def build(repo_root, check=False):
    changed = []
    for root in ROOTS:
        base = os.path.join(repo_root, root)
        if not os.path.isdir(base):
            continue
        for variant in sorted(os.listdir(base)):
            vdir = os.path.join(base, variant)
            if not os.path.isdir(vdir):
                continue
            skin = skin_for(variant)
            files, special = [], []
            for dp, _dirs, fs in os.walk(vdir):
                for f in sorted(fs):
                    if f in ('index.json', 'README.md') or f.startswith('.'):
                        continue
                    rel = os.path.relpath(os.path.join(dp, f), vdir).replace(os.sep, '/')
                    top = rel.split('/')[0]
                    if top == 'media':
                        continue                     # delivered by config_policy
                    if top in ('pov', 'nimbus'):
                        special.append(rel)          # handled by dedicated seeders
                        continue
                    d = dest_for(variant, skin, rel)
                    if d is None:
                        special.append(rel)
                        continue
                    files.append({'src': rel, 'dest': d})
            index = {'variant': variant, 'skin': skin,
                     'inherit': variant not in SELF_CONTAINED,
                     'files': files, 'special': sorted(special)}
            path = os.path.join(vdir, 'index.json')
            new = json.dumps(index, ensure_ascii=False, indent=2) + '\n'
            old = None
            if os.path.isfile(path):
                with open(path, encoding='utf-8') as fh:
                    old = fh.read()
            if old != new:
                changed.append('%s/%s' % (root, variant))
                if not check:
                    with open(path, 'w', encoding='utf-8', newline='\n') as fh:
                        fh.write(new)
            print('  %-46s %2d file(s) + %d special' % (
                '%s/%s' % (root, variant), len(files), len(special)))
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo-root', default='.')
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args()
    print('[gen_variant_index]')
    changed = build(args.repo_root, args.check)
    if args.check and changed:
        print('[gen_variant_index] STALE: %s' % ', '.join(changed))
        return 1
    print('[gen_variant_index] %d index file(s) %s' % (
        len(changed), 'would change' if args.check else 'written'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
