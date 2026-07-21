# -*- coding: utf-8 -*-
########### Imports #####################
from modules import kodi_utils
from modules.kodi_utils import get_setting
import requests

########### Settings ####################
def is_wizdom_enabled():
    return get_setting('hebrew_subtitles.match_wizdom', 'true') == 'true'

########### Constants ###################
WIZDOM_API_SEARCH_URL = "https://wizdom.xyz/api/search"
DEFAULT_REQUEST_TIMEOUT = 5


def search_for_subtitles(media_metadata):
    """
    Search for Hebrew subtitles on wizdom.xyz.
    """
    if not is_wizdom_enabled():
        kodi_utils.logger("POV-HEBSUBS", "Wizdom search disabled. Skipping...")
        return []
        
    media_type = media_metadata.get("media_type")
    title = media_metadata.get("title", "")
    season = media_metadata.get("season", 0)
    episode = media_metadata.get("episode", 0)
    imdb_id = media_metadata.get("imdb_id", "")
    
    kodi_utils.logger("POV-HEBSUBS", f"[WIZDOM] Searching: {media_type} - {title} S{season}E{episode} imdb:{imdb_id}")
    
    # Build query parameters
    params = {
        'action': 'by_id',
        'imdb': imdb_id
    }
    
    if media_type == 'tv':
        params['season'] = str(season).zfill(2)
        params['episode'] = str(episode).zfill(2)
    
    try:
        response = requests.get(WIZDOM_API_SEARCH_URL, params=params, timeout=DEFAULT_REQUEST_TIMEOUT)
        response.raise_for_status()
        wizdom_response_json = response.json()
    except Exception as e:
        kodi_utils.logger("POV-HEBSUBS", f"[WIZDOM] Error: {str(e)}")
        return []
    
    wizdom_subtitles_list = []
    
    if wizdom_response_json:
        for wizdom_subtitle in wizdom_response_json:
            if isinstance(wizdom_subtitle, dict) and "versioname" in wizdom_subtitle:
                wizdom_subtitles_list.append(wizdom_subtitle["versioname"])
    
    kodi_utils.logger("POV-HEBSUBS", f"[WIZDOM] Found {len(wizdom_subtitles_list)} subtitles")
    return wizdom_subtitles_list
