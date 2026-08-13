# -*- coding: utf-8 -*-
"""OLED screen-protection settings.

WHY THIS IS NOT A FILE EDIT
---------------------------
The original code wrote guisettings.xml directly. That does not work while Kodi
is running: Kodi holds its settings in memory and rewrites the whole file when
it exits, so an edit made mid-session is discarded. Both OLED entry points did
exactly that -- the installer writes the file and then RESTARTS Kodi, which is
the worst possible ordering.

Measured on Asaf's box 2026-08-13: after writing the four values and closing
Kodi normally, `screensaver.time` and `screensaver.disableforaudio` survived
(they already existed in the file with a value) but

    <setting id="screensaver.mode" />

came back EMPTY -- and that is the one setting that actually turns the black
screensaver on. So the feature silently did nothing, on the Windows box and on
the living-room box alike.

Kodi's settings API changes the value INSIDE the running Kodi, so it is saved
on exit like any change the user makes in the GUI. That is what this module
uses.

Types matter: Settings.SetSettingValue is typed, so `screensaver.time` must be
sent as an int and the booleans as real booleans -- a string "1" or "false" is
rejected.
"""
import json

import xbmc

# setting id -> value, with the type Kodi expects
OLED_SETTINGS = (
    ('screensaver.mode', 'screensaver.xbmc.builtin.black'),  # black, not dim
    ('screensaver.time', 1),                                 # minutes (int)
    ('screensaver.disableforaudio', False),                  # still blank on music
    ('screensaver.usedimonpause', True),                     # dim a paused frame
    # Second layer: actually cut the video signal when idle. Kodi's own help
    # for this setting reads "Turn off display when idle. Useful for TVs that
    # turn off when there is no display signal detected" -- so on an OLED it
    # powers the panel down rather than just painting it black.
    #
    # 5 minutes, Asaf's call (I had suggested 15 to avoid the TV switching off
    # during a long pause; he prefers the stronger protection). The black
    # screensaver above already blanks the panel after one minute, so this is
    # purely about cutting the signal on top of that.
    ('powermanagement.displaysoff', 5),                      # minutes (int)
)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[plugin.program.masterkodi.il.wizard.oled] %s' % msg, level)


def _set(setting_id, value):
    """Set one setting through Kodi's API. Returns True only if Kodi confirms."""
    req = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.SetSettingValue',
           'params': {'setting': setting_id, 'value': value}}
    try:
        resp = json.loads(xbmc.executeJSONRPC(json.dumps(req)))
    except Exception as e:
        log('%s: call failed: %s' % (setting_id, e), xbmc.LOGERROR)
        return False
    if resp.get('error'):
        log('%s: rejected by Kodi: %s' % (setting_id, resp['error']), xbmc.LOGERROR)
        return False
    # Kodi answers {"result": true} on success; anything else means it did not
    # take the value (wrong type, unknown id, ...) and we must not report success
    if resp.get('result') is not True:
        log('%s: not applied (result=%r)' % (setting_id, resp.get('result')),
            xbmc.LOGWARNING)
        return False
    return True


def apply_oled_settings():
    """Apply every OLED setting. Returns (applied, failed) as lists of ids."""
    applied, failed = [], []
    for setting_id, value in OLED_SETTINGS:
        if _set(setting_id, value):
            applied.append(setting_id)
            log('applied %s = %r' % (setting_id, value))
        else:
            failed.append(setting_id)
    log('OLED result: %d applied, %d failed%s'
        % (len(applied), len(failed), (' -> %s' % failed) if failed else ''))
    return applied, failed


def verify_oled_settings():
    """Read the values back out of Kodi. Used to prove the change actually stuck
    rather than trusting the write call."""
    out = {}
    for setting_id, _value in OLED_SETTINGS:
        req = {'jsonrpc': '2.0', 'id': 1, 'method': 'Settings.GetSettingValue',
               'params': {'setting': setting_id}}
        try:
            resp = json.loads(xbmc.executeJSONRPC(json.dumps(req)))
            out[setting_id] = (resp.get('result') or {}).get('value')
        except Exception as e:
            out[setting_id] = 'ERROR: %s' % e
    return out
