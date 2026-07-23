#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wizard unit tests against the REAL modules (via a minimal Kodi shim).

Covers the correctness/data-safety invariants hardened across the security
audits: keep backup/restore, credential preservation, the minidump crash-
signature parser, log scrubbing, the atomic op-lock, addon swap+rollback
recovery, build-zip CRC validation, backup zip-slip guard, and the
update-before-removal ordering (removals must be skipped when an update fails).

Run:  python tools/tests/run_tests.py
"""
import os, sys, tempfile, shutil, sqlite3, struct, zipfile

import _bootstrap  # noqa: E402  (same dir)
_bootstrap.setup_path()
HOME = _bootstrap.make_home()

import resources.libs.config as C            # noqa: E402
import resources.libs.keep as keep           # noqa: E402
import resources.libs.content_source as cs   # noqa: E402
import resources.libs.logs as logs           # noqa: E402
import resources.libs.modular_update as mu    # noqa: E402
import resources.libs.builds as builds       # noqa: E402
import resources.libs.backup as backup       # noqa: E402

PASS, FAIL = [], []
def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(('  PASS ' if cond else '  FAIL ') + name)


def test_imports():
    # The module-level imports above already loaded config/keep/content_source/
    # logs/modular_update/builds/backup -- reaching here proves they import with
    # zero import-time errors. Also load the remaining wizard libs explicitly.
    print("=== modules import cleanly under the Kodi shim ===")
    check('core wizard modules imported (config/keep/content_source/logs/'
          'modular_update/builds/backup)', True)
    for m in ('resources.libs.maintenance', 'resources.libs.ui'):
        try:
            __import__(m, fromlist=['x']); check('import ' + m, True)
        except Exception as e:
            check('import %s -> %s' % (m, e), False)


def test_keep():
    print("\n=== keep: safe_db_copy / db_write / read-errors / POV+gears roundtrip ===")
    d = tempfile.mkdtemp(); src = os.path.join(d, 'w.db'); dst = os.path.join(d, 's.db')
    c = sqlite3.connect(src); c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(id int)"); c.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(20)])
    c.commit(); c.execute("INSERT INTO t VALUES(999)"); c.commit()
    ok = keep._safe_db_copy(src, dst); c.close()
    check('safe_db_copy WAL-consistent (21 rows)', ok and sqlite3.connect(dst).execute("SELECT count(*) FROM t").fetchone()[0] == 21)
    db = os.path.join(d, 'x.db'); sqlite3.connect(db).execute("CREATE TABLE settings(setting_id TEXT UNIQUE, setting_value TEXT)")
    check('db_write missing -> nodb', keep._db_write(os.path.join(d, 'no.db'), {'a': 'b'}) == 'nodb')
    check('db_write ok -> True', keep._db_write(db, {'rd.token': 'X'}) is True)
    check('db_read absent -> {}', keep._db_read(os.path.join(d, 'no.db'), ['x']) == {})
    keys = [g['key'] for g in keep.GROUPS]
    check("keep has POV services + viewing groups", 'pov_services' in keys and 'pov_content' in keys)

    # --- REAL POV + gears database backup/restore roundtrip -------------------
    # Proves POV_DB_DIR points at the ACTUAL dir POV uses (plugin.video.pov/<db>
    # directly, NOT a databases/ subdir): the audit's #1 bug staged ZERO items
    # because the dir was wrong, and the old name-only assertion never caught it.
    # A real create -> backup -> wipe -> restore -> verify-rows cycle would.
    def _mkdb(path, sentinel):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        cc = sqlite3.connect(path); cc.execute("PRAGMA journal_mode=WAL")
        cc.execute("CREATE TABLE t(k TEXT, v TEXT)")
        cc.execute("INSERT INTO t VALUES('sentinel', ?)", (sentinel,))
        cc.commit(); cc.close()

    def _sentinel(path):
        if not os.path.isfile(path):
            return None
        cc = sqlite3.connect(path)
        try:
            row = cc.execute("SELECT v FROM t WHERE k='sentinel'").fetchone()
        finally:
            cc.close()
        return row[0] if row else None

    pov_w = os.path.join(keep.POV_DB_DIR, 'watched.db')
    pov_m = os.path.join(keep.POV_DB_DIR, 'maincache.db')
    gears_w = os.path.join(keep.GEARS_DB_DIR, 'watched.db')
    _mkdb(pov_w, 'POV_WATCHED'); _mkdb(pov_m, 'POV_CACHE'); _mkdb(gears_w, 'GEARS_WATCHED')

    # source-aware probe: these groups now report they have real data
    povg = next(g for g in keep.GROUPS if g['key'] == 'pov_content')
    gearsg = next(g for g in keep.GROUPS if g['key'] == 'gears_content')
    check('_group_has_data sees POV + gears viewing data',
          keep._group_has_data(povg) and keep._group_has_data(gearsg))

    ok_b, n = keep.backup(['pov_content', 'gears_content'])
    check('backup roundtrip ok + staged 3 dbs', ok_b and n == 3)
    check('POV dbs staged at correct dir',
          os.path.isfile(os.path.join(keep.STAGE, 'povdb__watched.db')) and
          os.path.isfile(os.path.join(keep.STAGE, 'povdb__maincache.db')))
    check('gears db staged', os.path.isfile(os.path.join(keep.STAGE, 'gearsdb__watched.db')))

    # simulate the wipe destroying the originals, then restore + verify the rows
    for p in (pov_w, pov_m, gears_w):
        os.remove(p)
    _, rf = keep.restore()
    check('restore reported no failures', rf == 0)
    check('POV watched.db restored with its row', _sentinel(pov_w) == 'POV_WATCHED')
    check('POV maincache.db restored with its row', _sentinel(pov_m) == 'POV_CACHE')
    check('gears watched.db restored with its row', _sentinel(gears_w) == 'GEARS_WATCHED')

    # --- #8: restore MERGES into an existing addon_data dir (doesn't skip it) ---
    # A fresh Kodi/bundle can create addon_data/<id> before restore; the old
    # skip-if-exists silently dropped the user's staged data then deleted the
    # backup. Stage a user settings.xml, pre-create the dest, restore, verify.
    if os.path.isdir(keep.STAGE):
        shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.STAGE)
    import json as _json
    _json.dump({'keys': ['extras'], 'settings': {}, 'xml': {}},
               open(os.path.join(keep.STAGE, 'manifest.json'), 'w'))
    stg = os.path.join(keep.STAGE, 'addondata__plugin.user.x')
    os.makedirs(stg)
    open(os.path.join(stg, 'settings.xml'), 'w').write('USER_STAGED')
    dest = os.path.join(keep.ADDON_DATA, 'plugin.user.x')
    os.makedirs(dest, exist_ok=True)                 # dest already exists
    _, rf8 = keep.restore()
    landed = os.path.join(dest, 'settings.xml')
    check('#8 existing addon_data dir MERGED (staged file restored)',
          os.path.isfile(landed) and open(landed).read() == 'USER_STAGED')
    check('#8 restore reported no failure', rf8 == 0)
    shutil.rmtree(d, ignore_errors=True)


def test_switch_transactional():
    print("\n=== content_source: transactional switch (#3) + backup consume (#4) ===")
    import json as _json
    d = tempfile.mkdtemp()
    idx = _json.dumps({'files': [
        {'src': 'a.xml', 'dest': os.path.join(d, 'a.xml')},
        {'src': 'b.xml', 'dest': os.path.join(d, 'b.xml')},
    ]})

    def fake_fetch(rel):
        return idx.encode('utf-8') if rel.endswith('index.json') else None

    orig_fetch, orig_fetchv = cs._fetch, cs._fetchv
    try:
        cs._fetch = fake_fetch
        # PHASE-1 abort: b.xml fails to fetch -> switch must write NOTHING
        cs._fetchv = lambda roots, src: (b'<x/>' if src == 'a.xml' else None)
        applied, failed = cs._apply_index(['root'], 'skin.test')
        wrote = os.path.exists(os.path.join(d, 'a.xml')) or os.path.exists(os.path.join(d, 'b.xml'))
        check('#3 fetch failure -> aborted (applied=0, failed>0)', applied == 0 and failed >= 1)
        check('#3 fetch failure -> NOTHING written to disk', not wrote)
        # all fetch OK -> both files written
        cs._fetchv = lambda roots, src: b'<ok/>'
        applied, failed = cs._apply_index(['root'], 'skin.test')
        check('#3 all fetched -> both written, no failures',
              applied == 2 and failed == 0 and
              os.path.isfile(os.path.join(d, 'a.xml')) and os.path.isfile(os.path.join(d, 'b.xml')))
    finally:
        cs._fetch, cs._fetchv = orig_fetch, orig_fetchv

    # #4 -- _restore_gears restores AND consumes the .pre_gears backup (so the
    # next switch captures fresh Gears state instead of reusing a stale copy).
    uf = os.path.join(cs.USERDATA, 'zzz_switchtest.xml')
    open(uf + '.pre_gears', 'w').write('GEARS_BACKUP')
    open(uf, 'w').write('POV_NOW')                   # live file is now POV
    cs._restore_gears('skin.test')
    check('#4 restore brought back gears content', open(uf).read() == 'GEARS_BACKUP')
    check('#4 .pre_gears consumed after restore', not os.path.exists(uf + '.pre_gears'))
    try:
        os.remove(uf)
    except Exception:
        pass
    shutil.rmtree(d, ignore_errors=True)


def test_cred_preserve():
    print("\n=== content_source: credential-preserving merge ===")
    d = tempfile.mkdtemp(); live = os.path.join(d, 'settings.xml')
    open(live, 'w', encoding='utf-8').write(
        '<settings><setting id="rd.token">U_RD</setting>'
        '<setting id="tb.account_id">U_TB</setting>'
        '<setting id="tmdb.session_id">U_SESS</setting>'
        '<setting id="some.cfg">old</setting></settings>')
    shipped = (b'<settings><setting id="rd.token" default="true" />'
               b'<setting id="tb.account_id" default="true" />'
               b'<setting id="tmdb.session_id" default="true" />'
               b'<setting id="some.cfg">new</setting></settings>')
    out = cs._merge_preserve_creds(shipped, live).decode('utf-8')
    check('preserves rd.token / tb.account_id / tmdb.session_id',
          'U_RD' in out and 'U_TB' in out and 'U_SESS' in out)
    check('config value updated to shipped', 'new' in out and '>old<' not in out)
    check('_POV_CRED_IDS comprehensive (>=28)', len(cs._POV_CRED_IDS) >= 28)
    shutil.rmtree(d, ignore_errors=True)


def test_logs():
    print("\n=== logs: minidump signature + scrub (Bearer/Basic/Cookie) ===")
    buf = bytearray(0x200); buf[0:4] = b'MDMP'; struct.pack_into('<II', buf, 8, 2, 0x20)
    struct.pack_into('<III', buf, 0x20, 6, 0, 0x40); struct.pack_into('<III', buf, 0x2C, 4, 0, 0x80)
    struct.pack_into('<I', buf, 0x48, 0xc0000005); struct.pack_into('<Q', buf, 0x58, 0x10000000 + 0x1c6744)
    struct.pack_into('<I', buf, 0x80, 1); struct.pack_into('<Q', buf, 0x84, 0x10000000)
    struct.pack_into('<I', buf, 0x8C, 0x200000); struct.pack_into('<I', buf, 0x98, 0xC0)
    nm = 'python3.8.dll'.encode('utf-16-le'); struct.pack_into('<I', buf, 0xC0, len(nm)); buf[0xC4:0xC4 + len(nm)] = nm
    dp = os.path.join(HOME, 'kodi_crashlog-t.dmp'); open(dp, 'wb').write(buf)
    check('dump signature', logs._dump_signature(dp) == 'python3.8.dll+0x1c6744 (code 0xc0000005)')
    jwt = 'eyJhbGciOiJI.eyJzdWIi.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV'
    check('scrub Bearer', jwt not in logs._scrub('Authorization: Bearer ' + jwt))
    check('scrub Basic', 'dXNlcjpw' not in logs._scrub('Authorization: Basic dXNlcjpwYXNzd29yZA=='))
    check('scrub Cookie', 'secret456' not in logs._scrub('Cookie: s=abc; auth_token=secret456'))


def test_lock_and_recovery():
    print("\n=== modular_update: atomic op-lock + rollback recovery ===")
    mu.release_op_lock()
    check('acquire -> True', mu.acquire_op_lock('t1') is True)
    check('re-acquire while held -> False', mu.acquire_op_lock('t2') is False)
    mu.release_op_lock()
    check('after release -> True', mu.acquire_op_lock('t3') is True)
    mu.release_op_lock()
    AP = mu.ADDONS_PATH
    os.makedirs(os.path.join(AP, '.rb_addonA'), exist_ok=True)
    os.makedirs(os.path.join(AP, '.stage_addonC'), exist_ok=True)
    mu._recover_orphaned_rollbacks()
    check('interrupted swap recovered', os.path.isdir(os.path.join(AP, 'addonA')) and not os.path.isdir(os.path.join(AP, '.rb_addonA')))
    check('stale staging cleared', not os.path.isdir(os.path.join(AP, '.stage_addonC')))


def test_validate_zip():
    print("\n=== builds.validate_build_zip: full CRC + structure ===")
    bm = builds.BuildManager(); d = tempfile.mkdtemp(); good = os.path.join(d, 'g.zip')
    MARK = b'CORRUPTME_' * 60
    with zipfile.ZipFile(good, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('addons/plugin.x/addon.xml', '<addon id="plugin.x" version="1.0"/>')
        z.writestr('userdata/guisettings.xml', '<settings/>')
        zi = zipfile.ZipInfo('addons/plugin.x/resources/font.ttf'); zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, MARK)
    check('valid zip -> ok', bm.validate_build_zip(good)[0] is True)
    bad = os.path.join(d, 'b.zip'); shutil.copy(good, bad)
    data = bytearray(open(bad, 'rb').read()); i = data.find(MARK)
    for j in range(i, i + 60):
        data[j] ^= 0xFF
    open(bad, 'wb').write(data)
    check('corrupt member REJECTED before wipe', bm.validate_build_zip(bad)[0] is False)
    check('empty zip rejected', bm.validate_build_zip(os.path.join(d, 'e.zip') if os.path.exists(os.path.join(d, 'e.zip')) else _mkempty(d))[0] is False)
    shutil.rmtree(d, ignore_errors=True)


def _mkempty(d):
    p = os.path.join(d, 'e.zip'); zipfile.ZipFile(p, 'w').close(); return p


def test_backup_restore():
    print("\n=== backup.restore: integrity + zip-slip guard ===")
    bmgr = backup.BackupManager()
    d2 = os.path.join(C.USERDATA, 'addon_data', 'wiz', 'backups'); os.makedirs(d2, exist_ok=True)
    zp = os.path.join(d2, 'b.zip'); outside = os.path.join(os.path.dirname(HOME), 'PWN_SENTINEL.txt')
    with zipfile.ZipFile(zp, 'w') as z:
        z.writestr('manifest.json', '{"scope":"full"}')
        z.writestr('userdata/guisettings.xml', '<settings/>')
        z.writestr('userdata/../../PWN_SENTINEL.txt', 'PWNED')
    check('restore valid -> True', bmgr.restore(zp) is True)
    check('zip-slip BLOCKED', not os.path.isfile(outside))


def test_backup_quick_creds():
    print("\n=== backup: quick backup captures Gears settings.db + POV creds (#9) ===")
    os.makedirs(C.backup_location(), exist_ok=True)
    gdb_dir = os.path.join(C.ADDON_DATA_PATH, C.GEARS_ADDON_ID, 'databases')
    os.makedirs(gdb_dir, exist_ok=True)
    gdb = os.path.join(gdb_dir, 'settings.db')
    cc = sqlite3.connect(gdb); cc.execute("PRAGMA journal_mode=WAL")
    cc.execute("CREATE TABLE settings(setting_id TEXT UNIQUE, setting_value TEXT)")
    cc.execute("INSERT INTO settings VALUES('rd.token','USER_RD_TOKEN')"); cc.commit(); cc.close()
    povdir = os.path.join(C.ADDON_DATA_PATH, 'plugin.video.pov')
    os.makedirs(povdir, exist_ok=True)
    open(os.path.join(povdir, 'settings.xml'), 'w', encoding='utf-8').write(
        '<settings><setting id="tb.token">USER_TB_TOKEN</setting></settings>')

    zp, _mf = backup.BackupManager().create('quick')
    check('quick backup created', bool(zp) and os.path.isfile(zp))
    db_arc = 'addon_data/%s/databases/settings.db' % C.GEARS_ADDON_ID
    pov_arc = 'addon_data/plugin.video.pov/settings.xml'
    with zipfile.ZipFile(zp) as z:
        names = z.namelist()
        check('quick backup includes Gears settings.db', db_arc in names)
        check('quick backup includes POV settings.xml', pov_arc in names)
        if pov_arc in names:
            check('POV creds captured', b'USER_TB_TOKEN' in z.read(pov_arc))
        if db_arc in names:
            td = tempfile.mkdtemp()
            z.extract(db_arc, td)
            dbp = os.path.join(td, *db_arc.split('/'))
            row = sqlite3.connect(dbp).execute(
                "SELECT setting_value FROM settings WHERE setting_id='rd.token'").fetchone()
            check('Gears settings.db snapshot preserved the cred row',
                  bool(row) and row[0] == 'USER_RD_TOKEN')
            shutil.rmtree(td, ignore_errors=True)


def test_update_ordering():
    print("\n=== run_update: removals SKIPPED when an update fails (#11 regression) ===")
    fake = {'addons': [{'id': 'plugin.new', 'version': '2.0', 'sha256': 'x' * 64, 'url': 'http://x'}],
            'generated_utc': 'now', 'config': None, 'content_variants': None}
    calls = {'rm': 0, 'one': 0}
    mu.fetch_manifest = lambda force=False: fake
    mu._recover_orphaned_rollbacks = lambda: None
    mu._pin_all_modded_once = lambda s: None
    mu.remove_junk_repos = lambda: []
    mu.repair_disabled_deps = lambda m: []
    mu.repair_skin_menu = lambda no_reload=False: False
    mu._maybe_apply_config = lambda m, s, force=False: False
    mu._maybe_apply_content_variants = lambda m, s, force=False: False
    mu._active_skin = lambda: 'skin.estuary'
    mu._load_state = lambda: {}
    mu._save_state = lambda s: None
    mu.compute_updates = lambda m, force=False: [fake['addons'][0]]

    def _rm(m, s):
        calls['rm'] += 1; return []
    mu._apply_removals = _rm

    def _fail(entry):
        calls['one'] += 1; raise Exception('sim fail')
    mu._apply_one = _fail
    mu.release_op_lock()
    s1 = mu.run_update(silent=True)
    check('failed update -> removals SKIPPED', calls['rm'] == 0 and s1.get('failed') == ['plugin.new'])

    calls['rm'] = 0
    mu._apply_one = lambda entry: None       # succeed
    mu.release_op_lock()
    mu.run_update(silent=True)
    check('successful update -> removals ran', calls['rm'] == 1)

    # --- user cancels mid-update -> removals + config apply BOTH skipped ------
    calls['rm'] = 0
    cfg = {'n': 0}
    mu._maybe_apply_config = lambda m, s, force=False: (cfg.__setitem__('n', cfg['n'] + 1), False)[1]
    mu._apply_one = lambda entry: None       # would succeed, but we cancel first

    class _CancelProg(object):
        def __init__(self, silent): pass
        def update(self, *a, **k): pass
        def iscanceled(self): return True    # cancel before the first apply
        def close(self): pass
    mu._Progress = _CancelProg
    mu.release_op_lock()
    sc = mu.run_update(silent=False)
    check('cancelled -> summary.cancelled True + ok False',
          sc.get('cancelled') is True and sc.get('ok') is False)
    check('cancelled -> nothing applied', sc.get('applied') == [])
    check('cancelled -> removals SKIPPED', calls['rm'] == 0)
    check('cancelled -> config apply SKIPPED', cfg['n'] == 0)


def main():
    for t in (test_imports, test_keep, test_cred_preserve, test_switch_transactional,
              test_logs, test_lock_and_recovery,
              test_validate_zip, test_backup_restore, test_backup_quick_creds,
              test_update_ordering):
        try:
            t()
        except Exception as e:
            import traceback; traceback.print_exc()
            FAIL.append('%s crashed: %s' % (t.__name__, e))
    print("\n" + "=" * 52)
    print("RESULT: %d passed, %d failed" % (len(PASS), len(FAIL)))
    for f in FAIL:
        print("  FAIL: " + f)
    shutil.rmtree(HOME, ignore_errors=True)
    return 1 if FAIL else 0


if __name__ == '__main__':
    sys.exit(main())
