/**
 * Kokomi Message Widgets
 * ----------------------
 * Convention-based rich widgets rendered inside AI message bubbles. Mirrors the
 * KokomiCharts / KokomiDiagrams pattern: renderMarkdown emits HTML carrying
 * `data-*` markers, and a module-level manager with a debounced MutationObserver
 * hydrates them once they land in the DOM. Everything degrades to plain text/HTML
 * if the manager never runs.
 *
 * Widgets:
 *   - Images        — every markdown image becomes a <figure> with lazy load,
 *                     captured dimensions, and click-to-expand lightbox. Images
 *                     inside markdown tables get the lightbox too.
 *   - Video         — markdown links/images to .mp4/.webm/.ogg, or a
 *                     ```kokomi-video``` fenced block (bare URL or JSON).
 *   - Tables        — GFM tables become sortable + filterable.
 *   - Action chips  — a ```kokomi-actions``` fenced block becomes clickable chips
 *                     that fill the composer, send a message, or open a URL.
 */

const VIDEO_EXT = /\.(mp4|webm|ogg|ogv|mov|m4v)(\?.*)?$/i;

// ── small shared helpers ─────────────────────────────────────────────────────
function escapeHtml(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function escapeAttr(s) { return escapeHtml(s).replace(/`/g, '&#96;'); }

// Route remote media through the same-origin image proxy (sidesteps CORP /
// mixed-content / hotlink blocks). Local paths and data URIs pass through.
function proxy(url) {
    if (!url) return '';
    if (url.startsWith('/') || url.startsWith('data:') || url.startsWith('blob:')) return url;
    return '/api/img?url=' + encodeURIComponent(url);
}

// ── Renderers (called from markdown.js setupMarkdown) ─────────────────────────

// Markdown image -> image figure OR inline video, depending on the URL.
export function renderImage(token) {
    const href = (token && token.href) || '';
    const text = (token && token.text) || '';
    const title = (token && token.title) || '';
    if (!href) return '';

    if (VIDEO_EXT.test(href)) {
        return videoHtml({ src: href, title: title || text });
    }

    const src = proxy(href);
    const cap = title || text;
    return `<figure class="kokomi-img-fig" data-kokomi-img>
        <img class="kokomi-img" loading="lazy" src="${escapeAttr(src)}"
             data-full="${escapeAttr(src)}" alt="${escapeAttr(text)}"
             onload="window.KokomiWidgets&&window.KokomiWidgets.onImgLoad(this)"
             onerror="this.closest('figure')?.classList.add('kokomi-img--err')">
        <figcaption class="kokomi-img-cap">
            <span class="kokomi-img-text">${escapeHtml(cap)}</span>
            <span class="kokomi-img-dims"></span>
        </figcaption>
    </figure>`;
}

// GFM table -> sortable / filterable table, with markers the manager hydrates.
export function renderTable(obj) {
    const header = obj.header.map(cell =>
        `<th>${marked.parseInline(cell.text)}</th>`).join('');
    const rows = obj.rows.map(row =>
        `<tr>${row.map(cell => `<td>${marked.parseInline(cell.text)}</td>`).join('')}</tr>`
    ).join('');
    return `<div class="kokomi-tbl-wrap" data-kokomi-table>
        <div class="kokomi-tbl-toolbar">
            <i class="fa-solid fa-magnifying-glass kokomi-tbl-search-ico"></i>
            <input type="text" class="kokomi-tbl-filter" placeholder="Filter rows…">
            <span class="kokomi-tbl-count"></span>
        </div>
        <div class="table-wrapper">
            <table class="kokomi-tbl"><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table>
        </div>
    </div>`;
}

// Fenced ```kokomi-video``` — bare URL or JSON { src, poster, title }.
export function renderVideoBlock(code) {
    let src = '', poster = '', title = '';
    const raw = (code || '').trim();
    try {
        const o = JSON.parse(raw);
        src = o.src || o.url || ''; poster = o.poster || ''; title = o.title || '';
    } catch {
        src = raw.split(/\s+/)[0] || '';
    }
    if (!src) return `<div class="kokomi-widget-err">Empty kokomi-video block</div>`;
    return videoHtml({ src, poster, title });
}

function videoHtml({ src, poster = '', title = '' }) {
    // NOTE: video is NOT routed through /api/img — that proxy only serves images
    // (it 415s on video content). The browser fetches the video URL directly. Only
    // the poster (an image) goes through the proxy.
    return `<figure class="kokomi-video-fig">
        <video class="kokomi-video" controls preload="metadata" playsinline
               ${poster ? `poster="${escapeAttr(proxy(poster))}"` : ''}>
            <source src="${escapeAttr(src)}">
        </video>
        ${title ? `<figcaption class="kokomi-img-cap"><span class="kokomi-img-text">${escapeHtml(title)}</span></figcaption>` : ''}
    </figure>`;
}

// Fenced ```kokomi-actions``` — JSON array or newline list of actions.
// Each action carries exactly one verb plus optional presentation:
//   verbs:  { send } | { fill } | { url } | { copy } | { set: {key:val} }
//   extra:  { label, icon, variant: "primary"|"ghost"|"danger", confirm: "prompt?" }
const MAX_VISIBLE_CHIPS = 6;

// The chat bubble re-renders via a wholesale innerHTML replace (Alpine's
// x-html) on every streaming chunk, AND once more when msg.streaming flips to
// false at the end — even when this block's content hasn't changed. Each
// replace mounts brand-new DOM nodes, which replays the CSS entrance
// animation. Track which exact (already-complete) block contents have played
// their entrance once already, so a re-render of unchanged content renders
// statically instead of animating again.
const _animatedActionBlocks = new Set();
function _hasAnimatedOnce(raw) {
    if (_animatedActionBlocks.has(raw)) return true;
    if (_animatedActionBlocks.size > 300) _animatedActionBlocks.clear(); // defensive cap
    _animatedActionBlocks.add(raw);
    return false;
}

// A block is still mid-stream (not genuinely malformed) if its brackets aren't
// balanced yet, or it doesn't end on a closing bracket. Used to decide between
// showing an animated shimmer (still typing) vs a real parse failure.
function looksStructurallyIncomplete(raw) {
    if (!raw) return true;
    const opens = (raw.match(/[{\[]/g) || []).length;
    const closes = (raw.match(/[}\]]/g) || []).length;
    return opens === 0 || opens !== closes || !/[}\]]\s*$/.test(raw);
}

function renderActionsShimmer() {
    return `<div class="kokomi-actions kokomi-actions--loading" data-kokomi-actions>
        <div class="kokomi-chip-shimmer" style="width:96px;"></div>
        <div class="kokomi-chip-shimmer" style="width:132px;"></div>
        <div class="kokomi-chip-shimmer" style="width:80px;"></div>
    </div>`;
}

export function renderActionsBlock(code) {
    const raw = (code || '').trim();
    let items = [];
    try {
        const parsed = JSON.parse(raw);
        items = Array.isArray(parsed) ? parsed : (parsed.actions || []);
    } catch {
        // Streaming JSON is incomplete until the closing bracket lands — show a
        // shimmer skeleton instead of the previous behavior (splitting the raw,
        // half-written JSON into one ugly chip per line). Only fall back to the
        // lenient newline parser once the block looks structurally finished but
        // still fails to parse (a genuinely malformed block from the model).
        if (looksStructurallyIncomplete(raw)) {
            return renderActionsShimmer();
        }
        items = raw.split('\n').map(l => l.trim()).filter(Boolean).map(line => {
            const [label, payload] = line.split('|').map(s => s.trim());
            return { label, send: payload || label };
        });
    }
    // Empty list: either genuinely no actions (valid "[]", render nothing) or
    // still-streaming with nothing parsed yet out of an incomplete block.
    if (!items.length) return looksStructurallyIncomplete(raw) ? renderActionsShimmer() : '';

    // Staggered entrance: each chip fades/slides in a beat after the previous
    // one (via an inline animation-delay) so the final reveal feels alive
    // instead of the whole row popping in at once. Only the FIRST time this
    // exact (complete) content is rendered gets the animation — a later
    // wholesale re-render of unchanged content (e.g. when streaming ends)
    // renders statically instead of replaying it.
    const animate = !_hasAnimatedOnce(raw);
    const chip = (a, hidden, index) => {
        const detail = JSON.stringify({
            fill: a.fill, send: a.send, url: a.url, copy: a.copy, set: a.set,
            confirm: a.confirm, label: a.label
        });
        const icon = a.icon ? `<i class="${escapeAttr(a.icon)}"></i>` : '';
        const variant = ['primary', 'ghost', 'danger'].includes(a.variant) ? ` kokomi-chip--${a.variant}` : '';
        const delay = (hidden || !animate) ? '' : ` style="animation-delay:${Math.min(index, 8) * 45}ms"`;
        const inClass = (hidden || !animate) ? '' : ' kokomi-chip--in';
        return `<button type="button" class="kokomi-chip${variant}${hidden ? ' kokomi-chip--hidden' : inClass}"${delay}
            onclick='window.KokomiWidgets&&window.KokomiWidgets.action(this)'
            data-action="${escapeAttr(detail)}">${icon}<span>${escapeHtml(a.label || 'Action')}</span></button>`;
    };

    const visible = items.slice(0, MAX_VISIBLE_CHIPS).map((a, i) => chip(a, false, i));
    const overflow = items.slice(MAX_VISIBLE_CHIPS);
    let tail = '';
    if (overflow.length) {
        const moreDelay = animate ? ` style="animation-delay:${Math.min(visible.length, 8) * 45}ms"` : '';
        const hidden = overflow.map((a, i) => chip(a, true, i)).join('');
        tail = hidden + `<button type="button" class="kokomi-chip kokomi-chip--more${animate ? ' kokomi-chip--in' : ''}"${moreDelay}
            onclick='window.KokomiWidgets&&window.KokomiWidgets.toggleMore(this)'>
            <i class="fa-solid fa-ellipsis"></i><span>+${overflow.length} more</span></button>`;
    }
    return `<div class="kokomi-actions" data-kokomi-actions>${visible.join('')}${tail}</div>`;
}

// Pull <video>/<img> out of a model-emitted ```html``` snippet and render the
// safe widget for it. Returns null if it isn't a simple, media-only snippet
// (so genuine HTML source still highlights normally). We never inject raw HTML.
function renderHtmlMedia(code) {
    const raw = (code || '').trim();
    // Only handle snippets that are essentially one media tag (no scripts, etc.).
    if (/<\s*(script|iframe|object|embed|form|style)/i.test(raw)) return null;

    if (/^<\s*video[\s>]/i.test(raw)) {
        const src = (raw.match(/<\s*source[^>]*\ssrc\s*=\s*["']([^"']+)["']/i)
                  || raw.match(/<\s*video[^>]*\ssrc\s*=\s*["']([^"']+)["']/i) || [])[1];
        const poster = (raw.match(/\sposter\s*=\s*["']([^"']+)["']/i) || [])[1] || '';
        if (src) return videoHtml({ src, poster });
    }
    if (/^<\s*img[\s>]/i.test(raw)) {
        const src = (raw.match(/\ssrc\s*=\s*["']([^"']+)["']/i) || [])[1];
        const alt = (raw.match(/\salt\s*=\s*["']([^"']*)["']/i) || [])[1] || '';
        if (src) return renderImage({ href: src, text: alt });
    }
    return null;
}

// Intercept fenced langs in markdown.js renderer.code. Returns HTML, or null to
// let the default code renderer handle it.
export function renderCodeWidget(lang, code) {
    switch ((lang || '').toLowerCase()) {
        case 'kokomi-video': return renderVideoBlock(code);
        case 'kokomi-actions': return renderActionsBlock(code);
        // Weaker models often wrap real markdown (images/tables) in a ```markdown
        // fence, which would otherwise render as source. Re-parse it so the widgets
        // actually appear.
        case 'markdown':
        case 'md':
            try { return marked.parse(code || ''); } catch { return null; }
        // Same for a lone <video>/<img> emitted inside a ```html fence.
        case 'html':
        case 'xml':
            return renderHtmlMedia(code);
        default: return null;
    }
}

// ── Manager: hydration + lightbox + action dispatch ──────────────────────────
const KokomiWidgets = {
    _lightbox: null,

    onImgLoad(img) {
        try {
            const dims = img.closest('figure')?.querySelector('.kokomi-img-dims');
            if (dims && img.naturalWidth) dims.textContent = `${img.naturalWidth}×${img.naturalHeight}`;
        } catch { /* noop */ }
    },

    // Sort + filter wiring for tables (idempotent; re-run safe via data-hydrated).
    hydrateTables(root = document) {
        root.querySelectorAll('.kokomi-tbl-wrap:not([data-hydrated])').forEach((wrap) => {
            wrap.setAttribute('data-hydrated', '1');
            const table = wrap.querySelector('table.kokomi-tbl');
            const tbody = table?.querySelector('tbody');
            if (!table || !tbody) return;

            // Sort
            table.querySelectorAll('thead th').forEach((th, idx) => {
                th.classList.add('kokomi-th-sort');
                th.addEventListener('click', () => {
                    const asc = th.getAttribute('data-sort') !== 'asc';
                    table.querySelectorAll('thead th').forEach(o => o.removeAttribute('data-sort'));
                    th.setAttribute('data-sort', asc ? 'asc' : 'desc');
                    const rows = [...tbody.querySelectorAll('tr')];
                    const val = (tr) => tr.children[idx]?.textContent.trim() ?? '';
                    rows.sort((a, b) => {
                        const x = val(a), y = val(b);
                        const nx = parseFloat(x.replace(/[^0-9.\-]/g, ''));
                        const ny = parseFloat(y.replace(/[^0-9.\-]/g, ''));
                        const bothNum = !isNaN(nx) && !isNaN(ny) && x !== '' && y !== '';
                        const cmp = bothNum ? nx - ny : x.localeCompare(y, undefined, { numeric: true });
                        return asc ? cmp : -cmp;
                    });
                    rows.forEach(r => tbody.appendChild(r));
                });
            });

            // Filter
            const filter = wrap.querySelector('.kokomi-tbl-filter');
            const count = wrap.querySelector('.kokomi-tbl-count');
            const total = tbody.querySelectorAll('tr').length;
            const updateCount = (shown) => { if (count) count.textContent = `${shown}/${total}`; };
            updateCount(total);
            filter?.addEventListener('input', () => {
                const q = filter.value.trim().toLowerCase();
                let shown = 0;
                tbody.querySelectorAll('tr').forEach(tr => {
                    const hit = !q || tr.textContent.toLowerCase().includes(q);
                    tr.style.display = hit ? '' : 'none';
                    if (hit) shown++;
                });
                updateCount(shown);
            });
        });
    },

    hydrate(root = document) {
        this.hydrateTables(root);
    },

    // ── Lightbox ─────────────────────────────────────────────────────────────
    _ensureLightbox() {
        if (this._lightbox) return this._lightbox;
        const el = document.createElement('div');
        el.className = 'kokomi-lightbox';
        el.innerHTML = `
            <button class="kokomi-lb-close" title="Close"><i class="fa-solid fa-xmark"></i></button>
            <a class="kokomi-lb-open" target="_blank" title="Open original"><i class="fa-solid fa-up-right-from-square"></i></a>
            <img class="kokomi-lb-img" alt="">
            <div class="kokomi-lb-meta"></div>`;
        el.addEventListener('click', (e) => {
            if (e.target === el || e.target.closest('.kokomi-lb-close')) this.closeLightbox();
        });
        document.body.appendChild(el);
        this._lightbox = el;
        return el;
    },

    openLightbox(src, full, alt = '') {
        const lb = this._ensureLightbox();
        const img = lb.querySelector('.kokomi-lb-img');
        const meta = lb.querySelector('.kokomi-lb-meta');
        const open = lb.querySelector('.kokomi-lb-open');
        img.src = full || src;
        open.href = full || src;
        meta.textContent = '';
        img.onload = () => { if (img.naturalWidth) meta.textContent = `${img.naturalWidth}×${img.naturalHeight}${alt ? ' · ' + alt : ''}`; };
        lb.classList.add('open');
        document.body.style.overflow = 'hidden';
    },

    closeLightbox() {
        if (!this._lightbox) return;
        this._lightbox.classList.remove('open');
        document.body.style.overflow = '';
    },

    // ── Action chip dispatch ─────────────────────────────────────────────────
    action(btn) {
        let detail = {};
        try { detail = JSON.parse(btn.getAttribute('data-action') || '{}'); } catch { /* noop */ }

        // Optional confirmation gate (for stateful / destructive actions).
        if (detail.confirm && !window.confirm(detail.confirm)) return;

        // Verbs handled entirely on the client — no round-trip to the component.
        if (detail.copy != null) {
            navigator.clipboard?.writeText(String(detail.copy)).then(() => {
                const span = btn.querySelector('span');
                if (span) {
                    const prev = span.textContent;
                    span.textContent = 'Copied!';
                    btn.classList.add('kokomi-chip--ok');
                    setTimeout(() => { span.textContent = prev; btn.classList.remove('kokomi-chip--ok'); }, 1400);
                }
            }).catch(() => { /* clipboard blocked */ });
            return;
        }
        if (detail.url) {
            window.open(detail.url, '_blank', 'noopener');
            return;
        }

        // send / fill / set are applied by the Alpine chat component.
        window.dispatchEvent(new CustomEvent('kokomi-action', { detail }));
    },

    // Reveal the chips hidden behind a "+N more" toggle, staggered in the same
    // style as the initial reveal.
    toggleMore(btn) {
        const wrap = btn.closest('.kokomi-actions');
        if (!wrap) return;
        wrap.querySelectorAll('.kokomi-chip--hidden').forEach((c, i) => {
            c.classList.remove('kokomi-chip--hidden');
            c.classList.add('kokomi-chip--in');
            c.style.animationDelay = `${Math.min(i, 8) * 45}ms`;
        });
        btn.remove();
    },

    setupObservers() {
        const chat = document.body;
        let t;
        const obs = new MutationObserver(() => {
            clearTimeout(t);
            t = setTimeout(() => this.hydrate(), 120);
        });
        obs.observe(chat, { childList: true, subtree: true });

        // Lightbox: delegate clicks for any widget image or table-cell image.
        document.addEventListener('click', (e) => {
            const img = e.target.closest('.kokomi-img, .kokomi-tbl td img, .chat-prose td img');
            if (!img) return;
            e.preventDefault();
            this.openLightbox(img.currentSrc || img.src, img.getAttribute('data-full') || img.src, img.alt);
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') this.closeLightbox();
        });

        this.hydrate();
    },
};

window.KokomiWidgets = KokomiWidgets;

export function setupWidgets() {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => KokomiWidgets.setupObservers());
    } else {
        KokomiWidgets.setupObservers();
    }
}

export { KokomiWidgets };
