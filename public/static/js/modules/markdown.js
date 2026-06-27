/**
 * Markdown Configuration and Renderers
 * Includes KaTeX math rendering for LaTeX expressions
 */

import { renderImage, renderTable, renderCodeWidget, setupWidgets } from './widgets.js';

// ── Math Token Store ─────────────────────────────────────────────────────────
// We extract math before marked runs (to prevent it from mangling LaTeX),
// store them in a temp map, then re-inject them after markdown is parsed.
function extractMath(src) {
    const store = [];
    let i = 0;

    const placeholder = (type, tex) => {
        const id = `\x00MATH_${i++}_${type}\x00`;
        store.push({ id, type, tex });
        return id;
    };

    // 1. Display math: $$...$$ (must come before inline $)
    src = src.replace(/\$\$([\s\S]+?)\$\$/g, (_, tex) => placeholder('block', tex.trim()));

    // 2. Display math: \[...\]
    src = src.replace(/\\\[([\s\S]+?)\\\]/g, (_, tex) => placeholder('block', tex.trim()));

    // 3. Inline math: \(...\)
    src = src.replace(/\\\((.+?)\\\)/g, (_, tex) => placeholder('inline', tex.trim()));

    // 4. Inline math: $...$ — careful not to match $ at start of a price like $10
    src = src.replace(/(?<![\\$])\$(?!\$)([^\n$]+?)\$/g, (_, tex) => placeholder('inline', tex.trim()));

    return { src, store };
}

function renderMathTokens(html, store) {
    if (!store.length || !window.katex) return html;

    for (const { id, type, tex } of store) {
        let rendered;
        try {
            rendered = window.katex.renderToString(tex, {
                displayMode: type === 'block',
                throwOnError: false,
                output: 'html',
                trust: false,
            });
        } catch (e) {
            // Fallback: show raw LaTeX in a styled span
            const escaped = tex.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            rendered = type === 'block'
                ? `<div class="math-error">$$${escaped}$$</div>`
                : `<span class="math-error">$${escaped}$</span>`;
        }

        if (type === 'block') {
            // Unique id so the copy button can find its sibling
            const uid = 'math-' + Math.random().toString(36).substr(2, 8);
            rendered = `<div class="math-block" id="${uid}">${rendered}<button class="math-copy-btn" onclick="window.copyMathAsImage('${uid}')" title="Copy formula as image"><i class="fa-solid fa-copy"></i></button></div>`;
        } else {
            rendered = `<span class="math-inline">${rendered}</span>`;
        }

        html = html.split(id).join(rendered);
    }
    return html;
}

// ── Marked Setup ─────────────────────────────────────────────────────────────
export function setupMarkdown() {
    marked.setOptions({ breaks: true, gfm: true });

    const renderer = new marked.Renderer();
    renderer.code = (obj) => {
        const code = obj.text || '';
        const lang = obj.lang || '';

        // Convention-based widgets (kokomi-video, kokomi-actions, …) take over the
        // fenced block before any syntax highlighting.
        const widget = renderCodeWidget(lang, code);
        if (widget !== null) return widget;

        let hl;
        try {
            hl = lang && hljs.getLanguage(lang)
                ? hljs.highlight(code, { language: lang }).value
                : hljs.highlightAuto(code).value;
        } catch { hl = code; }

        const label = lang
            ? `<span style="display:block;padding:6px 12px 0;font-size:10px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--text-quaternary);font-family:-apple-system,sans-serif">${lang}</span>`
            : '';
        return `<div class="code-wrapper">${label}<button class="code-copy-btn" onclick="window.copyCode(this)">Copy</button><pre><code class="hljs">${hl}</code></pre></div>`;
    };

    // Interactive (sortable + filterable) table widget.
    renderer.table = (obj) => renderTable(obj);

    // Image figure with dimensions + lightbox (also handles inline video URLs).
    renderer.image = (token) => renderImage(token);

    marked.use({ renderer });

    // Start the widget manager (lightbox, table hydration, action dispatch).
    setupWidgets();
}

// ── Exported render function (used in renderMarkdown) ────────────────────────
export function parseWithMath(rawContent) {
    if (!rawContent) return '';

    // 1. Extract math tokens BEFORE marked touches the text
    const { src: safeContent, store } = extractMath(rawContent);

    // 2. Run marked on the math-safe text
    let html;
    try {
        html = marked.parse(safeContent);
    } catch {
        html = safeContent;
    }

    // 3. Re-inject rendered KaTeX HTML
    html = renderMathTokens(html, store);

    return html;
}

export function copyCode(btn) {
    const text = btn.nextElementSibling.querySelector('code').textContent;
    navigator.clipboard.writeText(text).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => (btn.textContent = 'Copy'), 1500);
    });
}

// Make it global for the onclick handler in renderer
window.copyCode = copyCode;

// ── Copy math formula as a PNG image to clipboard ────────────────────────────
window.copyMathAsImage = async function(uid) {
    const block = document.getElementById(uid);
    if (!block) return;

    const btn = block.querySelector('.math-copy-btn');
    const katexEl = block.querySelector('.katex-display') || block.querySelector('.katex');
    if (!katexEl) return;

    // Measure the formula element
    const rect = katexEl.getBoundingClientRect();
    const scale = window.devicePixelRatio || 2;
    const pad = 24; // pixels padding around formula

    const canvas = document.createElement('canvas');
    canvas.width  = (rect.width  + pad * 2) * scale;
    canvas.height = (rect.height + pad * 2) * scale;
    const ctx = canvas.getContext('2d');
    ctx.scale(scale, scale);

    // Fill background matching theme
    const isDark = document.documentElement.classList.contains('dark');
    ctx.fillStyle = isDark ? '#131245' : '#FBFBFD';
    ctx.fillRect(0, 0, canvas.width / scale, canvas.height / scale);

    // Serialize the KaTeX element to SVG-like XML via foreignObject
    const svgNS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(svgNS, 'svg');
    svg.setAttribute('xmlns', svgNS);
    svg.setAttribute('xmlns:xhtml', 'http://www.w3.org/1999/xhtml');
    svg.setAttribute('width', String(rect.width + pad * 2));
    svg.setAttribute('height', String(rect.height + pad * 2));

    // Embed the KaTeX stylesheet so fonts render correctly
    const styleEl = document.querySelector('link[href*="katex"]');
    let cssText = '';
    if (styleEl) {
        // Try to read it; may be cross-origin and fail silently
        try {
            for (const sheet of document.styleSheets) {
                if (sheet.href && sheet.href.includes('katex')) {
                    cssText = [...sheet.cssRules].map(r => r.cssText).join('\n');
                    break;
                }
            }
        } catch { /* cross-origin, skip */ }
    }

    const style = document.createElementNS(svgNS, 'style');
    style.textContent = cssText + `
        body { margin: 0; padding: 0; background: transparent; }
        .katex { font-size: 1.4em; color: ${isDark ? '#EDEDF5' : '#1C1C2E'}; }
    `;

    const fo = document.createElementNS(svgNS, 'foreignObject');
    fo.setAttribute('x', String(pad));
    fo.setAttribute('y', String(pad));
    fo.setAttribute('width', String(rect.width));
    fo.setAttribute('height', String(rect.height));

    const div = document.createElement('div');
    div.setAttribute('xmlns', 'http://www.w3.org/1999/xhtml');
    div.style.cssText = 'display:flex;align-items:center;justify-content:center;width:100%;height:100%;';
    div.appendChild(katexEl.cloneNode(true));

    fo.appendChild(div);
    svg.appendChild(style);
    svg.appendChild(fo);

    const svgStr = new XMLSerializer().serializeToString(svg);
    const blob = new Blob([svgStr], { type: 'image/svg+xml' });
    const url  = URL.createObjectURL(blob);

    const img = new Image();
    img.onload = async () => {
        ctx.drawImage(img, 0, 0);
        URL.revokeObjectURL(url);

        canvas.toBlob(async (pngBlob) => {
            try {
                await navigator.clipboard.write([
                    new ClipboardItem({ 'image/png': pngBlob })
                ]);
                // Visual feedback on the button
                if (btn) {
                    const icon = btn.querySelector('i');
                    if (icon) {
                        icon.className = 'fa-solid fa-check';
                        setTimeout(() => { icon.className = 'fa-solid fa-copy'; }, 1800);
                    }
                }
            } catch (e) {
                console.warn('Clipboard write failed:', e);
                // Fallback: open the PNG in a new tab so the user can save it
                const fallbackUrl = URL.createObjectURL(pngBlob);
                window.open(fallbackUrl, '_blank');
                setTimeout(() => URL.revokeObjectURL(fallbackUrl), 5000);
            }
        }, 'image/png');
    };
    img.src = url;
};
