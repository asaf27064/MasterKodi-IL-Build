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
import io, os, sys, tempfile, shutil, sqlite3, struct, zipfile

# Test names carry Hebrew (the UI strings they assert on). The GitHub Windows
# runner's console is cp1252, so printing one raised
# "'charmap' codec can't encode characters ..." and FAILED the build on the test
# harness rather than on the code under test (2026-08-02). Never let output
# encoding decide whether a test passes.
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

import _bootstrap  # noqa: E402  (same dir)
_bootstrap.setup_path()
HOME = _bootstrap.make_home()
REPO = _bootstrap.REPO                       # repo root, for static source checks

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

    # roundtrip PER TARGET (source-aware restore only writes the target engine's
    # dbs): pov-target cycle restores the POV dbs, gears-target the gears db.
    ok_b, n = keep.backup(['pov_content'], target_content='pov')
    check('POV backup ok + staged 2 dbs', ok_b and n == 2)
    check('POV dbs staged at correct dir',
          os.path.isfile(os.path.join(keep.STAGE, 'povdb__watched.db')) and
          os.path.isfile(os.path.join(keep.STAGE, 'povdb__maincache.db')))
    os.remove(pov_w); os.remove(pov_m)          # simulate the wipe
    _, rf = keep.restore()
    check('restore reported no failures', rf == 0)
    check('POV watched.db restored with its row', _sentinel(pov_w) == 'POV_WATCHED')
    check('POV maincache.db restored with its row', _sentinel(pov_m) == 'POV_CACHE')

    # --- the continue-watching row across a reinstall -------------------
    # The merged row stores NOTHING of its own: it is computed from POV's
    # `progress` table (resume points) and `watched_status` (last watched per
    # show). So the question "will my continue watching survive a reinstall?"
    # is exactly "does keep carry those two tables?" -- asserted here with the
    # real tables rather than a sentinel row, for both halves at once.
    import sqlite3 as _sq3
    con = _sq3.connect(pov_w)
    con.execute('CREATE TABLE IF NOT EXISTS watched_status (db_type TEXT, media_id TEXT, '
                'season INTEGER, episode INTEGER, last_played TEXT, title TEXT)')
    con.execute('CREATE TABLE IF NOT EXISTS progress (db_type TEXT, media_id TEXT, '
                'season INTEGER, episode INTEGER, resume_point TEXT, curr_time TEXT, '
                'last_played TEXT, resume_id INTEGER, title TEXT)')
    con.execute("INSERT INTO watched_status VALUES ('episode','94997',1,1,'2026-08-23 21:10:00','')")
    con.execute("INSERT INTO progress VALUES ('episode','108978',1,2,'38.5','1120',"
                "'2026-08-24 14:00:00',0,'')")
    con.commit(); con.close()

    ok_b, _n = keep.backup(['pov_content'], target_content='pov')
    os.remove(pov_w)                                   # the wipe
    _, rf = keep.restore()
    con = _sq3.connect(pov_w)
    nxt = con.execute("SELECT media_id, season, episode FROM watched_status").fetchall()
    res = con.execute("SELECT media_id, season, episode, resume_point FROM progress").fetchall()
    con.close()
    check('reinstall keeps the NEXT-EPISODE half (watched history)',
          ok_b and rf == 0 and nxt == [('94997', 1, 1)])
    check('reinstall keeps the RESUME half (bookmarks/progress)',
          res == [('108978', 1, 2, '38.5')])

    cw = open(os.path.join(REPO, 'overlays', 'plugin.video.pov', 'files', 'resources',
                           'lib', 'kodirdil', 'continue_watching.py'), encoding='utf-8').read()
    check('...and the row itself stores nothing that could be lost',
          'INSERT' not in cw.upper() and 'set_bookmark' not in cw)

    ok_b, n = keep.backup(['gears_content'], target_content='gears')
    check('gears db staged', ok_b and os.path.isfile(os.path.join(keep.STAGE, 'gearsdb__watched.db')))
    os.remove(gears_w)
    _, rf = keep.restore()
    check('gears watched.db restored with its row', rf == 0 and _sentinel(gears_w) == 'GEARS_WATCHED')

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

    # --- #16: an executable addon dir is REPLACED, not merged (no old+new hybrid)
    if os.path.isdir(keep.STAGE):
        shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.STAGE)
    _json.dump({'keys': ['extras'], 'settings': {}, 'xml': {}},
               open(os.path.join(keep.STAGE, 'manifest.json'), 'w'))
    astg = os.path.join(keep.STAGE, 'addon__plugin.user.y')
    os.makedirs(astg)
    open(os.path.join(astg, 'new.py'), 'w').write('NEW')
    adst = os.path.join(keep.HOME_ADDONS, 'plugin.user.y')
    os.makedirs(adst, exist_ok=True)
    open(os.path.join(adst, 'old.py'), 'w').write('OLD')   # stale module present at dst
    _, rf16 = keep.restore()
    check('#16 addon dir REPLACED (stale old.py gone, new.py present)',
          os.path.isfile(os.path.join(adst, 'new.py')) and
          not os.path.isfile(os.path.join(adst, 'old.py')))
    check('#16 restore reported no failure', rf16 == 0)
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
        # #1 -- a MISSING/malformed index must be a FAILURE, not silent (0,0)
        # success (which let _apply_pov_core flip the box to POV with no files).
        cs._fetch = lambda rel: None
        applied, failed = cs._apply_index(['root'], 'skin.test')
        check('#1 missing index -> failure (not 0,0 success)', applied == 0 and failed >= 1)
        # #6 -- a valid but EMPTY index (parses, declares no files) is a failure too
        cs._fetch = lambda rel: (b'{"files": []}' if rel.endswith('index.json') else None)
        applied, failed = cs._apply_index(['root'], 'skin.test')
        check('#6 empty index -> failure (not 0,0 success)', applied == 0 and failed >= 1)
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
    # pid liveness: our own pid is alive, an absurd pid is dead
    check('_pid_alive(own) True', mu._pid_alive(os.getpid()) is True)
    check('_pid_alive(bogus) False', mu._pid_alive(2 ** 30) is False)
    # a lock left by a DEAD owner (crash) must be reclaimed IMMEDIATELY, not held
    # for OP_LOCK_STALE -- this is the boot-auto-update-blocked bug.
    mu.release_op_lock()
    mu.acquire_op_lock('crashed')                 # fresh lock owned by this (live) pid
    _orig_alive = mu._pid_alive
    try:
        mu._pid_alive = lambda pid: False         # simulate the owner having crashed
        check('dead-owner lock reclaimed at once', mu.acquire_op_lock('recover') is True)
    finally:
        mu._pid_alive = _orig_alive
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
    # #3 -- identity check: a valid zip for the WRONG addon is rejected
    check('#3 identity: requested addon present -> ok',
          bm.validate_build_zip(good, expected_addon_id='plugin.x')[0] is True)
    check('#3 identity: wrong-but-valid zip rejected',
          bm.validate_build_zip(good, expected_addon_id='skin.other')[0] is False)
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

    # #8 -- a db snapshot FAILURE must FAIL the whole quick backup (no false
    # success that silently omits the user's creds).
    _orig = backup.BackupManager._snapshot_db
    try:
        backup.BackupManager._snapshot_db = lambda self, src: None
        zp2, mf2 = backup.BackupManager().create('quick')
        check('#8 snapshot failure -> quick backup FAILS (not false success)',
              zp2 is None and mf2 is None)
    finally:
        backup.BackupManager._snapshot_db = _orig


def test_update_ordering():
    print("\n=== run_update: removals SKIPPED when an update fails (#11 regression) ===")
    fake = {'addons': [{'id': 'plugin.new', 'version': '2.0', 'sha256': 'x' * 64, 'url': 'http://x'}],
            'generated_utc': 'now', 'config': None, 'content_variants': None}
    calls = {'rm': 0, 'one': 0}
    # These stubs used to leak into EVERY later test (they were never restored),
    # so a subsequent test calling e.g. mu.repair_skin_menu silently exercised
    # the lambda instead of the real code and could not fail. Snapshot the real
    # attributes and restore them at the end of this test.
    _saved = {n: getattr(mu, n) for n in (
        'fetch_manifest', '_recover_orphaned_rollbacks', '_pin_all_modded_once',
        'remove_junk_repos', 'repair_disabled_deps', 'repair_skin_menu',
        '_maybe_apply_config', '_maybe_apply_content_variants', '_active_skin',
        '_load_state', '_save_state', 'compute_updates', '_apply_removals',
        '_apply_one')}
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
    for _n, _v in _saved.items():          # un-leak: later tests get the REAL code
        setattr(mu, _n, _v)


def test_skin_include_names_resolve():
    """Every <include>NAME</include> a shipped skin view calls must be DEFINED
    by whatever Includes.xml the variant lays over that skin.

    Kodi silently ignores an unknown include -- no error, no log line, the block
    just never renders. The estuary-pov variant renamed the ratings bar to
    POVRatingsBar while the skin's 7 view files still called GearsRatingsBar, so
    POV boxes showed NO rating flags at all while the data underneath was fine
    (Asaf, 2026-08-02). The property names are deliberately kept `gears.*` so
    one skin XML serves both engines; the include name must follow the same
    rule."""
    print("\n=== skin overrides: called includes are defined ===")
    import re as _re

    skin_xml = os.path.join(REPO, 'addons', 'skin.estuary', 'xml')
    called = set()
    for fn in os.listdir(skin_xml):
        if not fn.lower().endswith('.xml'):
            continue
        with open(os.path.join(skin_xml, fn), encoding='utf-8', errors='replace') as fh:
            txt = fh.read()
        called |= set(_re.findall(r'<include>([A-Za-z0-9_]+)</include>', txt))
    check('found include calls in the shipped skin', len(called) > 5)

    for variant in ('config-variants/estuary-pov',
                    'config-variants-piers/estuary-piers-pov'):
        ov = os.path.join(REPO, variant, 'skin-overrides', 'Includes.xml')
        if not os.path.isfile(ov):
            continue
        with open(ov, encoding='utf-8', errors='replace') as fh:
            ov_txt = fh.read()
        defined_ov = set(_re.findall(r'<include name="([A-Za-z0-9_]+)"', ov_txt))
        base = os.path.join(skin_xml, 'Includes.xml')
        with open(base, encoding='utf-8', errors='replace') as fh:
            defined_base = set(_re.findall(r'<include name="([A-Za-z0-9_]+)"', fh.read()))
        # the override REPLACES Includes.xml, so anything the base defined there
        # and a view still calls must survive the override
        lost = (defined_base & called) - defined_ov
        check('%s keeps every called include the base defined (%s)'
              % (variant.split('/')[-1], ', '.join(sorted(lost)) or 'none lost'),
              not lost)


def test_clean_install_option():
    """The KEEP dialog asks the MODE first: keep everything / pick / clean.

    The first attempt put a "keep nothing" row inside the checklist, which read
    as self-contradictory -- ticking it left every other row ticked too, so the
    dialog showed "keep nothing" AND "keep Debrid" at once (Asaf, 2026-08-02;
    Kodi's multiselect cannot untick rows reactively). Three exclusive options
    remove the ambiguity, and "keep everything" became one press.

    Cancel must still keep everything (never lose data by backing out), and the
    clean path must confirm first."""
    print("\n=== keep.prompt: mode question, then the checklist ===")
    import xbmcgui as _gui

    real_sel, real_ms, real_yesno = (_gui.Dialog.select, _gui.Dialog.multiselect,
                                     _gui.Dialog.yesno)
    seen = {}

    def run(mode, pick=None, confirm=True):
        def sel(self, heading, options, **kw):
            seen['options'] = options
            return mode
        def ms(self, heading, labels, preselect=None, **kw):
            seen['labels'] = labels
            seen['preselect'] = list(preselect or [])
            return pick(labels) if pick else list(preselect or [])
        _gui.Dialog.select, _gui.Dialog.multiselect = sel, ms
        _gui.Dialog.yesno = lambda self, *a, **k: confirm
        try:
            return keep.prompt(extras=None, default_all=True)
        finally:
            (_gui.Dialog.select, _gui.Dialog.multiselect,
             _gui.Dialog.yesno) = real_sel, real_ms, real_yesno

    keys_all = run(0)
    check('mode question offers exactly 3 options', len(seen['options']) == 3)
    check('clean option is LAST and marked', 'נקייה' in seen['options'][2])
    check('checklist NOT shown for "keep everything"', 'labels' not in seen)
    check('"keep everything" returns every group', bool(keys_all))

    keys_pick = run(1)
    check('"pick" opens the checklist', 'labels' in seen)
    check('checklist has NO clean row (no contradiction possible)',
          not any('נקייה' in l for l in seen['labels']))
    check('checklist preselects every row', seen['preselect'] == list(range(len(seen['labels']))))
    check('"pick" with defaults keeps the same groups as "keep everything"',
          keys_pick == keys_all)

    check('clean choice keeps nothing', run(2) == [])
    check('un-ticking everything is also the clean choice',
          run(1, pick=lambda labels: []) == [])
    check('declining the clean confirm does NOT lose data',
          run(2, confirm=False) == keys_all)
    # Cancel USED to mean "keep everything, install anyway". It was data-safe but
    # guessed wrong where it costs: someone who meant "stop" got a full wipe and
    # reinstall regardless (Asaf, 2026-08-15). It now aborts -- see
    # test_keep_cancel_aborts_the_install for the caller-side ordering that makes
    # None distinguishable from the [] clean-install choice.
    check('cancelling the mode question ABORTS (None), it does not install',
          run(-1) is None)


def test_credentials_survive_reinstall():
    """KEEP must actually preserve the debrid/Trakt logins it offers to keep.

    POV and Gears use the SAME setting ids -- only the STORAGE differs (POV
    settings.xml vs Gears settings.db), so carrying them is a copy, not a
    rename. Two real bugs are pinned here, both found on 2026-08-02:

    1. the ids listed for Gears were `torbox.api_key` / `premiumize.token` /
       `alldebrid.token`, which exist in NEITHER engine (verified against a live
       settings.db) -- so those logins were never staged at all and even a plain
       Gears->Gears reinstall lost them;
    2. on a cross-source reinstall the POV xml values are the only copy and were
       dropped, because restore skips the POV xml on a Gears target. Asaf lost
       his TorBox login exactly this way while the UI listed the group as kept.
    """
    print("\n=== keep: debrid/Trakt logins survive reinstall (both directions) ===")
    import json as _json

    # ---- the ids we claim Gears uses must be the ones Gears really uses ----
    grp = next(g for g in keep.GROUPS if g['key'] == 'debrid')
    check('gears debrid ids include the real TorBox id (tb.token)',
          'tb.token' in grp['gears_ids'])
    check('no phantom ids that exist in neither engine',
          not ({'torbox.api_key', 'premiumize.token', 'alldebrid.token'}
               & set(grp['gears_ids'])))
    check('per-install OAuth registration NOT copied between boxes',
          'rd.client_id' not in grp['gears_ids'] and 'rd.secret' not in grp['gears_ids'])

    # ---- same-source Gears reinstall: tb.token must be staged AND restored ---
    shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.GEARS_DB_DIR, exist_ok=True)
    gdb = keep.GEARS_SETTINGS_DB
    if os.path.exists(gdb):
        os.remove(gdb)
    con = sqlite3.connect(gdb)
    con.execute("CREATE TABLE settings (setting_id TEXT PRIMARY KEY, setting_value TEXT)")
    con.execute("INSERT INTO settings VALUES ('tb.token','TORBOX_KEY')")
    con.execute("INSERT INTO settings VALUES ('ad.token','false')")
    con.commit(); con.close()
    ok, staged = keep.backup(['debrid'], target_content='gears', source_content='gears')
    check('gears->gears: backup reports ok', ok is True)
    saved = _json.load(open(os.path.join(keep.STAGE, 'manifest.json'), encoding='utf-8'))
    check('gears->gears: TorBox token actually staged',
          saved['settings'].get('gears', {}).get('tb.token') == 'TORBOX_KEY')
    os.remove(gdb)
    con = sqlite3.connect(gdb)
    con.execute("CREATE TABLE settings (setting_id TEXT PRIMARY KEY, setting_value TEXT)")
    con.commit(); con.close()
    keep.restore()
    con = sqlite3.connect(gdb)
    got = dict(con.execute("SELECT setting_id, setting_value FROM settings").fetchall())
    con.close()
    check('gears->gears: TorBox token restored', got.get('tb.token') == 'TORBOX_KEY')

    # ---- POV -> Gears: xml values carried into the gears db -----------------
    shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.STAGE, exist_ok=True)
    pov_xml = keep.POV_SETTINGS
    os.makedirs(os.path.dirname(pov_xml), exist_ok=True)
    _json.dump({'keys': ['debrid', 'trakt'], 'settings': {},
                'xml': {pov_xml: {'tb.token': 'TORBOX_KEY', 'ad.token': 'false',
                                  'rd.token': 'RD_KEY', 'trakt_user': 'asaf'}},
                'source_content': 'pov', 'target_content': 'gears'},
               open(os.path.join(keep.STAGE, 'manifest.json'), 'w'))
    os.remove(gdb)
    con = sqlite3.connect(gdb)
    con.execute("CREATE TABLE settings (setting_id TEXT PRIMARY KEY, setting_value TEXT)")
    con.commit(); con.close()
    keep.restore()
    con = sqlite3.connect(gdb)
    got = dict(con.execute("SELECT setting_id, setting_value FROM settings").fetchall())
    con.close()
    check('POV->Gears: TorBox token carried', got.get('tb.token') == 'TORBOX_KEY')
    check('POV->Gears: rd token carried', got.get('rd.token') == 'RD_KEY')
    check('POV->Gears: trakt username renamed to GEARS spelling',
          got.get('trakt.user') == 'asaf')
    check("POV->Gears: placeholder 'false' NOT carried", got.get('ad.token') is None)

    # ---- Gears -> POV: db values carried into the POV xml -------------------
    shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.STAGE, exist_ok=True)
    with open(pov_xml, 'w', encoding='utf-8') as fh:
        fh.write('<settings><setting id="tb.token"></setting>'
                 '<setting id="ad.token"></setting>'
                 '<setting id="trakt_user"></setting></settings>')
    _json.dump({'keys': ['debrid'],
                'settings': {'gears': {'tb.token': 'TORBOX_KEY2', 'ad.token': 'AD_KEY',
                                       'trakt.user': 'TRAKT_NAME'}},
                'xml': {}, 'source_content': 'gears', 'target_content': 'pov'},
               open(os.path.join(keep.STAGE, 'manifest.json'), 'w'))
    keep.restore()
    with open(pov_xml, encoding='utf-8') as fh:
        xml_after = fh.read()
    check('Gears->POV: TorBox token carried into the POV xml',
          'TORBOX_KEY2' in xml_after)
    check('Gears->POV: alldebrid token carried', 'AD_KEY' in xml_after)
    # The ONE id that genuinely differs between the engines: Gears stores the
    # Trakt username as trakt.user, POV as trakt_user. An identity copy wrote an
    # id POV never reads and the username vanished (live, 2026-08-02).
    check('Gears->POV: trakt username renamed to POV spelling',
          'trakt_user' in xml_after and 'TRAKT_NAME' in xml_after)

    try:
        os.remove(gdb)
    except Exception:
        pass


def test_cross_source_keep():
    """Cross-source reinstall (Gears->POV): the old engine's dbs are NOT restored,
    favourites are PARKED (not clobbering the new config's), gears creds are not
    stashed forever; source-agnostic creds (gearsai xml) still restore."""
    print("\n=== keep: cross-source restore is source-aware ===")
    import json as _json
    if os.path.isdir(keep.STAGE):
        shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.STAGE)
    ga_xml = os.path.join(keep.ADDON_DATA, 'service.subtitles.gearsai', 'settings.xml')
    os.makedirs(os.path.dirname(ga_xml), exist_ok=True)
    open(ga_xml, 'w', encoding='utf-8').write('<settings><setting id="api_key"></setting></settings>')
    _json.dump({'keys': ['gears_content', 'favs', 'gemini', 'debrid'],
                'settings': {'gears': {'rd.token': 'USER_RD'}},
                'xml': {ga_xml: {'api_key': 'USER_GEMINI'}},
                'source_content': 'gears', 'target_content': 'pov'},
               open(os.path.join(keep.STAGE, 'manifest.json'), 'w'))
    # staged artifacts: a gears viewing db + the old favourites
    sqlite3.connect(os.path.join(keep.STAGE, 'gearsdb__watched.db')).execute('CREATE TABLE t(x)')
    open(os.path.join(keep.STAGE, 'file__favourites.xml'), 'w', encoding='utf-8').write(
        '<favourites><favourite name="x">plugin://plugin.video.gears/</favourite></favourites>')
    # the NEW (POV) config's favourites already in place -- must survive
    open(keep.FAVOURITES, 'w', encoding='utf-8').write('<favourites>POV_CONFIG</favourites>')
    pending_before = os.path.isfile(keep.KEEP_PENDING)
    # clear leftovers from earlier tests so the not-restored assertion is real
    try:
        os.remove(os.path.join(keep.GEARS_DB_DIR, 'watched.db'))
    except Exception:
        pass

    _, rf = keep.restore()
    check('cross: restore no failures', rf == 0)
    check('cross: gears db NOT restored',
          not os.path.isfile(os.path.join(keep.GEARS_DB_DIR, 'watched.db')))
    check('cross: POV config favourites SURVIVED (not clobbered)',
          'POV_CONFIG' in open(keep.FAVOURITES, encoding='utf-8').read())
    park = keep.FAVOURITES.replace('favourites.xml', 'favourites.pre_gears.xml')
    check('cross: old favourites PARKED for recovery', os.path.isfile(park))
    check('cross: gears creds NOT stashed to pending (would wait forever)',
          os.path.isfile(keep.KEEP_PENDING) == pending_before)
    check('cross: source-agnostic gearsai key still restored',
          'USER_GEMINI' in open(ga_xml, encoding='utf-8').read())

    # REGRESSION (caught on-device 2026-07-30): install_build flips the live
    # content_source setting to the TARGET *before* keep.backup runs, so backup
    # must take source_content from the CALLER -- reading the setting made
    # source==target, cross collapsed to False, and gears creds were still
    # deferred on a POV-target install.
    import xbmcaddon as _xa
    _xa.Addon().setSetting('content_source', 'pov')      # already flipped to target
    ok_b, _n = keep.backup(['debrid'], target_content='pov', source_content='gears')
    mf = _json.load(open(os.path.join(keep.STAGE, 'manifest.json'), encoding='utf-8'))
    check('cross: backup records CALLER source (not the flipped setting)',
          mf.get('source_content') == 'gears' and mf.get('target_content') == 'pov')


def test_set_default_skin_no_guisettings():
    """REGRESSION (found on-device 2026-07-30, POV+AF3): set_default_skin used to
    log 'guisettings.xml not found' and give up, silently losing the user's skin
    choice -- the POV bundle ships no guisettings.xml, so every POV install with a
    non-default skin booted ESTUARY (the Shield 'chose Zephyr, got Estuary' bug).
    It must CREATE the file instead."""
    print("\n=== builds.set_default_skin: creates guisettings when missing ===")
    bm = builds.BuildManager()
    gs = os.path.join(builds.USERDATA, 'guisettings.xml')
    if os.path.isfile(gs):
        os.remove(gs)
    ok = bm.set_default_skin('skin.arctic.fuse.3')
    check('returns True with no pre-existing guisettings', ok is True)
    check('guisettings.xml created', os.path.isfile(gs))
    body = open(gs, encoding='utf-8').read() if os.path.isfile(gs) else ''
    check('skin written into it', 'skin.arctic.fuse.3' in body)
    check('font also set (Hebrew fontset)', 'lookandfeel.font' in body)
    # and the normal path (file exists) still works
    ok2 = bm.set_default_skin('skin.nimbus')
    body2 = open(gs, encoding='utf-8').read()
    check('existing-file path still updates the skin',
          ok2 is not False and 'skin.nimbus' in body2 and 'skin.arctic.fuse.3' not in body2)


def test_pov_shortcut_folder_seed_is_json():
    """The wizard seeds POV's shortcut folders into navigator.db. POV reads
    list_contents with json.loads (BaseCache.jsloads), so the seed MUST write
    JSON. An earlier repr() wrote a Python literal (single quotes) that
    json.loads rejected -> the folder read as an empty list and every POV
    services widget came up blank (AF3/Nimbus). Regression: seed a folder, then
    parse the stored row EXACTLY like POV does."""
    print("\n=== content_source: POV shortcut-folder seed is JSON (POV uses json.loads) ===")
    import json as _json
    folders = {'Connect Services': [
        {'mode': 'myservices', 'name': 'חיבור שירותים',
         'iconImage': 'x.png', 'isFolder': 'false'},
        {'mode': 'torbox.show_account_info', 'name': 'פרטי חשבון',
         'iconImage': 'y.png', 'isFolder': 'false'}]}
    orig = cs._fetchv

    def fake_fetchv(roots, rel):
        if rel == 'pov/shortcut_folders.json':
            return _json.dumps(folders).encode('utf-8')
        return None      # skip views.json / settings.xml
    cs._fetchv = fake_fetchv
    try:
        cs._seed_pov_db(['dummy'])
    finally:
        cs._fetchv = orig

    ndb = os.path.join(cs.ADDON_DATA_PATH, 'plugin.video.pov', 'navigator.db')
    check('navigator.db created by the seed', os.path.isfile(ndb))
    con = sqlite3.connect(ndb)
    row = con.execute("SELECT list_contents FROM navigator WHERE "
                      "list_name='Connect Services' AND "
                      "list_type='shortcut_folder'").fetchone()
    con.close()
    check('Connect Services row seeded', row is not None)
    stored = row[0] if row else ''
    ok_json, parsed = False, None
    try:
        parsed = _json.loads(stored); ok_json = True     # exactly POV's jsloads
    except Exception:
        pass
    check('list_contents is valid JSON (POV json.loads succeeds)', ok_json)
    check('parses to the 2 seeded items',
          ok_json and isinstance(parsed, list) and len(parsed) == 2)
    check("NOT a python repr (no single-quoted keys)", "'mode'" not in stored)


def test_pov_publishes_player_release():
    """POV must publish the chosen source's release name as the window property
    `subs.player_filename` -- the ONLY channel a subtitle service has to learn
    which release is playing, because debrid streams resolve to a bare UUID
    (getPlayingFile() -> '997ec702-0c77-...'). Without it GearsAI fell back to
    VideoPlayer.Tagline (the marketing tagline), match.player_release() returned
    empty, and the community pool served an out-of-sync subtitle (Supergirl,
    2026-08-01). Gears has always set this in modules/player.py.

    This is a STATIC check on both copies of the overlaid file because a POV
    re-merge is exactly how such a read/write gets silently dropped -- see the
    URLName -> display_name breakage repaired in overlay 0.1.2."""
    print("\n=== POV: publishes subs.player_filename at every play-selection ===")
    import ast as _ast
    copies = (
        ('overlay', os.path.join(REPO, 'overlays', 'plugin.video.pov', 'files',
                                 'resources', 'lib', 'windows', 'sources.py')),
        ('addon',   os.path.join(REPO, 'addons', 'plugin.video.pov',
                                 'resources', 'lib', 'windows', 'sources.py')),
    )
    for label, path in copies:
        src = ''
        try:
            with open(path, encoding='utf-8') as fh:
                src = fh.read()
        except Exception:
            pass
        check('%s: sources.py readable' % label, bool(src))
        if not src:
            continue
        try:
            _ast.parse(src); parsed = True
        except Exception:
            parsed = False
        check('%s: parses (valid AST)' % label, parsed)
        check('%s: helper defined' % label,
              'def publish_player_release(' in src)
        check('%s: writes the property GearsAI reads' % label,
              "'subs.player_filename'" in src)
        # 3 play-selection paths since POV 6.08.01: direct, seekable easynews,
        # browse packs (upstream changed the UNCACHED click to add-to-cloud
        # only -- no playback, nothing to publish). A re-merge that keeps only
        # some would half-fix the bug.
        check('%s: called at all 3 play-selection sites' % label,
              src.count('publish_player_release(') == 4)   # 3 calls + 1 def
        # A STALE name is worse than none -- it matches the PREVIOUS release.
        check('%s: cleared when the source list opens' % label,
              "set_window_property('subs.player_filename', '')" in src)
        check('%s: prefers display_name (upstream renamed URLName)' % label,
              "get('display_name')" in src and 'URLName' not in src)


def test_active_skin_update_on_windows():
    r"""The addon swap renames the live dir to .rb_<id>. Windows refuses that
    while a file inside is OPEN, so the ACTIVE SKIN could never update in place:
    "[WinError 5] Access is denied: addons\skin.nimbus -> addons\.rb_skin.nimbus"
    (Asaf, 2026-08-02). POSIX allows it, which is why Android/Linux never hit it.

    _inplace_update is the fallback: file-by-file, backed up first, and it must
    either fully apply or fully restore -- never leave a half-updated addon."""
    print("\n=== update: active skin falls back to an in-place file update ===")
    d = tempfile.mkdtemp()
    live = os.path.join(d, 'skin.x')
    staged = os.path.join(d, 'staged')
    rb = os.path.join(d, '.rb_skin.x')
    os.makedirs(os.path.join(live, 'xml'))
    open(os.path.join(live, 'addon.xml'), 'w').write('OLD')
    open(os.path.join(live, 'xml', 'a.xml'), 'w').write('OLD-A')
    open(os.path.join(live, 'xml', 'gone.xml'), 'w').write('DROP-ME')
    os.makedirs(os.path.join(staged, 'xml'))
    open(os.path.join(staged, 'addon.xml'), 'w').write('NEW')
    open(os.path.join(staged, 'xml', 'a.xml'), 'w').write('NEW-A')
    open(os.path.join(staged, 'xml', 'b.xml'), 'w').write('NEW-B')

    ok = mu._inplace_update(live, staged, rb)
    check('in-place update reports success', ok is True)
    check('changed file replaced', open(os.path.join(live, 'addon.xml')).read() == 'NEW')
    check('nested file replaced', open(os.path.join(live, 'xml', 'a.xml')).read() == 'NEW-A')
    check('new file added', os.path.isfile(os.path.join(live, 'xml', 'b.xml')))
    check('file dropped by the new version removed',
          not os.path.exists(os.path.join(live, 'xml', 'gone.xml')))

    # failure path: a staged tree that vanishes mid-copy must restore the old
    shutil.rmtree(live, ignore_errors=True)
    os.makedirs(live)
    open(os.path.join(live, 'addon.xml'), 'w').write('KEEP-ME')
    ok2 = mu._inplace_update(live, os.path.join(d, 'does_not_exist'), rb)
    check('missing staged tree -> reports failure', ok2 is False)
    check('live addon left intact after a failure',
          os.path.isfile(os.path.join(live, 'addon.xml'))
          and open(os.path.join(live, 'addon.xml')).read() == 'KEEP-ME')
    shutil.rmtree(d, ignore_errors=True)


def test_menu_bundle_never_overwrites_a_healthy_menu():
    """A bundle VERSION bump must not re-lay the bundle over a healthy menu.

    The bundle is a frozen snapshot and the base config is the live source of
    menu content, so re-laying on "stale" silently REVERTED newer content: on a
    GEARS box it stripped the TMDb widgets config ships (live srtym-1 was
    12 gears/0 tmdb where config ships 7/5), and on POV it replaced POV widgets
    with Gears ones. Same bug, both engines, 2026-08-02. The bundle is only the
    emergency net for skinshortcuts caching an EMPTY menu on a fresh install."""
    print("\n=== menu bundle: only lays over a BROKEN menu ===")
    import shutil as _sh

    ZEPHYR = 'skin.arctic.zephyr.2.resurrection.mod'
    skin_dir = os.path.join(mu.ADDONS_PATH, ZEPHYR, '1080i')
    os.makedirs(skin_dir, exist_ok=True)
    inc = os.path.join(skin_dir, 'script-skinshortcuts-includes.xml')
    open(os.path.join(skin_dir, 'Home.xml'), 'w').close()
    ss = os.path.join(HOME, 'userdata', 'addon_data', 'script.skinshortcuts')
    os.makedirs(ss, exist_ok=True)
    data = os.path.join(ss, 'srtym-1.DATA.xml')
    os.makedirs(mu.ADDON_DATA, exist_ok=True)
    marker = os.path.join(mu.ADDON_DATA, 'menu_ver_%s.txt' % ZEPHYR)

    orig_skin, orig_src, orig_major = mu._active_skin, mu._content_source, mu.KODI_MAJOR
    try:
        mu._active_skin = lambda: ZEPHYR
        mu._content_source = lambda: 'gears'
        mu.KODI_MAJOR = 21

        # A genuinely healthy menu: _menu_is_broken also flags an includes file
        # smaller than HALF the bundle's, and a mainmenu source with no
        # <shortcut> -- so model both, not just a stub.
        bundle_inc = os.path.join(
            mu.ADDON_PATH, 'resources', 'menu_defaults', ZEPHYR, 'includes',
            'script-skinshortcuts-includes.xml')
        pad = max(os.path.getsize(bundle_inc), 1000)
        with open(inc, 'w', encoding='utf-8') as fh:
            fh.write('<includes><shortcut>plugin://plugin.video.gears/?x</shortcut>'
                     + '<!--%s-->' % ('x' * pad) + '</includes>')
        with open(os.path.join(ss, 'mainmenu.DATA.xml'), 'w', encoding='utf-8') as fh:
            fh.write('<shortcuts><shortcut>plugin://plugin.video.gears/?m</shortcut></shortcuts>')
        with open(data, 'w', encoding='utf-8') as fh:
            fh.write('CONFIG_MENU_WITH_TMDB')
        with open(marker, 'w', encoding='utf-8') as fh:
            fh.write('0')
        restored = mu.repair_skin_menu(no_reload=True)
        with open(data, encoding='utf-8') as fh:
            after = fh.read()
        check('healthy menu NOT overwritten by a version bump',
              after == 'CONFIG_MENU_WITH_TMDB' and not restored)
        check('marker still advanced (no repeated checking)',
              open(marker, encoding='utf-8').read().strip() != '0')

        # genuinely BROKEN menu -> the bundle IS laid (the safety net still works)
        with open(inc, 'w', encoding='utf-8') as fh:
            fh.write('<includes></includes>')          # no <shortcut> == broken
        restored = mu.repair_skin_menu(no_reload=True)
        check('broken menu IS repaired from the bundle',
              'skinshortcuts-data' in restored)
    finally:
        mu._active_skin, mu._content_source = orig_skin, orig_src
        mu.KODI_MAJOR = orig_major
        _sh.rmtree(os.path.join(mu.ADDONS_PATH, ZEPHYR), ignore_errors=True)


class _KodiStub(object):
    """Stands in for anything Kodi-side a scraper module touches on import."""
    def __call__(self, *a, **k): return self
    def __getattr__(self, _): return self
    def __iter__(self): return iter(())
    def __bool__(self): return False
    def __mro_entries__(self, bases): return (object,)   # they subclass Thread


class _StubGlobals(dict):
    def __missing__(self, key):
        import builtins
        if hasattr(builtins, key): raise KeyError(key)
        return _KodiStub()


def _load_classifier(*parts):
    """Execute a shipped source_utils WHOLE, with its Kodi imports neutralised.

    The previous version sliced named functions out of the file, which stops
    covering anything that later moves into a helper -- exactly what happened
    when the strip logic became strip_meta_token."""
    import re as _re, builtins
    path = os.path.join(REPO, *parts)
    # addons/plugin.video.gears/ is GITIGNORED -- the mirror is assembled at
    # build time from the clean base zip + our overlay. It exists on a dev box
    # but not on a fresh CI checkout, and the APK/EXE workflows run the tests
    # BEFORE applying overlays, so this test passed locally and failed there
    # (2026-08-14). Fall back to the overlay copy, which IS committed;
    # verify_overlay_merge.py already proves the two are identical.
    if not os.path.exists(path) and parts[0] == 'addons':
        alt = os.path.join(REPO, 'overlays', parts[1], 'files', *parts[2:])
        if os.path.exists(alt):
            path = alt
    if not os.path.exists(path):
        raise AssertionError('classifier not found in the mirror or the overlay: %s'
                             % os.path.join(*parts))
    body = []
    for line in io.open(path, encoding='utf-8').read().split(chr(10)):
        s = line.strip()
        if s.startswith(('import ', 'from ')) and s != 'import re':
            body.append(line[:len(line) - len(line.lstrip())] + 'pass')
        else:
            body.append(line)
    g = _StubGlobals({'re': _re, '__file__': path, '__name__': 'cls_probe',
                      '__builtins__': builtins})
    exec(compile(chr(10).join(body), path, 'exec'), g)
    return g


def test_hebrew_title_quality_classification():
    """Every source must be labelled with the resolution its NAME states.

    Three separate defects put 4K/1080p rows in the SD column, all measured on
    the box:

      1. a Hebrew meta title normalises to nothing but DOTS, and
         release_title.replace(title, '') then stripped EVERY dot from the
         release name. The resolution patterns are word-boundary anchored, so
         with no separators nothing matched -- 4K rows shown as SD with N/A
         extra info (Reacher, 2026-08-13). Season packs escaped it, which is
         why it first looked provider-specific.
      2. an episode with no Hebrew name arrives as its NUMBER, so stripping
         episode 1 ('.1.') turned '.1080p.' into '.080p.'. That is The Ark
         S03E01: its 1080p rows were SD while its 720p rows were correct --
         '.720p.' happens not to contain '.1.' -- and S03E02 was fine
         throughout (2026-08-14).
      3. the tables never knew 1080i, hd1080, m1080p, fullhd, 1920x1080, 720i,
         hd720, 1280x720, 8K/4320p or 1440p/2K, so all of those were SD.

    Runs the REAL shipped modules, all three of them: magneto builds name_info,
    POV's lib/modules table turns it into the on-screen label (get_file_info,
    called from modules/sources.py for every source), and Gears does the same
    job in the other engine."""
    print("\n=== quality: release name -> resolution label (3 classifiers) ===")
    MAG = _load_classifier('addons', 'plugin.video.pov', 'resources', 'lib',
                           'magneto', 'modules', 'source_utils.py')
    POVSU = _load_classifier('addons', 'plugin.video.pov', 'resources', 'lib',
                             'modules', 'source_utils.py')
    GEARSSU = _load_classifier('addons', 'plugin.video.gears', 'resources',
                               'lib', 'modules', 'source_utils.py')
    info_from_name = MAG['info_from_name']

    def label(release, title, ep_title, hdlr, year):
        """The real path: magneto normalises, POV's display table classifies."""
        ni = info_from_name(release, title, year, hdlr, ep_title)
        return POVSU['get_release_quality'](ni), ni

    HEB = "\u05e8\u05d9\u05e6'\u05e8"          # Reacher, as meta_language=he delivers it
    cases = [
        ('2160p single episode', 'Reacher S04E01 2160p AMZN WEB DL DD 5 1 ATMOS DV HDR10 H 265', '4K'),
        ('1080p single episode', 'Reacher S04E01 MULTI VF2 1080p WEB EAC3 5 1 H264 FRQC MKV', '1080p'),
        ('720p single episode',  'Reacher S04E01 MULTI 720p AMZN WEB DL H264 DDP5 1 ATMOS', '720p'),
        ('genuine SD stays SD',  'Reacher S04E01 DVDRip XviD AC3', 'SD'),
    ]
    for lbl, name, want in cases:
        got, _ = label(name, HEB, 'City of Brotherly Love', 'S04E01', '2026')
        check('hebrew title: %s -> %s' % (lbl, want), got == want)
    for lbl, name, want in cases:
        got, _ = label(name, 'Reacher', 'City of Brotherly Love', 'S04E01', '2026')
        check('english title: %s -> %s' % (lbl, want), got == want)

    _, ni = label('Reacher S04E01 2160p AMZN WEB DL ATMOS', HEB,
                  'City of Brotherly Love', 'S04E01', '2026')
    check('dots preserved for a hebrew title', '.2160p.' in ni)

    # --- The Ark S03E01: the REAL names from kodi.log, every one shown as SD --
    ARK = [
        ('The.Ark.S03E01.1080p.WEB.h264-BAE.mkv', '1080p'),
        ('The.Ark.S03E01.1080p.x265-ELiTE.mkv', '1080p'),
        ('The.Ark.S03E01.I.Told.You.Not.To.Come.1080p.WEBRip.10Bit.DDP5.1.x265-NeoNoir.mkv', '1080p'),
        ('The.Ark.2023.S03E01.1080p.10bit.WEBRip.6CH.x265.HEVC-PSA.mkv', '1080p'),
        ('The.Ark.S03E01.1080p.HEVC.x265-MeGusta[EZTVx.to].mkv', '1080p'),
        ('The.Ark.S03E01.720p.x264-FENiX.mkv', '720p'),
        ('The.Ark.2023.S03E01.720p.10bit.WEBRip.2CH.x265.HEVC-PSA.mkv', '720p'),
    ]
    ARK_HEB = '\u05d4\u05ea\u05d9\u05d1\u05d4'
    ok = True
    for name, want in ARK:
        got, ni = label(name, ARK_HEB, '1', 'S03E01', '2023')
        if got != want:
            ok = False
            print('       %s -> %s (name_info=%s)' % (name, got, ni))
    check('the ark s03e01: numeric episode title does not eat the resolution', ok)

    ok = True
    for ep in range(1, 31):
        got, ni = label('The.Ark.S03E%02d.1080p.WEB.h264-BAE.mkv' % ep,
                        ARK_HEB, str(ep), 'S03E%02d' % ep, '2023')
        if got != '1080p':
            ok = False
            print('       episode %d -> %s (name_info=%s)' % (ep, got, ni))
    check('every episode number 1-30 keeps its 1080p label', ok)

    # --- the vocabulary itself, on all three classifiers ---------------------
    # 1440p/2K map down to 1080p and 8K/4320p up to 4K on purpose: the source
    # panel and the quality filters switch on '4K'/'1080p'/'720p'/'SD', so a new
    # tier would drop out of the panel instead of showing as its own row.
    VOCAB = [
        ('.the.show.2160p.web.dl.', '4K'), ('.the.show.4k.uhd.bluray.', '4K'),
        ('.the.show.hd2160.x265.', '4K'), ('.the.show.3840x2160.hdr.', '4K'),
        ('.the.show.4320p.remux.', '4K'),
        ('.the.show.1080p.web.', '1080p'), ('.the.show.1080i.hdtv.', '1080p'),
        ('.the.show.hd1080.x264.', '1080p'), ('.the.show.fullhd.web.dl.', '1080p'),
        ('.the.show.full.hd.web.dl.', '1080p'), ('.the.show.1920x1080.x264.', '1080p'),
        ('.the.show.m1080p.bluray.', '1080p'), ('.the.show.1440p.web.', '1080p'),
        ('.the.show.720p.web.', '720p'), ('.the.show.720i.hdtv.', '720p'),
        ('.the.show.hd720.x264.', '720p'), ('.the.show.1280x720.x264.', '720p'),
        ('.the.show.480p.dvdrip.', 'SD'), ('.the.show.xvid.dvdrip.', 'SD'),
    ]
    engines = (
        ('pov display', lambda n: POVSU['get_release_quality'](n)),
        ('pov magneto', lambda n: MAG['get_release_quality'](n)[0]),
        ('gears', lambda n: GEARSSU['get_release_quality'](n) or 'SD'),
    )
    for eng_name, fn in engines:
        bad = [(n, want, fn(n)) for n, want in VOCAB if fn(n) != want]
        for n, want, got in bad:
            print('       %-30s want %-6s got %s' % (n, want, got))
        check('%s: every real resolution label recognised' % eng_name, not bad)

    drift = [n for n, _ in VOCAB
             if len({POVSU['get_release_quality'](n),
                     MAG['get_release_quality'](n)[0],
                     GEARSSU['get_release_quality'](n) or 'SD'}) > 1]
    check('all three tables agree (they are maintained as one list)', not drift)

    # get_qual used to return a hard 'SD', so `get_qual(info) or get_qual(link)`
    # could never reach the link -- name_info is always non-empty.
    check('url is consulted when the name carries no resolution',
          MAG['get_release_quality']('.some.release.x265.',
                                     'http://h/movie.2160p.web.dl.mkv')[0] == '4K')


def test_oled_uses_settings_api():
    """The OLED option must go through Kodi's settings API, never a file edit.

    Both OLED entry points used to rewrite guisettings.xml while Kodi was
    running. Kodi keeps its settings in MEMORY and re-saves that file on exit,
    so the edit was discarded -- and the installer does it moments before
    restarting Kodi. Measured on the box 2026-08-13: after a normal close,
    screensaver.time and disableforaudio survived but

        <setting id="screensaver.mode" />

    came back EMPTY -- the one setting that actually enables the black
    screensaver. The feature silently did nothing on every install.
    builds.py already carried a comment warning about this exact behaviour."""
    print("\n=== wizard: OLED settings go through Kodi's API, not guisettings.xml ===")
    base = os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard')
    oled = open(os.path.join(base, 'resources', 'libs', 'oled.py'), encoding='utf-8').read()
    builds = open(os.path.join(base, 'resources', 'libs', 'builds.py'), encoding='utf-8').read()
    default_py = open(os.path.join(base, 'default.py'), encoding='utf-8').read()

    check('oled helper uses Settings.SetSettingValue',
          'Settings.SetSettingValue' in oled)
    check('oled helper never opens guisettings for writing',
          "open(" not in oled and "'w'" not in oled)
    check('installer path delegates to the helper',
          'oled_mod.apply_oled_settings()' in builds)
    check('maintenance menu delegates to the helper',
          'oled_mod.apply_oled_settings()' in default_py)

    # neither OLED path may open guisettings for writing again
    for name, src in (('builds.py', builds), ('default.py', default_py)):
        seg = src[src.find('OLED'):] if 'OLED' in src else ''
        check('%s: no guisettings write in the OLED path' % name,
              "open(guisettings_path, 'w'" not in src)

    # types must match what Kodi expects, or SetSettingValue rejects the call.
    # Slice the table by LINES: a regex up to the first ")\n" stopped early on
    # the trailing comment "(int)" and silently checked only half the table.
    _lines = oled.split(chr(10))
    _a = next(i for i, l in enumerate(_lines) if l.startswith('OLED_SETTINGS'))
    _b = next(i for i in range(_a, len(_lines)) if _lines[i].rstrip() == ')')
    body = chr(10).join(_lines[_a:_b + 1])
    check('OLED_SETTINGS table found', bool(body))
    if body:
        check('screensaver.time sent as an int (not "1")',
              "'screensaver.time', 1" in body)
        check('booleans sent as real booleans',
              "'screensaver.disableforaudio', False" in body
              and "'screensaver.usedimonpause', True" in body)
        check('black screensaver, not dim',
              'screensaver.xbmc.builtin.black' in body)


def test_subtitle_font_choice_is_the_users():
    """A config update must not overwrite the subtitle font the user picked.

    'subtitles.fontname' sat in the guisettings force_ids from 2026-07-14, when
    forcing was the only way to push the new font defaults out. force_ids means
    `push = sid in force_ids or ...` -- an UNCONDITIONAL overwrite on EVERY
    update. Once the GearsAI style panel shipped (1.0.40, Aug) that turned into
    the build silently undoing the user's own choice, which is exactly what Asaf
    hit ("the font/design changes by itself", 2026-08-14).

    The policy's own _comment describes this lifecycle: "remove it once
    propagated if you want user changes to stick again". This test holds it
    removed, and holds the mechanism itself intact -- lookandfeel.font is still
    forced (a wrong skin fontset means tofu on Android), our own default changes
    still propagate via the baseline, and exclude_ids still win."""
    print("\n=== config: the user's subtitle font survives an update ===")
    import json, re
    pol = json.load(open(os.path.join(REPO, 'config', 'config_policy.json'),
                         encoding='utf-8'))
    gui = next(e for e in pol['files'] if e['src'].endswith('userdata/guisettings.xml'))
    forced = list(gui.get('force_ids', []))
    excluded = list(gui.get('exclude_ids', []))

    check('subtitles.fontname is NOT force-pushed', 'subtitles.fontname' not in forced)
    check('lookandfeel.font is still forced (Android tofu guard)',
          'lookandfeel.font' in forced)

    def _xml(pairs):
        rows = ''.join('    <setting id="%s">%s</setting>\n' % kv for kv in pairs)
        return ('<settings version="2">\n' + rows + '</settings>\n')

    def _read(path, sid):
        txt = open(path, encoding='utf-8').read()
        m = re.search(r'<setting id="%s"[^>]*>([^<]*)</setting>' % re.escape(sid), txt)
        return m.group(1) if m else None

    ship = [('subtitles.fontname', 'Rubik'), ('lookandfeel.font', 'Default'),
            ('subtitles.fontsize', '52'), ('lookandfeel.skin', 'skin.estuary')]
    tmp = os.path.join(HOME, 'fonttest')
    os.makedirs(tmp, exist_ok=True)

    def _run(tag, user_pairs, shipped=None):
        """Deliver `shipped` onto a device holding `user_pairs`, twice: the first
        call only records the baseline, the second is the update that matters."""
        dest = os.path.join(tmp, tag + '.xml')
        with open(dest, 'w', encoding='utf-8') as fh:
            fh.write(_xml(user_pairs))
        mu._seed_settings_xml(_xml(ship).encode('utf-8'), dest, excluded,
                              forced, 'gui-' + tag)
        mu._seed_settings_xml(_xml(shipped or ship).encode('utf-8'), dest,
                              excluded, forced, 'gui-' + tag)
        return dest

    # 1. the user picked a different font -> ours must not come back
    d = _run('userpick', [('subtitles.fontname', 'Google Sans'),
                          ('lookandfeel.font', 'Default'),
                          ('subtitles.fontsize', '64')])
    check("user's subtitle font kept", _read(d, 'subtitles.fontname') == 'Google Sans')
    check("user's subtitle size kept (never forced)",
          _read(d, 'subtitles.fontsize') == '64')

    # 2. the forced id still forces
    d = _run('forced', [('subtitles.fontname', 'Rubik'),
                        ('lookandfeel.font', 'SomeOtherFont')])
    check('lookandfeel.font still overwritten by the build',
          _read(d, 'lookandfeel.font') == 'Default')

    # 3. when WE change the shipped default it must still reach the device,
    #    otherwise removing the force would strand everyone on the old value
    newship = [('subtitles.fontname', 'Assistant'), ('lookandfeel.font', 'Default'),
               ('subtitles.fontsize', '52'), ('lookandfeel.skin', 'skin.estuary')]
    d = _run('newdefault', [('subtitles.fontname', 'Rubik'),
                            ('lookandfeel.font', 'Default')], shipped=newship)
    check('a CHANGED shipped default still propagates',
          _read(d, 'subtitles.fontname') == 'Assistant')

    # 4. a device that has never heard of the setting still gets it
    d = _run('absent', [('lookandfeel.font', 'Default')])
    check('missing setting is still seeded', _read(d, 'subtitles.fontname') == 'Rubik')

    # 5. exclude_ids keep winning -- the skin must never be swapped underneath
    d = _run('excluded', [('lookandfeel.skin', 'skin.arctic.fuse.3'),
                          ('lookandfeel.font', 'Default')])
    check('excluded id untouched', _read(d, 'lookandfeel.skin') == 'skin.arctic.fuse.3')

    # 6. and the addon-side rescue must stay narrow: every family it migrates
    #    away from is one whose FILE the pack deletes, so it only ever fires for
    #    a font that genuinely no longer exists.
    sw = open(os.path.join(REPO, 'addons', 'service.subtitles.gearsai',
                           'resources', 'modules', 'sub_window.py'),
              encoding='utf-8').read()
    # An internal family name does NOT match its filename ('NarkisTamKODI
    # Light' ships in NarkisTamLightKodi.ttf; 'Assistant ExtraLight' was the
    # broken internal name stamped on all three Assistant-*.ttf), so the link
    # has to be stated rather than guessed. Adding a family to _FONT_MIGRATE
    # without naming the file it came from fails this test -- which is the
    # point: a migration whose file still exists would overwrite a valid choice.
    PROVIDED_BY = {
        'NarkisDVD': ('NarkisDVD.ttf',),
        'NarkisTam Light': ('NarkisTamLight.ttf',),
        'NarkisTamKODI Light': ('NarkisTamLightKodi.ttf', 'NTAMLI.ttf'),
        'Assistant ExtraLight': ('Assistant-Regular.ttf', 'Assistant-Bold.ttf',
                                 'Assistant-Light.ttf'),
        'Alef': ('Alef.ttf',),
        'Alef Bold': ('AlefBold.ttf',),
        'IBM Plex Hebrew': ('IBMPlexHebrew.ttf',),
        'IBM Plex Hebrew Bold': ('IBMPlexHebrewBold.ttf',),
        'Secular One': ('SecularOne.ttf',),
        'David Libre': ('DavidLibre.ttf',),
    }
    mig = re.search(r'_FONT_MIGRATE = \(([^)]*)\)', sw).group(1)
    rem = re.search(r'_FONT_REMOVE = \(([^)]*)\)', sw).group(1)
    fams = re.findall(r"'([^']+)'", mig)
    files = set(re.findall(r"'([^']+)'", rem))

    undocumented = [f for f in fams if f not in PROVIDED_BY]
    check('every migrated family names the file it came from', not undocumented)
    if undocumented:
        print('       add these to PROVIDED_BY: %s' % undocumented)

    still_there = [f for f in fams
                   if f in PROVIDED_BY
                   and not any(x in files for x in PROVIDED_BY[f])]
    check('every migrated family is one the pack actually deletes '
          '(so it never overrides a font that still exists)', not still_there)
    if still_there:
        print('       families whose file is NOT removed: %s' % still_there)

    check('the migration target itself is never removed',
          not any(v.startswith('Rubik') for v in files))


def _ttf_family(path):
    """nameID 1 (family) out of a TTF 'name' table.

    This is the string Kodi stores in subtitles.fontname -- the FILENAME is
    irrelevant to it, which is why 'NarkisTamKODI Light' lived in
    NarkisTamLightKodi.ttf and why three different Assistant-*.ttf files all
    announced themselves as 'Assistant ExtraLight'."""
    with open(path, 'rb') as fh:
        data = fh.read()
    num = struct.unpack('>H', data[4:6])[0]
    off = None
    for i in range(num):
        rec = 12 + i * 16
        if data[rec:rec + 4] == b'name':
            off = struct.unpack('>I', data[rec + 8:rec + 12])[0]
            break
    if off is None:
        return None
    count, str_off = struct.unpack('>HH', data[off + 2:off + 6])
    for i in range(count):
        r = off + 6 + i * 12
        pid, eid, lid, nid, ln, o = struct.unpack('>HHHHHH', data[r:r + 12])
        if nid != 1:
            continue
        raw = data[off + str_off + o: off + str_off + o + ln]
        try:
            return raw.decode('utf-16-be' if pid == 3 else 'latin-1')
        except Exception:
            return None
    return None


def test_font_picker_matches_the_shipped_pack():
    """Every row the style panel offers must be a font we actually install.

    The panel's list is a hand-written registry (_FONT_FAMILIES) while the fonts
    are files in resources/fonts -- nothing tied the two together, so a row could
    name a font that was never shipped (user picks it, Kodi silently falls back)
    or a shipped weight could stay invisible. Both are only discoverable on a
    device, because Kodi reads the family name out of the file, not the filename.

    Also holds the invariant the whole migration rests on: a family we SHIP must
    never appear in _FONT_MIGRATE, or the sync would reset a perfectly valid
    choice."""
    print("\n=== subtitle fonts: picker rows == shipped files ===")
    base = os.path.join(REPO, 'addons', 'service.subtitles.gearsai')
    fdir = os.path.join(base, 'resources', 'fonts')
    sw = open(os.path.join(base, 'resources', 'modules', 'sub_window.py'),
              encoding='utf-8').read()

    files = [f for f in sorted(os.listdir(fdir))
             if f.lower().endswith(('.ttf', '.otf'))]
    check('the pack ships fonts', bool(files))

    shipped, unreadable = {}, []
    for f in files:
        fam = _ttf_family(os.path.join(fdir, f))
        if fam:
            shipped[fam] = f
        else:
            unreadable.append(f)
    check('every shipped font has a readable family name', not unreadable)
    if unreadable:
        print('       unreadable: %s' % unreadable)

    import re as _re
    reg = _re.search(r'_FONT_FAMILIES = \[(.*?)\n\]', sw, _re.S).group(1)
    offered = [v for _lbl, v in _re.findall(r"\('([^']+)', '([^']+)'\)", reg)]

    # Kodi/Windows supply these; we deliberately list them without shipping them
    SYSTEM = {'DEFAULT', 'David', 'Tahoma', 'Arial'}

    ghosts = [v for v in offered if v not in shipped and v not in SYSTEM]
    check('no picker row names a font we do not ship', not ghosts)
    if ghosts:
        print('       offered but not shipped: %s' % ghosts)

    hidden = [fam for fam in sorted(shipped) if fam not in offered]
    check('no shipped font is missing from the picker', not hidden)
    if hidden:
        print('       shipped but not offered: %s' % hidden)

    check('duplicate family names would collide in Kodi -- none present',
          len(shipped) == len(files) - len(unreadable))

    # the fallback target must itself be shipped, or the rescue lands nowhere
    tgt = _re.search(r"_FONT_MIGRATE_TO = '([^']+)'", sw).group(1)
    check('the migration target (%s) is a font we ship' % tgt, tgt in shipped)

    # and we must never migrate away from a font we still provide
    mig = _re.findall(r"'([^']+)'",
                      _re.search(r'_FONT_MIGRATE = \(([^)]*)\)', sw).group(1))
    conflict = [m for m in mig if m in shipped]
    check('no shipped family is on the migrate-away list', not conflict)
    if conflict:
        print('       would reset a valid font: %s' % conflict)


def test_skip_pill_only_over_fullscreen_video():
    """The skip pill must exist ONLY while fullscreen video is on screen.

    It is a MODAL WindowXMLDialog, so wherever it opens it owns the keyboard.
    The service used to gate on isPlayingVideo() alone -- but backing out of
    playback into a menu leaves the video RUNNING, so the pill appeared over the
    home screen and over POV's sources list and swallowed the keys meant for
    them (Asaf, 2026-08-14).

    Second half: a dismiss carried no cooldown, deliberately, so that closing it
    via the OSD would re-offer the skip. In practice every keypress dismissed it
    and the next tick (1s) put it straight back -- it flickered against the
    remote. A dismiss now holds it off for DISMISS_HOLD seconds; pressing X
    ('no') still suppresses the segment for good, and a real skip still uses its
    own short cooldown.

    Drives the real service loop under the Kodi shim rather than grepping the
    source, so the interaction between the two rules is what is actually
    checked."""
    print("\n=== skip pill: fullscreen-only, and a dismiss actually holds ===")
    import importlib.util
    import types as _types
    import xbmc as _xbmc

    sk = os.path.join(REPO, 'addons', 'service.masterkodi.skipintro')
    spec = importlib.util.spec_from_file_location('mk_skipservice',
                                                  os.path.join(sk, 'service.py'))
    svc_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(svc_mod)

    state = {'fullscreen': True, 'osd': False}
    shown = []

    # the shim returns False for every unset setting, which would switch the
    # service off entirely; feed it the addon's real defaults instead
    DEFAULTS = {'enabled': True, 'use_skipdb': True, 'auto_skip': False}
    svc_mod._get_bool = lambda key, default=True: DEFAULTS.get(key, default)

    # The service's cooldowns are measured with time.time(), but the whole
    # simulated playthrough runs in milliseconds of REAL time -- so a wall-clock
    # hold would never expire and every "is it re-offered?" assertion would pass
    # no matter what the code did. Give it a clock that advances with the ticks.
    class _FakeTime(object):
        def time(self):
            return state['wall']
    svc_mod.time = _FakeTime()

    real_cond = _xbmc.getCondVisibility

    def fake_cond(cond):
        if 'fullscreenvideo' in cond:
            return state['fullscreen']
        if 'videoosd' in cond:
            return state['osd']
        return False

    fake_overlay = _types.ModuleType('overlay')

    def show(label, start, end, player, monitor):
        shown.append((label, start, end, state['clock']))
        return state['result']                     # (pressed, declined, timed_out)

    fake_overlay.show_skip_overlay = show
    sys.modules['overlay'] = fake_overlay

    class FakePlayer(object):
        def isPlayingVideo(self): return True
        def isPlaying(self): return True
        def getPlayingFile(self): return 'stack://ep1.mkv'
        def getTotalTime(self): return 3000.0
        def getTime(self): return state['clock']
        def seekTime(self, t): state['clock'] = t

    def run_ticks(svc, n, on_tick=None):
        """Advance the loop n times, 1 simulated second apart.

        run() keeps its per-episode state in LOCALS, so a scenario that needs
        state to persist has to happen inside ONE run() -- hence on_tick, which
        fires between ticks and can change the environment mid-flight."""
        svc._ticks = n
        svc._tick_no = 0

        def wait(t=0):
            svc._ticks -= 1
            svc._tick_no += 1
            if svc._ticks <= 0:
                return True
            state['clock'] += 1.0
            state['wall'] += 1.0
            if on_tick:
                on_tick(svc._tick_no)
            return False
        svc.waitForAbort = wait
        svc.abortRequested = lambda: False
        svc.run()

    def fresh(clock, result=(False, False, False)):
        state.update({'clock': clock, 'wall': 10000.0, 'result': result,
                      'fullscreen': True, 'osd': False})
        del shown[:]
        s = svc_mod.SkipService()
        s.player = FakePlayer()
        s._detect = lambda: [('intro', 30.0, 120.0)]
        return s

    _xbmc.getCondVisibility = fake_cond
    try:
        # 1. inside the intro, fullscreen -> offered
        s = fresh(40.0)
        run_ticks(s, 3)
        check('offered while fullscreen video is on screen', len(shown) >= 1)

        # 2. same moment, but the user backed out to a menu -> never offered
        s = fresh(40.0)
        state['fullscreen'] = False
        run_ticks(s, 6)
        check('NOT offered over a menu / POV sources list', not shown)

        # 3. dismissed (no press, no X) -> must not re-pop on the next tick
        s = fresh(40.0, result=(False, False, False))
        run_ticks(s, 6)
        check('a dismiss is not re-offered every second', len(shown) == 1)
        check('DISMISS_HOLD is long enough to use the remote',
              svc_mod.DISMISS_HOLD >= 8)

        # 4. X ("no") -> never again for that segment
        s = fresh(40.0, result=(False, True, False))
        run_ticks(s, 40)
        check('X suppresses the segment for good', len(shown) == 1)

        # 5. pressed -> seeks past the segment and stops offering
        s = fresh(40.0, result=(True, False, False))
        run_ticks(s, 5)
        check('a press seeks to the segment end', state['clock'] >= 120.0)

        # 6. leaving fullscreen mid-intro and coming back must NOT wipe the
        #    "declined" answer -- that was the trap in gating the whole loop
        s = fresh(40.0, result=(False, True, False))

        def trip(tick):
            if tick == 2:
                state['fullscreen'] = False        # user backs out to a menu
            elif tick == 5:
                state['fullscreen'] = True         # ...and returns to the video
        run_ticks(s, 20, on_tick=trip)
        check('declined answer survives a trip to a menu', len(shown) == 1)
        # 7. the visible-seconds cap: once the button has had its 8s it steps
        #    aside, and must NOT come back every DISMISS_HOLD for the rest of a
        #    90-second intro -- that would be worse than the parked button the
        #    cap exists to fix.
        s = fresh(40.0, result=(False, False, True))
        run_ticks(s, 60)
        check('a timed-out button is not re-offered for the same segment',
              len(shown) == 1)
    finally:
        _xbmc.getCondVisibility = real_cond
        sys.modules.pop('overlay', None)

    # the cap itself, read from the overlay module
    ov_src = open(os.path.join(sk, 'overlay.py'), encoding='utf-8').read()
    import re as _re2
    default = int(_re2.search(r'PILL_SECONDS_DEFAULT = (\d+)', ov_src).group(1))
    check('default visible seconds is 8 (Asaf 2026-08-14)', default == 8)
    check('the cap is user-configurable', "getSetting('pill_seconds')" in ov_src)
    check('0 means the whole segment (opt out of the cap)',
          'if cap else None' in ov_src)

    st = open(os.path.join(sk, 'resources', 'settings.xml'), encoding='utf-8').read()
    check('pill_seconds is declared in the settings UI', 'pill_seconds' in st)
    check('its declared default matches the code', 'default="8"' in st)
    check('shipped settings carry no XML comment (crashes Kodi)', '<!--' not in st)

    # the already-open pill must close itself if the user leaves fullscreen
    ov = open(os.path.join(sk, 'overlay.py'), encoding='utf-8').read()
    poll = ov[ov.index('def _poll'):ov.index('def _update_bar')]
    check('an open pill closes when fullscreen video goes away',
          'fullscreenvideo' in poll)


def test_log_upload_loses_nothing():
    """A log upload must never carry LESS than it did before the change.

    The old _collect() glued kodi.log + kodi.old.log and kept a blind tail of the
    result. kodi.log comes first, so a big kodi.old.log could push the CURRENT
    session out of the upload entirely -- with no trace, since the banner went
    with it. Asaf's Shield report on 2026-08-14 was exactly that: the upload held
    only the tail of the previous session and the reported moment had scrolled
    away.

    Two things changed, and the point of this test is that neither one costs
    anything:
      * the Cloudflare upload now uses the Worker's real 2 MB capacity instead of
        the 380 KB PASTE limit, so it carries strictly more
      * within that budget the two files share fairly, so the current session can
        no longer vanish

    A fair split and a blind tail genuinely trade against each other at the SAME
    budget, so the 380 KB paste fallback deliberately keeps the legacy behaviour.
    This asserts, per file, that what the old code preserved is still preserved."""
    print("\n=== log upload: strictly more than before, never less ===")
    import xbmcvfs as _vfs
    logpath = _vfs.translatePath('special://logpath/')   # not HOME/temp -- ask the shim
    os.makedirs(logpath, exist_ok=True)

    def legacy(files, budget):
        """The pre-change algorithm, kept here as the thing we must not regress."""
        parts = ['=================== %s ===================\n%s' % (fn, txt)
                 for fn, txt in files]
        combined = ('\n\n'.join(parts)).strip()
        if len(combined) > budget:
            combined = '...(older lines truncated)...\n' + combined[-budget:]
        return combined

    def kept_from(out, txt, fn):
        """How many bytes of THIS file's tail survived into `out`."""
        if not txt:
            return 0
        lo, hi = 0, len(txt)
        while lo < hi:                       # longest suffix of txt present
            mid = (lo + hi + 1) // 2
            if txt[-mid:] in out:
                lo = mid
            else:
                hi = mid - 1
        return lo

    K = 1024
    CASES = (
        ('both small', 40 * K, 60 * K),
        ('current huge, old small', 900 * K, 30 * K),
        ('current small, old huge', 30 * K, 900 * K),      # the Shield case
        ('both huge', 1500 * K, 1500 * K),
        ('no old log', 200 * K, 0),
    )
    for name, n_cur, n_old in CASES:
        cur = ''.join('cur %07d line\n' % i for i in range(n_cur // 16))
        old = ''.join('old %07d line\n' % i for i in range(n_old // 16))
        with open(os.path.join(logpath, 'kodi.log'), 'w', encoding='utf-8') as fh:
            fh.write(cur)
        oldp = os.path.join(logpath, 'kodi.old.log')
        if n_old:
            with open(oldp, 'w', encoding='utf-8') as fh:
                fh.write(old)
        elif os.path.exists(oldp):
            os.remove(oldp)

        files = [('kodi.log', cur)] + ([('kodi.old.log', old)] if n_old else [])
        before = legacy(files, logs.MAX_BYTES)
        after = logs._collect(logs.CF_MAX_BYTES)         # what actually gets uploaded

        ok = True
        for fn, txt in files:
            if kept_from(after, txt, fn) < kept_from(before, txt, fn):
                ok = False
        check('%-24s no file loses bytes it used to keep' % name, ok)

    # the Shield case specifically: the current session must now be present
    cur = ''.join('cur %07d line\n' % i for i in range(30 * K // 16))
    old = ''.join('old %07d line\n' % i for i in range(900 * K // 16))
    with open(os.path.join(logpath, 'kodi.log'), 'w', encoding='utf-8') as fh:
        fh.write(cur)
    with open(os.path.join(logpath, 'kodi.old.log'), 'w', encoding='utf-8') as fh:
        fh.write(old)
    before = legacy([('kodi.log', cur), ('kodi.old.log', old)], logs.MAX_BYTES)
    after = logs._collect(logs.CF_MAX_BYTES)
    check('the CURRENT session used to be dropped entirely',
          'kodi.log ===' not in before)
    check('...and is now included', 'kodi.log ===' in after)
    check('the previous session is still there too', 'kodi.old.log ===' in after)

    # the small paste fallback must be byte-identical to the legacy trim
    check('paste fallback is unchanged (fair=False)',
          logs._collect(logs.MAX_BYTES, fair=False)
          == legacy([('kodi.log', cur), ('kodi.old.log', old)], logs.MAX_BYTES))

    # and when something IS dropped, the upload has to say so
    tiny = logs._collect(50 * K)
    check('a trimmed file states how much was dropped', 'dropped from' in tiny)
    check('the Worker budget is under its 2 MB hard cap',
          logs.CF_MAX_BYTES < 2 * 1024 * 1024)


def test_subtitle_passthrough_is_utf8_and_real():
    """A download that is not an archive must still be normalised and checked.

    Ktuvit serves plain .srt, so extract()'s non-zip branch is its NORMAL path.
    It used to return the file untouched, skipping the convert_to_utf() the zip
    path performs -- and Ktuvit's files are cp1255. Kodi then held a subtitle
    track that was selectable and blank. Reproduced against the live site on
    2026-08-14: 5 of 5 Ktuvit downloads were non-UTF-8, while 5 of 5
    OpenSubtitles downloads (gzip -> extracted -> converted) were clean.

    It only ever appeared to work because 'auto_fix_sub_punctuation' runs
    chardet and rewrites the file; correctness must not depend on an unrelated
    cosmetic setting.

    The cases below include everything that ALREADY worked, so the fix cannot
    quietly narrow it."""
    print("\n=== subtitles: a non-archive download is converted and validated ===")
    sys.path.insert(0, os.path.join(REPO, 'addons', 'service.subtitles.gearsai'))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        'mk_extract_sub', os.path.join(REPO, 'addons', 'service.subtitles.gearsai',
                                       'resources', 'modules', 'extract_sub.py'))
    es = importlib.util.module_from_spec(spec)
    import types as _t
    fake_log = _t.ModuleType('log')
    fake_log.warning = lambda *a, **k: None
    fake_log.info = fake_log.error = fake_log.warning
    pkg = sys.modules.setdefault('resources', _t.ModuleType('resources'))
    mods = sys.modules.setdefault('resources.modules', _t.ModuleType('resources.modules'))
    mods.log = fake_log
    sys.modules['resources.modules.log'] = fake_log
    spec.loader.exec_module(es)

    work = os.path.join(HOME, 'subx')
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    SRT = ('1\n00:00:01,000 --> 00:00:03,000\n%s\n\n'
           '2\n00:00:04,000 --> 00:00:06,000\n%s\n')
    HEB = 'שלום עולם'

    def put(name, data):
        p = os.path.join(work, name)
        with open(p, 'wb') as fh:
            fh.write(data)
        return p

    def utf8_ok(p):
        with open(p, 'rb') as fh:
            raw = fh.read()
        try:
            raw.decode('utf-8')
            return True
        except UnicodeDecodeError:
            return False

    # 1. THE BUG: a cp1255 Hebrew srt, exactly what Ktuvit serves
    p = put('ktuvit.srt', (SRT % (HEB, HEB)).encode('cp1255'))
    got = es.extract(p, work)
    check('cp1255 srt is accepted', got == p)
    check('...and converted to UTF-8 (was the blank-subtitle bug)', utf8_ok(p))
    with open(p, encoding='utf-8') as fh:
        check('...with the Hebrew text intact', HEB in fh.read())

    # 2. MUST NOT REGRESS: an already-UTF-8 srt stays byte-identical
    data = (SRT % (HEB, HEB)).encode('utf-8')
    p = put('already.srt', data)
    got = es.extract(p, work)
    with open(p, 'rb') as fh:
        after = fh.read()
    check('an already-UTF-8 srt is returned', got == p)
    check('...and left byte-identical', after == data)

    # 3. MUST NOT REGRESS: a real zip still goes through the zip path
    import zipfile as _zip
    zp = os.path.join(work, 'bundle.zip')
    with _zip.ZipFile(zp, 'w') as z:
        z.writestr('movie.srt', SRT % (HEB, HEB))
    got = es.extract(zp, work)
    check('a zip still extracts via the zip path',
          bool(got) and got != '0' and got.lower().endswith('.srt'))

    # 4. THE OTHER HALF: payloads that are NOT subtitles must fail honestly
    for name, blob, why in (
            ('error.srt', b'<!DOCTYPE html><html><body>Error 500</body></html>',
             'an HTML error page'),
            ('error2.srt', b'{"error":"quota exceeded"}', 'a JSON error'),
            ('empty.srt', b'', 'an empty file'),
            ('junk.rar', b'Rar!\x1a\x07\x00' + b'\x00' * 200, 'a rar we cannot open')):
        p = put(name, blob)
        check('%-22s -> honest failure, not a blank subtitle' % why,
              es.extract(p, work) == '0')

    # 5. MUST NOT REGRESS: other real subtitle formats still pass
    p = put('micro.sub', b'{0}{75}Hello world|Second line\n')
    check('MicroDVD .sub still accepted', es.extract(p, work) == p)
    p = put('styled.ass', ('[Script Info]\nTitle: x\n\n[Events]\n'
                           'Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,%s\n'
                           % HEB).encode('utf-8'))
    check('ASS/SSA still accepted', es.extract(p, work) == p)

    # 6. MUST NOT REGRESS: image-based subs are passed through untouched
    put('vob.idx', b'# VobSub index\ntimestamp: 00:00:01:000\n')
    binary = b'\x00\x01\x02\x03' * 64
    p = put('vob.sub', binary)
    got = es.extract(p, work)
    with open(p, 'rb') as fh:
        check('VobSub .sub passed through and NOT text-converted',
              got == p and fh.read() == binary)


def test_wand_press_shows_feedback():
    """Pressing the wand must say something immediately.

    show_results already raises an overlay for the sub_window action, but
    nothing ever seeded general.show_msg, so it drew its progress underscores
    with an EMPTY message -- the press looked like it did nothing. Measured from
    Asaf's own logs, the gap between the press and the list appearing is 11.2s
    on Windows and 3.5-3.7s on the Shield when the prefetch misses (0.1-0.2s
    when it hits), so the silence is very visible.

    Two properties matter and both are order-sensitive: the message has to be
    seeded BEFORE the overlay thread starts, and the window has to publish
    'END' so the overlay closes instead of lingering behind it."""
    print("\n=== wand: the press gives immediate feedback ===")
    base = os.path.join(REPO, 'addons', 'service.subtitles.gearsai')
    src = open(os.path.join(base, 'autosub.py'), encoding='utf-8').read()
    win = open(os.path.join(base, 'resources', 'modules', 'sub_window.py'),
               encoding='utf-8').read()

    seed = src.find('show_msg = "מחפש')
    start = src.find('thread[0].start()')
    branch = src.find("elif action=='sub_window':")
    check('the wand action seeds a search message', seed != -1)
    check('...before the overlay thread starts', -1 < seed < start)
    check('...and before the slow search runs', -1 < seed < branch)
    check('it covers the unpause variant too',
          'sub_window", "sub_window_unpause"' in src
          or "'sub_window', 'sub_window_unpause'" in src)
    # it must NOT be gated on the automatic-notification setting: that governs
    # unattended toasts, not feedback for a button the user just pressed.
    # Judge the guarding `if` ITSELF -- scanning the preceding text picked up
    # the neighbouring condition and the comment explaining this very rule.
    guard = src.rfind('if ', 0, seed)
    guard_line = src[guard:src.index(chr(10), guard)]
    check('not gated on enable_autosub_notifications',
          'enable_autosub_notifications' not in guard_line)
    check('...and the guard is the action check itself',
          'sub_window' in guard_line)
    check('the window publishes END so the overlay closes',
          "general.show_msg = 'END'" in win)

    # --- the wand must also consult the prefetch ---------------------------
    # The playback flow already did; the wand did not, so pressing it mid-episode
    # re-ran the whole live search. And because a prefetch HIT skips
    # temporary_pop_and_get_subtitles, the sqlite cache is never populated for
    # that episode -- the better the prefetch worked, the more certain the wand
    # was to search again.
    def branch(name):
        i = src.find("elif action=='%s':" % name)
        if i < 0:
            return ''
        j = src.find("elif action==", i + 10)
        return src[i:j if j > 0 else len(src)]

    for name in ('sub_window', 'sub_window_unpause'):
        b = branch(name)
        check('%-18s asks the prefetch first' % name,
              'prefetch_lookup(video_data)' in b)
        check('%-18s still falls back to the live search'
              % name, 'temporary_pop_and_get_subtitles(video_data)' in b)
        check('%-18s fallback is guarded by "is None"' % name,
              'if f_result is None:' in b)
        # the match-% sort must STILL run on the live video_data afterwards,
        # or a prefetched list would be ordered by the wrong release name
        check('%-18s still sorts on the live playback data' % name,
              'sort_subtitles' in b
              and b.index('sort_subtitles') > b.index('prefetch_lookup'))

    # branches we deliberately did NOT change: a manual 'search', a download,
    # and the next/previous steppers keep their existing behaviour
    for name in ('next', 'previous'):
        check('%-18s left alone (not silently switched to the prefetch)' % name,
              'prefetch_lookup' not in branch(name))


def test_keep_cancel_aborts_the_install():
    """Cancelling 'מה לשמור בהתקנה?' must abort, not install anyway.

    Cancel used to be read as "keep everything" and the install went ahead. It
    is data-safe, but it guessed wrong in the expensive direction: someone who
    meant "keep everything" loses nothing either way, while someone who meant
    "stop" got a full wipe and reinstall (Asaf, 2026-08-15).

    The subtle part is that an EMPTY list already means something -- it is the
    deliberate clean install. So cancel cannot be expressed as []; it returns
    None, and the caller must check `is None` BEFORE its `if not keep_keys`
    branch, or an abort would be executed as "wipe everything". That ordering
    is what this test really pins."""
    print("\n=== install: cancelling the keep question aborts ===")
    import xbmcgui as _gui

    real_select, real_multi, real_yesno = _gui.Dialog.select, _gui.Dialog.multiselect, _gui.Dialog.yesno
    try:
        # 1. cancel on the mode dialog -> None (abort)
        _gui.Dialog.select = lambda self, *a, **k: -1
        check('cancel returns None (abort), not a key list',
              keep.prompt(extras=[], default_all=True) is None)

        # 2. "keep everything" still returns every key
        _gui.Dialog.select = lambda self, *a, **k: 0
        allk = keep.prompt(extras=[], default_all=True)
        check('"שמור הכל" still returns the full key list',
              isinstance(allk, list) and len(allk) > 0)

        # 3. the deliberate clean install still returns [] -- NOT None
        _gui.Dialog.select = lambda self, *a, **k: 2
        _gui.Dialog.yesno = lambda self, *a, **k: True
        clean_choice = keep.prompt(extras=[], default_all=True)
        check('"התקנה נקייה" still returns [] (a real, distinct choice)',
              clean_choice == [])
        check('and [] is NOT confused with the cancel signal',
              clean_choice is not None)

        # 4. cancelling the CHECKLIST goes back to the mode question, and if the
        #    user then cancels that, the whole thing aborts
        seq = [1, -1]
        _gui.Dialog.select = lambda self, *a, **k: seq.pop(0) if seq else -1
        _gui.Dialog.multiselect = lambda self, *a, **k: None
        check('checklist cancel steps back, then cancel aborts',
              keep.prompt(extras=[], default_all=True) is None)
    finally:
        _gui.Dialog.select, _gui.Dialog.multiselect, _gui.Dialog.yesno = \
            real_select, real_multi, real_yesno

    # 5. the caller must test `is None` BEFORE the falsy check, or an abort
    #    would fall into the clean-install branch and wipe the box
    src = open(os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                            'resources', 'libs', 'builds.py'), encoding='utf-8').read()
    call = src.index('keep_mod.prompt(')
    none_check = src.find('if keep_keys is None:', call)
    falsy_check = src.find('if not keep_keys:', call)
    check('caller handles the abort', none_check != -1)
    check('...and does so BEFORE the clean-install branch',
          -1 < none_check < falsy_check)
    check('...by leaving the install loop, not proceeding',
          'continue' in src[none_check:falsy_check])


def test_services_connect_offer():
    """A freshly installed build has no debrid login, so offer to connect once.

    Asked of the DATA, not of the user's keep choice -- that also covers "kept
    debrid, but there was never a token". The offer must appear after a first
    install and after a reinstall where debrid was NOT kept, and must stay quiet
    when a credential exists.

    The trap: the 'already asked' flag lives in the wizard's addon_data, which
    the wipe deliberately PRESERVES. A flag written on the old build would
    survive into the new one and swallow the question exactly when the box has
    no credentials -- the same shape as the marker-gated seed bug. install_build
    must therefore clear it, which is what keeps this to ONE ask per install."""
    print("\n=== install: offer to connect a service when there is no login ===")
    import importlib.util
    import xbmcgui as _gui
    import xbmc as _xbmc

    # service.py raises SystemExit at IMPORT when the build marker is missing
    # ("firstrun will handle launch"). SystemExit is not an Exception, so the
    # runner cannot catch it -- without the marker this test silently ends the
    # whole suite instead of failing.
    import xbmcvfs as _vfs
    marker = os.path.join(_vfs.translatePath('special://home/'), '.masterkodi_il_done')
    if not os.path.exists(marker):
        with open(marker, 'w', encoding='utf-8') as fh:
            fh.write('1')

    sp = importlib.util.spec_from_file_location(
        'mk_wizard_service',
        os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard', 'service.py'))
    svc = importlib.util.module_from_spec(sp)
    try:
        sp.loader.exec_module(svc)
    except SystemExit:
        check('wizard service importable (build marker present)', False)
        return

    import resources.libs.keep as keep_mod
    povdir = os.path.dirname(keep_mod.POV_SETTINGS)
    os.makedirs(povdir, exist_ok=True)

    # the offer only fires when an ENGINE is installed -- there is nothing to
    # connect otherwise. Give the temp home a POV addon.xml so get_addon_version
    # finds one (this also pins that the POV branch is reachable).
    eng = os.path.join(svc.ADDONS_PATH, 'plugin.video.pov')
    os.makedirs(eng, exist_ok=True)
    with open(os.path.join(eng, 'addon.xml'), 'w', encoding='utf-8') as fh:
        fh.write('<addon id="plugin.video.pov" version="6.08.10"/>')

    def write_pov(tb_token):
        with open(keep_mod.POV_SETTINGS, 'w', encoding='utf-8') as fh:
            fh.write('<settings version="2">\n'
                     '    <setting id="tb.token">%s</setting>\n'
                     '</settings>\n' % tb_token)

    # 1. the detector
    write_pov('')
    check('no token -> not connected', svc._has_debrid() is False)
    write_pov('empty_setting')
    check("Gears' 'empty_setting' placeholder is NOT a login",
          svc._has_debrid() is False)
    write_pov('a-real-looking-token')
    check('a real token -> connected', svc._has_debrid() is True)

    # 2. the offer itself
    ran, asked = [], []
    real_yesno, real_exec, real_cond = _gui.Dialog.yesno, _xbmc.executebuiltin, _xbmc.getCondVisibility
    _xbmc.getCondVisibility = lambda c: True          # home screen is up
    _xbmc.executebuiltin = lambda c, *a: ran.append(c)

    def fake_yesno(self, *a, **k):
        asked.append(a[1] if len(a) > 1 else '')
        return True
    _gui.Dialog.yesno = fake_yesno

    class Mon(object):
        def abortRequested(self): return False
        def waitForAbort(self, t=0): return False

    try:
        write_pov('')                                   # no credential
        svc.ADDON.setSetting('services_prompt_done', 'false')
        svc._offer_services_connect(Mon())
        check('offered when there is no login', len(asked) == 1)
        check('...and it opens a services entry point',
              bool(ran) and 'RunPlugin(' in ran[0]
              and ('myservices' in ran[0] or 'torbox.authenticate' in ran[0]))

        # 3. asked ONCE -- a second boot stays quiet
        del asked[:], ran[:]
        svc._offer_services_connect(Mon())
        check('never asked twice on a later boot', not asked)

        # 4. a reinstall must RE-ARM it: install_build clears the flag
        src = open(os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                                'resources', 'libs', 'builds.py'), encoding='utf-8').read()
        i = src.index("ADDON.setSetting('skip_update_check', 'true')")
        j = src.index('def ', i) if 'def ' in src[i:] else len(src)
        check('install_build re-arms the offer (survives the addon_data wipe)',
              "setSetting('services_prompt_done', 'false')" in src[i:j])

        # 5. with a credential present it stays quiet even when re-armed
        del asked[:], ran[:]
        write_pov('a-real-looking-token')
        svc.ADDON.setSetting('services_prompt_done', 'false')
        svc._offer_services_connect(Mon())
        check('stays quiet when a login already exists', not asked)
    finally:
        _gui.Dialog.yesno, _xbmc.executebuiltin, _xbmc.getCondVisibility = \
            real_yesno, real_exec, real_cond

    # 6. the flag has to be declared, or Kodi has nothing to persist
    st = open(os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                           'resources', 'settings.xml'), encoding='utf-8').read()
    check('services_prompt_done is declared', 'services_prompt_done' in st)
    check('shipped settings still carry no XML comment', '<!--' not in st)


def test_sdr_filter_against_real_sources():
    """The SDR-only filter, measured against 3,240 REAL release names.

    The filter is entirely our own code -- clean upstream POV 6.08.13 and Gears
    2.4.2 have no _hdr_tags and no _is_hdr_item -- so this only depends on
    upstream through the badge builder, which is exercised live on purpose.

    Both directions matter:
      * it must hide everything HDR/DV, or a non-HDR display shows washed-out
        (HDR10) or green/purple (DV profile 5) pictures
      * it must NOT hide plain SDR, or real watchable sources disappear

    Corpus frozen in fixtures/hdr_corpus.txt (public torrentio index, 14
    shows/films + 45 TMDb-resolved movies, 2026-08-22). Measured when written:
    599 filtered, 0 missed against a deliberately broad reference, POV and Gears
    identical on all 3,240.
    """
    print(chr(10) + '=== SDR filter vs 3,240 real release names ===')
    import ast as _ast
    import builtins
    import re as _re

    def predicate(engine):
        """Lift the SHIPPED _is_hdr_item out of the overlay and run it as-is."""
        p = os.path.join(REPO, 'overlays', 'plugin.video.%s' % engine, 'files',
                         'resources', 'lib', 'windows', 'sources.py')
        src = open(p, encoding='utf-8').read()
        fn = tags = words = None
        for n in _ast.walk(_ast.parse(src)):
            if isinstance(n, _ast.FunctionDef) and n.name == '_is_hdr_item':
                fn = _ast.get_source_segment(src, n)
            if isinstance(n, _ast.Assign):
                for t in n.targets:
                    if isinstance(t, _ast.Name) and t.id == '_hdr_tags':
                        tags = _ast.literal_eval(n.value)
                    if isinstance(t, _ast.Name) and t.id == '_hdr_words':
                        words = _ast.literal_eval(n.value)
        ns = {}
        exec(compile(_ast.parse(fn.replace(chr(9), '    ')), 'pred', 'exec'), ns)

        class S(object):
            _hdr_tags, _hdr_words = tags, words
        S._is_hdr_item = ns['_is_hdr_item']
        return S(), tags, words

    class _Stub(object):
        def __call__(self, *a, **k): return self
        def __getattr__(self, _): return self
        def __iter__(self): return iter(())
        def __bool__(self): return False
        def __mro_entries__(self, b): return (object,)

    class _G(dict):
        def __missing__(self, k):
            if hasattr(builtins, k):
                raise KeyError(k)
            return _Stub()

    def badge_builder():
        """POV's own get_file_info -- where the [B]HDR[/B] badges come from."""
        p = os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources', 'lib',
                         'modules', 'source_utils.py')
        body = []
        for line in open(p, encoding='utf-8').read().split(chr(10)):
            s = line.strip()
            body.append(line[:len(line) - len(line.lstrip())] + 'pass'
                        if s.startswith(('import ', 'from ')) and s != 'import re'
                        else line)
        g = _G({'re': _re, '__name__': 'su', '__builtins__': builtins})
        exec(compile(chr(10).join(body), 'su', 'exec'), g)
        return g['get_file_info']

    class Item(object):
        """Both engines uppercase the release name before storing it."""
        def __init__(self, name, extra):
            self._n, self._e = name.upper(), extra

        def getProperty(self, key):
            return self._n if 'name' in key else self._e

    pov, tags, words = predicate('pov')
    gears, gtags, gwords = predicate('gears')
    check('POV and Gears ship the SAME tag/word lists',
          tags == gtags and words == gwords)
    check("the engine's HDR10+ badge is covered", '[B]HDR10+[/B]' in tags)

    gfi = badge_builder()

    def norm(n):
        return '.' + _re.sub(r'[^a-z0-9+]+', '.',
                             n.lower().replace('&', 'and')).strip('.') + '.'

    fixture = os.path.join(REPO, 'tools', 'tests', 'fixtures', 'hdr_corpus.txt')
    names = [l for l in open(fixture, encoding='utf-8').read().split(chr(10))
             if l and not l.startswith('#')]
    check('corpus present (3,000+ real names)', len(names) > 3000)

    REF = _re.compile(r'(?:\b|_|\.)(hdr|hdr10|hdr10\+|hdr10plus|hdrplus|dv|dovi|'
                      r'dvhe|dvh1|dolby\.?vision|hlg|pq|dvp[5-9])(?:\b|_|\.)', _re.I)
    SDR = _re.compile(r'(?:\b|\.|_|\[)sdr(?:\b|\.|_|\])', _re.I)

    missed, split, filtered, pure_sdr_hidden = [], 0, 0, []
    for n in names:
        try:
            info = gfi(norm(n), None)
        except Exception:
            info = ('', '')
        extra = info[1] if isinstance(info, (tuple, list)) and len(info) > 1 else ''
        o = bool(pov._is_hdr_item(Item(n, extra)))
        g2 = bool(gears._is_hdr_item(Item(n, extra)))
        filtered += o
        if o != g2:
            split += 1
        if REF.search(n) and not o:
            missed.append(n)
        if o and SDR.search(n) and not REF.search(n):
            pure_sdr_hidden.append(n)

    check('POV and Gears agree on all %d names' % len(names), split == 0)
    check('nothing HDR/DV slips through', not missed)
    check('no PURE-SDR release is ever hidden', not pure_sdr_hidden)
    check('still filters a substantial share (sanity: >300)', filtered > 300)

    # Self-contradictory names ("SDR DV", "[HLG HDR SDR]") cannot be resolved
    # from metadata. They stay FILTERED deliberately: wrongly showing DV on a
    # non-HDR display gives green/purple garbage, wrongly hiding one costs a
    # single row out of dozens.
    mixed = [n for n in names if SDR.search(n) and REF.search(n)]
    check('ambiguous SDR+HDR names are a handful, not a class',
          0 < len(mixed) <= 10)

    EDGE = [('Movie.2024.2160p.WEB-DL.DV.HDR.H265-GRP', True),
            ('Show.S01E01.2160p.DoVi.HDR10.HEVC-GRP', True),
            ('Movie.2024.2160p.WEB-DL.HDR10+.HEVC-GRP', True),
            ('Movie.2024.DVDRip.XviD-GROUP', False),
            ('Movie.2024.HDRip.x264-GROUP', False),
            ('Movie.2024.1080p.WEB.H264-DVSUX', False),
            ('Movie.2024.1080p.BluRay.DTS-HD.MA.5.1.x264-GRP', False)]
    bad = [n for n, want in EDGE
           if bool(pov._is_hdr_item(Item(n, ''))) != want]
    check('edge cases (DVDRip / HDRip / DTS-HD stay visible)', not bad)
    if bad:
        print('       wrong verdict for: %s' % bad)


def test_persistent_sdr_filter():
    """The SDR answer has to STICK -- and never leave a user with no sources.

    A display that cannot show HDR is a property of the living room, so the
    engines' one-off "הצג SDR בלבד" filter meant re-applying it on every single
    search. The persistent form keys on the ENGINES' OWN pair of settings
    (POV filter_hdr/filter_dv, Gears filter.hdr/filter.dv, both = Exclude),
    which is also what the wizard writes, so there is exactly one switch.

    Everything below is executed, not read: the gate, the empty-list guard and
    the filterless-search escape hatch all run against fake listitems.
    """
    print(chr(10) + '=== persistent SDR filter (both engines + the wizard) ===')
    import ast as _ast
    import glob as _glob
    import re as _re

    ENGINE_IDS = {'pov': ('filter_hdr', 'filter_dv'),
                  'gears': ('gears.filter.hdr', 'gears.filter.dv')}

    def lift(engine):
        """Run the SHIPPED methods, with get_setting/home-property stubbed."""
        p = os.path.join(REPO, 'overlays', 'plugin.video.%s' % engine, 'files',
                         'resources', 'lib', 'windows', 'sources.py')
        src = open(p, encoding='utf-8').read()
        want = ('_is_hdr_item', '_sdr_persistent_on', '_sdr_only_enabled', '_apply_sdr_only')
        got = {}
        for n in _ast.walk(_ast.parse(src)):
            if isinstance(n, _ast.FunctionDef) and n.name in want:
                got[n.name] = _ast.get_source_segment(src, n)
            if isinstance(n, _ast.Assign):
                for t in n.targets:
                    if isinstance(t, _ast.Name) and t.id in ('_hdr_tags', '_hdr_words'):
                        got[t.id] = _ast.literal_eval(n.value)
        check('%s: ships _sdr_only_enabled + _apply_sdr_only' % engine,
              all(k in got for k in want))
        if not all(k in got for k in want):
            return None, src

        settings, home = {}, {}
        ns = {'get_setting': lambda sid, d=None: settings.get(sid, d)}
        for name in want:
            exec(compile(_ast.parse(got[name].replace(chr(9), '    ')), name, 'exec'), ns)

        class S(object):
            _hdr_tags, _hdr_words = got['_hdr_tags'], got['_hdr_words']
            item_list = []

            def _home_prop(self, key):
                return home.get(key, '')

            def get_home_property(self, key):       # Gears' name for the same thing
                return home.get(key, '')

        for name in want:
            setattr(S, name, ns[name])
        return (S(), settings, home), src

    class Item(object):
        def __init__(self, name, extra=''):
            self._n, self._e = name.upper(), extra

        def getProperty(self, key):
            return self._n if 'name' in key else self._e

    HDR = Item('Movie.2024.2160p.WEB-DL.DV.HDR.HEVC-GRP')
    HDR10P = Item('Movie.2024.2160p.WEB-DL.HDR10+.HEVC-GRP')   # upstream's blind spot
    HLG = Item('Movie.2024.2160p.WEB-DL.HLG.HEVC-GRP')         # and its other one
    SDR1 = Item('Movie.2024.1080p.BluRay.x264-GRP')
    SDR2 = Item('Movie.2024.720p.WEB.H264-GRP')

    for engine, (hdr_id, dv_id) in ENGINE_IDS.items():
        lifted, src = lift(engine)
        if not lifted:
            continue
        win, settings, home = lifted

        # the gate: BOTH excluded, nothing less
        win.item_list = [HDR, SDR1]
        win._apply_sdr_only()
        check('%s: default (both Include) changes nothing' % engine,
              win.item_list == [HDR, SDR1])

        settings[hdr_id] = '1'
        win.item_list = [HDR, SDR1]
        win._apply_sdr_only()
        check('%s: HDR-only Exclude does NOT trigger it (hybrids are upstream\'s '
              'call)' % engine, win.item_list == [HDR, SDR1])

        settings[dv_id] = '1'
        win.item_list = [HDR, SDR1, HDR10P, SDR2, HLG]
        win._apply_sdr_only()
        check('%s: both Exclude -> HDR/DV gone, SDR kept' % engine,
              win.item_list == [SDR1, SDR2])
        check('%s: catches HDR10+ and HLG, which upstream\'s badge pass misses'
              % engine, HDR10P not in win.item_list and HLG not in win.item_list)

        # the guard that matters most: never hand back an empty window
        win.item_list = [HDR, HDR10P]
        win._apply_sdr_only()
        check('%s: all-HDR list is shown IN FULL, never emptied' % engine,
              win.item_list == [HDR, HDR10P])

        # the user explicitly asked for a filterless search -> stay out of it
        home['fs_filterless_search'] = 'true'
        win.item_list = [HDR, SDR1]
        win._apply_sdr_only()
        check('%s: a filterless search is left alone' % engine,
              win.item_list == [HDR, SDR1])
        home.clear()

        # applied where the list is BUILT, so a cleared manual filter re-applies
        build = {'pov': 'self.item_list = list(builder())',
                 'gears': 'self.item_list = list(builder(self.results))'}[engine]
        after = src.split(build, 1)[1][:200] if build in src else ''
        check('%s: applied straight after the item_list build' % engine,
              '_apply_sdr_only()' in after)
        check('%s: applied exactly once' % engine, src.count('self._apply_sdr_only()') == 1)

    # the ids the windows read must be the ids the engines DECLARE
    pov_settings = open(os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources',
                                     'settings.xml'), encoding='utf-8').read()
    check('POV declares filter_hdr/filter_dv',
          all('id="%s"' % s in pov_settings for s in ('filter_hdr', 'filter_dv')))
    # Read the OVERLAY, not the mirror: addons/plugin.video.gears is gitignored
    # (CI builds it from the overlay), so on a fresh checkout the mirror does not
    # exist yet and a mirror-only path fails the run before it starts. Falls back
    # to the mirror for the case where only that is present.
    gears_cache = os.path.join(REPO, 'overlays', 'plugin.video.gears', 'files',
                               'resources', 'lib', 'caches', 'settings_cache.py')
    if not os.path.isfile(gears_cache):
        gears_cache = os.path.join(REPO, 'addons', 'plugin.video.gears', 'resources',
                                   'lib', 'caches', 'settings_cache.py')
    check('Gears settings declaration found', os.path.isfile(gears_cache))
    gears_defaults = open(gears_cache, encoding='utf-8').read() if os.path.isfile(gears_cache) else ''
    check('Gears declares filter.hdr/filter.dv',
          all("'setting_id': '%s'" % s in gears_defaults
              for s in ('filter.hdr', 'filter.dv')))

    # ...and the ids the WIZARD writes must be those same ids (one switch, not two)
    import resources.libs.sdr as _sdr
    sdr_src = open(os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                                'resources', 'libs', 'sdr.py'), encoding='utf-8').read()
    check('the installed-probe asks the filesystem, not xbmcaddon.Addon (which makes '
          'Kodi log an ERROR line for an addon that is simply not installed)',
          "'addons', addon_id, 'addon.xml'" in sdr_src)
    check('wizard writes POV\'s own ids', tuple(_sdr.POV_IDS) == ('filter_hdr', 'filter_dv'))
    check('wizard writes Gears\' own ids', tuple(_sdr.GEARS_IDS) == ('filter.hdr', 'filter.dv'))
    check('wizard uses Exclude=1 / Include=0, like both engines',
          (_sdr.EXCLUDE, _sdr.INCLUDE) == ('1', '0'))
    rows = _sdr._gears_rows(_sdr.EXCLUDE)
    check('wizard also writes the _name rows Gears shows in its settings screen',
          sorted(r[0] for r in rows) == ['filter.dv', 'filter.dv_name',
                                         'filter.hdr', 'filter.hdr_name'])
    check('and the display name matches the value', all(
        r[3] == 'Exclude' for r in rows if r[0].endswith('_name')))

    # A variant re-apply (skin switch, content-variant bump) rewrites POV's
    # settings.xml from the shipped copy, which carries the filter as 0. Run the
    # real merge and prove the user's own answer survives it -- otherwise the
    # question quietly un-answers itself and he has to find it again.
    import resources.libs.content_source as _cs
    live = os.path.join(HOME, '_sdr_live_settings.xml')
    open(live, 'w', encoding='utf-8').write(
        '<settings>'
        '<setting id="filter_hdr">1</setting>'
        '<setting id="filter_dv">1</setting>'
        '<setting id="tb.token">USERTOKEN</setting>'
        '<setting id="results.sort_size">1</setting>'
        '</settings>')
    shipped = ('<settings>'
               '<setting id="filter_hdr" default="true">0</setting>'
               '<setting id="filter_dv" default="true">0</setting>'
               '<setting id="tb.token"></setting>'
               '<setting id="results.sort_size">0</setting>'
               '</settings>').encode('utf-8')
    merged = _cs._merge_preserve_creds(shipped, live).decode('utf-8')
    check('variant re-apply keeps the user\'s SDR answer',
          '<setting id="filter_hdr">1</setting>' in merged
          and '<setting id="filter_dv">1</setting>' in merged)
    check('...and still keeps his debrid token',
          '<setting id="tb.token">USERTOKEN</setting>' in merged)
    check('...while a normal build default still wins',
          '<setting id="results.sort_size">0</setting>' in merged)
    variants = _glob.glob(os.path.join(REPO, 'config-variants', '*', 'pov', 'settings.xml'))
    shipped_on = [v for v in variants
                  if _re.search('id="filter_(hdr|dv)"[^>]*>1<', open(v, encoding='utf-8').read())]
    check('no variant ships the filter ON (a fresh box shows everything)',
          not shipped_on)


def test_sdr_switch_writes_both_engines():
    """The switch has to land in BOTH engines and take effect WITHOUT a restart.

    Each engine reads through a cache mirrored into HOME window properties (POV
    one JSON blob, Gears one property per id, read BEFORE the db). A write that
    only touches disk leaves the user flipping the switch, opening a source list
    and seeing nothing change -- the same trap that made Gears' external-scraper
    enforcement silently do nothing (2026-08-02).

    Gears' settings.db does not exist until Gears has run once, which on a fresh
    install is after the question is asked, so the no-db path must DEFER rather
    than report success.
    """
    print(chr(10) + '=== the SDR switch: both engines, live, and deferrable ===')
    import json as _json
    import sqlite3 as _sq
    import xbmcaddon as _addon
    import xbmcgui as _gui

    import resources.libs.sdr as _sdr

    # the probe looks on DISK, so present the engines the way a real box does
    for _aid in ('plugin.video.pov', 'plugin.video.gears'):
        _d = os.path.join(HOME, 'addons', _aid)
        os.makedirs(_d, exist_ok=True)
        open(os.path.join(_d, 'addon.xml'), 'w', encoding='utf-8').write(
            '<addon id="%s" version="1.0.0" name="x"/>' % _aid)
    check('the probe sees an engine that is on disk',
          _sdr._installed('plugin.video.pov') and _sdr._installed('plugin.video.gears'))
    check('...and never claims one that is absent',
          not _sdr._installed('plugin.video.nosuch'))

    dbdir = os.path.join(HOME, 'userdata', 'addon_data', 'plugin.video.gears', 'databases')
    os.makedirs(dbdir, exist_ok=True)
    db = _sdr.GEARS_SETTINGS_DB
    if os.path.exists(db):
        os.remove(db)
    con = _sq.connect(db)
    con.execute('CREATE TABLE settings (setting_id text not null unique, setting_type text, '
                'setting_default text, setting_value text)')       # the real schema
    con.execute("INSERT INTO settings VALUES ('filter.hdr','action','0','0')")
    con.execute("INSERT INTO settings VALUES ('filter.hdr_name','name','','Include')")
    con.commit(); con.close()

    win = _gui.Window(10000)
    win.setProperty('pov_settings', _json.dumps({'filter_hdr': '0', 'filter_dv': '0',
                                                'meta_language': 'he'}))
    win.setProperty('gears.filter.hdr', '0')

    res = _sdr.apply_sdr_only(True)
    check('POV written', res['pov'] is True)
    check('Gears written', res['gears'] is True)
    check('nothing reported as failed', not _sdr.failures(res))

    check('POV settings hold Exclude',
          all(_addon.Addon('plugin.video.pov').getSetting(s) == '1' for s in _sdr.POV_IDS))
    blob = _json.loads(win.getProperty('pov_settings'))
    check('POV live cache patched -> no restart needed',
          blob['filter_hdr'] == '1' and blob['filter_dv'] == '1')
    check('and the rest of the POV cache is untouched', blob['meta_language'] == 'he')

    con = _sq.connect(db)
    rows = dict(con.execute('SELECT setting_id, setting_value FROM settings').fetchall())
    types = dict(con.execute('SELECT setting_id, setting_type FROM settings').fetchall())
    con.close()
    check('Gears db holds Exclude for both ids',
          rows.get('filter.hdr') == '1' and rows.get('filter.dv') == '1')
    check('Gears settings screen shows the matching label',
          rows.get('filter.hdr_name') == 'Exclude' and rows.get('filter.dv_name') == 'Exclude')
    check('rows keep the types Gears expects',
          types.get('filter.dv') == 'action' and types.get('filter.dv_name') == 'name')
    check('Gears live property mirrored -> no restart needed',
          win.getProperty('gears.filter.hdr') == '1')

    check('status() reads it back off the box', _sdr.status() == {'pov': True, 'gears': True})
    check('is_enabled() agrees', _sdr.is_enabled() is True)

    # turning it back off is the same path in reverse
    off = _sdr.apply_sdr_only(False)
    check('switching off writes Include everywhere', not _sdr.failures(off))
    con = _sq.connect(db)
    back = dict(con.execute('SELECT setting_id, setting_value FROM settings').fetchall())
    con.close()
    check('db back to Include', back.get('filter.hdr') == '0' and back.get('filter.dv') == '0')
    check('label follows the value back', back.get('filter.hdr_name') == 'Include')
    check('status() says off', _sdr.is_enabled() is False)

    # fresh install: Gears' db is not born yet -> defer, never claim success
    os.remove(db)
    pending = os.path.join(HOME, 'userdata', 'addon_data',
                           'plugin.program.masterkodi.il.wizard', 'gears_keep_pending.json')
    if os.path.exists(pending):
        os.remove(pending)
    res = _sdr.apply_sdr_only(True)
    check('no db -> reported as deferred, not as written', res['gears'] == 'deferred')
    check('deferred is not counted as a failure', not _sdr.failures(res))
    check('values handed to the first-boot catch-up', os.path.isfile(pending))
    if os.path.isfile(pending):
        stashed = _json.load(open(pending, encoding='utf-8-sig'))
        check('and the catch-up carries both ids',
              stashed.get('filter.hdr') == '1' and stashed.get('filter.dv') == '1')
    check('POV still applied even though Gears had to wait', res['pov'] is True)


def test_upstream_watch_urls():
    """The watcher has to ask upstream for the RIGHT file, or its verdict is a lie.

    Two defects, both live until 2026-08-24:

      * a tag stored as "v3.2.13" went into a template that already spells the v
        -> ".../tags/vv3.2.13" -> 404 every scheduled run, which aborted the
        whole --all-safe loop and discarded the OTHER fleet's adoption with it.
      * Zephyr's URLs were pinned to a literal v1.1.9, with no placeholder. The
        classifier downloads "old" and "new" and diffs them -- both resolved to
        the SAME url, so it compared the file with itself and reported SAFE. The
        real answer, once the urls were templated, is MANUAL on both fleets (four
        overlaid files changed on Piers).

    So this checks the url a base.json actually produces, for every overlay in
    both fleets -- no network needed.
    """
    print(chr(10) + '=== upstream watch: the urls it asks for ===')
    import glob as _glob
    import io as _io
    import json as _json
    import zipfile as _zip
    sys.path.insert(0, os.path.join(REPO, 'tools'))
    import check_upstream as _cu

    check('a leading v is stripped, so a template that spells v cannot double it',
          _cu.url_version({'base_version': '1.0'}, 'v3.2.13') == '3.2.13')
    check('the TAG wins over the addon version, where they differ',
          _cu.url_version({'base_version': '1.0.51', 'upstream_tag': '1.1.9'}) == '1.1.9')
    check('with no tag it falls back to the addon version',
          _cu.url_version({'base_version': '6.08.13'}) == '6.08.13')

    # the real base.json files, in both fleets
    bad, checked = [], 0
    for p in sorted(_glob.glob(os.path.join(REPO, 'overlays*', '*', 'base.json'))):
        b = _json.loads(open(p, encoding='utf-8').read())
        if b.get('base_type') in ('local_committed', 'kodi_bundled'):
            continue
        url = b['base_zip_url'].format(version=_cu.url_version(b))
        checked += 1
        where = os.sep.join(p.split(os.sep)[-3:-1])
        if '/vv' in url or 'vv%s' % _cu.url_version(b) in url:
            bad.append('%s -> %s' % (where, url))
        # a url pinned to a literal version can never follow an adoption
        tag = _cu.url_version(b)
        if '{version}' not in b['base_zip_url'] and tag not in b['base_zip_url']:
            bad.append('%s: url is pinned and does not match the tracked tag %s' % (where, tag))
    check('every overlay resolves a sane base url (%d checked)' % checked, not bad)
    for x in bad:
        print('       %s' % x)

    # the url has to MOVE when the tag moves, or adopting is a no-op
    stuck = []
    for p in sorted(_glob.glob(os.path.join(REPO, 'overlays*', '*', 'base.json'))):
        b = _json.loads(open(p, encoding='utf-8').read())
        if b.get('base_type') in ('local_committed', 'kodi_bundled'):
            continue
        if '{version}' not in b['base_zip_url']:
            stuck.append(os.sep.join(p.split(os.sep)[-3:-1]))
    check('no overlay is pinned to a hardcoded release url', not stuck)
    for x in stuck:
        print('       pinned: %s' % x)

    # the version must come from the zip, never from the tag
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, 'w') as z:
        z.writestr('skin.x-1.1.10/addon.xml',
                   '<?xml version="1.0"?><addon id="skin.x" version="1.0.52" name="X"/>')
    check('the addon version is read out of the zip, not assumed from the tag',
          _cu.addon_version_in_zip(buf.getvalue(), 'skin.x') == '1.0.52')
    check('an unreadable zip returns None rather than a wrong version',
          _cu.addon_version_in_zip(b'not a zip', 'skin.x') is None)

    adopt = open(os.path.join(REPO, 'tools', 'adopt_upstream.py'), encoding='utf-8').read()
    check('adopting moves the tag AND takes the version from the zip',
          "base['upstream_tag'] = cu.url_version(base, tgt)" in adopt
          and 'cu.addon_version_in_zip' in adopt)
    check('an overlay can opt out of unattended adoption',
          "base.get('auto_adopt') is False" in adopt)
    check('one overlay failing cannot abort the whole run',
          'except Exception as e:' in adopt and 'failed.append(name)' in adopt)

    wf = open(os.path.join(REPO, '.github', 'workflows', 'upstream-watch.yml'),
              encoding='utf-8').read()
    check('the run still fails on an adopt error -- but AFTER the commit step',
          'Report adopt failures' in wf
          and wf.index('Commit & push adopted bases') < wf.index('Report adopt failures'))

    # The issue text must name the ADDON version, not just the release tag.
    # "1.1.9 -> 1.1.10" is the tag; the Omega fleet installs 1.0.52 and would
    # never show that number anywhere (Asaf, 2026-08-24).
    cu_src = open(os.path.join(REPO, 'tools', 'check_upstream.py'), encoding='utf-8').read()
    check('the checker carries the addon version alongside the tag',
          "res['latest_addon']" in cu_src and "'current_addon'" in cu_src)
    check('the update line spells out both when they differ',
          "'release tag %s, addon %s -> %s'" in cu_src)
    check('the up-to-date line does too',
          "'tag %s, addon %s'" in cu_src)
    check('...and stays terse when tag == addon version',
          "if la and (la != r['latest'] or (ca and ca != r['current']))" in cu_src)

    # A file we overlay for NO reason is not free: addon.xml changes in every
    # upstream release, so overlaying it purely to set a version -- which
    # _stamp_build_suffix rewrites at build time anyway -- made every Zephyr
    # release classify MANUAL and blocked auto-adoption. Catch a re-introduction.
    stamp = open(os.path.join(REPO, 'tools', 'apply_overlay.py'), encoding='utf-8').read()
    check('the build still stamps the version itself (the reason ours was moot)',
          'def _stamp_build_suffix' in stamp and "100 + count" in stamp)
    pointless = []
    for op in sorted(_glob.glob(os.path.join(REPO, 'overlays*', '*', 'files', 'addon.xml'))):
        txt = open(op, encoding='utf-8', errors='replace').read()
        who = os.sep.join(op.split(os.sep)[-4:-2])
        # ours is pointless if the ONLY thing it can contribute is the version:
        # no extra imports, extension points or metadata of our own
        marks = ('KODIRDIL', 'kodirdil')
        if any(m in txt for m in marks):
            continue
        pointless.append(who)
    # informational: the ones left must each justify themselves (AF3-Piers bumps
    # xbmc.gui, Estuary/Nimbus are whole-tree bases)
    check('zephyr no longer overlays addon.xml on either fleet',
          not any('zephyr' in w for w in pointless))
    print('       addon.xml still overlaid by: %s' % (', '.join(pointless) or '(none)'))

    # An overlay with no watchable upstream must be skipped, not crashed on.
    # Nimbus has no base_version at all, so adopt's "already at ..." line raised
    # KeyError -- invisible while the loop was dying earlier on the AF3 404, and
    # the very next failure once that was fixed.
    import tempfile as _tf
    import adopt_upstream as _au
    tmp = _tf.mkdtemp()
    od = os.path.join(tmp, 'skin.nowatch')
    os.makedirs(od)
    open(os.path.join(od, 'base.json'), 'w', encoding='utf-8').write(
        _json.dumps({'addon_id': 'skin.nowatch', 'base_type': 'local_committed',
                     'overlay_version': '1.0.0'}))
    try:
        status = _au.adopt(od, target=None, force=False, do_build=False)
        crashed = None
    except Exception as e:
        status, crashed = None, e
    check('an overlay with no watchable upstream is skipped, not crashed on',
          crashed is None and status == 'up-to-date')
    if crashed:
        print('       raised: %r' % (crashed,))

    # The opt-out lever exists and works -- tested on a synthetic overlay rather
    # than pinned to a real one, because no overlay should need it now: adopt
    # handles one-tag-two-assets by formatting the url from the TAG and reading
    # the version out of the downloaded zip (Zephyr v1.1.10 -> Omega 1.0.52,
    # Piers 1.1.10, verified against the real release).
    od2 = os.path.join(tmp, 'skin.optout')
    os.makedirs(od2)
    open(os.path.join(od2, 'base.json'), 'w', encoding='utf-8').write(
        _json.dumps({'addon_id': 'skin.optout', 'base_version': '1.0.0',
                     'auto_adopt': False,
                     'base_zip_url': 'https://example.invalid/{version}.zip',
                     'upstream_addons_xml': 'https://example.invalid/addons.xml'}))
    try:
        st2, crashed2 = _au.adopt(od2, target='9.9.9', force=False, do_build=False), None
    except Exception as e:
        st2, crashed2 = None, e
    check('auto_adopt:false holds an overlay back without erroring',
          crashed2 is None and st2 in ('manual', 'up-to-date'))
    check('...and an unreachable upstream cannot turn that into a failure',
          st2 != 'error')


def test_continue_watching_row():
    """One row that both resumes and advances -- executed, not eyeballed.

    POV keeps two lists fed by two tables: `progress` (resume points, which are
    DELETED when you finish an episode) and `watched_status` (last watched per
    show, +1). Every widget in this build pointed at the first one, so finishing
    an episode made the series vanish instead of moving to the next episode
    (Asaf, 2026-08-24).

    Our route drives POV's own builder twice and merges. What is pinned here is
    the merge itself: one entry per show, resume beating next, recency ordering,
    unaired never first, and no crash when a half fails.
    """
    print(chr(10) + '=== continue watching: resume + next, in one row ===')
    import ast as _ast

    SRC = os.path.join(REPO, 'overlays', 'plugin.video.pov', 'files', 'resources',
                       'lib', 'kodirdil', 'continue_watching.py')
    check('the module ships', os.path.isfile(SRC))
    if not os.path.isfile(SRC):
        return

    class _LI(object):
        """Just enough xbmcgui.ListItem for the sort keys."""
        def __init__(self, label=''):
            self.label, self._p = label, {}

        def getProperty(self, k): return self._p.get(k, '')
        def setProperty(self, k, v): self._p[k] = v
        def __repr__(self): return '<%s>' % self.label

    added = {}

    class _KU(object):
        """POV's kodi_utils, reduced to what the route touches."""
        @staticmethod
        def argv1(): return '1'
        @staticmethod
        def add_items(handle, items): added['items'] = list(items)
        @staticmethod
        def set_category(*a): added.setdefault('calls', []).append('category')
        @staticmethod
        def set_sort_method(*a): added.setdefault('calls', []).append('sort')
        @staticmethod
        def set_content(*a): added.setdefault('calls', []).append('content')
        @staticmethod
        def end_directory(*a): added.setdefault('calls', []).append('end')
        @staticmethod
        def set_view_mode(*a): added.setdefault('calls', []).append('view')

    class _FakeMenu(object):
        """Stands in for POV's episodes Menu: same seams, fake rendering.

        worker() reproduces the two behaviours our code depends on -- the
        in-progress half sorted by the `sort` index we hand it, the next half
        carrying last_played/unaired and POV's own ordering.
        """
        raise_on = None

        def __init__(self, params):
            self.params = params
            self.items, self.list, self.list_type = [], [], ''
            self.append = self.items.append
            self.bookmarks = params.get('_bookmarks', {})
            self.is_widget = False

        def _setup_next_episode(self, params_get):
            self.list_type = 'next_episode_pov'
            self.list = list(self.params.get('_next', []))

        def worker(self):
            if self.list_type == _FakeMenu.raise_on:
                raise RuntimeError('builder blew up')
            for pos, entry in enumerate(self.list):
                li = _LI('%s S%sE%s%s' % (entry.get('title') or entry['media_ids']['tmdb'],
                                          entry.get('season'), entry.get('episode'),
                                          '' if self.list_type == 'in_progress' else '+1'))
                if self.list_type.startswith('next_episode'):
                    li.setProperty('pov_last_played', entry.get('last_played', ''))
                    li.setProperty('pov_unaired', 'true' if entry.get('unaired') else 'false')
                else:
                    li.setProperty('pov_sort_order', str(entry.get('sort', pos)))
                    li.setProperty('pov_unaired', 'false')
                self.append(('url', li, False))
            if self.list_type.startswith('next_episode'):
                self.items.sort(key=lambda k: k[1].getProperty('pov_last_played'), reverse=True)
                self.items.sort(key=lambda k: k[1].getProperty('pov_unaired') == 'true')
            else:
                self.items.sort(key=lambda k: int(k[1].getProperty('pov_sort_order')))
            return self.items

    def load():
        """Exec the shipped module with its Kodi imports replaced."""
        body = []
        for line in open(SRC, encoding='utf-8').read().split(chr(10)):
            s = line.strip()
            body.append('pass' if s.startswith('from menus') or s.startswith('from modules')
                        else line)
        ns = {'Menu': _FakeMenu, 'kodi_utils': _KU, 'ls': lambda x: x,
              '__name__': 'continue_watching'}
        exec(compile(chr(10).join(body), 'continue_watching', 'exec'), ns)
        return ns['ContinueWatching']

    CW = load()

    def bm(tmdb, season, episode, last_played, title=None):
        """A `progress` row, in the real column order."""
        return ('episode', tmdb, season, episode, '42.0', '1200', last_played, 0,
                title or ('show%s' % tmdb))

    def nxt(tmdb, season, episode, last_played, unaired=False):
        # 'title' is only for a readable label in this harness -- the real
        # next-episode source carries media_ids/season/episode/last_played
        return {'media_ids': {'tmdb': tmdb}, 'season': season, 'episode': episode,
                'last_played': last_played, 'unaired': unaired,
                'title': 'show%s' % tmdb}

    def run(bookmarks, nexts):
        added.clear()
        _FakeMenu.raise_on = None
        cw = CW({'name': 'Continue Watching', '_bookmarks': bookmarks, '_next': nexts})
        cw.run()
        return [i[1].label for i in added.get('items', [])]

    # A: mid-episode AND the previous one watched -> ONE entry, the resume
    out = run({'a': bm('101', 1, 4, '2026-08-24 10:00:00')},
              [nxt('101', 1, 3, '2026-08-24 09:00:00')])
    check('a show being resumed appears exactly once', len(out) == 1)
    check('...and it is the resume entry, not the computed next one',
          out and not out[0].endswith('+1'))

    # B: finished an episode, no bookmark -> the NEXT episode shows up
    out = run({}, [nxt('202', 2, 5, '2026-08-23 20:00:00')])
    check('a finished show advances to the next episode',
          out == ['show202 S2E5+1'])

    # C: bookmark only, never finished anything -> still there
    out = run({'c': bm('303', 1, 1, '2026-08-22 18:00:00')}, [])
    check('a show you never finished an episode of is not lost',
          out == ['show303 S1E1'])

    # D: recency across BOTH halves
    out = run({'a': bm('101', 1, 4, '2026-08-20 10:00:00')},
              [nxt('202', 2, 5, '2026-08-24 22:00:00'),
               nxt('404', 1, 1, '2026-08-10 08:00:00')])
    check('ordered by recency across both halves, newest first',
          out == ['show202 S2E5+1', 'show101 S1E4', 'show404 S1E1+1'])

    # E: unaired sinks, however recent
    out = run({}, [nxt('505', 9, 9, '2026-08-24 23:59:00', unaired=True),
                   nxt('606', 1, 2, '2026-08-01 07:00:00')])
    check('an unaired next episode never ranks first',
          out == ['show606 S1E2+1', 'show505 S9E9+1'])

    # F: nothing at all -> no crash, and the directory is still closed
    out = run({}, [])
    check('empty everything is not a crash', out == [])
    check('the directory is ended even when empty',
          added.get('calls', []).count('end') == 1)

    # G: a movie bookmark is not an episode
    out = run({'m': ('movie', '777', '', '', '10', '60', '2026-08-24 12:00:00', 0, 'film')},
              [])
    check('a movie resume point never lands in the TV row', out == [])

    # H: no timestamp -> last, but not dropped
    out = run({'a': bm('101', 1, 4, '')},
              [nxt('202', 2, 5, '2026-08-01 10:00:00')])
    check('an entry with no timestamp sorts last but survives',
          out == ['show202 S2E5+1', 'show101 S1E4'])

    # I: one half failing must not take the row down with it
    added.clear()
    _FakeMenu.raise_on = 'in_progress'
    CW({'name': 'x', '_bookmarks': {'a': bm('101', 1, 4, '2026-08-24 10:00:00')},
        '_next': [nxt('202', 2, 5, '2026-08-23 10:00:00')]}).run()
    check('a failure in one half still shows the other',
          [i[1].label for i in added.get('items', [])] == ['show202 S2E5+1'])
    _FakeMenu.raise_on = None

    # J: dedupe is per SHOW, not per episode
    out = run({'a': bm('101', 3, 7, '2026-08-24 10:00:00')},
              [nxt('101', 1, 1, '2026-08-01 10:00:00')])
    check('dedupe is per show, whatever episode each half points at',
          out == ['show101 S3E7'])

    # the contract we depend on, checked against the SHIPPED upstream builder:
    # our two list_type strings must still hit the branches we expect
    ep = open(os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources', 'lib',
                           'menus', 'episodes.py'), encoding='utf-8').read()
    check("upstream still branches on list_type.startswith('next_episode')",
          "self.list_type.startswith('next_episode')" in ep)
    check("upstream's in-progress list_type is still 'in_progress'",
          "self.list_type = 'in_progress'" in ep)
    check("upstream's next-episode list_type is still 'next_episode_pov'",
          "self.list_type = 'next_episode_pov'" in ep)
    check('upstream still sorts the in-progress half by pov_sort_order',
          "getProperty('pov_sort_order')" in ep)
    check('upstream still stamps pov_last_played on the next half',
          "'pov_last_played'" in ep)
    check('upstream still marks unaired episodes', "props['pov_unaired']" in ep)

    entry = open(os.path.join(REPO, 'overlays', 'plugin.video.pov', 'files',
                              'resources', 'lib', 'entry.py'), encoding='utf-8').read()
    check('the route is registered', "'build_continue_watching'" in entry)
    check('...and points at our module',
          "kodirdil.continue_watching" in entry and 'ContinueWatching' in entry)

    # ---- the wiring: every POV row must point at the merged route ----------
    import glob as _glob
    import re as _re

    pov_files, gears_files = [], []
    for p in _glob.glob(os.path.join(REPO, 'config-variants', '**', '*'), recursive=True):
        if not os.path.isfile(p):
            continue
        try:
            txt = open(p, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        if 'plugin.video.pov' in txt or 'plugin.video.gears' in txt:
            (gears_files if 'gears' in os.path.basename(os.path.dirname(os.path.dirname(p)))
             or 'gears' in p.replace(os.sep, '/').split('config-variants/')[1].split('/')[0]
             else pov_files).append((p, txt))

    stale = [p for p, t in pov_files if 'build_in_progress_episode' in t
             or 'action=in_progress_tvshows&amp;iconImage=in_progress_tvshow' in t]
    check('no POV variant still points a row at the old episodes-only list',
          not stale)
    for p in stale:
        print('       still stale: %s' % p.replace(REPO + os.sep, ''))

    wired = [p for p, t in pov_files if 'build_continue_watching' in t]
    check('the merged route is wired into every POV skin (%d file(s))' % len(wired),
          len(wired) >= 8)
    skins = {p.replace(REPO + os.sep, '').split(os.sep)[1] for p in wired}
    check('...covering af3, estuary, nimbus and zephyr',
          {'af3-pov', 'af3-pov-tmdb', 'estuary-pov', 'nimbus-pov',
           'zephyr-pov', 'zephyr-pov-tmdb'} <= skins)

    # a Gears variant pointing at a route Gears does not have would be a dead row
    bad_gears = [p for p, t in gears_files if 'build_continue_watching' in t]
    check('no Gears variant points at a route only POV has', not bad_gears)

    # the defaults that make the row read right -- shipped, not just set on one
    # box. Each one earns its place: an unaired episode cannot be continued, the
    # three-part label is unreadable in Hebrew (bidi), Month-Day-Year is US
    # ordering in an Israeli build, and POV's own In Progress menu should agree
    # with the row above it. auto_resume_episode is deliberately NOT pinned --
    # Asaf wants the resume prompt.
    import re as _re2
    WANT = {'nextep.include_unaired': 'false', 'single_ep_display': '1',
            'single_ep_format': '0', 'sort.progress': '1'}
    wrong = []
    povset = _glob.glob(os.path.join(REPO, 'config-variants', '*', 'pov', 'settings.xml'))
    check('POV variant settings found', len(povset) >= 6)
    for sp in povset:
        st = open(sp, encoding='utf-8').read()
        for sid, val in WANT.items():
            m = _re2.search(r'<setting id="%s"[^>]*>([^<]*)</setting>' % _re2.escape(sid), st)
            got = m.group(1) if m else 'MISSING'
            if got != val:
                wrong.append('%s: %s=%s (want %s)'
                             % (sp.replace(REPO + os.sep, '').split(os.sep)[1], sid, got, val))
    check('every POV variant ships the row-friendly defaults', not wrong)
    for w in wrong:
        print('       %s' % w)
    novel = [sp for sp in povset
             if '<setting id="auto_resume_episode"' in open(sp, encoding='utf-8').read()
             and _re2.search(r'<setting id="auto_resume_episode"[^>]*>([^<]*)</setting>',
                             open(sp, encoding='utf-8').read()).group(1) != '0']
    check('the resume prompt is left alone (auto_resume_episode stays 0)', not novel)

    # the wizard ships its OWN seed copies of these rows -- a fresh install and
    # the "restore menu" flow read those, not the variants, so a row left behind
    # there quietly reinstates the old list on the next reinstall
    seeds = []
    for p2 in _glob.glob(os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                                      'resources', '**', '*'), recursive=True):
        if not os.path.isfile(p2):
            continue
        try:
            t2 = open(p2, encoding='utf-8', errors='replace').read()
        except Exception:
            continue
        if 'build_in_progress_episode' in t2:
            seeds.append(p2)
    check('the wizard ships no seed still pointing at the old list', not seeds)
    for p2 in seeds:
        print('       stale seed: %s' % p2.replace(REPO + os.sep, ''))

    # and the route must exist in the MIRROR that actually ships
    mirror_entry = os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources', 'lib', 'entry.py')
    mirror_mod = os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources', 'lib',
                              'kodirdil', 'continue_watching.py')
    check('the shipped mirror carries the route',
          os.path.isfile(mirror_entry)
          and "'build_continue_watching'" in open(mirror_entry, encoding='utf-8').read())
    check('the shipped mirror carries the module', os.path.isfile(mirror_mod))

    # Every mode inside a plugin.video.pov URL must be a route POV registers --
    # a typo there is a row that silently does nothing. Scoped to POV URLs on
    # purpose: the same files carry wizard and TMDb Helper modes too.
    modes = set()
    for _p, t in pov_files:
        for url in _re.findall(r'plugin://plugin\.video\.pov/\?[^"\',<)\]]*', t):
            modes.update(_re.findall(r'mode=([\w.]+)', url.replace('&amp;', '&')))
    # POV dispatches a mode either by exact match in its route table or by
    # PREFIX (navigator., torbox., build_trakt_, ...). Read the prefixes out of
    # entry.py rather than hardcoding them, so this stays true if upstream
    # reshuffles them.
    prefixes = tuple(_re.findall(r"mode\.startswith\('([^']+)'\)", entry))
    check('found POV\'s prefix routes to check against', len(prefixes) > 5)
    unknown = sorted(m for m in modes
                     if "'%s'" % m not in entry and not m.startswith(prefixes))
    check('every POV mode our variants reference is a registered route (%d checked)'
          % len(modes), not unknown)
    if unknown:
        print('       unknown modes: %s' % unknown)


def test_sdr_switch_in_the_filter_menu():
    """The switch has to be reachable -- and readable -- from the sources window.

    Once it is on, the engine drops HDR/DV BEFORE the window is built: nothing
    on screen says the list is filtered, and there is no way back without
    leaving the window. So the filter menu carries one row that both reports the
    state and flips it.

    Two traps this pins down:
      * the row must read the SETTING, not "are we filtering right now" -- during
        an explicit filterless search we stand down, but the switch is still on
        and the menu must say so.
      * Gears' settings_cache.set() does NOT strip a 'gears.' prefix (only get()
        does), so a prefixed write would silently create a second, dead row and
        the real setting would never change.
    """
    print(chr(10) + '=== the SDR switch, from inside the sources window ===')
    import ast as _ast

    ENGINES = {
        'pov': {'ids': ('filter_hdr', 'filter_dv'),
                'read': ('filter_hdr', 'filter_dv'),
                'home': '_home_prop'},
        'gears': {'ids': ('filter.hdr', 'filter.dv'),
                  'read': ('gears.filter.hdr', 'gears.filter.dv'),
                  'home': 'get_home_property'},
    }

    for engine, spec in ENGINES.items():
        p = os.path.join(REPO, 'overlays', 'plugin.video.%s' % engine, 'files',
                         'resources', 'lib', 'windows', 'sources.py')
        src = open(p, encoding='utf-8').read()

        want = ('_sdr_persistent_on', '_set_sdr_persistent')
        fns = {}
        for n in _ast.walk(_ast.parse(src)):
            if isinstance(n, _ast.FunctionDef) and n.name in want:
                fns[n.name] = _ast.get_source_segment(src, n)
        check('%s: ships the switch helpers' % engine, len(fns) == len(want))
        if len(fns) != len(want):
            continue

        import json as _json
        settings, written, home = {}, [], {}
        ns = {'get_setting': lambda sid, d=None: settings.get(sid, d),
              'set_setting': lambda sid, val: written.append((sid, val)),
              'json': _json}
        for name in want:
            exec(compile(_ast.parse(fns[name].replace(chr(9), '    ')), name, 'exec'), ns)

        class S(object):
            def _home_prop(self, k): return home.get(k, '')
            def get_home_property(self, k): return home.get(k, '')
            def _set_home_prop(self, k, v): home[k] = v
        for name in want:
            setattr(S, name, ns[name])
        win = S()

        check('%s: reports OFF by default' % engine, win._sdr_persistent_on() is False)
        settings[spec['read'][0]] = '1'
        check('%s: one filter alone is not "on"' % engine, win._sdr_persistent_on() is False)
        settings[spec['read'][1]] = '1'
        check('%s: both filters -> reports ON' % engine, win._sdr_persistent_on() is True)

        home['fs_filterless_search'] = 'true'
        check('%s: still reports ON during a filterless search (the SWITCH is on,'
              ' even though we stand down)' % engine, win._sdr_persistent_on() is True)
        home.clear()

        del written[:]
        win._set_sdr_persistent(True)
        check('%s: turning on writes BOTH ids, as Exclude' % engine,
              written == [(spec['ids'][0], '1'), (spec['ids'][1], '1')])
        del written[:]
        win._set_sdr_persistent(False)
        check('%s: turning off writes BOTH ids, as Include' % engine,
              written == [(spec['ids'][0], '0'), (spec['ids'][1], '0')])

        # The rows and the handlers, read out of the ACTUAL functions -- a
        # file-wide search would happily match the identical lines in the
        # automatic pass and prove nothing about the menu.
        bodies = {}
        for n in _ast.walk(_ast.parse(src)):
            if isinstance(n, _ast.FunctionDef) and n.name in ('filter_results',
                                                              'make_filter_items',
                                                              'filter_action'):
                bodies[n.name] = _ast.get_source_segment(src, n)
        rows = bodies.get('make_filter_items') or bodies.get('filter_results') or ''
        handlers = bodies.get('filter_action') or bodies.get('filter_results') or ''
        check('%s: menu code found' % engine, bool(rows) and bool(handlers))

        check('%s: the row reports the SWITCH, not "are we filtering now"' % engine,
              '_sdr_persistent_on()' in rows and '_sdr_only_enabled(' not in rows)
        check('%s: offers to turn it ON only while something is hideable' % engine,
              "'sdr_persist_on'" in rows
              and 'sdr_count < len(self.item_list)' in rows.replace('_sdr_count', 'sdr_count'))
        check('%s: offers the OFF row whenever the switch is on' % engine,
              "'sdr_persist_off'" in rows)
        check('%s: both choices are actually handled' % engine,
              handlers.count("== 'sdr_persist_on'") == 1
              and handlers.count("== 'sdr_persist_off'") == 1)
        on_branch = handlers[handlers.index("== 'sdr_persist_on'"):
                             handlers.index("== 'sdr_persist_off'")]
        check('%s: turning it on keeps the never-empty guard' % engine,
              'if sdr: self.item_list = sdr' in on_branch.replace('_sdr', 'sdr'))
        check('%s: turning it off tells the user a new search shows everything'
              % engine, 'חיפוש חדש יציג את כל המקורות' in handlers)
        if engine == 'gears':
            # Gears builds its filter list once, in __init__ -- both branches
            # have to rebuild it or the menu keeps describing the old state.
            check('gears: both branches rebuild the menu after toggling',
                  handlers.count('_refresh_after_sdr_toggle()') == 2)

    # POV alone needs the cache patch: Gears' own set_setting mirrors the value
    pov = open(os.path.join(REPO, 'overlays', 'plugin.video.pov', 'files', 'resources',
                            'lib', 'windows', 'sources.py'), encoding='utf-8').read()
    check('POV also patches its live settings blob (no restart needed)',
          "'pov_settings'" in pov and "d['filter_hdr']" in pov)
    check('POV can actually write settings (set_setting imported)',
          'from modules.kodi_utils import get_setting, set_setting' in pov)


def test_sdr_indicator_on_the_panel():
    """You must be able to see that the list is filtered WITHOUT opening a menu.

    With the switch on, the engine drops HDR/DV before the window exists, so the
    4K rows are simply absent and nothing says why. The panel marker is that
    answer -- and it is deliberately driven by "are we actually filtering this
    list" rather than by the switch: during an explicit filterless search we
    stand down, the list really is complete, and claiming otherwise would lie.

    The panel line itself is the one that has been rewritten repeatedly for bidi
    and width, so this also pins the mechanics: a real LRM before the Latin run,
    the marker appended LAST, and (Gears) inside the line rather than after the
    newline that separates it from "N Results".
    """
    print(chr(10) + '=== the "list is filtered" marker on the panel ===')
    MARK = 'מסנן: ללא ‎HDR/DV'
    START = '########### KODIRDIL - "this list is filtered" indicator'

    for engine, has_newline in (('pov', False), ('gears', True)):
        p = os.path.join(REPO, 'overlays', 'plugin.video.%s' % engine, 'files',
                         'resources', 'lib', 'windows', 'sources.py')
        src = open(p, encoding='utf-8').read()
        check('%s: ships the marker' % engine, START in src and MARK in src)
        if START not in src:
            continue

        block = src[src.index(START):]
        block = block[block.index(chr(10)) + 1:]
        block = block[:block.index('####################################################################')]
        # the block lives two tabs deep inside a method -- dedent it to run it
        body = chr(10).join(l[2:] if l.startswith(chr(9) * 2) else l
                            for l in block.split(chr(10)))

        class _Self(object):
            filtering = True
            def _sdr_only_enabled(self): return self.filtering
            def _sdr_persistent_on(self): return True

        def run(panel, filtering):
            me = _Self()
            me.filtering = filtering
            ns = {'self': me, 'hebrew_subtitles_panel_text': panel}
            exec(compile(body, 'marker', 'exec'), ns)
            return ns['hebrew_subtitles_panel_text']

        nl = chr(10) if has_newline else ''
        existing = 'נמצאו 2 כתוביות | התאמות: 4K:5' + nl

        out = run(existing, True)
        check('%s: marker appended to the existing panel text' % engine,
              MARK in out and out.startswith('נמצאו 2 כתוביות'))
        check('%s: marker comes LAST (Latin run at the end of the run)' % engine,
              out.rstrip(chr(10)).endswith('[/COLOR]'))
        check('%s: real LRM in front of the Latin' % engine, '‎' in out)
        if has_newline:
            check('gears: the "N Results" newline is preserved, and the marker is '
                  'INSIDE the line', out.endswith(chr(10)) and out.count(chr(10)) == 1)

        out_empty = run('', True)
        check('%s: works with no subtitle text at all' % engine,
              out_empty.strip(chr(10)) and MARK in out_empty)
        check('%s: no stray separator when there is nothing to separate' % engine,
              not out_empty.lstrip().startswith('|'))

        out_off = run(existing, False)
        check('%s: NOT shown when the list is not actually filtered '
              '(filterless search)' % engine, MARK not in out_off)
        check('%s: and the panel is otherwise untouched' % engine, out_off == existing)

        # colour discipline: this panel already assigns a meaning to every colour
        # it uses for subtitles -- the marker must not borrow one of them
        utils = open(os.path.join(REPO, 'overlays', 'plugin.video.%s' % engine, 'files',
                                  'resources', 'lib', 'kodirdil',
                                  'hebrew_subtitles_search_utils.py'), encoding='utf-8').read()
        marker_colour = block.split('[COLOR ')[1].split(']')[0]
        check('%s: marker colour is not one already meaning something else' % engine,
              '[COLOR %s]' % marker_colour not in utils)


def test_install_question_applies_the_answer():
    """Drive the install question itself, both answers, end to end.

    The dialog copy and the write path are separately covered; this proves the
    step in the middle -- that "לא, מסך רגיל" really turns the filter on, that
    "כן, תומך ב-HDR" turns it back off when it was on, and that answering yes on
    a box where it was never on writes NOTHING (so a reinstall cannot clobber a
    user's own Prefer/Sort choice)."""
    print(chr(10) + '=== install question: the answer is actually applied ===')
    import sqlite3 as _sq

    import resources.libs.builds as _builds
    import resources.libs.sdr as _sdr

    # the probe looks on DISK (constructing an Addon() for a missing addon makes
    # Kodi log an ERROR), so the sandbox has to look like a real install
    for _aid in ('plugin.video.pov', 'plugin.video.gears'):
        _d = os.path.join(HOME, 'addons', _aid)
        os.makedirs(_d, exist_ok=True)
        open(os.path.join(_d, 'addon.xml'), 'w', encoding='utf-8').write(
            '<addon id="%s" version="1.0.0" name="x"/>' % _aid)

    db = _sdr.GEARS_SETTINGS_DB
    os.makedirs(os.path.dirname(db), exist_ok=True)
    if os.path.exists(db):
        os.remove(db)
    con = _sq.connect(db)
    con.execute('CREATE TABLE settings (setting_id text not null unique, setting_type text, '
                'setting_default text, setting_value text)')
    con.commit(); con.close()
    _sdr.apply_sdr_only(False)                      # start from the shipped default
    check('starts off, like a fresh box', _sdr.is_enabled() is False)

    class _Dialog(object):
        def __init__(self, answer):
            self.answer, self.asked, self.oked = answer, 0, []

        def yesno(self, *a, **k):
            self.asked += 1
            return self.answer

        def ok(self, *a, **k):
            self.oked.append(a)

    mgr = _builds.BuildManager.__new__(_builds.BuildManager)   # no install side effects

    mgr.dialog = _Dialog(False)                     # "לא, מסך רגיל"
    mgr._ask_and_apply_sdr()
    check('the question was asked', mgr.dialog.asked == 1)
    check('"plain screen" turns the filter ON', _sdr.status() == {'pov': True, 'gears': True})
    check('and no error dialog was shown', not mgr.dialog.oked)

    mgr.dialog = _Dialog(True)                      # "כן, תומך ב-HDR" -- it WAS on
    mgr._ask_and_apply_sdr()
    check('"supports HDR" turns it back off', _sdr.is_enabled() is False)

    before = _sdr.status()
    mgr.dialog = _Dialog(True)                      # ...and again, already off
    mgr._ask_and_apply_sdr()
    check('answering "supports HDR" on an already-off box changes nothing',
          _sdr.status() == before)


def test_sdr_question_reads_right_in_hebrew():
    """Every Hebrew line we ship for this feature has to survive bidi, and the
    dialogs must not turn the filter on by a stray OK press.

    Two rules, both learned the hard way in this build:
      * a Latin run in the MIDDLE of a Hebrew line gets reordered -- the
        maintenance sizes and the sources panel both had to be rewritten for
        this (the panel rendered "התאמות 4" with an orphaned "K:3" elsewhere).
        Hebrew leads, ONE Latin run, at the end.
      * Kodi focuses NO by default. Here NO means "my TV is not HDR", so an
        install where the user just presses OK would silently start hiding
        sources. The focused button must be the one that changes nothing.
    """
    print(chr(10) + '=== the HDR question: bidi-safe, and safe to mis-click ===')
    import ast as _ast
    import re as _re

    HEB = _re.compile('[֐-׿]')
    LAT = _re.compile('[A-Za-z]')
    TAGS = _re.compile(r'\[/?(?:B|I|COLOR[^\]]*|CR|UPPERCASE|LOWERCASE)\]', _re.I)

    def offenders(text):
        bad = []
        for line in TAGS.sub('', text).split(chr(10)):
            if not (HEB.search(line) and LAT.search(line)):
                continue
            # the Latin run must be LAST: nothing but punctuation/space after it
            tail = line[max(m.start() for m in LAT.finditer(line)) + 1:]
            if HEB.search(tail):
                bad.append(line.strip())
        return bad

    files = {
        'default.py': os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                                   'default.py'),
        'builds.py': os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                                  'resources', 'libs', 'builds.py'),
    }
    total = 0
    for label, path in files.items():
        src = open(path, encoding='utf-8').read()
        strings = []
        for node in _ast.walk(_ast.parse(src)):
            if isinstance(node, _ast.Constant) and isinstance(node.value, str):
                strings.append(node.value)
        ours = [s for s in strings if 'HDR' in s and HEB.search(s)]
        total += len(ours)
        bad = [b for s in ours for b in offenders(s)]
        check('%s: every HDR line is bidi-safe (%d line(s) checked)'
              % (label, len(ours)), not bad)
        for b in bad:
            print('       Latin run is not last: %s' % b)
    check('the feature actually ships Hebrew text to check', total >= 5)

    # the focused button must be the harmless one
    builds = open(files['builds.py'], encoding='utf-8').read()
    ask = builds[builds.index('def _ask_and_apply_sdr'):]
    ask = ask[:ask.index('def _countdown_restart')]
    check('install question focuses "supports HDR" (changes nothing)',
          'DLG_YESNO_YES_BTN' in ask)
    check('install question still works on a Kodi without defaultbutton',
          'except (TypeError, AttributeError)' in ask)
    check('answering "supports HDR" writes nothing unless it was ON',
          'if any(v is True for v in state.values()):' in ask)

    menu = open(files['default.py'], encoding='utf-8').read()
    sdr = menu[menu.index('def sdr_menu'):]
    sdr = sdr[:sdr.index('def maintenance_menu')]
    check('maintenance toggle focuses the CURRENT state (mis-click = no change)',
          'DLG_YESNO_NO_BTN if enabled' in sdr)
    check('maintenance toggle survives a Kodi without defaultbutton',
          'except (TypeError, AttributeError)' in sdr)

    # ...and it has to be reachable, like the OLED one
    maint = menu[menu.index('def maintenance_menu'):]
    maint = maint[:maint.index('def clear_cache')]
    check('the toggle is listed in the maintenance menu', 'מסך ללא HDR' in maint)
    check('and it is actually dispatched', 'sdr_menu()' in maint)


def test_pack_never_picks_a_sample():
    """A torrent pack must never resolve to the SAMPLE file.

    Silo S03E08, 2026-08-22: the pack held both

        silo.s03e08.2160p.web.h265-cakes-sample.mkv     181,788,270 B
        silo.s03e08.2160p.web.h265-cakes.mkv          9,054,753,823 B

    and POV played the sample -- confirmed by HEAD on the URL Kodi opened
    (Content-Length byte-identical to the sample) and by its container declaring
    Duration 65.0s, exactly the 1:05 on screen.

    Two upstream asymmetries, both episode-only, and EITHER alone would have
    prevented it:
      * the extras filter is an `elif` under `if season:` -> movies only, so an
        episode was filtered by the season/episode pattern alone, which the
        sample filename matches just as well
      * the size sort is `if not season` -> an episode took the FIRST entry in
        the pack's arbitrary order instead of the biggest

    This replays the real file list through the shipped picker logic."""
    print(chr(10) + '=== debrid pack: the sample must never win ===')
    import re as _re
    src = open(os.path.join(REPO, 'addons', 'plugin.video.pov', 'resources', 'lib',
                            'modules', 'debrid.py'), encoding='utf-8').read()

    # the real pack from the incident, in the order the API returned it
    FILES = [{'filename': 'silo.s03e08.2160p.web.h265-cakes-sample.mkv',
              'size': 181788270, 'link': 'L-sample'},
             {'filename': 'silo.s03e08.2160p.web.h265-cakes.mkv',
              'size': 9054753823, 'link': 'L-full'}]
    EXTRAS = ('trailer', 'sample', 'extra', 'extras', 'blooper', 'bloopers',
              'deleted', 'inside', 'unused', 'footage', 'feature', 'featurette',
              'making.of', 'behind.the.scenes')

    def pick(files, season, episode, extras_on_episodes, sort_always):
        chosen = []
        for i in files:
            fn = i['filename'].lower()
            if season:
                if 's%02de%02d' % (int(season), int(episode)) not in fn:
                    continue
                if extras_on_episodes and any(x in fn for x in EXTRAS):
                    continue
            elif any(x in fn for x in EXTRAS):
                continue
            chosen.append(i)
        if sort_always or not season:
            chosen.sort(key=lambda k: k['size'], reverse=True)
        return next((i['link'] for i in chosen), None)

    # the bug, reproduced: neither guard -> the sample wins
    check('the old logic really did pick the sample (bug reproduced)',
          pick(FILES, 3, 8, False, False) == 'L-sample')
    # each guard ALONE is enough
    check('extras filter alone saves it', pick(FILES, 3, 8, True, False) == 'L-full')
    check('size sort alone saves it', pick(FILES, 3, 8, False, True) == 'L-full')
    check('both together', pick(FILES, 3, 8, True, True) == 'L-full')
    # movies were never affected, and must stay that way
    check('movie path still filters extras', pick(FILES, None, None, True, True) == 'L-full')

    # and the shipped file carries both guards
    body = src[src.index('def resolve_external_sources'):]
    body = body[:body.index('file_url = api.unrestrict_link')]
    season_branch = body[body.index('if season:'):body.index('elif any(')]
    check('shipped: extras filter runs on the EPISODE branch',
          'extras_filtering_list' in season_branch)
    check('shipped: the size sort is no longer gated on `not season`',
          "if not season: selected_files.sort" not in body
          and 'selected_files.sort(key=lambda k:' in body)


def test_sample_fix_still_needed_upstream():
    """Tell us when upstream fixes the sample bug, so our patch can be dropped.

    We adopted POV's modules/debrid.py purely to fix kodifitzwell/repo#136 (a
    torrent's sample file resolving instead of the episode). That file is
    otherwise none of our business, and carrying it costs a 3-way merge every
    POV release.

    So this reads the CLEAN upstream copy out of the committed base zip and
    checks whether the bug is still there. While it is, the test passes quietly.
    The moment upstream applies the extras filter to the episode branch -- or
    sorts by size unconditionally -- this FAILS, which is the signal to remove
    our overlay of that file.

    REMOVAL PROCEDURE (when this test fails):
      1. git revert 2eba636          # the whole sample fix, in one commit
      2. delete tools/tests/fixtures nothing -- the pack test goes with the revert
      3. python tools/apply_overlay.py overlays addons   # rebuild the mirror
      4. python tools/verify_overlay_merge.py plugin.video.pov
      5. drop this test
    Nothing else references that file, so the revert is complete on its own.
    """
    print(chr(10) + '=== is our POV sample fix still needed upstream? ===')
    import json
    import zipfile

    ov = os.path.join(REPO, 'overlays', 'plugin.video.pov')
    base = json.loads(open(os.path.join(ov, 'base.json'), encoding='utf-8').read())
    zp = os.path.join(ov, base['base_zip_local'].replace('/', os.sep))
    check('clean base zip present', os.path.isfile(zp))
    if not os.path.isfile(zp):
        return

    with zipfile.ZipFile(zp) as z:
        upstream = z.read('plugin.video.pov/resources/lib/modules/debrid.py').decode('utf-8')

    body = upstream[upstream.index('def resolve_external_sources'):]
    body = body[:body.index('file_url = api.unrestrict_link')]
    season_branch = body[body.index('if season:'):body.index('elif any(')]

    still_buggy = ('extras_filtering_list' not in season_branch
                   and 'if not season: selected_files.sort' in body)
    check('upstream %s still has the bug -- keep our overlay of debrid.py'
          % base['base_version'], still_buggy)
    if not still_buggy:
        print('       UPSTREAM APPEARS FIXED in %s.' % base['base_version'])
        print('       Drop our patch: git revert 2eba636, then')
        print('       python tools/apply_overlay.py overlays addons')
        print('       and delete this test. See kodifitzwell/repo#136.')

    # our overlay of that file exists only for this fix
    ours = os.path.join(ov, 'files', 'resources', 'lib', 'modules', 'debrid.py')
    check('we overlay debrid.py only for this reason', os.path.isfile(ours))
    if os.path.isfile(ours):
        src = open(ours, encoding='utf-8').read()
        check('and it carries both guards, nothing else of ours',
              src.count('KODIRDIL') == 2)


def test_no_comments_in_addon_settings():
    """A shipped addon settings.xml must contain NO XML comments.

    Kodi's addon-settings reader crashes NATIVELY on a comment node in
    addon_data/<addon>/settings.xml -- no Python traceback, the log simply
    stops. A three-line explanatory comment I added above skip_intro.enable
    shipped in all six POV config-variants and crash-looped Asaf's box on a
    FRESH INSTALL (2026-08-10); reproduced deterministically by injecting the
    comment and booting. Explanations belong in the commit message or the
    config policy, never inside a settings file a Kodi addon parses."""
    print("\n=== config: shipped addon settings.xml carry no XML comments ===")
    import glob as _glob
    bad = []
    for path in _glob.glob(os.path.join(REPO, 'config*', '**', 'settings.xml'), recursive=True):
        try:
            with open(path, encoding='utf-8') as fh:
                txt = fh.read()
        except Exception:
            continue
        if '<!--' in txt:
            bad.append(os.path.relpath(path, REPO).replace(os.sep, '/'))
    check('no XML comments in any shipped settings.xml', not bad)
    for b in bad[:6]:
        print('      has a comment: %s' % b)


def test_pov_placeholder_scrub():
    """Gears' 'empty_setting' sentinel must never survive as a POV credential.

    A keep-everything reinstall wrote it into POV's settings.xml as the "token"
    of every unused debrid service; POV rejects only ''/None, so rd/pm/oc/ad
    all showed up as authorized on Asaf's box (2026-08-09). Three layers:
    the cross-engine filter, the same-engine xml staging, and the migration
    that scrubs boxes already contaminated."""
    print("\n=== keep/migration: empty_setting is a placeholder, not a login ===")
    # 1. cross-engine carry filter
    got = keep._real_creds({'tb.token': 'REAL-TOKEN', 'rd.token': 'empty_setting',
                            'pm.token': '', 'tb.enabled': 'true'},
                           ['tb.token', 'rd.token', 'pm.token', 'tb.enabled'])
    check('cross-engine: sentinel dropped, real token kept',
          got == {'tb.token': 'REAL-TOKEN'})

    # 2. same-engine xml staging
    d = tempfile.mkdtemp()
    xml = os.path.join(d, 'settings.xml')
    rows = ['<settings version="2">',
            '<setting id="tb.token">REAL-TOKEN</setting>',
            '<setting id="tb.enabled">true</setting>',
            '<setting id="rd.token">empty_setting</setting>',
            '</settings>']
    with open(xml, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(rows))
    got = keep._xml_read(xml, ['tb.token', 'tb.enabled', 'rd.token'])
    check('xml staging: sentinel dropped, true/real kept',
          got == {'tb.token': 'REAL-TOKEN', 'tb.enabled': 'true'})

    # 3. migration scrubs an already-contaminated box, leaves the real login
    pov = os.path.join(HOME, 'userdata', 'addon_data', 'plugin.video.pov')
    os.makedirs(pov, exist_ok=True)
    live = os.path.join(pov, 'settings.xml')
    rows = ['<settings version="2">',
            '<setting id="tb.token">REAL-TOKEN</setting>',
            '<setting id="rd.token">empty_setting</setting>',
            '<setting id="pm.token">empty_setting</setting>',
            '<setting id="easynews_password">empty_setting</setting>',
            '</settings>']
    with open(live, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(rows))
    n = mu.fix_pov_placeholder_tokens()
    after = open(live, encoding='utf-8').read()
    check('migration blanked all 3 sentinels', n == 3)
    check('migration left the real token', '>REAL-TOKEN<' in after)
    check('no sentinel remains', 'empty_setting' not in after)
    check('migration idempotent (second run = 0)', mu.fix_pov_placeholder_tokens() == 0)


def test_no_invalid_tmdb_widgets():
    """No shipped menu may ask TMDb for an endpoint that does not exist.

    `info=upcoming` is movies-only (tmdbhelper declares types=('movie',); TMDb
    has /movie/upcoming and no /tv/upcoming). The Zephyr "בקרוב" widget paired
    it with tmdb_type=tv, so it failed on EVERY load -- the TMDb error Asaf saw
    on the Shield 2026-08-04, and present in Xiaomi logs from 2026-07-30.

    Also checks the migration, because the skinshortcuts config dir is
    `update: skip` + `content: gears`: a corrected shipped file reaches fresh
    installs only, so existing boxes need fix_invalid_tmdb_widgets()."""
    print("\n=== config: no movies-only TMDb info type paired with tv ===")
    import glob as _glob

    # Checks EVERY info/tmdb_type pair we ship (all 4 skins, both engines)
    # against tmdbhelper's own route declarations -- not just the one pairing
    # that broke, so a different bad combination cannot slip through.
    sys.path.insert(0, os.path.join(REPO, 'tools'))
    import check_tmdb_widgets as ctw

    routes, known = ctw.declared_routes(), ctw.known_routes()
    check('tmdbhelper route tables parsed', len(routes) >= 20 and len(known) >= 20)

    bad, seen = [], 0
    for rel, info, tmdb_type in ctw.shipped_widgets():
        seen += 1
        allowed = routes.get(info)
        if allowed is None:
            if not info.startswith('dir_') and info not in known:
                bad.append('%s (info=%s unknown)' % (rel, info))
        elif None not in allowed and tmdb_type and tmdb_type not in allowed:
            bad.append('%s (info=%s + tmdb_type=%s)' % (rel, info, tmdb_type))
    check('widgets were actually found to check', seen > 100)
    check('every shipped widget uses a tmdb_type its route supports', not bad)
    for b in bad[:5]:
        print('      still broken: %s' % b)

    # the migration must repair an existing box, and touch nothing else
    ss = os.path.join(HOME, 'userdata', 'addon_data', 'script.skinshortcuts')
    os.makedirs(ss, exist_ok=True)
    victim = os.path.join(ss, 'sdrvt-1.DATA.xml')
    with open(victim, 'w', encoding='utf-8') as fh:
        fh.write('<shortcuts><shortcut>plugin://plugin.video.themoviedb.helper/'
                 '?info=upcoming&amp;tmdb_type=tv&amp;widget=true</shortcut></shortcuts>')
    keep = os.path.join(ss, 'srtym-1.DATA.xml')
    with open(keep, 'w', encoding='utf-8') as fh:            # movies: VALID, must not change
        fh.write('<shortcuts><shortcut>plugin://plugin.video.themoviedb.helper/'
                 '?info=upcoming&amp;tmdb_type=movie&amp;widget=true</shortcut></shortcuts>')

    changed = mu.fix_invalid_tmdb_widgets()
    with open(victim, encoding='utf-8') as fh:
        after = fh.read()
    with open(keep, encoding='utf-8') as fh:
        after_keep = fh.read()
    check('migration repairs the tv widget', 'info=on_the_air&amp;tmdb_type=tv' in after)
    check('migration reports what it changed', 'sdrvt-1.DATA.xml' in changed)
    check('movies upcoming (VALID) left untouched',
          'info=upcoming&amp;tmdb_type=movie' in after_keep)


def test_seeds_survive_a_reinstall():
    """One-time seeds must be gated on the actual STATE, not on a marker file.

    The wipe deliberately preserves the wizard's own addon_data, so a marker
    written by the OLD build survives a reinstall while the seeded database is
    recreated empty -- the seed then believes it already ran and skips forever.
    That is exactly how "סדרות > רשתות סטרימינג" came up empty on Asaf's
    2026-08-02 Gears reinstall (marker dated the previous day, POV era).

    Also covers the sibling gap: cpath_cache.db is `update: skip` in the config
    policy, so a row ADDED to the shipped config (the TV-genres widget) never
    reaches an existing box unless something inserts it."""
    print("\n=== seeds are state-gated, not marker-gated ===")
    import sqlite3 as _sq

    # --- gears shortcut folders: stale marker must NOT suppress the seed ----
    dbdir = os.path.join(HOME, 'userdata', 'addon_data',
                         'plugin.video.gears', 'databases')
    os.makedirs(dbdir, exist_ok=True)
    ndb = os.path.join(dbdir, 'navigator.db')
    if os.path.exists(ndb):
        os.remove(ndb)
    os.makedirs(mu.ADDON_DATA, exist_ok=True)
    stale = os.path.join(mu.ADDON_DATA, 'gears_networks_seed_v2')
    with open(stale, 'w', encoding='utf-8') as fh:
        fh.write('1')                      # marker from the PREVIOUS build

    mu.seed_gears_shortcut_folder()

    con = _sq.connect(ndb)
    names = [r[0] for r in con.execute(
        "SELECT list_name FROM navigator WHERE list_type='shortcut_folder'")]
    con.close()
    check('seeded despite the stale marker', len(names) == len(mu.GEARS_SEED_FOLDERS))
    check('networks folder present (the empty רשתות סטרימינג row)',
          any('NETWROKS' in n.upper() for n in names))
    check('stale marker retired', not os.path.exists(stale))

    # a user's edited folder must not be overwritten on the next run
    con = _sq.connect(ndb)
    con.execute("UPDATE navigator SET list_contents='[]' WHERE list_name=?",
                (mu.GEARS_SEED_FOLDERS[0][0],))
    con.commit(); con.close()
    mu.seed_gears_shortcut_folder()
    con = _sq.connect(ndb)
    kept = con.execute("SELECT list_contents FROM navigator WHERE list_name=?",
                       (mu.GEARS_SEED_FOLDERS[0][0],)).fetchone()[0]
    con.close()
    check("a user's edited folder is not re-seeded over", kept == '[]')

    # --- nimbus cpaths: a row added to the shipped config reaches old boxes --
    nh = os.path.join(HOME, 'userdata', 'addon_data', 'script.nimbus.helper')
    os.makedirs(nh, exist_ok=True)
    live = os.path.join(nh, 'cpath_cache.db')
    if os.path.exists(live):
        os.remove(live)
    con = _sq.connect(live)
    con.execute('CREATE TABLE custom_paths (cpath_setting TEXT, cpath_path TEXT,'
                ' cpath_header TEXT, cpath_type TEXT, cpath_label TEXT)')
    con.execute("INSERT INTO custom_paths VALUES ('movie.widget.4','USER_EDITED',"
                "'x','y','z')")            # an existing row the user changed
    con.commit(); con.close()

    mu.seed_nimbus_missing_cpaths()

    con = _sq.connect(live)
    rows = dict(con.execute("SELECT cpath_setting, cpath_path FROM custom_paths"))
    con.close()
    check('missing TV-genres row added to an existing box',
          'menu_type=tvshow' in (rows.get('tvshow.widget.5') or ''))
    check('TV-genres row points at the right engine',
          'plugin.video.gears' in (rows.get('tvshow.widget.5') or ''))
    check("the user's edited row is left alone",
          rows.get('movie.widget.4') == 'USER_EDITED')


def test_menu_bundle_never_laid_on_pov():
    """repair_skin_menu ships GEARS home menus (every widget is a
    plugin://plugin.video.gears/ path). Laying them on a POV box replaces a
    WORKING POV menu with widgets pointing at an addon that isn't installed --
    home goes empty, log fills with 'Unable to find plugin plugin.video.gears'.

    Asaf hit this live 2026-08-02 on Zephyr: the trigger was NOT a broken menu
    but a bundle VERSION bump alone (`broken=False stale=True`), so the guard
    must hold for the stale path specifically. On POV the variant config owns
    those menu files, so the correct action is re-applying the POV variant.

    Also asserts the shipped bundles really are Gears-only -- if a POV bundle is
    ever added, this test should be revisited rather than silently passing."""
    print("\n=== repair_skin_menu: GEARS bundle never laid on a POV box ===")
    import shutil as _sh

    ZEPHYR = 'skin.arctic.zephyr.2.resurrection.mod'
    bundle = os.path.join(REPO, 'addons', 'plugin.program.masterkodi.il.wizard',
                          'resources', 'menu_defaults', ZEPHYR)
    check('zephyr menu bundle ships', os.path.isdir(bundle))

    # the bundle is Gears-only -- the premise of the whole guard
    hits = {'gears': 0, 'pov': 0}
    for root, _dirs, files in os.walk(bundle):
        for fn in files:
            try:
                with open(os.path.join(root, fn), encoding='utf-8',
                          errors='replace') as fh:
                    txt = fh.read()
            except Exception:
                continue
            hits['gears'] += txt.count('plugin.video.gears')
            hits['pov'] += txt.count('plugin.video.pov')
    check('bundle is GEARS menus (that is why the guard exists)',
          hits['gears'] > 0 and hits['pov'] == 0)

    # --- stage a POV box whose menu is healthy but whose bundle marker is old
    skin_dir = os.path.join(mu.ADDONS_PATH, ZEPHYR, '1080i')
    os.makedirs(skin_dir, exist_ok=True)
    inc = os.path.join(skin_dir, 'script-skinshortcuts-includes.xml')
    with open(inc, 'w', encoding='utf-8') as fh:
        fh.write('<includes><shortcut>plugin://plugin.video.pov/?mode=x</shortcut></includes>')
    open(os.path.join(skin_dir, 'Home.xml'), 'w').close()
    ss_dst = os.path.join(HOME, 'userdata', 'addon_data', 'script.skinshortcuts')
    os.makedirs(ss_dst, exist_ok=True)
    povdata = os.path.join(ss_dst, 'mainmenu.DATA.xml')
    with open(povdata, 'w', encoding='utf-8') as fh:
        fh.write('<shortcuts><shortcut>plugin://plugin.video.pov/?mode=y</shortcut></shortcuts>')
    os.makedirs(mu.ADDON_DATA, exist_ok=True)
    marker = os.path.join(mu.ADDON_DATA, 'menu_ver_%s.txt' % ZEPHYR)
    with open(marker, 'w', encoding='utf-8') as fh:
        fh.write('0')                      # older than the shipped VERSION -> stale

    orig_skin, orig_src, orig_major = mu._active_skin, mu._content_source, mu.KODI_MAJOR
    calls = {'pov_apply': 0}
    try:
        mu._active_skin = lambda: ZEPHYR
        mu._content_source = lambda: 'pov'
        mu.KODI_MAJOR = 21                 # Piers returns early for other reasons
        from resources.libs import content_source as _cs
        orig_core, orig_vdir = _cs._apply_pov_core, _cs._variant_dir

        def fake_core(skin_id):
            calls['pov_apply'] += 1
            return True, ''
        _cs._apply_pov_core = fake_core
        _cs._variant_dir = lambda s: 'zephyr-pov-tmdb'
        try:
            restored = mu.repair_skin_menu(no_reload=True)
        finally:
            _cs._apply_pov_core, _cs._variant_dir = orig_core, orig_vdir
    finally:
        mu._active_skin, mu._content_source = orig_skin, orig_src
        mu.KODI_MAJOR = orig_major

    check('POV variant re-applied instead of the Gears bundle',
          calls['pov_apply'] == 1)
    # the smoking gun from the live incident: the box's menu got Gears URLs
    with open(povdata, encoding='utf-8') as fh:
        after = fh.read()
    check('box menu NOT overwritten with gears URLs',
          'plugin.video.gears' not in after and 'plugin.video.pov' in after)
    with open(inc, encoding='utf-8') as fh:
        inc_after = fh.read()
    check('skin includes NOT overwritten with gears URLs',
          'plugin.video.gears' not in inc_after)
    check('reports the POV path, not a bundle relay',
          'skinshortcuts-data' not in restored and 'skin-includes' not in restored)

    # --- and when the POV variant cannot be applied: SKIP, never fall back
    with open(marker, 'w', encoding='utf-8') as fh:
        fh.write('0')
    try:
        mu._active_skin = lambda: ZEPHYR
        mu._content_source = lambda: 'pov'
        mu.KODI_MAJOR = 21
        from resources.libs import content_source as _cs
        orig_vdir = _cs._variant_dir
        _cs._variant_dir = lambda s: None          # no variant for this skin/version
        try:
            restored2 = mu.repair_skin_menu(no_reload=True)
        finally:
            _cs._variant_dir = orig_vdir
    finally:
        mu._active_skin, mu._content_source = orig_skin, orig_src
        mu.KODI_MAJOR = orig_major
    with open(povdata, encoding='utf-8') as fh:
        after2 = fh.read()
    check('no POV variant -> skips (still no gears URLs on the box)',
          'plugin.video.gears' not in after2 and not restored2)

    _sh.rmtree(os.path.join(mu.ADDONS_PATH, ZEPHYR), ignore_errors=True)


def test_maintenance_folder_contents():
    """The תחזוקה menu must not just EXIST -- it must be POPULATED, and its
    content-cache tile must match the engine actually installed. Presence-only
    checks miss a menu that opens empty or points at the wrong engine (Asaf,
    2026-07-31: "אתה בודק שיטחי... רק אם יש או אין את הקטגוריה אבל לא מה יש
    בתוכה"). Measured baseline on the Xiaomi: 4 entries, and the cache tile is
    maint_gears on a Gears box / maint_pov on a POV box."""
    print("\n=== maintenance folder: populated + engine-correct cache tile ===")
    import importlib, types
    wiz = os.path.join(_bootstrap.ADDON, 'default.py')

    def folder_items(pov_installed):
        """Run maintenance_folder() with a stubbed plugin API and capture rows."""
        povdir = os.path.join(HOME, 'addons', 'plugin.video.pov')
        if pov_installed:
            os.makedirs(povdir, exist_ok=True)
        else:
            shutil.rmtree(povdir, ignore_errors=True)
        rows = []
        import xbmcplugin, xbmcgui, sys as _s
        real_add = xbmcplugin.addDirectoryItem
        xbmcplugin.addDirectoryItem = lambda h, url, li, isFolder=False: rows.append(url)
        old_argv = list(_s.argv)
        _s.argv = ['plugin://plugin.program.masterkodi.il.wizard/', '1', '']
        try:
            src = open(wiz, encoding='utf-8').read()
            mod = types.ModuleType('wiz_probe')
            mod.__dict__['__name__'] = 'wiz_probe'      # skip the __main__ block
            exec(compile(src, wiz, 'exec'), mod.__dict__)
            mod.maintenance_folder()
        finally:
            xbmcplugin.addDirectoryItem = real_add
            _s.argv = old_argv
        return rows

    try:
        gears_rows = folder_items(pov_installed=False)
        pov_rows = folder_items(pov_installed=True)
    except Exception as e:
        check('maintenance_folder ran under the shim (%s)' % e, False)
        return

    check('gears box: 4 maintenance entries', len(gears_rows) == 4)
    check('gears box: cache tile is maint_gears',
          any('mode=maint_gears' in u and 'gearsai' not in u for u in gears_rows))
    check('gears box: no POV cleaner offered',
          not any('mode=maint_pov' in u for u in gears_rows))
    check('pov box: 4 maintenance entries', len(pov_rows) == 4)
    check('pov box: cache tile is maint_pov',
          any('mode=maint_pov' in u for u in pov_rows))
    check('pov box: no Gears cleaner offered',
          not any('mode=maint_gears' in u and 'gearsai' not in u for u in pov_rows))
    for must in ('maint_gearsai', 'send_logs', 'check_updates'):
        check('both boxes offer %s' % must,
              any(must in u for u in gears_rows) and any(must in u for u in pov_rows))


def test_maintenance_keeps_logs():
    """Clearing the cache must not unlink kodi.log: it lives in special://temp
    and Kodi holds it OPEN, so deleting it silently kills logging for the rest
    of the session -- i.e. it destroys the only support channel we have."""
    print("\n=== maintenance: cache clear keeps kodi.log, drops everything else ===")
    import resources.libs.maintenance as mnt
    tmp = os.path.join(HOME, 'temp')
    cache = os.path.join(HOME, 'cache')
    for p in (tmp, cache):
        os.makedirs(p, exist_ok=True)
    open(os.path.join(tmp, 'kodi.log'), 'w').write('x' * 100)
    open(os.path.join(tmp, 'kodi.old.log'), 'w').write('x' * 100)
    open(os.path.join(tmp, 'archive_cache.bin'), 'w').write('x' * 500)
    os.makedirs(os.path.join(tmp, 'subdir'), exist_ok=True)
    open(os.path.join(tmp, 'subdir', 'junk'), 'w').write('x' * 200)
    open(os.path.join(cache, 'junk'), 'w').write('x' * 300)

    res = mnt.clear_cache()
    check('kodi.log survives the cache clear', os.path.isfile(os.path.join(tmp, 'kodi.log')))
    check('kodi.old.log survives the cache clear', os.path.isfile(os.path.join(tmp, 'kodi.old.log')))
    check('non-log temp file removed', not os.path.exists(os.path.join(tmp, 'archive_cache.bin')))
    check('temp subdir removed', not os.path.exists(os.path.join(tmp, 'subdir')))
    check('home/cache emptied', not os.path.exists(os.path.join(cache, 'junk')))
    check('clear_cache returns a human summary (not None)',
          isinstance(res, str) and res and 'None' not in res)

    # thumbnails: Textures*.db only dropped when the caller commits to a restart
    dbdir = os.path.join(HOME, 'userdata', 'Database')
    thumbs = os.path.join(HOME, 'userdata', 'Thumbnails')
    os.makedirs(dbdir, exist_ok=True); os.makedirs(thumbs, exist_ok=True)
    open(os.path.join(thumbs, 'a.jpg'), 'w').write('x' * 10)
    tex = os.path.join(dbdir, 'Textures13.db')
    open(tex, 'w').write('x' * 10)
    mnt.clear_thumbnails(drop_texture_db=False)
    check('thumbnail files removed', not os.path.exists(os.path.join(thumbs, 'a.jpg')))
    check('Textures db KEPT when no restart is planned', os.path.isfile(tex))
    check('thumbnail shard folders recreated', os.path.isdir(os.path.join(thumbs, 'a')))
    mnt.clear_thumbnails(drop_texture_db=True)
    check('Textures db dropped when a restart IS planned', not os.path.exists(tex))


def test_gears_settings_go_live_without_restart():
    """Enforcing Gears settings must ALSO mirror them into the window properties.

    Gears reads its settings as `gears.<id>` window properties, refreshed from
    settings.db only by its own boot sync. A db-only write therefore stayed
    invisible until the next restart: right after a fresh Gears install the
    settings UI still showed EXTERNAL SCRAPERS off, the running scrape used the
    stale value, found no sources and surfaced as an error (Asaf, 2026-08-02).
    The views enforcement already mirrored for this reason; settings did not."""
    print("\n=== gears settings: applied live, not only in the db ===")
    import sqlite3 as _sq
    import xbmcgui as _gui

    dbdir = os.path.join(HOME, 'userdata', 'addon_data', 'plugin.video.gears', 'databases')
    os.makedirs(dbdir, exist_ok=True)
    db = os.path.join(dbdir, 'settings.db')
    if os.path.exists(db):
        os.remove(db)
    con = _sq.connect(db)
    con.execute('CREATE TABLE settings (setting_id TEXT PRIMARY KEY, setting_value TEXT)')
    con.execute("INSERT INTO settings VALUES ('provider.external','false')")
    con.commit(); con.close()

    win = _gui.Window(10000)
    win.setProperty('gears.provider.external', 'false')      # stale in-memory value
    ok = mu._enforce_gears_settings(HOME, {'provider.external': True,
                                           'rd.token': 'SECRET'}, {'rd.token'})
    check('enforcement reports success', ok is True)

    con = _sq.connect(db)
    val = con.execute("SELECT setting_value FROM settings WHERE setting_id='provider.external'").fetchone()[0]
    con.close()
    check('db updated with a lowercase boolean', val == 'true')
    check('window property mirrored -> live without a restart',
          win.getProperty('gears.provider.external') == 'true')
    check('excluded credential NOT mirrored into a window property',
          win.getProperty('gears.rd.token') == '')


def test_gears_settings_bool_serialization():
    """gears_settings enforcement must write JSON booleans as lowercase
    'true'/'false' -- Gears reads with string compares (== 'true'), so the old
    str(True)='True' left provider.external effectively DISABLED on fresh
    installs (external scrapers off + no scraper selected, Asaf's find)."""
    print("\n=== modular_update: gears_settings booleans land as 'true'/'false' ===")
    import resources.libs.modular_update as _mu
    home = tempfile.mkdtemp(prefix='gsbool_')
    dbdir = os.path.join(home, 'userdata', 'addon_data', 'plugin.video.gears', 'databases')
    os.makedirs(dbdir, exist_ok=True)
    db = os.path.join(dbdir, 'settings.db')
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE settings (setting_id TEXT UNIQUE, setting_value TEXT)")
    c.execute("INSERT INTO settings VALUES ('provider.external', 'false')")
    c.commit(); c.close()
    ok = _mu._enforce_gears_settings(home, {
        'provider.external': True,                       # JSON true -> must be 'true'
        'external.cache_check': False,                   # -> 'false'
        'external_scraper.module': 'script.module.magneto',
        'results.timeout': 12,
    }, set())
    check('enforce returned True', ok is True)
    c = sqlite3.connect(db)
    rows = dict(c.execute("SELECT setting_id, setting_value FROM settings").fetchall())
    c.close()
    check("bool True -> 'true' (lowercase)", rows.get('provider.external') == 'true')
    check("bool False -> 'false'", rows.get('external.cache_check') == 'false')
    check("string value unchanged", rows.get('external_scraper.module') == 'script.module.magneto')
    check("int stays str(12)", rows.get('results.timeout') == '12')


def test_detect_extras_skips_kodi_defaults():
    """detect_extras must NOT flag Kodi's own default addons (e.g. the music
    metadata scrapers metadata.album.universal) as 'addons you installed
    yourself'. They live in special://xbmc/addons; a copy in the profile dir is
    still a Kodi default, not a user addon. Regression for the false positive
    Asaf hit on the Windows install (offered 3 'user addons' = music scrapers)."""
    print("\n=== keep.detect_extras: skips Kodi default addons (music scrapers) ===")
    import resources.libs.keep as _keep
    ha = _keep.HOME_ADDONS
    os.makedirs(ha, exist_ok=True)
    # a Kodi default + a genuine user addon, both in the profile addons dir
    for name in ('metadata.album.universal', 'plugin.user.myaddon'):
        d = os.path.join(ha, name); os.makedirs(d, exist_ok=True)
        open(os.path.join(d, 'addon.xml'), 'w').write('<addon id="%s"/>' % name)
    # a SEPARATE fake xbmc/addons dir containing the Kodi default only
    xbmc_addons = tempfile.mkdtemp(prefix='xbmcaddons_')
    os.makedirs(os.path.join(xbmc_addons, 'metadata.album.universal'), exist_ok=True)
    orig = _keep.xbmcvfs.translatePath
    _keep.xbmcvfs.translatePath = (lambda p: xbmc_addons if p == 'special://xbmc/addons'
                                   else orig(p))
    try:
        extras = _keep.detect_extras(set())    # empty manifest -> nothing filtered by build
    finally:
        _keep.xbmcvfs.translatePath = orig
    check('user addon flagged as extra', 'plugin.user.myaddon' in extras)
    check('Kodi default (music scraper) NOT flagged', 'metadata.album.universal' not in extras)


def test_remove_skin_purges_residue():
    """Removing an optional skin must leave NO residue named after it: the skin
    folder, its addon_data, its DB rows (installed/addons/repo/update_rules), and
    the per-skin files the helper addons keep (skinshortcuts skin.X.*,
    skinvariables nodes/skin.X + skin.X-*.json). Exclusive helper ADDONS are
    intentionally kept (disabled, harmless)."""
    print("\n=== remove_skin: purges all skin-named residue (files + DB rows) ===")
    import resources.libs.config as _C
    sid = 'skin.nimbus'
    ad = os.path.join(_C.USERDATA, 'addon_data')
    # plant the full residue surface
    skin_dir = os.path.join(_C.ADDONS, sid)
    os.makedirs(skin_dir, exist_ok=True); open(os.path.join(skin_dir, 'addon.xml'), 'w').write('x')
    os.makedirs(os.path.join(ad, sid), exist_ok=True); open(os.path.join(ad, sid, 'settings.xml'), 'w').write('x')
    ss = os.path.join(ad, 'script.skinshortcuts'); os.makedirs(ss, exist_ok=True)
    for suf in ('.hash', '.properties', '.DATA.xml', '.properties.pre_gears'):
        open(os.path.join(ss, sid + suf), 'w').write('x')
    open(os.path.join(ss, 'mainmenu.DATA.xml'), 'w').write('KEEP')      # not skin-named
    sv = os.path.join(ad, 'script.skinvariables'); os.makedirs(os.path.join(sv, 'nodes', sid), exist_ok=True)
    open(os.path.join(sv, 'nodes', sid, 'x.json'), 'w').write('x')
    open(os.path.join(sv, sid + '-viewtypes.json'), 'w').write('x')
    open(os.path.join(sv, 'skin.arctic.fuse.3-viewtypes.json'), 'w').write('KEEP')  # other skin
    # our own menu-bundle marker: written by repair_skin_menu, never otherwise
    # deleted. Left behind, a REINSTALL of this skin sees stale=False and skips
    # the menu repair -- the repair that exists because a fresh install caches an
    # EMPTY skinshortcuts menu (found in Asaf's 2026-08-02 removal sweep).
    wiz_ad = os.path.join(ad, _C.ADDON_ID)
    os.makedirs(wiz_ad, exist_ok=True)
    marker = os.path.join(wiz_ad, 'menu_ver_%s.txt' % sid)
    open(marker, 'w').write('4')
    other_marker = os.path.join(wiz_ad, 'menu_ver_skin.arctic.fuse.3.txt')
    open(other_marker, 'w').write('4')
    # DB rows incl. update_rules
    dbdir = os.path.join(_C.USERDATA, 'Database'); os.makedirs(dbdir, exist_ok=True)
    live = os.path.join(dbdir, 'Addons33.db')
    c = sqlite3.connect(live)
    c.execute("CREATE TABLE IF NOT EXISTS installed (addonID TEXT, enabled INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS update_rules (addonID TEXT, addonRule INTEGER)")
    c.execute("INSERT INTO installed VALUES (?,1)", (sid,))
    c.execute("INSERT INTO update_rules VALUES (?,2)", (sid,))
    c.commit(); c.close()

    bm = builds.BuildManager()
    # active skin must differ so remove_skin proceeds
    import xbmc as _x
    orig = _x.getSkinDir
    _x.getSkinDir = lambda: 'skin.estuary'
    try:
        ok = bm.remove_skin(sid)
    finally:
        _x.getSkinDir = orig
    check('remove_skin returned True', ok is True)
    check('skin folder gone', not os.path.exists(skin_dir))
    check('skin addon_data gone', not os.path.exists(os.path.join(ad, sid)))
    left_ss = [n for n in os.listdir(ss) if n.startswith(sid)]
    check('skinshortcuts skin.X.* purged', left_ss == [])
    check('skinshortcuts shared file kept', os.path.isfile(os.path.join(ss, 'mainmenu.DATA.xml')))
    check('skinvariables nodes/skin.X purged', not os.path.exists(os.path.join(sv, 'nodes', sid)))
    check('skinvariables skin.X-*.json purged', not os.path.exists(os.path.join(sv, sid + '-viewtypes.json')))
    check('skinvariables OTHER skin json kept', os.path.isfile(os.path.join(sv, 'skin.arctic.fuse.3-viewtypes.json')))
    fresh = sqlite3.connect(live)
    inst = fresh.execute("SELECT COUNT(*) FROM installed WHERE addonID=?", (sid,)).fetchone()[0]
    rule = fresh.execute("SELECT COUNT(*) FROM update_rules WHERE addonID=?", (sid,)).fetchone()[0]
    fresh.close()
    check('installed row removed', inst == 0)
    check('update_rules row removed (pinning residue)', rule == 0)
    check('menu-bundle marker removed (else a reinstall skips the menu repair)',
          not os.path.exists(marker))
    check('OTHER skin menu marker kept', os.path.isfile(other_marker))
    # leave the shared HOME's Addons33.db clean for later tests
    try:
        os.remove(live)
    except Exception:
        pass


def test_dbmoved_install():
    """The 2026-07-30 Android reinstall bug: wipe+extract must NOT replace the
    LIVE (open) Addons33.db. Simulates Kodi's open handle across a full
    wipe -> extract -> merge -> reconcile cycle. NOTE: wipes the test HOME --
    keep this test LAST."""
    print("\n=== install: live Addons33.db preserved + merged + reconciled (DBMOVED) ===")
    import json as _json
    dbdir = os.path.join(HOME, 'userdata', 'Database')
    os.makedirs(dbdir, exist_ok=True)
    live = os.path.join(dbdir, 'Addons33.db')
    c = sqlite3.connect(live)
    c.execute("CREATE TABLE installed (id INTEGER PRIMARY KEY, addonID TEXT, enabled INTEGER, "
              "installDate TEXT, lastUpdated TEXT, lastUsed TEXT, origin TEXT, disabledReason INTEGER)")
    c.execute("CREATE TABLE update_rules (id INTEGER PRIMARY KEY, addonID TEXT, addonRule INTEGER)")
    c.execute("INSERT INTO installed (addonID, enabled) VALUES ('plugin.old.ghost', 1)")
    c.execute("INSERT INTO installed (addonID, enabled) VALUES ('xbmc.python', 1)")
    c.commit()
    kodi_handle = sqlite3.connect(live)          # simulates Kodi's OPEN connection

    # bundle: one addon + guisettings + a SEED Addons33.db registering it enabled
    d = tempfile.mkdtemp()
    seed = os.path.join(d, 'seed.db')
    s = sqlite3.connect(seed)
    s.execute("CREATE TABLE installed (id INTEGER PRIMARY KEY, addonID TEXT, enabled INTEGER, "
              "installDate TEXT, lastUpdated TEXT, lastUsed TEXT, origin TEXT, disabledReason INTEGER)")
    s.execute("CREATE TABLE update_rules (id INTEGER PRIMARY KEY, addonID TEXT, addonRule INTEGER)")
    s.execute("INSERT INTO installed (addonID, enabled, origin) VALUES ('plugin.new', 1, '')")
    s.execute("INSERT INTO update_rules (addonID, addonRule) VALUES ('plugin.new', 2)")
    s.commit(); s.close()
    zp = os.path.join(d, 'build.zip')
    with zipfile.ZipFile(zp, 'w') as z:
        z.writestr('addons/plugin.new/addon.xml', '<addon id="plugin.new" version="1.0"/>')
        z.writestr('userdata/guisettings.xml', '<settings/>')
        z.write(seed, 'userdata/Database/Addons33.db')

    bm = builds.BuildManager()
    os.makedirs(builds.TEMP_FOLDER, exist_ok=True)

    class _P:                                     # progress stub
        def update(self, *a, **k): pass
    wipe_fail = bm.wipe(_P())
    check('wipe: 0 undeletable files', wipe_fail == 0)
    check('wipe: LIVE Addons33.db preserved (not unlinked)', os.path.isfile(live))

    ok, errs = bm.extract_zip(zp, HOME, _P())
    check('extract ok', ok is True)
    check('bundle addon extracted', os.path.isfile(os.path.join(HOME, 'addons', 'plugin.new', 'addon.xml')))

    # THE core assertion: Kodi's pre-existing handle still writes to the SAME file
    try:
        kodi_handle.execute("INSERT INTO installed (addonID, enabled) VALUES ('handle.probe', 1)")
        kodi_handle.commit()
        handle_ok = True
    except Exception:
        handle_ok = False
    fresh = sqlite3.connect(live)
    probe = fresh.execute("SELECT COUNT(*) FROM installed WHERE addonID='handle.probe'").fetchone()[0]
    check('LIVE handle still writes to the real db (no DBMOVED)', handle_ok and probe == 1)
    merged = fresh.execute("SELECT enabled FROM installed WHERE addonID='plugin.new'").fetchone()
    check('bundle registry row MERGED into live db (enabled)', bool(merged) and merged[0] == 1)
    rule = fresh.execute("SELECT addonRule FROM update_rules WHERE addonID='plugin.new'").fetchone()
    check('bundle update_rules (pinning) merged', bool(rule) and rule[0] == 2)
    ghost = fresh.execute("SELECT COUNT(*) FROM installed WHERE addonID='plugin.old.ghost'").fetchone()[0]
    check('stale GHOST row reconciled away', ghost == 0)
    virt = fresh.execute("SELECT COUNT(*) FROM installed WHERE addonID='xbmc.python'").fetchone()[0]
    check('virtual xbmc.* row kept', virt == 1)
    fresh.close(); kodi_handle.close()
    shutil.rmtree(d, ignore_errors=True)


def main():
    for t in (test_imports, test_keep, test_cred_preserve, test_switch_transactional,
              test_logs, test_lock_and_recovery,
              test_validate_zip, test_backup_restore, test_backup_quick_creds,
              test_update_ordering, test_cross_source_keep,
              test_credentials_survive_reinstall,
              test_skin_include_names_resolve,
              test_clean_install_option,
              test_set_default_skin_no_guisettings, test_maintenance_keeps_logs,
              test_pov_shortcut_folder_seed_is_json,
              test_pov_publishes_player_release,
              test_menu_bundle_never_laid_on_pov,
              test_menu_bundle_never_overwrites_a_healthy_menu,
              test_hebrew_title_quality_classification,
              test_oled_uses_settings_api,
              test_subtitle_font_choice_is_the_users,
              test_font_picker_matches_the_shipped_pack,
              test_skip_pill_only_over_fullscreen_video,
              test_log_upload_loses_nothing,
              test_subtitle_passthrough_is_utf8_and_real,
              test_wand_press_shows_feedback,
              test_keep_cancel_aborts_the_install,
              test_services_connect_offer,
              test_sdr_filter_against_real_sources,
              test_persistent_sdr_filter,
              test_sdr_switch_writes_both_engines,
              test_upstream_watch_urls,
              test_continue_watching_row,
              test_sdr_switch_in_the_filter_menu,
              test_sdr_indicator_on_the_panel,
              test_install_question_applies_the_answer,
              test_sdr_question_reads_right_in_hebrew,
              test_pack_never_picks_a_sample,
              test_sample_fix_still_needed_upstream,
              test_no_comments_in_addon_settings,
              test_pov_placeholder_scrub,
              test_no_invalid_tmdb_widgets,
              test_seeds_survive_a_reinstall,
              test_active_skin_update_on_windows,
              test_maintenance_folder_contents, test_remove_skin_purges_residue, test_detect_extras_skips_kodi_defaults,
              test_gears_settings_bool_serialization,
              test_gears_settings_go_live_without_restart,
              test_dbmoved_install):
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
