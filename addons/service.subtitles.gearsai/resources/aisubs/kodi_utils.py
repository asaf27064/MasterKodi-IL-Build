# -*- coding: utf-8 -*-
# Thin wrapper over the xbmc* modules. Centralised so the rest of the
# addon never imports xbmc directly -- makes the logic unit-testable
# off-device (these functions degrade to no-ops / defaults when the
# Kodi runtime isn't present) and keeps settings IDs in one place.

import os

try:
    import xbmc
    import xbmcaddon
    import xbmcvfs
    import xbmcgui
except ImportError:  # off-device (tests, linting)
    xbmc = xbmcaddon = xbmcvfs = xbmcgui = None

ADDON_ID = 'service.subtitles.gearsai'
LOG_TAG = 'GearsAI-Subs'


def _addon():
    return xbmcaddon.Addon(ADDON_ID) if xbmcaddon else None


# ---------- logging ----------
def log(msg, level=None):
    """Always log at a visible level by default. Gears' own modules
    comment their logger out in production; we keep ours on because a
    translation that silently fails is the worst possible UX."""
    if not xbmc:
        print('[{0}] {1}'.format(LOG_TAG, msg))
        return
    try:
        xbmc.log('[{0}] {1}'.format(LOG_TAG, msg), level if level is not None else xbmc.LOGINFO)
    except Exception:
        pass


# ---------- settings ----------
def get_setting(setting_id, default=''):
    a = _addon()
    if not a:
        return default
    try:
        val = a.getSetting(setting_id)
        return val if val not in (None, '') else default
    except Exception:
        return default


def set_setting(setting_id, value):
    a = _addon()
    if not a:
        return
    try:
        a.setSetting(setting_id, str(value))
    except Exception:
        pass


def get_bool(setting_id, default=False):
    val = get_setting(setting_id, None)
    if val is None:
        return default
    return str(val).strip().lower() in ('true', '1', 'yes', 'on')


def get_int(setting_id, default=0):
    try:
        return int(str(get_setting(setting_id, default)).strip())
    except (ValueError, TypeError):
        return default


# ---------- paths ----------
def translate_path(path):
    if xbmcvfs:
        try:
            return xbmcvfs.translatePath(path)
        except Exception:
            pass
    return path


def profile_dir():
    """addon_data dir for this addon; created if missing."""
    a = _addon()
    if a:
        try:
            p = translate_path(a.getAddonInfo('profile'))
            if not os.path.isdir(p):
                os.makedirs(p)
            return p
        except Exception:
            pass
    # off-device fallback
    p = os.path.join(os.path.expanduser('~'), '.gearsai')
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def temp_dir():
    return translate_path('special://temp/')


def addon_path():
    a = _addon()
    return translate_path(a.getAddonInfo('path')) if a else ''


# ---------- UI ----------
def notification(message, heading='MasterKodi AI Subs', time_ms=4000, icon=None):
    if not xbmcgui:
        log('NOTIFY: {0} | {1}'.format(heading, message))
        return
    try:
        if icon is None:
            a = _addon()
            icon = translate_path(a.getAddonInfo('icon')) if a else ''
        xbmcgui.Dialog().notification(heading, message, icon, time_ms)
    except Exception:
        pass


def ok_dialog(message, heading='MasterKodi AI Subs'):
    if not xbmcgui:
        log('OK: {0} | {1}'.format(heading, message))
        return
    try:
        xbmcgui.Dialog().ok(heading, message)
    except Exception:
        pass


def yesno_dialog(message, heading='MasterKodi AI Subs', yes='', no=''):
    """Modal yes/no. Returns True if the user chose Yes, else False
    (also False when no GUI is available)."""
    if not xbmcgui:
        return False
    try:
        return bool(xbmcgui.Dialog().yesno(heading, message, yeslabel=yes or '', nolabel=no or ''))
    except Exception:
        return False


def progress_bg(heading='MasterKodi AI Subs'):
    """Background progress bar (non-modal). Returns the handle or None.
    Caller must call .update(pct, message) and .close()."""
    if not xbmcgui:
        return None
    try:
        p = xbmcgui.DialogProgressBG()
        p.create(heading, '')
        return p
    except Exception:
        return None


def fmt_eta(seconds):
    """Human ETA in Hebrew-friendly short form."""
    if seconds is None:
        return ''
    seconds = int(seconds)
    if seconds < 60:
        return '~{0} שניות'.format(max(1, seconds))
    m, s = divmod(seconds, 60)
    return '~{0}:{1:02d} דק׳'.format(m, s)


def localize(string_id, fallback=''):
    a = _addon()
    if not a:
        return fallback
    try:
        s = a.getLocalizedString(string_id)
        return s if s else fallback
    except Exception:
        return fallback
