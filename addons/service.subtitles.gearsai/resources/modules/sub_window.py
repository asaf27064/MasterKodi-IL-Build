import xbmc,xbmcaddon,xbmcgui,json,os,shutil
import re as _re
Addon=xbmcaddon.Addon()
ADDON_PATH=Addon.getAddonInfo('path')
from resources.modules import log
from resources.modules.engine import download_sub
from resources.modules.general import CachedSubFolder
from urllib.parse import parse_qsl
from resources.modules.general import user_dataDir,MySubFolder,save_file_name,get_db_data,Thread,TransFolder,get_last_sub_source,norm_site_token
import urllib.parse

unque=urllib.parse.unquote_plus

# Compact site badge + colour per source (short codes keep line 1 narrow).
SITE_BADGES = {
    '[Ktuvit]': ('KT', 'springgreen'),
    '[Wizdom]': ('WZ', 'yellow'),
    '[Telegram]': ('TG', 'cyan'),
    '[OpenSubtitles]': ('OS', 'orange'),
    '[subdl]': ('SD', 'lightgreen'),
    '[YIFY]': ('YF', 'chocolate'),
    '[SubSource]': ('SS', 'mediumslateblue'),
    '[Subscene]': ('SC', 'aquamarine'),
    '[BSPlayer]': ('BS', 'white'),
}
_LANG_HE = {'English': 'אנגלית', 'Russian': 'רוסית', 'Arabic': 'ערבית'}


def _get_params(url):
    return dict(parse_qsl(url.replace('?','')))


def _sanitize_glyphs(s):
    """Drop characters the UI font can't draw (Rubik: Hebrew+Latin+Cyrillic).
    Foreign-script names (CJK/Thai/Arabic release tags from OpenSubtitles)
    rendered as tofu boxes -- strip those runs; if nothing readable remains
    the caller falls back to a generic label."""
    try:
        import unicodedata
        s = unicodedata.normalize('NFKC', s)   # fold styled unicode to plain
    except Exception:
        pass
    out = _re.sub('[^\u0020-\u04FF\u0590-\u05FF\u2010-\u2027\u20AA\u200E\u200F]+', ' ', s)
    out = _re.sub(r'\[\s*\]|\(\s*\)', '', out)   # husks left by stripped runs
    return _re.sub(r'\s{2,}', ' ', out).strip(' -—·')


def _clean_name(items, video_data, strip_title=True, max_len=0):
    """Release name without colour codes / site prefix. Optionally strips the
    playing title's redundant prefix and middle-truncates to max_len."""
    raw = _re.sub(r'\[/?COLOR[^\]]*\]', '', items[1]).strip()
    raw = _sanitize_glyphs(raw)
    site = str(items[9] or '')
    if site and raw.startswith(site):
        raw = raw[len(site):].strip()
    if strip_title:
        try:
            toks = [t for t in _re.split(r'[.\s_\-]+', raw) if t]
            title = (video_data.get('OriginalTitle') or '').strip()
            ttoks = [t for t in _re.split(r'[.\s_\-]+', title) if t]
            if (ttoks and len(toks) > len(ttoks)
                    and [t.lower() for t in toks[:len(ttoks)]] == [t.lower() for t in ttoks]):
                raw = '.'.join(toks[len(ttoks):])
        except Exception:
            pass
    if max_len and len(raw) > max_len:
        raw = raw[:int(max_len*0.6)] + '…' + raw[-int(max_len*0.38):]
    if not raw.strip():
        # nothing renderable survived (foreign-script-only name)
        raw = 'כתובית ' + ("עברית" if "Hebrew" in str(items[0]) else str(items[0]))
    return raw


def _current_index(list_o, last_name, last_lang, cur_source):
    """Index of the row that is the CURRENTLY playing sub, or -1.

    Identity is (release name, language, source-site). Name+lang alone
    collided: a native Hebrew sub and a foreign-site source sharing one
    release name both matched, and the wrong row wore [ נוכחית ].

    Pass 1 -- exact (name, lang, site) when the placement recorded its source.
    Pass 2 -- (name, site): the placed file is a DERIVATIVE saved under the
              source row's name with a different recorded language (AI/pool
              translation of an English source); its source row is truthfully
              'the row whose sub is playing'.
    Legacy  -- meta without a source (old installs / stale sidecar): the
              original name+lang first-match, unchanged behavior.
    Pure in-memory pre-pass over ~a screenful of tuples -- zero I/O."""
    if cur_source:
        for i, it in enumerate(list_o):
            if (it[8] == last_name and it[0] == last_lang
                    and norm_site_token(it[9]) == cur_source):
                return i
        for i, it in enumerate(list_o):
            if it[8] == last_name and norm_site_token(it[9]) == cur_source:
                return i
        return -1
    for i, it in enumerate(list_o):
        if it[8] == last_name and it[0] == last_lang:
            return i
    return -1


def _build_rows(list_o, video_data, all_subs, last_name, last_lang, two_line):
    """Format the subtitle rows. Returns a list of (line1, line2) tuples --
    line2 is '' in single-line (classic) mode where line1 carries a shortened
    name instead."""
    rows = []
    # -- per-build state, hoisted OUT of the row loop (no per-row I/O) --
    cur_source = get_last_sub_source(video_data, last_name, last_lang)
    cur_idx = _current_index(list_o, last_name, last_lang, cur_source)
    try:
        _synced_now = xbmcgui.Window(10000).getProperty('gearsai.current_synced') == '1'
    except Exception:
        _synced_now = False
    try:
        _trans_names = set(os.listdir(TransFolder))
    except Exception:
        _trans_names = set()
    _origin_cache = {}
    for idx, items in enumerate(list_o):
        try:
            val = all_subs.get(items[8])
        except Exception:
            val = None
        try:
            pct = int(items[5])
        except Exception:
            pct = 0
        mcolor = 'springgreen' if pct >= 85 else ('gold' if pct >= 60 else 'darkorange')
        # embedded/derived rows carry a magic >100 sort value -- '101%' on
        # screen reads like a bug; say what it actually is
        pct_txt = ('מוטמע' if pct > 100 else '{0}%'.format(pct))
        lang = "עברית" if "Hebrew" in items[0] else _LANG_HE.get(items[0], items[0])

        is_downloaded = bool(val and items[0] in val)
        if idx == cur_idx:
            status = ("[COLOR gold][B][ נוכחית · סונכרן ][/B][/COLOR]  " if _synced_now
                      else "[COLOR gold][B][ נוכחית ][/B][/COLOR]  ")
        elif is_downloaded:
            status = "[COLOR springgreen][B][ ירדה ][/B][/COLOR]  "
        else:
            status = ""

        # Machine-translated to Hebrew? say so + from where (.origin marker).
        if items[8] in _trans_names:
            if items[8] in _origin_cache:
                origin = _origin_cache[items[8]]
            else:
                origin = ''
                try:
                    with open(os.path.join(TransFolder, items[8] + '.origin'),
                              encoding='utf-8') as f_o:
                        origin = f_o.read().strip()
                except Exception:
                    pass
                _origin_cache[items[8]] = origin
            # a SYNC of a native Hebrew sub is not a translation -- say what
            # actually happened (the .origin text), no misleading 'תורגם'
            # trans files (Gemini / pool / pool-synced) are ALWAYS keyed to the
            # ENGLISH SOURCE's release name -- the badge belongs on source rows
            # ("this English sub has a Hebrew derivative"). A NATIVE Hebrew row
            # (KT/WZ) sharing the release name inherits it only by collision:
            # a Hebrew sub can never be 'תורגם', so never badge Hebrew rows.
            _row_is_hebrew = 'Hebrew' in str(items[0])
            if not _row_is_hebrew:
                _is_sync_origin = bool(origin and ('סונכרן' in origin)
                                       and ('Gemini' not in origin) and ('Google' not in origin))
                tag = origin if _is_sync_origin else ('תורגם' + (' · ' + origin if origin else ' לעברית'))
                status += "[COLOR magenta][B][ " + tag + " ][/B][/COLOR]  "

        # SDH / hearing-impaired flag (speaker tags) -- these translate to
        # BETTER Hebrew gender, so surface it; the auto-pick already prefers
        # them as the translation source.
        if str(items[7]).lower() == 'true':
            status += "[COLOR cyan][B][ SDH ][/B][/COLOR]  "

        # Image-based subs (VobSub .idx / PGS .sup inside a zip): bitmaps that
        # ignore the Kodi font and render blurry. Shown (nothing is hidden)
        # but labelled so the user knows why auto-pick skips them.
        _lname = (str(items[8]) + ' ' + str(items[1])).lower()
        if ('idx_in_zip' in _lname or 'idx.in.zip' in _lname
                or 'sup_in_zip' in _lname or 'sup.in.zip' in _lname
                or 'vobsub' in _lname):
            status += "[COLOR darkorange][ כתובית תמונה · פונט קבוע ומטושטש ][/COLOR]  "

        site = str(items[9] or '')
        badge, bcolor = SITE_BADGES.get(site, (site.strip('[]') or '--', 'white'))
        if two_line:
            # name budget SHRINKS by the visible width of the status badges --
            # a fixed budget overflowed on badge-heavy rows and the ellipsis
            # swallowed the [ נוכחית ] marker itself
            _vis_status = _re.sub(r'\[/?(?:COLOR|B)[^\]]*\]', '', status)
            _budget = max(20, 42 - len(_vis_status))
            name1 = _clean_name(items, video_data, strip_title=True, max_len=_budget)
            # ‏ (RLM) pins EVERY row to RTL layout -- without it a row
            # whose first strong character is Latin (e.g. "[ SDH ]") flips to
            # LTR and its segments render on the opposite side of the others
            line1 = ("‏{st}[COLOR deepskyblue][B]{lang}[/B][/COLOR]"
                     "  [COLOR {mc}][B]{pct}[/B][/COLOR]"
                     "  [COLOR {bc}][B]{badge}[/B][/COLOR]  {name}").format(
                         st=status, lang=lang, mc=mcolor, pct=pct_txt,
                         bc=bcolor, badge=badge, name=name1)
            line2 = _clean_name(items, video_data, strip_title=False)
            rows.append((line1, line2))
        else:
            name = _clean_name(items, video_data, strip_title=True, max_len=58)
            line1 = ("‏{st}[COLOR deepskyblue][B]{lang}[/B][/COLOR]"
                     "  [COLOR {mc}][B]{pct}[/B][/COLOR]"
                     "  [COLOR {bc}][B]{badge}[/B][/COLOR]  {name}").format(
                         st=status, lang=lang, mc=mcolor, pct=pct_txt,
                         bc=bcolor, badge=badge, name=name)
            rows.append((line1, ''))
    return rows


def _download_row(items_row, video_data):
    """Download+apply the given result row (shared by both window styles).
    Returns (status, filename, language): status in 'ok'/'embedded'/'fault'."""
    params = _get_params(items_row[4])
    download_data = json.loads(unque(params["download_data"]))
    source = params["source"]
    language = params["language"]
    filename = params["filename"]
    try:
        # Manual pick = explicit consent; the AI confirmation prompt is skipped.
        from resources.modules import general
        general.ai_manual = True
        # the search-progress dialog leaves break_all=True when it closes, and
        # engine.py gates its translated-file WRITES on it -- a manual English
        # pick then 'succeeds' downloading but never writes the Hebrew file
        # (ENOENT -> 'נכשל'). A fresh explicit pick is never aborted.
        general.break_all = False
        try:
            sub_file = download_sub(source, download_data, MySubFolder, language, filename)
        finally:
            general.ai_manual = False
        xbmc.sleep(100)
        if sub_file == 'EmbeddedSubSelected':
            save_file_name(unque(filename), language, video_data, source=source)
            return 'embedded', filename, language
        if sub_file == 'FaultSubException' or not sub_file or sub_file == '0':
            return 'fault', filename, language
        xbmc.Player().setSubtitles(sub_file)
        log.warning('My Window Sub result:' + str(sub_file))
        save_file_name(filename, language, video_data, source=source)
        # Keep a copy in Cached_subs (respecting the cache-size setting).
        try:
            max_sub_cache = int(Addon.getSetting("subtitle_trans_cache"))
            if len(os.listdir(CachedSubFolder)) > max_sub_cache:
                for f_o in os.listdir(CachedSubFolder):
                    os.remove(os.path.join(CachedSubFolder, f_o))
            file_type = os.path.splitext(sub_file)[1]
            c_sub_file = os.path.join(CachedSubFolder, f"{source}_{language}_{filename}{file_type}")
            if not os.path.exists(c_sub_file):
                if file_type in ('.idx', '.sup'):
                    shutil.copy(sub_file, c_sub_file.replace('idx', 'sub').replace('sup', 'sub'))
                shutil.copy(sub_file, c_sub_file)
        except Exception as e:
            log.warning(f"cache copy failed | {e}")
        return 'ok', filename, language
    except Exception as e:
        log.warning(f"_download_row fault | {e}")
        return 'fault', filename, language


############################ NEW XML WINDOW ############################

class SubsXMLWindow(xbmcgui.WindowXMLDialog):
    """Modern two-line subtitle picker (SubsWindow.xml). Line 1 = tags +
    language + match% + source badge; line 2 = the FULL release name -- no
    truncation, no waiting for focus-scroll."""

    def setup(self, title, list_o, f_list, video_data, all_subs,
              last_sub_name_in_cache, last_sub_language_in_cache):
        self.title = title
        self.list_o = list_o
        self.full_list = f_list
        self.video_data = video_data
        self.all_subs = all_subs
        self.last_name = last_sub_name_in_cache
        self.last_lang = last_sub_language_in_cache
        self.close_window = False
        self.header_text = str(video_data.get('Tagline') or video_data.get('file_original_path') or '')


    def _status_ctrl(self, text):
        c = self.getControl(103)
        try:
            c.setText(text)      # textbox (wrapping, 2 lines)
        except Exception:
            try: c.setLabel(text)
            except Exception: pass

    def onInit(self):
        try:
            self.getControl(102).setLabel('[B]{0}[/B]'.format(self.title))
            self._status_ctrl(self.header_text)
            self.getControl(104).setLabel(
                '[COLOR gold][B]{0}[/B][/COLOR] כתוביות · מסודר לפי אחוז התאמה'.format(len(self.list_o)))
            self._fill_list()
            # results are ON SCREEN -> the search phase is over. Publish END so
            # show_results' top overlay ('מסדר כתוביות X/Y') closes instead of
            # lingering behind the window / style panel until its 120s timeout.
            try:
                from resources.modules import general
                general.show_msg = 'END'
            except Exception:
                pass
            Thread(target=self._background_task).start()
        except Exception as e:
            log.warning('SubsXMLWindow onInit error: %s' % e)

    def _fill_list(self, keep_pos=False):
        ctl = self.getControl(100)
        pos = ctl.getSelectedPosition() if keep_pos else 0
        ctl.reset()
        rows = _build_rows(self.list_o, self.video_data, self.all_subs,
                           self.last_name, self.last_lang, two_line=True)
        items = []
        # MASTERKODI: pinned action row -- re-time the Hebrew sub currently on
        # screen onto the playing file's real timing (embedded-English oracle
        # first, release-matched external English fallback). Shown only while a
        # Hebrew sub placed by us is active.
        # MASTERKODI: sync is an ACTION, not a result -- it lives as footer
        # button 105 (visible only while a placed Hebrew sub is showing), not
        # as a pinned row inside the results (Asaf, 2026-07-18).
        self._has_sync_row = False
        try:
            if Addon.getSetting('manual_sync_row') != 'false':
                cur = xbmcgui.Window(10000).getProperty('gearsai.current_heb_sub')
                self._has_sync_row = bool(cur)
        except Exception:
            pass
        try:
            self.getControl(105).setVisible(self._has_sync_row)
        except Exception:
            pass
        # stop-button visibility is XML-driven (Window(home) property)
        for line1, line2 in rows:
            items.append(xbmcgui.ListItem(label=line1, label2=line2))
        ctl.addItems(items)
        if items:
            ctl.selectItem(min(max(pos, 0), len(items) - 1))

    def _background_task(self):
        from resources.modules import general
        showing = False
        while not self.close_window:
            msg = general.show_msg
            if not msg or msg == 'END':
                # service-side translations publish via the window property
                try:
                    msg = xbmcgui.Window(10000).getProperty('gearsai.ai_status') or msg
                except Exception:
                    pass
            try:
                if ('מתרגם' in msg) or ('MasterKodi' in msg):
                    self._status_ctrl('[COLOR gold][B]{0}[/B][/COLOR]'.format(msg))
                    showing = True
                elif showing and (msg == 'END' or msg == ''):
                    self._status_ctrl(self.header_text)
                    showing = False
            except Exception:
                pass
            xbmc.sleep(500)

    def _set_status(self, text, color='gold'):
        try:
            self._status_ctrl('[COLOR {0}][B]{1}[/B][/COLOR]'.format(color, text))
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == 101:
            self.close_window = True
            self.close()
            return
        if control_id == 107:
            # open the live style panel; MySubs reopens this window after it
            self._open_style = True
            self.close_window = True
            self.close()
            return
        if control_id == 105:
            self._run_sync()
            return
        if control_id == 106:
            try:
                from resources.modules import general
                general.ai_cancel = True
            except Exception:
                pass
            try:
                xbmcgui.Window(10000).setProperty('gearsai.ai_cancel', '1')
            except Exception:
                pass
            self._set_status('מבטל תרגום…', 'orange')
            return
        if control_id != 100:
            return
        idx = self.getControl(100).getSelectedPosition()
        if idx < 0 or idx >= len(self.full_list):
            return
        # download (and a possible AI translation) run in a BACKGROUND thread:
        # inline they blocked Kodi's serialized event loop for this window, so
        # nothing -- not even CLOSE -- responded until the translation ended
        # (Asaf, 2026-07-18). One at a time; closing mid-way is fine, the
        # work continues exactly like a service-side translation.
        if getattr(self, '_dl_busy', False):
            self._set_status('הורדה כבר רצה…', 'orange')
            return
        self._dl_busy = True
        self._set_status('מוריד…')
        row = self.full_list[idx]

        def _bg_download():
            try:
                status, filename, language = _download_row(row, self.video_data)
                if self.close_window:
                    return
                if status == 'fault':
                    self._set_status('תקלה בהורדה, נסה שנית', 'red')
                    return
                if status == 'embedded':
                    self._set_status('נבחר תרגום מובנה, יופיע בעוד 10 שניות', 'deepskyblue')
                else:
                    self._set_status('מוכן', 'springgreen')
                try:
                    self.last_name, self.last_lang, self.all_subs = get_db_data(self.video_data)
                except Exception:
                    pass
                try:
                    self._fill_list(keep_pos=True)
                except Exception:
                    pass
                from resources.modules import general
                general.show_msg = "END"
            finally:
                self._dl_busy = False
        Thread(target=_bg_download).start()

    def _run_sync(self):
        """MASTERKODI: run the sync-current-sub flow (embedded-English oracle ->
        external English) and load the re-timed sub. Blocking with status text --
        the probe typically takes ~5-15s."""
        self._set_status('מסנכרן לפי הקובץ המתנגן… (עד ~20 שניות)')
        try:
            from resources.aisubs import ai_bridge
            path = ai_bridge.sync_current_sub()
        except Exception as e:
            log.warning('sync row error: %s' % e)
            path = None
        if path:
            try:
                # Mirror the normal-download path exactly (that reliably selects +
                # shows the sub): a plain setSubtitles to a fresh MySubFolder file.
                xbmc.Player().setSubtitles(path)
            except Exception as e:
                log.warning('apply synced sub failed: %s' % e)
            self._set_status('הכתובית סונכרנה!', 'springgreen')
        else:
            self._set_status('הסנכרון לא הצליח - הכתובית נשארה כפי שהיא', 'red')

    def onAction(self, action):
        if action.getId() in (10, 92, 216, 247, 257, 275, 61467, 61448):
            self.close_window = True
            self.close()


####################### LIVE SUBTITLE STYLE PANEL #######################
# Kodi has NO in-player UI for subtitle appearance (font size/color/etc live
# in Settings>Player). These are ordinary settings though, and for TEXT subs
# a JSON-RPC change restyles the subtitle ON SCREEN immediately -- so this
# compact top-right panel edits them live while the movie (and the subtitle,
# bottom-center) stays fully visible. Settings are GLOBAL and persist for all
# playbacks (guisettings.xml); 'שחזר ברירת מחדל' restores the look captured
# the first time the panel ever opened. Bitmap subs (VobSub/PGS) are baked
# pixels and ignore styling -- known, by design.

def _jrpc_setting(method, params):
    try:
        q = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params})
        return json.loads(xbmc.executeJSONRPC(q)).get('result')
    except Exception as e:
        log.warning('style jrpc error: %s' % e)
        return None


def _style_get(sid):
    r = _jrpc_setting('Settings.GetSettingValue', {'setting': sid})
    return r.get('value') if isinstance(r, dict) else None


def _style_set(sid, val):
    _jrpc_setting('Settings.SetSettingValue', {'setting': sid, 'value': val})


# family -> ordered variations (label, internal family name as Kodi lists it).
# TWO rows (גופן + וריאציה) instead of one flat 25-entry cycle (Asaf,
# 2026-07-19). Internal names are the ones we baked into the bundled statics.
_FONT_FAMILIES = [
    ('ברירת מחדל', [('רגיל', 'DEFAULT')]),
    ('Heebo', [('דק', 'Heebo Light'), ('רגיל', 'Heebo'), ('בינוני', 'Heebo Medium'),
               ('מודגש', 'Heebo Bold')]),
    ('Assistant', [('דק', 'Assistant Light'), ('רגיל', 'Assistant'),
                   ('חצי מודגש', 'Assistant SemiBold'), ('מודגש', 'Assistant Bold')]),
    ('Rubik', [('דק', 'Rubik Light'), ('רגיל', 'Rubik'), ('בינוני', 'Rubik Medium'),
               ('חצי מודגש', 'Rubik SemiBold'), ('מודגש', 'Rubik Bold'),
               ('מודגש מאוד', 'Rubik ExtraBold')]),
    ('Noto Hebrew', [('דק', 'Noto Sans Hebrew Light'), ('רגיל', 'Noto Sans Hebrew'),
                     ('חצי מודגש', 'Noto Sans Hebrew SemiBold'),
                     ('מודגש', 'Noto Sans Hebrew Bold'), ('מודגש מאוד', 'Noto Sans Hebrew ExtraBold')]),
    ('Open Sans', [('דק', 'Open Sans Light'), ('רגיל', 'Open Sans'), ('מודגש', 'Open Sans Bold')]),
    ('Google Sans', [('רגיל', 'Google Sans'), ('בינוני', 'Google Sans Medium'),
                     ('מודגש', 'Google Sans Bold')]),
    ('Varela Round', [('רגיל', 'Varela Round')]),
    ('David', [('רגיל', 'David')]),
    ('Tahoma', [('רגיל', 'Tahoma')]),
    ('Arial', [('רגיל', 'Arial')]),
]

_STYLE_COLORS = [('לבן', 'FFFFFFFF'), ('לבן רך', 'FFD8D8D8'), ('צהוב', 'FFFFFF00'),
                 ('תכלת', 'FF7FD4E8'), ('ירוק', 'FF54D169'), ('כתום', 'FFFFA500'),
                 ('שחור', 'FF000000')]
_STYLE_WEIGHTS = [('רגיל', 0), ('מודגש', 1), ('נטוי', 2), ('מודגש נטוי', 3)]
_STYLE_BGTYPES = [('ללא', 0), ('הצללה', 1), ('קופסה', 2), ('קופסה מרובעת', 3)]
_STYLE_SETTINGS = ('subtitles.fontname', 'subtitles.fontsize', 'subtitles.colorpick',
                   'subtitles.style', 'subtitles.bordersize', 'subtitles.shadowsize',
                   'subtitles.backgroundtype', 'subtitles.marginvertical',
                   'subtitles.opacity', 'subtitles.bgopacity', 'subtitles.shadowopacity')


class SubsStyleWindow(xbmcgui.WindowXMLDialog):
    """Live subtitle-appearance editor (SubsStyleWindow.xml). 12 rows in ROWS
    order; focus a row, arrows change the value (RTL: left=+) and the on-screen
    subtitle restyles instantly. 210 = reset to captured defaults, 101 = close."""

    ROWS = (208, 209, 201, 202, 211, 203, 204, 205, 213, 206, 212, 207)

    def onInit(self):
        self._defaults = self._load_or_capture_defaults()
        self._refresh_all()
        try:
            self.setFocusId(self.ROWS[0])
        except Exception:
            pass

    # -- defaults snapshot: the shipped look, captured once, forever the
    #    thing 'שחזר ברירת מחדל' returns to --
    def _load_or_capture_defaults(self):
        p = os.path.join(user_dataDir, 'style_defaults.json')
        snap = {}
        try:
            with open(p, encoding='utf-8') as f:
                snap = json.loads(f.read())
        except Exception:
            snap = {}
        # capture any setting the stored snapshot doesn't cover yet (e.g. the
        # font row was added after a snapshot was already written)
        changed = False
        for sid in _STYLE_SETTINGS:
            if sid not in snap:
                v = _style_get(sid)
                if v is not None:
                    snap[sid] = v
                    changed = True
        if changed:
            try:
                tmp = p + '.tmp'
                with open(tmp, 'w', encoding='utf-8') as f:
                    f.write(json.dumps(snap))
                os.replace(tmp, p)
            except Exception:
                pass
        return snap

    def _families(self):
        """The registry filtered to fonts Kodi actually lists right now.
        Built once per window open; if introspection fails, trust the registry
        (the self-heal installs the bundled fonts before this window opens)."""
        fams = getattr(self, '_fams', None)
        if fams:
            return fams
        avail = None
        try:
            r = _jrpc_setting('Settings.GetSettings', {'level': 'expert'})
            for st in (r or {}).get('settings', []):
                if st.get('id') == 'subtitles.fontname':
                    vals = [o.get('value') for o in st.get('options', []) if o.get('value')]
                    if vals:
                        avail = set(vals)
                    break
        except Exception as e:
            log.warning('font options: %s' % e)
        fams = []
        for fam, variations in _FONT_FAMILIES:
            if avail is None:
                keep = list(variations)
            else:
                keep = [v for v in variations if v[1] == 'DEFAULT' or v[1] in avail]
            if keep:
                fams.append((fam, keep))
        self._fams = fams
        return fams

    def _font_pos(self):
        """(family_idx, variation_idx) of the current fontname, or (-1, -1)."""
        cur = _style_get('subtitles.fontname') or 'DEFAULT'
        for fi, (_fam, variations) in enumerate(self._families()):
            for vi, (_lbl, val) in enumerate(variations):
                if val == cur:
                    return fi, vi
        return -1, -1

    def _row_state(self, cid):
        """(title, value-label) for a row from the LIVE setting value."""
        if cid == 208:
            fi, _vi = self._font_pos()
            return 'גופן', (self._families()[fi][0] if fi >= 0 else 'מותאם')
        if cid == 209:
            fi, vi = self._font_pos()
            return 'וריאציה', (self._families()[fi][1][vi][0] if fi >= 0 else '-')
        if cid == 201:
            return 'גודל', str(_style_get('subtitles.fontsize') or '?')
        if cid == 202:
            v = _style_get('subtitles.colorpick') or ''
            name = next((n for n, h in _STYLE_COLORS if h == v), 'מותאם')
            return 'צבע', name
        if cid == 203:
            v = _style_get('subtitles.style')
            name = next((n for n, i in _STYLE_WEIGHTS if i == v), 'רגיל')
            return 'משקל', name
        if cid == 204:
            return 'קו מתאר', '%s%%' % (_style_get('subtitles.bordersize') or 0)
        if cid == 205:
            return 'צל', '%s%%' % (_style_get('subtitles.shadowsize') or 0)
        if cid == 206:
            v = _style_get('subtitles.backgroundtype')
            name = next((n for n, i in _STYLE_BGTYPES if i == v), 'ללא')
            return 'רקע', name
        if cid == 211:
            return 'שקיפות טקסט', '%s%%' % (_style_get('subtitles.opacity') or 0)
        if cid == 213:
            return 'שקיפות צל', '%s%%' % (_style_get('subtitles.shadowopacity') or 0)
        if cid == 212:
            return 'שקיפות רקע', '%s%%' % (_style_get('subtitles.bgopacity') or 0)
        if cid == 207:
            try:
                v = int(round(float(_style_get('subtitles.marginvertical') or 0)))
            except Exception:
                v = 0
            return 'גובה מהתחתית', '%s%%' % v
        return '', ''

    def _refresh_row(self, cid):
        title, val = self._row_state(cid)
        try:
            # title on the button (right, RTL); value on its OWN label control
            # (cid+100, left side) -- button label2 renders at the same edge as
            # a right-aligned label and the two overlapped
            self.getControl(cid).setLabel('[B]%s[/B]' % title)
            self.getControl(cid + 100).setLabel('[B]%s[/B]' % val)
        except Exception as e:
            log.warning('style row %s: %s' % (cid, e))

    def _refresh_all(self):
        for cid in self.ROWS:
            self._refresh_row(cid)

    @staticmethod
    def _cycle(options, current, step):
        idx = next((i for i, (_, v) in enumerate(options) if v == current), -1)
        return options[(idx + step) % len(options)][1]

    def _adjust(self, cid, step):
        if cid == 208:
            fams = self._families()
            fi, vi = self._font_pos()
            cur_lbl = fams[fi][1][vi][0] if fi >= 0 else 'רגיל'
            nfam, nvars = fams[(fi + step) % len(fams)]
            # keep the same variation feel across families when possible
            nval = dict((l, v) for l, v in nvars).get(cur_lbl)
            if nval is None:
                nval = dict((l, v) for l, v in nvars).get('רגיל', nvars[0][1])
            _style_set('subtitles.fontname', nval)
            self._refresh_row(209)
        elif cid == 209:
            fams = self._families()
            fi, vi = self._font_pos()
            if fi < 0:
                _style_set('subtitles.fontname', fams[0][1][0][1])
            else:
                variations = fams[fi][1]
                _style_set('subtitles.fontname', variations[(vi + step) % len(variations)][1])
            self._refresh_row(208)
        elif cid == 201:
            v = int(_style_get('subtitles.fontsize') or 42) + step * 2
            _style_set('subtitles.fontsize', max(12, min(74, v)))
        elif cid == 202:
            cur = _style_get('subtitles.colorpick') or 'FFFFFFFF'
            _style_set('subtitles.colorpick',
                       self._cycle(_STYLE_COLORS, cur, step))
        elif cid == 203:
            _style_set('subtitles.style',
                       self._cycle(_STYLE_WEIGHTS, _style_get('subtitles.style'), step))
        elif cid == 204:
            v = int(_style_get('subtitles.bordersize') or 0) + step * 5
            _style_set('subtitles.bordersize', max(0, min(100, v)))
        elif cid == 205:
            v = int(_style_get('subtitles.shadowsize') or 0) + step * 5
            _style_set('subtitles.shadowsize', max(0, min(100, v)))
        elif cid == 206:
            _style_set('subtitles.backgroundtype',
                       self._cycle(_STYLE_BGTYPES, _style_get('subtitles.backgroundtype'), step))
        elif cid == 211:
            v = int(_style_get('subtitles.opacity') or 100) + step * 5
            _style_set('subtitles.opacity', max(0, min(100, v)))
        elif cid == 213:
            v = int(_style_get('subtitles.shadowopacity') or 0) + step * 5
            _style_set('subtitles.shadowopacity', max(0, min(100, v)))
        elif cid == 212:
            v = int(_style_get('subtitles.bgopacity') or 0) + step * 5
            _style_set('subtitles.bgopacity', max(0, min(100, v)))
        elif cid == 207:
            try:
                v = float(_style_get('subtitles.marginvertical') or 0) + step * 1.0
            except Exception:
                v = 0.0
            _style_set('subtitles.marginvertical', max(0.0, min(50.0, round(v, 2))))
            # KODI LIMITATION (OverlayRenderer.cpp): vertical margin is NOT
            # applied mid-playback (libass rewind side effect) -- it takes
            # effect on the next playback. Say so once per window.
            if not getattr(self, '_margin_note', False):
                self._margin_note = True
                try:
                    from resources.modules.general import notify
                    notify('גובה מהתחתית יוחל בניגון הבא', times=4500)
                except Exception:
                    pass
        else:
            return
        self._refresh_row(cid)

    def onClick(self, control_id):
        if control_id == 101:
            self.close()
        elif control_id == 210:
            for sid, val in (self._defaults or {}).items():
                _style_set(sid, val)
            self._refresh_all()
        elif control_id in self.ROWS:
            # SELECT on a row steps forward too (touch/mouse friendliness)
            self._adjust(control_id, 1)

    def onAction(self, action):
        aid = action.getId()
        if aid in (1, 2):     # ACTION_MOVE_LEFT / ACTION_MOVE_RIGHT
            cid = self.getFocusId()
            if cid in self.ROWS:
                # RTL: LEFT increases, RIGHT decreases (Asaf, 2026-07-19)
                self._adjust(cid, 1 if aid == 1 else -1)
                return
        if aid in (10, 92, 216, 247, 257, 275, 61467, 61448):
            self.close()


# Bump when the bundled font pack changes -- triggers one removal+overwrite
# sync pass per device (Kodi only lists special://home/media/Fonts for
# subtitles.fontname; the skins' font folders are invisible to it).
_FONT_PACK_VERSION = '7'   # v6: OFFICIAL Google static builds (fonts.google.com
                           # static/ folder), name-table-only rebrand -- the
                           # instancer-generated v4/v5 files rendered weights
                           # inconsistently vs Google's own statics (Asaf).
                           # v7: + Google Sans (open-sourced 2025, OFL, no RFN,
                           # HAS Hebrew) subset to Hebrew+Latin, 3 weights.
# Internal FAMILY names whose files the pack removes -- if the user's active
# subtitles.fontname points at one of these, migrate it to the build default
# ('Rubik') instead of letting Kodi silently fall back on a missing font.
# (Asaf's own device: legacy default was NarkisDVD @70 -- real case, 2026-07-19.)
_FONT_MIGRATE = ('NarkisDVD', 'NarkisTam Light', 'NarkisTamKODI Light',
                 'Assistant ExtraLight', 'Alef', 'Alef Bold', 'IBM Plex Hebrew',
                 'IBM Plex Hebrew Bold', 'Secular One', 'David Libre')
_FONT_MIGRATE_TO = 'Rubik'
# Legacy files to REMOVE: Narkis set (Asaf, 2026-07-19) + the AF3-sourced
# variable-font copies whose broken internal names ('Assistant ExtraLight' on
# every weight) merged whole families into one wrong list entry + the pack-2
# families Asaf swapped for Open Sans.
_FONT_REMOVE = ('NarkisDVD.ttf', 'NarkisTamLight.ttf', 'NarkisTamLightKodi.ttf',
                'NTAMLI.ttf', 'Heebo-Regular.ttf', 'Heebo-Bold.ttf',
                'Heebo-Light.ttf', 'Assistant-Regular.ttf', 'Assistant-Bold.ttf',
                'Assistant-Light.ttf', 'Alef.ttf', 'AlefBold.ttf',
                'IBMPlexHebrew.ttf', 'IBMPlexHebrewBold.ttf', 'SecularOne.ttf',
                'DavidLibre.ttf')


def _ensure_subtitle_fonts():
    """One-shot font-pack sync into Kodi's font folder: remove the legacy/broken
    files, install/overwrite the clean Google-Fonts statics (unique internal
    family per file so Kodi's family-dedup never merges them), drop the stale
    fontcache. Runs the full pass only when the pack version changes; otherwise
    it's a single setting read."""
    try:
        if Addon.getSetting('font_pack_v') == _FONT_PACK_VERSION:
            return
        import xbmcvfs
        src = os.path.join(ADDON_PATH, 'resources', 'fonts')
        dst = xbmcvfs.translatePath('special://home/media/Fonts')
        if not os.path.isdir(src):
            return
        os.makedirs(dst, exist_ok=True)
        changed = False
        for f in _FONT_REMOVE:
            try:
                p = os.path.join(dst, f)
                if os.path.exists(p):
                    os.remove(p)
                    changed = True
                    log.warning('removed legacy subtitle font: %s' % f)
            except Exception:
                pass
        for f in os.listdir(src):
            if f.lower().endswith(('.ttf', '.otf')):
                try:
                    sp, tp = os.path.join(src, f), os.path.join(dst, f)
                    if (not os.path.exists(tp)
                            or os.path.getsize(tp) != os.path.getsize(sp)):
                        shutil.copy2(sp, tp)
                        changed = True
                except Exception:
                    pass
        try:
            cache = os.path.join(dst, 'fontcache.xml')
            if os.path.exists(cache):
                os.remove(cache)
        except Exception:
            pass
        try:
            cur = _style_get('subtitles.fontname')
            if cur in _FONT_MIGRATE:
                _style_set('subtitles.fontname', _FONT_MIGRATE_TO)
                log.warning('migrated subtitle font %s -> %s (file removed by pack)'
                            % (cur, _FONT_MIGRATE_TO))
        except Exception:
            pass
        Addon.setSetting('font_pack_v', _FONT_PACK_VERSION)
        log.warning('subtitle font pack synced to v%s' % _FONT_PACK_VERSION)
        if changed:
            # Kodi enumerates fonts ONCE at startup (GUIFontManager::Initialize
            # -> LoadUserFonts); swapped files are invisible until then.
            try:
                from resources.modules.general import notify
                notify('גופני הכתוביות עודכנו - יש להפעיל מחדש את קודי', times=6000)
            except Exception:
                pass
    except Exception as e:
        log.warning('font install failed: %s' % e)


def open_style_window():
    try:
        _ensure_subtitle_fonts()
        w = SubsStyleWindow('SubsStyleWindow.xml', ADDON_PATH, 'Default', '1080i')
        w.doModal()
        del w
    except Exception as e:
        log.warning('style window failed: %s' % e)


########################## CLASSIC (pyxbmct) ###########################

def _classic_window(title,list_o,f_list,video_data,all_subs,last_sub_name_in_cache,last_sub_language_in_cache):
    from resources.modules import pyxbmct

    class MySubsClassic(pyxbmct.AddonDialogWindow):

        def __init__(self, title):
            super(MySubsClassic, self).__init__(title)
            self.list_o=list_o
            self.title=title
            wd=int(Addon.getSetting("subs_width"))
            hd=int(Addon.getSetting("subs_height"))
            px=int(Addon.getSetting("subs_px"))
            py=int(Addon.getSetting("subs_py"))
            self.full_list=f_list
            self.video_data=video_data
            # 10-row grid: header(0) / list(1-7) / count(8) / close(9)
            self.setGeometry(wd, hd, 10, 1,pos_x=px, pos_y=py)
            self.all_subs=all_subs
            self.last_sub_name_in_cache=last_sub_name_in_cache
            self.last_sub_language_in_cache=last_sub_language_in_cache
            self.set_info_controls()
            self.set_active_controls()
            self.set_navigation()
            self.close_window=False
            self.connect(pyxbmct.ACTION_NAV_BACK, self.close)
            Thread(target=self.background_task).start()

        def background_task(self):
            from resources.modules import general
            showing_progress = False
            while (self.close_window==False):
                msg = general.show_msg
                if ('מתרגם' in msg) or ('MasterKodi' in msg):
                    self.label_info.setLabel(f"[B][COLOR gold]{msg}[/COLOR][/B]")
                    showing_progress = True
                elif showing_progress and (msg == 'END' or msg == ''):
                    self.label_info.setLabel(self.label_info_text)
                    showing_progress = False
                xbmc.sleep(500)

        def set_info_controls(self):
            self.total_subs_count=len(self.list_o)
            self.label = pyxbmct.Label(
                f"[B][COLOR gold]{str(self.total_subs_count)}[/COLOR] כתוביות · מסודר לפי אחוז התאמה[/B]",
                alignment=pyxbmct.ALIGN_CENTER)
            self.placeControl(self.label,  8, 0, 1, 1)

            self.video_file_name_label = self.video_data['Tagline'] or self.video_data['file_original_path']
            self.label_info_text = f"[B][COLOR deepskyblue]{self.video_file_name_label}[/COLOR][/B]"
            self.label_info = pyxbmct.Label(self.label_info_text, alignment=pyxbmct.ALIGN_CENTER)
            self.placeControl(self.label_info,  0, 0, 1, 1)

            self.list = pyxbmct.List()
            self.placeControl(self.list, 1, 0, 7, 1)
            self.connect(self.list, self.click_list)

            self.button = pyxbmct.Button('[B]סגור[/B]')
            self.placeControl(self.button, 9, 0)
            self.connect(self.button, self.click_c)

        def click_list(self):
            idx=self.list.getSelectedPosition()
            self.label_info.setLabel('[B][COLOR gold]מוריד…[/COLOR][/B]' + ' | ' + self.label_info_text)
            status, filename, language = _download_row(self.full_list[idx], self.video_data)
            if status == 'fault':
                self.label_info.setLabel('[B][COLOR red]תקלה בהורדה, נסה שנית[/COLOR][/B]' + ' | ' + self.label_info_text)
                return
            if status == 'embedded':
                self.label_info.setLabel('[B][COLOR deepskyblue]נבחר תרגום מובנה, יופיע בעוד 10 שניות[/COLOR][/B]' + ' | ' + self.label_info_text)
            else:
                self.label_info.setLabel('[B][COLOR springgreen]מוכן[/COLOR][/B]' + ' | ' + self.label_info_text)
            self.last_sub_name_in_cache,self.last_sub_language_in_cache,self.all_subs=get_db_data(self.video_data)
            self.set_active_controls()
            from resources.modules import general
            general.show_msg="END"

        def click_c(self):
            self.close_window=True
            self.close()

        def set_active_controls(self):
            self.list.reset()
            rows = _build_rows(self.list_o, self.video_data, self.all_subs,
                               self.last_sub_name_in_cache, self.last_sub_language_in_cache,
                               two_line=False)
            self.list.addItems([r[0] for r in rows])

        def set_navigation(self):
            self.list.controlDown(self.button)
            self.list.controlRight(self.button)
            self.list.controlLeft(self.button)
            self.button.controlUp(self.list)
            self.button.controlDown(self.list)
            self.setFocus(self.list)

        def setAnimation(self, control):
            control.setAnimations([('WindowOpen', 'effect=fade start=0 end=100 time=100',),
                                    ('WindowClose', 'effect=fade start=100 end=0 time=100',)])

    window = MySubsClassic(title)
    window.doModal()
    del window


def MySubs(title,list,f_list,video_data,all_subs,last_sub_name_in_cache,last_sub_language_in_cache):
    """Subtitle picker entry point. Default = the new XML window (two-line rows,
    full release names). Setting classic_window=true -> the old pyxbmct window.
    Any problem opening the new window falls back to classic automatically."""
    _ensure_subtitle_fonts()
    if Addon.getSetting('classic_window') != 'true':
        try:
            _xml = 'SubsWindow.xml' if Addon.getSetting('window_style') != '1' else 'SubsWindowCenter.xml'
            while True:
                w = SubsXMLWindow(_xml, ADDON_PATH, 'Default', '1080i')
                w.setup(title, list, f_list, video_data, all_subs,
                        last_sub_name_in_cache, last_sub_language_in_cache)
                w.doModal()
                _style = getattr(w, '_open_style', False)
                del w
                if not _style:
                    return
                # picker closes so the MOVIE + subtitle are visible while
                # styling live; when the panel closes the picker comes back
                open_style_window()
        except Exception as e:
            log.warning('XML window failed (%s) -> falling back to classic' % e)
    _classic_window(title, list, f_list, video_data, all_subs,
                    last_sub_name_in_cache, last_sub_language_in_cache)
