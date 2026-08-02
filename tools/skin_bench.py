#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Benchmark Kodi skin startup performance on an Android box (the Xiaomi).

Why this exists: the skin picker calls Estuary "הכי מהיר" and nobody ever
measured it. The ranking only matters on the WEAKEST device, so this runs on
the box, not on the desktop.

Design goals (Asaf, 2026-08-02): "as accurate as possible" and "must not take
long".

Accuracy
  * every number comes from kodi.log's own millisecond timestamps -- no
    stopwatch, no screenshot timing, no human in the loop
  * the skin is selected by editing guisettings.xml while Kodi is STOPPED, so
    every run is a genuine cold boot straight into the target skin -- no
    switch dialog, no ReloadSkin, no warm caches from the previous skin
  * N runs per skin, MEDIAN reported (one slow outlier cannot skew a ranking)
  * the log is truncated before each run, so parsing can never pick up a
    previous run's lines

Speed
  * fully unattended: force-stop -> set skin -> start -> wait for markers
  * waits are event-driven (poll the log for the marker) with hard timeouts,
    never a fixed sleep

Usage
  python tools/skin_bench.py --device 192.168.1.143:5555 --runs 3
  python tools/skin_bench.py --device ... --skins skin.estuary,skin.nimbus
"""
import argparse
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

KODI_PKG = 'org.xbmc.kodi'
KODI_ACT = 'org.xbmc.kodi/.Splash'
DATA = '/sdcard/Android/data/%s/files/.kodi' % KODI_PKG
LOG = DATA + '/temp/kodi.log'
GUISETTINGS = DATA + '/userdata/guisettings.xml'

# stage -> regex over a kodi.log line. Ordered by when they happen.
MARKERS = [
    ('start',        r'Starting Kodi \('),
    ('gui_ready',    r'CApplication::CreateGUI|using the default windowing system'),
    ('skin_loading', r'Loading custom window XMLs from skin path'),
    ('home_loaded',  r'Loading skin file: Home\.xml'),
    ('startup_done', r'Loading skin file: Startup\.xml|initialize done'),
]
# what we consider "home is usable"
FINAL = 'home_loaded'


def _reconnect(dev, hard=False):
    """Re-establish the device link. `hard` also restarts the LOCAL adb server:
    on 2026-08-02 the Windows adb daemon itself wedged ('protocol fault') and
    only kill-server/start-server recovered it -- the device was reachable
    (ping fine) the whole time."""
    if hard:
        subprocess.run(['adb', 'kill-server'], capture_output=True, timeout=30)
        time.sleep(2)
        subprocess.run(['adb', 'start-server'], capture_output=True, timeout=30)
    subprocess.run(['adb', 'connect', dev], capture_output=True, timeout=30)
    r = subprocess.run(['adb', 'devices'], capture_output=True, text=True,
                       timeout=30)
    return ('%s\tdevice' % dev) in (r.stdout or '')


def sh(dev, cmd, timeout=60, retries=3):
    """adb shell with reconnect-and-retry.

    The Xiaomi's Wi-Fi adb drops mid-run. A dead connection makes adb return
    EMPTY output, which the first version of this harness took at face value --
    it read guisettings.xml as \"0 bytes\" and aborted a whole run over a
    connection blip (2026-08-02). Empty/erroring output now triggers a
    reconnect and retry; only a genuine empty after a live round-trip is
    returned.
    """
    last = ''
    for attempt in range(retries):
        try:
            r = subprocess.run(['adb', '-s', dev, 'shell', cmd],
                               capture_output=True, text=True, timeout=timeout,
                               errors='replace')
            err = (r.stderr or '')
            # NOTE: adb shell returns the REMOTE command's exit code, so a
            # non-zero status is usually normal (`pidof` with no process exits
            # 1). Judging connectivity by returncode made the harness treat a
            # correctly-stopped Kodi as a dead link and abort the run after
            # three pointless reconnects (2026-08-02). Only adb's OWN transport
            # errors, which it reports on stderr, mean the link is down.
            if not re.search(r'device .*not found|device offline|protocol fault|'
                             r'error: closed|no devices', err, re.I):
                return (r.stdout or '').replace('\r', '')
            last = err.strip()
        except subprocess.TimeoutExpired:
            last = 'timeout'
        # soft reconnect first, hard (local adb server restart) on the last try
        _reconnect(dev, hard=(attempt == retries - 2))
        time.sleep(2)
    raise RuntimeError('adb unreachable after %d tries (%s)' % (retries, last[:80]))


def awake(dev):
    return 'mWakefulness=Awake' in sh(dev, 'dumpsys power | grep mWakefulness')


def wake(dev):
    """The box sleeps; never trust it to be awake (see memory adb-device-driving)."""
    if awake(dev):
        return True
    sh(dev, 'input keyevent KEYCODE_WAKEUP')
    time.sleep(1.5)
    return awake(dev)


def stop_kodi(dev):
    sh(dev, 'am force-stop %s' % KODI_PKG)
    for _ in range(20):
        if not sh(dev, 'pidof %s' % KODI_PKG).strip():
            return True
        time.sleep(0.5)
    return False


def set_skin(dev, skin_id):
    """Rewrite lookandfeel.skin in guisettings.xml with Kodi STOPPED.

    Kodi rewrites guisettings.xml from memory on exit, so this MUST happen
    while the process is down (the project's standing rule).
    """
    raw = ''
    for _ in range(5):          # FUSE can lag right after a force-stop
        raw = sh(dev, 'cat %s' % GUISETTINGS, timeout=120)
        if 'lookandfeel' in raw or len(raw) > 1000:
            break
        time.sleep(2)
    if 'lookandfeel' not in raw:
        raise RuntimeError('guisettings.xml unreadable (%d bytes)' % len(raw))
    new, n = re.subn(r'(<setting id="lookandfeel\.skin"[^>]*>)[^<]*(</setting>)',
                     r'\g<1>%s\g<2>' % skin_id, raw)
    if not n:      # setting absent (self-closing / never set) -> insert one
        new = raw.replace('</settings>',
                          '    <setting id="lookandfeel.skin">%s</setting>\n</settings>'
                          % skin_id)
    local = os.path.join(os.environ.get('TEMP', '.'), '_gs.xml')
    with io.open(local, 'w', encoding='utf-8', newline='') as fh:
        fh.write(new)
    subprocess.run(['adb', '-s', dev, 'push', local, GUISETTINGS],
                   capture_output=True, timeout=120)
    back = sh(dev, "grep -o 'lookandfeel.skin\"[^<]*<[^>]*>[^<]*' %s | head -1" % GUISETTINGS)
    return skin_id in back


def start_kodi(dev):
    sh(dev, 'am start -n %s' % KODI_ACT)


def read_log(dev):
    return sh(dev, 'cat %s' % LOG, timeout=120)


def run_once(dev, skin_id, timeout=90):
    """One cold boot. Returns {stage: seconds-from-start} or None on timeout."""
    if not wake(dev):
        raise RuntimeError('device will not wake')
    stop_kodi(dev)
    if not set_skin(dev, skin_id):
        raise RuntimeError('could not set skin to %s' % skin_id)
    sh(dev, ': > %s' % LOG)          # truncate: no stale lines can be parsed
    time.sleep(1.0)
    t_launch = time.time()
    start_kodi(dev)

    seen, deadline = {}, time.time() + timeout
    while time.time() < deadline:
        txt = read_log(dev)
        for stage, pat in MARKERS:
            if stage in seen:
                continue
            m = re.search(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}).*(?:%s)' % pat,
                          txt, re.M)
            if m:
                seen[stage] = m.group(1)
        if FINAL in seen:
            break
        time.sleep(1.0)

    if FINAL not in seen:
        return None

    # Which skin did Kodi ACTUALLY load? A skin that is present on disk but
    # DISABLED in Addons33.db makes Kodi fall back to Estuary without a word --
    # so the first AF3 runs were Estuary booting under an AF3 label, and the
    # numbers looked plausible (2026-08-02). Never trust the setting; read the
    # skin path Kodi logged.
    txt = read_log(dev)
    m = re.search(r'Loading custom window XMLs from skin path .*?/addons/([^/]+)/', txt)
    loaded = m.group(1) if m else '?'
    if loaded != skin_id:
        raise RuntimeError('asked for %s but Kodi loaded %s (skin disabled?)'
                           % (skin_id, loaded))

    def secs(stamp):
        h, m, s = stamp.split(' ')[1].split(':')
        return int(h) * 3600 + int(m) * 60 + float(s)

    # All stages are relative to Kodi's OWN "Starting Kodi" line, so the number
    # is the app's startup cost and not our adb/launch latency.
    t0 = secs(seen['start'])
    out = {k: round(secs(v) - t0, 2) for k, v in seen.items()}
    out['_wall'] = round(time.time() - t_launch, 2)   # for sanity only
    return out


def bench(dev, skins, runs):
    results = {}
    for skin in skins:
        results[skin] = []
        for i in range(runs):
            print('  %-42s run %d/%d ... ' % (skin, i + 1, runs), end='', flush=True)
            try:
                r = run_once(dev, skin)
            except Exception as e:
                print('ERROR %s -- reconnecting and retrying once' % e)
                try:
                    _reconnect(dev)
                    time.sleep(3)
                    r = run_once(dev, skin)
                except Exception as e2:
                    print('    retry also failed: %s' % e2)
                    continue
            if not r:
                print('TIMEOUT')
                continue
            results[skin].append(r)
            print('home at +%.2fs' % r[FINAL])
    return results


def report(results):
    print('\n%-42s %8s %8s %8s  %s' % ('skin', 'gui', 'skin_xml', 'HOME', 'runs'))
    print('-' * 84)
    rank = []
    for skin, runs in results.items():
        if not runs:
            print('%-42s %s' % (skin, 'no successful runs'))
            continue
        def med(k):
            vals = [r[k] for r in runs if k in r]
            return statistics.median(vals) if vals else float('nan')
        home = med(FINAL)
        rank.append((home, skin))
        print('%-42s %8.2f %8.2f %8.2f  %s'
              % (skin, med('gui_ready'), med('skin_loading'), home,
                 ', '.join('%.2f' % r[FINAL] for r in runs)))
    print('-' * 84)
    for i, (home, skin) in enumerate(sorted(rank), 1):
        print('  #%d  %-40s %.2fs to home' % (i, skin, home))
    return sorted(rank)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--device', required=True)
    ap.add_argument('--runs', type=int, default=3)
    ap.add_argument('--skins', default='')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    skins = [s for s in a.skins.split(',') if s] or [
        'skin.estuary', 'skin.arctic.fuse.3', 'skin.nimbus',
        'skin.arctic.zephyr.2.resurrection.mod']
    print('benchmarking %d skin(s) x %d run(s) on %s' % (len(skins), a.runs, a.device))
    res = bench(a.device, skins, a.runs)
    ranking = report(res)
    if a.out:
        with io.open(a.out, 'w', encoding='utf-8') as fh:
            json.dump({'results': res, 'ranking': ranking}, fh,
                      ensure_ascii=False, indent=1)
        print('\nsaved -> %s' % a.out)
