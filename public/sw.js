// App shell cache-first, API network-only, WebAssign images cache-first.
const SHELL = 'shell-v7';
const IMG = 'img-v1';
const FILES = ['/', '/index.html', '/app.js', '/mml2tex.js', '/style.css', '/manifest.webmanifest', '/icon.svg',
  '/vendor/mathlive/mathlive.min.mjs', '/vendor/mathlive/mathlive-static.css'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => ![SHELL, IMG].includes(k)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin) return;
  if (url.pathname === '/api/proxy') {
    e.respondWith(caches.open(IMG).then(async c => {
      const hit = await c.match(e.request);
      if (hit) return hit;
      const r = await fetch(e.request);
      if (r.ok) c.put(e.request, r.clone());
      return r;
    }));
    return;
  }
  if (url.pathname.startsWith('/api/')) return;
  // Stale-while-revalidate for the app shell and fonts.
  e.respondWith(caches.open(SHELL).then(async c => {
    const hit = await c.match(e.request);
    const net = fetch(e.request).then(r => { if (r.ok) c.put(e.request, r.clone()); return r; }).catch(() => hit);
    return hit || net;
  }));
});
