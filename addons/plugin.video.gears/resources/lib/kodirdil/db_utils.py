# -*- coding: utf-8 -*-
########### Imports #####################
import sqlite3 as database
from os import path
from threading import RLock
from modules import kodi_utils

# Database paths for Hebrew subtitles
_addon_profile = None
_hebrew_subs_path = None
_media_meta_path = None

# Cached connections (per-thread sqlite is unsafe; we use check_same_thread=False with an RLock).
# RLock is required because _ensure_tables() acquires the lock and then calls _get_*_conn(),
# which acquires the same lock — a non-reentrant Lock would deadlock here.
_hebrew_subs_conn = None
_media_meta_conn = None
_conn_lock = RLock()
_tables_initialized = False


def _profile():
    global _addon_profile
    if _addon_profile is None:
        _addon_profile = kodi_utils.translate_path('special://profile/addon_data/plugin.video.gears/')
    return _addon_profile


def get_hebrew_subtitles_db_path():
    global _hebrew_subs_path
    if _hebrew_subs_path is None:
        _hebrew_subs_path = path.join(_profile(), 'hebrew_subtitles.db')
    return _hebrew_subs_path


def get_media_metadata_db_path():
    global _media_meta_path
    if _media_meta_path is None:
        _media_meta_path = path.join(_profile(), 'media_metadata.db')
    return _media_meta_path


def _get_hebrew_subs_conn():
    global _hebrew_subs_conn
    with _conn_lock:
        if _hebrew_subs_conn is None:
            try:
                _hebrew_subs_conn = database.connect(get_hebrew_subtitles_db_path(), timeout=20,
                                                    isolation_level=None, check_same_thread=False)
            except Exception as e:
                kodi_utils.logger("Gears-HEBSUBS", "Error connecting to hebrew_subtitles_db: %s" % str(e))
                return None
        return _hebrew_subs_conn


def _get_media_meta_conn():
    global _media_meta_conn
    with _conn_lock:
        if _media_meta_conn is None:
            try:
                _media_meta_conn = database.connect(get_media_metadata_db_path(), timeout=20,
                                                   isolation_level=None, check_same_thread=False)
            except Exception as e:
                kodi_utils.logger("Gears-HEBSUBS", "Error connecting to media_metadata_db: %s" % str(e))
                return None
        return _media_meta_conn


def _ensure_tables():
    global _tables_initialized
    if _tables_initialized:
        return
    with _conn_lock:
        if _tables_initialized:
            return
        try:
            hs = _get_hebrew_subs_conn()
            if hs is not None:
                hs.execute("CREATE TABLE IF NOT EXISTS current_subtitles_cache (subtitle_name TEXT, website_name TEXT)")
            mm = _get_media_meta_conn()
            if mm is not None:
                mm.execute("CREATE TABLE IF NOT EXISTS current_media_metadata_cache ("
                          "media_type TEXT, title TEXT, season TEXT, episode TEXT, year TEXT, tmdb_id TEXT)")
            _tables_initialized = True
        except Exception as e:
            kodi_utils.logger("Gears-HEBSUBS", "Error ensuring tables: %s" % str(e))


def write_unique_subtitles_to_hebrew_subtitles_db(unique_subtitles_list, website_subtitles_dict):
    """Write unique subtitles to the 'current_subtitles_cache' table (replaces existing rows)."""
    _ensure_tables()
    clear_hebrew_subtitles_db_cache()
    try:
        dbcon = _get_hebrew_subs_conn()
        if dbcon is None:
            return
        if unique_subtitles_list:
            rows = []
            # A release often exists on several sites. Attribute it by a FIXED
            # preference (Israeli sites first) -- NOT website_subtitles_dict's
            # order, which is thread-COMPLETION order and made a Ktuvit sub show
            # as OpenSubtitles whenever the OPS thread happened to finish first.
            website_preference = ['[HEB|KT]', '[HEB|WIZ]', '[HEB|OPS]']
            for subtitle_name in unique_subtitles_list:
                assigned = False
                for ws in website_preference:
                    if subtitle_name in website_subtitles_dict.get(ws, []):
                        rows.append((subtitle_name, ws))
                        assigned = True
                        break
                if not assigned:
                    for website_short_name, website_subtitles_list in website_subtitles_dict.items():
                        if subtitle_name in website_subtitles_list:
                            rows.append((subtitle_name, website_short_name))
                            break
            if rows:
                with _conn_lock:
                    dbcon.executemany("INSERT INTO current_subtitles_cache VALUES (?, ?)", rows)
        kodi_utils.logger("Gears-HEBSUBS", "Written %d subtitles to database" % len(unique_subtitles_list))
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "Error in writing subtitles to hebrew_subtitles_db: %s" % str(e))


def clear_hebrew_subtitles_db_cache():
    _ensure_tables()
    try:
        dbcon = _get_hebrew_subs_conn()
        if dbcon is None:
            return
        with _conn_lock:
            dbcon.execute("DELETE FROM current_subtitles_cache")
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "Error Clearing hebrew_subtitles_db cache: %s" % str(e))


def get_total_subtitles_found_list_from_hebrew_subtitles_db():
    """Returns list of (subtitle_name, website_name) tuples."""
    _ensure_tables()
    try:
        dbcon = _get_hebrew_subs_conn()
        if dbcon is None:
            return []
        with _conn_lock:
            cur = dbcon.execute("SELECT subtitle_name, website_name FROM current_subtitles_cache")
            rows = cur.fetchall()
        return rows or []
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "Error while reading hebrew_subtitles_db: %s" % str(e))
        return []


def write_current_media_metadata_to_media_metadata_db(media_type, title, season, episode, year, tmdb_id):
    _ensure_tables()
    clear_media_metadata_db_cache()
    try:
        dbcon = _get_media_meta_conn()
        if dbcon is None:
            return
        with _conn_lock:
            dbcon.execute("INSERT INTO current_media_metadata_cache VALUES (?, ?, ?, ?, ?, ?)",
                          (media_type, title, season, episode, year, tmdb_id))
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "EMBEDDED | Error in writing to media_metadata_db: %s" % str(e))


def clear_media_metadata_db_cache():
    _ensure_tables()
    try:
        dbcon = _get_media_meta_conn()
        if dbcon is None:
            return
        with _conn_lock:
            dbcon.execute("DELETE FROM current_media_metadata_cache")
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "EMBEDDED | Error Clearing media_metadata_db cache: %s" % str(e))


def get_media_type_from_media_metadata_db():
    _ensure_tables()
    try:
        dbcon = _get_media_meta_conn()
        if dbcon is None:
            return None
        with _conn_lock:
            cur = dbcon.execute("SELECT media_type FROM current_media_metadata_cache LIMIT 1")
            row = cur.fetchone()
        return row[0] if row else None
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "EMBEDDED | Error while reading media_metadata_db: %s" % str(e))
        return None


def get_current_media_metadata_from_media_metadata_db():
    _ensure_tables()
    try:
        dbcon = _get_media_meta_conn()
        if dbcon is None:
            return None
        with _conn_lock:
            cur = dbcon.execute("SELECT media_type, title, season, episode, year, tmdb_id FROM current_media_metadata_cache LIMIT 1")
            row = cur.fetchone()
        return row
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", "EMBEDDED | Error while reading media_metadata_db: %s" % str(e))
        return None
