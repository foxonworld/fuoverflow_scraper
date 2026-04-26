const CACHE_NAME = 'fu-exam-shell-v2';
const IMAGE_CACHE_NAME = 'fu-exam-images-v2';

const ASSETS = [
    './',
    './index.html',
    './data.js',
    './manifest.json',
    './icon.svg'
];

// 1. Cài đặt: Lưu App Shell
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => cache.addAll(ASSETS))
    );
    self.skipWaiting();
});

// 2. Kích hoạt: Dọn dẹp cache cũ
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys => Promise.all(
            keys.map(key => {
                if (key !== CACHE_NAME && key !== IMAGE_CACHE_NAME) return caches.delete(key);
            })
        ))
    );
    self.clients.claim();
});

// 3. Fetch: Xử lý thông minh
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    const isImage = url.pathname.match(/\.(webp|png|jpg|jpeg|svg|gif|ico)$/i) || url.pathname.includes('/images/');

    // Giao thức Cache-First cho HÌNH ẢNH (Quan trọng để tiết kiệm Token)
    if (isImage) {
        event.respondWith(
            caches.open(IMAGE_CACHE_NAME).then(cache => {
                return cache.match(event.request).then(cachedResponse => {
                    if (cachedResponse) {
                        // console.log('[SW] Cache Hit:', url.pathname);
                        return cachedResponse; 
                    }

                    // Nếu chưa có, tải về và lưu lại mãi mãi
                    return fetch(event.request).then(networkResponse => {
                        if (networkResponse && networkResponse.status === 200) {
                            cache.put(event.request, networkResponse.clone());
                        }
                        return networkResponse;
                    }).catch(() => {
                        // Offline hoàn toàn và chưa cache
                        return new Response('Offline', { status: 503 });
                    });
                });
            })
        );
        return;
    }

    // Giao thức Network-First cho HTML/JS (Để cập nhật khi có data mới)
    event.respondWith(
        fetch(event.request)
            .then(networkResponse => {
                if (networkResponse && networkResponse.status === 200) {
                    const copy = networkResponse.clone();
                    caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy));
                }
                return networkResponse;
            })
            .catch(() => caches.match(event.request))
    );
});
