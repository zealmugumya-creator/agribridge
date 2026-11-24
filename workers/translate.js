/**
 * AgriBridge Translation Worker
 * Route: POST /api/translate
 *
 * Request body:
 *   { "text": "...", "from": "lg", "to": "en" }
 *   OR { "text": "...", "from": "en", "to": "lg" }
 *
 * Response:
 *   { "translated": "...", "from": "lg", "to": "en", "cached": false, "provider": "sunbird" }
 *
 * Providers:
 *   Sunbird AI  — Ugandan languages (lg, ac, at, lu, rk, ls, sw)
 *   Google      — Everything else (rw, fr, ar, es, de, zh, hi, pt, ha, en)
 *
 * KV cache: TRANSLATIONS_KV (key = sha256(lang_pair:text), TTL = 24h)
 * Secrets:  SUNBIRD_API_KEY, GOOGLE_TRANSLATE_KEY
 */

// ── Language routing ─────────────────────────────────────────────────────────

// Sunbird AI language codes (their format differs from BCP-47)
const SUNBIRD_LANGS = {
  lg:  'lug',   // Luganda
  ac:  'ach',   // Acholi
  at:  'teo',   // Ateso
  lu:  'lgg',   // Lugbara
  rk:  'nyn',   // Runyankole/Nkore-Kiga
  ls:  'xog',   // Lusoga
  sw:  'swh',   // Kiswahili
};

// Google Translate language codes
const GOOGLE_LANGS = {
  rw: 'rw',  // Kinyarwanda
  fr: 'fr',  // French
  ar: 'ar',  // Arabic
  es: 'es',  // Spanish
  de: 'de',  // German
  zh: 'zh',  // Chinese
  hi: 'hi',  // Hindi
  pt: 'pt',  // Portuguese
  ha: 'ha',  // Hausa
  so: 'so',  // Somali
  am: 'am',  // Amharic
  rn: 'rn',  // Kirundi
  en: 'en',  // English (target for Sunbird, source for Google)
};

function getProvider(from, to) {
  // If either language is a Sunbird language, use Sunbird (they handle en↔lg etc)
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

// ── Sunbird AI ────────────────────────────────────────────────────────────────

async function translateSunbird(text, from, to, apiKey) {
  const srcCode = SUNBIRD_LANGS[from] || 'eng';
  const tgtCode = SUNBIRD_LANGS[to]   || 'eng';

  const resp = await fetch('https://api.sunbird.ai/tasks/nllb_translate', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
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
    throw new Error(`Sunbird ${resp.status}: ${err}`);
  }

  const data = await resp.json();
  // Sunbird returns: { output: { translated_text: "..." } }
  const translated = data?.output?.translated_text;
  if (!translated) throw new Error('Sunbird returned no translation');
  return translated;
}

// ── Google Translate ──────────────────────────────────────────────────────────

async function translateGoogle(text, from, to, apiKey) {
  const srcCode = GOOGLE_LANGS[from] || SUNBIRD_LANGS[from] ? 'en' : from;
  const tgtCode = GOOGLE_LANGS[to]   || SUNBIRD_LANGS[to]   ? 'en' : to;

  const url = new URL('https://translation.googleapis.com/language/translate/v2');
  url.searchParams.set('key', apiKey);

  const resp = await fetch(url.toString(), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      q: text,
      source: srcCode,
      target: tgtCode,
      format: 'text',
    }),
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

    // Only intercept POST /api/translate
    if (url.pathname !== '/api/translate') {
      // Let Cloudflare Assets handle everything else
      return env.ASSETS.fetch(request);
    }

    // CORS preflight
    if (request.method === 'OPTIONS') {
      return new Response(null, {
        status: 204,
        headers: corsHeaders(),
      });
    }

    if (request.method !== 'POST') {
      return jsonErr(405, 'Method not allowed');
    }

    // Parse body
    let body;
    try {
      body = await request.json();
    } catch {
      return jsonErr(400, 'Invalid JSON body');
    }

    const { text, from, to } = body;

    if (!text || typeof text !== 'string' || text.trim().length === 0) {
      return jsonErr(400, 'text is required');
    }
    if (!from || !to) {
      return jsonErr(400, 'from and to language codes are required');
    }
    if (from === to) {
      // No-op — return as-is
      return jsonOk({ translated: text, from, to, cached: true, provider: 'passthrough' });
    }

    // Truncate to 2000 chars (Sunbird limit)
    const safeText = text.slice(0, 2000);

    // ── Cache lookup ────────────────────────────────────────────────────────
    const key = await cacheKey(from, to, safeText);
    const kv  = env.TRANSLATIONS_KV;

    if (kv) {
      const cached = await kv.get(key);
      if (cached) {
        return jsonOk({ translated: cached, from, to, cached: true, provider: 'cache' });
      }
    }

    // ── Translate ───────────────────────────────────────────────────────────
    const provider = getProvider(from, to);
    let translated;

    try {
      if (provider === 'sunbird') {
        const apiKey = env.SUNBIRD_API_KEY;
        if (!apiKey) return jsonErr(503, 'Sunbird API key not configured');
        translated = await translateSunbird(safeText, from, to, apiKey);
      } else {
        const apiKey = env.GOOGLE_TRANSLATE_KEY;
        if (!apiKey) return jsonErr(503, 'Google Translate key not configured');
        translated = await translateGoogle(safeText, from, to, apiKey);
      }
    } catch (err) {
      console.error('[translate]', err.message);
      return jsonErr(502, `Translation failed: ${err.message}`);
    }

    // ── Cache store (24h TTL) ───────────────────────────────────────────────
    if (kv && translated) {
      await kv.put(key, translated, { expirationTtl: 86400 });
    }

    return jsonOk({ translated, from, to, cached: false, provider });
  },
};

// ── Helpers ───────────────────────────────────────────────────────────────────

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
