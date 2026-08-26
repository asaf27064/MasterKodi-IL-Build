

########### KODIRDIL - our own logger ###########
# 6.08.14 dropped service.py's module-level `from entry import logger` and
# moved the import inside __main__, so the name our banner code logs through
# no longer exists at module scope. Import it lazily rather than restoring a
# top-level import upstream deliberately removed.
def _kodirdil_log(msg):
	try:
		from entry import logger
		logger('POV', msg)
	except Exception:
		pass
################################################

########### KODIRDIL - Debrid subscription banner ###########
# On addon startup, for every debrid service the user has authenticated
# (rd/ad/pm/oc/tb), query its account info and show a Hebrew toast with days
# remaining + expiration date. Ported from the Gears overlay; adapted to POV:
# plain setting ids (no addon prefix), api modules under indexers.* (they moved
# there from debrids/ in 6.08.14), and no EasyDebrid (POV has EasyNews instead,
# which has no subscription expiry).
# Silent no-ops on: service disabled, empty token, network error, missing
# field, or any exception. A startup banner must never break boot.
DEBRID_SUBS = (
	# (display_name, enabled_setting, token_setting, api_module, api_class, field_path, ts_format)
	# Field paths are relative to what account_info() RETURNS: POV's TorBox and
	# AllDebrid _request already unwrap the 'data' envelope (see torbox_api's
	# own days_remaining(), which reads 'premium_expires_at' flat). The Gears
	# overlay's 'data.'-prefixed paths dug into a key that no longer exists, so
	# _dig returned None and the banner silently never showed (Asaf, 2026-08-01).
	('Real Debrid', 'rd.enabled', 'rd.token', 'indexers.real_debrid_api', 'RealDebridAPI', 'expiration',         'iso'),
	('AllDebrid',   'ad.enabled', 'ad.token', 'indexers.alldebrid_api',   'AllDebridAPI',  'user.premiumUntil',  'unix_s'),
	('Premiumize',  'pm.enabled', 'pm.token', 'indexers.premiumize_api',  'PremiumizeAPI', 'premium_until',      'unix_s'),
	('Offcloud',    'oc.enabled', 'oc.token', 'indexers.offcloud_api',    'OffcloudAPI',   'expirationDate',     'unix_ms'),
	('TorBox',      'tb.enabled', 'tb.token', 'indexers.torbox_api',      'TorBoxAPI',     'premium_expires_at', 'iso'),
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
			# NOT strptime. This runs in a boot-time THREAD, and CPython imports
			# time._strptime lazily on FIRST use -- a long-standing race that
			# makes the first strptime call off the main thread raise
			# "AttributeError: module 'time' has no attribute '_strptime'".
			# That is exactly what killed this banner: the field was present and
			# valid ('2026-08-30T11:02:56Z') but parsing returned None on every
			# boot (Asaf, 2026-08-26). POV's own days_remaining() never hit it
			# because it uses fromisoformat -- so do we now.
			# 3.8's fromisoformat is STRICT (Kodi 21 = 3.8, Kodi 22 = 3.11+):
			# it rejects a trailing 'Z' and accepts only 3- or 6-digit
			# fractional seconds. Drop the fraction (we need day resolution)
			# and spell the offset out, so both fleets take the same path.
			s = str(value).strip()
			if s.endswith('Z'): s = s[:-1] + '+00:00'
			if '.' in s:
				head, _, tail = s.partition('.')
				off = ''
				for sign in ('+', '-'):
					if sign in tail: off = sign + tail.split(sign, 1)[1]; break
				s = head + off
			dt = datetime.fromisoformat(s)
			return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
		if fmt == 'unix_s':
			return datetime.fromtimestamp(int(value), tz=timezone.utc)
		if fmt == 'unix_ms':
			return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)
	except Exception as e:
		# Never silent: the swallowed exception here is what made the failure
		# unreadable for three boots.
		_kodirdil_log('kodirdil banner: expiry parse failed on %r as %s -- %s: %s'
		              % (value, fmt, type(e).__name__, e))
		return None
	return None

def _show_debrid_banners():
	import xbmc
	from datetime import datetime, timezone
	from modules.kodi_utils import get_setting, notification
	# Boot race: this thread starts before the skin/GUI is ready, and a toast
	# fired that early is silently dropped. Wait for the window manager, then a
	# small settle. Bounded -- never block more than ~15s.
	mon = xbmc.Monitor()          # hoisted: the retry loop below uses it too
	try:
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
			# One shot at boot is fragile: the network stack is still coming up
			# and _request swallows ConnectionError/Timeout into a None return.
			# Retry a couple of times before giving up (Asaf, 2026-08-26 --
			# the banner had been silently absent on three consecutive boots).
			info = None
			for attempt in range(3):
				info = api_cls().account_info()
				if isinstance(info, dict) and _dig(info, field) is not None: break
				if attempt < 2 and mon.waitForAbort(4): return
			raw = _dig(info, field)
			expiry = _parse_expiry(raw, fmt)
			if expiry is None:
				# Say WHICH of the four failures this was -- the old single
				# message covered all of them and cost a diagnosis round.
				if info is None:
					why = 'account_info() returned None (request failed/timed out)'
				elif not isinstance(info, dict):
					why = 'account_info() returned %s, not a dict (non-JSON reply?)' % type(info).__name__
				elif raw is None:
					why = 'field %r absent; keys=%s' % (field, sorted(info.keys())[:25])
				else:
					why = 'field %r present but unparsable as %s: %r' % (field, fmt, raw)
				_kodirdil_log('kodirdil banner: %s no expiry -- %s' % (name, why))
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
			_kodirdil_log('kodirdil banner shown: %s' % msg)
			xbmc.sleep(1000)
		except Exception as e:
			# Never break boot -- but never be INVISIBLE either: a silent pass
			# here cost a full debugging round (2026-08-01).
			try: _kodirdil_log('kodirdil banner: %s failed: %s' % (name, e))
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
	from entry import POVMonitor
	########### KODIRDIL - fire the debrid banner (non-blocking) ###########
	_start_debrid_banner_thread()
	#########################################################################
	POVMonitor().run()

