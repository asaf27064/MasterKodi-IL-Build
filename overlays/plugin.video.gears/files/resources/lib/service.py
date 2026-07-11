# -*- coding: utf-8 -*-
from xbmc import Monitor
import os
import json
import inspect
from time import time
from threading import Thread
from caches.settings_cache import get_setting, set_setting, sync_settings
from modules import kodi_utils

pause_services_prop = 'gears.pause_services'
firstrun_update_prop = 'gears.firstrun_update'
current_skin_prop = 'gears.current_skin'
trakt_service_string = 'TraktMonitor Service Update %s - %s'
trakt_success_line_dict = {'success': 'Trakt Update Performed', 'no account': '(Unauthorized) Trakt Update Performed'}
update_string = 'Next Update in %s minutes...'

class SetAddonConstants:
	def run(self):
		kodi_utils.logger('gears', 'SetAddonConstants Service Starting')
		import random
		addon_items = [('gears.addon_version', kodi_utils.addon_info('version')),
						('gears.addon_path', kodi_utils.addon_info('path')),
						('gears.addon_profile', kodi_utils.translate_path(kodi_utils.addon_info('profile'))),
						('gears.addon_icon', kodi_utils.translate_path(kodi_utils.addon_info('icon'))),
						('gears.addon_icon_mini', os.path.join(kodi_utils.addon_info('path'), 'resources', 'media', 'addon_icons', 'minis',
						os.path.basename(kodi_utils.translate_path(kodi_utils.addon_info('icon'))))),
						('gears.addon_fanart', kodi_utils.translate_path(kodi_utils.addon_info('fanart'))),
						('gears.playback_key', str(random.randint(1000, 10000)))]
		for item in addon_items: kodi_utils.set_property(*item)
		return kodi_utils.logger('gears', 'SetAddonConstants Service Finished')

class DatabaseMaintenance:
	def run(self):
		kodi_utils.logger('gears', 'DatabaseMaintenance Service Starting')
		from caches.base_cache import make_databases
		make_databases()
		return kodi_utils.logger('gears', 'DatabaseMaintenance Service Finished')

class SyncSettings:
	def run(self):
		kodi_utils.logger('gears', 'SyncSettings Service Starting')
		sync_settings()
		return kodi_utils.logger('gears', 'SyncSettings Service Finished')

class OnUpdateChanges:
	def run(self):
		kodi_utils.logger('gears', 'OnUpdateChanges Service Starting')
		try:
			for method in list(filter(lambda x: x[0] != 'run', inspect.getmembers(OnUpdateChanges, predicate=inspect.isfunction))):
				if not get_setting('gears.updatechecks.%s' % method[0], 'false') == 'true':
					method[1](self)
					set_setting('updatechecks.%s' % method[0], 'true')
		except: pass
		return kodi_utils.logger('gears', 'OnUpdateChanges Service Finished')

	def context_menu_update_03(self):
		from caches.settings_cache import default_setting_values
		set_setting('context_menu.order', default_setting_values('context_menu.order')['setting_default'])
		set_setting('extras.enabled', default_setting_values('extras.enabled')['setting_default'])

class CustomFonts:
	def run(self):
		kodi_utils.logger('gears', 'CustomFonts Service Starting')
		from windows.base_window import FontUtils
		monitor = kodi_utils.kodi_monitor()
		wait_for_abort = monitor.waitForAbort
		kodi_utils.clear_property(current_skin_prop)
		font_utils = FontUtils()
		while not monitor.abortRequested():
			font_utils.execute_custom_fonts()
			wait_for_abort(20)
		try: del monitor
		except: pass
		return kodi_utils.logger('gears', 'CustomFonts Service Finished')

class TraktMonitor:
	def run(self):
		kodi_utils.logger('gears', 'TraktMonitor Service Starting')
		from apis.trakt_api import trakt_sync_activities
		from modules.settings import trakt_sync_interval, trakt_user_active
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		while not monitor.abortRequested():
			while is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': wait_for_abort(10)
			if not trakt_user_active():
				wait_for_abort(1800)
				continue
			wait_time = 1800
			try:
				sync_interval, wait_time = trakt_sync_interval()
				next_update_string = update_string % sync_interval
				status = trakt_sync_activities()
				if status == 'failed': kodi_utils.logger('gears', trakt_service_string % ('Failed. Error from Trakt', next_update_string))
				else:
					if status in ('success', 'no account'): kodi_utils.logger('gears', trakt_service_string % ('Success. %s' % trakt_success_line_dict[status], next_update_string))
					else: kodi_utils.logger('gears', trakt_service_string % ('Success. No Changes Needed', next_update_string))# 'not needed'
					if status == 'success' and get_setting('gears.trakt.refresh_widgets', 'false') == 'true': kodi_utils.run_plugin({'mode': 'kodi_refresh'})
			except Exception as e: kodi_utils.logger('gears', trakt_service_string % ('Failed', 'The following Error Occured: %s' % str(e)))
			wait_for_abort(wait_time)
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('gears', 'TraktMonitor Service Finished')

class UpdateCheck:
	def run(self):
		if kodi_utils.get_property(firstrun_update_prop) == 'true': return
		kodi_utils.logger('gears', 'UpdateCheck Service Starting')
		from modules.updater import update_check
		from modules.settings import update_action, update_delay
		end_pause = time() + update_delay()
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, is_playing = monitor.waitForAbort, player.isPlayingVideo
		while time() < end_pause: wait_for_abort(1)
		while kodi_utils.get_property(pause_services_prop) == 'true' or is_playing(): wait_for_abort(1)
		update_check(update_action())
		kodi_utils.set_property(firstrun_update_prop, 'true')
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('gears', 'UpdateCheck Service Finished')

class WidgetRefresher:
	def run(self):
		kodi_utils.logger('gears', 'WidgetRefresher Service Starting')
		from time import time
		from indexers.random_lists import refresh_widgets
		monitor, player = kodi_utils.kodi_monitor(), kodi_utils.kodi_player()
		wait_for_abort, self.is_playing = monitor.waitForAbort, player.isPlayingVideo
		wait_for_abort(10)
		self.set_next_refresh(time())
		while not monitor.abortRequested():
			try:
				wait_for_abort(10)
				offset = int(get_setting('gears.widget_refresh_timer', '60'))
				if offset != self.offset:
					self.set_next_refresh(time())
					continue
				if self.condition_check(): continue
				if self.next_refresh < time():
					kodi_utils.logger('gears', 'WidgetRefresher Service - Widgets Refreshed')
					refresh_widgets()
					self.set_next_refresh(time())
			except: pass
		try: del monitor
		except: pass
		try: del player
		except: pass
		return kodi_utils.logger('gears', 'WidgetRefresher Service Finished')

	def condition_check(self):
		if not self.external(): return True

		if self.next_refresh == None or self.is_playing() or kodi_utils.get_property(pause_services_prop) == 'true': return True
		if kodi_utils.get_property('gears.window_loaded') == 'true': return True 
		try:
			window_stack = json.loads(kodi_utils.get_property('gears.window_stack'))
			if window_stack or window_stack == []: return True
		except: pass
		return False

	def set_next_refresh(self, _time):
		self.offset = int(get_setting('gears.widget_refresh_timer', '60'))
		if self.offset: self.next_refresh = _time + (self.offset*60)
		else: self.next_refresh = None

	def external(self):
		return 'plugin' not in kodi_utils.get_infolabel('Container.PluginName')

class AutoStart:
	def run(self):
		kodi_utils.logger('gears', 'AutoStart Service Starting')
		from modules.settings import auto_start_gears
		if auto_start_gears(): kodi_utils.run_addon()
		return kodi_utils.logger('gears', 'AutoStart Service Finished')

class AddonXMLCheck:
	def run(self):
		kodi_utils.logger('gears', 'AddonXMLCheck Service Starting')
		from xml.dom.minidom import parse as mdParse
		self.addon_xml = kodi_utils.translate_path('special://home/addons/plugin.video.gears/addon.xml')
		self.root = mdParse(self.addon_xml)
		self.change_file = False
		self.check_property('reuse_language_invoker', 'reuselanguageinvoker')
		self.check_property('addon_icon_choice', 'icon')
		self.change_xml_file()
		return kodi_utils.logger('gears', 'AddonXMLCheck Service Finished')

	def check_property(self, setting, tag_name):
		current_addon_setting = get_setting('gears.%s' % setting, None)
		if current_addon_setting is None: return
		tag_instance = self.root.getElementsByTagName(tag_name)[0].firstChild
		current_property = tag_instance.data
		if current_property != current_addon_setting:
			tag_instance.data = current_addon_setting
			self.change_file = True

	def change_xml_file(self):
		if not self.change_file: return
		kodi_utils.notification('Refreshing Addon XML After Update. Restarting Addons')
		new_xml = str(self.root.toxml()).replace('<?xml version="1.0" ?>', '')
		with open(self.addon_xml, 'w') as f: f.write(new_xml)
		kodi_utils.logger('gears', 'AddonXMLCheck Service - Change Detected. Restarting Addons')
		kodi_utils.execute_builtin('ActivateWindow(Home)', True)
		kodi_utils.update_local_addons()
		kodi_utils.disable_enable_addon()


########### KODIRDIL - Debrid subscription banner ###########
# On addon startup, for every debrid service the user has authenticated (rd/ad/pm/oc/ed/tb),
# query its account info and show a Hebrew toast with days remaining + expiration date.
# Each service knows where its expiration field lives in its account_info() response,
# and how to interpret it (ISO string vs unix seconds vs unix ms). One toast per service,
# spaced 1s apart so they don't visually replace each other in Kodi's single notification slot.
#
# Banner format (Option C, chosen 2026-05-15):
#   heading: "<Service> · <N> ימים נותרו"   (or "<N> שעות נותרו" when < 24h)
#   body:    "פג תוקף: DD/MM"                (adds HH:MM when < 24h)
#
# Silent no-ops on: service disabled, empty token, network error, response missing the field,
# or any exception. A startup banner must never break boot.
DEBRID_SUBS = (
	# (display_name, enabled_setting, token_setting, api_module, api_class, field_path, ts_format)
	# field_path is a dotted path inside the JSON. ts_format: 'iso' | 'unix_s' | 'unix_ms'
	('Real Debrid', 'gears.rd.enabled', 'gears.rd.token', 'apis.real_debrid_api', 'RealDebridAPI', 'expiration',           'iso'),
	('AllDebrid',   'gears.ad.enabled', 'gears.ad.token', 'apis.alldebrid_api',   'AllDebridAPI',  'data.user.premiumUntil','unix_s'),
	('Premiumize',  'gears.pm.enabled', 'gears.pm.token', 'apis.premiumize_api',  'PremiumizeAPI', 'premium_until',         'unix_s'),
	('Offcloud',    'gears.oc.enabled', 'gears.oc.token', 'apis.offcloud_api',    'OffcloudAPI',   'expirationDate',        'unix_ms'),
	('EasyDebrid',  'gears.ed.enabled', 'gears.ed.token', 'apis.easydebrid_api',  'EasyDebridAPI', 'expiry_unix_seconds',   'unix_s'),
	('TorBox',      'gears.tb.enabled', 'gears.tb.token', 'apis.torbox_api',      'TorBoxAPI',     'data.premium_expires_at','iso'),
)

def _dig(obj, dotted):
	"""Walk a dotted key path through nested dicts."""
	for key in dotted.split('.'):
		if not isinstance(obj, dict): return None
		obj = obj.get(key)
	return obj

def _parse_expiry(value, fmt):
	"""Return a tz-aware UTC datetime, or None if unparseable."""
	from datetime import datetime, timezone
	try:
		if value is None: return None
		if fmt == 'iso':
			# Tolerate trailing 'Z' and fractional seconds.
			s = str(value).rstrip('Z')
			if '.' in s: s = s.split('.')[0]
			return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
		if fmt == 'unix_s':
			return datetime.fromtimestamp(int(value), tz=timezone.utc)
		if fmt == 'unix_ms':
			return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
	except Exception:
		return None
	return None

class DebridSubscriptionCheck:
	_banner_shown_prop = 'gears.debrid_subscription_banner_shown'

	def run(self):
		try:
			if kodi_utils.get_property(self._banner_shown_prop) == 'true': return
			# Mark up-front so even if we crash mid-iteration we don't double-fire on the next start.
			kodi_utils.set_property(self._banner_shown_prop, 'true')

			import importlib, time as _time, math, xbmcgui
			from datetime import datetime, timezone

			banner_count = 0
			for name, enabled_key, token_key, module_path, class_name, field_path, ts_format in DEBRID_SUBS:
				try:
					if get_setting(enabled_key, 'false') != 'true': continue
					tok = get_setting(token_key, 'empty_setting')
					if not tok or tok in ('empty_setting', ''): continue

					mod = importlib.import_module(module_path)
					api = getattr(mod, class_name)()
					info = api.account_info()
					if not info: continue

					# RD returns the dict directly; others wrap in {"success":true,"data":{...}}
					# Try both shapes by trying the dotted path on the response root first.
					expires_raw = _dig(info, field_path)
					expires_at = _parse_expiry(expires_raw, ts_format)
					if not expires_at: continue

					now = datetime.now(timezone.utc)
					delta_total_seconds = (expires_at - now).total_seconds()

					# Local date format DD/MM/YYYY — Israeli convention with 4-digit year for clarity.
					# Hours only when < 24h.
					exp_local = expires_at.astimezone()  # convert to user's local tz for display
					date_str = exp_local.strftime('%d/%m/%Y')
					time_str = exp_local.strftime('%H:%M')

					# RLM (‏, Right-to-Left Mark) before the number anchors it to the Hebrew RTL
					# run so "20 שעות נותרו" displays as one block with 20 visually adjacent to שעות.
					# Without RLM, the number gets pulled into the leading LTR "TorBox · " run and
					# visually appears separated from its noun on the wrong side.
					RLM = '‏'

					if delta_total_seconds < 0:
						heading = '%s · פג תוקף' % name
						body = 'מאז %s %s' % (date_str, time_str)
						time_ms = 10000
					elif delta_total_seconds < 86400:
						hours_left = max(1, math.ceil(delta_total_seconds / 3600))
						heading = '%s · %s%d שעות נותרו' % (name, RLM, hours_left)
						body = 'פג תוקף: %s %s' % (date_str, time_str)
						time_ms = 10000
					else:
						days_left = max(1, math.ceil(delta_total_seconds / 86400))
						heading = '%s · %s%d ימים נותרו' % (name, RLM, days_left)
						body = 'פג תוקף: %s' % date_str
						time_ms = 8000 if days_left <= 7 else 6000

					# Per-service icon: use rd.png/torbox.png/etc. if bundled.
					icon_key = {'Real Debrid': 'realdebrid', 'AllDebrid': 'alldebrid', 'Premiumize': 'premiumize',
								'Offcloud': 'offcloud', 'EasyDebrid': 'easydebrid', 'TorBox': 'torbox'}.get(name, '')
					icon_path = os.path.join(kodi_utils.addon_info('path'), 'resources', 'media', 'icons', '%s.png' % icon_key)
					if not os.path.exists(icon_path): icon_path = kodi_utils.addon_info('icon')

					# Stagger by 1s so multiple banners don't replace each other instantly.
					if banner_count > 0: _time.sleep(1.0)
					xbmcgui.Dialog().notification(heading, body, icon_path, time_ms)
					banner_count += 1
					kodi_utils.logger('gears', 'DebridSubscriptionCheck: %s -> %s | %s' % (name, heading, body))
				except Exception as inner:
					kodi_utils.logger('gears', 'DebridSubscriptionCheck %s failed: %s' % (name, str(inner)))
					continue
		except Exception as e:
			kodi_utils.logger('gears', 'DebridSubscriptionCheck failed: %s' % str(e))
##############################################################


class gearsMonitor(Monitor):
	def __init__ (self):
		Monitor.__init__(self)
		self.startServices()

	def startServices(self):
		SetAddonConstants().run()
		DatabaseMaintenance().run()
		SyncSettings().run()
		OnUpdateChanges().run()
		AddonXMLCheck().run()
		Thread(target=CustomFonts().run).start()
		Thread(target=TraktMonitor().run).start()
		Thread(target=UpdateCheck().run).start()
		Thread(target=WidgetRefresher().run).start()
		AutoStart().run()
		# KODIRDIL - Debrid subscription banner (RD/AD/PM/OC/ED/TB). Threaded so slow API calls
		# can't delay boot.
		Thread(target=DebridSubscriptionCheck().run).start()

	def onNotification(self, sender, method, data):
		if method in ('GUI.OnScreensaverActivated', 'System.OnSleep'):
			kodi_utils.set_property(pause_services_prop, 'true')
			kodi_utils.logger('OnNotificationActions', 'PAUSING gears Services Due to Device Sleep')
		elif method in ('GUI.OnScreensaverDeactivated', 'System.OnWake'):
			kodi_utils.clear_property(pause_services_prop)
			kodi_utils.logger('OnNotificationActions', 'UNPAUSING gears Services Due to Device Awake')

kodi_utils.logger('gears', 'Main Monitor Service Starting')
gearsMonitor().waitForAbort()
kodi_utils.logger('gears', 'Main Monitor Service Finished')
