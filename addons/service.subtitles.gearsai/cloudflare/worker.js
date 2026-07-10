var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// worker.js
var MAX_SRT_BYTES = 2 * 1024 * 1024;
var MIN_ENTRIES = 5;
var HIDE_FLAGS = 3;
var ENTRY_TOLERANCE = 0.25;
var worker_default = {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (request.method === "GET" && path === "/v1/health") return health(env);
      if (request.method === "GET" && path === "/v1/lookup") return lookup(url, env);
      if (request.method === "GET" && path === "/v1/fetch") return fetchSub(url, env);
      if (request.method === "GET" && (path === "/stats" || path === "/")) return statsPage(env);
      if (request.method === "GET" && path === "/v1/stats") return statsJson(env);
      if (request.method === "GET" && path === "/v1/admin/flagged") {
        if (!adminAuthed(request, env)) return json({ error: "unauthorized" }, 401);
        return adminFlagged(env);
      }
      if (request.method === "GET" && path === "/v1/admin/list") {
        if (!adminAuthed(request, env)) return json({ error: "unauthorized" }, 401);
        return adminList(env);
      }
      if (request.method === "GET" && path === "/admin") {
        return adminPage();
      }
      if (request.method === "GET" && path === "/v1/taglines") {
        return taglinesGet(url, env);
      }
      if (request.method === "POST") {
        if (path === "/v1/admin/delete") {
          if (!adminAuthed(request, env)) return json({ error: "unauthorized" }, 401);
          return adminDelete(request, env);
        }
        if (path === "/v1/admin/wipe") {
          if (!adminAuthed(request, env)) return json({ error: "unauthorized" }, 401);
          return adminWipe(request, env);
        }
        if (path === "/v1/admin/restore") {
          if (!adminAuthed(request, env)) return json({ error: "unauthorized" }, 401);
          return adminRestore(request, env);
        }
        if (!authed(request, env)) return json({ error: "unauthorized" }, 401);
        if (path === "/v1/taglines") return taglinesAdd(request, env);
        if (path === "/v1/translate") return translateProxy(request, env);
        if (path === "/v1/contribute") return contribute(request, env);
        if (path === "/v1/vote") return vote(request, env);
        if (path === "/v1/flag") return flag(request, env);
        if (path === "/v1/telemetry/fail") return telemetryFail(request, env);
      }
      return json({ error: "not found" }, 404);
    } catch (e) {
      return json({ error: String(e && e.message || e) }, 500);
    }
  }
};
function authed(request, env) {
  const want = (env.POOL_TOKEN || "").trim();
  if (!want) return true;
  const got = (request.headers.get("X-Gears-Key") || "").trim();
  return got === want;
}
__name(authed, "authed");
function adminAuthed(request, env) {
  const want = (env.ADMIN_TOKEN || "").trim();
  if (!want) return false;
  const got = (request.headers.get("X-Admin-Key") || "").trim();
  return got === want;
}
__name(adminAuthed, "adminAuthed");
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
}
__name(esc, "esc");
function json(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      // Allow the static build status page (different origin) to read /v1/stats.
      "Access-Control-Allow-Origin": "*"
    }
  });
}
__name(json, "json");
async function health(env) {
  const row = await env.DB.prepare("SELECT COUNT(*) AS n FROM subs").first();
  return json({ ok: true, count: row ? row.n : 0 });
}
__name(health, "health");
async function lookup(url, env) {
  const key = (url.searchParams.get("key") || "").trim();
  const lang = (url.searchParams.get("lang") || "he").trim();
  if (!key) return json({ error: "missing key" }, 400);
  const rs = await env.DB.prepare(
    `SELECT id, release, model, votes, entry_count, downloads, has_anchor
       FROM subs
      WHERE media_key = ? AND lang = ?
        AND NOT (flags >= ? AND votes <= flags)
      ORDER BY votes DESC, downloads DESC
      LIMIT 25`
  ).bind(key, lang, HIDE_FLAGS).all();
  return json({ subs: rs && rs.results || [] });
}
__name(lookup, "lookup");
async function fetchSub(url, env) {
  const id = (url.searchParams.get("id") || "").trim();
  if (!id) return json({ error: "missing id" }, 400);
  const part = (url.searchParams.get("part") || "he").trim();
  const col = part === "en" ? "eng" : "srt";
  const row = await env.DB.prepare(`SELECT ${col} AS body FROM blobs WHERE id = ?`).bind(id).first();
  if (!row || row.body == null) return json({ error: "not found" }, 404);
  if (part !== "en") {
    env.DB.prepare("UPDATE subs SET downloads = downloads + 1 WHERE id = ?").bind(id).run();
  }
  return new Response(row.body, {
    headers: { "Content-Type": "application/x-subrip; charset=utf-8" }
  });
}
__name(fetchSub, "fetchSub");
var GEMINI_SAFETY = [
  "HARM_CATEGORY_HARASSMENT",
  "HARM_CATEGORY_HATE_SPEECH",
  "HARM_CATEGORY_SEXUALLY_EXPLICIT",
  "HARM_CATEGORY_DANGEROUS_CONTENT"
].map((category) => ({ category, threshold: "BLOCK_NONE" }));
async function translateProxy(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || !b.prompt || !b.model) return json({ error: "bad payload" }, 400);
  const keys = (env.GEMINI_KEYS || "").split(",").map((s) => s.trim()).filter(Boolean);
  if (!keys.length) return json({ error: "proxy has no keys", kind: "config" }, 503);
  const model = String(b.model);
  const cfg = { temperature: typeof b.temperature === "number" ? b.temperature : 0.2, topP: 0.95 };
  if (typeof b.thinking_budget === "number" && (model.includes("2.5") || model.startsWith("gemini-3")))
    cfg.thinkingConfig = { thinkingBudget: b.thinking_budget };
  if (b.response_json) cfg.responseMimeType = "application/json";
  const payload = {
    contents: [{ role: "user", parts: [{ text: String(b.prompt) }] }],
    generationConfig: cfg,
    safetySettings: GEMINI_SAFETY
  };
  let last = "quota";
  for (const key of keys) {
    const u = "https://generativelanguage.googleapis.com/v1beta/models/" + encodeURIComponent(model) + ":generateContent?key=" + encodeURIComponent(key);
    let r;
    try {
      r = await fetch(u, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
    } catch (e) {
      last = "network";
      continue;
    }
    if (r.status === 429) {
      const t = await r.text();
      // Per-minute limits are PER KEY -- try the next key instead of giving up
      // (it used to return immediately, wasting the other keys' RPM budgets).
      if (/per.?minute|RPM|rate/i.test(t)) {
        last = "rate";
        continue;
      }
      last = "quota";
      continue;
    }
    if (r.status === 400 || r.status === 401 || r.status === 403) {
      last = "invalid";
      continue;
    }
    if (r.status >= 500) {
      last = "overload";
      continue;
    }
    if (r.status !== 200) {
      last = "status " + r.status;
      continue;
    }
    const data = await r.json().catch(() => null);
    const parts = data && data.candidates && data.candidates[0] && data.candidates[0].content && data.candidates[0].content.parts || [];
    const text = parts.map((p) => p && p.text || "").join("").trim();
    if (text) return json({ text });
    last = "empty";
  }
  return json({ error: last, kind: last === "quota" ? "quota" : (last === "rate" ? "rate" : "error") }, 429);
}
__name(translateProxy, "translateProxy");
async function contribute(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || !b.key || !b.srt) return json({ error: "bad payload" }, 400);
  const srt = String(b.srt);
  if (srt.length > MAX_SRT_BYTES) return json({ error: "too large" }, 413);
  const cueCount = (srt.match(/-->/g) || []).length;
  if (cueCount < MIN_ENTRIES) return json({ error: "too few cues" }, 422);
  const claimed = parseInt(b.entry_count, 10) || cueCount;
  if (Math.abs(claimed - cueCount) > Math.max(5, cueCount * ENTRY_TOLERANCE)) {
    return json({ error: "entry_count mismatch" }, 422);
  }
  const lang = (b.lang || "he").trim();
  if (lang === "he") {
    const hebChars = (srt.match(/[֐-׿]/g) || []).length;
    const letters = (srt.match(/[A-Za-z֐-׿]/g) || []).length || 1;
    if (hebChars < 50 || hebChars / letters < 0.4) {
      return json({ error: "does not look Hebrew" }, 422);
    }
  }
  const id = await sha256hex(srt);
  const existing = await env.DB.prepare("SELECT id FROM subs WHERE id = ?").bind(id).first();
  if (existing) {
    return json({ ok: true, id, deduped: true });
  }
  const contributor = await sha256hex(
    (request.headers.get("CF-Connecting-IP") || "anon") + "|" + (env.POOL_TOKEN || "")
  );
  let eng = b.eng != null ? String(b.eng) : null;
  if (eng && eng.length > MAX_SRT_BYTES) eng = null;
  const hasAnchor = eng ? 1 : 0;
  await env.DB.batch([
    env.DB.prepare("INSERT INTO blobs (id, srt, eng) VALUES (?, ?, ?)").bind(id, srt, eng),
    env.DB.prepare(
      `INSERT INTO subs
         (id, media_key, imdb, tmdb, title, year, season, episode,
          release, model, lang, entry_count, votes, flags,
          downloads, has_anchor, created, contributor)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,?,?)`
    ).bind(
      id,
      b.key,
      b.imdb || "",
      b.tmdb || "",
      b.title || "",
      String(b.year || ""),
      parseInt(b.season, 10) || 0,
      parseInt(b.episode, 10) || 0,
      b.release || "",
      b.model || "",
      lang,
      cueCount,
      hasAnchor,
      Math.floor(Date.now() / 1e3),
      contributor.slice(0, 16)
    )
  ]);
  return json({ ok: true, id, deduped: false, anchored: !!eng });
}
__name(contribute, "contribute");
async function vote(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || !b.id) return json({ error: "bad payload" }, 400);
  const dir = b.dir >= 0 ? 1 : -1;
  await env.DB.prepare("UPDATE subs SET votes = votes + ? WHERE id = ?").bind(dir, b.id).run();
  return json({ ok: true });
}
__name(vote, "vote");
async function flag(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || !b.id) return json({ error: "bad payload" }, 400);
  await env.DB.prepare("UPDATE subs SET flags = flags + 1 WHERE id = ?").bind(b.id).run();
  return json({ ok: true });
}
__name(flag, "flag");
async function telemetryFail(request, env) {
  const b = await request.json().catch(() => null);
  if (!b) return json({ error: "bad payload" }, 400);
  try {
    await env.DB.prepare(
      `INSERT INTO failures (media_key, imdb, title, year, release, model, reason, created)
       VALUES (?,?,?,?,?,?,?,?)`
    ).bind(
      b.key || "",
      b.imdb || "",
      b.title || "",
      String(b.year || ""),
      b.release || "",
      b.model || "",
      String(b.reason || "unknown").slice(0, 40),
      Math.floor(Date.now() / 1e3)
    ).run();
  } catch (e) {
    return json({ ok: true, stored: false });
  }
  return json({ ok: true, stored: true });
}
__name(telemetryFail, "telemetryFail");
async function adminFlagged(env) {
  const rs = await env.DB.prepare(
    `SELECT id, title, year, release, model, votes, flags, downloads, created
       FROM subs WHERE flags > 0
      ORDER BY flags DESC, votes ASC LIMIT 100`
  ).all();
  return json({ flagged: rs && rs.results || [] });
}
__name(adminFlagged, "adminFlagged");
async function ensureTaglines(env) {
  await env.DB.prepare(
    "CREATE TABLE IF NOT EXISTS taglines (tagline TEXT PRIMARY KEY, media_type TEXT, added INTEGER)"
  ).run();
}
__name(ensureTaglines, "ensureTaglines");
async function taglinesGet(url, env) {
  // Community-maintained embedded-Hebrew release list (replaces the dead
  // darksubshebsubs.github.io files). Plain text, one release name per line --
  // same format Gears' kodirdil parser already expects.
  const t = (url.searchParams.get("type") || "movie").toLowerCase();
  const like = t.startsWith("tv") ? "tv%" : "movie%";
  await ensureTaglines(env);
  const rs = await env.DB.prepare(
    "SELECT tagline FROM taglines WHERE media_type LIKE ? ORDER BY tagline LIMIT 20000"
  ).bind(like).all();
  const body = (rs && rs.results || []).map((r) => r.tagline).join("\n");
  return new Response(body, {
    headers: { "Content-Type": "text/plain; charset=utf-8", "Access-Control-Allow-Origin": "*" }
  });
}
__name(taglinesGet, "taglinesGet");
async function taglinesAdd(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || !b.tagline) return json({ error: "bad payload" }, 400);
  const tag = String(b.tagline).trim().toLowerCase().slice(0, 300);
  if (tag.length < 8) return json({ error: "too short" }, 422);
  const mt = String(b.media_type || "movie").toLowerCase().startsWith("tv") ? "tvshow" : "movie";
  await ensureTaglines(env);
  await env.DB.prepare(
    "INSERT OR IGNORE INTO taglines (tagline, media_type, added) VALUES (?,?,?)"
  ).bind(tag, mt, Math.floor(Date.now() / 1e3)).run();
  return json({ ok: true });
}
__name(taglinesAdd, "taglinesAdd");
async function adminList(env) {
  const rs = await env.DB.prepare(
    `SELECT id, media_key, imdb, tmdb, lang, title, year, season, episode,
            release, model, entry_count, votes, flags, downloads, has_anchor,
            created, contributor
       FROM subs ORDER BY created DESC LIMIT 2000`
  ).all();
  return json({ subs: rs && rs.results || [] });
}
__name(adminList, "adminList");
async function adminRestore(request, env) {
  const b = await request.json().catch(() => null);
  const s = b && b.sub;
  if (!s || !s.id || !b.srt) return json({ error: "bad payload" }, 400);
  const srt = String(b.srt);
  if (srt.length > MAX_SRT_BYTES) return json({ error: "too large" }, 413);
  const eng = b.eng != null && String(b.eng).length <= MAX_SRT_BYTES ? String(b.eng) : null;
  await env.DB.batch([
    env.DB.prepare("INSERT OR REPLACE INTO blobs (id, srt, eng) VALUES (?, ?, ?)")
      .bind(s.id, srt, eng),
    env.DB.prepare(
      `INSERT OR REPLACE INTO subs
         (id, media_key, imdb, tmdb, title, year, season, episode,
          release, model, lang, entry_count, votes, flags,
          downloads, has_anchor, created, contributor)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`
    ).bind(
      s.id, s.media_key || "", s.imdb || "", s.tmdb || "", s.title || "",
      String(s.year || ""), parseInt(s.season, 10) || 0, parseInt(s.episode, 10) || 0,
      s.release || "", s.model || "", s.lang || "he",
      parseInt(s.entry_count, 10) || 0, parseInt(s.votes, 10) || 0,
      parseInt(s.flags, 10) || 0, parseInt(s.downloads, 10) || 0,
      eng ? 1 : 0, parseInt(s.created, 10) || Math.floor(Date.now() / 1e3),
      String(s.contributor || "restore").slice(0, 16)
    )
  ]);
  return json({ ok: true, id: s.id });
}
__name(adminRestore, "adminRestore");
async function adminWipe(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || b.confirm !== "WIPE") return json({ error: 'confirm with {"confirm":"WIPE"}' }, 400);
  const before = await env.DB.prepare("SELECT COUNT(*) AS n FROM subs").first();
  await env.DB.batch([
    env.DB.prepare("DELETE FROM blobs"),
    env.DB.prepare("DELETE FROM subs")
  ]);
  return json({ ok: true, wiped: before ? before.n : 0 });
}
__name(adminWipe, "adminWipe");
function adminPage() {
  const html = `<!doctype html><html dir="rtl" lang="he"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>MasterKodi Pool · ניהול</title>
<style>
body{background:#12141c;color:#dde6ef;font-family:Segoe UI,Arial,sans-serif;margin:0;padding:24px}
h1{color:#00d8ff;font-size:22px;margin:0 0 4px}
.sub{color:#8a97a8;font-size:13px;margin-bottom:18px}
.bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
button{background:#0e2434;color:#dde6ef;border:1px solid #1e3a50;border-radius:6px;padding:8px 14px;cursor:pointer;font-size:13px}
button:hover{background:#164058}
button.danger{background:#3a1420;border-color:#5c2030}
button.danger:hover{background:#571d2e}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:#8a97a8;text-align:right;padding:8px;border-bottom:2px solid #00aeef}
td{padding:8px;border-bottom:1px solid #232838;vertical-align:middle}
tr:hover td{background:#181c28}
.rel{color:#8a97a8;direction:ltr;text-align:left;font-family:Consolas,monospace;font-size:12px}
.mini{padding:4px 10px;font-size:12px;margin-inline-start:4px}
#status{margin:12px 0;color:#ffd24d;min-height:18px;font-size:13px}
.ok{color:#7dff9e}.err{color:#ff7d7d}
input[type=file]{display:none}
</style></head><body>
<h1>MasterKodi Pool · ניהול</h1>
<div class="sub">מאגר התרגומים הקהילתי · הפעולות דורשות טוקן אדמין (נשמר בדפדפן בלבד)</div>
<div class="bar">
<button onclick="load()">רענן</button>
<button onclick="exportAll()">ייצוא הכול (גיבוי)</button>
<button onclick="document.getElementById('file').click()">שחזור מגיבוי</button>
<button class="danger" onclick="wipeAll()">מחק הכול</button>
<button onclick="setTok(true)">החלף טוקן</button>
<input type="file" id="file" accept=".json,.zip" onchange="restoreFile(this)">
</div>
<div id="status"></div>
<div id="tbl"></div>
<script>
function tok(force){var t=localStorage.getItem('mk_admin');if(!t||force){t=prompt('הדבק את טוקן האדמין:')||'';if(t)localStorage.setItem('mk_admin',t.trim());}return (t||'').trim();}
function setTok(f){tok(f);load();}
function st(m,c){var e=document.getElementById('status');e.textContent=m;e.className=c||'';}
async function api(p,opts){opts=opts||{};opts.headers=Object.assign({'X-Admin-Key':tok()},opts.headers||{});var r=await fetch(p,opts);if(r.status===401){localStorage.removeItem('mk_admin');throw new Error('טוקן שגוי');}if(!r.ok)throw new Error('HTTP '+r.status);return r;}
var SUBS=[];
function fdate(t){try{return new Date(t*1000).toLocaleDateString('he-IL');}catch(e){return ''}}
function se(s){var x='';if(s.season){x='S'+String(s.season).padStart(2,'0');if(s.episode)x+='E'+String(s.episode).padStart(2,'0');}return x;}
async function load(){st('טוען...');try{var d=await(await api('/v1/admin/list')).json();SUBS=d.subs||[];var h='<table><tr><th>כותר</th><th>פרק</th><th>Release</th><th>מודל</th><th>שורות</th><th>הורדות</th><th>תאריך</th><th>פעולות</th></tr>';SUBS.forEach(function(s,i){h+='<tr><td><b>'+esc(s.title)+'</b> '+esc(s.year||'')+'</td><td>'+se(s)+'</td><td class="rel">'+esc((s.release||'').trim())+'</td><td>'+esc((s.model||'').replace('gemini-',''))+'</td><td>'+(s.entry_count||0)+'</td><td>'+(s.downloads||0)+'</td><td>'+fdate(s.created)+'</td><td><button class="mini" onclick="dl('+i+',\\'he\\')">עברית</button><button class="mini" onclick="dl('+i+',\\'en\\')">אנגלית</button><button class="mini danger" onclick="del('+i+')">מחק</button></td></tr>';});h+='</table>';document.getElementById('tbl').innerHTML=h;st(SUBS.length+' תרגומים במאגר','ok');}catch(e){st('שגיאה: '+e.message,'err');}}
function esc(s){return String(s==null?'':s).replace(/[&<>"]/g,function(c){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c];});}
function saveFile(name,text){var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type:'text/plain;charset=utf-8'}));a.download=name;a.click();}
function saveBlob(name,u8,mime){var a=document.createElement('a');a.href=URL.createObjectURL(new Blob([u8],{type:mime||'application/zip'}));a.download=name;a.click();}
function crc32(u8){var c,t=[],i,j;for(i=0;i<256;i++){c=i;for(j=0;j<8;j++)c=(c&1)?(3988292384^(c>>>1)):(c>>>1);t[i]=c;}c=4294967295;for(i=0;i<u8.length;i++)c=t[(c^u8[i])&255]^(c>>>8);return (c^4294967295)>>>0;}
function zipStore(files){var enc=new TextEncoder(),parts=[],cd=[],off=0;function n16(v){return new Uint8Array([v&255,(v>>8)&255]);}function n32(v){return new Uint8Array([v&255,(v>>8)&255,(v>>16)&255,(v>>>24)&255]);}files.forEach(function(f){var name=enc.encode(f.name),data=(typeof f.data==='string')?enc.encode(f.data):f.data;var crc=crc32(data);[n32(0x04034b50),n16(20),n16(0x0800),n16(0),n16(0),n16(0),n32(crc),n32(data.length),n32(data.length),n16(name.length),n16(0)].forEach(function(p){parts.push(p);});parts.push(name);parts.push(data);cd.push({name:name,crc:crc,size:data.length,off:off});off+=30+name.length+data.length;});var cdStart=off,cdLen=0;cd.forEach(function(e){[n32(0x02014b50),n16(20),n16(20),n16(0x0800),n16(0),n16(0),n16(0),n32(e.crc),n32(e.size),n32(e.size),n16(e.name.length),n16(0),n16(0),n16(0),n16(0),n32(0),n32(e.off)].forEach(function(p){parts.push(p);});parts.push(e.name);cdLen+=46+e.name.length;});[n32(0x06054b50),n16(0),n16(0),n16(cd.length),n16(cd.length),n32(cdLen),n32(cdStart),n16(0)].forEach(function(p){parts.push(p);});var total=0;parts.forEach(function(p){total+=p.length;});var out=new Uint8Array(total),pos=0;parts.forEach(function(p){out.set(p,pos);pos+=p.length;});return out;}
function safeName(s){return String(s||'').replace(/[\\\\/:*?"<>|]/g,'').trim()||'sub';}
async function dl(i,part){var s=SUBS[i];st('מוריד...');try{var r=await fetch('/v1/fetch?id='+s.id+'&part='+part);var t=await r.text();if(!r.ok||!t.trim()){st('אין קובץ כזה','err');return;}saveFile((s.title||'sub')+'.'+((s.release||'').trim().slice(0,50)||s.id.slice(0,8))+'.'+(part==='he'?'he':'eng_anchor')+'.srt',t);st('ירד','ok');}catch(e){st('שגיאה: '+e.message,'err');}}
async function del(i){var s=SUBS[i];if(!confirm('למחוק את "'+(s.title||'')+'" ('+(s.release||'').trim().slice(0,40)+')?'))return;st('מוחק...');try{await api('/v1/admin/delete',{method:'POST',body:JSON.stringify({id:s.id})});st('נמחק','ok');load();}catch(e){st('שגיאה: '+e.message,'err');}}
async function exportAll(){st('מייצא... (עלול לקחת רגע)');try{var d=await(await api('/v1/admin/list')).json();var subs=d.subs||[];var out=[];var files=[];for(var i=0;i<subs.length;i++){var s=subs[i];st('מייצא '+(i+1)+'/'+subs.length+'...');var he=await(await fetch('/v1/fetch?id='+s.id+'&part=he')).text();var en='';try{var r2=await fetch('/v1/fetch?id='+s.id+'&part=en');if(r2.ok)en=await r2.text();}catch(e){}out.push({sub:s,srt:he,eng:en||null});var base=safeName(s.title)+'.'+ (safeName((s.release||'').trim()).slice(0,60)||s.id.slice(0,8))+'.'+s.id.slice(0,8);files.push({name:'subtitles/'+base+'.he.srt',data:he});if(en)files.push({name:'anchors/'+base+'.eng.srt',data:en});}files.push({name:'bundle.json',data:JSON.stringify({exported:Date.now(),subs:out},null,1)});saveBlob('pool_backup_'+new Date().toISOString().slice(0,10)+'.zip',zipStore(files));st('גיבוי ירד: '+out.length+' תרגומים (ZIP עם קובצי SRT + bundle לשחזור)','ok');}catch(e){st('שגיאה: '+e.message,'err');}}
function unzipFind(u8,want){var dv=new DataView(u8.buffer,u8.byteOffset,u8.byteLength),pos=0,dec=new TextDecoder();while(pos+30<=u8.length){if(dv.getUint32(pos,true)!==0x04034b50)break;var method=dv.getUint16(pos+8,true),size=dv.getUint32(pos+18,true),nlen=dv.getUint16(pos+26,true),elen=dv.getUint16(pos+28,true);var name=dec.decode(u8.subarray(pos+30,pos+30+nlen));var data=u8.subarray(pos+30+nlen+elen,pos+30+nlen+elen+size);if(name===want&&method===0)return dec.decode(data);pos+=30+nlen+elen+size;}return null;}
async function restoreFile(inp){var f=inp.files[0];inp.value='';if(!f)return;st('קורא קובץ...');try{var txt;if(/\\.zip$/i.test(f.name)){txt=unzipFind(new Uint8Array(await f.arrayBuffer()),'bundle.json');if(!txt){st('לא נמצא bundle.json בתוך ה-ZIP','err');return;}}else{txt=await f.text();}var d=JSON.parse(txt);var subs=d.subs||[];var n=0;for(var i=0;i<subs.length;i++){st('משחזר '+(i+1)+'/'+subs.length+'...');var b=subs[i];if(!b.sub||!b.srt)continue;await api('/v1/admin/restore',{method:'POST',body:JSON.stringify(b)});n++;}st('שוחזרו '+n+' תרגומים','ok');load();}catch(e){st('שגיאה: '+e.message,'err');}}
async function wipeAll(){var w=prompt('זה ימחק את כל המאגר לצמיתות. הקלד WIPE לאישור:');if(w!=='WIPE')return;st('מוחק הכול...');try{var d=await(await api('/v1/admin/wipe',{method:'POST',body:JSON.stringify({confirm:'WIPE'})})).json();st('נמחקו '+(d.wiped||0)+' תרגומים','ok');load();}catch(e){st('שגיאה: '+e.message,'err');}}
load();
</script></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}
__name(adminPage, "adminPage");
async function adminDelete(request, env) {
  const b = await request.json().catch(() => null);
  if (!b || !b.id) return json({ error: "bad payload" }, 400);
  await env.DB.batch([
    env.DB.prepare("DELETE FROM blobs WHERE id = ?").bind(b.id),
    env.DB.prepare("DELETE FROM subs WHERE id = ?").bind(b.id)
  ]);
  return json({ ok: true, deleted: b.id });
}
__name(adminDelete, "adminDelete");
async function gatherStats(env) {
  const totals = await env.DB.prepare(
    `SELECT COUNT(*) AS subs, COALESCE(SUM(downloads),0) AS downloads,
            COALESCE(SUM(has_anchor),0) AS anchors,
            COUNT(DISTINCT media_key) AS titles
       FROM subs`
  ).first();
  const byLang = await env.DB.prepare(
    "SELECT lang, COUNT(*) AS n FROM subs GROUP BY lang ORDER BY n DESC"
  ).all();
  const byModel = await env.DB.prepare(
    "SELECT model, COUNT(*) AS n FROM subs GROUP BY model ORDER BY n DESC LIMIT 8"
  ).all();
  const top = await env.DB.prepare(
    `SELECT title, year, COUNT(*) AS variants, COALESCE(SUM(downloads),0) AS dl
       FROM subs GROUP BY media_key ORDER BY dl DESC, variants DESC LIMIT 15`
  ).all();
  const recent = await env.DB.prepare(
    `SELECT title, year, release, model, created FROM subs
      ORDER BY created DESC LIMIT 15`
  ).all();
  let failCount = 0, recentFails = { results: [] };
  try {
    const fc = await env.DB.prepare("SELECT COUNT(*) AS n FROM failures").first();
    failCount = fc && fc.n || 0;
    recentFails = await env.DB.prepare(
      "SELECT title, year, model, reason, created FROM failures ORDER BY created DESC LIMIT 10"
    ).all();
  } catch (e) {
  }
  return {
    totals: totals || { subs: 0, downloads: 0, anchors: 0, titles: 0 },
    by_lang: byLang && byLang.results || [],
    by_model: byModel && byModel.results || [],
    top: top && top.results || [],
    recent: recent && recent.results || [],
    failures: failCount,
    recent_failures: recentFails && recentFails.results || [],
    generated: Math.floor(Date.now() / 1e3)
  };
}
__name(gatherStats, "gatherStats");
async function statsJson(env) {
  return json(await gatherStats(env));
}
__name(statsJson, "statsJson");
function fmtDate(epoch) {
  if (!epoch) return "";
  try {
    return new Date(epoch * 1e3).toISOString().replace("T", " ").slice(0, 16);
  } catch (e) {
    return "";
  }
}
__name(fmtDate, "fmtDate");
async function statsPage(env) {
  const s = await gatherStats(env);
  const t = s.totals;
  const card = /* @__PURE__ */ __name((label, val) => `<div class="card"><div class="num">${esc(val)}</div><div class="lbl">${esc(label)}</div></div>`, "card");
  const rows = /* @__PURE__ */ __name((arr, cols) => arr.map((r) => "<tr>" + cols.map((c) => `<td>${esc(c.f ? c.f(r[c.k], r) : r[c.k])}</td>`).join("") + "</tr>").join(""), "rows");
  const html = `<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MasterKodi AI Subs \u2014 \u05DE\u05D0\u05D2\u05E8 \u05E7\u05D4\u05D9\u05DC\u05EA\u05D9</title>
<style>
 :root{color-scheme:dark}
 body{margin:0;background:#0f1115;color:#e6e6e6;font-family:Segoe UI,Roboto,Arial,sans-serif}
 .wrap{max-width:900px;margin:0 auto;padding:24px}
 h1{font-size:22px;margin:0 0 4px} .sub{color:#8b93a7;font-size:13px;margin-bottom:20px}
 .cards{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:24px}
 .card{flex:1;min-width:130px;background:#171a21;border:1px solid #232733;border-radius:12px;padding:16px;text-align:center}
 .num{font-size:28px;font-weight:700;color:#4fd1c5} .lbl{font-size:12px;color:#8b93a7;margin-top:4px}
 h2{font-size:15px;margin:24px 0 8px;color:#cfd6e6}
 table{width:100%;border-collapse:collapse;font-size:13px;background:#141720;border-radius:10px;overflow:hidden}
 th,td{padding:8px 10px;text-align:right;border-bottom:1px solid #232733}
 th{background:#1b1f29;color:#8b93a7;font-weight:600}
 tr:last-child td{border-bottom:none}
 .foot{color:#5a6275;font-size:11px;margin-top:24px;text-align:center}
 .pill{background:#232733;border-radius:6px;padding:1px 7px;font-size:11px;color:#9aa3b5}
</style></head><body><div class="wrap">
 <h1>\u{1F916} MasterKodi AI Subs \u2014 \u05DE\u05D0\u05D2\u05E8 \u05DB\u05EA\u05D5\u05D1\u05D9\u05D5\u05EA \u05E7\u05D4\u05D9\u05DC\u05EA\u05D9</h1>
 <div class="sub">\u05DB\u05DC \u05EA\u05E8\u05D2\u05D5\u05DD \u05E9\u05DE\u05D9\u05E9\u05D4\u05D5 \u05D9\u05D5\u05E6\u05E8 \u05DE\u05E9\u05E8\u05EA \u05D0\u05EA \u05DB\u05D5\u05DC\u05DD \xB7 \u05E2\u05D5\u05D3\u05DB\u05DF ${esc(fmtDate(s.generated))} UTC</div>
 <div class="cards">
   ${card("\u05DB\u05EA\u05D5\u05D1\u05D9\u05D5\u05EA \u05D1\u05DE\u05D0\u05D2\u05E8", t.subs)}
   ${card("\u05DB\u05D5\u05EA\u05E8\u05D9\u05DD", t.titles)}
   ${card("\u05D4\u05D5\u05E8\u05D3\u05D5\u05EA", t.downloads)}
   ${card("\u05E2\u05DD \u05E2\u05D5\u05D2\u05DF \u05DC\u05E1\u05E0\u05DB\u05E8\u05D5\u05DF", t.anchors)}
   ${card("\u05EA\u05E8\u05D2\u05D5\u05DE\u05D9\u05DD \u05E9\u05E0\u05DB\u05E9\u05DC\u05D5", s.failures)}
 </div>
 <h2>\u05D4\u05DB\u05D9 \u05DE\u05D1\u05D5\u05E7\u05E9\u05D9\u05DD</h2>
 <table><tr><th>\u05DB\u05D5\u05EA\u05E8</th><th>\u05E9\u05E0\u05D4</th><th>\u05D2\u05E8\u05E1\u05D0\u05D5\u05EA</th><th>\u05D4\u05D5\u05E8\u05D3\u05D5\u05EA</th></tr>
 ${rows(s.top, [{ k: "title" }, { k: "year" }, { k: "variants" }, { k: "dl" }]) || '<tr><td colspan="4">\u2014</td></tr>'}</table>
 <h2>\u05E0\u05D5\u05E1\u05E4\u05D5 \u05DC\u05D0\u05D7\u05E8\u05D5\u05E0\u05D4</h2>
 <table><tr><th>\u05DB\u05D5\u05EA\u05E8</th><th>\u05D2\u05E8\u05E1\u05D4 (Release)</th><th>\u05DE\u05D5\u05D3\u05DC</th><th>\u05DE\u05EA\u05D9</th></tr>
 ${rows(s.recent, [{ k: "title" }, { k: "release" }, { k: "model" }, { k: "created", f: fmtDate }]) || '<tr><td colspan="4">\u2014</td></tr>'}</table>
 <h2>\u05E4\u05D9\u05DC\u05D5\u05D7 \u05DC\u05E4\u05D9 \u05DE\u05D5\u05D3\u05DC</h2>
 <div>${s.by_model.map((m) => `<span class="pill">${esc(m.model || "?")}: ${esc(m.n)}</span>`).join(" ") || "\u2014"}</div>
 <div class="foot">MasterKodi IL \xB7 service.subtitles.gearsai \xB7 D1 community pool</div>
</div></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8" } });
}
__name(statsPage, "statsPage");
async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((x) => x.toString(16).padStart(2, "0")).join("");
}
__name(sha256hex, "sha256hex");
export {
  worker_default as default
};
//# sourceMappingURL=worker.js.map
