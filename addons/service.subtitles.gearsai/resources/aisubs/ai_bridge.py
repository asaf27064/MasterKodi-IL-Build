# -*- coding: utf-8 -*-
# On-demand AI Hebrew translation for the "AI translate" item in the subtitle
# dialog. Reuses the gearsai AI engine (now living in resources/aisubs/).
# Fully fail-open: returns None + a notification on any problem.

import os
import xbmc
import xbmcgui
import xbmcaddon


def _info():
    def g(label):
        return xbmc.getInfoLabel(label) or ''
    tvshow = g('VideoPlayer.TVShowTitle')
    return {
        'title': g('VideoPlayer.Title') or g('VideoPlayer.OriginalTitle'),
        'tvshow': tvshow,
        'year': g('VideoPlayer.Year'),
        'season': g('VideoPlayer.Season'),
        'episode': g('VideoPlayer.Episode'),
        'imdb': g('VideoPlayer.IMDBNumber'),
        'media_type': 'episode' if tvshow else 'movie',
    }


def _cast(info):
    """Cast + per-actor gender from TMDb, to sharpen Hebrew gender forms in the
    translation (Hebrew is heavily gendered). Best-effort -> [] on any problem,
    in which case the model infers gender from dialogue cues as before."""
    try:
        from resources.aisubs import tmdb
        return tmdb.cast_for(
            imdb_id=info.get('imdb', ''),
            media_type=('episode' if info.get('media_type') == 'episode' else 'movie')) or []
    except Exception as e:
        xbmc.log('[gearsai-ai] cast lookup failed: {0}'.format(e), xbmc.LOGWARNING)
        return []


def _set_origin(origin):
    """Record where the Hebrew came from (pool / Gemini) -- engine writes it
    next to the translated file so the subtitle window can tag the row."""
    try:
        from resources.modules import general
        general.ai_last_origin = origin
    except Exception:
        pass


def _is_manual():
    """True when the current download was an explicit user pick in the
    subtitle window (set by sub_window) -- no confirmation needed then."""
    try:
        from resources.modules import general
        return bool(getattr(general, 'ai_manual', False))
    except Exception:
        return False


def _notify(msg, time_ms=4500):
    """Toast notification -- visible over playback regardless of which UI
    (window / native dialog / auto popup) triggered the translation."""
    try:
        xbmcgui.Dialog().notification('MasterKodi AI', msg, time=time_ms)
    except Exception:
        pass


def _step(msg, pct=None):
    """Show a MasterKodi AI step in DarkSubs' native progress bar
    (general.show_msg / progress_msg, rendered by show_results)."""
    try:
        from resources.modules import general
        if pct is not None:
            general.progress_msg = int(pct)
        general.show_msg = 'MasterKodi AI · ' + msg
    except Exception:
        pass


def _pick_info():
    """(release_short, match%, site) of the sub being translated -- set by the
    auto path (place_sub) so the AI step can show which version it's using."""
    try:
        from resources.modules import general
        name = (getattr(general, 'ai_pick_name', '') or '').strip()
        if len(name) > 34:
            name = name[:33] + '…'
        site = (getattr(general, 'ai_pick_site', '') or '').strip().strip('[]')
        pct = getattr(general, 'ai_pick_pct', 0) or 0
        return name, pct, site
    except Exception:
        return '', 0, ''


def _pool_hebrew(info, release):
    """Return a ready-made Hebrew SRT from the community pool for the playing
    media (instant, free, no Gemini quota -- works even for keyless users), or
    None. Best-effort: any error -> None and we translate normally."""
    try:
        from resources.aisubs import pool
        if not pool.enabled():
            return None
        _step('בודק מאגר קהילתי…', 0)
        cands = pool.lookup(imdb=info['imdb'], title=info['tvshow'] or info['title'],
                            year=info['year'], season=info['season'], episode=info['episode'])
        if not cands:
            return None
        best = pool.best_match(cands, release)
        if not best:
            return None
        heb = pool.fetch(best.get('id'), 'he')
        if not (heb and heb.strip()):
            return None
        # Good release/timing match (or we can't tell -> no release detected):
        # use it as-is, instantly.
        if not release or best.get('_match', 0) >= 80:
            xbmc.log('[gearsai-ai] pool hit ({0}, sync {1}) -> instant Hebrew'.format(
                best.get('id'), best.get('_match', '?')), xbmc.LOGINFO)
            _step('נמצא תרגום מוכן במאגר · סנכרון {0}%'.format(best.get('_match', '?')), 100)
            _m = best.get('_match')
            _set_origin('מאגר קהילתי')
            _notify('נשלף מהמאגר הקהילתי' + (' · סנכרון {0}%'.format(_m) if _m else ''))
            return heb
        # Timing mismatch -> try re-timing this Hebrew onto the user's EXACT
        # release for free (no re-translation), using the stored English anchor.
        _step('מסנכרן תרגום מהמאגר לגרסה שלך…', 50)
        retimed = _pool_retime(best, heb, info, release)
        if retimed:
            _step('סונכרן מהמאגר לגרסה שלך', 100)
            _set_origin('מאגר קהילתי · סונכרן')
            _notify('נשלף מהמאגר הקהילתי · סונכרן לגרסה שלך')
        else:
            _set_origin('מאגר קהילתי')
            _notify('נשלף מהמאגר הקהילתי')
        return retimed or heb   # re-time failed -> as-is Hebrew still beats nothing
    except Exception as e:
        xbmc.log('[gearsai-ai] pool lookup failed: {0}'.format(e), xbmc.LOGWARNING)
    return None


def _pool_retime(best, hebrew_a, info, release):
    """Re-time a pooled Hebrew sub (translated for a DIFFERENT release) onto the
    user's release, using the pool's stored English anchor + a fresh English sub
    that already matches the user's release. Returns re-timed SRT or None."""
    try:
        from resources.aisubs import pool, resync, sources, match
        english_a = pool.fetch(best.get('id'), 'en')   # anchor it was translated from
        if not english_a:
            return None
        eng = sources.search_english(
            imdb_id=info['imdb'], title=info['tvshow'] or info['title'],
            media_type=info['media_type'], season=info['season'] or 0,
            episode=info['episode'] or 0, year=info['year']) or []
        if not eng:
            return None
        try:
            match.rank_candidates(eng, release, is_episode=(info['media_type'] == 'episode'),
                                  season=info['season'] or 0, episode=info['episode'] or 0)
        except Exception:
            pass
        english_b = sources.download(eng[0]['download_link'])   # matches user's release
        if not english_b:
            return None
        res = resync.retime(hebrew_a, english_a, english_b)
        if res and res.get('ok') and res.get('srt'):
            xbmc.log('[gearsai-ai] pool re-timed onto release (text, conf {0:.2f})'.format(
                res.get('confidence', 0)), xbmc.LOGINFO)
            return res['srt']
        # Fallback: when text alignment isn't confident, try a global TIMESTAMP
        # shift of the Hebrew's own cues onto english_b's timing (fail-open).
        try:
            from resources.aisubs import sync_align
            al = sync_align.align(hebrew_a, english_b)
            if al and al.get('ok') and al.get('srt'):
                xbmc.log('[gearsai-ai] pool re-timed onto release (offset {0:+.2f}s, conf {1:.2f})'.format(
                    al.get('offset', 0.0), al.get('confidence', 0.0)), xbmc.LOGINFO)
                return al['srt']
        except Exception as e:
            xbmc.log('[gearsai-ai] timestamp-align fallback failed: {0}'.format(e), xbmc.LOGWARNING)
        # Last resort (opt-in): use the playing file's OWN embedded subtitle track
        # as the timing oracle -- perfect for the exact release, and works even when
        # no external English matched. mkv_probe reads a few cue timestamps via HTTP
        # range (in-memory, byte-capped, no download); align_to_anchors fits them.
        try:
            mkv = _mkv_oracle_retime(hebrew_a)
            if mkv:
                return mkv
        except Exception as e:
            xbmc.log('[gearsai-ai] mkv oracle fallback failed: {0}'.format(e), xbmc.LOGWARNING)
    except Exception as e:
        xbmc.log('[gearsai-ai] pool re-time failed: {0}'.format(e), xbmc.LOGWARNING)
    return None


def _mkv_oracle_retime(hebrew_srt):
    """Opt-in: sync `hebrew_srt` to the currently-playing file's embedded subtitle
    timings. Returns re-timed SRT or None (fail-open)."""
    try:
        if xbmcaddon.Addon().getSetting('mkv_sync_oracle') != 'true':
            return None
        url = xbmc.Player().getPlayingFile()
    except Exception:
        return None
    if not url or not (url.startswith('http') and ('.mkv' in url.lower() or 'tb-cdn' in url.lower() or '/dld/' in url.lower())):
        # Only remote MKV-ish streams; local files Kodi already exposes natively.
        if not (url and url.startswith('http')):
            return None
    from resources.aisubs import mkv_probe, sync_align
    anchors = mkv_probe.probe_anchor_times(url)
    if not anchors:
        xbmc.log('[gearsai-ai] mkv oracle: no usable anchors', xbmc.LOGINFO)
        return None
    al = sync_align.align_to_anchors(hebrew_srt, anchors)
    if al and al.get('ok') and al.get('srt'):
        xbmc.log('[gearsai-ai] mkv oracle re-timed (scale {0:.4f} offset {1:+.2f}s, {2} anchors, conf {3:.2f})'.format(
            al.get('scale', 1.0), al.get('offset', 0.0), len(anchors), al.get('confidence', 0.0)), xbmc.LOGINFO)
        return al['srt']
    return None


def _pool_share(hebrew, english, info, release, model):
    """Push a freshly translated Hebrew SRT back to the pool so the next user
    gets it free. Best-effort; the Worker re-validates + de-dupes server-side."""
    try:
        from resources.aisubs import pool, srt
        entry_count = len(srt.parse(hebrew))
        pool.contribute(hebrew, entry_count, release=release, model=model,
                        imdb=info['imdb'], title=info['tvshow'] or info['title'],
                        year=info['year'], season=info['season'], episode=info['episode'],
                        eng=english)
    except Exception as e:
        xbmc.log('[gearsai-ai] pool contribute failed: {0}'.format(e), xbmc.LOGWARNING)


def translate_now():
    """Search an English sub, AI-translate it to Hebrew, return the .srt path
    (or None). Checks the community pool first for an instant free translation."""
    try:
        from resources.aisubs import sources, translate, match, kodi_utils, gemini
    except Exception as e:
        xbmc.log('[gearsai-ai] import failed: {0}'.format(e), xbmc.LOGERROR)
        return None

    info = _info()
    try:
        release = match.player_release() or ''
    except Exception:
        release = ''

    # Community pool first -- an existing translation is instant, free and works
    # even without a Gemini key.
    pooled = _pool_hebrew(info, release)
    if pooled:
        return _write_srt(pooled)

    if not gemini.have_keys():
        xbmcgui.Dialog().notification('MasterKodi AI', 'הגדר מפתח Gemini בהגדרות', time=4000)
        return None

    try:
        eng = sources.search_english(
            imdb_id=info['imdb'], title=info['tvshow'] or info['title'],
            media_type=info['media_type'], season=info['season'] or 0,
            episode=info['episode'] or 0, year=info['year']) or []
    except Exception as e:
        xbmc.log('[gearsai-ai] english search failed: {0}'.format(e), xbmc.LOGERROR)
        eng = []
    if not eng:
        xbmcgui.Dialog().notification('MasterKodi AI', 'לא נמצאה כתובית אנגלית לתרגום', time=4000)
        return None

    try:
        match.rank_candidates(eng, release, is_episode=(info['media_type'] == 'episode'),
                              season=info['season'] or 0, episode=info['episode'] or 0)
    except Exception:
        pass
    best = eng[0]

    try:
        english_txt = sources.download(best['download_link'])
    except Exception as e:
        xbmc.log('[gearsai-ai] english download failed: {0}'.format(e), xbmc.LOGERROR)
        english_txt = None
    if not english_txt:
        xbmcgui.Dialog().notification('MasterKodi AI', 'הורדת הכתובית האנגלית נכשלה', time=4000)
        return None

    prog = xbmcgui.DialogProgressBG()
    try:
        prog.create('MasterKodi AI', 'מתרגם לעברית...')
    except Exception:
        prog = None

    def _pct(pct, done, total, extra=None):
        try:
            if prog:
                active = (extra or {}).get('model') or ''
                prog.update(int(pct), 'MasterKodi AI',
                            '{0} · {1}/{2} שורות'.format(gemini.label(active), done, total))
        except Exception:
            pass

    stats = {}
    try:
        hebrew = translate.translate_srt(
            english_srt=english_txt, source_lang='en',
            title=info['title'], year=info['year'], cast=_cast(info),
            is_episode=(info['media_type'] == 'episode'),
            tvshow=info['tvshow'], season=info['season'], episode=info['episode'],
            api_key=kodi_utils.get_setting('api_key', ''),
            model=kodi_utils.get_setting('model', gemini.DEFAULT_MODEL),
            progress_cb=_pct, stats_out=stats)
    except gemini.RateLimited:
        _close(prog)
        xbmcgui.Dialog().notification('MasterKodi AI', 'יותר מדי בקשות - נסה שוב בעוד דקה', time=5000)
        return None
    except gemini.QuotaExceeded:
        _close(prog)
        xbmcgui.Dialog().notification('MasterKodi AI', 'מכסת Gemini היומית נגמרה', time=5000)
        return None
    except Exception as e:
        _close(prog)
        xbmc.log('[gearsai-ai] translate failed: {0}'.format(e), xbmc.LOGERROR)
        xbmcgui.Dialog().notification('MasterKodi AI', 'התרגום נכשל', time=4000)
        return None

    _close(prog)
    if not hebrew or not hebrew.strip():
        return None
    # Share it back so the next user gets it free. Record the ENGLISH SUB's
    # release (the timing lineage), not the played file's name.
    _mdl = stats.get('model') or kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)
    _pool_share(hebrew, english_txt, info, (best.get('name') or '').strip() or release, _mdl)
    _notify('תורגם עם {0}'.format(gemini.label(_mdl)) +
            (' · שותף במאגר' if pool_should_share() else ''))
    return _write_srt(hebrew)


def translate_english_text(english_text):
    """Translate a raw English SRT *string* to a Hebrew SRT string with Gemini,
    pulling title/season/episode from the player. Returns the Hebrew SRT text,
    or None on ANY failure (so DarkSubs' auto_translate falls back to Google).

    This is the hook that lets DarkSubs' built-in auto_translate use our AI
    engine instead of the Google/Bing/Yandex web scrapers -- it only ever runs
    when no Hebrew subtitle was found, on the English sub DarkSubs already
    downloaded, so it never conflicts with the Hebrew sources."""
    try:
        from resources.aisubs import translate, kodi_utils, gemini, match
    except Exception as e:
        xbmc.log('[gearsai-ai] import failed: {0}'.format(e), xbmc.LOGERROR)
        return None
    if not english_text or not english_text.strip():
        return None
    _set_origin('')   # reset -- set again by whichever path produces the Hebrew

    info = _info()
    try:
        release = match.player_release() or ''
    except Exception:
        release = ''

    # Community pool first -- a ready translation is instant, free, no Gemini
    # quota, and works even for keyless users.
    pooled = _pool_hebrew(info, release)
    if pooled:
        return pooled

    # have_keys() is True when the user has a key OR the community proxy is up,
    # so keyless users still translate. If neither -> let caller use Google.
    if not gemini.have_keys():
        return None

    # Show which English sub was chosen + its match%, then per-line progress --
    # all in DarkSubs' OWN native bar (general.show_msg / progress_msg).
    _name, _pctmatch, _site = _pick_info()
    _model = kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)

    # Ask the user before an AUTOMATIC AI translation starts (a manual pick in
    # the subtitle window is already explicit consent, and pool hits above are
    # instant + free so they never ask). Declining returns the DECLINED
    # sentinel -- the caller keeps the English sub as-is, no Google fallback.
    if kodi_utils.get_bool('ai_confirm', True) and not _is_manual():
        try:
            _q = 'לא נמצאו כתוביות בעברית.'
            if _name:
                _q += '\nנמצאה כתובית באנגלית ({0}% התאמה).'.format(_pctmatch or '?')
            _q += '\nלתרגם לעברית עם AI?'
            ok = xbmcgui.Dialog().yesno('MasterKodi AI', _q,
                                        yeslabel='תרגם', nolabel='לא',
                                        autoclose=20000)
        except Exception:
            ok = True
        if not ok:
            xbmc.log('[gearsai-ai] user declined auto AI translation', xbmc.LOGINFO)
            return 'DECLINED'

    # Heartbeat: chunks can generate silently for minutes; without updates the
    # progress dialog idle-times-out and vanishes mid-translation. A ticking
    # elapsed clock keeps it alive (and shows the user it's working).
    import time as _time
    import threading as _threading
    _hb = {'run': True, 'pct': 0, 'done': 0, 'total': 0,
           'model': _model, 'start': _time.time()}

    def _render():
        el = int(_time.time() - _hb['start'])
        _step('מתרגם · {0} · {1}/{2} שורות · {3}:{4:02d}'.format(
            gemini.label(_hb['model']), _hb['done'], _hb['total'],
            el // 60, el % 60), _hb['pct'])

    def _pct(pct, done, total, extra=None):
        # Show the LIVE active model's quality tier (★ = best). If key/quota
        # rotation had to drop to a weaker model, the label changes so the user
        # sees it immediately.
        _hb.update(pct=int(pct), done=done, total=total,
                   model=(extra or {}).get('model') or _model)
        _render()

    def _hb_loop():
        while _hb['run']:
            _time.sleep(8)
            if _hb['run']:
                _render()
    _threading.Thread(target=_hb_loop, daemon=True).start()

    # Identify cast (TMDb) -> better Hebrew gender, then the character/gender
    # analysis pass -- surfaced as their own steps.
    _step('מזהה שחקנים (TMDb)…', 0)
    cast = _cast(info)
    _step('מנתח דמויות ומגדר…', 0)

    # Announce the chosen English source + its match% right before translating,
    # so it stays on screen until the first chunk returns.
    if _name:
        _hdr = 'נבחר: {0}'.format(_name)
        if _pctmatch:
            _hdr += ' · {0}%'.format(_pctmatch)
        if _site:
            _hdr += ' · {0}'.format(_site)
        _step(_hdr, 0)

    stats = {}
    try:
        hebrew = translate.translate_srt(
            english_srt=english_text, source_lang='en',
            title=info['title'], year=info['year'], cast=cast,
            is_episode=(info['media_type'] == 'episode'),
            tvshow=info['tvshow'], season=info['season'], episode=info['episode'],
            api_key=kodi_utils.get_setting('api_key', ''),
            model=kodi_utils.get_setting('model', gemini.DEFAULT_MODEL),
            progress_cb=_pct, stats_out=stats)
    except Exception as e:
        xbmc.log('[gearsai-ai] auto_translate failed: {0}'.format(e), xbmc.LOGERROR)
        return None
    finally:
        _hb['run'] = False
    if not hebrew or not hebrew.strip():
        return None
    # Share it back so the next user gets this episode free. The release we
    # record is the ENGLISH SUB's release name (the Hebrew inherits ITS timing),
    # not the video file we happened to play -- that's what the next user's
    # sync-match must compare against.
    _mdl = stats.get('model') or kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)
    _set_origin('Gemini AI' + (' ★' if gemini.is_best(_mdl) else ''))
    sub_release = ''
    try:
        from resources.modules import general as _g
        sub_release = (getattr(_g, 'ai_pick_name', '') or '').strip()
    except Exception:
        pass
    if pool_should_share():
        _step('משתף את התרגום במאגר…', 100)
        _pool_share(hebrew, english_text, info, sub_release or release, _mdl)
        _notify('תורגם עם {0} · שותף במאגר'.format(gemini.label(_mdl)))
    else:
        _pool_share(hebrew, english_text, info, sub_release or release, _mdl)
        _notify('תורגם עם {0}'.format(gemini.label(_mdl)))
    return hebrew


def pool_should_share():
    try:
        from resources.aisubs import pool
        return pool.enabled() and pool.contribute_enabled()
    except Exception:
        return False


def _write_srt(text):
    """Write Hebrew SRT text to the shared temp path and return it (or None)."""
    try:
        from resources.aisubs import kodi_utils
        path = os.path.join(kodi_utils.temp_dir(), 'gearsai_ai_he.srt')
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        return path
    except Exception as e:
        xbmc.log('[gearsai-ai] write failed: {0}'.format(e), xbmc.LOGERROR)
        return None


def _close(prog):
    try:
        if prog:
            prog.close()
    except Exception:
        pass
