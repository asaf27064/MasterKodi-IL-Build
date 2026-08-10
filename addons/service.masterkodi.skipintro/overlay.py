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
    # Overridden by SkipBarOverlay; the poll/close machinery only touches these.
    BTN_ID = BUTTON_ID
    BAR_FILL_ID = BAR_FILL

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
        self.declined = False        # X pressed: don't re-show for this segment
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
        if controlId == self.BTN_ID:
            self._do_skip()

    def onAction(self, action):
        aid = action.getId()
        if aid == ACTION_SELECT:
            try:
                if self.getFocusId() == self.BTN_ID:
                    self._do_skip()
            except Exception:
                pass
            return
        # ANY other input (open OSD, seek, info, context, nav, back) dismisses the
        # pill so it never blocks the OSD or playback controls -- Netflix/Nuvio
        # style: the skip prompt yields the instant you interact with the player.
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
                # If the video OSD opens (by any route), get out of its way.
                if xbmc.getCondVisibility('Window.IsVisible(videoosd)'):
                    return self._close_bg()
                p = self._player
                if p and p.isPlaying() and self._target is not None:
                    t = p.getTime()
                    if t >= self._target:
                        return self._close_bg()    # intro ended -> hide
                    # Countdown bar tracks PLAYBACK progress toward the intro end,
                    # so it reads as "time left to skip" and the pill stays up for
                    # the whole intro instead of a fixed 10s flash.
                    if self._start is not None and self._target > self._start:
                        frac = (self._target - t) / (self._target - self._start)
                    else:
                        frac = (self._deadline - time.time()) / DISPLAY_DURATION
                    frac = max(0.0, min(1.0, frac))
                    try:
                        self._update_bar(frac)
                    except Exception:
                        pass
                elif p and not p.isPlaying():
                    return self._close_bg()
            except Exception:
                pass

    def _update_bar(self, frac):
        # pill: an image whose width drains (texture set programmatically)
        if self._fill_full:
            self.getControl(self.BAR_FILL_ID).setWidth(int(self._fill_full * frac))

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


# POV-style top bar (Asaf 2026-08-09: "copy his design, keep ours as another
# style"). Layout cloned from plugin.video.pov's episodes.xml; the poll/close/
# dismiss machinery is inherited unchanged from SkipOverlay, so both styles
# behave identically (stay up the whole intro, yield to the OSD, safety cap).
BAR_BTN_SKIP = 3101
BAR_BTN_CLOSE = 3102
BAR_FILL_BAR = 3108


class SkipBarOverlay(SkipOverlay):
    BTN_ID = BAR_BTN_SKIP
    BAR_FILL_ID = BAR_FILL_BAR

    def onInit(self):
        try:
            self.setProperty('mk.skip.icon',
                             os.path.join(ADDON_PATH, 'icon.png'))
            # POV feeds these from its own player meta; we serve ANY player, so
            # the InfoLabels of whatever is playing are the equivalent source.
            title = xbmc.getInfoLabel('VideoPlayer.TVShowTitle') or xbmc.getInfoLabel('VideoPlayer.Title') or ''
            season = xbmc.getInfoLabel('VideoPlayer.Season')
            episode = xbmc.getInfoLabel('VideoPlayer.Episode')
            ep_name = xbmc.getInfoLabel('VideoPlayer.EpisodeName')
            parts = [title]
            if season and episode:
                parts.append('S%sE%s' % (season, episode))
            if ep_name:
                parts.append(ep_name)
            self.setProperty('mk.skip.title', ' [B]|[/B] '.join(p for p in parts if p))
            poster = (xbmc.getInfoLabel('Player.Art(tvshow.poster)')
                      or xbmc.getInfoLabel('Player.Art(poster)') or '')
            self.setProperty('mk.skip.poster', poster)
            self.setProperty('mk.skip.prompt', self._label or 'לדלג על הפתיח?')
        except Exception as e:
            xbmc.log('[skipintro] bar props: %s' % e, xbmc.LOGWARNING)
        try:
            self.getControl(self.BAR_FILL_ID).setPercent(100)
        except Exception:
            pass
        try:
            self.setFocusId(self.BTN_ID)
        except Exception:
            pass
        if self._target is not None and self._player is not None:
            self._deadline = time.time() + SAFETY_MAX
            self._poll_thread = threading.Thread(target=self._poll)
            self._poll_thread.daemon = True
            self._poll_thread.start()

    def _update_bar(self, frac):
        # bar: Kodi progress control -> the active skin styles the fill
        self.getControl(self.BAR_FILL_ID).setPercent(int(frac * 100))

    def onClick(self, controlId):
        if controlId == self.BTN_ID:
            self._do_skip()
        elif controlId == BAR_BTN_CLOSE:
            self._decline()

    def onAction(self, action):
        # The pill dismisses on ANY input because it has one button and must
        # never block the OSD. The bar has TWO buttons, so arrow keys are
        # navigation between them, not "get out of my way" -- swallowing them
        # made the bar vanish the moment you pressed down (Asaf, 2026-08-09).
        aid = action.getId()
        if aid in (1, 2, 3, 4):          # left/right/up/down: let Kodi move focus
            return
        if aid == ACTION_SELECT:
            try:
                fid = self.getFocusId()
            except Exception:
                fid = self.BTN_ID
            if fid == BAR_BTN_CLOSE:
                self._decline()
            else:
                self._do_skip()
            return
        self._dismiss()                  # back / OSD / anything else: yield

    def _decline(self):
        # X is an ANSWER ("no, don't skip"), not a mere yield: the service must
        # not re-show this segment. Anything else (back/OSD) stays a yield and
        # the bar may return while still inside the intro (Asaf, 2026-08-09).
        self.declined = True
        self._dismiss()


def show_skip_overlay(label, start, target, player, monitor):
    """Blocks until the overlay closes (skip / intro end / safety cap). Stays up
    for the whole intro. Returns (pressed, declined): pressed=True means seek
    past the segment; declined=True means the user answered NO (bar-style X) and
    the segment must not be offered again."""
    mon = monitor or xbmc.Monitor()
    if mon.abortRequested():
        return False, False
    if not _wait_clock(player, mon, target):
        return False, False
    # style: '0'/'' = POV-style top bar (default), '1' = the original pill
    try:
        style = ADDON.getSetting('overlay_style')
    except Exception:
        style = ''
    cls, xml = ((SkipOverlay, 'SkipIntro.xml') if style == '1'
                else (SkipBarOverlay, 'SkipIntroBar.xml'))
    try:
        w = cls(xml, ADDON_PATH, 'Default', _res(),
                        label=label, start=start, target=target, player=player, monitor=monitor)
        w.doModal()
        pressed, declined = w.skip_pressed, w.declined
        del w
        return pressed, declined
    except Exception as e:
        xbmc.log('[skipintro] overlay error: %s' % e, xbmc.LOGERROR)
        return False, False


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
