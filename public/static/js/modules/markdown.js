/**
 * Markdown Configuration and Renderers
 */

export function setupMarkdown() {
    marked.setOptions({ breaks: true, gfm: true });

    const renderer = new marked.Renderer();
    renderer.code = (obj) => {
        const code = obj.text || '';
        const lang = obj.lang || '';
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

    renderer.table = (obj) => {
        const header = obj.header.map(cell => `<th>${marked.parseInline(cell.text)}</th>`).join('');
        const rows = obj.rows.map(row => `<tr>${row.map(cell => `<td>${marked.parseInline(cell.text)}</td>`).join('')}</tr>`).join('');
        return `<div class="table-wrapper"><table><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
    };

    marked.use({ renderer });
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
