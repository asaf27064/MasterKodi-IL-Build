# -*- coding: utf-8 -*-
# On-demand AI Hebrew translation for the "AI translate" item in the subtitle
# dialog. Reuses the gearsai AI engine (now living in resources/aisubs/).
# Fully fail-open: returns None + a notification on any problem.

import os
import time
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
    # window property = visible across interpreters (the wand window runs in
    # the plugin invoker; auto-translations run in the SERVICE invoker)
    try:
        import xbmcgui as _xg
        _xg.Window(10000).setProperty('gearsai.ai_status', 'MasterKodi AI · ' + msg)
    except Exception:
        pass


def _clear_ai_status():
    """Reset the shared AI status line (window prop + DarkSubs show_msg).
    Every EARLY return of an AI flow must call this: the pool check runs
    before the consent dialog and writes 'בודק מאגר קהילתי…' to the status
    prop -- returning without clearing left the wand showing that text (and
    its STOP button) forever. Seen live 2026-07-21 on a declined auto
    translation."""
    try:
        xbmcgui.Window(10000).clearProperty('gearsai.ai_status')
    except Exception:
        pass
    try:
        from resources.modules import general
        general.show_msg = 'END'
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


def _mkv_oracle_retime(hebrew_srt, force=False):
    """Sync `hebrew_srt` to the currently-playing file's embedded subtitle timings.
    Returns re-timed SRT or None (fail-open). `force` bypasses the opt-in setting
    (used by the manual 'sync this subtitle' action, where the user consented)."""
    try:
        if not force and xbmcaddon.Addon().getSetting('mkv_sync_oracle') != 'true':
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
        _clear_ai_status()
        return _write_srt(pooled)

    if not gemini.have_keys():
        xbmcgui.Dialog().notification('MasterKodi AI', 'הגדר מפתח Gemini בהגדרות', time=4000)
        _clear_ai_status()
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
        _clear_ai_status()
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
        _clear_ai_status()
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
    # reset any stale STOP request from a previous run before we begin
    try:
        from resources.modules import general as _gen
        _gen.ai_cancel = False
    except Exception:
        _gen = None
    try:
        import xbmcgui as _xg
        _xg.Window(10000).clearProperty('gearsai.ai_cancel')
    except Exception:
        pass

    def _user_cancelled():
        if _gen and getattr(_gen, 'ai_cancel', False):
            return True
        try:
            import xbmcgui as _xg
            return _xg.Window(10000).getProperty('gearsai.ai_cancel') == '1'
        except Exception:
            return False

    try:
        hebrew = translate.translate_srt(
            english_srt=english_txt, source_lang='en',
            title=info['title'], year=info['year'], cast=_cast(info),
            is_episode=(info['media_type'] == 'episode'),
            tvshow=info['tvshow'], season=info['season'], episode=info['episode'],
            api_key=kodi_utils.get_setting('api_key', ''),
            model=kodi_utils.get_setting('model', gemini.DEFAULT_MODEL),
            progress_cb=_pct, stats_out=stats,
            abort_cb=_user_cancelled)
    except translate.TranslationAborted:
        _close(prog)
        if _gen:
            _gen.ai_cancel = False
        xbmcgui.Dialog().notification('MasterKodi AI', 'התרגום בוטל', time=4000)
        return None
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
    hebrew = _heb_punct(hebrew) if hebrew else hebrew
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
        _clear_ai_status()
        return pooled

    # have_keys() is True when the user has a key OR the community proxy is up,
    # so keyless users still translate. If neither -> let caller use Google.
    if not gemini.have_keys():
        _clear_ai_status()
        return None

    # Show which English sub was chosen + its match%, then per-line progress --
    # all in DarkSubs' OWN native bar (general.show_msg / progress_msg).
    _name, _pctmatch, _site = _pick_info()
    _model = kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)

    # Ask the user before an AUTOMATIC AI translation starts (a manual pick in
    # the subtitle window is already explicit consent, and pool hits above are
    # instant + free so they never ask). Declining returns the DECLINED
    # sentinel -- the caller keeps the English sub as-is, no Google fallback.
    # An embedded foreign track means the result can be synced to THIS file --
    # say so in the consent dialog (and the resolver below delivers on it).
    _emb_here = (kodi_utils.get_bool('embedded_ai_translate', True)
                 and _emb_streams_present())
    if kodi_utils.get_bool('ai_confirm', True) and not _is_manual():
        try:
            _q = 'לא נמצאו כתוביות בעברית.'
            if _name:
                _q += '\nנמצאה כתובית באנגלית ({0}% התאמה).'.format(_pctmatch or '?')
            if _emb_here:
                _q += '\nבקובץ קיימת כתובית מובנית - התרגום יסונכרן לפי התזמון שלה.'
            _q += '\nלתרגם לעברית עם AI?'
            ok = xbmcgui.Dialog().yesno('MasterKodi AI', _q,
                                        yeslabel='תרגם', nolabel='לא',
                                        autoclose=20000)
        except Exception:
            ok = True
        if not ok:
            xbmc.log('[gearsai-ai] user declined auto AI translation', xbmc.LOGINFO)
            _clear_ai_status()
            return 'DECLINED'

    # MASTERKODI: embedded-sync upgrade of the translation SOURCE. Align the
    # ranked external pick onto the embedded cue skeleton (cheap, pure win --
    # same text, this file's exact timing); when the pick's match is weak
    # (<85%), the resolver may also try better-aligning externals or the full
    # embedded-text extract. Fail-open: on any miss we translate the original
    # pick exactly as before.
    _emb_used = False
    _emb_desc = ''
    _src_lang = 'en'
    if _emb_here:
        if _emb_single_flight_take():
            try:
                _e_abort = _emb_abort_cb()
                _e_src, _e_lang, _e_desc = _emb_resolve_source(
                    info, release, _e_abort, _step, pre_text=english_text,
                    allow_extract=bool((_pctmatch or 0) < 85))
                if _e_src:
                    english_text = _e_src
                    _src_lang = _e_lang or 'en'
                    _emb_used = True
                    _emb_desc = _e_desc or ''
                    # Transparency: which path + which subtitle became the source.
                    if _emb_desc:
                        _step(_emb_desc, 0)
                        _notify(_emb_desc, 5000)
                    xbmc.log('[gearsai-emb] auto flow: source synced via embedded '
                             '(lang {0}): {1}'.format(_src_lang, _emb_desc),
                             xbmc.LOGINFO)
            except Exception as _e:
                xbmc.log('[gearsai-emb] auto upgrade failed: {0}'.format(_e),
                         xbmc.LOGWARNING)
            finally:
                _emb_single_flight_release()

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
    # reset any stale STOP request from a previous run before we begin
    try:
        from resources.modules import general as _gen
        _gen.ai_cancel = False
    except Exception:
        _gen = None
    try:
        import xbmcgui as _xg
        _xg.Window(10000).clearProperty('gearsai.ai_cancel')
    except Exception:
        pass

    def _user_cancelled():
        if _gen and getattr(_gen, 'ai_cancel', False):
            return True
        try:
            import xbmcgui as _xg
            return _xg.Window(10000).getProperty('gearsai.ai_cancel') == '1'
        except Exception:
            return False

    try:
        hebrew = translate.translate_srt(
            english_srt=english_text, source_lang=_src_lang,
            title=info['title'], year=info['year'], cast=cast,
            is_episode=(info['media_type'] == 'episode'),
            tvshow=info['tvshow'], season=info['season'], episode=info['episode'],
            api_key=kodi_utils.get_setting('api_key', ''),
            model=kodi_utils.get_setting('model', gemini.DEFAULT_MODEL),
            progress_cb=_pct, stats_out=stats,
            abort_cb=_user_cancelled)
    except translate.TranslationAborted:
        # user pressed STOP in the subtitle window -- clean, deliberate end
        if _gen:
            _gen.ai_cancel = False
        _step('התרגום בוטל', 0)
        xbmc.log('[gearsai-ai] translation cancelled by user', xbmc.LOGINFO)
        return None
    except Exception as e:
        xbmc.log('[gearsai-ai] auto_translate failed: {0}'.format(e), xbmc.LOGERROR)
        return None
    finally:
        _hb['run'] = False
        try:
            import xbmcgui as _xg
            _xg.Window(10000).clearProperty('gearsai.ai_status')
            _xg.Window(10000).clearProperty('gearsai.ai_cancel')
        except Exception:
            pass
    hebrew = _heb_punct(hebrew) if hebrew else hebrew
    if not hebrew or not hebrew.strip():
        return None
    # Share it back so the next user gets this episode free. The release we
    # record is the ENGLISH SUB's release name (the Hebrew inherits ITS timing),
    # not the video file we happened to play -- that's what the next user's
    # sync-match must compare against.
    _mdl = stats.get('model') or kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)
    _emb_tag = ''
    if _emb_used:
        _emb_tag = ('מובנית · חולץ מהקובץ · ' if _emb_desc.startswith('חולץ')
                    else 'מובנית · סונכרן · ')
    _set_origin(_emb_tag + 'Gemini AI' + (' ★' if gemini.is_best(_mdl) else ''))
    sub_release = ''
    try:
        from resources.modules import general as _g
        sub_release = (getattr(_g, 'ai_pick_name', '') or '').strip()
    except Exception:
        pass
    # Embedded-synced source: its timing lineage is the PLAYING file, not the
    # external pick's release -- record the playing release for the pool.
    if _emb_used:
        sub_release = release or sub_release
    if pool_should_share():
        _step('משתף את התרגום במאגר…', 100)
        _pool_share(hebrew, english_text, info, sub_release or release, _mdl)
        _notify('תורגם עם {0} · שותף במאגר'.format(gemini.label(_mdl)))
    else:
        _pool_share(hebrew, english_text, info, sub_release or release, _mdl)
        _notify('תורגם עם {0}'.format(gemini.label(_mdl)))
    # reset the status: without END the last step text ('משתף את התרגום
    # במאגר…') stayed on the window's status line forever, reading as stuck
    try:
        from resources.modules import general as _g2
        _g2.show_msg = 'END'
    except Exception:
        pass
    return hebrew


def _heb_punct(srt_text):
    """Trailing SDH interruption dashes -> ellipsis at the logical FRONT,
    matching rtl.fix_lines' visual-reorder convention (Kodi renders lines
    LTR-base; front punctuation displays LEFT = the Hebrew sentence end).
    Hebrew lines only; mechanical; translation content untouched."""
    import re as _r
    def _line(ln):
        try:
            if _r.search(r'-{2,}\s*$', ln) and any(0x590 <= ord(c) <= 0x5FF for c in ln):
                return chr(0x2026) + _r.sub(r'-{2,}\s*$', '', ln).rstrip()
            return ln
        except Exception:
            return ln
    try:
        return chr(10).join(_line(l) for l in srt_text.split(chr(10)))
    except Exception:
        return srt_text

def pool_should_share():
    try:
        from resources.aisubs import pool
        return pool.enabled() and pool.contribute_enabled()
    except Exception:
        return False


def sync_current_sub():
    """Manual action: re-time the currently-shown Hebrew subtitle onto the best
    available timing reference -- the playing file's embedded English track (mkv
    oracle) first, then a release-matched external English sub. Keeps the Hebrew
    TEXT; only shifts timestamps. Returns a path to the re-timed .srt or None."""
    try:
        cur = xbmcgui.Window(10000).getProperty('gearsai.current_heb_sub')
        if not cur or not os.path.exists(cur):
            cur = _newest_cached_srt()
        if not cur or not cur.lower().endswith('.srt'):
            _notify('לא נמצאה כתובית עברית פעילה לסנכרון')
            return None
        hebrew = _read_text(cur)
        if not hebrew:
            _notify('לא הצלחתי לקרוא את הכתובית')
            return None
        _notify('מסנכרן את הכתובית לפי הקובץ המתנגן...', 3500)
        retimed = _sync_to_best_oracle(hebrew)
        if not retimed:
            _notify('הסנכרון לא הצליח - הכתובית נשארה כפי שהיא')
            return None
        # Write to MySubFolder with a unique name, exactly like a normal download
        # (_download_row) -- that's the path Kodi reliably loads + selects. A fresh
        # name each time defeats Kodi's per-path subtitle cache.
        out = _write_synced_srt(retimed)
        if out:
            _notify('הכתובית סונכרנה!')
            # remember that the CURRENT sub is the synced version, so the
            # window can show [ נוכחית · סונכרן ] instead of a silent state
            try:
                xbmcgui.Window(10000).setProperty('gearsai.current_synced', '1')
            except Exception:
                pass
        return out
    except Exception as e:
        xbmc.log('[gearsai-ai] sync_current_sub failed: {0}'.format(e), xbmc.LOGWARNING)
        return None


def _write_synced_srt(text):
    try:
        from resources.modules import general
        folder = getattr(general, 'MySubFolder', None) or None
        if not folder or not os.path.isdir(folder):
            from resources.aisubs import kodi_utils
            folder = kodi_utils.temp_dir()
        path = os.path.join(folder, 'gearsai_synced_%d.he.srt' % int(time.time()))
        with open(path, 'w', encoding='utf-8', newline='') as f:
            f.write(text)
        return path
    except Exception as e:
        xbmc.log('[gearsai-ai] write synced failed: {0}'.format(e), xbmc.LOGWARNING)
        return None


def _sync_to_best_oracle(hebrew):
    """Try the embedded-MKV oracle (exact file), then a release-matched external
    English sub. Returns re-timed Hebrew SRT or None."""
    # 1. Embedded English from the playing file -- ground truth for this release.
    try:
        r = _mkv_oracle_retime(hebrew, force=True)
        if r:
            xbmc.log('[gearsai-ai] manual sync: used embedded MKV oracle', xbmc.LOGINFO)
            return r
    except Exception as e:
        xbmc.log('[gearsai-ai] manual sync mkv path failed: {0}'.format(e), xbmc.LOGWARNING)
    # 2. External English matching the user's release -> timestamp-align onto it.
    try:
        from resources.aisubs import sources, match, sync_align
        info = _info()
        eng = sources.search_english(
            imdb_id=info['imdb'], title=info['tvshow'] or info['title'],
            media_type=info['media_type'], season=info['season'] or 0,
            episode=info['episode'] or 0, year=info['year']) or []
        if not eng:
            return None
        release = info.get('release') or info.get('filename') or ''
        try:
            match.rank_candidates(eng, release, is_episode=(info['media_type'] == 'episode'),
                                  season=info['season'] or 0, episode=info['episode'] or 0)
        except Exception:
            pass
        english_b = sources.download(eng[0]['download_link'])
        if not english_b:
            return None
        al = sync_align.align(hebrew, english_b)
        if al and al.get('ok') and al.get('srt'):
            xbmc.log('[gearsai-ai] manual sync: aligned to external English (offset {0:+.2f}s)'.format(
                al.get('offset', 0.0)), xbmc.LOGINFO)
            return al['srt']
    except Exception as e:
        xbmc.log('[gearsai-ai] manual sync english path failed: {0}'.format(e), xbmc.LOGWARNING)
    return None


def _newest_cached_srt():
    """Most-recently-written Hebrew .srt in the cache/download folders (fallback
    when the active-sub property isn't set, e.g. first run after update)."""
    try:
        from resources.modules import general
        cands = []
        for d in (getattr(general, 'CachedSubFolder', ''), getattr(general, 'MySubFolder', '')):
            if d and os.path.isdir(d):
                for fn in os.listdir(d):
                    if fn.lower().endswith('.srt'):
                        p = os.path.join(d, fn)
                        try: cands.append((os.path.getmtime(p), p))
                        except Exception: pass
        if not cands:
            return None
        cands.sort(reverse=True)
        return cands[0][1]
    except Exception:
        return None


def _read_text(path):
    try:
        with open(path, 'rb') as f:
            data = f.read()
        for enc in ('utf-8', 'utf-8-sig', 'cp1255', 'windows-1255'):
            try:
                return data.decode(enc)
            except Exception:
                continue
        return data.decode('utf-8', 'replace')
    except Exception:
        return None


def _write_srt(text, name='gearsai_ai_he.srt'):
    """Write Hebrew SRT text to a temp path and return it (or None)."""
    try:
        from resources.aisubs import kodi_utils
        path = os.path.join(kodi_utils.temp_dir(), name)
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


# ---------------------------------------------------------------------------
# MASTERKODI: "תרגום מובנה ← עברית (AI)" -- translate the playing file's OWN
# embedded foreign subtitle track to Hebrew. The embedded track's cue
# timestamps ARE the video's timeline, so the Hebrew comes out perfectly
# synced with no re-sync guessing. Two strategies, cheapest first:
#
#   FAST PATH  read only the embedded track's cue TIMES (a handful of HTTP
#              range requests), then re-time an external English sub onto that
#              skeleton (sync_align) and translate the re-timed English.
#   FALLBACK   extract the embedded track's full TEXT (embedded_extract) and
#              translate it directly (source_lang = the track's language).
#
# For LOCAL files the order flips: a local text extract is free and exact, so
# it runs first. Every step is fail-open; on total failure the user gets a
# clear notification and the row simply does nothing harmful.
# ---------------------------------------------------------------------------

# ISO 639-2 (what Kodi's getAvailableSubtitleStreams returns) -> 639-1 (what
# translate/prompt expect). Only languages prompt.LANG_NAME knows.
EMB_LANG3TO2 = {
    'eng': 'en', 'spa': 'es', 'fre': 'fr', 'fra': 'fr', 'ger': 'de',
    'deu': 'de', 'por': 'pt', 'ita': 'it', 'rus': 'ru', 'ara': 'ar',
    'dut': 'nl', 'nld': 'nl', 'pol': 'pl', 'tur': 'tr', 'jpn': 'ja',
    'kor': 'ko', 'chi': 'zh', 'zho': 'zh',
}

# Source-language preference for the full-text extract: languages with strong
# grammatical gender first (they give the model the best Hebrew את/אתה forms),
# then English, then weak-gender languages.
_EMB_SRC_RANK = ('es', 'pt', 'it', 'fr', 'ar', 'ru', 'pl', 'de', 'nl', 'tr',
                 'en', 'ja', 'ko', 'zh')

# Hebrew display names for the source-language transparency line.
_EMB_LANG_HE = {
    'en': 'אנגלית', 'es': 'ספרדית', 'fr': 'צרפתית', 'de': 'גרמנית',
    'pt': 'פורטוגזית', 'it': 'איטלקית', 'ru': 'רוסית', 'ar': 'ערבית',
    'nl': 'הולנדית', 'pl': 'פולנית', 'tr': 'טורקית', 'ja': 'יפנית',
    'ko': 'קוריאנית', 'zh': 'סינית',
}

_EMB_ACTIVE_PROP = 'gearsai.embedded_ai_active'
_EMB_DEADLINE_S = 600.0     # hard ceiling on the full-text extract
_EMB_ALIGN_BUDGET_S = 75.0  # soft ceiling on the whole align-first attempt


def _emb_rank(lang2):
    try:
        return _EMB_SRC_RANK.index(lang2)
    except ValueError:
        return len(_EMB_SRC_RANK)


def _emb_log(msg):
    xbmc.log('[gearsai-emb] {0}'.format(msg), xbmc.LOGINFO)


def _emb_single_flight_take():
    """One embedded run at a time, across processes (the row can be clicked
    from the wand's invoker AND the service invoker). Two concurrent runs
    double the range-request load on the debrid token the PLAYER is streaming
    with -- that exact shape has been seen to 429 the player and close the
    movie. Monotonic clock: immune to NTP wall-clock jumps on Android boxes;
    a Kodi restart clears the Window prop, so there is no cross-boot concern.
    Returns True when we own the flag."""
    try:
        import time as _tm
        win = xbmcgui.Window(10000)
        raw = win.getProperty(_EMB_ACTIVE_PROP)
        now = _tm.monotonic()
        if raw:
            try:
                age = now - float(raw)
            except (ValueError, TypeError):
                age = _EMB_DEADLINE_S + 999.0
            if 0 <= age < (_EMB_DEADLINE_S + 120):
                _emb_log('another embedded translation is already running -- refusing')
                _notify('תרגום מובנה כבר רץ - המתן לסיומו')
                return False
        win.setProperty(_EMB_ACTIVE_PROP, str(now))
        return True
    except Exception:
        return True    # never let the guard itself kill the feature


def _emb_single_flight_release():
    try:
        xbmcgui.Window(10000).clearProperty(_EMB_ACTIVE_PROP)
    except Exception:
        pass


def _emb_abort_cb():
    """Abort closure for the extract/cue reads. They share the debrid token
    with the player's own video stream, so: stop when playback ends, and if
    the user PAUSED to let this run and then RESUMES, hand the token back
    instantly (resume-into-a-hot-token is the field crash shape). ALSO honors
    the wand's STOP button (gearsai.ai_cancel) -- the button only aborted the
    TRANSLATION stage; a long extraction ignored it (seen live 2026-07-21)."""
    state = {'saw_pause': False}
    # A stale cancel from a previous run must not abort this one instantly.
    try:
        xbmcgui.Window(10000).clearProperty('gearsai.ai_cancel')
    except Exception:
        pass
    try:
        from resources.modules import general as _g
        _g.ai_cancel = False
    except Exception:
        pass

    def _abort():
        try:
            if xbmcgui.Window(10000).getProperty('gearsai.ai_cancel') == '1':
                return True
        except Exception:
            pass
        try:
            from resources.modules import general as _g2
            if getattr(_g2, 'ai_cancel', False):
                return True
        except Exception:
            pass
        try:
            p = xbmc.Player()
            if not p.isPlayingVideo():
                return True
            if xbmc.getCondVisibility('Player.Paused'):
                state['saw_pause'] = True
                return False
            return state['saw_pause']
        except Exception:
            return False
    return _abort


def _emb_tracks(url):
    """Foreign embedded TEXT tracks of the playing file, best source first:
    [{'num','lang2'}, ...]. [] on any problem (non-MKV, unreadable, none)."""
    try:
        from resources.aisubs import embedded_extract
        out = []
        for t in embedded_extract.probe_tracks(url, log=_emb_log):
            if not t.get('is_text'):
                continue
            l3 = (t.get('lang') or '')[:3]
            l2 = EMB_LANG3TO2.get(l3, (t.get('lang') or '')[:2]
                                  if (t.get('lang') or '')[:2] in EMB_LANG3TO2.values() else '')
            if not l2 or l2 == 'he':
                continue
            import re as _re
            name = t.get('name') or ''
            sdh = bool(_re.search(r'\bsdh\b|\bcc\b|hearing|impair', name, _re.I))
            out.append({'num': t['num'], 'lang2': l2, 'sdh': sdh,
                        'forced': bool(t.get('forced'))})
        # Rank: forced tracks last (signs/songs only). Then the AUDIO
        # (original) language first -- its track IS the dialogue; any other
        # language is already a translation, and translating a translation
        # loses fidelity (live 2026-07-21: an English-language show got its
        # RUSSIAN track extracted because Russian outranks English on gender
        # strength). Then English (best-understood by the model), then gender
        # strength as a mere tie-break; SDH wins within a language.
        audio2 = ''
        try:
            a = (xbmc.getInfoLabel('VideoPlayer.AudioLanguage') or '').strip().lower()
            audio2 = EMB_LANG3TO2.get(a[:3], a[:2] if a[:2] in EMB_LANG3TO2.values() else '')
        except Exception:
            pass
        out.sort(key=lambda t: (t['forced'],
                                0 if (audio2 and t['lang2'] == audio2) else 1,
                                0 if t['lang2'] == 'en' else 1,
                                _emb_rank(t['lang2']),
                                0 if t['sdh'] else 1))
        return out
    except Exception as e:
        _emb_log('track probe failed: {0}'.format(e))
        return []


def _emb_anchor_secs(url, lang2, abort_cb):
    """Timing skeleton of the embedded track, in SECONDS. Dense per-cue Cues
    index first (a handful of range requests), downsampled to <=80 anchors so
    align_to_anchors' offset scan stays fast on TV boxes; else the sparse
    cluster-sampling probe (mkv_probe). None when neither works."""
    try:
        from resources.aisubs import embedded_extract
        ms = embedded_extract.cue_reference_times(
            url, lang=lang2, allow_http=True, abort_cb=abort_cb, log=_emb_log)
        if ms and len(ms) >= 8:
            secs = [m / 1000.0 for m in ms]
            if len(secs) > 80:
                step = len(secs) / 80.0
                secs = [secs[int(i * step)] for i in range(80)]
            return secs
    except Exception as e:
        _emb_log('cue-times failed: {0}'.format(e))
    try:
        from resources.aisubs import mkv_probe
        l3s = tuple(k for k, v in EMB_LANG3TO2.items() if v == lang2)
        return mkv_probe.probe_anchor_times(url, prefer_langs=l3s + (lang2, 'eng', 'en'))
    except Exception as e:
        _emb_log('anchor probe failed: {0}'.format(e))
        return None


def _emb_aligned_english(info, release, anchors, abort_cb, status_cb,
                         pre_text=None):
    """Re-time an external English sub onto the embedded skeleton. `pre_text`
    (an already-downloaded English sub, e.g. the auto flow's ranked pick) gets
    the FIRST shot -- zero extra downloads when it fits. Then the top
    release-ranked candidates, SDH bubbled up within 10% match (speaker tags
    -> better Hebrew gender); name similarity is not timing similarity, so
    several get a shot -- under a soft time budget. Returns the re-timed SRT
    text or None."""
    try:
        from resources.aisubs import sources, match, sync_align

        def _fix_txt(al):
            # Human-readable timing verdict for the transparency line.
            off, sc = al.get('offset', 0.0), al.get('scale', 1.0)
            if abs(sc - 1.0) > 1e-4:
                return 'תוקנו הזזה וקצב ({0:+.1f} שנ׳)'.format(off)
            if abs(off) > 0.2:
                return 'תוקנה הזזה של {0:+.1f} שנ׳'.format(off)
            return 'הייתה מסונכרנת'

        def _try_align(txt, tag, desc_src):
            if not txt or txt.count('-->') < 8:
                return None
            al = sync_align.align_to_anchors(txt, anchors)
            if al and al.get('ok') and al.get('srt'):
                # A NON-identity scale is a strong claim (framerate conversion)
                # -- at borderline confidence it is more likely the fitter
                # CONTORTING wrong-content cues onto the skeleton than a real
                # PAL/NTSC fix (seen live 2026-07-21: an S01-pack EPISODE-1 sub
                # "aligned" onto episode 3 at scale 0.9591 conf 0.61 and got
                # translated). Real framerate fixes align sharply; demand it.
                if (abs(al.get('scale', 1.0) - 1.0) > 1e-4
                        and al.get('confidence', 0.0) < 0.75):
                    _emb_log('rejected %s: non-identity scale %.4f at low conf '
                             '%.2f' % (tag, al.get('scale', 1.0),
                                       al.get('confidence', 0.0)))
                    return None
                _emb_log('aligned %s (scale %.4f offset %+.2fs conf %.2f)'
                         % (tag, al.get('scale', 1.0),
                            al.get('offset', 0.0), al.get('confidence', 0.0)))
                return al['srt'], 'מסלול מהיר · {0} · {1}'.format(desc_src, _fix_txt(al))
            return None

        if pre_text:
            status_cb('מיישר את הכתובית שנבחרה לפי הזמנים המובנים...')
            r = _try_align(pre_text, '<pre-downloaded pick>', 'הכתובית שנבחרה')
            if r:
                return r

        eng = sources.search_english(
            imdb_id=info['imdb'], title=info['tvshow'] or info['title'],
            media_type=info['media_type'], season=info['season'] or 0,
            episode=info['episode'] or 0, year=info['year']) or []
        if not eng:
            return None
        try:
            match.rank_candidates(eng, release,
                                  is_episode=(info['media_type'] == 'episode'),
                                  season=info['season'] or 0,
                                  episode=info['episode'] or 0)
        except Exception:
            pass
        # EPISODES: drop season-pack candidates entirely. A pack name can't
        # prove which episode's file the download will yield -- live failure
        # 2026-07-21: an 'S01' pack delivered EPISODE 1's sub, it false-
        # positive-aligned onto episode 3 and got translated. Same detection
        # rule as match.rank_candidates. With packs gone, an episode whose
        # only externals are packs falls through to the full embedded-text
        # extract -- the one source whose identity is beyond doubt.
        try:
            import re as _re
            s_n, e_n = int(info['season'] or 0), int(info['episode'] or 0)
            if info['media_type'] == 'episode' and s_n and e_n:
                def _is_pack(c):
                    nm = _re.sub(r'[^a-z0-9]+', '', (c.get('name') or '').lower())
                    has_ep = any(p in nm for p in (
                        's%02de%02d' % (s_n, e_n), 's%de%d' % (s_n, e_n),
                        '%dx%02d' % (s_n, e_n)))
                    season_tagged = (('s%02d' % s_n in nm)
                                     or ('season%d' % s_n in nm))
                    return bool(c.get('full_season')) or (season_tagged and not has_ep)
                _dropped = [c.get('name') for c in eng if _is_pack(c)]
                if _dropped:
                    _emb_log('dropping %d season-pack candidate(s): %s'
                             % (len(_dropped), _dropped))
                eng = [c for c in eng if not _is_pack(c)]
        except Exception:
            pass
        if not eng:
            return None
        # SDH within 10% of the leader tries first (mirrors place_sub's rule).
        try:
            best = max((c.get('match', 0) for c in eng[:8]), default=0)
            eng.sort(key=lambda c: (0 if (c.get('hi') and
                                          c.get('match', 0) >= best - 10) else 1,))
        except Exception:
            pass
        t0 = time.time()
        for i, cand in enumerate(eng[:4]):
            if abort_cb() or (time.time() - t0) > _EMB_ALIGN_BUDGET_S:
                break
            status_cb('מיישר כתובית אנגלית לפי הזמנים המובנים... ({0}/4)'.format(i + 1))
            try:
                txt = sources.download(cand['download_link'])
            except Exception:
                txt = None
            _cn = (cand.get('name') or '').strip()
            _cn_short = (_cn[:30] + '…') if len(_cn) > 31 else _cn
            _src_desc = (_cn_short or 'כתובית חיצונית') + (' (SDH)' if cand.get('hi') else '')
            r = _try_align(txt, repr(_cn[:60]), _src_desc)
            if r:
                return r
        return None
    except Exception as e:
        _emb_log('align path failed: {0}'.format(e))
        return None


def _emb_extract_text(url, tracks, abort_cb, status_cb):
    """Full embedded-text extract (the heavy path). Returns
    (srt_text, lang2, desc) or (None, None, None). Tries the best-ranked
    foreign text track only -- one track is one full pass; trying more would
    multiply the token load."""
    if not tracks:
        return None, None, None
    tr = tracks[0]
    _lang_he = _EMB_LANG_HE.get(tr['lang2'], tr['lang2'])
    try:
        from resources.aisubs import embedded_extract

        def _prog(done, total):
            try:
                status_cb('מחלץ כתובית מובנית ({0})... {1}/{2}'.format(
                    _lang_he, done, total))
            except Exception:
                pass
        status_cb('מחלץ את הכתובית המובנית ({0})...'.format(_lang_he))
        txt = embedded_extract.extract_srt(
            url, track_num=tr['num'], allow_http=True,
            deadline_s=_EMB_DEADLINE_S, abort_cb=abort_cb,
            log=_emb_log, progress_cb=_prog)
        n_cues = txt.count('-->') if txt else 0
        # DENSITY guard: a sub-track Cues index sometimes holds only ~1 point
        # per cluster, so a "successful" HTTP extract can return a few dozen
        # cues for a full episode -- delivering that would show near-empty
        # subtitles. Demand a plausible dialogue density (>=4 cues/minute,
        # dialogue is typically ~15) before accepting.
        min_cues = 8
        try:
            total_min = xbmc.Player().getTotalTime() / 60.0
            if total_min > 2:
                min_cues = max(8, int(total_min * 4))
        except Exception:
            pass
        if n_cues >= min_cues:
            desc = 'חולץ מהקובץ · רצועה {0}{1}'.format(
                _lang_he, ' (SDH)' if tr.get('sdh') else '')
            return txt, tr['lang2'], desc
        if n_cues:
            _emb_log('extract too sparse: %d cue(s) < required %d -- rejecting'
                     % (n_cues, min_cues))
        return None, None, None
    except Exception as e:
        _emb_log('extract failed: {0}'.format(e))
        return None, None, None


def _emb_streams_present():
    """Cheap gate: the playing file reports a foreign EMBEDDED subtitle
    stream. Kodi's list mixes embedded tracks (bare language codes) with
    loaded external subs (full filenames) -- only 2-3 letter alpha codes
    count (same rule as the window row's gate)."""
    try:
        subs = xbmc.Player().getAvailableSubtitleStreams()
        return any(s and 2 <= len(s) <= 3 and s.isalpha()
                   and s.lower() not in ('heb', 'he', 'und') for s in subs)
    except Exception:
        return False


def _emb_resolve_source(info, release, abort_cb, status_cb, pre_text=None,
                        allow_extract=True, mode='auto'):
    """Resolve a translation SOURCE synced to the playing file via its
    embedded subtitle track. Returns (src_text, src_lang) or (None, None).

    Order: local file -> extract first (free + exact); stream -> fast path
    (embedded cue skeleton + re-timed external English, `pre_text` first),
    then full extract. `allow_extract=False` limits the resolver to the cheap
    alignment path (used by the auto flow when its external pick already
    matches well -- fixing its sync is a free win, but heavy extraction isn't
    justified)."""
    try:
        url = xbmc.Player().getPlayingFile()
    except Exception:
        url = ''
    if not url:
        return None, None, None
    is_http = url.startswith('http')
    tracks = _emb_tracks(url)
    _emb_log('resolve: http=%s mode=%s | foreign text tracks: %s'
             % (is_http, mode, tracks))
    do_align = True
    do_extract = allow_extract
    if mode == 'align':
        do_extract = False
    elif mode == 'extract':
        do_align = False
        do_extract = True              # explicit user choice overrides

    # LOCAL file: extracting is free and exact -- do it first.
    if not is_http and tracks and do_extract:
        t, l, d = _emb_extract_text(url, tracks, abort_cb, status_cb)
        if t:
            return t, l, d

    # FAST PATH: embedded cue skeleton + re-timed external English.
    if do_align:
        skel_lang = tracks[0]['lang2'] if tracks else 'en'
        status_cb('קורא זמנים מהכתובית המובנית...')
        anchors = _emb_anchor_secs(url, skel_lang, abort_cb)
        if anchors and len(anchors) >= 5:
            aligned = _emb_aligned_english(info, release, anchors, abort_cb,
                                           status_cb, pre_text=pre_text)
            if aligned:
                return aligned[0], 'en', aligned[1]

    # FALLBACK: full text extract (HTTP -- guarded + paced internally).
    if is_http and tracks and do_extract:
        t, l, d = _emb_extract_text(url, tracks, abort_cb, status_cb)
        if t:
            return t, l, d
    return None, None, None


def translate_embedded(download_data=None):
    """Produce a Hebrew SRT synced to the playing file via its embedded
    subtitle track. Returns the Hebrew SRT TEXT, 'DECLINED' when the user
    said no to a non-manual run, or None on failure (caller notifies)."""
    try:
        from resources.aisubs import translate, kodi_utils, gemini, match
    except Exception as e:
        xbmc.log('[gearsai-emb] import failed: {0}'.format(e), xbmc.LOGERROR)
        return None

    info = _info()
    try:
        release = match.player_release() or ''
    except Exception:
        release = ''

    # Community pool first -- instant, free, works for keyless users. The
    # pool's release match + anchor re-time already handle sync.
    pooled = _pool_hebrew(info, release)
    if pooled:
        _clear_ai_status()
        return pooled

    # A NON-manual arrival here (auto flow re-picking a remembered row) asks
    # first, exactly like auto AI translation does. Autoclose = decline.
    if kodi_utils.get_bool('ai_confirm', True) and not _is_manual():
        try:
            ok = xbmcgui.Dialog().yesno(
                'MasterKodi AI', 'לתרגם את הכתובית המובנית לעברית עם AI?',
                yeslabel='תרגם', nolabel='לא', autoclose=20000)
        except Exception:
            ok = False
        if not ok:
            _emb_log('user declined embedded AI translation')
            return 'DECLINED'

    if not gemini.have_keys():
        _notify('הגדר מפתח Gemini בהגדרות')
        return None

    try:
        url = xbmc.Player().getPlayingFile()
    except Exception:
        url = ''
    if not url:
        _notify('אין קובץ מתנגן')
        return None

    # MANUAL click: let the user pick the method (Asaf, 2026-07-21). The auto
    # flow never asks -- it stays on the automatic ladder.
    _mode = 'auto'
    if _is_manual():
        try:
            _c = xbmcgui.Dialog().select(
                'תרגום מסונכרן לפי הכתובית המובנית',
                ['אוטומטי (מומלץ) - מהיר כשאפשר, חילוץ כשצריך',
                 'יישור מהיר - כתובית חיצונית מסונכרנת לפי הזמנים המובנים',
                 'חילוץ מלא - תרגום ישירות מהכתובית המובנית (איטי יותר)'])
        except Exception:
            _c = 0
        if _c < 0:
            _emb_log('user cancelled method chooser')
            return None
        _mode = ('auto', 'align', 'extract')[_c]

    if not _emb_single_flight_take():
        return None

    prog = xbmcgui.DialogProgressBG()
    try:
        prog.create('MasterKodi AI', 'תרגום מובנה - מתחיל...')
    except Exception:
        prog = None

    def _status(msg, pct=None):
        try:
            if prog:
                prog.update(int(pct) if pct is not None else 0, 'MasterKodi AI', msg)
        except Exception:
            pass
        _step(msg, pct)

    abort_cb = _emb_abort_cb()
    try:
        src_text, src_lang, src_desc = _emb_resolve_source(info, release,
                                                           abort_cb, _status,
                                                           mode=_mode)
        if not src_lang:
            src_lang = 'en'
        if src_text and src_desc:
            # Transparency: tell the user exactly which path ran and which
            # subtitle became the source, before the translation starts.
            _status(src_desc)
            _notify(src_desc, 5000)
            _emb_log('source resolved: ' + src_desc)

        if not src_text:
            _close(prog)
            _notify('לא הצלחתי לקרוא את הכתובית המובנית מהקובץ')
            return None

        # ---- translate (same plumbing as translate_now) ----
        def _pct(pct, done, total, extra=None):
            try:
                active = (extra or {}).get('model') or ''
                _status('{0} · {1}/{2} שורות'.format(
                    gemini.label(active), done, total), pct)
            except Exception:
                pass

        stats = {}
        try:
            from resources.modules import general as _gen
            _gen.ai_cancel = False
        except Exception:
            _gen = None
        try:
            xbmcgui.Window(10000).clearProperty('gearsai.ai_cancel')
        except Exception:
            pass

        def _user_cancelled():
            if abort_cb():
                return True
            if _gen and getattr(_gen, 'ai_cancel', False):
                return True
            try:
                return xbmcgui.Window(10000).getProperty('gearsai.ai_cancel') == '1'
            except Exception:
                return False

        _status('מתרגם לעברית...')
        try:
            hebrew = translate.translate_srt(
                english_srt=src_text, source_lang=src_lang,
                title=info['title'], year=info['year'], cast=_cast(info),
                is_episode=(info['media_type'] == 'episode'),
                tvshow=info['tvshow'], season=info['season'],
                episode=info['episode'],
                api_key=kodi_utils.get_setting('api_key', ''),
                model=kodi_utils.get_setting('model', gemini.DEFAULT_MODEL),
                progress_cb=_pct, stats_out=stats,
                abort_cb=_user_cancelled)
        except translate.TranslationAborted:
            _close(prog)
            if _gen:
                _gen.ai_cancel = False
            _notify('התרגום בוטל')
            return None
        except gemini.RateLimited:
            _close(prog)
            _notify('יותר מדי בקשות - נסה שוב בעוד דקה')
            return None
        except gemini.QuotaExceeded:
            _close(prog)
            _notify('מכסת Gemini היומית נגמרה')
            return None
        except Exception as e:
            _close(prog)
            xbmc.log('[gearsai-emb] translate failed: {0}'.format(e), xbmc.LOGERROR)
            _notify('התרגום נכשל')
            return None

        _close(prog)
        hebrew = _heb_punct(hebrew) if hebrew else hebrew
        if not hebrew or not hebrew.strip():
            return None

        # Share back. release = the PLAYING file's release: this Hebrew's
        # timing lineage IS the playing file (that is the whole point), and
        # the source text we used carries that same timing, so it doubles as
        # a valid re-time anchor for other releases.
        _mdl = stats.get('model') or kodi_utils.get_setting('model', gemini.DEFAULT_MODEL)
        _short = ('חולץ מהקובץ' if (src_desc or '').startswith('חולץ')
                  else 'יושר לפי המובנית')
        _set_origin('תרגום מובנה · {0} · {1}'.format(_short, gemini.label(_mdl)))
        _pool_share(hebrew, src_text, info, release, _mdl)
        _notify('תרגום מובנה הושלם · {0} · {1}'.format(_short, gemini.label(_mdl)) +
                (' · שותף במאגר' if pool_should_share() else ''))
        return hebrew
    finally:
        _emb_single_flight_release()
        _close(prog)
        try:
            xbmcgui.Window(10000).clearProperty('gearsai.ai_status')
        except Exception:
            pass
