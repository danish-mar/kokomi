/**
 * In-Memory & SessionStorage Cache Helper
 * Stale-While-Revalidate (SWR) for instant UI updates and background refreshes.
 */

const _cache = new Map();
const TTL = 5 * 60 * 1000; // 5 minutes

export async function fetchWithCache(url, options = {}) {
    const now = Date.now();
    const cached = _cache.get(url);

    // If cached in memory and fresh, return memory cache immediately
    if (cached && (now - cached.ts < TTL)) {
        return cached.data;
    }

    // Attempt to load from sessionStorage for immediate initial load across page navigations
    if (!cached) {
        try {
            const ss = sessionStorage.getItem(`cache:${url}`);
            if (ss) {
                const parsed = JSON.parse(ss);
                if (now - parsed.ts < TTL) {
                    _cache.set(url, parsed);
                    // Trigger background fetch to keep fresh
                    fetch(url, options).then(res => res.json()).then(data => {
                        _cache.set(url, { data, ts: Date.now() });
                        sessionStorage.setItem(`cache:${url}`, JSON.stringify({ data, ts: Date.now() }));
                    }).catch(() => {});
                    return parsed.data;
                }
            }
        } catch(e) {}
    }

    // Fetch fresh network response
    const r = await fetch(url, options);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();

    const entry = { data, ts: Date.now() };
    _cache.set(url, entry);
    try {
        sessionStorage.setItem(`cache:${url}`, JSON.stringify(entry));
    } catch(e) {}

    return data;
}

export function invalidateCache(url) {
    if (url) {
        _cache.delete(url);
        try { sessionStorage.removeItem(`cache:${url}`); } catch(e) {}
    } else {
        _cache.clear();
        try { sessionStorage.clear(); } catch(e) {}
    }
}
