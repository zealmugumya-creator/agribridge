/**
 * AgriBridge Translation Worker
 * Route: POST /api/translate
 *
 * Request body:
 *   { "text": "...", "from": "lg", "to": "en" }
 *
 * Response:
 *   { "translated": "...", "from": "lg", "to": "en", "cached": false, "provider": "sunbird" }
 *
 * Providers:
 *   Sunbird AI  — Ugandan languages (lg, ac, at, lu, rk, ls, sw)
 *   Google      — Everything else (rw, fr, ar, es, de, zh, hi, pt, ha)
 *
 * KV cache: TRANSLATIONS_KV (key = sha256(lang_pair:text), TTL = 24h)
 * Secrets:
 *   SUNBIRD_USERNAME   — email used to register at api.sunbird.ai
 *   SUNBIRD_PASSWORD   — password for that account
 *   GOOGLE_TRANSLATE_KEY — Google Cloud Translate API key (optional)
 *
 * Sunbird auth:
 *   POST /auth/token (OAuth2 password flow) → access_token (7-day TTL)
 *   Token is cached in KV under key "sunbird:token" to avoid re-auth on every request.
 */

// ── Language routing ──────────────────────────────────────────────────────────

const SUNBIRD_LANGS = {
  lg:  'lug',   // Luganda
  ac:  'ach',   // Acholi
  at:  'teo',   // Ateso
  lu:  'lgg',   // Lugbara
  rk:  'nyn',   // Runyankole/Nkore-Kiga
  ls:  'xog',   // Lusoga
  sw:  'swh',   // Kiswahili
  en:  'eng',   // English (target when translating FROM Ugandan langs)
};

const GOOGLE_LANGS = {
  rw:'rw', fr:'fr', ar:'ar', es:'es', de:'de',
  zh:'zh', hi:'hi', pt:'pt', ha:'ha', so:'so', am:'am', rn:'rn',
};

function getProvider(from, to) {
  if (SUNBIRD_LANGS[from] || SUNBIRD_LANGS[to]) return 'sunbird';
  return 'google';
}

// ── Cache key ─────────────────────────────────────────────────────────────────

async function cacheKey(from, to, text) {
  const raw = `${from}:${to}:${text}`;
  const encoded = new TextEncoder().encode(raw);
  const hash = await crypto.subtle.digest('SHA-256', encoded);
  const hex = Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2,'0')).join('');
  return `tl:${hex}`;
}

// ── Sunbird auth ──────────────────────────────────────────────────────────────

async function getSunbirdToken(env) {
  const kv = env.TRANSLATIONS_KV;

  // Check KV cache first (token lives 7 days; we cache for 6 to be safe)
  if (kv) {
    const cached = await kv.get('sunbird:token');
    if (cached) return cached;
  }

  const username = env.SUNBIRD_USERNAME;
  const password = env.SUNBIRD_PASSWORD;
  if (!username || !password) throw new Error('Sunbird credentials not configured');

  // OAuth2 password flow — form-encoded body
  const body = new URLSearchParams();
  body.set('username', username);
  body.set('password', password);

  const resp = await fetch('https://api.sunbird.ai/auth/token', {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Sunbird auth ${resp.status}: ${err}`);
  }

  const data = await resp.json();
  const token = data?.access_token;
  if (!token) throw new Error('No access_token in Sunbird auth response');

  // Cache for 6 days (7-day TTL minus 1 day buffer)
  if (kv) {
    await kv.put('sunbird:token', token, { expirationTtl: 6 * 24 * 60 * 60 });
  }

  return token;
}

// ── Sunbird translate ─────────────────────────────────────────────────────────

async function translateSunbird(text, from, to, env) {
  const token = await getSunbirdToken(env);

  const srcCode = SUNBIRD_LANGS[from] || 'eng';
  const tgtCode = SUNBIRD_LANGS[to]   || 'eng';

  const resp = await fetch('https://api.sunbird.ai/tasks/translate', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Accept': 'application/json',
    },
    body: JSON.stringify({
      source_language: srcCode,
      target_language: tgtCode,
      text: text,
    }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    // If 401, token may have expired — clear cached token and throw so caller can retry
    if (resp.status === 401 && env.TRANSLATIONS_KV) {
      await env.TRANSLATIONS_KV.delete('sunbird:token');
    }
    throw new Error(`Sunbird translate ${resp.status}: ${err}`);
  }

  const data = await resp.json();
  // Response: { id, status: "COMPLETED", output: { translated_text, source_language, target_language } }
  const translated = data?.output?.translated_text;
  if (!translated) throw new Error('Sunbird returned no translated_text');
  return translated;
}

// ── Google Translate ──────────────────────────────────────────────────────────

async function translateGoogle(text, from, to, apiKey) {
  const srcCode = GOOGLE_LANGS[from] || (SUNBIRD_LANGS[from] ? 'en' : from);
  const tgtCode = GOOGLE_LANGS[to]   || (SUNBIRD_LANGS[to]   ? 'en' : to);

  const url = new URL('https://translation.googleapis.com/language/translate/v2');
  url.searchParams.set('key', apiKey);

  const resp = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ q: text, source: srcCode, target: tgtCode, format: 'text' }),
  });

  if (!resp.ok) {
    const err = await resp.text();
    throw new Error(`Google Translate ${resp.status}: ${err}`);
  }

  const data = await resp.json();
  const translated = data?.data?.translations?.[0]?.translatedText;
  if (!translated) throw new Error('Google returned no translation');
  return translated;
}

// ── Main handler ──────────────────────────────────────────────────────────────

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname !== '/api/translate') {
      return env.ASSETS.fetch(request);
    }

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
    if (request.method !== 'POST') {
      return jsonErr(405, 'Method not allowed');
    }

    let body;
    try { body = await request.json(); }
    catch { return jsonErr(400, 'Invalid JSON body'); }

    const { text, from, to } = body;
    if (!text || typeof text !== 'string' || !text.trim()) return jsonErr(400, 'text is required');
    if (!from || !to) return jsonErr(400, 'from and to are required');
    if (from === to) return jsonOk({ translated: text, from, to, cached: true, provider: 'passthrough' });

    const safeText = text.slice(0, 2000);

    // Cache lookup
    const key = await cacheKey(from, to, safeText);
    const kv  = env.TRANSLATIONS_KV;
    if (kv) {
      const cached = await kv.get(key);
      if (cached) return jsonOk({ translated: cached, from, to, cached: true, provider: 'cache' });
    }

    // Translate
    const provider = getProvider(from, to);
    let translated;
    try {
      if (provider === 'sunbird') {
        translated = await translateSunbird(safeText, from, to, env);
      } else {
        const apiKey = env.GOOGLE_TRANSLATE_KEY;
        if (!apiKey) return jsonErr(503, 'Google Translate key not configured');
        translated = await translateGoogle(safeText, from, to, apiKey);
      }
    } catch (err) {
      console.error('[translate]', err.message);
      return jsonErr(502, `Translation failed: ${err.message}`);
    }

    // Cache result
    if (kv && translated) {
      await kv.put(key, translated, { expirationTtl: 86400 });
    }

    return jsonOk({ translated, from, to, cached: false, provider });
  },
};

function corsHeaders() {
  return {
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  };
}
function jsonOk(data) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { 'Content-Type': 'application/json', ...corsHeaders() },
  });
}
function jsonErr(status, message) {
  return new Response(JSON.stringify({ error: message }), {
    status,
    headers: { 'Content-Type': 'application/json', ...corsHeaders() },
  });
}
