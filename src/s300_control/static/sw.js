"use strict";
const CACHE = "s300-shell-v10-black";
const ASSETS = [
  "./",
  "./index.html",
  "./app.css",
  "./app.js",
  "./manifest.webmanifest",
  "./icons/v4/black/icon.svg",
  "./icons/v4/black/app-1024.png",
  "./icons/v4/black/app-128.png",
  "./icons/v4/black/app-16.png",
  "./icons/v4/black/app-192.png",
  "./icons/v4/black/app-256.png",
  "./icons/v4/black/app-32.png",
  "./icons/v4/black/app-384.png",
  "./icons/v4/black/app-48.png",
  "./icons/v4/black/app-512.png",
  "./icons/v4/black/app-96.png",
  "./icons/v4/black/apple-touch-icon-1024.png",
  "./icons/v4/black/maskable-1024.png",
  "./icons/v4/black/maskable-512.png",
];
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .then(() => self.skipWaiting()),
  );
});
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((key) => key.startsWith("s300-shell-") && key !== CACHE)
            .map((key) => caches.delete(key)),
        ),
      )
      .then(() => self.clients.claim()),
  );
});
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Control writes and state are always live. Never cache or replay device commands.
  if (
    event.request.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.includes("/api/") ||
    url.pathname.endsWith("/healthz")
  )
    return;
  const assetUrls = ASSETS.map(
    (asset) => new URL(asset, self.registration.scope).href,
  );
  if (event.request.mode !== "navigate" && !assetUrls.includes(url.href))
    return;
  event.respondWith(
    fetch(event.request)
      .then(async (response) => {
        if (response.status >= 500 && event.request.mode === "navigate") {
          const shell = await caches.match(
            new URL("./index.html", self.registration.scope),
          );
          if (shell) return shell;
        }
        if (response.ok) {
          const clone = response.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, clone));
        }
        return response;
      })
      .catch(
        async () =>
          (await caches.match(event.request)) ||
          (event.request.mode === "navigate"
            ? caches.match(new URL("./index.html", self.registration.scope))
            : Response.error()),
      ),
  );
});
