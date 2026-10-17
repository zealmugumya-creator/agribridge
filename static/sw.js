// AgriBridge Uganda — Service Worker v4
// Handles offline caching, smart fetch strategies, SPA navigation, and
// OS notifications (push + notification taps).

const CACHE_NAME = 'agribridge-v7';
const STATIC_ASSETS = [
  '/',
  '/index.html',
  'https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:wght@300;400;500;600&display=swap',
  'https://cdn.jsdelivr.net/npm/chart.js',
];

// ── INSTALL: Pre-cache static assets ──
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(STATIC_ASSETS).catch(err => {
        console.warn('[SW] Some assets failed to pre-cache:', err);
      });
    }).then(() => self.skipWaiting())
  );
});

// ── ACTIVATE: Delete old caches ──
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => {
            console.log('[SW] Deleting old cache:', key);
            return caches.delete(key);
          })
      )
    ).then(() => self.clients.claim())
  );
});

// ── FETCH: Smart caching strategy ──
self.addEventListener('fetch', event => {
  // Only GETs are cacheable. Let POST/PUT/etc (orders, auth, uploads) go straight to the network.
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  // 1. Always network-first for API calls (never cache live data)
  if (url.hostname.includes('onrender.com') || url.pathname.startsWith('/api/')) {
    event.respondWith(
      fetch(event.request).catch(() =>
        new Response(
          JSON.stringify({ success: false, error: 'You appear to be offline. Please check your connection.' }),
          { headers: { 'Content-Type': 'application/json' } }
        )
      )
    );
    return;
  }

  // 2. Cache-first for Google Fonts (they change rarely)
  if (url.hostname.includes('fonts.googleapis.com') || url.hostname.includes('fonts.gstatic.com')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        });
      })
    );
    return;
  }

  // 3. Cache-first for CDN assets (Chart.js, etc.)
  if (url.hostname.includes('cdn.jsdelivr.net') || url.hostname.includes('cdnjs.cloudflare.com')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        }).catch(() => cached || new Response('', { status: 503 }));
      })
    );
    return;
  }

  // 4. Network-first with cache fallback for Unsplash images
  if (url.hostname.includes('unsplash.com') || url.hostname.includes('images.unsplash.com')) {
    event.respondWith(
      fetch(event.request).then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      }).catch(() => caches.match(event.request))
    );
    return;
  }

  // 5. Network-FIRST for the app shell (index.html + same-origin) so code
  //    fixes reach users on the very next open. Falls back to cache when the
  //    network fails, or after 4s on slow rural connections, so it still works
  //    offline. (Was stale-while-revalidate, which showed old code for a load.)
  if (url.origin === self.location.origin || event.request.mode === 'navigate') {
    event.respondWith(
      caches.open(CACHE_NAME).then(cache =>
        // Only page navigations fall back to the cached app shell. Other same-origin
        // files (legal pages, manifest.json, business-config.js) must never be answered
        // with index.html, or a slow connection would show the wrong document.
        cache.match(event.request.mode === 'navigate' ? '/index.html' : event.request).then(cached => {
          const network = fetch(event.request).then(response => {
            // Never cache error pages (404/500) as if they were the app.
            if (response.ok) cache.put(event.request.url === '/' ? '/index.html' : event.request, response.clone());
            return response;
          });
          if (!cached) {
            return network.catch(() => new Response('AgriBridge is offline', { status: 503 }));
          }
          // Prefer fresh network; fall back to cache on failure or after 4s.
          const slowFallback = new Promise(res => setTimeout(() => res(cached), 4000));
          return Promise.race([network.catch(() => cached), slowFallback]);
        })
      )
    );
    return;
  }

  // 6. Default: try network, fall back to cache
  event.respondWith(
    fetch(event.request).catch(() => caches.match(event.request))
  );
});

// ── MESSAGE: Handle skip-waiting from app ──
self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
  if (event.data && event.data.type === 'GET_VERSION') {
    event.ports[0].postMessage({ version: CACHE_NAME });
  }
});

// ── PUSH: show a real OS notification when the server sends a Web Push ──
// Payload (JSON): { title, body, url?, tag? }. Lets a future server-side push
// work with no further worker changes.
self.addEventListener('push', event => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { body: event.data ? event.data.text() : '' };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'AgriBridge', {
      body: data.body || '',
      icon: '/icon-192.png',
      tag: data.tag || 'agribridge',
      data: { url: data.url || '/?notif=1' },
      vibrate: [120, 60, 120],
    })
  );
});

// ── NOTIFICATION TAP: focus the open app (and ask it to show the bell), or open it ──
self.addEventListener('notificationclick', event => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || '/?notif=1';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const client of list) {
        if ('focus' in client) {
          client.postMessage({ type: 'OPEN_NOTIFICATIONS' });
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
