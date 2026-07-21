# -*- coding: utf-8 -*-
########### Imports #####################
from modules import kodi_utils
from modules.kodi_utils import get_setting
import requests, random

########### Settings ####################
def is_opensubtitles_enabled():
    return get_setting('hebrew_subtitles.match_opensubtitles', 'true') == 'true'

########### Constants ###################
# The legacy rest.opensubtitles.org API was shut down (resolved to a broken
# host). We now use the current api.opensubtitles.com REST API -- the same one
# MasterKodi AI Subs uses successfully -- with baked community keys so it works
# out of the box. requests follows the API's canonical-order 301 redirect
# automatically.
OPENSUBTITLES_API_URL = "https://api.opensubtitles.com/api/v1/subtitles"
DEFAULT_REQUEST_TIMEOUT = 6
USER_AGENT = "MasterKodiGears v1.0"
BAKED_KEYS = [
    '9bOXkEUkqg5fHTWCrOQ6pYLBlHtRd9fM',
    '3MeVyIKDINfXJPKMTAWtuGzJDYcAYTnb',
    'yh4v2XkLaz4k341i5KW3a3yma46DafjE',
    'P83ZTw2Ec3XP6h2uXwTqOI9PWbo3cpGL',
]


def _api_key():
    # Optional user key from settings, else a random baked community key.
    return get_setting('hebrew_subtitles.opensubtitles_apikey', '') or random.choice(BAKED_KEYS)


def search_for_subtitles(media_metadata):
    """Search Hebrew subtitles on OpenSubtitles.com. Returns a list of release
    names (strings)."""
    if not is_opensubtitles_enabled():
        kodi_utils.logger("POV-HEBSUBS", "OpenSubtitles search disabled. Skipping...")
        return []

    media_type = media_metadata.get("media_type")
    title = media_metadata.get("title", "")
    season = media_metadata.get("season", 0)
    episode = media_metadata.get("episode", 0)
    imdb_id = str(media_metadata.get("imdb_id", "") or "")

    kodi_utils.logger("POV-HEBSUBS", f"[OPENSUBTITLES] Searching: {media_type} - {title} S{season}E{episode}")

    try:
        params = {"languages": "he"}
        imdb_clean = imdb_id.replace("tt", "")
        if imdb_clean:
            if media_type == "movie":
                params["imdb_id"] = imdb_clean
            else:
                params["parent_imdb_id"] = imdb_clean
                params["season_number"] = season
                params["episode_number"] = episode
        else:
            params["query"] = title
            if media_type != "movie":
                params["season_number"] = season
                params["episode_number"] = episode

        headers = {"User-Agent": USER_AGENT, "Api-Key": _api_key()}
        response = requests.get(OPENSUBTITLES_API_URL, headers=headers,
                                params=params, timeout=DEFAULT_REQUEST_TIMEOUT)
        if response.status_code != 200:
            kodi_utils.logger("POV-HEBSUBS", f"[OPENSUBTITLES] HTTP {response.status_code}")
            return []

        data = response.json()
        opensubtitles_list = []
        for item in (data.get("data") or []):
            attr = item.get("attributes", {}) or {}
            name = attr.get("release") or ""
            if not name:
                files = attr.get("files") or []
                if files:
                    name = files[0].get("file_name", "") or ""
            name = (name or "").replace(".srt", "").replace(".sub", "").strip()
            if name and name not in opensubtitles_list:
                opensubtitles_list.append(name)

        kodi_utils.logger("POV-HEBSUBS", f"[OPENSUBTITLES] Found {len(opensubtitles_list)} subtitles")
        return opensubtitles_list

    except Exception as e:
        kodi_utils.logger("POV-HEBSUBS", f"[OPENSUBTITLES] Error: {str(e)}")
        return []
