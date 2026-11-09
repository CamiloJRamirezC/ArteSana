// Service worker de ArteSana: permite instalar la app y muestra un aviso
// claro cuando el servidor no está corriendo. Siempre intenta la red primero,
// así las actualizaciones se ven al instante.
const CACHE = "artesana-v1";
const BASICOS = [
  "/offline",
  "/static/estilo.css",
  "/static/iconos/icono-192.png",
  "/static/iconos/favicon-32.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(BASICOS)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((claves) => Promise.all(claves.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== location.origin) return;

  if (req.mode === "navigate") {
    e.respondWith(fetch(req).catch(() => caches.match("/offline")));
    return;
  }
  if (new URL(req.url).pathname.startsWith("/static/")) {
    e.respondWith(
      fetch(req)
        .then((resp) => {
          const copia = resp.clone();
          caches.open(CACHE).then((c) => c.put(req, copia));
          return resp;
        })
        .catch(() => caches.match(req))
    );
  }
});
