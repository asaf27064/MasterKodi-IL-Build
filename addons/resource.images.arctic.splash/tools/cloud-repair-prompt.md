# Clear and hazy cloud repair

Created with the built-in ImageGen tool on 2026-10-21, editing the original
`resources/layers/03-clouds/clear/0800.webp` from revision
`7b4dda18c2cadac4ecfb66182ced22c5ec303904` with transparent output.

The installed daytime plate is the compressed master. Rebuild the hourly clear
and hazy variants with `python tools/rebuild_clear_clouds.py <master.png>`.
The script fits the existing hourly palettes from the recorded original Git
revision and retains hazy's 40% opacity. The original generated PNG is an
authoring intermediate; only the 16 replacement WebP plates are shipped.

## Prompt

Use case: precise-object-edit
Asset type: transparent scrolling cloud layer for an illustrated landscape, wide 1920 x 1080 canvas.
Edit target: the attached daytime cloud plate. It currently has a triangular mountain-shaped hole around the middle-right, many tree-shaped holes and tiny blue/yellow extraction fringes. These are masking defects, not intentional cloud shapes.
Primary request: repair this into a clean, continuous illustrated cloud bank suitable for horizontal movement behind separately drawn mountains. Fill the central triangular missing area with matching white and pale blue clouds, naturally joining the cloud masses on either side. Remove all foliage-shaped holes, blue sky debris, yellow/red fringes and dark purple blemishes. Reconstruct clean, naturally feathered cloud edges.
Preserve: the hand-painted anime landscape style, predominantly white and pale cool blue clouds, airy sparse wisps in the upper-right, the low horizontal cloud bank roughly between 25% and 49% of the canvas height. Keep the rest of the upper sky and lower half genuinely transparent. Keep similar cloud density and scale and the original wide composition; do not add an opaque sky.
Constraints: clouds only on real transparency; no mountains, terrain, trees, sun, moon, lake, text, watermark, checkerboard or colored background. The lower cloud bank must flow continuously across the FULL image width with no terrain silhouette cutout. Natural soft cloud outline at bottom, not a straight cropped edge. No sharp vertical cutoffs or abrupt changes at the left/right boundaries; the two boundaries should have similar cloud density and height for a mirrored scrolling tile. Output a single transparent 1920x1080 cloud layer.
