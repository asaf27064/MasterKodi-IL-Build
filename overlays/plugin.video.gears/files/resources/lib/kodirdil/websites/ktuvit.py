# -*- coding: utf-8 -*-
########### Imports #####################
from modules import kodi_utils
from caches.settings_cache import get_setting
import json
import re
import requests

########### Settings ####################
def is_ktuvit_enabled():
    return get_setting('gears.hebrew_subtitles.match_ktuvit', 'true') == 'true'

########### Constants ###################
KTUVIT_URL = "https://www.ktuvit.me"
LOGIN_URL = f"{KTUVIT_URL}/Services/MembershipService.svc/Login"
SEARCH_URL = f"{KTUVIT_URL}/Services/ContentProvider.svc/SearchPage_search"
MOVIE_INFO_URL = f"{KTUVIT_URL}/MovieInfo.aspx"
EPISODE_INFO_URL = f"{KTUVIT_URL}/Services/GetModuleAjax.ashx?"
DEFAULT_REQUEST_TIMEOUT = 6


def search_for_subtitles(media_metadata):
    """
    Search for Hebrew subtitles on ktuvit.me.

    Args:
        media_metadata (dict): Dictionary containing media information.
    
    Returns:
        list: List of subtitle names found.
    """
    if not is_ktuvit_enabled():
        kodi_utils.logger("Gears-HEBSUBS", "Ktuvit search disabled. Skipping...")
        return []
        
    media_type = media_metadata.get("media_type")
    title = media_metadata.get("title", "").lower()
    season = media_metadata.get("season", 0)
    episode = media_metadata.get("episode", 0)
    imdb_id = media_metadata.get("imdb_id", "")
    tmdb_id = media_metadata.get("tmdb_id", "")

    kodi_utils.logger("Gears-HEBSUBS", f"[KTUVIT] Searching: {media_type} - {title} S{season}E{episode}")

    # Ktuvit indexes titles by their ENGLISH/original name, but the title we get
    # here is usually the Hebrew display name (e.g. "ריצ'ר") which returns 0
    # results. Resolve the English original name from TMDb -- but ONLY when the
    # title isn't already English (ASCII), to avoid a needless TMDb call. Same
    # logic All Subs Plus uses (its isascii() short-circuit).
    if title and str(title).isascii():
        english_title = title
    else:
        english_title = get_english_title_from_tmdb(tmdb_id, media_type) or title

    # Apply title mapping for known mismatches
    english_title = get_matching_ktuvit_name(english_title)

    try:
        # Search for movie/show in Ktuvit search page (by English name)
        ktuvit_search_response = ktuvit_search_request(english_title, media_type)

        # Get matching Ktuvit ID from search results
        ktuvit_page_id = get_ktuvit_id(ktuvit_search_response, imdb_id, english_title)

        if ktuvit_page_id == '':
            return []
            
        # Get login cookie from Ktuvit
        ktuvit_login_cookie = login_to_ktuvit()

        # Set API parameters based on media_type
        ktuvit_api_url, headers, params = create_headers_params(media_type, ktuvit_page_id, season, episode)

        # Search subtitles in Ktuvit
        ktuvit_response = requests.get(
            ktuvit_api_url, 
            headers=headers, 
            params=params, 
            cookies=ktuvit_login_cookie, 
            timeout=DEFAULT_REQUEST_TIMEOUT
        ).content
        
        # Extract subtitles list from response
        ktuvit_subtitles_list = extract_subtitles_list(ktuvit_response)
        
        return ktuvit_subtitles_list
        
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"[KTUVIT] Error: {str(e)}")
        return []
    
    
# Cache the Ktuvit login cookie in-memory for ~50 min so we don't log in on
# every source-window search -- the login is the slow part (used to blow the
# search time budget, dropping Ktuvit results entirely).
import time as _time
_ktuvit_cookie_cache = {'cookies': None, 'ts': 0}


def login_to_ktuvit():
    """Login to ktuvit.me and return cookies (cached ~50 min)."""
    now = _time.time()
    if _ktuvit_cookie_cache['cookies'] and (now - _ktuvit_cookie_cache['ts']) < 3000:
        return _ktuvit_cookie_cache['cookies']

    headers = {
        'authority': 'www.ktuvit.me',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'x-requested-with': 'XMLHttpRequest',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'content-type': 'application/json',
        'origin': KTUVIT_URL,
    }

    email = 'darksubsil1@gmail.com'
    password = 'ZkCyMZfsIHt9HQK4eL8bbfaxXoNBjmFO9w39kt/gA14='
    data = f'{{"request":{{"Email":"{email}","Password":"{password}"}}}}'

    # Retry a couple of times -- a transient failure used to yield no cookies
    # and an empty Ktuvit result for the whole session.
    cookies_dict = {}
    for _attempt in range(3):
        try:
            response = requests.post(LOGIN_URL, headers=headers, data=data, timeout=DEFAULT_REQUEST_TIMEOUT)
            cookies_dict = {c.name: c.value for c in response.cookies}
            if cookies_dict:
                break
        except Exception:
            pass

    if cookies_dict:
        _ktuvit_cookie_cache['cookies'] = cookies_dict
        _ktuvit_cookie_cache['ts'] = now
    return cookies_dict
    
    
_english_title_cache = {}


def get_english_title_from_tmdb(tmdb_id, media_type):
    """Resolve the ENGLISH original title from TMDb (Ktuvit searches by it).
    Cached per tmdb_id. Returns '' on any problem (caller falls back)."""
    try:
        if not tmdb_id:
            return ''
        ck = (str(tmdb_id), media_type)
        if ck in _english_title_cache:
            return _english_title_cache[ck]
        try:
            from modules.settings import tmdb_api_key
            key = tmdb_api_key()
        except Exception:
            key = '2fec88ea9c5507165266b6e1f8eaaa92'
        if not key:
            key = '2fec88ea9c5507165266b6e1f8eaaa92'
        kind = 'movie' if media_type == 'movie' else 'tv'
        url = f'https://api.themoviedb.org/3/{kind}/{tmdb_id}?api_key={key}&language=en-US'
        r = requests.get(url, timeout=DEFAULT_REQUEST_TIMEOUT)
        name = ''
        if r.status_code == 200:
            j = r.json()
            name = (j.get('original_name') or j.get('original_title')
                    or j.get('name') or j.get('title') or '')
        _english_title_cache[ck] = name
        return name
    except Exception:
        return ''


def ktuvit_search_request(title, media_type):
    """Search for a title on Ktuvit."""
    headers = {
        'authority': 'www.ktuvit.me',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'x-requested-with': 'XMLHttpRequest',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'content-type': 'application/json',
        'origin': KTUVIT_URL,
        'referer': f'{KTUVIT_URL}/Search.aspx',
    }
    
    search_type = '0' if media_type == 'movie' else '1'
    with_subs_only = media_type == 'movie'

    data = {
        "request": {
            "FilmName": title,
            "Actors": [],
            "Studios": None,
            "Directors": [],
            "Genres": [],
            "Countries": [],
            "Languages": [],
            "Year": "",
            "Rating": [],
            "Page": 1,
            "SearchType": search_type,
            "WithSubsOnly": with_subs_only
        }
    }
    
    response = requests.post(SEARCH_URL, headers=headers, json=data, timeout=DEFAULT_REQUEST_TIMEOUT)
    return response.json()
    

def extract_imdb_id_from_result(result):
    """Extract IMDb ID from a Ktuvit search result."""
    imdb_link = str(result.get('IMDB_Link', '')).rstrip("/")
    imdb_parts = imdb_link.split("/")
    imdb_id = imdb_parts[-1] if imdb_parts else ''
    
    if not imdb_id.startswith("tt"):
        imdb_id = str(result.get('ImdbID', ''))
        
    return imdb_id


def get_ktuvit_id(ktuvit_search_response, imdb_id, title):
    """Get the Ktuvit page ID for a media item."""
    try:
        ktuvit_results = json.loads(ktuvit_search_response['d'])['Films']
    except:
        return ''
        
    ktuvit_page_id = ''

    if imdb_id:
        for result in ktuvit_results:
            imdb_id_from_ktuvit = extract_imdb_id_from_result(result)
            if imdb_id_from_ktuvit in imdb_id:
                ktuvit_page_id = result['ID']
                break
    
    return ktuvit_page_id


def create_headers_params(media_type, ktuvit_page_id, season, episode):
    """Create headers and parameters for subtitle search request."""
    referer_url = f"{MOVIE_INFO_URL}?ID={ktuvit_page_id}"
    params = {}

    if media_type == 'movie':
        headers = {
            'authority': 'www.ktuvit.me',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9',
            'referer': referer_url,
        }
        api_url = f"{MOVIE_INFO_URL}?ID={ktuvit_page_id}"
    else:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:81.0) Gecko/20100101 Firefox/81.0',
            'Accept': 'text/html, */*; q=0.01',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': referer_url,
        }
        params = (
            ('moduleName', 'SubtitlesList'),
            ('SeriesID', ktuvit_page_id),
            ('Season', str(season).zfill(2)),
            ('Episode', str(episode).zfill(2)),
        )
        api_url = EPISODE_INFO_URL
    
    return api_url, headers, params


def extract_subtitles_list(ktuvit_response):
    """Extract subtitles list from Ktuvit API response."""
    ktuvit_subtitles_list = []
    
    table_row_regex = '<tr>(.+?)</tr>'
    table_rows = re.compile(table_row_regex, re.DOTALL).findall(ktuvit_response.decode('utf-8'))
    
    for table_row in table_rows:
        subtitle_row_regex = r'<div style="float.+?>\s*(?:<i.*?</i>\s*)?(.*?)<br />.+?data-subtitle-id="(.+?)"'
        extracted_subtitle_row = re.compile(subtitle_row_regex, re.DOTALL).findall(table_row)
        
        if len(extracted_subtitle_row) == 0:
            continue
    
        extracted_subtitle_name = extracted_subtitle_row[0][0]
        extracted_subtitle_name = (
            extracted_subtitle_name.strip()
            .replace('\n', '')
            .replace('\r', '')
            .replace('\t', '')
            .replace(' ', '.')
        )

        # Remove problematic characters
        characters_to_remove = '\\/:*?"<>|\''
        extracted_subtitle_name = ''.join(c for c in extracted_subtitle_name if c not in characters_to_remove)
            
        ktuvit_subtitles_list.append(extracted_subtitle_name)
    
    return ktuvit_subtitles_list


def get_ktuvit_original_title_mapping():
    """Get title mapping for known Ktuvit mismatches."""
    try:
        url = 'https://kodi7rd.github.io/repository/other/DarkSubs_Ktuvit_Title_Mapping/darksubs_ktuvit_title_mapping.json'
        response = requests.get(url, timeout=DEFAULT_REQUEST_TIMEOUT)
        return response.json()
    except:
        return {}


def get_matching_ktuvit_name(title):
    """Get the corrected Ktuvit title if a mapping exists."""
    try:
        mapping = get_ktuvit_original_title_mapping()
        return mapping.get(title, title).lower()
    except:
        return title
