# masterkodi-subpool (Cloudflare Worker)

The community subtitle pool behind `service.subtitles.gearsai` (resources/aisubs/pool.py).
Worker name: `masterkodi-subpool`. Deployed via wrangler from this directory.

`wrangler.toml` is deliberately gitignored (repo-wide rule). To redeploy from a
fresh machine, recreate it with:
- `name = "masterkodi-subpool"`, `main = "worker.js"`
- one `[[d1_databases]]` binding named `DB` -> the `masterkodi-subpool` D1 database
  (id from the Cloudflare dashboard)

Secrets (set with `wrangler secret put`, NEVER in files):
- `POOL_TOKEN`  - client token the addon sends (X-Gears-Key)
- `ADMIN_TOKEN` - maintainer token for /v1/admin/* (X-Admin-Key)
- `GEMINI_KEYS` - comma-separated Gemini keys for /v1/translate

Admin tooling: `tools/pool_admin.py` (reads `pool_admin_token.txt` next to
itself -- gitignored; keep the token in the private backup).
