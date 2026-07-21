# Piers (Kodi 22) POV skin-menu variants

POV as an INSTALL OPTION alongside Gears on Piers, same as Kodi 21. Piers does
NOT have the 21.3 widget crash, so POV uses plain POV widgets (no tmdb needed).

## What's SHARED with Kodi 21 (not duplicated here — same files, addon_data is
## Kodi-version-independent):
- POV addon + Hebrew overlay incl. ratings port (overlays-staging/plugin.video.pov)
- userdata config-variants: nimbus cpath seed, af3 skinvariables nodes,
  estuary favourites, POV views.db/navigator.db folders/settings
  (these are script.*/plugin.* addon_data — identical on Kodi 21 and 22)

## What's PIERS-SPECIFIC (here — different skin menu-file structure):
- zephyr-piers-pov/skin-overrides/menus.xml + templates.xml
  (Piers Zephyr uses the skin's OWN shortcuts menu system, NOT
  script.skinshortcuts like K21). BigSquare network-logo fix PRESERVED.
- estuary-piers-pov/skin-overrides/*.xml (same structure as K21 estuary)

## OPEN (needs a live Piers box / base skin files):
- Verify POV loads on Kodi 22 (deps OK: xbmc.python 3.0.0 = Matrix API,
  forward-compatible; runtime unverified).
- Nimbus/AF3 Piers overlays only add fonts/views/strings; their MENUS come
  from the base skin (DenDyGH, not in this repo). IF Piers nimbus/af3 use the
  same cpath/skinvariables-nodes menu system as K21, the shared userdata
  variants apply directly. Needs confirmation on a Piers box.
- big_square (BigSquare widgetAspect) is the network-logo-not-cut fix, on the
  networks + torbox + maintenance folder widgets in zephyr menus.xml.
