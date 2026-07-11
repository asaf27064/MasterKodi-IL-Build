# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Beautiful Dialog-Based UI
"""
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
import os
import sys
import json

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_PATH = ADDON.getAddonInfo('path')
ADDON_VERSION = ADDON.getAddonInfo('version')
ADDON_DATA = xbmcvfs.translatePath(f'special://userdata/addon_data/{ADDON_ID}')

# Colors
COLOR_HEADER = 'FF00BFFF'  # Deep Sky Blue
COLOR_SUCCESS = 'FF00FF00'  # Lime
COLOR_WARNING = 'FFFFFF00'  # Yellow
COLOR_ERROR = 'FFFF0000'   # Red
COLOR_INFO = 'FF87CEEB'    # Sky Blue
COLOR_GOLD = 'FFFFD700'    # Gold
COLOR_WHITE = 'FFFFFFFF'
COLOR_GRAY = 'FFA0A0A0'


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] {msg}', level)


def color(text, hex_color):
    """Wrap text with color"""
    return f'[COLOR {hex_color}]{text}[/COLOR]'


def bold(text):
    """Make text bold"""
    return f'[B]{text}[/B]'


def menu_item(label, label2='', icon='DefaultAddon.png'):
    """A menu row as a (label, label2, icon) tuple. Consumed by wizard_select
    (custom window) or converted to a ListItem for the fallback select."""
    return (label, label2, icon)


_WIZ_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))


def _strip_markup(s):
    """Drop Kodi [COLOR]/[B]/[I] tags for the big detail panel + fallbacks."""
    import re
    return re.sub(r'\[/?(COLOR[^\]]*|B|I|UPPERCASE|LOWERCASE)\]', '', s or '')


class WizardMenu(xbmcgui.WindowXMLDialog):
    """Unified MasterKodi menu: RTL list on the right, a big branded detail
    panel (icon + title + description) on the left. Returns the chosen index
    via .selection (-1 = cancelled)."""

    def __init__(self, *args, **kwargs):
        self.rows = kwargs.pop('rows', [])       # [(label, label2, icon), ...]
        self.heading = kwargs.pop('heading', '')
        self.selection = -1
        super().__init__(*args)

    @staticmethod
    def pick(heading, rows):
        d = WizardMenu('wizard-menu.xml', _WIZ_PATH, 'Default', '1080i',
                       rows=rows, heading=heading)
        d.doModal()
        sel = d.selection
        del d
        return sel

    def onInit(self):
        self.setProperty('heading', self.heading)
        lst = self.getControl(100)
        lst.reset()
        for label, label2, icon in self.rows:
            li = xbmcgui.ListItem(label)
            li.setLabel2(label2)
            li.setArt({'icon': icon, 'thumb': icon})
            lst.addItem(li)
        self.setFocusId(100)

    def onClick(self, control_id):
        if control_id == 100:
            self.selection = self.getControl(100).getSelectedPosition()
            self.close()

    def onAction(self, action):
        if action.getId() in (9, 10, 92):  # BACK / PREVIOUS_MENU / NAV_BACK
            self.selection = -1
            self.close()


def wizard_select(header, rows):
    """Show a menu via the custom WizardMenu window. `rows` may be
    (label, label2, icon) tuples (from menu_item) or plain strings. Falls back
    to a useDetails dialog.select if the window can't load."""
    norm = []
    for r in rows:
        if isinstance(r, (tuple, list)):
            label, label2, icon = (list(r) + ['', 'DefaultAddon.png'])[:3]
        else:
            label, label2, icon = _strip_markup(r), '', 'DefaultAddon.png'
        norm.append((_strip_markup(label), _strip_markup(label2), icon))
    try:
        return WizardMenu.pick(_strip_markup(header), norm)
    except Exception as e:
        log(f"WizardMenu failed ({e}); using fallback select", xbmc.LOGWARNING)
        li_list = []
        for label, label2, icon in norm:
            li = xbmcgui.ListItem(label)
            li.setLabel2(label2)
            li.setArt({'icon': icon, 'thumb': icon})
            li_list.append(li)
        return xbmcgui.Dialog().select(_strip_markup(header), li_list, useDetails=True)


# ============================================
# STATUS HELPERS
# ============================================
def get_gearsai_status():
    """AI Subs (service.subtitles.gearsai) install status."""
    try:
        from resources.libs.installer import GearsaiInstaller
        ai = GearsaiInstaller()
        if not ai.is_installed():
            return {'addon': False, 'hebrew': False, 'version': None}
        v = ai.get_installed_version()
        return {'addon': True, 'hebrew': True, 'version': (None if v in ('0', '') else v)}
    except Exception as e:
        log(f"Error getting AI Subs status: {e}")
        return {'addon': False, 'hebrew': False, 'version': None}


def format_status(status):
    """Format status for display"""
    if not status['addon']:
        return color('לא מותקן', COLOR_GRAY)
    elif not status['hebrew']:
        return color('עברית לא מותקנת', COLOR_WARNING)
    else:
        ver = status['version'] if status['version'] and status['version'] != 'installed' else ''
        if ver:
            return color(f'מותקן (v{ver})', COLOR_SUCCESS)
        else:
            return color('מותקן', COLOR_SUCCESS)


def build_status_menu():
    """Show every installed addon + its version, checked against the manifest.
    A read-only view of the build's actual state (a new capability of the
    manifest model)."""
    import os, re
    dialog = xbmcgui.Dialog()
    addons_path = xbmcvfs.translatePath('special://home/addons/')

    def _ver(aid):
        try:
            with open(os.path.join(addons_path, aid, 'addon.xml'), encoding='utf-8', errors='replace') as fh:
                m = re.search(r'<addon[^>]*version="([^"]+)"', fh.read())
            return m.group(1) if m else '?'
        except Exception:
            return None

    try:
        from resources.libs import modular_update
        manifest = modular_update.fetch_manifest()
        m_addons = manifest.get('addons', [])
    except Exception as e:
        dialog.ok(ADDON_NAME, f"{color('לא ניתן לטעון מאניפסט:', COLOR_ERROR)}\n{e}")
        return

    rows = []
    n_ok = n_missing = n_old = 0
    for a in sorted(m_addons, key=lambda x: (x.get('channel', 'core'), x['id'])):
        installed = _ver(a['id'])
        if installed is None:
            if a.get('channel') == 'optional':
                continue  # optional not installed -> not relevant here
            state = color('חסר', COLOR_ERROR); n_missing += 1
        elif installed == a['version']:
            state = color('מעודכן', COLOR_SUCCESS); n_ok += 1
        else:
            state = color(f'{installed} -> {a["version"]}', COLOR_WARNING); n_old += 1
        tag = ' [Skin]' if a.get('channel') == 'optional' else ''
        rows.append(menu_item(f"{a['id']}{tag}", f"v{a.get('version','?')}  ·  {state}", 'DefaultAddonInfoProvider.png'))

    header = (f"{color('סטטוס הבילד', COLOR_GOLD)}  ·  "
              f"{color(str(n_ok)+' מעודכנים', COLOR_SUCCESS)}"
              + (f" · {color(str(n_old)+' לעדכון', COLOR_WARNING)}" if n_old else '')
              + (f" · {color(str(n_missing)+' חסרים', COLOR_ERROR)}" if n_missing else ''))
    wizard_select(header, rows)


# ============================================
# MAIN MENU
# ============================================
def main_menu():
    """Show beautiful main menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        gearsai_status = get_gearsai_status()

        # Menu reflects the manifest model: everything (Gears + Hebrew, skins,
        # AI subs) is pre-merged and delivered/updated by "בדוק עדכונים". The old
        # per-addon "reinstall Hebrew overlay" flows (Gears/Skin/POV) are gone --
        # they downloaded the legacy overlay zips and no longer apply.
        items, handlers = [], []
        items.append(menu_item('בדוק עדכונים', 'עדכון כל הבילד · אימות SHA256 · רק מה שהשתנה', 'DefaultAddonsUpdates.png'))
        handlers.append(check_updates_now)
        items.append(menu_item('התקנה / החלפת בילד', 'התקן בילד · בחר סקין (Estuary · Nimbus · Arctic Fuse)', 'DefaultAddonProgram.png'))
        handlers.append(build_menu)
        items.append(menu_item('סטטוס הבילד', 'מה מותקן וגרסאות · מהמאניפסט', 'DefaultAddonInfoProvider.png'))
        handlers.append(build_status_menu)
        items.append(menu_item('כתוביות AI (Gemini)', format_status(gearsai_status), 'DefaultAddonSubtitles.png'))
        handlers.append(gearsai_menu)
        items.append(menu_item('תחזוקה', 'ניקוי מטמון · חבילות · תמונות · OLED', 'DefaultAddonService.png'))
        handlers.append(maintenance_menu)
        items.append(menu_item('גיבוי ושחזור', 'מפתח Gemini · דבריד · הגדרות', 'DefaultHardDisk.png'))
        handlers.append(backup_menu)
        items.append(menu_item('הגדרות האשף', 'עדכון אוטומטי · השהיות · אפשרויות', 'DefaultAddonProgram.png'))
        handlers.append(lambda: ADDON.openSettings())

        header = f"{color('MasterKodi IL Wizard', COLOR_GOLD)} v{ADDON_VERSION}"
        selection = wizard_select(header, items)
        if selection == -1:
            break
        handlers[selection]()


# ============================================
# POV MENU
# ============================================






# ============================================
# GEARS + AI SUBS MENUS
# ============================================


def gearsai_menu():
    """AI Subs (gearsai) submenu - settings / info / install."""
    dialog = xbmcgui.Dialog()
    status = get_gearsai_status()
    if not status['addon']:
        if dialog.yesno('כתוביות AI',
                        f"{color('כתוביות AI (gearsai) לא מותקנות.', COLOR_WARNING)}\n\nלהתקין עכשיו?"):
            install_gearsai()
        return
    items = [
        menu_item('הגדרות כתוביות AI', 'מפתח Gemini · מודל · מאגר קהילתי', 'DefaultAddonProgram.png'),
        menu_item('מידע', f"v{status['version']}", 'DefaultAddonInfoProvider.png'),
        menu_item('חזרה', '', 'DefaultFolderBack.png'),
    ]
    sel = wizard_select(f"{color('כתוביות AI (Gemini)', COLOR_HEADER)} - {format_status(status)}", items)
    if sel == 0:
        try:
            xbmcaddon.Addon('service.subtitles.gearsai').openSettings()
        except Exception as e:
            dialog.ok('שגיאה', str(e))
    elif sel == 1:
        dialog.textviewer('כתוביות AI - מידע',
                          f"[B]גרסה:[/B] {status['version']}\n\n"
                          "כתוביות עברית אוטומטיות עם תרגום AI (Gemini) ומאגר קהילתי.\n"
                          "מפתח Gemini ומודל נקבעים ב'הגדרות כתוביות AI'.")


def install_gearsai():
    """Install/enable the AI Subs add-on."""
    dialog = xbmcgui.Dialog()
    progress = xbmcgui.DialogProgress()
    progress.create('כתוביות AI', 'מתקין...')
    try:
        from resources.libs.installer import GearsaiInstaller
        ok = GearsaiInstaller().install(progress_callback=lambda m, p: progress.update(p, m))
        progress.close()
        dialog.ok('כתוביות AI',
                  color('הותקנו בהצלחה!', COLOR_SUCCESS) if ok else color('ההתקנה נכשלה!', COLOR_ERROR))
    except Exception as e:
        progress.close()
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


# ============================================
# SKIN MENU
# ============================================






# ============================================
# BUILD MENU
# ============================================
def build_menu():
    """Build installation menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        items = [
            menu_item('התקנת בילד', 'התקנה נקייה · בחירת סקין (Estuary · Nimbus · Arctic Fuse) · מוחק את הקיים', 'DefaultAddonProgram.png'),
            menu_item('עדכון בילד', 'משדרג את הבילד הנוכחי בלי למחוק הגדרות ומפתחות', 'DefaultAddonsUpdates.png'),
            menu_item('מידע על בילד נוכחי', 'שם · גרסה · סקין מותקן', 'DefaultAddonInfoProvider.png'),
        ]

        selection = wizard_select(color('התקנה / עדכון בילד', COLOR_HEADER), items)

        if selection == -1:
            return
        elif selection == 0:
            install_build()
        elif selection == 1:
            update_build()
        elif selection == 2:
            show_build_info()


def install_build():
    """Install a build - uses builds_menu which handles skin selection"""
    try:
        from resources.libs.builds import builds_menu
        builds_menu()
    except Exception as e:
        dialog = xbmcgui.Dialog()
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def update_build():
    """Update the current build via the manifest (same engine as 'check updates').

    In the manifest model there is no separate 'build update' -- every addon
    (Gears + its baked Hebrew, skins, AI subs, wizard) is pulled/verified from
    the manifest. So this just runs the modular updater."""
    try:
        from resources.libs import modular_update
        modular_update.check_and_prompt()
    except Exception as e:
        xbmcgui.Dialog().ok(ADDON_NAME, f"{color('שגיאה בעדכון:', COLOR_ERROR)}\n{e}")


def show_build_info():
    """Show current build info"""
    dialog = xbmcgui.Dialog()
    
    build_name = ADDON.getSetting('buildname') or 'לא מותקן'
    build_version = ADDON.getSetting('buildversion') or 'N/A'
    
    dialog.textviewer(
        'מידע על הבילד',
        f"[B]בילד נוכחי:[/B] {build_name}\n"
        f"[B]גירסה:[/B] {build_version}\n"
    )


# ============================================
# MAINTENANCE MENU
# ============================================

# ============================================
# OLED SETTINGS
# ============================================
def apply_oled_to_guisettings():
    """Apply OLED settings to guisettings.xml"""
    try:
        guisettings_path = os.path.join(
            xbmcvfs.translatePath('special://home/'),
            'userdata',
            'guisettings.xml'
        )
        
        if not os.path.exists(guisettings_path):
            return False
        
        with open(guisettings_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        import re
        oled_settings = {
            'screensaver.mode': 'screensaver.xbmc.builtin.black',
            'screensaver.time': '1',
            'screensaver.disableforaudio': 'false',
            'screensaver.usedimonpause': 'true'
        }
        
        for setting_id, setting_value in oled_settings.items():
            pattern = rf'<setting id="{setting_id}"[^>]*>[^<]*</setting>'
            replacement = f'<setting id="{setting_id}">{setting_value}</setting>'
            
            if re.search(pattern, content):
                content = re.sub(pattern, replacement, content)
            else:
                content = content.replace('</settings>', f'    {replacement}\n</settings>')
        
        with open(guisettings_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return True
    except Exception as e:
        log(f"Error applying OLED: {e}")
        return False


def oled_menu():
    """OLED settings menu"""
    dialog = xbmcgui.Dialog()
    
    result = dialog.yesno(
        color('הגדרות OLED', COLOR_HEADER),
        f'{bold("יש לך מסך OLED?")}\n\n'
        f'אם כן, נגדיר הגדרות להגנה על המסך:\n'
        f'- Screensaver שחור (לא אנימציה)\n'
        f'- הפעלה אחרי דקה\n'
        f'- עמעום בזמן השהיה',
        yeslabel='כן, יש לי OLED',
        nolabel='לא'
    )
    
    if result:
        if apply_oled_to_guisettings():
            dialog.ok('הצלחה', f'{color("הגדרות OLED הוחלו!", COLOR_SUCCESS)}\n\n'
                     f'- Screensaver: Black\n'
                     f'- זמן המתנה: דקה\n'
                     f'- עמעום בהשהיה: פעיל\n\n'
                     f'{color("יש להפעיל מחדש את Kodi", COLOR_WARNING)}')
        else:
            dialog.ok('שגיאה', 'לא הצלחתי להחיל את ההגדרות')


def maintenance_menu():
    """Maintenance menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        menu_items = [
            menu_item('ניקוי Cache', 'מחיקת מטמון זמני להאצת Kodi ופינוי מקום', 'DefaultAddonService.png'),
            menu_item('ניקוי Packages', 'מחיקת קובצי התקנה שמורים (packages)', 'DefaultAddonService.png'),
            menu_item('ניקוי Thumbnails', 'מחיקת תמונות ממוזערות שמורות', 'DefaultAddonService.png'),
            menu_item('ניקוי הכל', 'Cache · Packages · Thumbnails יחד', 'DefaultAddonService.png'),
            menu_item('סגירת Kodi', 'סגירה מלאה (לרענון אחרי שינויים)', 'DefaultAddonService.png'),
            menu_item('הגדרות OLED', 'חיסכון בשחיקת מסך · בהירות · הגנת פיקסלים', 'DefaultAddonPVRClient.png'),
        ]

        selection = wizard_select(color('תחזוקה', COLOR_HEADER), menu_items)

        if selection == -1:
            return
        elif selection == 0:
            clear_cache()
        elif selection == 1:
            clear_packages()
        elif selection == 2:
            clear_thumbnails()
        elif selection == 3:
            clear_all()
        elif selection == 4:
            force_close()
        elif selection == 5:
            oled_menu()


def clear_cache():
    """Clear cache"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno('ניקוי Cache', 'האם לנקות את ה-Cache?'):
        return
    
    try:
        from resources.libs.maintenance import clear_cache as do_clear
        cleared = do_clear()
        dialog.ok('הצלחה', f'{color("Cache נוקה!", COLOR_SUCCESS)}\n\nנמחקו: {cleared}')
    except Exception as e:
        dialog.ok('שגיאה', str(e))


def clear_packages():
    """Clear packages"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno('ניקוי Packages', 'האם לנקות את ה-Packages?'):
        return
    
    try:
        from resources.libs.maintenance import clear_packages as do_clear
        cleared = do_clear()
        dialog.ok('הצלחה', f'{color("Packages נוקה!", COLOR_SUCCESS)}\n\nנמחקו: {cleared}')
    except Exception as e:
        dialog.ok('שגיאה', str(e))


def clear_thumbnails():
    """Clear thumbnails"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno('ניקוי Thumbnails', 'האם לנקות את ה-Thumbnails?\n\nפעולה זו תמחק את כל התמונות השמורות.'):
        return
    
    try:
        from resources.libs.maintenance import clear_thumbnails as do_clear
        cleared = do_clear()
        dialog.ok('הצלחה', f'{color("Thumbnails נוקה!", COLOR_SUCCESS)}\n\nנמחקו: {cleared}')
    except Exception as e:
        dialog.ok('שגיאה', str(e))


def clear_all():
    """Clear everything"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno(
        'ניקוי הכל',
        f'{color("אזהרה!", COLOR_WARNING)}\n\n'
        'פעולה זו תנקה:\n'
        '- Cache\n'
        '- Packages\n'
        '- Thumbnails\n\n'
        'האם להמשיך?'
    ):
        return
    
    try:
        from resources.libs.maintenance import clear_cache, clear_packages, clear_thumbnails
        
        progress = xbmcgui.DialogProgress()
        progress.create('ניקוי', 'מנקה...')
        
        progress.update(0, 'מנקה Cache...')
        cache = clear_cache()
        
        progress.update(33, 'מנקה Packages...')
        packages = clear_packages()
        
        progress.update(66, 'מנקה Thumbnails...')
        thumbs = clear_thumbnails()
        
        progress.close()
        
        dialog.ok(
            'הצלחה',
            f'{color("הכל נוקה!", COLOR_SUCCESS)}\n\n'
            f'Cache: {cache}\n'
            f'Packages: {packages}\n'
            f'Thumbnails: {thumbs}'
        )
    except Exception as e:
        dialog.ok('שגיאה', str(e))


def force_close():
    """Force close Kodi"""
    dialog = xbmcgui.Dialog()
    
    if dialog.yesno('סגירת Kodi', 'האם לסגור את Kodi?'):
        xbmc.executebuiltin('Quit')


# ============================================
# BACKUP MENU
# ============================================
def backup_menu():
    """Backup & Restore menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        items = [
            menu_item('יצירת גיבוי', 'מהיר (מפתח Gemini · טוקני דבריד · הגדרות · מועדפים) או מלא', 'DefaultAddonService.png'),
            menu_item('שחזור מגיבוי', 'שחזר מפתחות והגדרות אחרי התקנה מחדש', 'DefaultAddonsUpdates.png'),
            menu_item('מחיקת גיבויים', 'ניקוי גיבויים ישנים ופינוי מקום', 'DefaultAddonService.png'),
        ]

        selection = wizard_select(color('גיבוי ושחזור', COLOR_HEADER), items)

        if selection == -1:
            return
        elif selection == 0:
            create_backup()
        elif selection == 1:
            restore_backup()
        elif selection == 2:
            delete_backups()


def create_backup():
    """Create backup (Quick settings/keys or Full userdata)."""
    try:
        from resources.libs import backup
        backup.create_flow()
    except Exception as e:
        log(f"create_backup error: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok('שגיאה', f"{color('הגיבוי נכשל:', COLOR_ERROR)}\n{str(e)}")


def restore_backup():
    """Restore from a saved backup."""
    try:
        from resources.libs import backup
        backup.restore_flow()
    except Exception as e:
        log(f"restore_backup error: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok('שגיאה', f"{color('השחזור נכשל:', COLOR_ERROR)}\n{str(e)}")


def delete_backups():
    """Delete saved backups."""
    try:
        from resources.libs import backup
        backup.manage_flow()
    except Exception as e:
        log(f"delete_backups error: {e}", xbmc.LOGERROR)
        xbmcgui.Dialog().ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def check_updates_now():
    """Manually trigger the manifest-driven update check.

    The whole build is now delivered from the MasterKodi-IL-Build manifest:
    every addon carries a version + sha256, and the updater fetches only what
    changed, verifies the hash, and installs it. This one button updates Gears,
    the AI subs, skins, the wizard itself -- everything -- in one pass."""
    dialog = xbmcgui.Dialog()
    try:
        from resources.libs import modular_update
        modular_update.check_and_prompt()
    except BaseException as e:
        log(f"check_updates_now error: {e}", xbmc.LOGERROR)
        dialog.ok(ADDON_NAME, f"{color('בדיקת העדכונים נכשלה:', COLOR_ERROR)}\n{str(e)}")


# ============================================
# PARAMETER PARSING
# ============================================
def parse_params():
    """Parse addon parameters from sys.argv"""
    params = {}
    try:
        log(f"sys.argv = {sys.argv}")
        
        if len(sys.argv) > 2:
            param_string = sys.argv[2]
        elif len(sys.argv) > 1:
            param_string = sys.argv[1]
        else:
            return params
        
        if param_string:
            if param_string.startswith('?'):
                param_string = param_string[1:]
            
            pairs = param_string.split('&')
            for pair in pairs:
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    params[key] = value
                elif pair:
                    params[pair] = 'true'
        
        log(f"Parsed params: {params}")
        
    except Exception as e:
        log(f"Error parsing params: {e}", xbmc.LOGERROR)
    
    return params


# ============================================
# ENTRY POINT
# ============================================
if __name__ == '__main__':
    log(f"Wizard started - v{ADDON_VERSION}")
    
    params = parse_params()
    mode = params.get('mode', '')
    
    if mode == 'builds':
        log("Opening Build Installation directly")
        build_menu()
    elif mode == 'gearsai':
        gearsai_menu()
    elif mode == 'maintenance':
        maintenance_menu()
    elif mode == 'backup':
        backup_menu()
    elif mode == 'check_updates':
        check_updates_now()
    elif mode == 'status':
        build_status_menu()
    else:
        main_menu()
    
    log("Wizard closed")
