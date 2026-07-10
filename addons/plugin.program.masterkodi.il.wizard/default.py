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
    """A rich ListItem (icon + title + subtitle) for useDetails=True selects.
    Icons are standard Kodi default textures, so they render on any skin."""
    li = xbmcgui.ListItem(label)
    li.setLabel2(label2)
    li.setArt({'icon': icon, 'thumb': icon})
    return li


# ============================================
# STATUS HELPERS
# ============================================
def get_pov_status():
    """Get POV Hebrew installation status"""
    try:
        from resources.libs.installer import POVHebrewInstaller
        installer = POVHebrewInstaller()
        
        if not installer.is_pov_installed():
            return {'addon': False, 'hebrew': False, 'version': None}
        
        hebrew_installed = installer.is_installed()
        version = installer.get_installed_version() if hebrew_installed else None
        
        return {'addon': True, 'hebrew': hebrew_installed, 'version': version}
    except Exception as e:
        log(f"Error getting POV status: {e}")
        return {'addon': False, 'hebrew': False, 'version': None}


def get_gears_status():
    """Gears base + Hebrew overlay status (this build's main video add-on)."""
    try:
        from resources.libs.installer import GearsHebrewInstaller
        g = GearsHebrewInstaller()
        if not g.is_gears_installed():
            return {'addon': False, 'hebrew': False, 'version': None, 'base': None}
        base = None
        try:
            base = xbmcaddon.Addon('plugin.video.gears').getAddonInfo('version')
        except Exception:
            pass
        hebrew = g.is_installed()
        version = g.get_installed_version() if hebrew else None
        if version in ('0', ''):
            version = None
        return {'addon': True, 'hebrew': hebrew, 'version': version, 'base': base}
    except Exception as e:
        log(f"Error getting Gears status: {e}")
        return {'addon': False, 'hebrew': False, 'version': None, 'base': None}


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


def get_skin_status():
    """Get Skin Hebrew installation status"""
    try:
        from resources.libs.installer import ArcticFuseHebrewInstaller
        installer = ArcticFuseHebrewInstaller()
        
        if not installer.is_skin_installed():
            return {'addon': False, 'hebrew': False, 'version': None}
        
        hebrew_installed = installer.is_installed()
        version = installer.get_installed_version() if hebrew_installed else None
        
        return {'addon': True, 'hebrew': hebrew_installed, 'version': version}
    except Exception as e:
        log(f"Error getting Skin status: {e}")
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


# ============================================
# MAIN MENU
# ============================================
def main_menu():
    """Show beautiful main menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        gears_status = get_gears_status()
        gearsai_status = get_gearsai_status()
        skin_status = get_skin_status()
        pov_status = get_pov_status()

        # Build rows + a parallel handler list, so optional rows (POV only when
        # actually installed) never desync the click indices.
        gears_sub = (f"Gears {gears_status['base']}" if gears_status.get('base') else '') + \
                    f"  ·  {format_status(gears_status)}"
        items, handlers = [], []
        items.append(menu_item('Gears + עברית', gears_sub.strip(' ·'), 'DefaultAddonSubtitles.png'))
        handlers.append(gears_menu)
        items.append(menu_item('כתוביות AI (Gemini)', format_status(gearsai_status), 'DefaultAddonSubtitles.png'))
        handlers.append(gearsai_menu)
        items.append(menu_item('עברית לסקין (Arctic Fuse)', format_status(skin_status), 'DefaultAddonSkin.png'))
        handlers.append(skin_menu)
        if pov_status['addon']:  # legacy POV — only shown when it's actually installed
            items.append(menu_item('POV עברית', format_status(pov_status), 'DefaultAddonSubtitles.png'))
            handlers.append(pov_menu)
        items.append(menu_item('התקנת בילד', 'התקנה ועדכון בילד', 'DefaultAddonProgram.png'))
        handlers.append(build_menu)
        items.append(menu_item('תחזוקה', 'ניקוי מטמון · חבילות · תמונות · OLED', 'DefaultAddonService.png'))
        handlers.append(maintenance_menu)
        items.append(menu_item('גיבוי ושחזור', 'מפתח Gemini · דבריד · הגדרות', 'DefaultHardDisk.png'))
        handlers.append(backup_menu)
        items.append(menu_item('בדוק עדכונים עכשיו', 'Gears · כתוביות AI · סקין', 'DefaultAddonsUpdates.png'))
        handlers.append(check_updates_now)
        items.append(menu_item('הגדרות', 'הגדרות האשף', 'DefaultAddonProgram.png'))
        handlers.append(lambda: ADDON.openSettings())

        header = f"{color('MasterKodi IL Wizard', COLOR_GOLD)} v{ADDON_VERSION}"
        selection = dialog.select(header, items, useDetails=True)
        if selection == -1:
            break
        handlers[selection]()


# ============================================
# POV MENU
# ============================================
def pov_menu():
    """POV Hebrew submenu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        status = get_pov_status()
        
        if not status['addon']:
            dialog.ok(
                'POV',
                f"{color('POV לא מותקן!', COLOR_ERROR)}\n\n"
                "יש להתקין את POV לפני התקנת קבצי העברית."
            )
            return
        
        menu_items = []
        
        if status['hebrew']:
            ver_text = f" (v{status['version']})" if status['version'] and status['version'] != 'installed' else ''
            menu_items = [
                f"{bold('עדכון קבצי עברית')}",
                f"{bold('הסרת קבצי עברית')}",
                f"{bold('מידע')}{ver_text}",
            ]
        else:
            menu_items = [
                f"{bold('התקנת קבצי עברית')}",
            ]
        
        menu_items.append(f"{color('חזרה', COLOR_GRAY)}")
        
        selection = dialog.select(
            f"{color('POV Hebrew', COLOR_HEADER)} - {format_status(status)}",
            menu_items
        )
        
        if selection == -1 or menu_items[selection].startswith('[COLOR'):
            return
        
        if status['hebrew']:
            if selection == 0:  # Update
                install_pov_hebrew()
            elif selection == 1:  # Uninstall
                uninstall_pov_hebrew()
            elif selection == 2:  # Info
                show_pov_info()
        else:
            if selection == 0:  # Install
                install_pov_hebrew()


def install_pov_hebrew():
    """Install POV Hebrew files"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno(
        'התקנת עברית ל-POV',
        f"{bold('האם להתקין את קבצי העברית?')}\n\n"
        "הקבצים יורדו מ-GitHub ויותקנו אוטומטית."
    ):
        return
    
    progress = xbmcgui.DialogProgress()
    progress.create('התקנת עברית ל-POV', 'מתחיל...')
    
    try:
        from resources.libs.installer import POVHebrewInstaller
        installer = POVHebrewInstaller()
        
        def update_progress(msg, pct):
            progress.update(pct, msg)
        
        success = installer.install(progress_callback=update_progress)
        progress.close()
        
        if success:
            dialog.ok(
                'הצלחה!',
                f"{color('קבצי העברית הותקנו בהצלחה!', COLOR_SUCCESS)}\n\n"
                "מומלץ להפעיל מחדש את Kodi."
            )
            
            if dialog.yesno('הפעלה מחדש', 'האם להפעיל מחדש את Kodi עכשיו?'):
                xbmc.executebuiltin('Quit')
        else:
            dialog.ok('שגיאה', color('ההתקנה נכשלה!', COLOR_ERROR))
            
    except Exception as e:
        progress.close()
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def uninstall_pov_hebrew():
    """Uninstall POV Hebrew files"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno(
        'הסרת עברית מ-POV',
        f"{color('האם להסיר את קבצי העברית?', COLOR_WARNING)}\n\n"
        "הקבצים המקוריים ישוחזרו מהגיבוי."
    ):
        return
    
    try:
        from resources.libs.installer import POVHebrewInstaller
        installer = POVHebrewInstaller()
        
        success = installer.uninstall()
        
        if success:
            dialog.ok('הצלחה', color('קבצי העברית הוסרו!', COLOR_SUCCESS))
        else:
            dialog.ok('שגיאה', color('ההסרה נכשלה!', COLOR_ERROR))
            
    except Exception as e:
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def show_pov_info():
    """Show POV info"""
    dialog = xbmcgui.Dialog()
    status = get_pov_status()
    
    try:
        pov_addon = xbmcaddon.Addon('plugin.video.pov')
        pov_version = pov_addon.getAddonInfo('version')
    except:
        pov_version = 'לא ידוע'
    
    hebrew_ver = status['version'] if status['version'] and status['version'] != 'installed' else 'לא ידוע'
    
    dialog.textviewer(
        'POV - מידע',
        f"[B]גירסת POV:[/B] {pov_version}\n"
        f"[B]גירסת עברית:[/B] {hebrew_ver}\n"
        f"[B]סטטוס:[/B] {'מותקן' if status['hebrew'] else 'לא מותקן'}\n\n"
        "[B]קבצי עברית כוללים:[/B]\n"
        "- תמיכה בכתוביות עבריות אוטומטיות\n"
        "- התאמה לשירותי כתוביות ישראליים\n"
        "- Ktuvit, Wizdom, OpenSubtitles"
    )


# ============================================
# GEARS + AI SUBS MENUS
# ============================================
def gears_menu():
    """Gears (this build's main add-on) Hebrew submenu."""
    dialog = xbmcgui.Dialog()
    while True:
        status = get_gears_status()
        if not status['addon']:
            dialog.ok('Gears',
                      f"{color('Gears לא מותקן!', COLOR_ERROR)}\n\n"
                      "התקן את הבילד (Build Installation) תחילה.")
            return
        base = status.get('base') or '?'
        if status['hebrew']:
            ver = f" (v{status['version']})" if status['version'] else ''
            items = [
                menu_item('עדכון/התקנה מחדש של עברית', 'מחיל את ה-overlay העברי על Gears', 'DefaultAddonsUpdates.png'),
                menu_item(f'מידע{ver}', f'Gears בסיס v{base}', 'DefaultAddonInfoProvider.png'),
                menu_item('חזרה', '', 'DefaultFolderBack.png'),
            ]
            sel = dialog.select(f"{color('Gears + עברית', COLOR_HEADER)} - {format_status(status)}", items, useDetails=True)
            if sel in (-1, 2):
                return
            elif sel == 0:
                install_gears_hebrew()
            elif sel == 1:
                show_gears_info()
        else:
            items = [
                menu_item('התקנת קבצי עברית ל-Gears', f'Gears בסיס v{base} מותקן', 'DefaultAddonService.png'),
                menu_item('חזרה', '', 'DefaultFolderBack.png'),
            ]
            sel = dialog.select(f"{color('Gears', COLOR_HEADER)} - {format_status(status)}", items, useDetails=True)
            if sel in (-1, 1):
                return
            elif sel == 0:
                install_gears_hebrew()


def install_gears_hebrew():
    """Apply / update the Gears Hebrew overlay."""
    dialog = xbmcgui.Dialog()
    if not dialog.yesno('עברית ל-Gears',
                        f"{bold('להתקין/לעדכן את קבצי העברית ל-Gears?')}\n\n"
                        "הקבצים יורדו מ-GitHub ויוחלו על Gears."):
        return
    progress = xbmcgui.DialogProgress()
    progress.create('עברית ל-Gears', 'מתחיל...')
    try:
        from resources.libs.installer import GearsHebrewInstaller
        ok = GearsHebrewInstaller().install_hebrew_files(progress_callback=lambda m, p: progress.update(p, m))
        progress.close()
        if ok:
            if dialog.yesno('הצלחה!', f"{color('קבצי העברית הותקנו!', COLOR_SUCCESS)}\n\nלהפעיל מחדש את Kodi עכשיו?"):
                xbmc.executebuiltin('Quit')
        else:
            dialog.ok('שגיאה', color('ההתקנה נכשלה!', COLOR_ERROR))
    except Exception as e:
        progress.close()
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def show_gears_info():
    """Show Gears + AI Subs versions."""
    status = get_gears_status()
    ai = get_gearsai_status()
    xbmcgui.Dialog().textviewer(
        'Gears - מידע',
        f"[B]Gears (בסיס):[/B] {status.get('base') or 'לא ידוע'}\n"
        f"[B]עברית (overlay):[/B] {status['version'] or 'לא מותקן'}\n"
        f"[B]כתוביות AI (gearsai):[/B] {ai['version'] or 'לא מותקן'}\n\n"
        "[B]כולל:[/B]\n"
        "- כתוביות עברית אוטומטיות (Ktuvit / Wizdom / OpenSubtitles)\n"
        "- תרגום AI לעברית עם Gemini\n"
        "- תקצירים ודירוגים בעברית"
    )


def gearsai_menu():
    """AI Subs (gearsai) submenu — settings / info / install."""
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
    sel = dialog.select(f"{color('כתוביות AI (Gemini)', COLOR_HEADER)} - {format_status(status)}", items, useDetails=True)
    if sel == 0:
        try:
            xbmcaddon.Addon('service.subtitles.gearsai').openSettings()
        except Exception as e:
            dialog.ok('שגיאה', str(e))
    elif sel == 1:
        show_gears_info()


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
def skin_menu():
    """Skin Hebrew submenu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        status = get_skin_status()
        
        if not status['addon']:
            dialog.ok(
                'Arctic Fuse Skin',
                f"{color('הסקין לא מותקן!', COLOR_ERROR)}\n\n"
                "יש להתקין את Arctic Fuse 3 לפני התקנת קבצי העברית."
            )
            return
        
        menu_items = []
        
        if status['hebrew']:
            ver_text = f" (v{status['version']})" if status['version'] and status['version'] != 'installed' else ''
            menu_items = [
                f"{bold('עדכון קבצי עברית')}",
                f"{bold('הסרת קבצי עברית')}",
                f"{bold('מידע')}{ver_text}",
            ]
        else:
            menu_items = [
                f"{bold('התקנת קבצי עברית')}",
            ]
        
        menu_items.append(f"{color('חזרה', COLOR_GRAY)}")
        
        selection = dialog.select(
            f"{color('Arctic Fuse Skin Hebrew', COLOR_HEADER)} - {format_status(status)}",
            menu_items
        )
        
        if selection == -1 or menu_items[selection].startswith('[COLOR FFA0A0A0'):
            return
        
        if status['hebrew']:
            if selection == 0:  # Update
                install_skin_hebrew()
            elif selection == 1:  # Uninstall
                uninstall_skin_hebrew()
            elif selection == 2:  # Info
                show_skin_info()
        else:
            if selection == 0:  # Install
                install_skin_hebrew()


def install_skin_hebrew():
    """Install Skin Hebrew files"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno(
        'התקנת עברית לסקין',
        f"{bold('האם להתקין את קבצי העברית?')}\n\n"
        "הקבצים יורדו מ-GitHub ויותקנו אוטומטית.\n"
        "כולל: פונטים עבריים ותרגום ממשק."
    ):
        return
    
    progress = xbmcgui.DialogProgress()
    progress.create('התקנת עברית לסקין', 'מתחיל...')
    
    try:
        from resources.libs.installer import ArcticFuseHebrewInstaller
        installer = ArcticFuseHebrewInstaller()
        
        def update_progress(msg, pct):
            progress.update(pct, msg)
        
        success = installer.install(progress_callback=update_progress)
        progress.close()
        
        if success:
            dialog.ok(
                'הצלחה!',
                f"{color('קבצי העברית הותקנו בהצלחה!', COLOR_SUCCESS)}\n\n"
                "יש להפעיל מחדש את Kodi כדי לראות את השינויים."
            )
            
            if dialog.yesno('הפעלה מחדש', 'האם להפעיל מחדש את Kodi עכשיו?'):
                xbmc.executebuiltin('Quit')
        else:
            dialog.ok('שגיאה', color('ההתקנה נכשלה!', COLOR_ERROR))
            
    except Exception as e:
        progress.close()
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def uninstall_skin_hebrew():
    """Uninstall Skin Hebrew files"""
    dialog = xbmcgui.Dialog()
    
    if not dialog.yesno(
        'הסרת עברית מהסקין',
        f"{color('האם להסיר את קבצי העברית?', COLOR_WARNING)}\n\n"
        "הקבצים המקוריים ישוחזרו מהגיבוי."
    ):
        return
    
    try:
        from resources.libs.installer import ArcticFuseHebrewInstaller
        installer = ArcticFuseHebrewInstaller()
        
        success = installer.uninstall()
        
        if success:
            dialog.ok('הצלחה', color('קבצי העברית הוסרו!', COLOR_SUCCESS))
        else:
            dialog.ok('שגיאה', color('ההסרה נכשלה!', COLOR_ERROR))
            
    except Exception as e:
        dialog.ok('שגיאה', f"{color('שגיאה:', COLOR_ERROR)}\n{str(e)}")


def show_skin_info():
    """Show Skin info"""
    dialog = xbmcgui.Dialog()
    status = get_skin_status()
    
    hebrew_ver = status['version'] if status['version'] and status['version'] != 'installed' else 'לא ידוע'
    
    dialog.textviewer(
        'Arctic Fuse Skin - מידע',
        f"[B]סקין:[/B] Arctic Fuse 3\n"
        f"[B]גירסת עברית:[/B] {hebrew_ver}\n"
        f"[B]סטטוס:[/B] {'מותקן' if status['hebrew'] else 'לא מותקן'}\n\n"
        "[B]קבצי עברית כוללים:[/B]\n"
        "- פונט Rubik לתמיכה בעברית\n"
        "- תרגום ממשק לעברית\n"
        "- התאמות RTL"
    )


# ============================================
# BUILD MENU
# ============================================
def build_menu():
    """Build installation menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        items = [
            menu_item('התקנת בילד', 'התקן בילד חדש', 'DefaultAddonProgram.png'),
            menu_item('עדכון בילד', 'עדכן בילד נוכחי', 'DefaultAddonsUpdates.png'),
            menu_item('מידע על בילד נוכחי', '', 'DefaultAddonInfoProvider.png'),
            menu_item('חזרה', '', 'DefaultFolderBack.png'),
        ]

        selection = dialog.select(
            color('Build Installation', COLOR_HEADER),
            items, useDetails=True
        )

        if selection == -1 or selection == 3:
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
    """Update current build"""
    dialog = xbmcgui.Dialog()
    dialog.ok('עדכון בילד', 'פיצ\'ר זה יהיה זמין בקרוב!')


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
        f'• Screensaver שחור (לא אנימציה)\n'
        f'• הפעלה אחרי דקה\n'
        f'• עמעום בזמן השהיה',
        yeslabel='כן, יש לי OLED',
        nolabel='לא'
    )
    
    if result:
        if apply_oled_to_guisettings():
            dialog.ok('הצלחה', f'{color("הגדרות OLED הוחלו!", COLOR_SUCCESS)}\n\n'
                     f'• Screensaver: Black\n'
                     f'• זמן המתנה: דקה\n'
                     f'• עמעום בהשהיה: פעיל\n\n'
                     f'{color("יש להפעיל מחדש את Kodi", COLOR_WARNING)}')
        else:
            dialog.ok('שגיאה', 'לא הצלחתי להחיל את ההגדרות')


def maintenance_menu():
    """Maintenance menu"""
    dialog = xbmcgui.Dialog()
    
    while True:
        menu_items = [
            f"{bold('ניקוי Cache')}",
            f"{bold('ניקוי Packages')}",
            f"{bold('ניקוי Thumbnails')}",
            f"{bold('ניקוי הכל')}",
            f"{bold('Force Close Kodi')}",
            f"{color('─────────────────────────────────', COLOR_GRAY)}",
            f"{bold('הגדרות OLED')}",
            f"{color('חזרה', COLOR_GRAY)}",
        ]
        
        selection = dialog.select(
            color('Maintenance', COLOR_HEADER),
            menu_items
        )
        
        if selection == -1 or selection == 7:
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
        elif selection == 5:  # Separator
            continue
        elif selection == 6:
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
            menu_item('יצירת גיבוי', 'מהיר (הגדרות+מפתחות) או מלא', 'DefaultAddonService.png'),
            menu_item('שחזור מגיבוי', 'שחזר הגדרות ומפתחות', 'DefaultAddonsUpdates.png'),
            menu_item('מחיקת גיבויים', '', 'DefaultAddonService.png'),
            menu_item('חזרה', '', 'DefaultFolderBack.png'),
        ]

        selection = dialog.select(
            color('Backup & Restore', COLOR_HEADER),
            items, useDetails=True
        )

        if selection == -1 or selection == 3:
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
    elif mode == 'gears':
        gears_menu()
    elif mode == 'gearsai':
        gearsai_menu()
    elif mode == 'pov':
        pov_menu()
    elif mode == 'skin':
        skin_menu()
    elif mode == 'maintenance':
        maintenance_menu()
    elif mode == 'backup':
        backup_menu()
    elif mode == 'check_updates':
        check_updates_now()
    else:
        main_menu()
    
    log("Wizard closed")
