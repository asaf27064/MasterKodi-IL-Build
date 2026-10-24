# Scenic resource authoring guide

This guide describes how to construct illustrated, layered environmental
resources in the style of Arctic Splash. It also provides an executable brief
for AI agents producing their own similar resources.

The visual principles are recommendations derived from the installed artwork.
The compatibility notes describe this resource and its current Arctic Fuse 3
consumer. Another resource may use different scenery, but must explicitly define
its own layer contract if it departs from this one.

## Version 1.1 delivery contract

The current package combines Fuji, Countryside and Tokyo. The artistic guidance
below primarily describes the original Fuji reference. The current runtime
contract is version 2: see [README.md](README.md), the
[scene index](resources/manifest.json) and [asset catalog](resources/catalog.json).
Large PNG layer families now use WebP. Shared transparency is supplied separately
through literal diffuse masks, including seasonal stream and scenery mattes.
The skin retains live season/weather selection through visible control branches.

Preserved original source add-ons, prompts and older authoring tools live outside
the distributable. Build new delivery copies once using `tools/rebuild.py` and the
recorded plan; do not run original image-processing tools against the repacked
RGB files. The installed files are delivery assets, not source masters.

## Visual direction

Create a calm, immersive landscape with a hand-painted anime background
aesthetic: believable natural forms, painterly shading, readable silhouettes
and atmospheric depth. Keep the painting style consistent across scenery,
weather effects and tiny sprites.

Concentrate fine detail in nearby foliage, bark and rocks. Simplify distant
scenery with softer edges, lower contrast and atmospheric colour. Avoid text,
branding and interface decorations within the artwork.

The reference scene balances detailed foreground vegetation with spacious sky,
distant mountains and reflective water. Preserve that balance when selecting
another subject; repeating Mount Fuji is not a requirement.

## Composition and elements

Build one recognisable place with three depth zones:

| Zone | Typical elements | Role |
| --- | --- | --- |
| Background | Sky, sun or moon, clouds, distant landmark | Scale, lighting and atmosphere |
| Middle distance | Hills, woodland, mist, shoreline | Connect the landmark to the foreground |
| Foreground | Trees, branches, rocks, nearby foliage | Frame the view and establish depth |

Keep a generous central opening. In the reference, a large tree frames the left,
smaller elements frame the right, and the lake leads toward the mountain.
Choose elements for useful roles: water supports reflections and restrained
movement, vegetation communicates season, and clouds and mist express weather.

Lock camera, perspective, horizon, shorelines and major landmarks across all
variants. Change foliage, snow cover, atmosphere and illumination within that
shared geography. Winter can expose branches hidden in summer, but the tree
should remain recognisable. Derive variants from shared masters rather than
independently generating every complete scene.

## Layer contract

The reference uses registered 1920 x 1080 canvases and ordinary source-over
alpha compositing, in this order:

| Order | Directory | Responsibility |
| --- | --- | --- |
| 1 | `01-sky` | Opaque foundation and broad sky colour |
| 2 | `02-sun-moon` | Hourly celestial position and appearance |
| 3 | `03-clouds` | Weather-dependent cloud forms and hourly shading |
| 4 | `04-sunset-sunrise` | Localised twilight glow |
| 5 | `05-mountains` | Seasonal terrain and distant scenery |
| 6 | `06-lake` | Seasonal, weather-dependent, hourly water and reflections |
| 7 | `07-trees` | Seasonal foreground framing |
| 8 | `08-conditions` | Atmospheric and weather overlays |
| 9 | `09-time` | Registered scene lighting and colour treatment |

Author the intended appearance for ordinary alpha-over. Do not require runtime
multiply, screen or other specialist lighting blend modes. Shared white-RGB
diffuse masks may supply transparency before normal alpha-over. Lighting and most reflections
are authored offline; the skin adds animation and additional overlays.

Keep each layer's responsibility clear. Clouds must contain clouds on genuine
transparency, without opaque sky, terrain fragments or tree-shaped extraction
holes. Preserve soft alpha for mist, glows, clouds and fine vegetation. Inspect
edges on both light and dark backgrounds for coloured fringes and debris.

Paint sufficient hidden material beneath overlaps for motion. A mountain-shaped
hole concealed in a still cloud plate becomes visible when that plate drifts.
Keep registered layers at the same canvas size and origin; do not independently
crop them to their visible bounds.

## Palette and illumination

Use a coherent environmental palette with controlled seasonal accents:

| State | Palette direction |
| --- | --- |
| Spring | Blossom pink, soft white, fresh green, clear blue atmosphere |
| Summer | Rich green, yellow-green highlights, blue water and bright sky |
| Autumn | Vermilion, orange, gold and earth tones against cool blue distance |
| Winter | Snow white, icy blue, blue-grey shadows, dark exposed branches |
| Night | Deep navy, indigo, muted violet, restrained moonlit highlights |
| Twilight | Peach, rose or amber near the horizon; cool blue-violet shadows |
| Wet weather | Slate clouds, cooler illumination, subdued environmental colour |

These are descriptive directions, not measured colour specifications. Strong
foreground accents should coexist with quieter sky, water and distant terrain.
Seasonal colours must respond to illumination: orange leaves become subdued at
night rather than staying brightly orange.

Coordinate sky colour, celestial position, cloud shading, terrain lighting and
reflections. Keep shadow detail readable at night and twilight warmth localised
and directionally plausible. Match moving particles to the surrounding light.

The current consumer uses local-clock bands, not astronomical sunrise/sunset:
night before 05:00, dawn transitions at 05:00, 06:00 and 07:00, shared daytime
from 08:00 to 17:00, and evening transitions at 17:00, 18:00 and 19:00 before
night at 20:00. Different layer families alias these periods differently;
celestial and lake plates retain 24 hourly selections.

The skin applies approximately 50% black dimming from 06:00 to 18:00. Review
undimmed source composites and the intended dimmed presentation. Seasons use
calendar months with hemisphere inferred from the selected Kodi region.

## Weather treatment

Weather changes atmosphere, visibility and motion as well as adding weather marks.

| Weather | Treatment in the current consumer |
| --- | --- |
| Clear | Light cloud presence, open atmosphere and readable scenery |
| Partly cloudy | Clear base plates with increased cloud opacity |
| Cloudy | Heavier coverage and subdued illumination |
| Hazy | Veiled distance, soft contrast and translucent drifting atmosphere |
| Rain | Overcast plates, compatible water reflections, moving precipitation |
| Heavy rain | Rain base with stronger precipitation |
| Snow | Rain cloud/lake plates with soft drifting flakes |
| Wind | Cloud base with stronger seasonal particle movement |
| Thunderstorm | Rain between flashes; coordinated storm clouds, lake and illumination |

Keep season and weather independent. Snow weather can occur in any season and
does not automatically select winter scenery. Reuse compatible base plates
instead of creating redundant full-scene variants.

For compatibility, read the current skin selectors. The installed manifest's
weather-code table differs from the consumer's current mappings.

## Water, motion and effects

Author water reflections to match terrain, vegetation and celestial position.
Reduce their clarity and contrast relative to objects above the water. Keep
shoreline geometry fixed and avoid duplicating reflections between layers.

The skin adds faint, vertically flipped and compressed moving cloud reflections.
Lake ripples reuse the lake texture through two feathered horizontal masks,
moving a few pixels in opposite directions. Bands widen toward the viewer and
fade near the distant bank. Supply artwork that tolerates this movement without
exposing seams or shifting the shoreline.

Clouds drift slowly behind the mountains. Original and horizontally mirrored
copies form the repeat; check matching edge density, natural silhouettes and
unobtrusive mirrored joins throughout the loop. Haze moves more slowly.

Seasonal sprites use the same visual vocabulary as the landscape: spring petals,
summer leaves, autumn leaves and soft winter snow particles. Vary size, rotation,
path and timing; fade particles before their paths restart. Give moving pieces
their own hourly lighting variants because they appear above the static grade.
Remove detached pieces from static plates when replacing them with moving ones.

Keep rain fine enough to preserve scenery and snow soft and readable at small
sizes. Allow transparent padding and overscan to conceal tile boundaries. Ambient
seasonal particles are suppressed during rain, snow and thunderstorms. Lightning
must coordinate the cloud plate, lake reflection and brief scene illumination.
Prefer reusable sprites, textures and masks over full-screen animation frames.

## Packaging and source material

Share identical plates and document aliases. Use predictable season, weather and
hour names. A compatible replacement must preserve paths, dimensions, alpha
behaviour, state coverage and assumptions used by the consumer, including the
lake reflection placement and registered lighting masks. Different geography may
require consumer changes; do not promise compatibility based on filenames alone.

The merged resource keeps original JPEG skies and small PNG effects, while large
layers use the approved mixed WebP delivery profile. Shared white-RGB mattes
retain exact alpha. Keep the entire distributable below 100,000,000 bytes,
including documentation and offline build tools, excluding Git and caches.
Use the new offline rebuild command in README.md; earlier source tools and
original inputs remain in the preserved source set outside the distributable.

Never use the merged RGB delivery plates as authoring masters. Source hashes
and encoding/mask recipes are recorded in `tools/build-plan.json`, while
`resources/catalog.json` records current output hashes and source aliases.
Rendering the installed resource must not require executable resource code
or skin-owned Python scripts.

## Workflow for AI agents

1. Inspect the resource README, manifest, representative images, effect assets
   and consumer XML. Distinguish observed behaviour from proposed design choices.
2. Establish whether the request is a compatible replacement or a new resource
   system. Record the destination, subject, camera, palette, supported states,
   layer responsibilities and consumer constraints. Ask only for unresolved
   decisions that materially block the work.
3. Produce a representative daytime master composition for visual review before
   expanding the complete state matrix. Lock geography and depth relationships.
4. Construct clean registered layers, reconstructing hidden scenery where needed.
   Do not treat automatic background removal as sufficient layer authoring.
5. Derive seasonal and lighting variants from shared masters, then weather,
   reflections and small effects. Reuse identical assets through explicit aliases.
6. Record provenance and reproducible build steps. Optimize delivery copies once
   and verify their decoded dimensions and alpha against the intended originals.
7. Validate all manifest references and state coverage. Render representative
   composites and motion samples outside Kodi; inspect them visually and review
   edge joins, registration, reflections, lighting and particle restarts.
8. Report deliverables, validation evidence, unresolved limitations and consumer
   changes required. Obtain explicit typed chat approval before launching or
   controlling Kodi for any further tests.

Keep review sheets, temporary renders and verification scripts outside the
distributed add-on. Automated structural checks do not replace visual review.

### Reusable agent brief

Copy and complete this brief when requesting a similar resource:

> Create a layered scenic image resource using AUTHORING.md as the reference.
> Destination and add-on identity: [path and ID].
> Compatibility target: [existing Arctic Fuse 3 contract / new consumer contract].
> Scene and visual direction: [subject, framing, landmark, depth and mood].
> Required seasons, time states and weather: [coverage].
> Preserve one registered geography across all variants. Use ordinary alpha-over,
> clean transparent layers, coordinated lighting/reflections and reusable small
> effects. Inspect consumer assumptions before claiming compatibility. Record
> masters, provenance, prompts, state aliases and rebuild steps. Present a daytime
> master for review before expanding the state matrix. Validate structure and
> visually inspect composites and motion outside Kodi. Report limitations and
> required consumer changes. Do not launch or control Kodi without explicit typed
> chat approval for the proposed tests.

## Acceptance checklist

- Stable geography, perspective and exact registration across variants.
- Consistent painting style, readable silhouettes and atmospheric depth.
- Clean alpha, sufficient hidden artwork and no coloured extraction fringes.
- Coordinated sky, terrain, foliage, celestial highlights and reflections.
- Readable nights, coherent twilight and suitable contrast under skin dimming.
- Distinct weather treatments with independent season selection.
- Natural cloud joins, fixed shorelines and concealed animation restarts.
- Complete state references, documented aliases and reproducible source material.
- Verified delivery dimensions, alpha preservation and package budget.
- Clear distinction between offline validation, visual approval and Kodi testing.

## Reference implementation

- Resource: [README.md](README.md), [scene index](resources/manifest.json),
  [Fuji manifest](resources/layers/manifest.json), [catalog](resources/catalog.json),
  and [offline builder](tools/rebuild.py).
- Skin `Includes_StartUp.xml`: Fuji layer ordering, lighting and dimming.
- `Includes_StartUp_Clouds.xml`: literal weather mattes, mirrored motion and reflections.
- `Includes_StartUp_Weather.xml`: ripple mattes, precipitation and lightning.
- `Includes_StartUp_Test_Countryside.xml` and `Includes_StartUp_Test_Tokyo.xml`:
  fixed seasonal mattes with live season selection.
- Original generation prompts and source scene builders: preserved source archive.

Inspect consumer files again when implementing; they can change independently.
