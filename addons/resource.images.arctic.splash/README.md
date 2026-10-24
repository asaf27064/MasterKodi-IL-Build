# Arctic Splash

Kodi image resource `resource.images.arctic.splash`, version **1.1.0**.

One package contains Fuji, Countryside and Tokyo. The existing skin keeps Fuji
as its startup scene and exposes all three through the scene preview. Weather
cards remain in `resource.images.arctic.weather`. No Python runs in Kodi.

## Layout and contract

- `resources/manifest.json`: version 2 scene index and delivery contract.
- `resources/layers/`: Fuji layers plus shared sky, celestial, cloud, glow and haze artwork.
- `resources/scenes/countryside/`: unique landscape, foreground, water and stream frames.
- `resources/scenes/tokyo/`: unique street and hourly illumination artwork.
- `resources/shared/conditions/`: common Countryside/Tokyo weather veils.
- `resources/effects/`: common foliage, precipitation and small effect assets.
- `resources/masks/`: shared white-RGB, lossless-alpha diffuse textures.
- `resources/catalog.json`: asset hashes, dimensions, original source aliases and encodings.

All scene-manifest image and diffuse paths are relative to `resources/`.
Composite by applying a layer's optional diffuse alpha, then ordinary
straight-alpha source-over at its declared opacity. An RGB file with a diffuse
entry is opaque on its own: the diffuse is required. Layer dimensions and
registered positions are unchanged.

The skin must use `special://home/addons/resource.images.arctic.splash/resources/`
for these files, with `compile="false"`. This preserves WebP delivery files.
The skin dependency now requires version 1.1.0; earlier consumers do not
understand the extracted masks or renamed large PNG layer files.

## Shared transparency and motion

Clouds share five weather mattes across their hourly colors. The original/mirrored
loop and its flipped lake reflection apply the same matte and orientation.
Diffuse filenames are literal Kodi texture attributes: weather changes select
separate visible controls instead of putting dynamic `$INFO`/`$VAR` labels in
`diffuse`. Seasonal scene controls use the same pattern for seasonal masks.

Fuji water uses one shoreline matte. Two additional lossless masks combine that
matte with the original alternating ripple bands, so the existing opposing
three-pixel motion still uses one diffuse per draw. The combination is stored at
8-bit alpha precision. Cloud and lake lightning flashes use the matching masks.

Countryside keeps its seasonal water, foreground and landscape mattes; pond
strips share a matte per season/band. Its 48-frame stream loops retain 80 ms
per frame, all hourly/seasonal selections, the 456 x 252 source frame size,
and the 760 x 420 display rectangle at (480, 660). Each seasonal `multiimage`
control applies its fixed matte through the `imagepath` diffuse attribute.

Tokyo shares a street matte per season. Café illumination and residential
window schedules retain all existing hourly selections. Snow, rain, wind,
lightning, foliage and live preview selectors keep their existing behavior.

## Compression and size

The approved mixed profile uses WebP settings 50 and 60 for large delivery
textures, choosing a smaller original or exact lossless WebP when available.
Skies remain original JPG and small effects/sprites remain original PNG. Large
PNG layer families consistently use `.webp` so selectors have predictable paths.
Neither dimensions nor state/frame coverage is reduced. Alpha stays exact in
embedded channels or separate lossless masks; RGB compression is lossy.

The entire distributable, including documentation and offline build tools, must
stay below **100,000,000 bytes**. Git history, caches, preserved originals and
visual reviews are outside the distributable. Small repeated alias files remain
where keeping a predictable hourly selector is simpler than adding lookup logic;
large common textures are shared across scenes.

## Rebuilding offline

The original three add-on directories, previous authoring tools, source masters
and provenance were preserved before conversion outside the distributed package.
They are immutable build inputs. **Do not rebuild from installed delivery
textures or recompress them again.** Source SHA-256 checks enforce this.

With Pillow installed, run the offline builder against that original source set:

```text
python tools/rebuild.py --sources /path/to/original-addons --output /path/to/new/resource.images.arctic.splash
```

`--sources` contains `resource.images.arctic.splash`,
`resource.images.arctic.countryside` and `resource.images.arctic.tokyo`.
The output must be a new directory outside both the installed package and
original sources. `--verify-bytes` additionally requires the recorded encoded
bytes, using the Pillow/libwebp versions in `tools/build-plan.json`. The plan
records original hashes, selected encodings and mask recipes. No original
source paths or private absolute paths are needed in the distributed package.

The builder checks source identity, image dimensions, exact alpha, extracted
RGB/matte reconstruction and total package size. Broader verification and
review renders stay outside the add-on. Original prompts and earlier scene
builders are in the preserved source archive; the new builder reproduces
delivery conversion and merging, not the initial artwork generation.

## Validation status

The merge is checked outside Kodi. Pixel reconstruction checks do not establish
GPU filtering, memory/performance behavior or visual approval inside Kodi.
Launching or controlling Kodi for testing requires separate explicit typed
approval in chat. See `AUTHORING.md` for the visual workflow and `AGENTS.md`
for contributor instructions.
