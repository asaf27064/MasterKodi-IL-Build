# -*- coding: utf-8 -*-
"""Two small background helpers that make the build FEEL faster.

1. Bytecode precompile (precompile_pass / precompile_if_due)
   Python compiles a module from source the first time it is imported after
   the file changed and caches the result in __pycache__. Every add-on update
   therefore turns the user's next visits into "compile the engine" visits:
   the first open of each list pays 0.05-0.2 s on a PC and several times that
   on an Android box, on top of the busy wheel the user already waits for.
   This compiles every add-on source file ONCE, in idle time, with the exact
   same interpreter the add-ons run in (same magic number, same optimisation
   level -- Kodi runs Python with -O on Windows, so the cache tag is
   "opt-1" there and plain elsewhere; py_compile with optimize=-1 follows
   whatever this interpreter is). Only stale or missing caches are rebuilt,
   validated the way Python itself does (source mtime + size in the .pyc
   header), so a second pass is a cheap stat sweep.
   Paced: one file at a time with a short pause (Kodi's add-on interpreters
   share one GIL), never while a video plays, and the summary line says how
   many files actually needed compiling -- that number is the proof of how
   much the pass is worth on a given platform.

2. Busy-tap notice (start_busy_watcher)
   A tap on a movie or episode starts the engine's interpreter and its
   imports BEFORE the engine can show its own progress window. On a slow box
   that gap is long enough for the user to think the tap was ignored and tap
   again. If Kodi's busy wheel has been up for a moment after a tap on a
   PLAYABLE item and no add-on window has appeared yet, one short toast says
   sources are being looked up. Nothing is shown once the engine's own window
   is up, and nothing is shown for folder navigation.
"""
import importlib.util
import os
import py_compile
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDONS_PATH = xbmcvfs.translatePath('special://home/addons/')

PRECOMPILE_INTERVAL = 6 * 3600     # seconds between full sweeps
PRECOMPILE_PAUSE = 0.02            # seconds between files (shared GIL)
PRECOMPILE_MAX_SECONDS = 300       # a sweep never holds the service longer
BUSY_NOTICE_AFTER = 1.5            # seconds of busy wheel before the toast
BUSY_NOTICE_MS = 2500

_state = {'last_pass': 0.0, 'watcher': None}


def _log(msg, level=xbmc.LOGINFO):
    xbmc.log('[%s] %s' % (ADDON_ID, msg), level)


def _setting_on(key, default=True):
    try:
        v = ADDON.getSetting(key)
    except Exception:
        return default
    if v == '':
        return default
    return v == 'true'


# ---------------------------------------------------------------- bytecode

def _pyc_is_current(py_path, pyc_path):
    """True when the cached bytecode would be ACCEPTED by this interpreter:
    same magic number, timestamp-mode header, and the header's source mtime
    and size match the .py on disk. This is Python's own validation, so a
    file Python would recompile on import is exactly a file we recompile."""
    try:
        st = os.stat(py_path)
        with open(pyc_path, 'rb') as fh:
            head = fh.read(16)
        if len(head) < 16 or head[:4] != importlib.util.MAGIC_NUMBER:
            return False
        flags = int.from_bytes(head[4:8], 'little')
        if flags != 0:               # hash-based pyc: leave it to Python
            return True
        mtime = int.from_bytes(head[8:12], 'little')
        size = int.from_bytes(head[12:16], 'little')
        return mtime == (int(st.st_mtime) & 0xFFFFFFFF) and size == (st.st_size & 0xFFFFFFFF)
    except Exception:
        return False


def _iter_sources():
    """Every add-on .py under special://home/addons, skipping caches, git
    junk and the '<addon>_old_<ts>' backups left by updates."""
    try:
        tops = sorted(os.listdir(ADDONS_PATH))
    except Exception:
        return
    for top in tops:
        if '_old_' in top or top.startswith('.') or top == 'packages':
            continue
        root = os.path.join(ADDONS_PATH, top)
        if not os.path.isdir(root):
            continue
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in ('__pycache__', '.git', '.github', 'node_modules')]
            for f in files:
                if f.endswith('.py'):
                    yield os.path.join(d, f)


def precompile_pass(monitor, reason='scheduled'):
    """One sweep. Returns (scanned, compiled, failed) or None when skipped."""
    if not _setting_on('precompile_bytecode', True):
        return None
    # Kodi's interpreter runs with sys.dont_write_bytecode = True (measured on
    # Windows 2026-10-22: a full session imported POV, TMDb Helper and their
    # libraries and wrote 0 .pyc files). That flag only stops the IMPORT
    # system from writing caches; it still READS a valid one, and
    # py_compile writes regardless of it. So this sweep is what gives Kodi
    # bytecode at all, on every platform.
    started = time.time()
    scanned = compiled = failed = 0
    player = xbmc.Player()
    for py_path in _iter_sources():
        if monitor.abortRequested():
            return None
        if time.time() - started > PRECOMPILE_MAX_SECONDS:
            _log('bytecode: sweep time cap reached; continuing next time')
            break
        if player.isPlayingVideo():
            _log('bytecode: video started; sweep paused until next time')
            break
        scanned += 1
        try:
            pyc_path = importlib.util.cache_from_source(py_path)
        except Exception:
            continue
        if os.path.isfile(pyc_path) and _pyc_is_current(py_path, pyc_path):
            continue
        try:
            # optimize=-1: this interpreter's own level -> the cache tag the
            # add-on interpreters look for. quiet=1: no stderr on a bad file.
            py_compile.compile(py_path, cfile=pyc_path, doraise=True, optimize=-1, quiet=1)
            compiled += 1
        except Exception as e:
            failed += 1
            if failed <= 5:
                _log('bytecode: cannot compile %s: %s' % (py_path, e), xbmc.LOGDEBUG)
        if monitor.waitForAbort(PRECOMPILE_PAUSE):
            return None
    _state['last_pass'] = time.time()
    try:
        import sys
        tag = os.path.basename(importlib.util.cache_from_source('x.py'))
        env = 'optimize=%s dont_write=%s prefix=%s tag=%s' % (
            sys.flags.optimize, int(bool(sys.dont_write_bytecode)),
            getattr(sys, 'pycache_prefix', None) or '-', tag)
    except Exception:
        env = '?'
    _log('bytecode (%s): %d files, %d compiled, %d failed, %.1fs [%s]' % (
        reason, scanned, compiled, failed, time.time() - started, env))
    return scanned, compiled, failed


def precompile_if_due(monitor):
    """Idle-loop hook: run a sweep when the last one is older than the
    interval (and never while a video plays)."""
    try:
        if time.time() - _state['last_pass'] < PRECOMPILE_INTERVAL:
            return
        if xbmc.Player().isPlayingVideo():
            return
        precompile_pass(monitor, 'periodic')
    except Exception as e:
        _log('bytecode: sweep error: %s' % e, xbmc.LOGWARNING)


# ---------------------------------------------------------------- busy tap

# POV plays with mode=play_media, Gears with mode=playback.video /
# playback.meta / playback_choice, TMDb Helper's player route with info=play.
_PLAY_MARKERS = ('mode=play', 'info=play', 'action=play')
_BUSY_WINDOWS = (10138, 10160)     # busydialog, busydialognocancel


def _is_play_path(path):
    p = (path or '').lower()
    if not p.startswith('plugin://'):
        return False
    return any(m in p for m in _PLAY_MARKERS)


_BUSY_COND = 'Window.IsActive(busydialog) | Window.IsActive(busydialognocancel)'


def _busy_now():
    """Kodi's busy wheel is up: by its window id on the dialog stack, or by
    Kodi's own visibility condition (the wheel is not always the topmost
    modal the id call reports)."""
    try:
        if xbmcgui.getCurrentWindowDialogId() in _BUSY_WINDOWS:
            return True
    except Exception:
        pass
    return bool(xbmc.getCondVisibility(_BUSY_COND))


def _busy_watch_loop(monitor):
    icon = ADDON.getAddonInfo('icon')
    armed_at = None
    shown = False
    path_at_tap = ''
    while not monitor.abortRequested():
        if monitor.waitForAbort(0.25):
            return
        try:
            if not _setting_on('busy_tap_notice', True):
                continue
            busy = _busy_now()
            if not busy:
                if armed_at is not None:
                    _log('busy spell %.2fs play=%s shown=%s' % (
                        time.time() - armed_at, _is_play_path(path_at_tap), shown), xbmc.LOGDEBUG)
                armed_at = None
                shown = False
                continue
            if armed_at is None:
                # the focused item is still the one that was tapped
                armed_at = time.time()
                path_at_tap = xbmc.getInfoLabel('ListItem.FilenameAndPath') or xbmc.getInfoLabel('ListItem.Path')
                continue
            if shown or time.time() - armed_at < BUSY_NOTICE_AFTER:
                continue
            if not _is_play_path(path_at_tap):
                shown = True            # a folder: stay quiet for this busy spell
                continue
            shown = True
            xbmcgui.Dialog().notification('MasterKodi IL', 'מחפש מקורות, עוד רגע...',
                                          icon, BUSY_NOTICE_MS, False)
            _log('busy-tap notice shown after %.1fs (%s)' % (time.time() - armed_at, path_at_tap[:80]))
        except Exception as e:
            _log('busy watcher error: %s' % e, xbmc.LOGDEBUG)


def start_busy_watcher(monitor):
    """Start the watcher thread once (daemon: dies with the service)."""
    if _state['watcher'] is not None:
        return
    t = threading.Thread(target=_busy_watch_loop, args=(monitor,), name='mk_busy_tap')
    t.daemon = True
    t.start()
    _state['watcher'] = t
