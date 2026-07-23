// MasterKodi IL - log upload Worker (KV-backed, isolated from the subtitle pool).
//
// WRITE is public by necessity (the upload key ships in every client, so it can
// never be secret) -- it's only a "you're one of our apps" gate, backed by a
// body-size cap and a best-effort per-IP rate limit.
//
// READ is maintainer-only. Logs/crash-signatures are private diagnostics, not a
// shared resource, so every read route requires ADMIN_KEY -- a secret set in the
// Cloudflare dashboard (`wrangler secret put ADMIN_KEY`) that NEVER ships in a
// client or the repo. If ADMIN_KEY is unset the read routes fail CLOSED.
//
//   POST /v1/logs                 -> store (X-Gears-Key = public upload key)
//   GET  /v1/logs/<device>/<ts>   -> read one entry            (ADMIN_KEY)
//   GET  /v1/logs?device=<id>     -> list a device's uploads   (ADMIN_KEY)
//   GET  /v1/logs/recent          -> newest across all devices (ADMIN_KEY)
const KEY = "mk-76ed711408c449eda0c5a2d868720b0438e36309";  // public upload gate
const TTL = 60 * 60 * 24 * 45;          // keep logs 45 days
const MAX_BODY = 2 * 1024 * 1024;       // 2 MB: scrubbed logs + tiny signatures
const RL_MAX = 60;                      // uploads per IP per hour
const RL_WINDOW = 3600;

// Maintainer read auth: header `X-Admin-Key` or `?key=`. Fails closed when the
// secret isn't configured, so a fresh deploy is never publicly readable.
function adminOk(request, url, env) {
  if (!env || !env.ADMIN_KEY) return false;
  const given = request.headers.get("X-Admin-Key") || url.searchParams.get("key") || "";
  return given === env.ADMIN_KEY;
}

// never let a log/listing response sit in an edge cache (it would be replayable
// via its URL). Applies to every read route.
const NOCACHE = { "Cache-Control": "no-store" };

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === "POST" && p === "/v1/logs") {
      if (request.headers.get("X-Gears-Key") !== KEY)
        return new Response("unauthorized", { status: 401 });
      // reject oversized uploads before reading the body
      const clen = parseInt(request.headers.get("Content-Length") || "0", 10);
      if (clen > MAX_BODY) return new Response("payload too large", { status: 413 });
      // best-effort per-IP rate limit (never blocks on KV error)
      const ip = request.headers.get("CF-Connecting-IP") || "0";
      try {
        const rlKey = `rl/${ip}/${Math.floor(Date.now() / 1000 / RL_WINDOW)}`;
        const n = parseInt((await env.LOGS.get(rlKey)) || "0", 10) + 1;
        if (n > RL_MAX) return new Response("rate limited", { status: 429 });
        await env.LOGS.put(rlKey, String(n), { expirationTtl: RL_WINDOW });
      } catch (e) { /* fail open on limiter errors */ }
      const body = await request.text();
      if (body.length > MAX_BODY) return new Response("payload too large", { status: 413 });
      const dev = (request.headers.get("X-Device-Id") || "unknown").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40) || "unknown";
      const plat = (request.headers.get("X-Platform") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 20);
      const ts = new Date().toISOString().replace(/[:.]/g, "-");
      const key = `${dev}/${ts}`;
      await env.LOGS.put(key, body, { expirationTtl: TTL, metadata: { platform: plat, len: body.length, at: Date.now() } });
      return Response.json({ url: `${url.origin}/v1/logs/${key}`, device: dev });
    }

    // ---- read routes: maintainer-only ----
    if (request.method === "GET" && p === "/v1/logs/recent") {
      if (!adminOk(request, url, env)) return new Response("unauthorized", { status: 401 });
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "15", 10) || 15, 100);
      const list = await env.LOGS.list({ limit: 1000 });
      const rows = list.keys
        .filter(k => !k.name.startsWith("rl/"))
        .map(k => ({ name: k.name, at: (k.metadata && k.metadata.at) || 0,
                     platform: (k.metadata && k.metadata.platform) || "", len: (k.metadata && k.metadata.len) || 0 }))
        .sort((a, b) => b.at - a.at)
        .slice(0, limit)
        .map(r => ({ url: `${url.origin}/v1/logs/${r.name}`,
                     device: r.name.split("/")[0], platform: r.platform, len: r.len,
                     uploaded: r.at ? new Date(r.at).toISOString() : "" }));
      return Response.json(rows, { headers: NOCACHE });
    }

    if (request.method === "GET" && p === "/v1/logs") {
      if (!adminOk(request, url, env)) return new Response("unauthorized", { status: 401 });
      const dev = (url.searchParams.get("device") || "").replace(/[^A-Za-z0-9_-]/g, "");
      if (!dev) return new Response("device required", { status: 400 });
      const list = await env.LOGS.list({ prefix: `${dev}/` });
      return Response.json(list.keys.map(k => `${url.origin}/v1/logs/${k.name}`), { headers: NOCACHE });
    }

    if (request.method === "GET" && p.startsWith("/v1/logs/")) {
      if (!adminOk(request, url, env)) return new Response("unauthorized", { status: 401 });
      const key = p.slice("/v1/logs/".length);
      if (!key || key.startsWith("rl/")) return new Response("not found", { status: 404 });
      const val = await env.LOGS.get(key);
      if (val === null) return new Response("not found", { status: 404 });
      return new Response(val, { headers: { "Content-Type": "text/plain; charset=utf-8", ...NOCACHE } });
    }

    return new Response("MasterKodi logs worker", { status: 200 });
  },
};
