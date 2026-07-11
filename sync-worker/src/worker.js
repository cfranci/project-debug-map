// Ready Debug Map sync worker
// - Serves the debug map static assets (index.html + *.project.json)
// - Shared bug store in KV so the whole team sees one Kanban per project
// - Tunnel env overlay: keepalive scripts POST their current staging URLs,
//   served config JSONs get environments rewritten on the fly

const JSON_HEADERS = {
  'content-type': 'application/json',
  'cache-control': 'no-store',
  'access-control-allow-origin': '*',
  'access-control-allow-methods': 'GET, POST, OPTIONS',
  'access-control-allow-headers': 'content-type, x-debug-key',
};

function authed(request, env) {
  const url = new URL(request.url);
  const key = request.headers.get('x-debug-key') || url.searchParams.get('key');
  return key && env.SYNC_KEY && key === env.SYNC_KEY;
}

async function loadStore(env, project) {
  const raw = await env.BUGS.get('bugs:' + project);
  if (!raw) return { bugs: {}, tombstones: [] };
  try { return JSON.parse(raw); } catch { return { bugs: {}, tombstones: [] }; }
}

async function saveStore(env, project, store) {
  await env.BUGS.put('bugs:' + project, JSON.stringify(store));
}

function storeResponse(store) {
  return new Response(JSON.stringify({
    bugs: Object.values(store.bugs),
    tombstones: store.tombstones,
  }), { headers: JSON_HEADERS });
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (path.startsWith('/api/')) {
      if (request.method === 'OPTIONS') return new Response(null, { headers: JSON_HEADERS });
      if (!authed(request, env)) {
        return new Response(JSON.stringify({ error: 'bad key' }), { status: 401, headers: JSON_HEADERS });
      }

      if (path === '/api/bugs') {
        const project = (url.searchParams.get('project') || '').replace(/[^a-z0-9-]/g, '');
        if (!project) return new Response(JSON.stringify({ error: 'project required' }), { status: 400, headers: JSON_HEADERS });

        if (request.method === 'GET') {
          return storeResponse(await loadStore(env, project));
        }
        if (request.method === 'POST') {
          const body = await request.json();
          const store = await loadStore(env, project);
          const tomb = new Set(store.tombstones);
          for (const id of (body.deletes || [])) {
            delete store.bugs[id];
            tomb.add(id);
          }
          for (const b of (body.upserts || [])) {
            if (!b || typeof b.id === 'undefined' || tomb.has(b.id)) continue;
            const cur = store.bugs[b.id];
            if (cur && (cur.updated || 0) > (b.updated || 0)) continue;
            store.bugs[b.id] = b;
          }
          store.tombstones = [...tomb].slice(-2000);
          await saveStore(env, project, store);
          return storeResponse(store);
        }
      }

      if (path === '/api/envs' && request.method === 'POST') {
        // { project: "<config filename without .project.json>", environments: {label: url} }
        const body = await request.json();
        if (!body.project || !body.environments) {
          return new Response(JSON.stringify({ error: 'project + environments required' }), { status: 400, headers: JSON_HEADERS });
        }
        await env.BUGS.put('envs:' + body.project, JSON.stringify({
          environments: body.environments,
          reportedAt: new Date().toISOString(),
          host: body.host || 'unknown',
        }));
        return new Response(JSON.stringify({ ok: true }), { headers: JSON_HEADERS });
      }

      if (path === '/api/envs' && request.method === 'GET') {
        const project = url.searchParams.get('project') || '';
        const raw = await env.BUGS.get('envs:' + project);
        return new Response(raw || '{}', { headers: JSON_HEADERS });
      }

      return new Response(JSON.stringify({ error: 'not found' }), { status: 404, headers: JSON_HEADERS });
    }

    // Config JSONs: overlay live tunnel environments if a keepalive has reported them
    if (path.endsWith('.project.json')) {
      const asset = await env.ASSETS.fetch(request);
      if (!asset.ok) return asset;
      const slug = path.slice(1).replace(/\.project\.json$/, '');
      const raw = await env.BUGS.get('envs:' + slug);
      if (!raw) return asset;
      try {
        const cfg = await asset.json();
        const live = JSON.parse(raw);
        cfg.environments = { ...live.environments, ...cfg.environments };
        // live tunnel URLs go first so they become the default environment
        const ordered = {};
        for (const k of Object.keys(live.environments)) ordered[k] = live.environments[k];
        for (const k of Object.keys(cfg.environments)) if (!(k in ordered)) ordered[k] = cfg.environments[k];
        cfg.environments = ordered;
        return new Response(JSON.stringify(cfg), { headers: JSON_HEADERS });
      } catch {
        return env.ASSETS.fetch(request);
      }
    }

    return env.ASSETS.fetch(request);
  },
};
