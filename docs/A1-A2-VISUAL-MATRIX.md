# A1/A2 visual verification — findings (Gears side)

## Verified VISUALLY (menu opened on screen, items rendered)
| combo | חיבור שירותים | תחזוקה |
|---|---|---|
| Gears + Zephyr  | 3 TorBox tiles, icons OK | 4 tiles, broom/info/refresh icons OK |
| Gears + AF3     | 3 TorBox tiles, icons OK | 4 tiles, broom/info/refresh icons OK |
| Gears + Estuary | 3 TorBox tiles, gold art OK | 4 tiles, **generic gear icons**, **different wording** |
| Gears + Nimbus  | 3 items, **plain list, English heading "TorBox Services", ".." exposed** | 4 tiles, correct icons + wording |

GEARS SIDE COMPLETE: all 4 skins opened on screen, both menus, items rendered.

## Findings
1. WORDING INCONSISTENCY (cosmetic): Estuary labels the cache actions
   "ניקוי מטמון Gears" / "ניקוי מטמון GearsAI", while AF3 + Zephyr label the
   same actions "ניקוי קאש Gears" / "ניקוי קאש Gears AI". Estuary's menu comes
   from favourites.xml (variant-shipped), the others from the wizard's
   maintenance_folder -- two separate label sources that drifted apart.
2. MISSING ICONS (cosmetic): on Estuary all four תחזוקה tiles render the generic
   Kodi gear icon. AF3/Zephyr render the wizard's bundled art (broom.png,
   broom-ball.png, circle-info.png, arrows-rotate.png) because those come from
   maintenance_folder. Estuary's favourites entries carry no icon.
3. PARITY GAP (from verify_variants, confirmed): zephyr-pov-tmdb ships a
   power-menu "ניקוי קאש POV" via skin-overrides; zephyr-gears-tmdb has no
   skin-overrides dir at all -> no Gears equivalent in the power menu.
   Estuary DOES ship a Gears cache-clear in favourites, so this is Zephyr-Gears
   specific. Cache clearing still reachable everywhere via תחזוקה.

Note: all items are PRESENT and functional in every combo checked. The three
findings above are consistency/polish, not breakage.

4. NIMBUS SERVICES PRESENTATION (cosmetic): on Nimbus, חיבור שירותים renders as a
   PLAIN LIST with no icons/art, the breadcrumb shows the raw English folder name
   "TorBox Services" (every other skin shows Hebrew), and a bare ".." parent entry
   is exposed to the user. AF3/Zephyr/Estuary all render tiles with icons and a
   Hebrew heading. Nimbus's תחזוקה is fine (correct icons + "ניקוי קאש" wording)
   -- it is only the services folder that presents rawly.
5. GEARS MOVIES SUBMENU IS ENGLISH on Nimbus (Trending / Popular / Premieres /
   Latest Releases / ...). Noticed in passing while navigating; worth confirming
   whether other skins localize it.

## POV side

| combo | חיבור שירותים | תחזוקה |
|---|---|---|
| POV + Zephyr | **2 tiles only**, and the first one launches **REAL-DEBRID pairing** | 4 tiles, first = ניקוי קאש POV (engine-correct) |

### FINDING 6 — HIGH: POV's "חיבור שירותים" opens Real-Debrid pairing, not TorBox
On POV + Zephyr the services row has only TWO tiles (Gears has three):
  * חיבור שירותים        -> plugin://plugin.video.pov/?mode=myservices
  * פרטי חשבון TorBox    -> plugin://plugin.video.pov/?mode=torbox.show_account_info
Pressing the first one does NOT open a list of services -- it immediately starts a
**Real-Debrid device-authorisation flow** (QR code + PIN + https://real-debrid.com/device,
15-minute countdown). Asaf uses **TorBox only** (confirmed 2026-07-31), so on a POV
build the primary "connect services" action sends the user to the wrong provider.
Gears by contrast offers three explicit TorBox entries (connect / account / disconnect).

Also missing on POV: any **disconnect** action (Gears has התנתק מ-TorBox).

Labels also differ between engines for the same thing:
  Gears: התחבר ל-TorBox / פרטי מנוי TorBox / התנתק מ-TorBox
  POV:   חיבור שירותים   / פרטי חשבון TorBox / (none)

I backed out of the pairing screen without completing it -- nothing was linked.

### Verified good on POV + Zephyr
* תחזוקה = 4 tiles with correct icons, and the cache tile is **ניקוי קאש POV**
  (never the Gears cleaner) -- the engine-aware branch works on a live box.
* "ניקוי קאש Gears AI" keeps its name on POV, which is correct: GearsAI is the
  shared subtitle addon.
* Install was clean: only plugin.video.pov on disk, Gears fully removed.
* Cross-source keep offered only source-agnostic groups (Debrid / Trakt / user
  addons), staged 12 items -- no Gears content data carried over.
* Kodi did NOT self-relaunch after the install hard-exit (60s wait), confirming
  the Android relaunch is unreliable and validating the 2.4.149 honest messaging.

| POV + Estuary | 3 TorBox tiles (connect/account/disconnect), gold art OK | 4 tiles, ניקוי מטמון POV, generic icons + "מטמון" wording |

IMPORTANT SCOPING: POV + Estuary services are CORRECT (3 TorBox tiles incl. disconnect,
via favourites.xml). So finding #6 (RD pairing + missing disconnect) is **POV+ZEPHYR
SPECIFIC** -- the zephyr-pov skinshortcuts services submenu, not POV in general.
The Estuary "מטמון" wording + generic-icon findings (#1, #2) reproduce on POV too, so
those are Estuary-specific across both engines.

## POV + AF3 (verified visually)

### FINDING 7 — HIGH: POV + AF3 "חיבור שירותים" widget is EMPTY on screen
The חיבור שירותים nav item opens a widget headed "שירותי TorBox" but the body shows
"אין תוצאות" (no results) + a sad-face placeholder -- zero tiles. This is the exact
"empty by mistake" case. Verified: waited 8s (not a load delay), still empty; the
widget path also returns 0 via RPC:
  plugin://plugin.video.pov/?mode=navigator.build_shortcut_folder_list&shortcut_folder=True&name=Connect+Services  -> 0 items
NOT a seeding failure: the switch log shows "seeded 2 POV shortcut folder(s)" for
AF3. So POV seeds 2 shortcut folders but this widget query returns none of them --
a mismatch inside POV's build_shortcut_folder_list (needs the POV overlay code, not
a config change).

### POV services now renders THREE different ways across skins (all same POV addon):
  * Zephyr  : pressing חיבור שירותים launches Real-Debrid device pairing (finding #6)
  * Estuary : 3 correct TorBox tiles (connect/account/disconnect) via favourites.xml
  * AF3     : empty "שירותי TorBox / אין תוצאות" widget (finding #7)
This per-skin divergence on the SAME provider is the core issue -- the POV services
surface is only correct on Estuary.

## FULL MATRIX COMPLETE (both engines x 4 skins, contents + opened on screen)

| combo | חיבור שירותים | תחזוקה |
|---|---|---|
| Gears+Zephyr  | 3 TorBox tiles ✓ | 4 tiles ✓ |
| Gears+Estuary | 3 TorBox tiles ✓ (gold art) | 4 tiles, generic icons, "מטמון" wording |
| Gears+AF3     | 3 TorBox tiles ✓ | 4 tiles ✓ |
| Gears+Nimbus  | 3 items ✓ (plain list, English "TorBox Services" head, "..") | 4 tiles ✓ |
| POV+Zephyr    | ✗ launches Real-Debrid pairing | 4 tiles ✓ |
| POV+Estuary   | 3 TorBox tiles ✓ (gold art) | 4 tiles, generic icons, "מטמון" |
| POV+AF3       | ✗ EMPTY "אין תוצאות" | 4 tiles ✓ |
| POV+Nimbus    | ✗ EMPTY "Connect Services / .." | 4 items ✓ (plain list) |

### HEADLINE FINDINGS
1. **[ROOT-CAUSED + FIXED in 2.4.151] POV services empty on AF3/Nimbus.**
   The wizard seeded POV's Connect-Services folder with `repr()`; POV reads
   it with `json.loads`, which failed -> empty widget. Fixed with `json.dumps`.
   Verified on-device: POV+Nimbus services now renders its 2 entries.
   STILL OPEN (content/presentation, not the bug): POV ships 2 service entries
   vs Gears' 3 (no disconnect); Zephyr+POV launches Real-Debrid pairing; Nimbus
   shows an English breadcrumb. See memory pov-gears-serialization.
1. **POV services broken on 3 of 4 skins.** Gears services are correct on ALL
   four skins (3 TorBox tiles). POV services are correct ONLY on Estuary; on
   Zephyr it launches Real-Debrid device-pairing (Asaf uses TorBox), and on AF3 +
   Nimbus the widget is EMPTY. Estuary works only because it hardcodes the 3
   TorBox entries in favourites.xml instead of using POV's dynamic Connect
   Services folder, which returns 0 items. This is a POV-overlay issue
   (build_shortcut_folder_list / the seeded shortcut folder), not a config fix.
2. **תחזוקה is SOLID on all 8 combos** — 4 items, engine-correct cache tile
   (ניקוי קאש POV on POV, ניקוי קאש Gears on Gears), never the wrong engine. This
   is exactly what the new CI test test_maintenance_folder_contents locks in.
3. **Estuary maintenance polish** (both engines): "ניקוי מטמון" wording vs the
   "ניקוי קאש" used everywhere else, and generic gear icons instead of the
   branded broom/info/refresh art. Cosmetic; from favourites.xml vs the wizard
   folder.
4. **Nimbus presents folders rawly**: services + maintenance render as plain
   lists with ENGLISH headings ("TorBox Services" / "Connect Services") and an
   exposed ".." parent, where the other skins show Hebrew-headed tiles. POV's
   movies submenu is also English on Nimbus (Trending/Popular/Premieres...).
5. **zephyr-pov-tmdb** ships a power-menu cache-clear that **zephyr-gears-tmdb**
   lacks (no skin-overrides dir) — from the CI checker, engine-asymmetric.
