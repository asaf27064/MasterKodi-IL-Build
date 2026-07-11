# -*- coding: utf-8 -*-
# MasterKodi Skip Intro
#
# Shows a "Skip Intro" / "Skip Recap" pill over fullscreen video. Data source:
# TheIntroDB + SkipDB (skipdb.py), with the file's chapter markers as a last
# fallback. The pill is a WindowXMLDialog shown via doModal() + a poll thread
# (overlay.py) so it renders over video WITHOUT needing the OSD. Fail-safe: no
# data -> no pill, ever. Outro/credits are left to Gears' Next Episode.

import os
import sys
import xbmc
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
sys.path.insert(0, ADDON_PATH)            # so `import skipdb` / `import overlay` work

try:
    import skipdb
except Exception:
    skipdb = None

LABELS = {'intro': 'דלג על הפתיח', 'recap': 'דלג על התקציר'}

MIN_INTRO, MAX_INTRO = 20, 150
LATEST_START, LATEST_END = 300, 360


def log(msg, level=xbmc.LOGINFO):
    xbmc.log('[skipintro] ' + msg, level)


def _get_bool(key, default=True):
    try:
        return ADDON.getSettingBool(key)
    except Exception:
        raw = ADDON.getSetting(key)
        return default if raw == '' else raw.lower() == 'true'


class SkipService(xbmc.Monitor):
    def __init__(self):
        xbmc.Monitor.__init__(self)
        self.player = xbmc.Player()

    def _file(self):
        try:
            return self.player.getPlayingFile()
        except Exception:
            return None

    def _time(self):
        try:
            return float(self.player.getTime())
        except Exception:
            return -1

    def _media_ids(self):
        imdb = season = episode = ''
        try:
            tag = self.player.getVideoInfoTag()
            if tag:
                imdb = (tag.getIMDBNumber() or '').strip()
                if not imdb:
                    try:
                        imdb = (tag.getUniqueID('imdb') or '').strip()
                    except Exception:
                        pass
                s, e = tag.getSeason(), tag.getEpisode()
                if s is not None and s >= 0:
                    season = str(s)
                if e is not None and e >= 1:
                    episode = str(e)
        except Exception:
            pass
        if not imdb:
            imdb = (xbmc.getInfoLabel('VideoPlayer.IMDBNumber') or '').strip()
        if not season:
            season = (xbmc.getInfoLabel('VideoPlayer.Season') or '').strip()
        if not episode:
            episode = (xbmc.getInfoLabel('VideoPlayer.Episode') or '').strip()
        return imdb, season, episode

    def _from_db(self):
        if not skipdb or not _get_bool('use_skipdb', True):
            return []
        imdb, season, episode = self._media_ids()
        if not imdb:
            return None                      # ids not ready yet -> retry
        try:
            duration = float(self.player.getTotalTime())
        except Exception:
            duration = 0
        data = skipdb.get_segments(imdb, season, episode, duration)
        log('db imdb=%s s=%s e=%s dur=%.0f -> %s' % (imdb, season, episode, duration, data))
        segs = []
        for kind in ('intro', 'recap'):
            if kind in data:
                segs.append((kind, data[kind][0], data[kind][1]))
        return segs

    def _from_chapters(self):
        try:
            csv = xbmc.getInfoLabel('Player.Chapters') or ''
            pcts = []
            for part in csv.split(','):
                part = part.strip()
                if part:
                    try:
                        pcts.append(float(part))
                    except ValueError:
                        pass
            try:
                total = float(self.player.getTotalTime())
            except Exception:
                total = 0
            if total <= 0 or len(pcts) < 2:
                return []
            secs = [p / 100.0 * total for p in pcts]
            if secs[0] > 1:
                secs = [0.0] + secs
            for i in range(1, len(secs)):
                a, b = secs[i - 1], secs[i]
                # Must start after a cold-open (a >= 10): a chapter from 0 is the
                # opening scene, not a skippable intro (the HotD false positive).
                if 10 <= a <= LATEST_START and MIN_INTRO <= (b - a) <= MAX_INTRO and b <= LATEST_END:
                    return [('intro', a, b)]
            return []
        except Exception:
            return []

    def _detect(self):
        segs = self._from_db()
        if segs is None:
            return None
        if segs:
            return segs
        return self._from_chapters()

    def run(self):
        import overlay
        log('service started (use_skipdb=%s)' % _get_bool('use_skipdb', True))
        last_file = None
        segs = None
        attempts = 0
        shown = set()
        while not self.abortRequested():
            try:
                if _get_bool('enabled', True) and self.player.isPlayingVideo():
                    f = self._file()
                    if f != last_file:
                        last_file = f
                        segs = None
                        attempts = 0
                        shown = set()
                    if segs is None:
                        result = self._detect()
                        attempts += 1
                        if result is not None:
                            segs = result
                            if segs:
                                log('resolved %s' % segs)
                        elif attempts >= 12:
                            segs = []
                    if segs:
                        t = self._time()
                        for i, (kind, start, end) in enumerate(segs):
                            if i in shown:
                                continue
                            # Don't pop the pill during the chaotic first seconds
                            # of playback (startup OSD / autosub) -- it gets
                            # dismissed instantly. Wait at least a few seconds in.
                            show_at = max(start, 4.0)
                            if show_at <= t < (end - 1):
                                shown.add(i)
                                if _get_bool('auto_skip', False):
                                    try:
                                        self.player.seekTime(float(end))
                                    except Exception:
                                        pass
                                else:
                                    pressed = overlay.show_skip_overlay(
                                        LABELS.get(kind, 'דלג'), float(start), float(end), self.player, self)
                                    if pressed:
                                        try:
                                            self.player.seekTime(float(end))
                                        except Exception:
                                            pass
                                break
                else:
                    last_file = None
                    segs = None
                    shown = set()
            except Exception as e:
                xbmc.log('[skipintro] loop error: %s' % e, xbmc.LOGDEBUG)
            if self.waitForAbort(1):
                break


if __name__ == '__main__':
    try:
        SkipService().run()
    except Exception as e:
        xbmc.log('[skipintro] fatal: %s' % e, xbmc.LOGERROR)
