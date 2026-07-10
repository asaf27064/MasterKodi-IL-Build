#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI builder for the MasterKodi IL Windows installer (.exe).

Assembles: base Kodi (KodiFiles) + bootstrap portable_data (wizard/firstrun/repo
injected from the freshly built addon zips) -> package.7z -> Inno Setup -> EXE.

Runs on windows-latest. All heavy inputs (KodiFiles, portable_data template) are
downloaded from our build-inputs release by the workflow; this script only
assembles and compiles.
"""

import argparse
import os
import re
import shutil
import subprocess
import sqlite3
import sys
import tempfile
import zipfile

WIZARD_ID = 'plugin.program.masterkodi.il.wizard'
FIRSTRUN_ID = 'service.kodi.il.firstrun'
REPO_ID = 'repository.masterkodi.il'


def log(m): print('  %s' % m, flush=True)


def extract_addon_from_zip(zp, dest):
    with zipfile.ZipFile(zp) as zf:
        zf.extractall(dest)
    for i in os.listdir(dest):
        p = os.path.join(dest, i)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, 'addon.xml')):
            return p
    return dest if os.path.exists(os.path.join(dest, 'addon.xml')) else None


def addon_version(xml_dir):
    try:
        with open(os.path.join(xml_dir, 'addon.xml'), encoding='utf-8') as f:
            m = re.search(r'<addon[^>]+version="([^"]+)"', f.read())
        return m.group(1) if m else None
    except Exception:
        return None


def fix_addons_db(db):
    if not os.path.exists(db):
        log('Addons33.db not found (skipping DB fix): %s' % db)
        return False
    conn = sqlite3.connect(db)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO installed (addonID,enabled,installDate,origin,disabledReason) VALUES (?,1,datetime("now"),?,0)', (WIZARD_ID, REPO_ID))
    c.execute('INSERT OR REPLACE INTO installed (addonID,enabled,installDate,origin,disabledReason) VALUES (?,1,datetime("now"),?,0)', (REPO_ID, REPO_ID))
    c.execute('INSERT OR REPLACE INTO installed (addonID,enabled,installDate,origin,disabledReason) VALUES (?,1,datetime("now"),"",0)', (FIRSTRUN_ID,))
    row = c.execute('SELECT id FROM repo WHERE addonID=?', (REPO_ID,)).fetchone()
    if row:
        rid = row[0]
    else:
        c.execute('INSERT INTO repo (addonID,checksum,lastcheck,version,nextcheck) VALUES (?,"","2000-01-01","1.0.0","2000-01-01")', (REPO_ID,))
        rid = c.lastrowid
    row = c.execute('SELECT id FROM addons WHERE addonID=?', (WIZARD_ID,)).fetchone()
    if row:
        aid = row[0]
    else:
        c.execute('INSERT INTO addons (addonID,version,name,summary,news,description,metadata) VALUES (?,"1.0.0","MasterKodi IL Wizard","","","","")', (WIZARD_ID,))
        aid = c.lastrowid
    c.execute('INSERT OR IGNORE INTO addonlinkrepo (idRepo,idAddon) VALUES (?,?)', (rid, aid))
    conn.commit()
    conn.close()
    log('DB fixed')
    return True


def find_zip(dist, prefix):
    for f in os.listdir(dist):
        if f.startswith(prefix) and f.endswith('.zip'):
            return os.path.join(dist, f)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--kodifiles', required=True, help='extracted KodiFiles dir')
    ap.add_argument('--portable', required=True, help='bootstrap portable_data dir')
    ap.add_argument('--dist', required=True, help='dir with built addon zips')
    ap.add_argument('--assets', required=True, help='installers/windows dir (iss, ico, bmp, splash)')
    ap.add_argument('--iscc', required=True, help='path to ISCC.exe')
    ap.add_argument('--sevenzip', default='7z', help='7z executable')
    args = ap.parse_args()

    # normalise to absolute paths - several steps run with cwd=KodiFiles, so any
    # relative path (esp. package.7z) would otherwise resolve against the wrong dir
    args.kodifiles = os.path.abspath(args.kodifiles)
    args.portable = os.path.abspath(args.portable)
    args.dist = os.path.abspath(args.dist)
    args.assets = os.path.abspath(args.assets)

    addons = os.path.join(args.portable, 'addons')
    os.makedirs(addons, exist_ok=True)

    # 1) inject freshly built wizard/firstrun/repo into the bootstrap portable_data
    log('[1] Injecting bootstrap addons...')
    wiz_ver = None
    for prefix, aid in [(WIZARD_ID, WIZARD_ID), (FIRSTRUN_ID, FIRSTRUN_ID), (REPO_ID, REPO_ID)]:
        zp = find_zip(args.dist, prefix)
        if not zp:
            log('  WARN: no zip for %s' % prefix)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            src = extract_addon_from_zip(zp, tmp)
            tgt = os.path.join(addons, aid)
            if os.path.exists(tgt):
                shutil.rmtree(tgt)
            shutil.copytree(src, tgt)
            v = addon_version(tgt)
            if aid == WIZARD_ID:
                wiz_ver = v
            log('  %s -> %s' % (aid, v))
    if not wiz_ver:
        log('ERROR: wizard not injected'); return 2

    # 2) fix Addons33.db
    log('[2] Fixing Addons33.db...')
    fix_addons_db(os.path.join(args.portable, 'userdata', 'Database', 'Addons33.db'))

    # 3) ISS AppVersion -> wizard version
    log('[3] ISS AppVersion -> %s' % wiz_ver)
    iss = os.path.join(args.assets, 'MasterKodiIL.iss')
    with open(iss, encoding='utf-8') as f:
        ic = f.read()
    ic = re.sub(r'AppVersion=.+', 'AppVersion=%s' % wiz_ver, ic, count=1)
    with open(iss, 'w', encoding='utf-8') as f:
        f.write(ic)

    # 4) splash + portable_data -> KodiFiles
    log('[4] Staging portable_data into KodiFiles...')
    splash = os.path.join(args.assets, 'splash.png')
    media = os.path.join(args.kodifiles, 'media')
    if os.path.exists(splash) and os.path.isdir(media):
        shutil.copy2(splash, os.path.join(media, 'splash.png'))
    pd = os.path.join(args.kodifiles, 'portable_data')
    if os.path.exists(pd):
        shutil.rmtree(pd)
    shutil.copytree(args.portable, pd)

    # 5) package.7z from KodiFiles
    log('[5] Creating package.7z...')
    pkg = os.path.join(args.assets, 'package.7z')
    if os.path.exists(pkg):
        os.remove(pkg)
    r = subprocess.run([args.sevenzip, 'a', '-t7z', '-mx=5', pkg, '.\\*'],
                       cwd=args.kodifiles, capture_output=True, text=True)
    if r.returncode != 0:
        log('7z failed: %s' % (r.stderr or r.stdout)); return 3
    log('  package.7z %.1f MB' % (os.path.getsize(pkg) / 1048576))

    # 6) Inno Setup compile
    log('[6] Inno Setup compile...')
    r = subprocess.run([args.iscc, iss], cwd=args.assets, capture_output=True, text=True)
    if r.returncode != 0:
        log('ISCC failed:\n%s' % (r.stdout + r.stderr)); return 4
    out = os.path.join(args.assets, 'Output', 'MasterKodiIL_Setup.exe')
    if not os.path.exists(out):
        # some ISS emit next to the iss; search
        for root, _, files in os.walk(args.assets):
            for fn in files:
                if fn.lower().endswith('.exe') and 'setup' in fn.lower():
                    out = os.path.join(root, fn)
    if os.path.exists(out):
        log('BUILT: %s (%.1f MB)' % (out, os.path.getsize(out) / 1048576))
        print('EXE_PATH=%s' % out)
        return 0
    log('ERROR: setup exe not found after compile'); return 5


if __name__ == '__main__':
    sys.exit(main())
