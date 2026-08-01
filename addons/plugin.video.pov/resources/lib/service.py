from entry import logger, POVMonitor


########### KODIRDIL - Debrid subscription banner ###########
# On addon startup, for every debrid service the user has authenticated
# (rd/ad/pm/oc/tb), query its account info and show a Hebrew toast with days
# remaining + expiration date. Ported from the Gears overlay; adapted to POV:
# plain setting ids (no addon prefix), api modules under debrids.*, and no
# EasyDebrid (POV has EasyNews instead, which has no subscription expiry).
# Silent no-ops on: service disabled, empty token, network error, missing
# field, or any exception. A startup banner must never break boot.
DEBRID_SUBS = (
	# (display_name, enabled_setting, token_setting, api_module, api_class, field_path, ts_format)
	# Field paths are relative to what account_info() RETURNS: POV's TorBox and
	# AllDebrid _request already unwrap the 'data' envelope (see torbox_api's
	# own days_remaining(), which reads 'premium_expires_at' flat). The Gears
	# overlay's 'data.'-prefixed paths dug into a key that no longer exists, so
	# _dig returned None and the banner silently never showed (Asaf, 2026-08-01).
	('Real Debrid', 'rd.enabled', 'rd.token', 'debrids.real_debrid_api', 'RealDebridAPI', 'expiration',         'iso'),
	('AllDebrid',   'ad.enabled', 'ad.token', 'debrids.alldebrid_api',   'AllDebridAPI',  'user.premiumUntil',  'unix_s'),
	('Premiumize',  'pm.enabled', 'pm.token', 'debrids.premiumize_api',  'PremiumizeAPI', 'premium_until',      'unix_s'),
	('Offcloud',    'oc.enabled', 'oc.token', 'debrids.offcloud_api',    'OffcloudAPI',   'expirationDate',     'unix_ms'),
	('TorBox',      'tb.enabled', 'tb.token', 'debrids.torbox_api',      'TorBoxAPI',     'premium_expires_at', 'iso'),
)

def _dig(obj, dotted):
	for key in dotted.split('.'):
		if not isinstance(obj, dict): return None
		obj = obj.get(key)
	return obj

def _parse_expiry(value, fmt):
	from datetime import datetime, timezone
	try:
		if value is None: return None
		if fmt == 'iso':
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

def _show_debrid_banners():
	import xbmc
	from datetime import datetime, timezone
	from modules.kodi_utils import get_setting, notification
	# Boot race: this thread starts before the skin/GUI is ready, and a toast
	# fired that early is silently dropped. Wait for the window manager, then a
	# small settle. Bounded -- never block more than ~15s.
	try:
		mon = xbmc.Monitor()
		for _ in range(30):
			if xbmc.getCondVisibility('Window.IsVisible(home)') or mon.abortRequested():
				break
			mon.waitForAbort(0.5)
		mon.waitForAbort(2)
		if mon.abortRequested(): return
	except Exception:
		pass
	for name, en_set, tok_set, mod_path, cls_name, field, fmt in DEBRID_SUBS:
		try:
			if get_setting(en_set, 'false') != 'true': continue
			if not get_setting(tok_set, ''): continue
			import importlib
			api_cls = getattr(importlib.import_module(mod_path), cls_name)
			info = api_cls().account_info()
			expiry = _parse_expiry(_dig(info, field), fmt)
			if expiry is None:
				logger('POV', 'kodirdil banner: %s no expiry in account_info' % name)
				continue
			now = datetime.now(timezone.utc)
			remaining = expiry - now
			total_hours = remaining.total_seconds() / 3600.0
			if total_hours <= 0: continue
			# Bidi-safe single line: Hebrew-leading with ONE Latin run at the
			# END. The old 'Name · N ימים נותרו | פג תוקף: date' had Latin at
			# the front + two mixed runs, and Kodi's RTL rendering shuffled it
			# into 'TORBOX · 28 30/08 | ...' (Asaf, 2026-08-01).
			if total_hours < 24:
				msg = 'נותרו %d שעות · בתוקף עד %s · %s' % (
					int(total_hours), expiry.strftime('%d/%m %H:%M'), name)
			else:
				msg = 'נותרו %d ימים · בתוקף עד %s · %s' % (
					int(remaining.days), expiry.strftime('%d/%m'), name)
			notification(msg, 6000)
			logger('POV', 'kodirdil banner shown: %s' % msg)
			xbmc.sleep(1000)
		except Exception as e:
			# Never break boot -- but never be INVISIBLE either: a silent pass
			# here cost a full debugging round (2026-08-01).
			try: logger('POV', 'kodirdil banner: %s failed: %s' % (name, e))
			except Exception: pass

def _start_debrid_banner_thread():
	try:
		from threading import Thread
		t = Thread(target=_show_debrid_banners, name='kodirdil_debrid_banner')
		t.daemon = True
		t.start()
	except Exception:
		pass
#############################################################

if __name__ == '__main__':
	logger('POV', 'Main Monitor Service Starting (%s)' % POVMonitor.ver())
	logger('POV', 'Settings Monitor Service Starting')

	########### KODIRDIL - fire the debrid banner (non-blocking) ###########
	_start_debrid_banner_thread()
	#########################################################################
	POVMonitor().run()

	logger('POV', 'Settings Monitor Service Finished')
	logger('POV', 'Main Monitor Service Finished')

