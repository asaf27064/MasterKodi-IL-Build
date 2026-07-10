# -*- coding: utf-8 -*-
########### Imports #####################
from modules import kodi_utils
import time
from threading import Thread

########### KODIRDIL Imports ##########
from kodirdil import hebrew_subtitles_search_utils


def create_search_hebrew_subtitles_thread(media_type, title, season, episode, year, tmdb_id, imdb_id):
    """
    Creates a thread to search for Hebrew subtitles for a media item.

    Args:
        media_type (str): The type of media item, either 'movie' or 'episode'.
        title (str): The title of the media item.
        season (int): The season number for a TV show, or 0 for a movie.
        episode (int): The episode number for a TV show, or 0 for a movie.
        year (int): The year the media item was released.
        tmdb_id (str): The TMDb ID of the media item.
        imdb_id (str): The IMDb ID of the media item.

    Returns:
        threading.Thread: The created thread for searching Hebrew subtitles.
    """
    if media_type == 'movie':
        hebrew_subtitles_search_arguments = ('movie', title, '0', '0', year, tmdb_id, imdb_id)
        kodi_utils.logger("Gears-HEBSUBS", f"START search_hebrew_subtitles_thread - Movie: {title} | Year: {year} | TMDb ID: {tmdb_id}")
    else:
        hebrew_subtitles_search_arguments = ('tv', title, season, episode, year, tmdb_id, imdb_id)
        kodi_utils.logger("Gears-HEBSUBS", f"START search_hebrew_subtitles_thread - TV: {title} S{season}E{episode} | Year: {year} | TMDb ID: {tmdb_id}")
    
    try:
        search_hebrew_subtitles_thread = Thread(
            target=hebrew_subtitles_search_utils.search_hebrew_subtitles_for_selected_media, 
            args=hebrew_subtitles_search_arguments
        )
        return search_hebrew_subtitles_thread
        
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"ERROR creating search_hebrew_subtitles_thread: {str(e)}")
        return None


def manage_search_hebrew_subtitles_thread(search_hebrew_subtitles_thread, search_hebrew_subtitles_thread_start_time):
    """
    Manage the search_hebrew_subtitles_thread to prevent it from running indefinitely. 

    This function checks if the thread is still running and waits for it to complete,
    with a maximum timeout of 10 seconds.

    Args:
        search_hebrew_subtitles_thread (threading.Thread): The thread to be managed.
        search_hebrew_subtitles_thread_start_time (float): The start time of the thread.
    """
    try:
        count = 0
        while search_hebrew_subtitles_thread is not None and search_hebrew_subtitles_thread.is_alive():
            kodi_utils.sleep(100)
            count += 1
            if count > 100:  # 10 seconds timeout
                kodi_utils.logger("Gears-HEBSUBS", "FORCE STOP search_hebrew_subtitles_thread - exceeded 10 seconds")
                break
                
        elapsed_time = time.time() - search_hebrew_subtitles_thread_start_time
        kodi_utils.logger("Gears-HEBSUBS", f"END search_hebrew_subtitles_thread - Total run time: {elapsed_time:.2f} seconds")
        
    except Exception as e:
        kodi_utils.logger("Gears-HEBSUBS", f"ERROR managing search_hebrew_subtitles_thread: {str(e)}")
