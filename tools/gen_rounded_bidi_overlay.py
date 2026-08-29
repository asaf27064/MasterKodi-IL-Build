#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regenerate the Rounded overlay's RTL separator fix from clean upstream.

THE BUG
-------
The skin separates label segments with `  •  ` (plain spaces around a bullet),
e.g. `$VAR[Label_Year]$VAR[Label_Genre,  •  ,]$VAR[Label_Duration,  •  ,]`.
Spaces and the bullet are bidi-NEUTRAL. Per UAX#9, neutrals sitting between a
European Number and a right-to-left run resolve to R, so on a Hebrew build the
separator is absorbed into the Hebrew run and re-ordered to its far end:

    before   2026פשע / אנימציה / הרפתקאות / אקשן  •   •  27m
    after    2026  •  פשע / אנימציה / הרפתקאות / אקשן  •  27m

The year fused with the genre, and both separators piled up together.

THE FIX
-------
    U+200E LRM  NBSP NBSP  •  NBSP NBSP  U+200E LRM

LRM is a strong LEFT-TO-RIGHT character, so the neutral run binds to the LTR
paragraph and cannot be pulled into the Hebrew run. NBSP additionally stops the
gap being trimmed. Visual width is unchanged.

An earlier attempt used RLM, which is a strong RIGHT-to-left character: that
forces exactly the absorption we are trying to prevent, and broke the Latin
case too. If this ever regresses, check the mark is U+200E and not U+200F.

WHY GENERATED
-------------
466 sites across 16 files. Hand-carrying those as overlay files would mean
re-merging 16 large upstream files by hand on every skin update. The edit is a
single mechanical substitution, so instead we regenerate it: this tool takes
the CLEAN upstream file (or the existing overlay file, where we have other
edits in it - Home.xml carries the buildxml fix and the font pin) and applies
the substitution. On a skin update: bump base_version, re-run this, done.

    python tools/gen_rounded_bidi_overlay.py            # regenerate
    python tools/gen_rounded_bidi_overlay.py --check    # verify, touch nothing
"""
import io
import os
import sys
import zipfile

SKIN = 'skin.arctic.zephyr.rounded'
OV = os.path.join('overlays', SKIN)
FILES = os.path.join(OV, 'files')

OLD = '  •  '                                    # SP SP bullet SP SP
NEW = '‎  •  ‎'    # LRM NBSP NBSP • NBSP NBSP LRM


def base_zip():
    import json
    base = json.load(io.open(os.path.join(OV, 'base.json'), encoding='utf-8'))
    local = base.get('base_zip_local')
    if local:
        p = os.path.join(OV, local)
        if os.path.isfile(p):
            return zipfile.ZipFile(p)
    raise SystemExit(
        'clean base zip not found. It is gitignored (73 MB); fetch it with\n'
        '  curl -L -o %s %s'
        % (os.path.join(OV, local or 'base/<id>-<ver>.zip'),
           base['base_zip_url'].format(version=base['base_version'])))


def main():
    check = '--check' in sys.argv
    z = base_zip()
    names = [n for n in z.namelist()
             if n.startswith('%s/1080i/' % SKIN) and n.lower().endswith('.xml')]

    changed = total = 0
    problems = []
    for n in sorted(names):
        rel = n[len('%s/' % SKIN):]                      # 1080i/Foo.xml
        upstream = z.read(n).decode('utf-8')
        if OLD not in upstream:
            continue
        # keep any OTHER edits we already carry in this file
        ours = os.path.join(FILES, rel.replace('/', os.sep))
        src = (io.open(ours, encoding='utf-8').read()
               if os.path.isfile(ours) else upstream)
        out = src.replace(OLD, NEW)
        sites = src.count(OLD)
        total += sites
        changed += 1
        if check:
            if not os.path.isfile(ours) or io.open(ours, encoding='utf-8').read() != out:
                problems.append(rel)
            continue
        d = os.path.dirname(ours)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        io.open(ours, 'wb').write(out.encode('utf-8'))

    if check:
        print('[bidi] %d file(s), %d site(s); out of date: %s'
              % (changed, total, problems or 'none'))
        return 1 if problems else 0

    print('[bidi] regenerated %d file(s), %d separator site(s)' % (changed, total))
    print('[bidi] separator: %s' % ' '.join('U+%04X' % ord(c) for c in NEW))

    from xml.dom import minidom
    bad = 0
    for d, _s, fs in os.walk(FILES):
        for f in fs:
            if f.lower().endswith('.xml'):
                try:
                    minidom.parse(os.path.join(d, f))
                except Exception as e:
                    print('[bidi] XML FAIL %s: %s' % (f, e))
                    bad += 1
    print('[bidi] XML parse failures: %d' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
