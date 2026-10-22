# Arctic Weather

Kodi image resource add-on `resource.images.arctic.weather`, version 1.0.3.

## Weather cards

`resources/weather/` contains 49 full-bleed 320 x 720 portrait JPEG cards:
one distinct Japanese illustration for each Kodi weather fanart code `0` through
`47`, plus `na` for unavailable conditions. Scenes include Mount Fuji, cherry
blossoms, Tokyo streets, Kyoto temples, mountain villages, gardens and coasts.
The artwork depicts the code's precipitation, visibility, wind, cloud cover,
temperature or day/night condition in the same painted style as the splash.

Use unpadded code filenames, matching Kodi's standard fanart codes:

`special://home/addons/resource.images.arctic.weather/resources/weather/32.jpg`

The skin uses direct add-on paths, following the layered splash pattern, and
avoids resource-handler loading failures observed for the cards and fallback.

Unavailable weather uses `weather/na.jpg`. `resources/weather/manifest.json`
records all code meanings, scenes, individual weather prompts and the common
style prompt. The mapping follows the
[official Kodi weather codes](https://kodi.wiki/view/Weather_addons#Weather_Codes).
The cards are generated individually using built-in imagegen with the Tokyo
alley illustration from this project as a style reference. They are opaque RGB
JPEGs at quality 90 with no chroma subsampling, borders or overlay labels.

`tools/weather-cards-preview.jpg` provides a labeled contact sheet. Run
`python tools/prepare_weather_cards.py` to validate coverage, dimensions and
distinct artwork and recreate the contact sheet (Pillow required). To prepare
new built-in imagegen outputs, pass `--sources sources.json`, containing a list
of objects with `code` and absolute original image `path` fields. Existing cards
are preserved; resizing uses proportional center fitting and Lanczos sampling.

## Watercolour weather cards

`resources/weather-watercolour/` contains a separate set of 49 full-bleed
320 x 720 portrait cards for the same codes `0` through `47` and `na`.
The original illustrated set remains in `resources/weather/` with its existing
filenames and artwork intact. Each style has its own manifest and contact sheet.

The watercolours use broad transparent washes, soft edges, paper texture and
simplified architecture and foliage, matching the approved Sunny / code 32
draft. The shared prompt and per-code scene and weather requirements are recorded
in `resources/weather-watercolour/manifest.json`. Every card is authored with
built-in imagegen; code 32 uses the approved draft itself.

The watercolour set, including its `na.jpg` fallback, is available at:

`special://home/addons/resource.images.arctic.weather/resources/weather-watercolour/32.jpg`

`tools/weather-watercolour-preview.jpg` shows the full labeled set. Validate it
and regenerate the preview with
`python tools/prepare_weather_cards.py --style watercolour`.
Pass `--sources sources.json` to prepare newly generated originals. The
preparation tool defaults to the original illustrated set, so both styles
can be maintained separately without overwriting existing cards.

## Woodblock weather cards

`resources/weather-woodblock/` contains 49 full-bleed 320 x 720 RGB JPEG cards
for codes `0` through `47` and `na`. The style follows the approved Sunny
(code 32) and Severe thunderstorms (code 3) drafts: flat Japanese woodblock
shapes, paper grain, layered muted blues, ivory highlights and vermilion accents.
The two approved drafts are used directly, with proportional resizing only.
Each remaining condition was generated individually with built-in imagegen,
using both approved drafts as style references. Codes 30 and 36 received a
final palette refinement. Day/night, cloud coverage and precipitation distinctions
follow the existing condition mapping.

The static woodblock set, including its `na.jpg` fallback, is available at:

`special://home/addons/resource.images.arctic.weather/resources/weather-woodblock/32.jpg`

`resources/weather-woodblock/manifest.json` records the shared prompt, individual
scene and weather requirements, and refinement prompts. Approved draft prompts
remain in `tools/weather-japanese-draft-32.prompt.txt` and
`tools/weather-japanese-draft-3.prompt.txt`. The generation source paths are
recorded in `tools/weather-woodblock-sources.json`; final JPEGs are self-contained
and do not depend on the original generation files.

`tools/weather-woodblock-preview.jpg` is the complete labeled contact sheet.
Validate coverage, size and distinct artwork and regenerate it with
`python tools/prepare_weather_cards.py --style woodblock`.
To prepare additional originals, use `--sources sources.json` as for the other
styles. The original illustrated and watercolour sets remain available in their
own directories.

## Layered animated woodblock cards

`resources/weather-woodblock-animated/` covers the same 49 codes. The skin now
uses these layers in MyWeather and the weather hub.
Each code has one static `base.jpg`, three folders of five ordered single-frame
RGBA PNGs, and one `lightning.png`:

- `atmosphere/00.png` through `04.png`: masked fog drift or staggered star
  twinkling on night cards, 1200 ms per phase. Twinkles vary existing star pixels;
  the moon, ordinary clouds, trees and leaves stay static. Tornado and hurricane
  cards instead use separated painted cloud layers at 300 ms per phase with a
  100 ms crossfade. Their complete cloud patterns move over static sky plates.
- `surface/00.png` through `04.png`: travelling water ripple bands, raindrop
  rings on wet paving, or localized summer heat shimmer, 300 ms per phase.
  The tornado's ground stays static. Hurricane sea motion and precipitation
  remain separate from the rotating cloud layer.
- `weather/00.png` through `04.png`: condition-specific rain, snow, sleet or hail,
  180 ms per phase. Scattered/isolated precipitation
  is clipped to the corresponding shower regions.
- `lightning.png`: extracted original bolts for thunderstorm codes. The skin
  reuses its existing localized double-flash include on a five-second cycle.

Inactive layers use transparent 1 x 1 placeholders. Active layers are 320 x 720.
Every image is single-frame: no video, GIF, APNG, sprite sheets or playback
scripts. Kodi composites the layers directly using ordered multiimage controls
with no randomization, crossfade or end pause. Each moving layer has at most
five phases; independent timings give rain, fog and water different speeds.
Conditions without any remaining moving effects display their static plate.
Unknown conditions retain a static `na/base.jpg` fallback.

One clean plate was prepared with built-in imagegen for each of 29 precipitation
cards, including the approved thunderstorm draft. Clean plates remove airborne
precipitation and lightning while preserving accumulated snow, ice and the
existing weather setting. The other 20 cards keep their original static JPEGs.
All animated phases are produced by deterministic extraction, masking, drawing,
resampling and alpha compositing. No full scene is regenerated per phase.

`tools/woodblock-vortex-layers/` holds the one-time cloud cutouts, clean sky
masters, fixed scene references and exact built-in imagegen prompts for codes
0 and 2. `python tools/prepare_woodblock_vortices.py` prepares their sky plates
and cloud layers. The tornado uses five cloud-only painted rotation poses,
aligned to the same funnel tip; only those transparent cloud layers vary.
The hurricane rotates a complete cloud disc with fivefold sampling to close
its five-frame loop. There are no drawn
wind-band highlights or stationary vortex patterns under the moving clouds.
The extraction/clean-plate prompt set is in `prompts.json`; cloud-only rotation
pose prompts are in `rotation-poses.prompt.json` in the same source folder.

`tools/woodblock-animation-plan.json` records the exact clean-plate prompt and
generation sources. `tools/woodblock-animation-plates/` stores the resized clean
plates for reproducible builds. `tools/woodblock-animation-regions.json` records
the authored region masks. The runtime `manifest.json` records paths, effects
and timings for all 49 cards. Runtime assets do not depend on generation paths.

Run `python tools/build_woodblock_animation.py` to rebuild the layers and verify
coverage, sizes, alpha transparency and distinct active phases (Pillow and numpy
required). It preserves source artwork and uses cached clean plates. Use
`--partial` during plate preparation or `--codes 3 32` for targeted rebuilds.
`tools/weather-woodblock-animated-preview.jpg` shows one composed phase per code;
`tools/woodblock-animation-review/` contains all five offline review composites
per card. These review JPGs are not used for Kodi playback.

Run `python tools/validate_woodblock_animation.py` to additionally check visible
motion, periodic precipitation loops, the dependency version, skin XML and
the expanded artwork paths for every code. Results are recorded in
`tools/woodblock-animation-validation.json`.

## Landscape woodblock artwork

`resources/weather-woodblock-landscape/` contains 49 static 1280 x 720 RGB JPEGs,
one per code `0` through `47` plus `na`. Each static woodblock portrait was
individually extended with built-in imagegen into a wide landscape composition,
preserving its scene, weather and day/night. The landscape set uses the approved
hail and sleet drafts as style references: broad printed color planes, subtle
paper grain, layered silhouettes and selective architectural detail. Fine
foliage, roof tiles, masonry and surface reflections are reduced while keeping
the character of each scene. The portrait sources and animated assets remain
available.

The current-weather dialog selects this set through the shared
`Weather_Card_Artwork` include's `landscape` parameter. Its unavailable-condition
fallback is `weather-woodblock-landscape/na.jpg`; animated layers are excluded
when the dialog loads. Portrait weather cards retain their existing animation.

The landscape manifest records scene and weather mappings. Generation paths are
recorded in `tools/weather-woodblock-landscape-sources.json`; runtime images are
self-contained. `tools/weather-woodblock-landscape-preview.jpg` shows every
landscape with its weather code. Validate and recreate the preview with
`python tools/prepare_weather_cards.py --style woodblock-landscape`.
Local generation tools, source records and previews are kept in the ignored
`tools/` directory. To rebuild approved artwork from local generation records,
pass `--sources tools/weather-woodblock-landscape-sources.json --replace`.

The skin requires this add-on at version 1.0.3 or newer. Install it in Kodi's
user add-ons directory. The image resource uses type="weather" and
compile="false" to preserve the individual image files.
