# Instructions for AI agents

Read [AUTHORING.md](AUTHORING.md) before creating or changing artwork, layers,
effects, manifests or authoring tools. It defines the visual approach, layer
contract, workflow and review criteria for this resource family.

- Treat this add-on as a reference when building another resource. Create a new
  resource in the user-authorized destination; do not overwrite this one unless
  the user requests changes here.
- Distinguish a compatible replacement for the existing skin from a new scene
  system. Preserve the existing runtime contract for a compatible replacement.
  Document proposed contract changes for a new system before implementation.
- Inspect existing artwork and consumer XML before choosing an implementation.
  The manifest describes static composites; it does not fully specify the skin's
  animated rendering or current weather mappings.
- Keep runtime resources declarative. Authoring tools run offline; do not add
  runtime Python or require skin-owned scripts to display the resource.
- Preserve source masters, record provenance and prompts, and derive delivery
  files reproducibly. Never repeatedly recompress delivery artwork.
- Validate outside Kodi first. Do not launch or control any Kodi instance for
  testing without explicit typed approval in the chat describing those tests.
- Keep verification scripts and generated review sheets outside the distributed
  add-on. Do not add unit tests or a tests folder to the skin.
- Report changed files, validation results and limitations. Do not claim Kodi
  testing or visual approval when only static checks were performed.

Documentation-only work must not alter artwork, runtime selectors or metadata
versions unless separately requested.
