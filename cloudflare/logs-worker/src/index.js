// MasterKodi IL - log upload Worker (KV-backed, isolated from the subtitle pool).
// POST /v1/logs                     -> store scrubbed log, return {url}
// GET  /v1/logs/<device>/<ts>       -> return the stored log text
// GET  /v1/logs?device=<id>         -> list a device's uploads
const KEY = "mk-76ed711408c449eda0c5a2d868720b0438e36309";
const TTL = 60 * 60 * 24 * 45;   // keep logs 45 days

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const p = url.pathname;

    if (request.method === "POST" && p === "/v1/logs") {
      if (request.headers.get("X-Gears-Key") !== KEY)
        return new Response("unauthorized", { status: 401 });
      const dev = (request.headers.get("X-Device-Id") || "unknown").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 40) || "unknown";
      const plat = (request.headers.get("X-Platform") || "").replace(/[^A-Za-z0-9_-]/g, "").slice(0, 20);
      const ts = new Date().toISOString().replace(/[:.]/g, "-");
      const key = `${dev}/${ts}`;
      const body = await request.text();
      await env.LOGS.put(key, body, { expirationTtl: TTL, metadata: { platform: plat, len: body.length, at: Date.now() } });
      return Response.json({ url: `${url.origin}/v1/logs/${key}`, device: dev });
    }

    // GET /v1/logs/recent?key=<KEY>&limit=N -> newest uploads across all devices
    // (key in the query so a maintainer can read it via a plain browser/fetch).
    if (request.method === "GET" && p === "/v1/logs/recent") {
      if (url.searchParams.get("key") !== KEY)
        return new Response("unauthorized", { status: 401 });
      const limit = Math.min(parseInt(url.searchParams.get("limit") || "15", 10) || 15, 100);
      const list = await env.LOGS.list({ limit: 1000 });
      const rows = list.keys
        .map(k => ({ name: k.name, at: (k.metadata && k.metadata.at) || 0,
                     platform: (k.metadata && k.metadata.platform) || "", len: (k.metadata && k.metadata.len) || 0 }))
        .sort((a, b) => b.at - a.at)
        .slice(0, limit)
        .map(r => ({ url: `${url.origin}/v1/logs/${r.name}`,
                     device: r.name.split("/")[0], platform: r.platform, len: r.len,
                     uploaded: r.at ? new Date(r.at).toISOString() : "" }));
      return Response.json(rows);
    }

    if (request.method === "GET" && p.startsWith("/v1/logs/")) {
      const key = p.slice("/v1/logs/".length);
      const val = await env.LOGS.get(key);
      if (val === null) return new Response("not found", { status: 404 });
      return new Response(val, { headers: { "Content-Type": "text/plain; charset=utf-8" } });
    }

    if (request.method === "GET" && p === "/v1/logs") {
      const dev = (url.searchParams.get("device") || "").replace(/[^A-Za-z0-9_-]/g, "");
      if (!dev) return new Response("device required", { status: 400 });
      const list = await env.LOGS.list({ prefix: `${dev}/` });
      return Response.json(list.keys.map(k => `${url.origin}/v1/logs/${k.name}`));
    }

    return new Response("MasterKodi logs worker", { status: 200 });
  },
};
