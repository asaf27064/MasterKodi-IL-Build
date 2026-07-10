# -*- coding: utf-8 -*-
# Skip button overlay. Renders over FULLSCREEN VIDEO without needing the OSD,
# using a WindowXMLDialog(type="dialog") shown via doModal() + a background
# poll thread that auto-closes it. (A plain WindowDialog.show() only appeared
# with the OSD up -- that was the bug.) Pattern adapted from TheIntroDB's addon.
import os
import threading
import time

import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo('path')

BUTTON_ID = 3001
BG_SHADOW = 3003
ICON_ID = 3006
BAR_TRACK = 3007
BAR_FILL = 3008
ACTION_SELECT = 7
ACTION_PREVIOUS_MENU = 10
ACTION_BACK = 92

DISPLAY_DURATION = 10.0    # legacy fallback when segment bounds are unknown
SAFETY_MAX = 180.0         # hard cap so a frozen player never leaves the pill up
POLL = 0.1                 # fine-grained so the countdown bar drains smoothly
START_POLL = 0.25
CLOCK_EPS = 0.05


def _res():
    try:
        return '1080i' if xbmc.getScreenHeight() >= 1080 else '720p'
    except Exception:
        return '1080i'


def _skin_tex(name):
    return xbmcvfs.translatePath(os.path.join(
        ADDON_PATH, 'resources', 'skins', 'Default', _res(), name))


def _tex():
    return _skin_tex('rounded_rect.png')


class SkipOverlay(xbmcgui.WindowXMLDialog):
    def __new__(cls, xml, path, skin, res, **kw):
        return super(SkipOverlay, cls).__new__(cls, xml, path, skin, res)

    def __init__(self, xml, path, skin, res, label='דלג', start=None, target=None, player=None, monitor=None):
        super(SkipOverlay, self).__init__(xml, path, skin, res)
        self._label = label
        self._start = start
        self._target = target
        self._player = player
        self._monitor = monitor
        self.skip_pressed = False
        self._closed = False
        self._lock = threading.Lock()
        self._deadline = None
        self._poll_thread = None
        self._fill_full = 0

    def onInit(self):
        try:
            tex = _tex()
            self.getControl(BG_SHADOW).setImage(tex)
            self.getControl(BAR_TRACK).setImage(tex)
            fill = self.getControl(BAR_FILL)
            fill.setImage(tex)
            self._fill_full = fill.getWidth()      # remember full width for the drain
        except Exception as e:
            xbmc.log('[skipintro] overlay tex: %s' % e, xbmc.LOGWARNING)
        try:
            self.getControl(ICON_ID).setImage(_skin_tex('skip_next.png'))
        except Exception:
            pass
        try:
            b = self.getControl(BUTTON_ID)
            if isinstance(b, xbmcgui.ControlButton):
                b.setLabel(self._label)
        except Exception:
            pass
        try:
            self.setFocusId(BUTTON_ID)
        except Exception:
            pass
        if self._target is not None and self._player is not None:
            # Safety cap only; the pill normally closes when playback reaches the
            # segment end (self._target), so it stays up for the WHOLE intro.
            self._deadline = time.time() + SAFETY_MAX
            self._poll_thread = threading.Thread(target=self._poll)
            self._poll_thread.daemon = True
            self._poll_thread.start()

    def onClick(self, controlId):
        if controlId == BUTTON_ID:
            self._do_skip()

    def onAction(self, action):
        aid = action.getId()
        if aid == ACTION_SELECT:
            try:
                if self.getFocusId() == BUTTON_ID:
                    self._do_skip()
            except Exception:
                pass
            return
        if aid in (ACTION_PREVIOUS_MENU, ACTION_BACK):
            self._dismiss()

    def _do_skip(self):
        with self._lock:
            if self._closed:
                return
            self.skip_pressed = True
        self._dismiss()

    def _poll(self):
        mon = self._monitor or xbmc.Monitor()
        while True:
            with self._lock:
                if self._closed:
                    return
            if mon.waitForAbort(POLL):
                return self._close_bg()
            try:
                if self._deadline and time.time() >= self._deadline:
                    return self._close_bg()        # safety cap (frozen player)
                p = self._player
                if p and p.isPlaying() and self._target is not None:
                    t = p.getTime()
                    if t >= self._target:
                        return self._close_bg()    # intro ended -> hide
                    # Countdown bar tracks PLAYBACK progress toward the intro end,
                    # so it reads as "time left to skip" and the pill stays up for
                    # the whole intro instead of a fixed 10s flash.
                    if self._fill_full:
                        if self._start is not None and self._target > self._start:
                            frac = (self._target - t) / (self._target - self._start)
                        else:
                            frac = (self._deadline - time.time()) / DISPLAY_DURATION
                        frac = max(0.0, min(1.0, frac))
                        try:
                            self.getControl(BAR_FILL).setWidth(int(self._fill_full * frac))
                        except Exception:
                            pass
                elif p and not p.isPlaying():
                    return self._close_bg()
            except Exception:
                pass

    def _close_bg(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.close()
        except Exception:
            pass

    def _dismiss(self):
        with self._lock:
            self._closed = True
        try:
            self.close()
        except Exception:
            pass


def show_skip_overlay(label, start, target, player, monitor):
    """Blocks until the pill closes (skip / intro end / safety cap). Stays up for
    the whole intro. Returns True if the user pressed skip."""
    mon = monitor or xbmc.Monitor()
    if mon.abortRequested():
        return False
    if not _wait_clock(player, mon, target):
        return False
    try:
        w = SkipOverlay('SkipIntro.xml', ADDON_PATH, 'Default', _res(),
                        label=label, start=start, target=target, player=player, monitor=monitor)
        w.doModal()
        pressed = w.skip_pressed
        del w
        return pressed
    except Exception as e:
        xbmc.log('[skipintro] overlay error: %s' % e, xbmc.LOGERROR)
        return False


def _wait_clock(player, monitor, target):
    """Wait until the playback clock is actually advancing before opening, so
    the poll thread doesn't instantly close the pill on a stale getTime()."""
    if player is None:
        return True
    prev = None
    while not monitor.abortRequested():
        try:
            if player.isPlaying():
                t = player.getTime()
                if target is not None and t >= target:
                    return False
                if prev is not None and t > prev + CLOCK_EPS:
                    return True
                prev = t
            else:
                prev = None
        except Exception:
            prev = None
        if monitor.waitForAbort(START_POLL):
            return False
    return False
