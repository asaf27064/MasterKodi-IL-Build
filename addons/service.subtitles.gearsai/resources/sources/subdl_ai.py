# -*- coding: utf-8 -*-
# subdl.com as a NATIVE DarkSubs English provider. Wraps our aisubs/subdl.py so
# its English candidates compete in DarkSubs' unified match% ranking. When no
# Hebrew sub exists, DarkSubs can pick a subdl English sub as the best match and
# -- with translate_p = "MasterKodi AI (Gemini)" -- our engine translates it.
# English-only (subdl is queried with languages=EN), so it just enriches the
# pool of subs available for translation. Baked subdl key lives in aisubs/subdl.
import shutil
import xbmcaddon, os, xbmc
global global_var, site_id, sub_color  # global
global_var = []
from resources.modules import log
import requests, json
import urllib
from resources.modules.extract_sub import extract
from resources.modules.general import DEFAULT_REQUEST_TIMEOUT
import xbmcvfs
from resources.aisubs import subdl as _subdl
#########################################

que = urllib.parse.quote_plus
Addon = xbmcaddon.Addon()
MyScriptID = Addon.getAddonInfo('id')
xbmc_tranlate_path = xbmcvfs.translatePath
__profile__ = xbmc_tranlate_path(Addon.getAddonInfo('profile'))
MyTmp = xbmc_tranlate_path(os.path.join(__profile__, 'temp_subdl'))

site_id = '[subdl]'
sub_color = 'lightgreen'
#########################################


def get_subs(video_data, all_lang_override=False):
    # For settings changes to take effect.
    Addon = xbmcaddon.Addon()
    global global_var
    global_var = []
    log.warning('DEBUG | [subdl] | Searching subdl (English)')

    # English-only source -> only run when English is being searched (directly,
    # via all_lang, or via the retry-all-languages pass).
    english_on = (Addon.getSetting('language_english') == 'true'
                  or Addon.getSetting('all_lang') == 'true' or all_lang_override)
    if not english_on:
        return []

    imdb_id = video_data.get('imdb', '')
    media_type = video_data.get('media_type', '')
    title = video_data.get('OriginalTitle', '') or video_data.get('title', '')
    season = video_data.get('season', '')
    episode = video_data.get('episode', '')
    year = video_data.get('year', '')

    try:
        cands = _subdl.search_english(
            imdb_id=imdb_id, title=title,
            media_type=('episode' if media_type == 'tv' else 'movie'),
            season=season, episode=episode, year=year) or []
    except Exception as e:
        log.warning('DEBUG | [subdl] | search error: %s' % repr(e))
        return []

    subtitle_list = []
    for it in cands:
        link = it.get('download_link') or ''
        if not link:
            continue
        name = (it.get('name') or 'subdl').strip()
        characters_to_remove = '\\/:*?"<>|\''
        name = ''.join(c for c in name if c not in characters_to_remove) or 'subdl'
        hi = 'true' if it.get('hi') else 'false'

        download_data = {'filename': name, 'download_link': link,
                         'format': 'zip', 'hearing_imp': hi}
        url = "plugin://%s/?action=download&filename=%s&language=English&download_data=%s&source=subdl_ai" % (
            MyScriptID, que(name), que(json.dumps(download_data)))
        subtitle_list.append({
            'url': url,
            'label': 'English',
            'label2': site_id + ' ' + name,
            'iconImage': '0',
            'thumbnailImage': 'en',
            'hearing_imp': hi,
            'site_id': site_id,
            'sub_color': sub_color,
            'filename': name,
            'sync': 'false',
        })

    global_var = subtitle_list


def download(download_data, MySubFolder):
    try:
        shutil.rmtree(MyTmp)
    except Exception:
        pass
    xbmcvfs.mkdirs(MyTmp)

    link = download_data['download_link']
    filename = download_data['filename']
    fmt = download_data.get('format', 'zip')
    subFile = os.path.join(MyTmp, "%s.%s" % (str(filename), fmt))
    log.warning('DEBUG | [subdl] | downloading %s -> %s' % (link, subFile))

    try:
        r = requests.get(link, headers={'User-Agent': _subdl._ua()},
                         timeout=DEFAULT_REQUEST_TIMEOUT)
        r.raise_for_status()
    except Exception as e:
        log.warning('DEBUG | [subdl] | download failed: %s' % repr(e))
        return '0'
    with open(subFile, 'wb') as handle:
        handle.write(r.content)

    sub_file = extract(subFile, MySubFolder)
    return sub_file if (sub_file and sub_file != '0') else '0'
