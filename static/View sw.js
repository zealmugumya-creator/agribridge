// ═══════════════════════════════════════════════════════
// AgriBridge Service Worker v2
// Bump CACHE_NAME version whenever index.html changes
// ═══════════════════════════════════════════════════════
const CACHE_NAME = 'agribridge-v2';

// App shell — files that make the app work offline
const SHELL_URLS = [
  '/',
  '/index.html',
  '/manifest.json',
  'https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap'
];

// ── INSTALL: cache the app shell ────────────────────────
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return Promise.allSettled(
        SHELL_URLS.map(url =>
          cache.add(url).catch(err => {
            console.warn('AgriBridge SW: could not cache', url, err);
          })
        )
      );
    }).then(() => {
      console.log('AgriBridge SW v2 installed');
      return self.skipWaiting(); // activate immediately
    })
  );
});

// ── ACTIVATE: remove old caches ─────────────────────────
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => {
            console.log('AgriBridge SW: deleting old cache', key);
            return caches.delete(key);
          })
      )
    ).then(() => {
      console.log('AgriBridge SW v2 activated');
      return self.clients.claim(); // take control of all tabs
    })
  );
});

// ── FETCH: smart caching strategy ───────────────────────
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // 1. Supabase & backend API — always network-first, no caching
  if (
    url.hostname.includes('supabase.co') ||
    url.hostname.includes('onrender.com') ||
    url.pathname.includes('/rest/') ||
    url.pathname.includes('/auth/')
  ) {
    event.respondWith(
      fetch(event.request).catch(() => {
        // Offline fallback for API: return empty result so UI degrades gracefully
        return new Response(
          JSON.stringify({ error: 'offline', data: [], count: 0 }),
          { headers: { 'Content-Type': 'application/json' } }
        );
      })
    );
    return;
  }

  // 2. Google Fonts — cache-first (they rarely change)
  if (
    url.hostname.includes('fonts.googleapis.com') ||
    url.hostname.includes('fonts.gstatic.com')
  ) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // 3. Own origin (index.html, manifest.json, sw.js) — stale-while-revalidate
  //    Serve from cache immediately, update cache in background
  if (url.origin === self.location.origin) {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        cache.match(event.request).then(cached => {
          const networkFetch = fetch(event.request).then(response => {
            if (response.ok && event.request.method === 'GET') {
              cache.put(event.request, response.clone());
            }
            return response;
          }).catch(() => {
            // Network failed — fall back to cache
            if (cached) return cached;
            // Navigation requests: serve index.html as SPA fallback
            if (event.request.mode === 'navigate') {
              return cache.match('/index.html');
            }
            return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
          });

          // Return cached version immediately if available, otherwise wait for network
          return cached || networkFetch;
        })
      )
    );
    return;
  }

  // 4. Everything else — network only
  event.respondWith(fetch(event.request));
});

// ── MESSAGE: handle skip-waiting from app ───────────────
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  // Allow app to check SW version
  if (event.data && event.data.type === 'GET_VERSION') {
    event.ports[0].postMessage({ version: CACHE_NAME });
  }
});
