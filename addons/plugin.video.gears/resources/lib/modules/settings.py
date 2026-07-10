# -*- coding: utf-8 -*-
from caches.settings_cache import get_setting, set_setting, default_setting_values
from modules.kodi_utils import translate_path, get_property
# from modules.kodi_utils import logger

def tmdb_api_key():
	return get_setting('gears.tmdb_api', '')

def trakt_client():
	return get_setting('gears.trakt.client', '')

def trakt_secret():
	return get_setting('gears.trakt.secret', '')

def trakt_user_active():
	return get_setting('gears.trakt.user', 'empty_setting') not in (None, 'empty_setting', '')

def tmdblist_user_active():
	return get_setting('gears.tmdb.account_id', 'empty_setting') not in (None, 'empty_setting', '')

def results_format():
	results_window_numbers_dict = {'List': 2000, 'Rows': 2001, 'WideList': 2002}
	window_format = str(get_setting('gears.results.list_format', 'List'))
	if not window_format in results_window_numbers_dict:
		window_format = 'List'
		set_setting('results.list_format', window_format)
	window_number = results_window_numbers_dict[window_format]
	return window_format.lower(), window_number

def store_resolved_to_cloud(debrid_service, pack):
	setting_value = int(get_setting('gears.store_resolved_to_cloud.%s' % debrid_service.lower(), '0'))
	return setting_value in (1, 2) if pack else setting_value == 1

def enabled_debrids_check(debrid_service):
	if not get_setting('gears.%s.enabled' % debrid_service) == 'true': return False
	return authorized_debrid_check(debrid_service)

def authorized_debrid_check(debrid_service):
	if get_setting('gears.%s.token' % debrid_service) in (None, '', 'empty_setting'): return False
	return True

def playback_key():
	return get_setting('gears.playback_key', '0')

def playback_settings():
	return (int(get_setting('gears.playback.watched_percent', '90')), int(get_setting('gears.playback.resume_percent', '5')))

def limit_resolve():
	return get_setting('gears.playback.limit_resolve', 'false') == 'true'

def movies_directory():
	return translate_path(get_setting('gears.movies_directory'))
	
def tv_show_directory():
	return translate_path(get_setting('gears.tv_shows_directory'))

def download_directory(media_type):
	download_directories_dict = {'movie': 'gears.movie_download_directory', 'episode': 'gears.tvshow_download_directory', 'thumb_url': 'gears.image_download_directory',
								'image_url': 'gears.image_download_directory','image': 'gears.image_download_directory', 'premium': 'gears.premium_download_directory',
								None: 'gears.premium_download_directory', 'None': False}
	return translate_path(get_setting(download_directories_dict[media_type]))

def ai_model_order():
	return get_setting('gears.ai_model.order', 'gemini-2.5-flash-lite,llama-3.3-70b-versatile,gemma-3-27b-it,llama-3.1-8b-instant').split(',')

def ai_model_limit():
	return max(1, int(get_setting('gears.ai_model.limit', '10')))

def show_unaired_watchlist():
	return get_setting('gears.show_unaired_watchlist', 'true') == 'true'

def lists_cache_duraton():
	return int(get_setting('gears.lists_cache_duraton', '48'))

def auto_start_gears():
	return get_setting('gears.auto_start_gears', 'false') == 'true'

def source_folders_directory(media_type, source):
	setting = 'gears.%s.movies_directory' % source if media_type == 'movie' else 'gears.%s.tv_shows_directory' % source
	if get_setting(setting) not in ('', 'None', None): return translate_path( get_setting(setting))
	else: return False

def avoid_episode_spoilers():
	return get_setting('gears.avoid_episode_spoilers', 'false') == 'true'

def paginate(is_home):
	paginate_lists = int(get_setting('gears.paginate.lists', '0'))
	if is_home: return paginate_lists in (2, 3)
	else: return paginate_lists in (1, 3)

def page_limit(is_home):	
	return int(get_setting({True: 'gears.paginate.limit_widgets', False: 'gears.paginate.limit_addon'}[is_home], '20'))

def quality_filter(setting):
	return get_setting('gears.%s' % setting).split(', ')

def sort_to_top_filter(autoplay):
	return {0: False, 1: False if autoplay else True, 2: True if autoplay else False, 3: True}[int(get_setting('gears.filter.sort_to_top', '0'))]

def audio_filters():
	setting = get_setting('gears.filter_audio')
	if setting in ('empty_setting', ''): return []
	return setting.split(', ')

def preferred_filters():
	setting = get_setting('gears.filter.preferred_filters')
	if setting in ('empty_setting', ''): return []
	return setting.split(', ')

def include_prerelease_results():
	return int(get_setting('gears.filter.include_prerelease', '0')) == 0

def auto_enable_subs():
	return get_setting('gears.playback.auto_enable_subs', 'false') == 'true'

def stingers_show():
	return get_setting('gears.stinger_alert.show', 'false') == 'true'

def stingers_use_chapters():
	return get_setting('gears.stinger_alert.use_chapters', 'false') == 'true'

def stingers_percentage():
	return int(get_setting('gears.stinger_alert.window_percentage', '90'))

def include_anime_tvshow():
	return get_setting('gears.include_anime_tvshow', 'false') == 'true'

def auto_play(media_type):
	return get_setting('gears.auto_play_%s' % media_type, 'false') == 'true'

def autoplay_next_episode():
	if auto_play('episode') and get_setting('gears.autoplay_next_episode', 'false') == 'true': return True
	else: return False

def autoscrape_next_episode():
	if not auto_play('episode') and get_setting('gears.autoscrape_next_episode', 'false') == 'true': return True
	else: return False

def auto_rescrape_cache_ignored():
	return int(get_setting('gears.results.auto_rescrape_cache_ignored', '1'))

def auto_rescrape_imdb_year():
	return int(get_setting('gears.results.auto_rescrape_imdb_year', '0'))

def auto_rescrape_with_all():
	return int(get_setting('gears.results.auto_rescrape_with_all', '0'))

def autoplay_prescrape(scrape_provider):
	return get_setting('gears.autoplay.%s' % scrape_provider, 'false') == 'true'

def auto_episode_group():
	return int(get_setting('gears.results.auto_episode_group', '0'))

def auto_nextep_settings(play_type):
	play_type = 'autoplay' if play_type == 'autoplay_nextep' else 'autoscrape'
	window_percentage = 100 - int(get_setting('gears.%s_next_window_percentage' % play_type, '95'))
	use_chapters = get_setting('gears.%s_use_chapters' % play_type, 'true') == 'true'
	scraper_time = int(get_setting('gears.results.timeout', '60')) + 20
	if play_type == 'autoplay':
		alert_method = int(get_setting('gears.autoplay_alert_method', '0'))
		default_action = {'0': 'play', '1': 'cancel', '2': 'pause'}[get_setting('gears.autoplay_default_action', '1')]
	else: alert_method, default_action = '', ''
	return {'scraper_time': scraper_time, 'window_percentage': window_percentage, 'alert_method': alert_method, 'default_action': default_action, 'use_chapters': use_chapters}

def filter_status(filter_type):
	return int(get_setting('gears.filter.%s' % filter_type, '0'))

def limit_number_quality():
	return int(get_setting('gears.results.limit_number_quality', '0'))

def limit_number_total():
	return int(get_setting('gears.results.limit_number_total', '0'))

def ignore_results_filter():
	return int(get_setting('gears.results.ignore_filter', '0'))

def trakt_sync_interval():
	setting = get_setting('gears.trakt.sync_interval', '60')
	interval = int(setting) * 60
	return setting, interval

def lists_sort_order(setting):
	return int(get_setting('gears.sort.%s' % setting, '0'))

def personal_lists_sort_unseen_to_top():
	return get_setting('gears.personal_list.sort_unseen_to_top') == 'true'

def personal_lists_unseen_highlight():
	if get_setting('gears.personal_list.highlight_unseen', 'false') == 'false': return None
	return get_setting('gears.personal_list.unseen_highlight', 'FF4DDBFF')

def personal_lists_show_author():
	return get_setting('gears.personal_list.show_author', 'true') == 'true'

def show_specials():
	return get_setting('gears.show_specials', 'false') == 'true'

def single_ep_unwatched_episodes():
	return get_setting('gears.single_ep_unwatched_episodes', 'false') == 'true'

def single_ep_display_format(is_external):
	if is_external: setting, default = 'gears.single_ep_display_widget', '1'
	else: setting, default = 'gears.single_ep_display', ''
	return int(get_setting(setting, default))

def easynews_active():
	if get_setting('gears.provider.easynews', 'false') == 'true': easynews_status = easynews_authorized()
	else: easynews_status = False
	return easynews_status

def easynews_playback_method(query):
	method = int(get_setting('gears.easynews.playback_method', '0'))
	queries = {'retry': lambda: method in (1, 3), 'non_seek': lambda: method in (2, 3),
				'direct_play': lambda: method in (2, 3) and get_setting('gears.easynews.playback_method_limited', 'false') != 'true'}
	setting = queries[query]()
	return setting

def easynews_authorized():
	easynews_user = get_setting('gears.easynews_user', 'empty_setting')
	easynews_password = get_setting('gears.easynews_password', 'empty_setting')
	if easynews_user in ('empty_setting', '') or easynews_password in ('empty_setting', ''): easynews_status = False
	else: easynews_status = True
	return easynews_status

def extras_enable_extra_ratings():
	return get_setting('gears.extras.enable_extra_ratings', 'true') == 'true'

def extras_enable_scrollbars():
	return get_setting('gears.extras.enable_scrollbars', 'true')

def extras_enabled_menus():
	setting = get_setting('gears.extras.enabled', '2000,2050,2051,2052,2053,2054,2055,2056,2057,2058,2059,2060,2061,2062')
	if setting in ('', None, 'noop', []): return []
	split_setting = setting.split(',')
	return [int(i) for i in split_setting]

def recommend_service():
	return int(get_setting('gears.recommend_service', '0'))

def recommend_seed():
	return int(get_setting('gears.recommend_seed', '5'))

def tv_progress_location():
	return int(get_setting('gears.tv_progress_location', '0'))

def check_prescrape_sources(scraper, media_type):
	if scraper in ('easynews', 'rd_cloud', 'pm_cloud', 'ad_cloud', 'oc_cloud', 'tb_cloud', 'folders'): return get_setting('gears.check.%s' % scraper) == 'true'
	if get_setting('gears.check.%s' % scraper) == 'true' and auto_play(media_type): return True
	else: return False

def external_scraper_info():
	module = get_setting('gears.external_scraper.module')
	if module in ('empty_setting', ''): return None, ''
	return module, module.split('.')[-1]

def external_filter_sources():
	return get_setting('gears.external.filter_sources', 'true') == 'true'

def filter_by_name(scraper):
	if get_property('fs_filterless_search') == 'true': return False
	return get_setting('gears.%s.title_filter' % scraper, 'false') == 'true'

def easynews_language_filter():
	enabled = get_setting('gears.easynews.filter_lang') == 'true'
	if enabled: filters = get_setting('gears.easynews.lang_filters').split(', ')
	else: filters = []
	return enabled, filters

def size_sort_weighted():
	return get_setting('gears.results.size_sort_weighted', 'false') == 'true'

def results_sort_order():
	sort_direction = -1 if get_setting('gears.results.size_sort_direction') == '0' else 1
	return (
			lambda k: (k['quality_rank'], k['provider_rank'], sort_direction*k['size_rank']), #Quality, Provider, Size
			lambda k: (k['quality_rank'], sort_direction*k['size_rank'], k['provider_rank']), #Quality, Size, Provider
			lambda k: (k['provider_rank'], k['quality_rank'], sort_direction*k['size_rank']), #Provider, Quality, Size
			lambda k: (k['provider_rank'], sort_direction*k['size_rank'], k['quality_rank']), #Provider, Size, Quality
			lambda k: (sort_direction*k['size_rank'], k['quality_rank'], k['provider_rank']), #Size, Quality, Provider
			lambda k: (sort_direction*k['size_rank'], k['provider_rank'], k['quality_rank'])  #Size, Provider, Quality
			)[int(get_setting('gears.results.sort_order', '1'))]

def active_internal_scrapers():
	settings = ['provider.external', 'provider.easynews', 'provider.folders']
	settings_append = settings.append
	for item in [('rd', 'provider.rd_cloud'), ('pm', 'provider.pm_cloud'), ('ad', 'provider.ad_cloud'), ('oc', 'provider.oc_cloud'), ('tb', 'provider.tb_cloud')]:
		if enabled_debrids_check(item[0]): settings_append(item[1])
	active = [i.split('.')[1] for i in settings if get_setting('gears.%s' % i) == 'true']
	return active

def provider_sort_ranks():
	fo_priority = int(get_setting('gears.folders.priority', '6'))
	en_priority = int(get_setting('gears.en.priority', '7'))
	rd_priority = int(get_setting('gears.rd.priority', '8'))
	ad_priority = int(get_setting('gears.ad.priority', '9'))
	pm_priority = int(get_setting('gears.pm.priority', '10'))
	oc_priority = int(get_setting('gears.oc.priority', '10'))
	ed_priority = int(get_setting('gears.ed.priority', '10'))
	tb_priority = int(get_setting('gears.tb.priority', '10'))
	return {'easynews': en_priority, 'real-debrid': rd_priority, 'premiumize.me': pm_priority, 'alldebrid': ad_priority, 'offcloud': oc_priority, 'easydebrid': ed_priority,
	'torbox': tb_priority, 'rd_cloud': rd_priority, 'pm_cloud': pm_priority, 'ad_cloud': ad_priority, 'oc_cloud': oc_priority, 'tb_cloud': tb_priority, 'folders': fo_priority}

def sort_to_top(provider):
	sort_to_top_dict = {'folders': 'gears.results.sort_folders_first', 'rd_cloud': 'gears.results.sort_rdcloud_first', 'pm_cloud': 'gears.results.sort_pmcloud_first',
						'ad_cloud': 'gears.results.sort_adcloud_first', 'oc_cloud': 'gears.results.sort_occloud_first', 'tb_cloud': 'gears.results.sort_tbcloud_first'}
	return get_setting(sort_to_top_dict[provider]) == 'true'

def auto_resume(media_type, autoplay_status):
	return {0: False, 1: True, 2: autoplay_status}[int(get_setting('gears.auto_resume_%s' % media_type))]

def scraping_settings():
	highlight_type = int(get_setting('gears.highlight.type', '0'))
	if highlight_type == 2:
		highlight = get_setting('gears.scraper_single_highlight', 'FF008EB2')
		return {'highlight_type': 1, '4k': highlight, '1080p': highlight, '720p': highlight, 'sd': highlight}
	easynews_highlight, debrid_cloud_highlight, folders_highlight = '', '', ''
	rd_highlight, pm_highlight, ad_highlight, oc_highlight, ed_highlight, tb_highlight = '', '', '', '', '', ''
	highlight_4K, highlight_1080P, highlight_720P, highlight_SD = '', '', '', ''
	if highlight_type == 0:
		easynews_highlight = get_setting('gears.provider.easynews_highlight', 'FF00B3B2')
		debrid_cloud_highlight = get_setting('gears.provider.debrid_cloud_highlight', 'FF7A01CC')
		folders_highlight = get_setting('gears.provider.folders_highlight', 'FFB36B00')
		rd_highlight = get_setting('gears.provider.rd_highlight', 'FF3C9900')
		pm_highlight = get_setting('gears.provider.pm_highlight', 'FF8C8C8C')
		ad_highlight = get_setting('gears.provider.ad_highlight', 'FFE6B800')
		oc_highlight = get_setting('gears.provider.oc_highlight', 'FF008EB2')
		ed_highlight = get_setting('gears.provider.ed_highlight', 'FF3233FF')
		tb_highlight = get_setting('gears.provider.tb_highlight', 'FF01662A')
	else:
		highlight_4K = get_setting('gears.scraper_4k_highlight', 'FFFF00FE')
		highlight_1080P = get_setting('gears.scraper_1080p_highlight', 'FFE6B800')
		highlight_720P = get_setting('gears.scraper_720p_highlight', 'FF3C9900')
		highlight_SD = get_setting('gears.scraper_SD_highlight', 'FF0166FF')
	return {'highlight_type': highlight_type, 'real-debrid': rd_highlight, 'premiumize': pm_highlight, 'alldebrid': ad_highlight,
			'offcloud': oc_highlight, 'easydebrid': ed_highlight, 'torbox': tb_highlight, 'rd_cloud': debrid_cloud_highlight,
			'pm_cloud': debrid_cloud_highlight, 'ad_cloud': debrid_cloud_highlight, 'oc_cloud': debrid_cloud_highlight, 'tb_cloud': debrid_cloud_highlight,
			'easynews': easynews_highlight, 'folders': folders_highlight, '4k': highlight_4K, '1080p': highlight_1080P, '720p': highlight_720P, 'sd': highlight_SD}

def external_cache_check():
	return get_setting('gears.external.cache_check') == 'true'

def omdb_api_key():
	return get_setting('gears.omdb_api', 'empty_setting')

def default_all_episodes():
	return int(get_setting('gears.default_all_episodes', '0'))

def max_threads():
	if not get_setting('gears.limit_concurrent_threads', 'false') == 'true': return 60
	return int(get_setting('gears.max_threads', '60'))

def get_meta_filter():
	return get_setting('gears.meta_filter', 'true')

def mpaa_region():
	return get_setting('gears.mpaa_region', 'US')

def widget_hide_next_page():
	return get_setting('gears.widget_hide_next_page', 'false') == 'true'

def widget_hide_watched():
	return get_setting('gears.widget_hide_watched', 'false') == 'true'

def calendar_sort_order():
	return int(get_setting('gears.trakt.calendar_sort_order', '0'))

def ignore_articles():
	return get_setting('gears.ignore_articles', 'false') == 'true'

def trakt_lists_auto_load_az():
	# Toggle: auto-load Trakt list contents A-Z (capped at trakt_lists_auto_load_max()).
	return get_setting('gears.trakt.lists.auto_load_az', 'true') == 'true'

def trakt_lists_auto_load_max():
	try: return int(get_setting('gears.trakt.lists.auto_load_max', '1000'))
	except: return 1000


def date_offset():
	return int(get_setting('gears.datetime.offset', '0')) + 5

def media_open_action(media_type):
	return int(get_setting('gears.media_open_action_%s' % media_type, '0'))

def watched_indicators():
	if not trakt_user_active(): return 0
	return int(get_setting('gears.watched_indicators', '0'))

def flatten_episodes():
	return get_setting('gears.trakt.flatten_episodes', 'false') == 'true'

def nextep_method():
	return int(get_setting('gears.nextep.method', '0'))

def nextep_limit_history():
	return get_setting('gears.nextep.limit_history', 'false') == 'true'

def nextep_limit():
	return int(get_setting('gears.nextep.limit', '20'))

def nextep_include_unwatched():
	return int(get_setting('gears.nextep.include_unwatched', '0'))

def nextep_include_airdate():
	return get_setting('gears.nextep.include_airdate', 'false') == 'true'

def nextep_airing_today():
	return get_setting('gears.nextep.airing_today', 'false') == 'true'

def nextep_include_unaired():
	return get_setting('gears.nextep.include_unaired', 'false') == 'true'

def nextep_sort_key():
	return {0: 'last_played', 1: 'first_aired', 2: 'name'}[int(get_setting('gears.nextep.sort_type', '0'))]

def nextep_sort_direction():
	return int(get_setting('gears.nextep.sort_order', '0')) == 0

def update_delay():
	return int(get_setting('gears.update.delay', '45'))

def update_action():
	return int(get_setting('gears.update.action', '2'))

def cm_sort_order():
	try: return {i: c for c, i in enumerate(get_setting('gears.context_menu.order').split(','))}
	except: return {i: c for c, i in enumerate(default_setting_values('context_menu.order')['setting_default'].split(','))}

def cm_default_order():
	return {i: c for c, i in enumerate(default_setting_values('context_menu.order')['setting_default'].split(','))}

def rpdb_api_key(media_type):
	if int(get_setting('gears.rpdb_enabled', '0')) not in {'movie': (1, 3), 'tvshow': (2, 3)}[media_type]: return None
	return get_setting('gears.rpdb_api')

def use_season_name():
	return get_setting('gears.use_season_name', 'false') == 'true'


