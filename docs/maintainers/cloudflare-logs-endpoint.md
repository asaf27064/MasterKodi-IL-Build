# Cloudflare logs Worker (DEPLOYED)

The power-menu "שלח לוגים לתמיכה" uploads the scrubbed log to a dedicated Worker,
which stores it in KV under the device id and returns a readable URL a maintainer
can open directly.

**Deployed 2026-07-14** on Asaf's account (`482ed527...`), isolated from the
subtitle-pool Worker:

- Worker: `https://masterkodi-logs.asaf27064.workers.dev`  (source: `cloudflare/logs-worker/`)
- KV namespace `LOGS` = `fabe565bcc1c450bbea3edaa9dbbcd33`
- Auth: `X-Gears-Key` = the shared build key (same as the pool)
- Retention: 45 days (KV `expirationTtl`)

## Endpoints
- `POST /v1/logs`  (headers `X-Gears-Key`, `X-Device-Id`, `X-Platform`; body = log)
  -> `{ "url": ".../v1/logs/<device>/<ts>", "device": "<id>" }`
- `GET /v1/logs/<device>/<ts>`  -> the stored log text (WebFetch-readable)
- `GET /v1/logs?device=<id>`    -> list all of a device's uploads
- `GET /v1/logs/recent?key=<KEY>&limit=N` -> newest uploads across ALL devices
  (key in query so it is WebFetch-readable). This is how I find a just-uploaded
  log with NOTHING from the user -- I fetch /recent, read the newest url.

## Re-deploy after editing the Worker
```
cd cloudflare/logs-worker
wrangler deploy
```
Requires wrangler logged into Asaf's Cloudflare account.

## NOTE
Cloudflare's edge 403s requests with an empty/bot User-Agent. The addon sends
`User-Agent: MasterKodiIL`, and WebFetch sends a normal UA, so both work.
