# MasterKodi IL — maintainer docs (git-backed snapshot)

This folder is a **version-controlled backup** of the maintainer knowledge that
otherwise lives only on the maintainer's machine under `~/.claude/`. It exists so
the whole ecosystem can be reconstructed after a disk loss.

Nothing here contains secrets (keystore passwords, Gemini/API keys, and tokens
are GitHub Actions secrets and are never committed). It is safe in a public repo.

## Contents

| Path | What it is |
|---|---|
| `add-skin.md` | How to add an optional third-party skin (Zephyr worked example). |
| `skill/SKILL.md` | The `masterkodi-il-builder` agent skill entry point. |
| `skill/references/*.md` | Deep, file-by-file references (hebrew-mod, base-build, arctic-fuse, wizard, wizard-build, ai-subs, release-packaging, migration, gotchas). |
| `skill/scripts/*.py` | Maintenance scripts (rebuild hebrew zip, build release zips, publish AI subs, scan residual fenlight). |
| `memory/*.md` | Persistent facts about the build, workflow, and references. |

## Keeping it in sync

This is a **snapshot**, not the live source. The live copies are:
- Skill: `~/.claude/skills/masterkodi-il-builder/`
- Memory: `~/.claude/projects/<project-hash>/memory/`

Re-sync before a release (copy the live folders over this one and commit) so the
git backup doesn't drift. `migration.md` (re-homing the Hebrew mod onto a new
base addon) lives under `skill/references/`.
