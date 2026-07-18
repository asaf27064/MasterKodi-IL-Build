# -*- coding: utf-8 -*-
import sys,xbmcgui,xbmc,xbmcplugin,xbmcaddon
import json
from resources.modules import log


def run_async_task(target):
    import threading
    task_thread = threading.Thread(target=target)
    task_thread.start()
    task_thread.join()


    


def start():
    if len(sys.argv) >= 2:
        ##### TELEGRAM ###########
        if sys.argv[1] == "login_to_telegram":
            from resources.sources.telegram import run_async_login_to_telegram
            run_async_task(run_async_login_to_telegram)
            return
        if sys.argv[1] == "logout_from_telegram":
            from resources.sources.telegram import run_async_logout_from_telegram
            run_async_task(run_async_logout_from_telegram)
            return
        if sys.argv[1] == "telegram_helper_window":
            from resources.sources.telegram import telegram_helper_window
            telegram_helper_window()
            return
        ##########################
        # MasterKodi AI: pair a Gemini key from the phone (QR code) -> saves to
        # the 'api_key' setting. Painless alternative to typing it on a TV remote.
        if sys.argv[1] == "pair_gemini_key":
            try:
                from resources.aisubs import pair
                pair.run_pairing()
            except Exception as _e:
                log.warning('pair_gemini_key error: %s' % _e)
            return
        # MasterKodi AI: the "AI translate" item -> translate now, hand back the .srt.
        if len(sys.argv) > 2 and 'gearsai_ai=1' in sys.argv[2]:
            try:
                from resources.aisubs import ai_bridge
                _p = ai_bridge.translate_now()
            except Exception as _e:
                log.warning('AI translate error: %s' % _e); _p = None
            if _p:
                _li = xbmcgui.ListItem(label='עברית')
                xbmcplugin.addDirectoryItems(int(sys.argv[1]), [(_p, _li, False)], 1)
            xbmcplugin.endOfDirectory(int(sys.argv[1]))
            return
        # MasterKodi: "sync the currently-shown Hebrew subtitle" -> re-time it onto
        # the playing file's embedded English (or a release-matched English), hand
        # back the re-timed .srt for Kodi to load.
        if len(sys.argv) > 2 and 'gearsai_sync=1' in sys.argv[2]:
            try:
                from resources.aisubs import ai_bridge
                _p = ai_bridge.sync_current_sub()
            except Exception as _e:
                log.warning('sync error: %s' % _e); _p = None
            if _p:
                _li = xbmcgui.ListItem(label='עברית (מסונכרן)')
                xbmcplugin.addDirectoryItems(int(sys.argv[1]), [(_p, _li, False)], 1)
            xbmcplugin.endOfDirectory(int(sys.argv[1]))
            return
        #Addon = xbmcaddon.Addon('service.subtitles.gearsai').setSetting("get_subs",'1')

        xbmcaddon.Addon('service.subtitles.gearsai').setSetting("man_search_return","")
        log.warning(sys.argv)
        try:
            sub_data=sys.argv[2]+'$$$$$$$$'+sys.argv[1]
        except:
            sub_data="None"+'$$$$$$$$'+sys.argv[1]
        #xbmcgui.Window(10000).setProperty("man_search_subs",str(sub_data))
        xbmcaddon.Addon('service.subtitles.gearsai').setSetting("man_search_subs",sub_data)
        response=""
        timeout=0
        all_d=[]
        while response=="":
            response=xbmcaddon.Addon('service.subtitles.gearsai').getSetting("man_search_return")
            
            
            xbmc.sleep(100)
            timeout+=1
            if timeout>400:#40 sec
                break
        log.warning(response)
        
        if timeout>400:
            sys.exit(1)
        response=json.loads(response)
        if "hearing_imp" in str(response) and 'thumbnailImage' in str(response):

            # MasterKodi: top row -> sync the Hebrew sub currently on screen onto the
            # playing file's real timing (embedded English first, external English
            # fallback). Only offered while a Hebrew sub is actually active.
            try:
                _row_on = xbmcaddon.Addon('service.subtitles.gearsai').getSetting('manual_sync_row') != 'false'
                _has_cur = _row_on and bool(xbmcgui.Window(10000).getProperty('gearsai.current_heb_sub'))
            except Exception:
                _has_cur = False
            if _has_cur:
                sync_li = xbmcgui.ListItem(label='עברית', label2='● סנכרן את הכתובית שמוצגת כעת')
                sync_li.setArt({'thumb': 'he', 'icon': '0'})
                sync_li.setProperty('sync', 'true')
                sync_li.setProperty('hearing_imp', 'false')
                all_d.append(('plugin://service.subtitles.gearsai/?action=download&gearsai_sync=1', sync_li, False))

            for items in response:
                # foreign-script names (CJK etc.) render as tofu boxes in the
                # skin's subtitle dialog (Rubik has no such glyphs) -- reuse
                # the wand window's sanitizer for the KODI window too
                try:
                    from resources.modules.sub_window import _sanitize_glyphs
                    _lbl2 = _sanitize_glyphs(items['label2']) or ('כתובית ' + str(items['label']))
                except Exception:
                    _lbl2 = items['label2']
                listitem = xbmcgui.ListItem(label          = items['label'],
                                            label2         = _lbl2,
                                            
                                            )
                listitem.setArt({'thumb' : items['thumbnailImage'], 'icon': items['iconImage']})
                listitem.setProperty( "sync", items["sync"] )
                listitem.setProperty( "hearing_imp",items["hearing_imp"] )
                
                all_d.append((items['url'],listitem,False))

            # MasterKodi AI: offer an on-the-fly Gemini translation as an extra row.
            ai_li = xbmcgui.ListItem(label='עברית', label2='AI · תרגם עם Gemini')
            ai_li.setArt({'thumb': 'he', 'icon': '0'})
            ai_li.setProperty('sync', 'true')
            ai_li.setProperty('hearing_imp', 'false')
            all_d.append(('plugin://service.subtitles.gearsai/?action=download&gearsai_ai=1', ai_li, False))

        else:
            response=response.replace('\\\\','\\')
            log.warning(response)
            listitem =  xbmcgui.ListItem(label=response)
            
            if response!='"clean"' and response!='"open_settings"' :
                xbmc.executebuiltin('Dialog.Close(all,true)')
            
            #sys.exit(1)
    try:
        xbmcplugin .addDirectoryItems(int(sys.argv[1]),all_d,len(all_d))
        xbmc.sleep(100)
        xbmcplugin.endOfDirectory(int(sys.argv[1]),updateListing =True,cacheToDisc =True)
        log.warning('4')
    except:
        pass
    
            
            
