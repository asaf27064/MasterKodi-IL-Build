import time,re,xbmcaddon
import xbmc,xbmcgui,xbmcplugin,xbmcvfs
import os,json,shutil
from resources.modules import cache
from resources.modules import log
from resources.modules.engine import download_sub,get_subtitles,sort_subtitles
from urllib.parse import  unquote_plus, unquote,  quote
from resources.modules.twilight.write_heb_embedded_taglines_check import write_heb_embedded_taglines_check_func
from resources.modules.general import TransFolder,clean_title,CachedSubFolder,get_video_data,get_db_data,MySubFolder,notify,Thread,show_results,save_file_name
from urllib.parse import parse_qsl
from resources.modules.sub_window import MySubs
import urllib.parse
from resources.modules import general
Addon=xbmcaddon.Addon()
MyScriptName = xbmcaddon.Addon().getAddonInfo('name')
MyScriptID = xbmcaddon.Addon().getAddonInfo('id')
unque=urllib.parse.unquote_plus
monit = xbmc.Monitor()
ab_req=monit.abortRequested()
log.warning('Starting %s Service!!!'%MyScriptName)

global video_id,pre_video_id,trigger
global playing_addon,break_wait
break_wait=False
playing_addon=""
video_id=""
pre_video_id=""
trigger=False
que=urllib.parse.quote_plus

####################################################################################
GET_SUBTITLES_POP_KEYS = ['tmdb', 'Tagline', 'Tagline_From_Fen', 'VideoPlayer.Tagline', 'file_original_path', 'mpaa', 'is_local_media_playing', 'media_type_videoInfoTag', 'media_type_ListItem.DBTYPE']
def temporary_pop_and_get_subtitles(video_data):
    # Store and remove the values for the specified keys
    temp_values = {key: video_data.pop(key) for key in GET_SUBTITLES_POP_KEYS if key in video_data}
    log.warning(f"DEBUG | temporary_pop_and_get_subtitles | MID POP | video_data={str(video_data)}")
    
    try:
        return cache.get(get_subtitles, 24, video_data, table='subs')
    finally:
        # Restore the original values to the video_data
        video_data.update(temp_values)
####################################################################################

#################### MASTERKODI - Subtitle pre-fetch (opt-in) ######################
# Gears notifies us (JSONRPC.NotifyAll 'gearsai_prefetch') the moment it STARTS
# scraping sources: the user will very likely press play within seconds, and
# imdb/season/episode are already known. We run the exact provider search the
# autosub flow would run at playback (engine.get_subtitles, all enabled sites)
# and keep the result in memory; when playback starts, autosub consumes it
# instead of searching again -- the subtitle appears near-instantly. Keyed in
# memory (not the sqlite fn-cache) because that cache's key is an md5 of the
# whole video_data dict, which differs between browse and playback states.
# Off by default ('prefetch' setting). Fail-open: any error -> normal flow.
PREFETCH_TTL = 6 * 3600
prefetch_store = {}          # 'imdb|season|episode' -> {'ts': epoch, 'result': list}
prefetch_inflight = set()    # keys currently being searched (dedupe)


def _prefetch_key(imdb, season, episode):
    return '%s|%s|%s' % (imdb or '', str(season or '0'), str(episode or '0'))


def _prefetch_worker(payload):
    key = _prefetch_key(payload.get('imdb'), payload.get('season'), payload.get('episode'))
    try:
        media_type = 'tv' if payload.get('media_type') not in ('movie', 'movies') else 'movie'
        title = clean_title(payload.get('title', ''))
        # Gears passes the SHOW title for episodes (that's what its scraper meta holds).
        video_data = {
            'state': 'prefetch',
            'imdb_UniqueID': payload.get('imdb', ''),
            'IMDBNumber': payload.get('imdb', ''),
            'imdb': payload.get('imdb', ''),
            'title': title,
            'OriginalTitle': clean_title(payload.get('original_title', '') or payload.get('title', '')),
            'TVShowTitle': title if media_type == 'tv' else '',
            'year': str(payload.get('year', '')),
            'season': str(payload.get('season', '') or '0'),
            'episode': str(payload.get('episode', '') or '0'),
            'tmdb': str(payload.get('tmdb', '')),
            'mpaa': '',
            'Tagline': '',
            'VideoPlayer.Tagline': '',
            'Tagline_From_Fen': '',
            'file_original_path': title,
            'is_local_media_playing': False,
            'media_type': media_type,
        }
        log.warning('PREFETCH | searching for %s' % key)
        f_result = get_subtitles(dict(video_data))
        if f_result:
            prefetch_store[key] = {'ts': time.time(), 'result': f_result}
            log.warning('PREFETCH | ready: %s subs for %s' % (len(f_result), key))
        else:
            log.warning('PREFETCH | no results for %s' % key)
        # keep the store tiny -- a browse session touches a handful of items
        if len(prefetch_store) > 8:
            oldest = sorted(prefetch_store, key=lambda k: prefetch_store[k]['ts'])[0]
            prefetch_store.pop(oldest, None)
    except Exception as e:
        log.warning('PREFETCH | worker error: %s' % e)
    finally:
        prefetch_inflight.discard(key)


def maybe_start_prefetch(data):
    """Handler for the gears 'gearsai_prefetch' notification (data = json str)."""
    if xbmcaddon.Addon().getSetting('prefetch') != 'true':
        return
    payload = json.loads(data)
    if isinstance(payload, list):          # NotifyAll sometimes wraps in a list
        payload = payload[0]
    if not payload.get('imdb', '').startswith('tt'):
        return
    key = _prefetch_key(payload.get('imdb'), payload.get('season'), payload.get('episode'))
    entry = prefetch_store.get(key)
    if entry and (time.time() - entry['ts']) < PREFETCH_TTL:
        return                              # already have fresh results
    if key in prefetch_inflight:
        return                              # already searching
    prefetch_inflight.add(key)
    t = Thread(_prefetch_worker, payload)
    t.daemon = True                        # never block Kodi shutdown
    t.start()


def prefetch_lookup(video_data):
    """Return prefetched results matching the now-playing item, else None."""
    try:
        if xbmcaddon.Addon().getSetting('prefetch') != 'true':
            return None
        key = _prefetch_key(video_data.get('imdb'), video_data.get('season'), video_data.get('episode'))
        entry = prefetch_store.get(key)
        if entry and (time.time() - entry['ts']) < PREFETCH_TTL and entry['result']:
            log.warning('PREFETCH | HIT for %s (%s subs, no live search needed)' % (key, len(entry['result'])))
            return entry['result']
    except Exception as e:
        log.warning('PREFETCH | lookup error: %s' % e)
    return None
####################################################################################
    
# Currently only for Hebrew/English, the most common.
def translate_sub_language_to_hebrew(language):
    if "Hebrew" in language:
        return "עברית"
    elif language == "English":
        return "אנגלית"
    elif language == "Russian":
        return "רוסית"
    elif language == "Arabic":
        return "ערבית"
    else:
        return language
####################################################################################

def wait_for_video():

    log.warning('Waiting for video')
    counter=0
    vidtime_pre=0
    once=True
    
    while counter<70:
        try:
            vidtime = xbmc.Player().getTime()
            if vidtime>0:
                  if once:
                      vidtime_pre=vidtime
                      once=False
                  if vidtime_pre!=vidtime:
                      break
                  vidtime_pre=vidtime
                  
            counter+=1
            xbmc.sleep(100)
        except:
          break
          
    log.warning(f"DEBUG | Time waited for video to start (wait_for_video): {counter/10} seconds.")

######################### EMBEDDED SUBS #####################################
    
def determine_placeHebrewEmbeddedSub(last_sub_in_cache_is_external, last_sub_in_cache_is_heb_embedded, first_external_sub_language):

    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()

    # Check if "auto_place_hebrew_embedded_subs" setting is 'true'
    if Addon.getSetting("auto_place_hebrew_embedded_subs") == 'true':
        # Check if last sub cache is not an external subtitle (not empty or not embedded Hebrew sub).
        if not last_sub_in_cache_is_external:
            return True
            
    else:
        # Check if first external subtitle language is NOT "Hebrew"
        if first_external_sub_language != "Hebrew":
            # Check if last sub cache is not an external subtitle (not empty or not embedded Hebrew sub).
            if not last_sub_in_cache_is_external:
                return True
                
        # If first_external_sub_language=="Hebrew", Check if last sub cache is Hebrew embedded
        elif last_sub_in_cache_is_heb_embedded:
            return True 
    
    return False
    
    
def wait_for_video_and_return_subs_list():

    log.warning('Waiting for video')
    counter=0
    vidtime=0
    once=True
    subs=[]
    
    while counter<70:
        try:
          subs=xbmc.Player().getAvailableSubtitleStreams()
          if len(subs)>0:
            log.warning(f"DEBUG | Time waited for video to start (wait_for_video_and_return_subs_list): {counter/10} seconds.")
            return subs
          
          vidtime = xbmc.Player().getTime()
          if vidtime>0:
                if once:
                    vidtime_pre=vidtime
                    once=False
                if vidtime_pre!=vidtime:
                    break
                vidtime_pre=vidtime
        except:
          pass
        counter+=1
        xbmc.sleep(100)
    
    log.warning(f"DEBUG | Time waited for video to start (wait_for_video_and_return_subs_list): {counter/10} seconds.")    
    return subs
        
        
def check_if_embedded_sub_exists(embedded_language):
    subs = wait_for_video_and_return_subs_list()
    return any(sub == embedded_language for sub in subs)
    
    
def get_embedded_sub_index(subs, embedded_language):
    return next((index for index, sub in enumerate(subs) if sub == embedded_language), None)

    
def set_embedded_hebrew_sub(video_data):

    subs = wait_for_video_and_return_subs_list()
    index_sub = get_embedded_sub_index(subs, 'heb')
    
    if index_sub is not None:
        log.warning(f'Placing Hebrew embedded Stream Sub: index_sub={str(index_sub)}')
        xbmc.Player().setSubtitleStream(index_sub)
        
        save_data='HebrewSubEmbedded'+video_data['imdb']+str(video_data['season'])+str(video_data['episode'])+video_data['OriginalTitle']+video_data['Tagline']
        save_file_name(que(save_data),"Hebrew",video_data,source='loc')
            
        xbmc.sleep(300)
    
    
def add_embedded_sub_if_exists(video_data, f_result, embedded_language):

    # Avoid checking when using subtitles search from context menu (display_subtitle)
    if not xbmc.Player().isPlaying():
        log.warning(f'DEBUG | add_embedded_sub_if_exists STOP | embedded_language={embedded_language} | xbmc.Player().isPlaying(): {xbmc.Player().isPlaying()}')
        return f_result

    if embedded_language=='heb':
        try:
            global is_embedded_hebrew_sub_exists
            if not is_embedded_hebrew_sub_exists:
                log.warning(f'DEBUG | add_embedded_sub_if_exists STOP | embedded_language={embedded_language} | is_embedded_hebrew_sub_exists: {is_embedded_hebrew_sub_exists}')
                return f_result
        except:
            pass
            
    if embedded_language=='heb':
        embedded_sub_name_prefix = 'HebrewSubEmbedded'
        FullLanguageName = 'Hebrew'
        thumbnailImageLanguageName = 'he'
        EmbeddedSubLabel = 'תרגום מובנה בעברית'
    elif embedded_language=='eng':
        embedded_sub_name_prefix = 'EnglishSubEmbedded'
        FullLanguageName = 'English'
        thumbnailImageLanguageName = 'en'
        EmbeddedSubLabel = 'תרגום מובנה באנגלית'
        
    # Exit if embedded sub is already present in f_result. Else, continue. Safe checking again.
    for items in f_result:
        if embedded_sub_name_prefix in items[8]:
            return f_result
            
    subs=wait_for_video_and_return_subs_list()
    index_sub = get_embedded_sub_index(subs, embedded_language)
    log.warning(f'add_embedded_sub_if_exists | embedded_language={embedded_language} | Embbeded subs list: {subs} | index_sub={index_sub}')

    if index_sub is not None:
        download_data={}
        download_data['url']=str(index_sub)
        download_data['file_name']=str(index_sub)
        save_data=embedded_sub_name_prefix+video_data['imdb']+str(video_data['season'])+str(video_data['episode'])+video_data['OriginalTitle']+video_data['Tagline']
        
        url = "plugin://%s/?action=download&download_data=%s&filename=%s&language=%s&source=%s" % (MyScriptID,
                                                            que(json.dumps(download_data)),
                                                            que(que(que(save_data))),
                                                            FullLanguageName,
                                                            embedded_sub_name_prefix)
        json_value={'url':url,
                             'label':FullLanguageName,
                             'label2':'[LOC] '+ EmbeddedSubLabel,
                             'iconImage':"0",
                             'thumbnailImage':thumbnailImageLanguageName,
                             'hearing_imp':'false',
                             'site_id':'[LOC]',
                             'sub_color':'cyan',
                             'filename':que(save_data),
                             'sync': 'true'}
                             
                        
        index = 0
        if embedded_language=='eng':
            # Find the index where English subtitles should be inserted
            for i, sub in enumerate(f_result):
                if sub[0] == 'Hebrew':
                    index = i + 1  # Insert after the last Hebrew subtitle
                             
            
        f_result.insert(index, (json_value['label'],'[COLOR %s]'%json_value['sub_color']+json_value['label2']+'[/COLOR]',json_value['iconImage'],json_value['thumbnailImage'],json_value['url'],101,json_value['sync'],json_value['hearing_imp'],json_value['filename'],json_value['site_id']))
        
    return f_result
    
    
def add_embedded_ai_sub_if_exists(video_data, f_result):
    """MASTERKODI: offer "תרגום מובנה ← עברית (AI)" when the playing file has a
    FOREIGN embedded subtitle stream. Clicking it translates that track (or an
    external English sub re-timed onto its cue skeleton) to Hebrew -- perfectly
    synced, because the embedded cues carry the video's own timeline. The row is
    never auto-picked (see the source=embedded_ai guards in place_sub); only an
    explicit click -- or a remembered previous click, behind its own consent
    dialog -- runs it. Gate is free: Kodi's own stream list, no network."""
    try:
        if xbmcaddon.Addon().getSetting('embedded_ai_translate') == 'false':
            return f_result
        if not xbmc.Player().isPlaying():
            return f_result
        for items in f_result:
            if 'EmbeddedAISub' in items[8]:
                return f_result
        subs = wait_for_video_and_return_subs_list()
        # Kodi's stream list mixes EMBEDDED tracks (bare language codes like
        # 'eng') with loaded EXTERNAL subs (full filenames, e.g. 'Toy Story 5
        # ... (חיצוני)') -- seen live 2026-07-21. Only 2-3 letter alpha codes
        # are embedded tracks; anything else must not summon the row.
        foreign = [s for s in subs
                   if s and 2 <= len(s) <= 3 and s.isalpha()
                   and s.lower() not in ('heb', 'he', 'und')]
        if not foreign:
            return f_result

        download_data = {'url': 'ai', 'file_name': 'ai',
                         'langs': ','.join(dict.fromkeys(foreign))}
        save_data = ('EmbeddedAISub' + video_data['imdb'] + str(video_data['season'])
                     + str(video_data['episode']) + video_data['OriginalTitle']
                     + video_data['Tagline'])
        url = "plugin://%s/?action=download&download_data=%s&filename=%s&language=%s&source=%s" % (
            MyScriptID, que(json.dumps(download_data)), que(que(que(save_data))),
            'Hebrew', 'embedded_ai')
        label2 = '[AI] תרגום מובנה בעברית'
        # Insert after the Hebrew block (same placement rule as the embedded rows).
        index = 0
        for i, sub in enumerate(f_result):
            if sub[0] == 'Hebrew':
                index = i + 1
        f_result.insert(index, ('Hebrew', '[COLOR gold]' + label2 + '[/COLOR]',
                                '0', 'he', url, 100, 'true', 'false',
                                que(save_data), '[AI]'))
    except Exception as e:
        log.warning(f'add_embedded_ai_sub_if_exists error: {e}')
    return f_result


def add_embedded_subs_to_subs_list(video_data, f_result):

    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()

    # Add Hebrew Embbeded Subtitles if exists
    search_language_hebrew_bool = (Addon.getSetting('language_hebrew') == 'true' or Addon.getSetting("all_lang") == 'true')
    if search_language_hebrew_bool:
        f_result=add_embedded_sub_if_exists(video_data, f_result, 'heb')

    # Add English Embbeded Subtitles if exists
    search_language_english_bool = (Addon.getSetting('language_english') == 'true' or Addon.getSetting("all_lang") == 'true')
    if search_language_english_bool:
        f_result=add_embedded_sub_if_exists(video_data, f_result, 'eng')

    # MASTERKODI: AI translation of a foreign embedded track
    f_result=add_embedded_ai_sub_if_exists(video_data, f_result)

    return f_result
        
####################################################################################
    
def isPlayingAddonExcluded(movieFullPath,playing_addon):
    excluded_addons=['idanplus','sdarot.tv','youtube','kids_new']

    playing_addon=playing_addon+movieFullPath
 
    if (playing_addon.find("pvr://") > -1) :
        log.warning("isPlayingAddonExcluded(): Video is playing via Live TV, which is currently set as excluded location.")
        return True
    if (xbmc.getInfoLabel("VideoPlayer.mpaa")=='heb'):
          log.warning("isPlayingAddonExcluded(): mpaa!!." )
          return True
    for x in excluded_addons:
        
        if x in playing_addon.lower():
            
            log.warning("isPlayingAddonExcluded(): Video is playing from '%s', which is currently set as !!excluded_addons!!."%x )
            
            return True
    if ',' in  Addon.getSetting('ExcludedAddons'):
        ExcludedAddons = Addon.getSetting('ExcludedAddons').split(',')  
    else:
        ExcludedAddons = [Addon.getSetting('ExcludedAddons')]
    for items in ExcludedAddons:
        if items.lower() in playing_addon.lower() and (len(items)>0):
            log.warning("isPlayingAddonExcluded(): Video is playing from '%s', which is currently set as !!excluded_addons!!."%items )
            return True
    return False
    
def _is_image_result(f_sub):
    """True if a result row is an image-based sub (VobSub 'idx in zip' / PGS .sup)
    -- detected from its filename + label so auto-pick can skip past it to text."""
    try:
        blob = (str(f_sub[8]) + ' ' + str(f_sub[1])).lower()
    except Exception:
        return False
    # Only the reliable image-sub markers (Ktuvit tags VobSub uploads
    # '..._idx_in_zip', PGS '..._sup_in_zip'). Deliberately NOT a bare '.sup'/'pgs'
    # substring -- that would false-match normal names like 'The.Superman...'.
    return ('idx_in_zip' in blob or 'idx.in.zip' in blob
            or 'sup_in_zip' in blob or 'sup.in.zip' in blob
            or 'vobsub' in blob)


def place_sub(video_data,f_result,last_sub_name_in_cache,last_sub_language_in_cache,all_subs,last_sub_in_cache_is_empty):

    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()
    
    if Addon.getSetting("enable_autosub_notifications")=='true':
        general.show_msg="מוריד כתובית נבחרת"
    # Default placing sub to the first subtitle in subtitles list results.
    selected_sub=f_result[0]
    # Prefer a TEXT subtitle over image-based (VobSub 'idx in zip' / PGS) results
    # even when the image sub ranks higher by match% -- image subs ignore the Kodi
    # font, render blurry and are often mis-tagged. This ONLY changes which sub
    # auto-downloads; the results list shown to the user stays sorted by match%.
    # MASTERKODI: never auto-pick the embedded-AI row -- it triggers a full AI
    # translation, which must stay an explicit user choice (a REMEMBERED pick is
    # honored below, and translate_embedded still asks consent on non-manual runs).
    for f_sub in f_result:
        if not _is_image_result(f_sub) and 'source=embedded_ai' not in str(f_sub[4]):
            selected_sub=f_sub
            break
    # When the pick is non-Hebrew (i.e. it's about to be AI-translated), prefer
    # an SDH source of the same language within 10% match -- SDH speaker tags
    # ("SARA:") give the translator much better Hebrew gender. Never applies
    # when a Hebrew sub was found (Hebrew needs no translation).
    try:
        if (Addon.getSetting("auto_translate")=='true'
                and selected_sub[0] != 'Hebrew' and selected_sub[7] != 'true'):
            for f_sub in f_result:
                if (f_sub[0] == selected_sub[0] and f_sub[7] == 'true'
                        and not _is_image_result(f_sub)
                        and f_sub[5] >= selected_sub[5] - 10):
                    log.warning(f"place_sub | preferring SDH source for translation: {f_sub[8]}")
                    selected_sub=f_sub
                    break
    except Exception:
        pass
    log.warning(f"place_sub | selected_sub BEFORE checking last sub in cache: {selected_sub}")
    # Changing subtitle to place to subtitles from database.db cache db (if exists)
    if not last_sub_in_cache_is_empty:
        for f_sub in f_result:
            if (last_sub_name_in_cache==f_sub[8]) and (last_sub_language_in_cache==f_sub[0]):
                # Honor the remembered pick -- EXCEPT a remembered NON-Hebrew
                # source (an auto-translate input, not a deliberate Hebrew
                # choice) when the fresh best beats it by >10% match: failed
                # retries used to persist a poor source (e.g. a 28% release)
                # and starve a 62% match forever.
                try:
                    if f_sub[0] != 'Hebrew' and (selected_sub[5] - f_sub[5]) > 10:
                        log.warning(f"place_sub | ignoring remembered low-match source: {f_sub[8]} ({f_sub[5]}% vs best {selected_sub[5]}%)")
                        break
                except Exception:
                    pass
                selected_sub=f_sub
                break
    log.warning(f"place_sub | selected_sub AFTER checking last sub in cache: {selected_sub}")
    
    c_sub_file=None
    place_sub_count = 0
    for f_sub in f_result:
        place_sub_count += 1
    
        params=get_params(selected_sub[4],"")
        download_data=unque(params["download_data"])
        download_data=json.loads(download_data)
        source=(params["source"])
        language=(params["language"])
        filename=unque(params["filename"])
        # Stash which sub was picked + its match% so the AI translate step can
        # show "chose: <release> · <match>% · <site>" in the progress bar.
        try:
            general.ai_pick_name = selected_sub[8]
            general.ai_pick_pct = selected_sub[5]
            general.ai_pick_site = selected_sub[9]
        except Exception:
            pass
        try:
                
            sub_file=download_sub(source,download_data,MySubFolder,language,filename)
            log.warning('Auto Sub result:'+str(sub_file))

            # MASTERKODI: the user DECLINED the AI translation -- if the file
            # carries an embedded foreign track, show THAT (perfect sync, no
            # cost) instead of placing a weak external. Only real embedded
            # streams count (bare 2-3 letter codes; loaded externals appear in
            # the same list as full filenames). English preferred.
            try:
                if getattr(general, 'ai_declined', False):
                    _subs = wait_for_video_and_return_subs_list()
                    _idx = get_embedded_sub_index(_subs, 'eng')
                    if _idx is None:
                        _idx = next((i for i, s in enumerate(_subs)
                                     if s and 2 <= len(s) <= 3 and s.isalpha()
                                     and s.lower() not in ('heb', 'he', 'und')), None)
                    if _idx is not None:
                        log.warning(f"place_sub | declined -> embedded stream {_idx} ({_subs[_idx]})")
                        xbmc.Player().setSubtitleStream(_idx)
                        xbmc.Player().showSubtitles(True)
                        # Record the pick exactly like a manual embedded-row
                        # click does -- otherwise the wand shows no [ נוכחית ]
                        # marker (its 'current' comes from this DB record).
                        try:
                            if _subs[_idx] == 'eng':
                                _sd = 'EnglishSubEmbedded'+video_data['imdb']+str(video_data['season'])+str(video_data['episode'])+video_data['OriginalTitle']+video_data['Tagline']
                                save_file_name(que(_sd), "English", video_data, source='EnglishSubEmbedded')
                        except Exception as _se:
                            log.warning(f"place_sub | declined-embedded save failed: {_se}")
                        if Addon.getSetting("enable_autosub_notifications")=='true':
                            notify("התרגום בוטל - מוצגת הכתובית המובנית")
                        general.ai_declined = False
                        break
            except Exception as _e:
                log.warning(f"place_sub | declined-embedded switch failed: {_e}")

            # MASTERKODI: a REMEMBERED embedded pick resolves to the sentinel
            # 'EmbeddedSubSelected' (stream already selected inside
            # download_sub). setSubtitles() on that string would silently load
            # nothing and the loop would fall through to an external -- handle
            # it like the manual paths do: save + done.
            if sub_file == 'EmbeddedSubSelected':
                save_file_name(params["filename"],language,video_data,source=source)
                if Addon.getSetting("enable_autosub_notifications")=='true':
                    notify("התרגום המובנה יופיע בעוד 10 שניות")
                log.warning(f"place_sub | remembered embedded pick honored ({language})")
                break

            xbmc.sleep(200)
            xbmc.Player().setSubtitles(sub_file)
            save_file_name(params["filename"],language,video_data,source=source)

            f_count=0
            max_sub_cache=int(Addon.getSetting("subtitle_trans_cache"))
            for filename_o in os.listdir(CachedSubFolder):
                f_count+=1
            
            if (f_count>max_sub_cache):
                    for filename_o in os.listdir(CachedSubFolder):
                        f = os.path.join(CachedSubFolder, filename_o)
                        os.remove(f)
            try:
                file_type=(os.path.splitext(sub_file)[1])
            except:
                file_type=""
            c_sub_file=os.path.join(CachedSubFolder, f"{source}_{language}_{filename}{file_type}")
            if not os.path.exists(c_sub_file):
                    if file_type=='.idx'  or file_type=='.sup':
                        shutil.copy(sub_file,c_sub_file.replace('idx','sub').replace('sup','sub'))
                    try:
                        shutil.copy(sub_file,c_sub_file)
                    except Exception as e:
                        log.warning(f"shutil.copy(sub_file,c_sub_file) | Exception: {str(e)}")
                        pass
            
            ################################################################################################################################
            # Reformatting variables for user notification of auto selected subtitle
            if Addon.getSetting("enable_autosub_notifications")=='true':
                
                from resources.modules.engine import format_website_source_name
                notify_website_name = format_website_source_name(source)
                # MASTERKODI: no '(תרגום מכונה)' tag when the user declined the
                # translation -- the sub being placed is the plain original.
                notify_language = f"{translate_sub_language_to_hebrew(language)} (תרגום מכונה)" if language != "Hebrew" and Addon.getSetting("auto_translate")=='true' and not getattr(general, 'ai_declined', False) else translate_sub_language_to_hebrew(language)
                notify_sync_percent = str(selected_sub[5])
            
                notify( f"{notify_language} | {notify_sync_percent}% | {notify_website_name}" )
            ################################################################################################################################
            
            # MASTERKODI: remember the active Hebrew sub so the manual "sync this
            # subtitle" action knows which file to re-time.
            try:
                if 'Hebrew' in str(language) or 'עברית' in str(language):
                    xbmcgui.Window(10000).setProperty('gearsai.current_heb_sub', sub_file)
            except Exception:
                pass

            # Break the loop since setting external subtitle was successful.
            log.warning(f"DEBUG | place_sub | Number of try: {place_sub_count} | Successfuly set external sub: {sub_file}")
            break
                    
        except Exception as e:
            # Try the next subtitle in f_result.
            log.warning(f"DEBUG | place_sub | Number of try: {place_sub_count} | Exception in Sub: {str(e)}")
            # MASTERKODI: the retry walk must not wander into the embedded-AI row
            # either -- skip it and keep looking for a normal subtitle.
            if 'source=embedded_ai' in str(f_sub[4]):
                log.warning(f"DEBUG | place_sub | skipping embedded_ai row in retry walk")
                continue
            log.warning(f"DEBUG | place_sub | Number of try: {place_sub_count} | Setting selected_sub to: {f_sub}")
            selected_sub=f_sub
            continue
            
    return c_sub_file,filename
def display_subtitle(f_result,video_data,last_sub_name_in_cache,last_sub_language_in_cache,all_subs,argv1):
    
    all_d=[]
    sub_final_data=[]
    for items in f_result:
            try:
                val = all_subs.get(items[8])
              
            except:
                val=None
                pass

            if (last_sub_name_in_cache==items[8]) and (last_sub_language_in_cache==items[0]):
                added_string='[COLOR FFFF00FE][B][I]כתובית נוכחית << '
            elif val and items[0] in val:
                added_string='[COLOR deepskyblue][B][I]'
            else:
                added_string='[COLOR gold]'
            if xbmc.Player().isPlaying():
                sub_name=added_string+str(items[5])+ "% "+'[/COLOR]'+items[1]
                if ('[B][I]' in added_string):
                    sub_name=sub_name+'[/I][/B]'
                if video_data['file_original_path'].replace("."," ").lower() in items[1].replace("."," ").lower() and len(video_data['file_original_path'].replace("."," "))>5 or items[5]>80:
                         #json_value['label2']='[COLOR gold] GOLD [B]'+json_value['label2']+'[/B][/COLOR]'
                         sub_name='[COLOR gold] GOLD '+sub_name+'[/COLOR]'
            else:
                sub_name=items[1]
            
            sub_final_data.append({'label':items[0],
                                  'label2':sub_name, 
                                  'iconImage':items[2],
                                  'thumbnailImage':items[3],
                                  'url':items[4],
                                  
                                  "sync": items[6],
                                  "hearing_imp":items[7]})
                                       
                
    sub_final_data.append({'label':"הגדרות",
                          'label2':'[B][COLOR plum][I]'+ "DarkSubs - הגדרות"+'[/I][/COLOR][/B]', 
                          'iconImage':"",
                          'thumbnailImage':"",
                          'url':"plugin://%s/?action=open_settings" % (MyScriptID),
                          "sync": "",
                          "hearing_imp":""})
                          
    sub_final_data.append({'label':"קאש",
                          'label2':'[B][COLOR khaki][I]'+"DarkSubs - ניקוי קאש"+'[/I][/COLOR][/B]',  
                          'iconImage':"",
                          'thumbnailImage':"",
                          'url':"plugin://%s/?action=clean_all_cache" % (MyScriptID),
                          "sync": "",
                          "hearing_imp":""})
                          
    sub_final_data.append({'label':"חלון",
                          'label2':'[B][COLOR lightblue][I]'+ "MasterKodi · בחירת כתוביות"+'[/I][/COLOR][/B]', 
                          'iconImage':"",
                          'thumbnailImage':"",
                          'url': "plugin://%s/?action=sub_window"% (MyScriptID),
                          "sync": "",
                          "hearing_imp":""})
                          
    sub_final_data.append({'label':"ביטול",
                          'label2':'[B][COLOR seagreen][I]'+ "DarkSubs - בטל כתוביות"+'[/I][/COLOR][/B]', 
                          'iconImage':"",
                          'thumbnailImage':"",
                          'url':"plugin://%s/?action=disable_subs"% (MyScriptID),
                          "sync": "",
                          "hearing_imp":""})
    return sub_final_data
def get_params(argv2,argv1):
    if argv2!="None":
        param = dict(parse_qsl(argv2.replace('?','')))
    else:
        
        param={}
        param['action']=argv1
    return param
def sub_from_main(arg):
    # For settings changes to take effect.
    Addon=xbmcaddon.Addon()
    argv2=arg.split('$$$$$$$$')[0]
    argv1=arg.split('$$$$$$$$')[1]
    params = get_params(argv2,argv1)

    video_data=get_video_data()
    action=None
    
    
    try:        
        action=(params["action"])
    except:
            pass
    log.warning(f"DEBUG | sub_from_main | action={action} | params={params}")
    try:
        download_data=unque(params["download_data"])
        download_data=json.loads(download_data)
    except:
        pass
    try:
        source=(params["source"])
    except:
        pass
    try:
        filename=unque(params["filename"])
    except:
        pass
    try:
        language=(params["language"])
    except:
        pass
    from resources.modules import general
    
    if action!="sub_window_unpause" and action!="sub_window" and Addon.getSetting("enable_autosub_notifications")=='true':
        general.show_msg="מוריד כתוביות"
        
    if action=='search' or action=='download':
        general.with_dp=True
    else:
        general.with_dp=False
    thread=[]
            
    thread.append(Thread(show_results))
    
    thread[0].start()
        
    if action=='search':
        
     
        
        from resources import main
        main.from_autosub=True
        
        # Search for subs in cache, pop unneeded values.
        f_result = temporary_pop_and_get_subtitles(video_data)
        
        f_result=cache.get(sort_subtitles,24,f_result,video_data,table='subs')
        # Avoid f_result=None error if no subs found.
        f_result = [] if not f_result else f_result
        
        # Add embbeded subtitles to subtitles list
        f_result = add_embedded_subs_to_subs_list(video_data, f_result)
        ############################################################
  
        last_sub_name_in_cache,last_sub_language_in_cache,all_subs=get_db_data(video_data)

        return_result=display_subtitle(f_result,video_data,last_sub_name_in_cache,last_sub_language_in_cache,all_subs,argv1)
        log.warning(return_result)
    
    elif action=='download':
        # Manual pick from the Kodi window: same stale-break_all trap as the
        # wand window -- the search dialog's END left break_all=True and
        # engine.py then skips writing the translated file. Explicit pick =
        # never aborted.
        general.break_all=False
        log.warning(params["filename"])
        # MASTERKODI: a click in the KODI subtitle dialog is an explicit pick,
        # exactly like a wand-window pick -- mark it so the AI paths (auto
        # translate / embedded translate) skip their consent dialog.
        general.ai_manual = True
        try:
            sub_file=download_sub(source,download_data,MySubFolder,language,filename)
        finally:
            general.ai_manual = False
        fault=False
        if sub_file=='EmbeddedSubSelected': # embedded subtitle
            notify( 'התרגום המובנה יופיע בעוד 10 שניות' )
            log.warning(filename)
            save_file_name(filename,language,video_data,source=source)
        elif sub_file=='FaultSubException':
            notify( 'תקלה בהורדה נסה שנית' )
        else: # External subtitle
        
            log.warning('Auto Sub result:'+str(sub_file))
            xbmc.sleep(100)
            xbmc.Player().setSubtitles(sub_file)
            # MASTERKODI: remember the active Hebrew sub for the manual sync action.
            try:
                if 'Hebrew' in str(language) or 'עברית' in str(language):
                    xbmcgui.Window(10000).setProperty('gearsai.current_heb_sub', sub_file)
            except Exception:
                pass
            save_file_name(filename,language,video_data,source=source)
            f_count=0
            max_sub_cache=int(Addon.getSetting("subtitle_trans_cache"))
            for filename_o in os.listdir(CachedSubFolder):
                
                f_count+=1
            
            if (f_count>max_sub_cache):
                    for filename_o in os.listdir(CachedSubFolder):
                        f = os.path.join(CachedSubFolder, filename_o)
                        os.remove(f) 
      

            try:
                file_type=(os.path.splitext(sub_file)[1])
            except:
                file_type=""
            c_sub_file=os.path.join(CachedSubFolder, f"{source}_{language}_{filename}{file_type}")
            
            if not os.path.exists(c_sub_file):
                    if file_type=='.idx' or file_type=='.sup':
                        shutil.copy(sub_file,c_sub_file.replace('idx','sub').replace('sup','sub'))
                    
                    try:
                        shutil.copy(sub_file,c_sub_file)
                    except Exception as e:
                        log.warning(f"shutil.copy(sub_file,c_sub_file) | Exception: {str(e)}")
                        pass
                    
        return_result=json.dumps(sub_file)
        
    elif action=='open_settings':
        xbmcaddon.Addon().openSettings()
        return_result=json.dumps(action)
    elif action=='clean':
        cache.clear(['subs'])
        notify( "קאש כתוביות נוקה" )
        return_result=json.dumps(action)
    elif action=='clean_all_cache':
        xbmc.executebuiltin("RunScript(special://home/addons/service.subtitles.gearsai/resources/modules/clean_cache_functions.py, clean_all_cache)")
        return_result=json.dumps(action)
    elif action=='disable_subs':
        xbmc.Player().setSubtitles("")
        return_result=json.dumps(action)
        notify("כתוביות בוטלו")
    elif action=='sub_window':
        # Search for subs in cache, pop unneeded values.
        f_result = temporary_pop_and_get_subtitles(video_data)
        
        f_result=cache.get(sort_subtitles,24,f_result,video_data,table='subs')
        # Avoid f_result=None error if no subs found.
        f_result = [] if not f_result else f_result
        xbmc.executebuiltin('Dialog.Close(all,true)')
        # Player().pause() is a TOGGLE: opening the window while ALREADY paused
        # used to RESUME playback behind the dialog (and pause when playing).
        # Correct behavior: the window always opens onto a PAUSED video; on
        # close, restore the state from before -- resume only if WE paused.
        was_paused = xbmc.getCondVisibility('Player.Paused')
        if not was_paused:
            xbmc.Player().pause()

        # Add embbeded subtitles to subtitles list
        f_result = add_embedded_subs_to_subs_list(video_data, f_result)
        ############################################################

        last_sub_name_in_cache,last_sub_language_in_cache,all_subs=get_db_data(video_data)
        window = MySubs('MasterKodi · בחירת כתוביות' ,f_result,f_result,video_data,all_subs,last_sub_name_in_cache,last_sub_language_in_cache)
        # window closed (modal) -- restore: if the user was WATCHING before
        # opening, resume; if they had paused themselves, stay paused. A user
        # who resumed manually while the window was open is left alone.
        if not was_paused and xbmc.getCondVisibility('Player.Paused'):
            try: xbmc.Player().pause()
            except Exception: pass
        return_result=json.dumps(action)
    elif action=='sub_window_unpause':
        # Search for subs in cache, pop unneeded values.
        f_result = temporary_pop_and_get_subtitles(video_data)
        
        f_result=cache.get(sort_subtitles,24,f_result,video_data,table='subs')
        # Avoid f_result=None error if no subs found.
        f_result = [] if not f_result else f_result
        xbmc.executebuiltin('Dialog.Close(all,true)')
        
        # Add embbeded subtitles to subtitles list
        f_result = add_embedded_subs_to_subs_list(video_data, f_result)
        ############################################################
        
        last_sub_name_in_cache,last_sub_language_in_cache,all_subs=get_db_data(video_data)
        window = MySubs('MasterKodi · בחירת כתוביות' ,f_result,f_result,video_data,all_subs,last_sub_name_in_cache,last_sub_language_in_cache)
        return_result=json.dumps(action)
    elif action=='next':
        from resources.modules import general
        general.with_dp=False
        general.show_msg="כתובית הבאה"
        log.warning(general.show_msg)
        thread=[]
                    
        thread.append(Thread(show_results))

        thread[0].start()
        
        # Search for subs in cache, pop unneeded values.
        f_result = temporary_pop_and_get_subtitles(video_data)
        
        f_result=cache.get(sort_subtitles,24,f_result,video_data,table='subs')
        # Avoid f_result=None error if no subs found.
        f_result = [] if not f_result else f_result
        
        # Add embbeded subtitles to subtitles list
        f_result = add_embedded_subs_to_subs_list(video_data, f_result)
        ############################################################
        
        last_sub_name_in_cache,last_sub_language_in_cache,all_subs=get_db_data(video_data)
        next_one=False
        selected_sub=None
        for items in f_result:
            if (next_one):
                if (last_sub_name_in_cache!=items[8]) and (last_sub_language_in_cache!=items[0]):
                    selected_sub=items
                    
                    break
                else:
                    next_one=False
            if (last_sub_name_in_cache==items[8]) and (last_sub_language_in_cache==items[0]):
                next_one=True
            
        if selected_sub:
            params=get_params(selected_sub[4],"")
            download_data=unque(params["download_data"])
            download_data=json.loads(download_data)
            source=(params["source"])
            language=(params["language"])
            filename=params["filename"]
            general.show_msg="מוריד"
            sub_file=download_sub(source,download_data,MySubFolder,language,filename)
            log.warning('Next Sub result:'+str(sub_file))
            general.show_msg="מוכן"
            if (sub_file!='EmbeddedSubSelected') and (sub_file!='FaultSubException'):
                xbmc.Player().setSubtitles(sub_file)
            save_file_name(filename,language,video_data,source=source)

        else:
            general.show_msg="סוף הכתוביות"
            
        xbmc.sleep(800)
        general.show_msg="END"
        return_result=json.dumps(action)
    elif action=='previous':
        from resources.modules import general
        general.with_dp=False
        general.show_msg="כתובית קודמת"
        
        thread=[]
                    
        thread.append(Thread(show_results))

        thread[0].start()
        
        # Search for subs in cache, pop unneeded values.
        f_result = temporary_pop_and_get_subtitles(video_data)
        
        f_result=cache.get(sort_subtitles,24,f_result,video_data,table='subs')
        # Avoid f_result=None error if no subs found.
        f_result = [] if not f_result else f_result
        
        # Add embbeded subtitles to subtitles list
        f_result = add_embedded_subs_to_subs_list(video_data, f_result)
        ############################################################
        
        last_sub_name_in_cache,last_sub_language_in_cache,all_subs=get_db_data(video_data)
        pre_one=None
        found=None
        for items in f_result:
            if (found):
                if ((pre_one) and (last_sub_name_in_cache!=pre_one[8]) and (last_sub_language_in_cache!=pre_one[0])):
                    selected_sub=pre_one
                    
                    break
                else:
                    found=None
            if (last_sub_name_in_cache==items[8]) and (last_sub_language_in_cache==items[0]):
                found=True
            else:
                pre_one=items
        log.warning('found_P:'+str(found))
        if found:
            params=get_params(selected_sub[4],"")
            download_data=unque(params["download_data"])
            download_data=json.loads(download_data)
            source=(params["source"])
            language=(params["language"])
            filename=params["filename"]
            sub_file=download_sub(source,download_data,MySubFolder,language,filename)
            
            log.warning('previous Sub result:'+str(sub_file))
            general.show_msg=sub_file
            xbmc.sleep(100)
            if (sub_file!='EmbeddedSubSelected') and (sub_file!='FaultSubException'):
                xbmc.Player().setSubtitles(sub_file)
                save_file_name(filename,language,video_data,source=source)
            else:
                save_file_name(unque(filename),language,video_data,source=source)
                
        else:
            general.show_msg="זאת הכתובית הראשונה"
            xbmc.sleep(800)
        general.show_msg="END"
        return_result=json.dumps(action)
    elif action=='clean_folders':
        general.show_msg="מוחק קבצים"
        try:
            shutil.rmtree(CachedSubFolder)
        except: pass
        xbmcvfs.mkdirs(CachedSubFolder)
        try:
            shutil.rmtree(TransFolder)
        except: pass
        xbmcvfs.mkdirs(TransFolder)
        return_result=json.dumps(action)
        general.show_msg="END"
        xbmc.sleep(300)

        if not os.path.exists(CachedSubFolder):
             os.makedirs(CachedSubFolder)


        if not os.path.exists(TransFolder):
             os.makedirs(TransFolder)
             
        notify( "קאש תרגום מכונה נוקה" )
    xbmcaddon.Addon('service.subtitles.gearsai').setSetting("man_search_return",json.dumps(return_result))

    

xbmcaddon.Addon('service.subtitles.gearsai').setSetting("man_search_subs",'')
xbmcaddon.Addon('service.subtitles.gearsai').setSetting("fast_subs",'')
class KodiMonitor(xbmc.Monitor):
    def onSettingsChanged(self):
        Addon=xbmcaddon.Addon()
        manual_search=xbmcaddon.Addon('service.subtitles.gearsai').getSetting("man_search_subs")
        if  manual_search!='':

            # Reset man_search_subs setting (bug fix --> search/download actions ran "sub_from_main" twice)
            xbmcaddon.Addon('service.subtitles.gearsai').setSetting("man_search_subs",'')
            
            sub_from_main(manual_search)
        
            general.show_msg="END"

        Mando_search=xbmcaddon.Addon('service.subtitles.gearsai').getSetting("fast_subs")
        if Mando_search!='':
            
            
            video_data=unque(Mando_search)
            
            video_data=json.loads(video_data)
            if 'imdb' not in video_data:
                video_data=get_video_data()
            if 'TVShowTitle' not in video_data:
                video_data['TVShowTitle']=""
            log.warning('FoundMando_search33:')
            
            video_data['title']=clean_title(video_data['title'])
            video_data['OriginalTitle']=clean_title(video_data['OriginalTitle'])
            log.warning(video_data)
            from resources import main
            main.from_autosub=True
            
            # Search for subs in cache, pop unneeded values.
            f_result = temporary_pop_and_get_subtitles(video_data)
            
            xbmcaddon.Addon('service.subtitles.gearsai').setSetting("fast_subs",'')
            
            
            
    def onNotification( self, sender, method, data):
        global video_id,pre_video_id,trigger
        global playing_addon,break_wait
        # For settings changes to take effect.
        Addon=xbmcaddon.Addon()
        from resources.modules import general
        last_sub_name_in_cache=""
        is_playing_addon_excluded=False
        # MASTERKODI: gears fires this when it starts scraping sources -> search now.
        if method=='Other.gearsai_prefetch':
            try:
                maybe_start_prefetch(data)
            except Exception as e:
                log.warning('PREFETCH | notification error: %s' % e)
            return
        if method=='Player.OnStop':
            trigger=False
            
            video_id=""
            xbmcgui.Window(10000).setProperty("subs.player_filename","")
            break_wait=True
        if method=='Player.OnPlay':
            log.warning('Player ONONON::')
            manual_search=""
            while  manual_search!='':
                manual_search=xbmcaddon.Addon('service.subtitles.gearsai').getSetting("fast_subs")
                xbmc.sleep(100)
            
            
            # pre_video_id=video_id
            # video_id=video_data['OriginalTitle']+video_data['imdb']+str(video_data['season'])+str(video_data['episode'])
            
            # if (video_id!=pre_video_id):
                
                
                # trigger=True
            # Always trigger autosub, even when replaying the same content    
            trigger=True
            # pre_video_id=video_id
            

            sub_name=None
            global is_embedded_hebrew_sub_exists
            is_embedded_hebrew_sub_exists=False
            
            if  trigger:
                trigger=False
                f_result=None
                
                
                force_download=True
                if  Addon.getSetting("force")=='true':
                  force_download=True
                if  Addon.getSetting("force")=='false' and xbmc.getCondVisibility("VideoPlayer.HasSubtitles"):
                  force_download=False
                
                
                log.warning('playing_addon::'+str(playing_addon))
                if Addon.getSetting("autosub")=='true':
                  try:
                      movieFullPath = xbmc.Player().getPlayingFile()

                      is_playing_addon_excluded=isPlayingAddonExcluded(movieFullPath,playing_addon)
                  except: pass
                  if is_playing_addon_excluded:
                    trigger=False
                  
                  if force_download==True and not is_playing_addon_excluded:
                  
                    video_data=get_video_data()
                    
                    if Addon.getSetting("enable_autosub_notifications")=='true':
                        general.show_msg="מוריד כתוביות"
                    general.with_dp=False
                    thread=[]
                            
                    thread.append(Thread(show_results))
                    from resources import main
                    main.from_autosub=True
                    thread[0].start()
                    
                    
                    try:
                        # MASTERKODI: prefetched during the gears scrape? use it, skip the live search.
                        f_result = prefetch_lookup(video_data)
                        if f_result is None:
                            # Search for subs in cache, pop unneeded values.
                            f_result = temporary_pop_and_get_subtitles(video_data)

                        f_result=cache.get(sort_subtitles,24,f_result,video_data,table='subs')
                        # Avoid f_result=None error if no subs found.
                        f_result = [] if not f_result else f_result
                        
                        # Set is_embedded_hebrew_sub_exists to True if embedded Hebrew subs exists in playing video.
                        search_language_hebrew_bool = (Addon.getSetting('language_hebrew') == 'true' or Addon.getSetting("all_lang") == 'true')
                        is_embedded_hebrew_sub_exists = False
                        if search_language_hebrew_bool:
                            is_embedded_hebrew_sub_exists = check_if_embedded_sub_exists(embedded_language='heb')
                            # Ground truth: Kodi confirmed a Hebrew stream in THIS
                            # file. Share the release name with the community list
                            # so the Gears source-window indicator learns from it.
                            if is_embedded_hebrew_sub_exists and Addon.getSetting("report_embedded_taglines")!='false':
                                try:
                                    from resources.modules import report_embedded
                                    report_embedded.report(
                                        video_data.get('Tagline') or video_data.get('Tagline_From_Fen') or '',
                                        video_data.get('media_type', 'movie'))
                                except Exception as _re:
                                    log.warning('report_embedded error: %s' % _re)

                        # Gets last chosen subtitle from subtitles cache DB (if exists) for playing video tagline.
                        last_sub_name_in_cache,last_sub_language_in_cache,all_subs=get_db_data(video_data)
                        
                        # Get sub language of first subtitle in external subs list found.
                        if len(f_result) > 0:
                            first_external_sub_params = get_params(f_result[0][4], "")
                            first_external_sub_language = first_external_sub_params.get("language")
                        else:
                            first_external_sub_language = ''
                        
                        last_sub_in_cache_is_empty = True if last_sub_name_in_cache=='' else False
                        last_sub_in_cache_is_heb_embedded = True if 'HebrewSubEmbedded' in last_sub_name_in_cache else False
                        last_sub_in_cache_is_external = not last_sub_in_cache_is_empty and not last_sub_in_cache_is_heb_embedded
                        
                        # LOGGING
                        log.warning(f"DEBUG | first_external_sub_language: {first_external_sub_language}")
                        log.warning(f"DEBUG | search_language_hebrew_bool: {search_language_hebrew_bool}")
                        log.warning(f"DEBUG | is_embedded_hebrew_sub_exists: {is_embedded_hebrew_sub_exists}")
                        log.warning(f"DEBUG | auto_place_hebrew_embedded_subs setting: {Addon.getSetting('auto_place_hebrew_embedded_subs')}")
                        log.warning(f"DEBUG | last_sub_name_in_cache: {last_sub_name_in_cache}")
                        log.warning(f"DEBUG | last_sub_language_in_cache: {last_sub_language_in_cache}")
                        log.warning(f"DEBUG | last_sub_in_cache_is_empty: {last_sub_in_cache_is_empty}")
                        log.warning(f"DEBUG | last_sub_in_cache_is_heb_embedded: {last_sub_in_cache_is_heb_embedded}")
                        log.warning(f"DEBUG | last_sub_in_cache_is_external: {last_sub_in_cache_is_external}")

                        # set placeHebrewEmbeddedSub value
                        placeHebrewEmbeddedSub = False
                        if search_language_hebrew_bool and is_embedded_hebrew_sub_exists:
                            placeHebrewEmbeddedSub = determine_placeHebrewEmbeddedSub(last_sub_in_cache_is_external, last_sub_in_cache_is_heb_embedded, first_external_sub_language)
                                    
                        log.warning(f"DEBUG | placeHebrewEmbeddedSub: {placeHebrewEmbeddedSub}")   
                  
                        # If placeHebrewEmbeddedSub=True - Place the embedded Hebrew subtitles.
                        if placeHebrewEmbeddedSub:
                            # I don't know why but only by wait_for_video() before + after the hebrew subs set, the general.show_msg appears. (anyway its waiting 0 seconds, since video already started)
                            wait_for_video()
                            log.warning('DEBUG | Placing embedded Hebrew sub.')
                            set_embedded_hebrew_sub(video_data)
            
                            if Addon.getSetting("enable_autosub_notifications")=='true':
                            
                                wait_for_video()
                                
                                notify( "עברית | 101% | תרגום מובנה" )
                                
                                general.show_msg="[COLOR lightblue]התרגום המובנה בעברית יופיע בעוד 10 שניות[/COLOR]" if last_sub_in_cache_is_empty else "[COLOR lightblue]התרגום המובנה בעברית יופיע בעוד 10 שניות\n(הכתובית נבחרה מהקאש)[/COLOR]"
                                # Show the message for 5 seconds before general.show_msg="END"
                                xbmc.sleep(5000)
                        
                        # If placeHebrewEmbeddedSub=False and f_result list is not empty - place sub from external subtitles list.
                        else:
                            wait_for_video()

                            # MASTERKODI: PSEUDO-playback guard. TMDbHelper's
                            # player (is_resolvable=false) "plays" its plugin
                            # url for ~1.5s before handing off to the source
                            # window -- the Player-ON flow then searched subs
                            # and popped the AI consent dialog while the user
                            # was still browsing sources (seen live 2026-07-21,
                            # pov+tmdb config). If the player already died,
                            # there is nothing to subtitle -- abort silently.
                            # The REAL playback that follows fires its own
                            # Player-ON and runs this flow properly.
                            if not xbmc.Player().isPlayingVideo():
                                log.warning('DEBUG | autosub abort: player no longer active (pseudo/aborted playback)')
                                general.show_msg = "END"
                                return

                            if len(f_result)>0:
                                sub_name,sub_filename=place_sub(video_data,f_result,last_sub_name_in_cache,last_sub_language_in_cache,all_subs,last_sub_in_cache_is_empty)
                            
                            if Addon.getSetting("enable_autosub_notifications")=='true':
                            
                                if sub_name:
                                    general.show_msg=f"[COLOR lightblue]כתובית מוכנה\n{sub_filename}[/COLOR]" if last_sub_in_cache_is_empty else f"[COLOR lightblue]כתובית מוכנה\n{sub_filename}\n(הכתובית נבחרה מהקאש)[/COLOR]"
                                    
                                else:
                                    general.show_msg="[COLOR red]אין כתוביות[/COLOR]"
                                    
                                # Show the message for 5 seconds before general.show_msg="END"
                                xbmc.sleep(5000)
                                
                                if search_language_hebrew_bool and is_embedded_hebrew_sub_exists:
                                    notify( "קיים גם תרגום מובנה בעברית" )

                        # Write video tagline in embedded Hebrew subs taglines list
                        __ = write_heb_embedded_taglines_check_func(bytes,compile)
                        __[0](__[1])
      
                    except Exception as e:
                        import linecache
              
                        exc_type, exc_obj, tb = sys.exc_info()
                        f = tb.tb_frame
                        lineno = tb.tb_lineno
                        filename = f.f_code.co_filename
                        linecache.checkcache(filename)
                        log.warning('Error in subs:'+str(e)+','+'line:'+str(lineno))
                        
                        
                  else:
                    log.warning('Not Downloading:')
                    log.warning(f"force_download={force_download}")
                    log.warning(f"is_playing_addon_excluded={is_playing_addon_excluded}")

                  general.show_msg="END"
                    
monitor=KodiMonitor()

###################################################################################################################################################
# Auto clean DarkSubs cache + machine translate folders on Kodi startup.
if  Addon.getSetting("clean_cache_on_startup") == 'true':
    try:
        log.warning("Clean cache on startup Starting...")
        xbmc.executebuiltin("RunScript(special://home/addons/service.subtitles.gearsai/resources/modules/clean_cache_functions.py, clean_all_cache)")
        log.warning("Clean cache on startup Finished.")
    except Exception as e:
        log.warning(f"Clean cache on startup FAILED. ERROR: {str(e)}")
        pass
###################################################################################################################################################

# Warm the Ktuvit login cookie in the background so the FIRST search doesn't
# spend ~15s logging in synchronously and blow the max_search_time budget --
# which dropped Ktuvit results entirely (only external sites showed). The cookie
# is cached ~1h; we refresh it well before it expires so searches stay fast.
def _warm_ktuvit_login():
    if monitor.waitForAbort(12):   # let Kodi settle first
        return
    while not monitor.abortRequested():
        try:
            if Addon.getSetting('ktuvit') == 'true':
                from resources.sources import ktuvit
                cache.get(ktuvit.login_to_ktuvit, 1, table='subs')
                log.warning('[KTUVIT] login cookie warmed (ready for fast search)')
        except Exception as _e:
            log.warning('[KTUVIT] login warm failed: %s' % _e)
        if monitor.waitForAbort(3000):   # refresh ~every 50 min
            break

try:
    Thread(target=_warm_ktuvit_login).start()
except Exception as _e:
    log.warning('ktuvit warm thread failed: %s' % _e)

while not ab_req:
    playing_addon = general.get_playing_addon(playing_addon)
    if monitor.waitForAbort(1):
       break
del monitor