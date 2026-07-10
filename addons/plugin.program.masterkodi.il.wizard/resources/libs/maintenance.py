# -*- coding: utf-8 -*-
"""
MasterKodi IL Wizard - Maintenance Tools
"""
import os
import shutil
import xbmc
import xbmcgui

from resources.libs.config import (
    ADDON_ID, ADDON_NAME, HOME, ADDONS, USERDATA, ADDON_DATA_PATH, DATABASE,
    TEMP_FOLDER, COLOR_SUCCESS, COLOR_ERROR, COLOR_WARNING
)


def log(msg, level=xbmc.LOGINFO):
    xbmc.log(f'[{ADDON_ID}] Maintenance: {msg}', level)


def get_size(path):
    total = 0
    try:
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except:
                    pass
    except:
        pass
    return total


def format_size(b):
    for u in ['B', 'KB', 'MB', 'GB']:
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} TB"


def clear_cache():
    dialog = xbmcgui.Dialog()
    
    locations = [
        os.path.join(HOME, 'cache'),
        os.path.join(HOME, 'temp'),
        TEMP_FOLDER
    ]
    
    total = sum(get_size(l) for l in locations if os.path.exists(l))
    
    if total == 0:
        dialog.ok(ADDON_NAME, "No cache to clear.")
        return
    
    if not dialog.yesno(ADDON_NAME,
        f"Cache size: [COLOR {COLOR_WARNING}]{format_size(total)}[/COLOR]\n\nClear cache?",
        yeslabel="[B]Clear[/B]", nolabel="Cancel"):
        return
    
    for loc in locations:
        if os.path.exists(loc):
            try:
                for item in os.listdir(loc):
                    p = os.path.join(loc, item)
                    try:
                        if os.path.isfile(p):
                            os.remove(p)
                        else:
                            shutil.rmtree(p)
                    except:
                        pass
            except:
                pass
    
    dialog.ok(ADDON_NAME, f"[COLOR {COLOR_SUCCESS}]Cache cleared![/COLOR]")


def clear_packages():
    dialog = xbmcgui.Dialog()
    
    pkg = os.path.join(ADDONS, 'packages')
    if not os.path.exists(pkg):
        dialog.ok(ADDON_NAME, "Packages folder is empty.")
        return
    
    size = get_size(pkg)
    if size == 0:
        dialog.ok(ADDON_NAME, "Packages folder is empty.")
        return
    
    if not dialog.yesno(ADDON_NAME,
        f"Packages size: [COLOR {COLOR_WARNING}]{format_size(size)}[/COLOR]\n\nClear packages?",
        yeslabel="[B]Clear[/B]", nolabel="Cancel"):
        return
    
    try:
        for f in os.listdir(pkg):
            try:
                os.remove(os.path.join(pkg, f))
            except:
                pass
        dialog.ok(ADDON_NAME, f"[COLOR {COLOR_SUCCESS}]Packages cleared![/COLOR]")
    except Exception as e:
        dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]Error:[/COLOR] {str(e)}")


def clear_thumbnails():
    dialog = xbmcgui.Dialog()
    
    thumb = os.path.join(USERDATA, 'Thumbnails')
    if not os.path.exists(thumb):
        dialog.ok(ADDON_NAME, "Thumbnails folder is empty.")
        return
    
    size = get_size(thumb)
    if size == 0:
        dialog.ok(ADDON_NAME, "Thumbnails folder is empty.")
        return
    
    if not dialog.yesno(ADDON_NAME,
        f"Thumbnails size: [COLOR {COLOR_WARNING}]{format_size(size)}[/COLOR]\n\n"
        "Clear thumbnails?\n[COLOR yellow]Kodi will regenerate them as needed.[/COLOR]",
        yeslabel="[B]Clear[/B]", nolabel="Cancel"):
        return
    
    progress = xbmcgui.DialogProgress()
    progress.create(ADDON_NAME, "Clearing thumbnails...")
    
    try:
        for item in os.listdir(thumb):
            p = os.path.join(thumb, item)
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                    os.makedirs(p)
                else:
                    os.remove(p)
            except:
                pass
        
        # Clear Textures DB
        for f in os.listdir(DATABASE):
            if f.startswith('Textures') and f.endswith('.db'):
                try:
                    os.remove(os.path.join(DATABASE, f))
                except:
                    pass
        
        progress.close()
        
        if dialog.yesno(ADDON_NAME,
            f"[COLOR {COLOR_SUCCESS}]Thumbnails cleared![/COLOR]\n\nRestart Kodi?",
            yeslabel="[B]Restart[/B]", nolabel="Later"):
            xbmc.executebuiltin('Quit')
    except Exception as e:
        progress.close()
        dialog.ok(ADDON_NAME, f"[COLOR {COLOR_ERROR}]Error:[/COLOR] {str(e)}")


def maintenance_menu():
    dialog = xbmcgui.Dialog()
    
    while True:
        cache = sum(get_size(l) for l in [os.path.join(HOME, 'cache'), os.path.join(HOME, 'temp')] if os.path.exists(l))
        pkg = get_size(os.path.join(ADDONS, 'packages'))
        thumb = get_size(os.path.join(USERDATA, 'Thumbnails'))
        
        items = [
            f"[COLOR yellow]Clear Cache[/COLOR] ({format_size(cache)})",
            f"[COLOR yellow]Clear Packages[/COLOR] ({format_size(pkg)})",
            f"[COLOR yellow]Clear Thumbnails[/COLOR] ({format_size(thumb)})",
            "",
            f"[COLOR red]Clear All[/COLOR] ({format_size(cache + pkg + thumb)})",
            "",
            "[COLOR gray]< Back[/COLOR]"
        ]
        
        sel = dialog.select("Maintenance", items)
        
        if sel < 0 or sel == len(items) - 1:
            break
        
        if sel == 0:
            clear_cache()
        elif sel == 1:
            clear_packages()
        elif sel == 2:
            clear_thumbnails()
        elif sel == 4:
            if dialog.yesno(ADDON_NAME,
                f"[COLOR {COLOR_WARNING}]Clear ALL cache?[/COLOR]\n\n"
                f"Total: {format_size(cache + pkg + thumb)}",
                yeslabel="[B]Clear All[/B]", nolabel="Cancel"):
                clear_cache()
                clear_packages()
                clear_thumbnails()
