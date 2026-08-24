# -*- coding: utf-8 -*-
"""The persistent "my TV cannot show HDR" switch.

WHY IT EXISTS
-------------
Both source engines can hide HDR/Dolby-Vision releases, but only for the window
that is open: the user picks Filter -> "הצג SDR בלבד" again on every single
search. Whether a display can show HDR is a property of the living room, not of
one search, so it belongs in a setting that is asked once.

WHAT IT WRITES -- THE ENGINES' OWN SETTINGS, NOT ONE OF OURS
------------------------------------------------------------
    POV     filter_hdr / filter_dv    (settings.xml)   0=Include 1=Exclude
    Gears   filter.hdr / filter.dv    (settings.db)    0=Include 1=Exclude

Inventing a setting of our own would have meant a switch that only works where
our window code runs. Upstream's pair filters at the RESULTS level, so it also
covers autoplay and background resolve, and the user can flip it back from the
addon's own settings screen without going through us.

BOTH ids are always written together. With only one of them set to Exclude each
engine deliberately KEEPS DV/HDR "hybrid" releases (they fall back to HDR10 on a
DV-less display); both set is the unambiguous "this display is SDR", and it is
what our windows/sources.py coverage patch keys on.

WHY NOT setSetting()/sqlite ALONE
---------------------------------
Each engine reads through a memory cache mirrored into HOME window properties --
POV keeps one JSON blob in `pov_settings`, Gears one property per id
(`gears.<id>`), and Gears' `get_setting` reads the PROPERTY FIRST. A write that
only lands on disk stays invisible until that cache is rebuilt, i.e. the user
flips the switch, opens a source list and sees nothing change. So every write
here updates the live cache too.

Gears also mirrors every "action" setting into a `<id>_name` row that the
settings screen displays ('Include' / 'Exclude'). Writing the value without the
name leaves the screen saying Include over a value of 1, so both rows are
written.

FRESH INSTALLS
--------------
Gears' settings.db is created the first time Gears runs, which on a fresh
install is AFTER this question is asked. When the db is missing the values are
stashed in the same first-boot catch-up file the kept-credentials flow uses
(gears_keep_pending.json), so they land as soon as the db exists.
"""
import json
import os
import sqlite3

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

WIZARD_ID = 'plugin.program.masterkodi.il.wizard'
POV_ID = 'plugin.video.pov'
GEARS_ID = 'plugin.video.gears'

USERDATA = xbmcvfs.translatePath('special://userdata/')
ADDON_DATA = os.path.join(USERDATA, 'addon_data')
GEARS_SETTINGS_DB = os.path.join(ADDON_DATA, GEARS_ID, 'databases', 'settings.db')

# 0 = Include (show everything), 1 = Exclude (hide it) -- both engines.
INCLUDE, EXCLUDE = '0', '1'
POV_IDS = ('filter_hdr', 'filter_dv')
GEARS_IDS = ('filter.hdr', 'filter.dv')
_GEARS_OPTIONS = {INCLUDE: 'Include', EXCLUDE: 'Exclude'}


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s.sdr] %s' % (WIZARD_ID, msg), level)


def _installed(addon_id):
    try:
        xbmcaddon.Addon(addon_id)
        return True
    except Exception:
        return False


# --------------------------------------------------------------------- POV
def _pov_apply(value):
    """POV: Kodi's own settings API (POV runs a service -- writing its
    settings.xml by hand while Kodi is up is the mistake this build has a hard
    rule against), then patch the live cache blob."""
    try:
        addon = xbmcaddon.Addon(POV_ID)
    except Exception as e:
        log('POV not installed (%s) -- skipped' % e)
        return None
    try:
        for sid in POV_IDS:
            addon.setSetting(sid, value)
        # readback through the same API: prove it landed, never assume
        back = dict((sid, xbmcaddon.Addon(POV_ID).getSetting(sid)) for sid in POV_IDS)
        ok = all(v == value for v in back.values())
        if not ok:
            log('POV write UNVERIFIED: %r' % back, xbmc.LOGERROR)
        _pov_refresh_cache(value)
        _pov_confirm_on_disk(value)
        return ok
    except Exception as e:
        log('POV write failed: %s' % e, xbmc.LOGERROR)
        return False


def _pov_confirm_on_disk(value):
    """Log whether the value really reached settings.xml.

    Kodi saves an addon's settings the moment setSetting is called, which is why
    a plugin's debrid token survives a crash -- but the install asks this
    question seconds before a HARD Kodi exit, so it is worth having the evidence
    in the log rather than the assumption. READ ONLY: writing that file by hand
    while Kodi runs is the mistake this build has a rule against."""
    try:
        p = os.path.join(ADDON_DATA, POV_ID, 'settings.xml')
        if not os.path.isfile(p):
            log('POV settings.xml not on disk yet (Kodi may write it on exit)',
                xbmc.LOGWARNING)
            return
        txt = open(p, encoding='utf-8', errors='replace').read()
        landed = all('<setting id="%s">%s</setting>' % (sid, value) in txt
                     for sid in POV_IDS)
        log('POV settings.xml on disk: %s' % ('confirmed' if landed else 'NOT YET'),
            xbmc.LOGINFO if landed else xbmc.LOGWARNING)
    except Exception as e:
        log('POV disk check failed (harmless): %s' % e, xbmc.LOGWARNING)


def _pov_refresh_cache(value):
    """POV reads settings out of a JSON blob in a HOME window property. Leave it
    stale and the running Kodi keeps the OLD value until the blob is rebuilt."""
    try:
        win = xbmcgui.Window(10000)
        raw = win.getProperty('pov_settings')
        if not raw:
            return                 # not built yet -> POV builds it with the new value
        data = json.loads(raw)
        for sid in POV_IDS:
            data[sid] = value
        win.setProperty('pov_settings', json.dumps(data))
        log('POV live settings cache refreshed')
    except Exception as e:
        log('POV cache refresh failed (value still written): %s' % e, xbmc.LOGWARNING)


# ------------------------------------------------------------------- Gears
def _gears_rows(value):
    """(setting_id, setting_type, setting_default, setting_value) for the value
    rows AND the `_name` rows the settings screen displays."""
    rows = []
    for sid in GEARS_IDS:
        rows.append((sid, 'action', INCLUDE, value))
        rows.append(('%s_name' % sid, 'name', '', _GEARS_OPTIONS[value]))
    return rows


def _gears_apply(value):
    """Gears: sqlite settings.db + the per-id HOME window properties its
    get_setting() reads FIRST. Returns True/False, or 'deferred' when the db
    isn't born yet (fresh install)."""
    if not _installed(GEARS_ID):
        log('Gears not installed -- skipped')
        return None
    rows = _gears_rows(value)
    # live cache first: it is what the current Kodi session actually reads
    try:
        win = xbmcgui.Window(10000)
        for sid, _t, _d, val in rows:
            win.setProperty('gears.%s' % sid, val)
    except Exception as e:
        log('Gears property refresh failed: %s' % e, xbmc.LOGWARNING)

    if not os.path.isfile(GEARS_SETTINGS_DB):
        return 'deferred' if _gears_defer(value) else False
    try:
        # timeout: Gears' own service can hold the db for a moment. Waiting beats
        # reporting a failure the user would have to redo.
        con = sqlite3.connect(GEARS_SETTINGS_DB, timeout=5)
        for row in rows:
            con.execute('INSERT OR REPLACE INTO settings '
                        '(setting_id, setting_type, setting_default, setting_value) '
                        'VALUES (?, ?, ?, ?)', row)   # named: survives a column reorder
        con.commit()
        for sid, _t, _d, val in rows:       # readback, same rule as POV
            got = con.execute('SELECT setting_value FROM settings WHERE setting_id=?',
                              (sid,)).fetchone()
            if not got or got[0] != val:
                con.close()
                log('Gears write UNVERIFIED for %s' % sid, xbmc.LOGERROR)
                return False
        con.close()
        return True
    except Exception as e:
        log('Gears write failed: %s' % e, xbmc.LOGERROR)
        return False


def _gears_defer(value):
    """No settings.db yet (fresh install -- Gears creates it on first run). Hand
    the values to the SAME first-boot catch-up that restores kept credentials."""
    try:
        from resources.libs import keep
        return bool(keep._stash_keep_pending(
            dict((sid, val) for sid, _t, _d, val in _gears_rows(value))))
    except Exception as e:
        log('Gears defer failed: %s' % e, xbmc.LOGERROR)
        return False


# -------------------------------------------------------------------- API
def apply_sdr_only(enable=True):
    """Turn the persistent SDR-only filter on (or off).

    Returns {'pov': result, 'gears': result} where a result is True (written),
    False (failed), 'deferred' (will land on first boot) or None (engine not
    installed -- not a failure)."""
    value = EXCLUDE if enable else INCLUDE
    out = {'pov': _pov_apply(value), 'gears': _gears_apply(value)}
    log('SDR-only %s -> %r' % ('ON' if enable else 'OFF', out))
    return out


def failures(result):
    """The engines that genuinely failed (not-installed and deferred are fine)."""
    return [k for k, v in result.items() if v is False]


def is_enabled():
    """True only when an engine is actually filtering. Reads what is on the box,
    never what we think we wrote."""
    return any(v is True for v in status().values())


def status():
    """{'pov': True/False/None, 'gears': ...} -- None = engine not installed."""
    out = {'pov': None, 'gears': None}
    if _installed(POV_ID):
        try:
            addon = xbmcaddon.Addon(POV_ID)
            out['pov'] = all(addon.getSetting(sid) == EXCLUDE for sid in POV_IDS)
        except Exception as e:
            log('POV status failed: %s' % e, xbmc.LOGWARNING)
    if _installed(GEARS_ID) and os.path.isfile(GEARS_SETTINGS_DB):
        try:
            con = sqlite3.connect(GEARS_SETTINGS_DB, timeout=5)
            vals = []
            for sid in GEARS_IDS:
                row = con.execute('SELECT setting_value FROM settings WHERE setting_id=?',
                                  (sid,)).fetchone()
                vals.append(row[0] if row else INCLUDE)
            con.close()
            out['gears'] = all(v == EXCLUDE for v in vals)
        except Exception as e:
            log('Gears status failed: %s' % e, xbmc.LOGWARNING)
    return out
