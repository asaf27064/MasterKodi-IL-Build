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
                                  'rd.token': 'RD_KEY', 'trakt.user': 'asaf'}},
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
    check('POV->Gears: trakt user carried', got.get('trakt.user') == 'asaf')
    check("POV->Gears: placeholder 'false' NOT carried", got.get('ad.token') is None)

    # ---- Gears -> POV: db values carried into the POV xml -------------------
    shutil.rmtree(keep.STAGE, ignore_errors=True)
    os.makedirs(keep.STAGE, exist_ok=True)
    with open(pov_xml, 'w', encoding='utf-8') as fh:
        fh.write('<settings><setting id="tb.token"></setting>'
                 '<setting id="ad.token"></setting></settings>')
    _json.dump({'keys': ['debrid'],
                'settings': {'gears': {'tb.token': 'TORBOX_KEY2', 'ad.token': 'AD_KEY'}},
                'xml': {}, 'source_content': 'gears', 'target_content': 'pov'},
               open(os.path.join(keep.STAGE, 'manifest.json'), 'w'))
    keep.restore()
    with open(pov_xml, encoding='utf-8') as fh:
        xml_after = fh.read()
    check('Gears->POV: TorBox token carried into the POV xml',
          'TORBOX_KEY2' in xml_after)
    check('Gears->POV: alldebrid token carried', 'AD_KEY' in xml_after)

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
              test_set_default_skin_no_guisettings, test_maintenance_keeps_logs,
              test_pov_shortcut_folder_seed_is_json,
              test_pov_publishes_player_release,
              test_menu_bundle_never_laid_on_pov,
              test_maintenance_folder_contents, test_remove_skin_purges_residue, test_detect_extras_skips_kodi_defaults,
              test_gears_settings_bool_serialization,
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
