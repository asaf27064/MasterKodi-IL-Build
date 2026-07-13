import xbmc,xbmcaddon,xbmcgui,json,os,shutil
import re as _re
Addon=xbmcaddon.Addon()
ADDON_PATH=Addon.getAddonInfo('path')
from resources.modules import log
from resources.modules.engine import download_sub
from resources.modules.general import CachedSubFolder
from urllib.parse import parse_qsl
from resources.modules.general import user_dataDir,MySubFolder,save_file_name,get_db_data,Thread,TransFolder
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


def _clean_name(items, video_data, strip_title=True, max_len=0):
    """Release name without colour codes / site prefix. Optionally strips the
    playing title's redundant prefix and middle-truncates to max_len."""
    raw = _re.sub(r'\[/?COLOR[^\]]*\]', '', items[1]).strip()
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
    return raw


def _build_rows(list_o, video_data, all_subs, last_name, last_lang, two_line):
    """Format the subtitle rows. Returns a list of (line1, line2) tuples --
    line2 is '' in single-line (classic) mode where line1 carries a shortened
    name instead."""
    rows = []
    current_marked = False
    for items in list_o:
        try:
            val = all_subs.get(items[8])
        except Exception:
            val = None
        try:
            pct = int(items[5])
        except Exception:
            pct = 0
        mcolor = 'springgreen' if pct >= 85 else ('gold' if pct >= 60 else 'darkorange')
        lang = "עברית" if "Hebrew" in items[0] else _LANG_HE.get(items[0], items[0])

        is_current = ((not current_marked)
                      and (last_name == items[8]) and (last_lang == items[0]))
        is_downloaded = bool(val and items[0] in val)
        if is_current:
            current_marked = True
            status = "[COLOR gold][B][ נוכחית ][/B][/COLOR]  "
        elif is_downloaded:
            status = "[COLOR springgreen][B][ ירדה ][/B][/COLOR]  "
        else:
            status = ""

        # Machine-translated to Hebrew? say so + from where (.origin marker).
        trans_file = os.path.join(TransFolder, items[8])
        if os.path.exists(trans_file):
            origin = ''
            try:
                with open(trans_file + '.origin', encoding='utf-8') as f_o:
                    origin = f_o.read().strip()
            except Exception:
                pass
            tag = 'תורגם' + (' · ' + origin if origin else ' לעברית')
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
            name1 = _clean_name(items, video_data, strip_title=True, max_len=64)
            line1 = ("{st}[COLOR deepskyblue][B]{lang}[/B][/COLOR]"
                     "  [COLOR {mc}][B]{pct}%[/B][/COLOR]"
                     "  [COLOR {bc}][B]{badge}[/B][/COLOR]  {name}").format(
                         st=status, lang=lang, mc=mcolor, pct=pct,
                         bc=bcolor, badge=badge, name=name1)
            line2 = _clean_name(items, video_data, strip_title=False)
            rows.append((line1, line2))
        else:
            name = _clean_name(items, video_data, strip_title=True, max_len=58)
            line1 = ("{st}[COLOR deepskyblue][B]{lang}[/B][/COLOR]"
                     "  [COLOR {mc}][B]{pct}%[/B][/COLOR]"
                     "  [COLOR {bc}][B]{badge}[/B][/COLOR]  {name}").format(
                         st=status, lang=lang, mc=mcolor, pct=pct,
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
        try:
            sub_file = download_sub(source, download_data, MySubFolder, language, filename)
        finally:
            general.ai_manual = False
        xbmc.sleep(100)
        if sub_file == 'EmbeddedSubSelected':
            save_file_name(unque(filename), language, video_data)
            return 'embedded', filename, language
        if sub_file == 'FaultSubException' or not sub_file or sub_file == '0':
            return 'fault', filename, language
        xbmc.Player().setSubtitles(sub_file)
        log.warning('My Window Sub result:' + str(sub_file))
        save_file_name(filename, language, video_data)
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

    def onInit(self):
        try:
            self.getControl(102).setLabel('[B]{0}[/B]'.format(self.title))
            self.getControl(103).setLabel(self.header_text)
            self.getControl(104).setLabel(
                '[COLOR gold][B]{0}[/B][/COLOR] כתוביות · מסודר לפי אחוז התאמה'.format(len(self.list_o)))
            self._fill_list()
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
        self._has_sync_row = False
        try:
            cur = xbmcgui.Window(10000).getProperty('gearsai.current_heb_sub')
            self._has_sync_row = bool(cur)
        except Exception:
            pass
        if self._has_sync_row:
            items.append(xbmcgui.ListItem(
                label='[COLOR springgreen][B]סנכרון[/B][/COLOR]  ·  סנכרן את הכתובית שמוצגת כעת',
                label2='מיישר את התזמון לפי הקובץ המתנגן (אנגלית מוטמעת / חיצונית תואמת)'))
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
            try:
                if ('מתרגם' in msg) or ('MasterKodi' in msg):
                    self.getControl(103).setLabel('[COLOR gold][B]{0}[/B][/COLOR]'.format(msg))
                    showing = True
                elif showing and (msg == 'END' or msg == ''):
                    self.getControl(103).setLabel(self.header_text)
                    showing = False
            except Exception:
                pass
            xbmc.sleep(500)

    def _set_status(self, text, color='gold'):
        try:
            self.getControl(103).setLabel('[COLOR {0}][B]{1}[/B][/COLOR]'.format(color, text))
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == 101:
            self.close_window = True
            self.close()
            return
        if control_id != 100:
            return
        idx = self.getControl(100).getSelectedPosition()
        # MASTERKODI: pinned sync row sits at index 0 when present.
        if getattr(self, '_has_sync_row', False):
            if idx == 0:
                self._run_sync()
                return
            idx -= 1
        if idx < 0 or idx >= len(self.full_list):
            return
        self._set_status('מוריד…')
        status, filename, language = _download_row(self.full_list[idx], self.video_data)
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
        self._fill_list(keep_pos=True)
        from resources.modules import general
        general.show_msg = "END"

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
    if Addon.getSetting('classic_window') != 'true':
        try:
            w = SubsXMLWindow('SubsWindow.xml', ADDON_PATH, 'Default', '1080i')
            w.setup(title, list, f_list, video_data, all_subs,
                    last_sub_name_in_cache, last_sub_language_in_cache)
            w.doModal()
            del w
            return
        except Exception as e:
            log.warning('XML window failed (%s) -> falling back to classic' % e)
    _classic_window(title, list, f_list, video_data, all_subs,
                    last_sub_name_in_cache, last_sub_language_in_cache)
