# Cloudflare `/v1/logs` endpoint (log upload to R2)

The power-menu "שלח לוגים לתמיכה" tries to POST the (scrubbed) log to
`https://masterkodi-subpool.asaf27064.workers.dev/v1/logs` with the shared
`X-Gears-Key`, then falls back to paste.kodi.tv. Until this endpoint exists on the
Worker, the Cloudflare upload silently no-ops and paste.kodi.tv is used.

**This must be deployed by Asaf** (needs the Cloudflare account / `wrangler`). I
can't deploy to your account.

## 1. Add an R2 bucket binding (`wrangler.toml`)
```toml
[[r2_buckets]]
binding = "LOGS"                 # -> env.LOGS in the Worker
bucket_name = "masterkodi-logs"  # create it: wrangler r2 bucket create masterkodi-logs
```

## 2. Add these routes to the Worker's `fetch` handler
```js
// --- MasterKodi log upload/read (add inside your existing fetch()) ---
const KEY = "mk-76ed711408c449eda0c5a2d868720b0438e36309"; // same X-Gears-Key as the pool

// POST /v1/logs  -> store, return {url}
if (request.method === "POST" && url.pathname === "/v1/logs") {
  if (request.headers.get("X-Gears-Key") !== KEY)
    return new Response("unauthorized", { status: 401 });
  const dev = (request.headers.get("X-Device-Id") || "unknown").replace(/[^A-Za-z0-9_-]/g, "");
  const ts  = new Date().toISOString().replace(/[:.]/g, "-");
  const key = `logs/${dev}/${ts}.log`;
  const body = await request.text();
  await env.LOGS.put(key, body, { httpMetadata: { contentType: "text/plain; charset=utf-8" } });
  const read = `${url.origin}/v1/logs/${dev}/${ts}`;
  return Response.json({ url: read, key });
}

// GET /v1/logs/<device>/<ts>  -> return the stored text (so a maintainer can read it)
if (request.method === "GET" && url.pathname.startsWith("/v1/logs/")) {
  const rest = url.pathname.slice("/v1/logs/".length);      // "<device>/<ts>"
  const obj = await env.LOGS.get(`logs/${rest}.log`);
  if (!obj) return new Response("not found", { status: 404 });
  return new Response(obj.body, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}

// GET /v1/logs?device=<id>  -> list a device's uploads (optional, handy for me)
if (request.method === "GET" && url.pathname === "/v1/logs") {
  const dev = (url.searchParams.get("device") || "").replace(/[^A-Za-z0-9_-]/g, "");
  if (!dev) return new Response("device required", { status: 400 });
  const list = await env.LOGS.list({ prefix: `logs/${dev}/` });
  return Response.json(list.objects.map(o => `${url.origin}/${o.key.replace(/\.log$/, "").replace(/^logs\//, "v1/logs/")}`));
}
```

## 3. Deploy
```
wrangler r2 bucket create masterkodi-logs   # once
wrangler deploy
```

## How I read it
- The addon shows the user a URL like
  `https://masterkodi-subpool.asaf27064.workers.dev/v1/logs/<device>/<ts>`.
- I `WebFetch` that URL and read the scrubbed log — the header block at the top
  tells me the device (id, platform, Kodi version, build, skin, time).
- `GET /v1/logs?device=<id>` lists all a device's uploads.

## Retention (optional)
Add an R2 lifecycle rule (Cloudflare dashboard → R2 → bucket → Settings) to auto-
delete objects older than e.g. 30 days, so logs don't accumulate.
