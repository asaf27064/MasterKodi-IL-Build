# -*- coding: utf-8 -*-
########### Imports ##################### 
from modules import kodi_utils
import requests
import os
import sqlite3
import time

########### Constants ###################
# Community taglines now live on the MasterKodi worker (the original
# darksubshebsubs.github.io files are gone -- 404).
MOVIES_TAGLINES_FILE_URL = "https://masterkodi-subpool.asaf27064.workers.dev/v1/taglines?type=movie"
TV_SHOWS_TAGLINES_FILE_URL = "https://masterkodi-subpool.asaf27064.workers.dev/v1/taglines?type=tv"
TAGLINES_POST_URL = "https://masterkodi-subpool.asaf27064.workers.dev/v1/taglines"
TAGLINES_HEADERS = {"User-Agent": "MasterKodiGears/1.0", "X-Gears-Key": "mk-76ed711408c449eda0c5a2d868720b0438e36309"}
DEFAULT_REQUEST_TIMEOUT = 10
CACHE_DURATION_HOURS = 0.5   # refresh often so newly-reported embedded taglines appear

# In-memory cache
_taglines_cache = {}
_cache_timestamps = {}

########### Local Learning Database ###################
def get_local_embedded_db_path():
    """Get path to local embedded taglines database"""
    profile_path = kodi_utils.translate_path('special://profile/addon_data/plugin.video.gears/')
    if not os.path.exists(profile_path):
        os.makedirs(profile_path)
    return os.path.join(profile_path, 'hebrew_embedded_learned.db')

def init_local_embedded_db():
    """Initialize the local learning database"""
    try:
        db_path = get_local_embedded_db_path()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS learned_taglines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tagline TEXT UNIQUE NOT NULL,
                media_type TEXT NOT NULL,
                added_date INTEGER NOT NULL,
                play_count INTEGER DEFAULT 1
            )
        ''')
        conn.commit()
        conn.close()
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Error initializing local DB: {str(e)}")

def add_to_local_embedded_db(tagline, media_type):
    """
    Add a tagline to the local learning database when user confirms embedded Hebrew subs.
    
    Args:
        tagline (str): The video tagline/release name
        media_type (str): 'movie' or 'tvshow'
    """
    try:
        if not tagline:
            return
        
        init_local_embedded_db()
        db_path = get_local_embedded_db_path()
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        tagline_lower = tagline.strip().lower()
        current_time = int(time.time())
        
        # Try to insert, or update play_count if exists
        cursor.execute('''
            INSERT INTO learned_taglines (tagline, media_type, added_date, play_count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(tagline) DO UPDATE SET play_count = play_count + 1
        ''', (tagline_lower, media_type, current_time))
        
        conn.commit()
        conn.close()
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Added to local DB: {tagline_lower[:50]}...")
        # Share with the community list so every MasterKodi user benefits
        # (fail-open -- a network problem never breaks playback).
        try:
            requests.post(TAGLINES_POST_URL,
                          json={"tagline": tagline_lower, "media_type": media_type},
                          headers=TAGLINES_HEADERS, timeout=6)
        except Exception:
            pass
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Error adding to local DB: {str(e)}")

def get_local_embedded_taglines(media_type=None):
    """
    Get all taglines from the local learning database.
    
    Args:
        media_type (str, optional): Filter by 'movie' or 'tvshow'
        
    Returns:
        set: Set of learned taglines (lowercase)
    """
    try:
        db_path = get_local_embedded_db_path()
        if not os.path.exists(db_path):
            return set()
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        if media_type:
            cursor.execute('SELECT tagline FROM learned_taglines WHERE media_type = ?', (media_type,))
        else:
            cursor.execute('SELECT tagline FROM learned_taglines')
        
        taglines = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Loaded {len(taglines)} taglines from local DB")
        return taglines
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Error reading local DB: {str(e)}")
        return set()

def get_local_embedded_count():
    """Get count of learned taglines"""
    try:
        db_path = get_local_embedded_db_path()
        if not os.path.exists(db_path):
            return 0
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM learned_taglines')
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

def clear_local_embedded_db():
    """Clear all learned taglines"""
    try:
        db_path = get_local_embedded_db_path()
        if os.path.exists(db_path):
            os.remove(db_path)
            kodi_utils.logger("Gears-HEBSUBS", "EMBEDDED | Cleared local learning DB")
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Error clearing local DB: {str(e)}")

########### Remote Taglines with Caching ###################
def get_hebrew_embedded_taglines(media_type):
    """
    Fetch the list of known embedded Hebrew subtitle taglines.
    Combines remote repository + local learned taglines with 24-hour caching.
    
    Args:
        media_type (str): Either 'movie' or 'tv'
        
    Returns:
        set or None: Set of taglines (lowercase) or None if failed
    """
    global _taglines_cache, _cache_timestamps
    
    try:
        if not media_type:
            return None
        
        cache_key = f"embedded_{media_type}"
        current_time = time.time()
        
        # Check if cache is valid (24 hours)
        if cache_key in _taglines_cache and cache_key in _cache_timestamps:
            cache_age_hours = (current_time - _cache_timestamps[cache_key]) / 3600
            if cache_age_hours < CACHE_DURATION_HOURS:
                kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Using cached taglines ({len(_taglines_cache[cache_key])} items)")
                return _taglines_cache[cache_key]
        
        # Start with local learned taglines
        combined_taglines = get_local_embedded_taglines(media_type)
        
        # Try to fetch remote taglines
        try:
            url = MOVIES_TAGLINES_FILE_URL if media_type == "movie" else TV_SHOWS_TAGLINES_FILE_URL
            response = requests.get(url, timeout=DEFAULT_REQUEST_TIMEOUT, headers=TAGLINES_HEADERS)
            
            if response.status_code == 200:
                remote_taglines = {line.strip().lower() for line in response.text.split("\n") if line.strip()}
                combined_taglines = combined_taglines.union(remote_taglines)
                kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Loaded {len(remote_taglines)} remote + {len(combined_taglines) - len(remote_taglines)} local taglines")
        except Exception as e:
            kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Remote fetch failed (using local only): {str(e)}")
        
        # Update cache
        _taglines_cache[cache_key] = combined_taglines
        _cache_timestamps[cache_key] = current_time
        
        return combined_taglines if combined_taglines else None
        
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Error fetching taglines: {str(e)}")
        return None

def clear_taglines_cache():
    """Clear the in-memory taglines cache"""
    global _taglines_cache, _cache_timestamps
    _taglines_cache = {}
    _cache_timestamps = {}
    kodi_utils.logger("Gears-HEBSUBS", "EMBEDDED | Cleared taglines cache")

########### Matching Functions ###################
def check_hebrew_embedded(original_video_tagline, hebrew_embedded_taglines):
    """
    Check if the video tagline matches any known embedded Hebrew subtitle tagline.
    
    Args:
        original_video_tagline (str): The tagline of the video source
        hebrew_embedded_taglines (set): Set of known embedded subtitle taglines (lowercase)
        
    Returns:
        bool: True only on an EXACT match to a known embedded-Hebrew release.
    """
    if not original_video_tagline or not hebrew_embedded_taglines:
        return False

    # Normalize the tagline for comparison
    tagline_lower = original_video_tagline.strip().lower()

    if not tagline_lower:
        return False

    # EXACT match only -- a source is flagged embedded only when its release
    # name is a known embedded-Hebrew release. (No fuzzy/estimated matching:
    # that risked false positives.)
    return tagline_lower in hebrew_embedded_taglines


# Alias for backwards compatibility
check_match = check_hebrew_embedded
