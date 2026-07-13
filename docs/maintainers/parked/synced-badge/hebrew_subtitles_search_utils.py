# -*- coding: utf-8 -*-
########### Imports #####################
from modules import kodi_utils
from caches.settings_cache import get_setting
import threading

########### KODIRDIL Imports ##########
from kodirdil import string_utils
from kodirdil import db_utils
from kodirdil.websites import hebrew_embedded
from kodirdil.websites import ktuvit
from kodirdil.websites import wizdom
from kodirdil.websites import opensubtitles

########### Settings ####################
def get_minimum_sync_percent():
    return int(get_setting('gears.hebrew_subtitles.minimum_sync_percent', '70'))

def is_embedded_search_enabled():
    return get_setting('gears.hebrew_subtitles.match_embedded', 'true') == 'true'

########### Constants ###################
hebrew_subtitles_websites_info = {
    'ktuvit': {'website': ktuvit, 'short_name': '[HEB|KT]'},
    'wizdom': {'website': wizdom, 'short_name': '[HEB|WIZ]'},
    'opensubtitles': {'website': opensubtitles, 'short_name': '[HEB|OPS]'},
}
    
release_names = [
    'blueray', 'bluray', 'blu-ray', 'bdrip', 'brrip', 'brip',
    'hdtv', 'hdtvrip', 'pdtv', 'tvrip', 'hdrip', 'hd-rip',
    'web', 'web-dl', 'web dl', 'web-dlrip', 'webrip', 'web-rip',
    'dvdr', 'dvd-r', 'dvd-rip', 'dvdrip', 'cam', 'hdcam', 'cam-rip', 'camrip', 
    'screener', 'dvdscr', 'dvd-full', 'telecine', 'hdts', 'telesync'
]

# Flag to track if search was performed from external sources
IS_SEARCHED_FROM_EXTERNAL = False


def search_hebrew_subtitles_on_website(website_info, media_metadata, website_subtitles_dict, lock):
    """Search for Hebrew subtitles on a specific website."""
    try:
        hebrew_subtitles_list = website_info['website'].search_for_subtitles(media_metadata)
        hebrew_subtitles_list = strip_problematic_chars_from_subtitle_names_list(hebrew_subtitles_list)

        with lock:
            website_subtitles_dict[website_info['short_name']] = hebrew_subtitles_list

        kodi_utils.logger("Gears-HEBSUBS", f"{website_info['short_name']}_subtitles_list: {str(hebrew_subtitles_list)}")

    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"Error in searching Hebrew subtitles from {website_info['website']}: {str(e)}")


def search_hebrew_subtitles_for_selected_media(media_type, title, season, episode, year, tmdb_id, imdb_id):
    """
    Search for Hebrew subtitles for a selected media and write the filtered subtitles to a cache table.

    Args:
        media_type: The type of media ('movie' or 'tv').
        title: The title of the media.
        season: The season number for TV shows.
        episode: The episode number for TV shows.
        year: The release year of the media.
        tmdb_id: The ID of the media in the TMDB database.
        imdb_id: The IMDb ID of the media.
    """
    global IS_SEARCHED_FROM_EXTERNAL

    # Clear caches for new search
    clear_caches()

    # Check if the current metadata in the cache is the same as the new metadata
    current_media_metadata_in_cache = db_utils.get_current_media_metadata_from_media_metadata_db()
    input_params = (str(media_type), str(title), str(season), str(episode), str(year), str(tmdb_id))

    if current_media_metadata_in_cache and current_media_metadata_in_cache == input_params:
        kodi_utils.logger("Gears-HEBSUBS", "current_media_metadata_in_cache is the same. Skipping subtitles search...")
        IS_SEARCHED_FROM_EXTERNAL = True
        return

    # Mark searched only after we commit to fetching for THIS media
    IS_SEARCHED_FROM_EXTERNAL = True
        
    # Write the current media metadata to cache
    db_utils.write_current_media_metadata_to_media_metadata_db(media_type, title, season, episode, year, tmdb_id)
    
    media_metadata = {
        "media_type": media_type,
        "title": title.replace("%20", " ").replace("%27", "'"),
        "season": season,
        "episode": episode,
        "year": year,
        "tmdb_id": tmdb_id,
        "imdb_id": imdb_id
    }
    
    kodi_utils.logger("Gears-HEBSUBS", f"Starting Hebrew subtitles search for: {media_metadata}")
    
    # Search for subtitles in all websites using threads
    lock = threading.Lock()
    hebrew_subtitles_search_threads = []
    website_subtitles_dict = {}

    for website_info in hebrew_subtitles_websites_info.values():
        thread = threading.Thread(
            target=search_hebrew_subtitles_on_website, 
            args=(website_info, media_metadata, website_subtitles_dict, lock)
        )
        hebrew_subtitles_search_threads.append(thread)
        thread.start()

    for thread in hebrew_subtitles_search_threads:
        thread.join()
    
    # Extract subtitles in the desired order
    unique_subtitles_list = []
    
    for website_info in hebrew_subtitles_websites_info.values():
        subtitles = website_subtitles_dict.get(website_info['short_name'], [])
        unique_subtitles_list.extend(subtitle for subtitle in subtitles if subtitle not in unique_subtitles_list)
    
    kodi_utils.logger("Gears-HEBSUBS", f"unique_subtitles_list: {str(unique_subtitles_list)}")

    # Write the unique subtitles list to the database
    db_utils.write_unique_subtitles_to_hebrew_subtitles_db(unique_subtitles_list, website_subtitles_dict)
  
  
def strip_problematic_chars_from_subtitle_names_list(subtitles_list):
    """Removes problematic characters from subtitle names."""
    return [subtitle_name.replace("'", "") for subtitle_name in subtitles_list]
        

def generate_subtitles_match_top_panel_text_for_sync_percent_match(
    total_external_subtitles_found_count, 
    total_hebrew_embedded_subtitles_matches_count, 
    total_subtitles_matches_count, 
    total_quality_counts
):
    """
    Generate formatted strings for the top panel showing subtitle match statistics.

    Returns:
        tuple: (total_subtitles_found_text, subtitles_matched_count_text)
    """
    minimum_sync_percent = get_minimum_sync_percent()
    
    global IS_SEARCHED_FROM_EXTERNAL
    if not IS_SEARCHED_FROM_EXTERNAL:
        return (
            "[COLOR yellow]מקורות ענן | לחיפוש מלא עם התאמת כתוביות:[/COLOR]",
            "[COLOR cyan]לחץ על חיפוש מלא (בסוף הרשימה)[/COLOR]"
        )
    
    total_subtitles_found_count = total_external_subtitles_found_count + total_hebrew_embedded_subtitles_matches_count
    
    # Calculate total (external + embedded)
    total_all_subtitles = total_subtitles_found_count + total_hebrew_embedded_subtitles_matches_count
    
    hebrew_embedded_text_string = ""
    if total_hebrew_embedded_subtitles_matches_count > 0:
        hebrew_embedded_text_string = f" [COLOR cyan]({total_hebrew_embedded_subtitles_matches_count} מוטמעות)[/COLOR]"

    if total_all_subtitles == 1:
        total_subtitles_found_text = f"[COLOR FFFE9900]נמצאה כתובית{hebrew_embedded_text_string}[/COLOR]"
    elif total_all_subtitles > 0:
        total_subtitles_found_text = f"[COLOR FFFE9900]נמצאו {total_all_subtitles} כתוביות{hebrew_embedded_text_string}[/COLOR]"
    else:
        total_subtitles_found_text = "[COLOR red]אין כתוביות[/COLOR]"
    
    if total_subtitles_found_count > 0 and total_subtitles_matches_count == 0:
        subtitles_matched_count_text = f"[COLOR yellow]0 מעל {minimum_sync_percent}%[/COLOR]"
    else:
        subtitles_matched_count_text = ""
                                   
    if total_subtitles_matches_count > 0:
        count_4k = total_quality_counts.get("4K", 0)
        count_1080p = total_quality_counts.get("1080p", 0)
        count_720p = total_quality_counts.get("720p", 0)
        count_sd = total_quality_counts.get("SD", 0)

        quality_texts = []
        if count_4k > 0:
            quality_texts.append(f"[COLOR FFFF00FE]4K: {count_4k}[/COLOR]")
        if count_1080p > 0:
            quality_texts.append(f"[COLOR FF3CFA38]1080p: {count_1080p}[/COLOR]")
        if count_720p > 0:
            quality_texts.append(f"[COLOR FF3C9900]720p: {count_720p}[/COLOR]")
        if count_sd > 0:
            quality_texts.append(f"[COLOR FF0166FF]SD: {count_sd}[/COLOR]")
            
        if quality_texts:
            subtitles_matched_count_text = " | ".join(quality_texts)
        
    kodi_utils.logger("Gears-HEBSUBS", f"Sources with matched subtitles: {total_subtitles_matches_count}")
    
    return total_subtitles_found_text, subtitles_matched_count_text
    
    
def calculate_highest_sync_percent_and_set_match_text(
    total_subtitles_found_list, 
    original_video_tagline, 
    quality, 
    hebrew_embedded_taglines
):
    """
    Calculates the highest subtitle synchronization percentage and returns match information.

    Args:
        total_subtitles_found_list: A list of all the subtitle sources found.
        original_video_tagline: The name of the original source file.
        quality: The quality of the video file.
        hebrew_embedded_taglines: List of embedded Hebrew subtitle taglines.

    Returns:
        tuple: (external_subtitles_matched_count, hebrew_embedded_subtitles_matched_count,
                subtitle_matches_text, quality_counts_for_source, sync_badge)
        sync_badge: ('full'|'partial'|'embedded'|'', percent) -- MASTERKODI: feeds
        the colored "synced" pill badge in sources_results.xml ('full' >= 90%).
    """
    minimum_sync_percent = get_minimum_sync_percent()
    search_embedded = is_embedded_search_enabled()
    
    # Keys must match Gears's quality values exactly
    quality_counts_for_source = {
        "4K": 0,
        "1080p": 0,
        "720p": 0,
        "SD": 0
    }
    
    external_subtitles_matched_count = 0
    hebrew_embedded_subtitles_matched_count = 0
    subtitle_matches_text = ""
    sync_badge = ('', 0)
    
    global IS_SEARCHED_FROM_EXTERNAL
    if not IS_SEARCHED_FROM_EXTERNAL:
        return external_subtitles_matched_count, hebrew_embedded_subtitles_matched_count, subtitle_matches_text, quality_counts_for_source, sync_badge
    
    # Check Hebrew embedded taglines first
    if search_embedded and hebrew_embedded_taglines:
        is_hebrew_embedded_tagline_match_found = hebrew_embedded.check_match(
            original_video_tagline, 
            hebrew_embedded_taglines
        )
        
        if is_hebrew_embedded_tagline_match_found:
            matched_subtitle_website_name = "[HEB|LOC]"
            hebrew_embedded_subtitles_matched_count = 1
            subtitle_matches_text = f"[B][COLOR deepskyblue]  כתוביות: [/COLOR][COLOR cyan]{matched_subtitle_website_name} מוטמע[/COLOR][/B]"

            if quality in quality_counts_for_source:
                quality_counts_for_source[quality] = 1

            kodi_utils.logger("Gears-HEBSUBS", f"EMBEDDED | Match found! For: {original_video_tagline}")
            sync_badge = ('embedded', 100)
            return external_subtitles_matched_count, hebrew_embedded_subtitles_matched_count, subtitle_matches_text, quality_counts_for_source, sync_badge
            
    # Check external subtitles
    if total_subtitles_found_list:
        highest_sync_percent, matched_subtitle_name, matched_subtitle_website_name = \
            calculate_highest_sync_percent_between_subtitles_and_source(
                total_subtitles_found_list, 
                original_video_tagline, 
                quality
            )
        
        if highest_sync_percent >= minimum_sync_percent:
            external_subtitles_matched_count = 1
            subtitle_matches_text = f"[B][COLOR deepskyblue]  כתוביות: [/COLOR][COLOR FFFF8800]{matched_subtitle_website_name} {highest_sync_percent}% התאמה[/COLOR][/B]"
            
            if quality in quality_counts_for_source:
                quality_counts_for_source[quality] = 1
            
            kodi_utils.logger("Gears-HEBSUBS", f"Match found! {highest_sync_percent}% | Source: {original_video_tagline} | Subtitle: {matched_subtitle_name}")
            sync_badge = ('full' if highest_sync_percent >= 90 else 'partial', highest_sync_percent)
            
    return external_subtitles_matched_count, hebrew_embedded_subtitles_matched_count, subtitle_matches_text, quality_counts_for_source, sync_badge


# Cache for tokenized subtitle names to avoid re-tokenizing
_subtitle_tokens_cache = {}


def get_cached_tokens(name):
    """Get tokenized name from cache or compute and cache it."""
    if name not in _subtitle_tokens_cache:
        _subtitle_tokens_cache[name] = tokenize_release_name(name)
    return _subtitle_tokens_cache[name]


def clear_caches():
    """Clear all caches - call when starting new search."""
    global _subtitle_tokens_cache
    _subtitle_tokens_cache = {}


def tokenize_release_name(name):
    """
    Tokenize a release name into meaningful parts (Twilight style).
    """
    if not name:
        return []
    
    cleaned = (
        name.strip()
        .replace(".srt", "")
        .replace("_", ".")
        .replace(" ", ".")
        .replace("+", ".")
        .replace("/", ".")
        .replace("-", ".")
        .replace(".avi", "")
        .replace(".mp4", "")
        .replace(".mkv", "")
    )
    return [x.strip().lower() for x in cleaned.split(".") if x.strip()]


def calculate_highest_sync_percent_between_subtitles_and_source(total_subtitles_found_list, original_video_tagline, quality):
    """
    Calculates the highest synchronization percentage (Twilight-style, simple and fast).
    """
    # Map quality
    quality_mapping = {
        "4K": "2160p",
        "1080p": "1080p",
        "720p": "720p",
        "SD": "480p"
    }
    quality_lower = quality_mapping.get(quality, quality).lower()
    
    # Tokenize source (with caching)
    array_source = get_cached_tokens(original_video_tagline)
    
    if not array_source:
        return 0, "", ""
    
    # Add quality if not present
    if quality_lower not in array_source:
        array_source = array_source + [quality_lower]
    
    highest_sync_percent = 0
    matched_subtitle_name = ""
    matched_subtitle_website_name = ""
    
    for subtitle_element in total_subtitles_found_list:
        subtitle_name, subtitle_website_name = subtitle_element
        
        # Get cached tokens for subtitle
        array_subtitle = get_cached_tokens(subtitle_name)
        
        if not array_subtitle:
            continue
        
        # Add quality to subtitle array if present in source but not in subtitle
        if quality_lower in array_source and quality_lower not in array_subtitle:
            pass  # Don't add - this is a mismatch indicator
        
        # Twilight's trick: give more weight to release names
        source_weighted = list(array_source)
        subtitle_weighted = list(array_subtitle)
        
        for release_name in release_names:
            if release_name in array_source and release_name in array_subtitle:
                # Add 3 extra copies for weighting (like Twilight does)
                source_weighted.extend([release_name] * 3)
                subtitle_weighted.extend([release_name] * 3)
        
        # Simple SequenceMatcher (like Twilight)
        sync_percent = string_utils.similar(source_weighted, subtitle_weighted)
        
        if sync_percent > highest_sync_percent:
            highest_sync_percent = sync_percent
            matched_subtitle_name = subtitle_name
            matched_subtitle_website_name = subtitle_website_name
            
            # Early exit for very high matches
            if highest_sync_percent >= 95:
                break
    
    return highest_sync_percent, matched_subtitle_name, matched_subtitle_website_name


def reset_search_flag():
    """Reset the IS_SEARCHED_FROM_EXTERNAL flag."""
    global IS_SEARCHED_FROM_EXTERNAL
    IS_SEARCHED_FROM_EXTERNAL = False
