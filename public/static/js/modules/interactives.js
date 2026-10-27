/**
 * Kokomi Interactive Components
 * ─────────────────────────────
 * OOP-based inline widgets rendered inside AI message bubbles.
 *
 * AI output syntax:
 *
 *   <CheckboxList title="My List" subtitle="optional note">
 *   ## 🥦 Section
 *   - [ ] Unchecked item
 *   - [x] Pre-checked item
 *   </CheckboxList>
 *
 *   <List title="Features" subtitle="optional">
 *   ## Category
 *   - Item
 *   </List>
 *
 * Class hierarchy:
 *   Interactive (base)
 *     ├── InteractiveList
 *     └── InteractiveCheckboxList
 *
 * Fix: uses event DELEGATION on document so clicks survive Alpine's x-html
 * re-renders — no event listeners are attached per-element during mount().
 */

// ── State ─────────────────────────────────────────────────────────────────────
const _state    = new Map();   // iid → Set<number> of checked indices
const _loaded   = new Set();   // iids whose server state has been fetched
const _saveQ    = new Map();   // iid → debounce timer id
const _animated     = new Set();   // iids that have already played their entrance animation
const _itemsAnimated = new Set();  // iids whose items have been stagger-animated
const _calcDefaults  = new Map();  // iid → { varId: defaultVal } for Calculator reset
const _timerIntervals = new Map(); // iid → intervalId
const _formSubmissions = new Map(); // iid → { fieldId: value }

// ── Helpers ───────────────────────────────────────────────────────────────────

function esc(s) {
    return String(s ?? '')
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function contentHash(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (Math.imul(31, h) + str.charCodeAt(i)) | 0;
    return Math.abs(h).toString(36);
}

function parseAttrs(attrStr) {
    const attrs = {};
    const re = /(\w+)="([^"]*)"/g;
    let m;
    while ((m = re.exec(attrStr)) !== null) attrs[m[1]] = m[2];
    return attrs;
}

function parseInner(raw) {
    const sections = [];
    let cur = { title: null, items: [] };

    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;

        if (t.startsWith('##')) {
            if (cur.items.length || cur.title) sections.push(cur);
            cur = { title: t.replace(/^##\s*/, ''), items: [] };
            continue;
        }
        const checked = t.match(/^-\s+\[x\]\s+(.*)/i);
        if (checked) { cur.items.push({ text: checked[1], checked: true }); continue; }

        const plain = t.match(/^-\s+(?:\[\s\]\s+)?(.*)/);
        if (plain) { cur.items.push({ text: plain[1], checked: false }); continue; }
    }
    if (cur.items.length || cur.title) sections.push(cur);
    if (!sections.length) sections.push({ title: null, items: [] });
    return sections;
}

// ── SVG checkmark ─────────────────────────────────────────────────────────────

const CHECK_SVG = `<svg viewBox="0 0 12 10" fill="none" xmlns="http://www.w3.org/2000/svg" style="width:11px;height:9px;display:block;pointer-events:none"><path d="M1 5l3.5 3.5L11 1" stroke="white" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

// ── HTML renderers ─────────────────────────────────────────────────────────────

function checkboxListHTML(iid, title, subtitle, sections) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const saved = _state.get(iid) || new Set();
    let totalCount = 0, doneCount = 0;

    const bodyHTML = sections.map(sec => {
        const itemsHTML = sec.items.map(item => {
            const idx = totalCount++;
            const isChecked = item.checked || saved.has(idx);
            if (isChecked) doneCount++;

            return `<div class="koki-cl-item${isChecked ? ' is-done' : ''}" data-idx="${idx}" data-iid="${iid}" role="checkbox" aria-checked="${isChecked}" tabindex="0">
                <input type="checkbox" class="koki-cl-cb" data-idx="${idx}"${isChecked ? ' checked' : ''} tabindex="-1" aria-hidden="true">
                <span class="koki-cl-mark">${isChecked ? CHECK_SVG : ''}</span>
                <span class="koki-cl-text">${esc(item.text)}</span>
            </div>`;
        }).join('');

        return sec.title
            ? `<div class="koki-cl-section"><div class="koki-cl-sec-hdr">${esc(sec.title)}</div>${itemsHTML}</div>`
            : `<div class="koki-cl-section">${itemsHTML}</div>`;
    }).join('');

    const pct = totalCount ? ((doneCount / totalCount) * 100).toFixed(1) : 0;

    return `<div class="koki-interactive koki-cl${noAnim}" data-interactive="checklist" data-iid="${iid}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
                ${subtitle ? `<div class="koki-cl-sub">${esc(subtitle)}</div>` : ''}
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter"><span class="koki-cl-done">${doneCount}</span>/<span class="koki-cl-total">${totalCount}</span></div>
                <div class="koki-cl-checked-lbl">checked</div>
            </div>
        </div>
        <div class="koki-cl-bar"><div class="koki-cl-fill" style="width:${pct}%"></div></div>
        <div class="koki-cl-body">${bodyHTML}</div>
    </div>`;
}

function plainListHTML(iid, title, subtitle, sections) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const total = sections.reduce((n, s) => n + s.items.length, 0);
    let idx = 0;

    const bodyHTML = sections.map(sec => {
        const itemsHTML = sec.items.map(item =>
            `<div class="koki-list-item" data-idx="${idx++}">
                <span class="koki-list-bullet"><span class="koki-list-bullet-inner"></span></span>
                <span class="koki-cl-text">${esc(item.text)}</span>
            </div>`
        ).join('');

        return sec.title
            ? `<div class="koki-cl-section"><div class="koki-cl-sec-hdr">${esc(sec.title)}</div>${itemsHTML}</div>`
            : `<div class="koki-cl-section">${itemsHTML}</div>`;
    }).join('');

    return `<div class="koki-interactive koki-list${noAnim}" data-interactive="list" data-iid="${iid}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
                ${subtitle ? `<div class="koki-cl-sub">${esc(subtitle)}</div>` : ''}
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter" style="font-size:17px">${total}</div>
                <div class="koki-cl-checked-lbl">items</div>
            </div>
        </div>
        <div class="koki-cl-body">${bodyHTML}</div>
    </div>`;
}

function shimmerHTML(partialTitle) {
    const title = partialTitle ? esc(partialTitle) : '';
    return `<div class="koki-interactive koki-interactive--shimmer">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                ${title
                    ? `<div class="koki-cl-title">${title}</div><div class="koki-shimmer-line" style="width:110px;height:10px;margin-top:5px"></div>`
                    : `<div class="koki-shimmer-line" style="width:150px;height:15px"></div><div class="koki-shimmer-line" style="width:100px;height:10px;margin-top:5px"></div>`
                }
            </div>
            <div class="koki-shimmer-line" style="width:36px;height:22px;border-radius:8px"></div>
        </div>
        <div class="koki-cl-bar"><div class="koki-cl-fill" style="width:0%"></div></div>
        <div class="koki-cl-body" style="padding:10px 16px 14px">
            <div class="koki-shimmer-line" style="width:88%"></div>
            <div class="koki-shimmer-line" style="width:76%"></div>
            <div class="koki-shimmer-line" style="width:92%"></div>
            <div class="koki-shimmer-line" style="width:68%"></div>
        </div>
    </div>`;
}

// ── Calculator: safe math evaluator ──────────────────────────────────────────
// Supports: +,-,*,/,%, unary-, comparisons (>,<,>=,<=,==,!=), &&, ||, !
// Built-in functions: round, abs, min, max, sqrt, floor, ceil, pow, log, if

function safeEval(expr, vars) {
    const toks = [];
    let i = 0;
    while (i < expr.length) {
        if (/\s/.test(expr[i])) { i++; continue; }
        if (/[\d.]/.test(expr[i])) {
            let n = '';
            while (i < expr.length && /[\d.]/.test(expr[i])) n += expr[i++];
            toks.push({ t: 'num', v: parseFloat(n) }); continue;
        }
        if (/[a-zA-Z_]/.test(expr[i])) {
            let s = '';
            while (i < expr.length && /\w/.test(expr[i])) s += expr[i++];
            toks.push({ t: 'id', v: s }); continue;
        }
        if (i + 1 < expr.length) {
            const two = expr[i] + expr[i + 1];
            if (['>=', '<=', '==', '!=', '&&', '||'].includes(two)) {
                toks.push({ t: 'op', v: two }); i += 2; continue;
            }
        }
        if ('+-*/(),%!><'.includes(expr[i])) { toks.push({ t: 'op', v: expr[i++] }); continue; }
        i++;
    }
    let pos = 0;
    const peek = () => toks[pos];
    const eat  = () => toks[pos++];

    function parseExpr()   { return parseOr(); }
    function parseOr()     { let l = parseAnd();     while (peek()?.v === '||') { eat(); const r = parseAnd();     l = (l || r) ? 1 : 0; } return l; }
    function parseAnd()    { let l = parseCmp();     while (peek()?.v === '&&') { eat(); const r = parseCmp();     l = (l && r) ? 1 : 0; } return l; }
    function parseCmp()    {
        let l = parseAdd();
        const CMP = ['>=', '<=', '==', '!=', '>', '<'];
        while (CMP.includes(peek()?.v)) {
            const op = eat().v; const r = parseAdd();
            if (op === '>') l = l > r ? 1 : 0; else if (op === '<') l = l < r ? 1 : 0;
            else if (op === '>=') l = l >= r ? 1 : 0; else if (op === '<=') l = l <= r ? 1 : 0;
            else if (op === '==') l = l == r ? 1 : 0; else if (op === '!=') l = l != r ? 1 : 0;
        }
        return l;
    }
    function parseAdd()    { let l = parseMul();   while (peek()?.v === '+' || peek()?.v === '-') { const op = eat().v; const r = parseMul();   l = op === '+' ? l + r : l - r; } return l; }
    function parseMul()    { let l = parseUnary(); while (peek()?.v === '*' || peek()?.v === '/' || peek()?.v === '%') { const op = eat().v; const r = parseUnary(); l = op === '*' ? l * r : op === '/' ? (r === 0 ? 0 : l / r) : l % r; } return l; }
    function parseUnary()  {
        if (peek()?.v === '-') { eat(); return -parsePrimary(); }
        if (peek()?.v === '+') { eat(); return parsePrimary(); }
        if (peek()?.v === '!') { eat(); return parsePrimary() ? 0 : 1; }
        return parsePrimary();
    }
    function parsePrimary() {
        const t = peek();
        if (!t) return 0;
        if (t.t === 'num') { eat(); return t.v; }
        if (t.t === 'id') {
            eat();
            if (peek()?.v === '(') {
                eat();
                const args = [];
                while (peek()?.v !== ')' && peek()) { args.push(parseExpr()); if (peek()?.v === ',') eat(); }
                if (peek()?.v === ')') eat();
                const FN = {
                    round: (v, d = 0) => Math.round(v * 10 ** d) / 10 ** d,
                    abs: Math.abs, min: Math.min, max: Math.max,
                    sqrt: Math.sqrt, floor: Math.floor, ceil: Math.ceil,
                    pow: Math.pow,
                    log: (x, b) => b ? Math.log(x) / Math.log(b) : Math.log(x),
                    if: (cond, tv, fv) => cond ? tv : fv,
                };
                return FN[t.v]?.(...args) ?? 0;
            }
            return vars[t.v] ?? 0;
        }
        if (t.v === '(') { eat(); const v = parseExpr(); if (peek()?.v === ')') eat(); return v; }
        return 0;
    }
    try { return parseExpr(); } catch { return 0; }
}

function _fmtNum(v) {
    if (isNaN(v) || !isFinite(v)) return '–';
    if (Math.abs(v - Math.round(v)) < 0.0005 && Math.abs(v) < 1e12)
        return Math.round(v).toLocaleString();
    return parseFloat(v.toFixed(2)).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function _fmtWithUnit(v, unit) {
    const val = _fmtNum(v);
    if (!unit) return val;
    if (unit.includes('{v}')) return unit.replace('{v}', val);
    if (/^[₹$€£¥]/.test(unit)) return unit + val;
    return val + ' ' + unit;
}

// ── Calculator: body parser ───────────────────────────────────────────────────
// Lines without '=' → inputs;  lines with '=' → outputs.
// ## Heading creates a named section.

function parseCalculator(raw) {
    const sections = [];
    let cur = { title: null, items: [] };

    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;

        if (t.startsWith('##')) {
            if (cur.items.length || cur.title !== null) sections.push(cur);
            cur = { title: t.replace(/^##\s*/, '').trim(), items: [] };
            continue;
        }
        if (!t.startsWith('-')) continue;
        const content = t.slice(1).trim();

        // Output: varId = formula | Label | unit
        const outM = content.match(/^(\w+)\s*=\s*([^|]+)\|\s*([^|]*)\|?\s*(.*)/);
        if (outM) {
            cur.items.push({ kind: 'output', id: outM[1].trim(), formula: outM[2].trim(), label: outM[3].trim(), unit: outM[4].trim() });
            continue;
        }

        // Input: varId | Label | default | type-spec
        const parts = content.split('|').map(s => s.trim());
        if (parts.length < 2) continue;
        const [id, label, defRaw = '0', typeStr = 'number'] = parts;
        let inputType = 'number', inputOpts = null, defVal = 0;

        if (typeStr.startsWith('slider:')) {
            const [, mn, mx, st] = typeStr.split(':');
            inputType = 'slider';
            inputOpts = { min: +mn || 0, max: +mx || 100, step: +st || 1 };
            defVal = parseFloat(defRaw) || 0;
        } else if (typeStr.startsWith('select:')) {
            inputType = 'select';
            inputOpts = typeStr.slice(7).split(',').map(o => {
                const eq = o.lastIndexOf('=');
                return eq === -1 ? { label: o.trim(), value: 0 } : { label: o.slice(0, eq).trim(), value: parseFloat(o.slice(eq + 1)) || 0 };
            });
            defVal = parseFloat(defRaw) || inputOpts[0]?.value || 0;
        } else if (typeStr === 'toggle') {
            inputType = 'toggle';
            defVal = parseFloat(defRaw) || 0;
        } else {
            defVal = parseFloat(defRaw) || 0;
        }

        cur.items.push({ kind: 'input', id, label, default: defVal, inputType, inputOpts });
    }
    if (cur.items.length || cur.title !== null) sections.push(cur);
    if (!sections.length) sections.push({ title: null, items: [] });
    return sections;
}

// ── Calculator: input field renderers ─────────────────────────────────────────

function _renderCalcInput(iid, inp) {
    if (inp.inputType === 'slider') {
        const { min, max, step } = inp.inputOpts;
        return `<div class="koki-calc-field koki-calc-field--slider">
            <div class="koki-calc-field-row">
                <label class="koki-calc-label">${esc(inp.label)}</label>
                <span class="koki-calc-sv" data-for="${inp.id}">${inp.default}</span>
            </div>
            <input class="koki-calc-input koki-calc-slider" type="range" min="${min}" max="${max}" step="${step}" value="${inp.default}" data-iid="${iid}" data-var="${inp.id}">
        </div>`;
    }
    if (inp.inputType === 'select') {
        const opts = inp.inputOpts.map(o =>
            `<option value="${o.value}"${o.value === inp.default ? ' selected' : ''}>${esc(o.label)}</option>`
        ).join('');
        return `<div class="koki-calc-field">
            <label class="koki-calc-label">${esc(inp.label)}</label>
            <select class="koki-calc-input koki-calc-select" data-iid="${iid}" data-var="${inp.id}">${opts}</select>
        </div>`;
    }
    if (inp.inputType === 'toggle') {
        const on = inp.default !== 0;
        return `<div class="koki-calc-field koki-calc-field--toggle">
            <label class="koki-calc-label">${esc(inp.label)}</label>
            <div class="koki-calc-toggle${on ? ' is-on' : ''}" role="switch" aria-checked="${on}" tabindex="0" data-iid="${iid}" data-var="${inp.id}" data-state="${on ? 1 : 0}"></div>
        </div>`;
    }
    return `<div class="koki-calc-field">
        <label class="koki-calc-label">${esc(inp.label)}</label>
        <input class="koki-calc-input" type="number" data-iid="${iid}" data-var="${inp.id}" value="${inp.default}">
    </div>`;
}

// ── Calculator: HTML renderer ─────────────────────────────────────────────────

function calculatorHTML(iid, title, subtitle, icon, sections) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);

    // Seed initial vars from all input defaults
    const vars = {};
    for (const sec of sections)
        for (const item of sec.items)
            if (item.kind === 'input') vars[item.id] = item.default;
    _calcDefaults.set(iid, { ...vars });

    const iconCls = icon || 'fa-solid fa-calculator';

    const bodyHTML = sections.map((sec, si) => {
        const inputs  = sec.items.filter(x => x.kind === 'input');
        const outputs = sec.items.filter(x => x.kind === 'output');
        const inputsHTML  = inputs.map(inp => _renderCalcInput(iid, inp)).join('');
        const outputsHTML = outputs.length
            ? `<div class="koki-calc-outputs">${outputs.map(out => {
                const val = safeEval(out.formula, vars);
                return `<div class="koki-calc-row">
                    <span class="koki-calc-row-lbl">${esc(out.label)}</span>
                    <span class="koki-calc-row-val" data-iid="${iid}" data-expr="${esc(out.formula)}" data-unit="${esc(out.unit)}">${_fmtWithUnit(val, out.unit)}</span>
                </div>`;
            }).join('')}</div>` : '';
        return `<div class="koki-calc-sec${si === 0 ? ' koki-calc-sec--first' : ''}">
            ${sec.title ? `<div class="koki-calc-sec-title">${esc(sec.title)}</div>` : ''}
            ${inputsHTML}${outputsHTML}
        </div>`;
    }).join('');

    return `<div class="koki-interactive koki-calc${noAnim}" data-interactive="calculator" data-iid="${iid}">
        <div class="koki-calc-head">
            <div class="koki-calc-icon"><i class="${iconCls}"></i></div>
            <div class="koki-calc-meta">
                <div class="koki-cl-title">${esc(title)}</div>
                ${subtitle ? `<div class="koki-cl-sub">${esc(subtitle)}</div>` : ''}
            </div>
            <div class="koki-calc-live">LIVE</div>
        </div>
        <div class="koki-calc-body">${bodyHTML}</div>
        <div class="koki-calc-footer">
            <button class="koki-calc-reset" data-iid="${iid}"><i class="fa-solid fa-rotate-left"></i> Reset</button>
        </div>
    </div>`;
}

function calculatorShimmerHTML(partialTitle) {
    const title = partialTitle ? esc(partialTitle) : '';
    return `<div class="koki-interactive koki-interactive--shimmer">
        <div class="koki-calc-head">
            <div class="koki-shimmer-line" style="width:36px;height:36px;border-radius:10px;margin-bottom:0;flex-shrink:0"></div>
            <div class="koki-calc-meta">
                ${title
                    ? `<div class="koki-cl-title">${title}</div><div class="koki-shimmer-line" style="width:90px;height:9px;margin-top:5px"></div>`
                    : `<div class="koki-shimmer-line" style="width:130px;height:14px"></div><div class="koki-shimmer-line" style="width:90px;height:9px;margin-top:5px"></div>`
                }
            </div>
            <div class="koki-shimmer-line" style="width:34px;height:18px;border-radius:5px;margin-bottom:0"></div>
        </div>
        <div style="padding:12px 16px 14px;border-top:1px solid var(--border)">
            <div class="koki-shimmer-line" style="width:75px;height:10px;margin-bottom:10px"></div>
            <div class="koki-shimmer-line" style="width:100%;height:34px;border-radius:8px;margin-bottom:8px"></div>
            <div class="koki-shimmer-line" style="width:100%;height:34px;border-radius:8px;margin-bottom:8px"></div>
            <div class="koki-shimmer-line" style="width:100%;height:34px;border-radius:8px"></div>
        </div>
    </div>`;
}

// ── Calculator: live recalculation ────────────────────────────────────────────

function _recalculator(iid) {
    const container = document.querySelector(`[data-interactive="calculator"][data-iid="${iid}"]`);
    if (!container) return;

    const vars = {};
    container.querySelectorAll(`.koki-calc-input[data-iid="${iid}"]`).forEach(inp => {
        vars[inp.dataset.var] = parseFloat(inp.value) || 0;
    });
    container.querySelectorAll(`.koki-calc-toggle[data-iid="${iid}"]`).forEach(tog => {
        vars[tog.dataset.var] = parseInt(tog.dataset.state, 10) || 0;
    });

    // Update slider value labels
    container.querySelectorAll(`.koki-calc-slider[data-iid="${iid}"]`).forEach(sl => {
        const lbl = container.querySelector(`.koki-calc-sv[data-for="${sl.dataset.var}"]`);
        if (lbl) lbl.textContent = sl.value;
    });

    // Recompute all outputs
    container.querySelectorAll(`[data-expr][data-iid="${iid}"]`).forEach(el => {
        el.textContent = _fmtWithUnit(safeEval(el.dataset.expr, vars), el.dataset.unit || '');
    });
}

// ── Timer helpers ─────────────────────────────────────────────────────────────

function _parseDuration(s) {
    if (!s) return 0;
    const str = String(s).trim();
    if (str.includes(':')) {
        const parts = str.split(':');
        if (parts.length === 2) return parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
        if (parts.length === 3) return parseInt(parts[0], 10) * 3600 + parseInt(parts[1], 10) * 60 + parseInt(parts[2], 10);
    }
    return parseInt(str, 10) || 0;
}

function _fmtTime(secs) {
    const s = Math.max(0, secs);
    const m = Math.floor(s / 60);
    const ss = s % 60;
    return `${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function _timerTick(iid) {
    const el = document.querySelector(`[data-interactive="timer"][data-iid="${iid}"]`);
    if (!el) { clearInterval(_timerIntervals.get(iid)); _timerIntervals.delete(iid); return; }
    let rem = parseInt(el.dataset.rem, 10) - 1;
    if (rem < 0) rem = 0;
    el.dataset.rem = rem;
    const total = parseInt(el.dataset.total, 10);
    const timeEl = el.querySelector(`.koki-timer-time[data-iid="${iid}"]`);
    if (timeEl) timeEl.textContent = _fmtTime(rem);
    // Update SVG arc
    const arc = el.querySelector(`.koki-timer-arc[data-iid="${iid}"]`);
    if (arc) {
        const circ = 2 * Math.PI * 44;
        const offset = total > 0 ? circ * (1 - rem / total) : 0;
        arc.style.strokeDashoffset = offset;
    }
    if (rem <= 0) {
        el.dataset.running = '0';
        el.classList.add('koki-timer--done');
        clearInterval(_timerIntervals.get(iid));
        _timerIntervals.delete(iid);
        el.querySelector(`.koki-timer-start[data-iid="${iid}"]`)?.style.removeProperty('display');
        el.querySelector(`.koki-timer-pause[data-iid="${iid}"]`)?.style.setProperty('display', 'none');
    }
}

// ── New component HTML renderers ──────────────────────────────────────────────

function ratingHTML(iid, attrs) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title    = attrs.title    || 'Rating';
    const subtitle = attrs.subtitle || '';
    const max      = parseInt(attrs.max, 10) || 5;
    const type     = attrs.type === 'slider' ? 'slider' : 'stars';

    let bodyHTML;
    if (type === 'stars') {
        const stars = [];
        for (let v = 1; v <= max; v++) {
            stars.push(`<button class="koki-star" data-iid="${iid}" data-val="${v}" title="${v} star${v !== 1 ? 's' : ''}" type="button">
                <i class="fa-regular fa-star"></i>
            </button>`);
        }
        bodyHTML = `<div class="koki-rating-stars">${stars.join('')}</div>`;
    } else {
        bodyHTML = `<div class="koki-calc-field koki-calc-field--slider" style="padding:12px 16px 14px">
            <div class="koki-calc-field-row">
                <label class="koki-calc-label">${esc(title)}</label>
                <span class="koki-calc-sv koki-rating-val">0</span>
            </div>
            <input class="koki-calc-input koki-calc-slider koki-rating-slider" type="range" min="0" max="${max}" step="1" value="0" data-iid="${iid}">
        </div>`;
    }

    return `<div class="koki-interactive koki-rating${noAnim}" data-interactive="rating" data-iid="${iid}" data-val="0" data-max="${max}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
                ${subtitle ? `<div class="koki-cl-sub">${esc(subtitle)}</div>` : ''}
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter"><span class="koki-rating-display">0</span>/${max}</div>
                <div class="koki-cl-checked-lbl">rating</div>
            </div>
        </div>
        <div class="koki-rating-body">${type === 'stars' ? bodyHTML : ''}</div>
        ${type === 'slider' ? bodyHTML : ''}
    </div>`;
}

// ── Quiz parser + renderer ─────────────────────────────────────────────────────

function parseQuiz(raw) {
    const questions = [];
    let cur = null;
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        if (t.startsWith('##')) {
            if (cur) questions.push(cur);
            cur = { question: t.replace(/^##\s*/, ''), options: [] };
            continue;
        }
        if (cur && t.startsWith('-')) {
            const text = t.slice(1).trim();
            const correct = text.endsWith('✓');
            cur.options.push({ text: text.replace(/\s*✓$/, ''), correct });
        }
    }
    if (cur) questions.push(cur);
    return questions;
}

function quizHTML(iid, attrs, questions) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Quiz';

    const qsHTML = questions.map((q, qi) => {
        const optsHTML = q.options.map((o, oi) =>
            `<button class="koki-quiz-opt" data-iid="${iid}" data-qi="${qi}" data-oi="${oi}" data-correct="${o.correct ? 1 : 0}" type="button">
                <span class="koki-quiz-opt-icon"><i class="fa-regular fa-circle"></i></span>
                <span class="koki-quiz-opt-text">${esc(o.text)}</span>
            </button>`
        ).join('');
        return `<div class="koki-quiz-q" data-qi="${qi}">
            <div class="koki-quiz-q-text">${esc(q.question)}</div>
            <div class="koki-quiz-opts">${optsHTML}</div>
        </div>`;
    }).join('');

    return `<div class="koki-interactive koki-quiz${noAnim}" data-interactive="quiz" data-iid="${iid}" data-score="0">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter"><span class="koki-quiz-score">0</span>/${questions.length}</div>
                <div class="koki-cl-checked-lbl">correct</div>
            </div>
        </div>
        <div class="koki-quiz-body">${qsHTML}</div>
    </div>`;
}

// ── Flashcards parser + renderer ───────────────────────────────────────────────

function parseFlashcards(raw) {
    const cards = [];
    let cur = null;
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        if (t.startsWith('##')) {
            if (cur) cards.push(cur);
            cur = { front: t.replace(/^##\s*/, ''), back: '' };
            continue;
        }
        if (cur) cur.back = (cur.back ? cur.back + '\n' : '') + t;
    }
    if (cur) cards.push(cur);
    return cards;
}

function flashcardsHTML(iid, attrs, cards) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Flashcards';
    const total = cards.length;

    const cardsHTML = cards.map((c, i) =>
        `<div class="koki-fc-card${i === 0 ? ' is-active' : ''}" data-idx="${i}" data-flipped="0">
            <div class="koki-fc-inner">
                <div class="koki-fc-front">${esc(c.front)}</div>
                <div class="koki-fc-back">${esc(c.back)}</div>
            </div>
        </div>`
    ).join('');

    return `<div class="koki-interactive koki-flashcards${noAnim}" data-interactive="flashcards" data-iid="${iid}" data-cur="0" data-known="0" data-total="${total}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter"><span class="koki-fc-idx">1</span>/${total}</div>
                <div class="koki-cl-checked-lbl">card</div>
            </div>
        </div>
        <div class="koki-fc-prog"><div class="koki-fc-prog-fill" style="width:${total > 0 ? (1/total*100).toFixed(1) : 0}%"></div></div>
        <div class="koki-fc-stage" data-iid="${iid}">${cardsHTML}</div>
        <div class="koki-fc-nav">
            <button class="koki-fc-btn" data-action="prev" data-iid="${iid}" type="button"><i class="fa-solid fa-arrow-left"></i> Prev</button>
            <div class="koki-fc-known-wrap"><i class="fa-solid fa-check"></i> Known: <span class="koki-fc-known">0</span></div>
            <button class="koki-fc-btn" data-action="know" data-iid="${iid}" type="button"><i class="fa-solid fa-thumbs-up"></i> Know it</button>
            <button class="koki-fc-btn" data-action="next" data-iid="${iid}" type="button">Next <i class="fa-solid fa-arrow-right"></i></button>
        </div>
    </div>`;
}

// ── Stepper parser + renderer ──────────────────────────────────────────────────

function parseStepper(raw) {
    const steps = [];
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        if (t.startsWith('-')) {
            const content = t.slice(1).trim();
            const colon = content.indexOf(':');
            if (colon !== -1) {
                steps.push({ title: content.slice(0, colon).trim(), desc: content.slice(colon + 1).trim() });
            } else {
                steps.push({ title: content, desc: '' });
            }
        }
    }
    return steps;
}

function stepperHTML(iid, attrs, steps) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Steps';
    const total = steps.length;

    const stepsHTML = steps.map((s, i) => {
        const isCurrent = i === 0;
        const isLast = i === total - 1;
        return `<div class="koki-step${isCurrent ? ' is-current' : ''}" data-idx="${i}">
            <div class="koki-step-aside">
                <div class="koki-step-dot"></div>
                ${!isLast ? '<div class="koki-step-line"></div>' : ''}
            </div>
            <div class="koki-step-body">
                <div class="koki-step-title">${esc(s.title)}</div>
                ${s.desc ? `<div class="koki-step-desc">${esc(s.desc)}</div>` : ''}
                <button class="koki-step-btn${!isCurrent ? ' koki-step-btn--hidden' : ''}" data-iid="${iid}" data-idx="${i}" type="button">
                    <i class="fa-solid fa-check"></i> Mark done
                </button>
            </div>
        </div>`;
    }).join('');

    return `<div class="koki-interactive koki-stepper${noAnim}" data-interactive="stepper" data-iid="${iid}" data-cur="0" data-total="${total}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter"><span class="koki-step-done">0</span>/${total}</div>
                <div class="koki-cl-checked-lbl">done</div>
            </div>
        </div>
        <div class="koki-stepper-body">${stepsHTML}</div>
    </div>`;
}

// ── Kanban parser + renderer ───────────────────────────────────────────────────

function parseKanban(raw) {
    const cols = [];
    let cur = null;
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        if (t.startsWith('##')) {
            if (cur) cols.push(cur);
            cur = { title: t.replace(/^##\s*/, ''), items: [] };
            continue;
        }
        if (cur && t.startsWith('-')) {
            cur.items.push(t.slice(1).trim());
        }
    }
    if (cur) cols.push(cur);
    return cols;
}

function kanbanHTML(iid, attrs, cols) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Kanban';
    const ncols = cols.length;

    const colsHTML = cols.map((col, ci) => {
        const cardsHTML = col.items.map((item, itemIdx) => {
            const kid = `${ci}_${itemIdx}`;
            return `<div class="koki-kb-card" data-iid="${iid}" data-kid="${kid}" data-col="${ci}">
                <span class="koki-kb-card-text">${esc(item)}</span>
                <div class="koki-kb-actions">
                    ${ci > 0 ? `<button class="koki-kb-mv" data-iid="${iid}" data-kid="${kid}" data-dir="-1" title="← Move left" type="button"><i class="fa-solid fa-arrow-left"></i></button>` : ''}
                    ${ci < ncols - 1 ? `<button class="koki-kb-mv" data-iid="${iid}" data-kid="${kid}" data-dir="1" title="→ Move right" type="button"><i class="fa-solid fa-arrow-right"></i></button>` : ''}
                </div>
            </div>`;
        }).join('');
        return `<div class="koki-kb-col" data-col="${ci}">
            <div class="koki-kb-col-hdr">
                <span class="koki-kb-col-title">${esc(col.title)}</span>
                <span class="koki-kb-cnt">${col.items.length}</span>
            </div>
            <div class="koki-kb-items" data-col="${ci}" data-iid="${iid}">${cardsHTML}</div>
        </div>`;
    }).join('');

    return `<div class="koki-interactive koki-kanban${noAnim}" data-interactive="kanban" data-iid="${iid}" data-ncols="${ncols}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
        </div>
        <div class="koki-kb-board">${colsHTML}</div>
    </div>`;
}

// ── Form parser + renderer ─────────────────────────────────────────────────────

function parseForm(raw) {
    const fields = [];
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t || !t.startsWith('-')) continue;
        const parts = t.slice(1).trim().split('|').map(s => s.trim());
        if (parts.length < 2) continue;
        const [id, label, type = 'text', placeholder = ''] = parts;
        fields.push({ id, label, type, placeholder });
    }
    return fields;
}

function formHTML(iid, attrs, fields) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title  = attrs.title  || 'Form';
    const submit = attrs.submit || 'Submit';

    const fieldsHTML = fields.map(f => {
        let input;
        if (f.type === 'textarea') {
            input = `<textarea class="koki-calc-input koki-form-ta" data-fid="${esc(f.id)}" placeholder="${esc(f.placeholder)}"></textarea>`;
        } else if (f.type.startsWith('select:')) {
            const opts = f.type.slice(7).split(',').map(o =>
                `<option value="${esc(o.trim())}">${esc(o.trim())}</option>`
            ).join('');
            input = `<select class="koki-calc-input" data-fid="${esc(f.id)}"><option value="">-- select --</option>${opts}</select>`;
        } else {
            const t = ['text','email','number'].includes(f.type) ? f.type : 'text';
            input = `<input class="koki-calc-input" type="${t}" data-fid="${esc(f.id)}" placeholder="${esc(f.placeholder)}">`;
        }
        return `<div class="koki-form-field">
            <label class="koki-calc-label">${esc(f.label)}</label>
            ${input}
        </div>`;
    }).join('');

    return `<div class="koki-interactive koki-form${noAnim}" data-interactive="form" data-iid="${iid}" data-submitted="0">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
        </div>
        <div class="koki-form-body">${fieldsHTML}</div>
        <div class="koki-form-footer">
            <button class="koki-form-submit" data-iid="${iid}" type="button">${esc(submit)}</button>
        </div>
        <div class="koki-form-done" style="display:none">
            <i class="fa-solid fa-circle-check"></i>
            <span>Submitted! Thank you.</span>
        </div>
    </div>`;
}

// ── Timeline parser + renderer ─────────────────────────────────────────────────

function parseTimeline(raw) {
    const items = [];
    let cur = null;
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t) continue;
        if (t.startsWith('##')) {
            if (cur) items.push(cur);
            const rest = t.replace(/^##\s*/, '');
            const pipeIdx = rest.indexOf('|');
            let label, current = false;
            if (pipeIdx !== -1) {
                label = rest.slice(0, pipeIdx).trim();
                current = rest.slice(pipeIdx + 1).trim().toLowerCase() === 'current';
            } else {
                label = rest;
            }
            cur = { label, current, desc: '' };
            continue;
        }
        if (cur) cur.desc = (cur.desc ? cur.desc + '\n' : '') + t;
    }
    if (cur) items.push(cur);
    return items;
}

function timelineHTML(iid, attrs, items) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Timeline';

    const itemsHTML = items.map((item, i) => {
        const isLast = i === items.length - 1;
        return `<div class="koki-tl-item${item.current ? ' is-current' : ''}">
            <div class="koki-tl-aside">
                <div class="koki-tl-dot"></div>
                ${!isLast ? '<div class="koki-tl-line"></div>' : ''}
            </div>
            <div class="koki-tl-content">
                <div class="koki-tl-label">${esc(item.label)}${item.current ? ' <span class="koki-tl-now">NOW</span>' : ''}</div>
                ${item.desc ? `<div class="koki-tl-desc">${esc(item.desc.trim())}</div>` : ''}
            </div>
        </div>`;
    }).join('');

    return `<div class="koki-interactive koki-timeline${noAnim}" data-interactive="timeline" data-iid="${iid}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
        </div>
        <div class="koki-tl-body">${itemsHTML}</div>
    </div>`;
}

// ── Meter renderer (no body parser) ───────────────────────────────────────────

function meterHTML(iid, attrs) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Meter';
    const value = parseFloat(attrs.value) || 0;
    const max   = parseFloat(attrs.max)   || 100;
    const unit  = attrs.unit  || '';
    const label = attrs.label || '';
    const pct   = max > 0 ? Math.min(100, (value / max) * 100) : 0;
    const colorCls = pct > 80 ? 'koki-meter--danger' : pct > 55 ? 'koki-meter--warn' : 'koki-meter--ok';

    const display = unit ? (unit.includes('{v}') ? unit.replace('{v}', value) : `${value}${unit}`) : String(value);

    return `<div class="koki-interactive koki-meter ${colorCls}${noAnim}" data-interactive="meter" data-iid="${iid}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
                ${label ? `<div class="koki-cl-sub">${esc(label)}</div>` : ''}
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter" style="font-size:20px">${esc(display)}</div>
                ${unit ? `<div class="koki-cl-checked-lbl">${esc(unit.replace('{v}','').trim())}</div>` : ''}
            </div>
        </div>
        <div class="koki-cl-bar"><div class="koki-cl-fill" style="width:${pct.toFixed(1)}%"></div></div>
    </div>`;
}

// ── Timer renderer (no body parser) ───────────────────────────────────────────

function timerHTML(iid, attrs) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title    = attrs.title    || 'Timer';
    const label    = attrs.label    || '';
    const total    = _parseDuration(attrs.duration || '25:00');
    const circ     = 2 * Math.PI * 44;

    return `<div class="koki-interactive koki-timer${noAnim}" data-interactive="timer" data-iid="${iid}" data-total="${total}" data-rem="${total}" data-running="0">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
                ${label ? `<div class="koki-cl-sub">${esc(label)}</div>` : ''}
            </div>
        </div>
        <div class="koki-timer-body">
            <div class="koki-timer-ring-wrap">
                <svg class="koki-timer-ring" width="108" height="108" viewBox="0 0 108 108">
                    <circle class="koki-timer-track" cx="54" cy="54" r="44" fill="none" stroke-width="6"/>
                    <circle class="koki-timer-arc" data-iid="${iid}" cx="54" cy="54" r="44" fill="none" stroke-width="6"
                        stroke-dasharray="${circ.toFixed(2)}"
                        stroke-dashoffset="0"
                        transform="rotate(-90 54 54)"/>
                </svg>
                <div class="koki-timer-disp">
                    <div class="koki-timer-time" data-iid="${iid}">${_fmtTime(total)}</div>
                </div>
            </div>
            <div class="koki-timer-ctrl">
                <button class="koki-timer-btn koki-timer-start" data-iid="${iid}" type="button"><i class="fa-solid fa-play"></i> Start</button>
                <button class="koki-timer-btn koki-timer-pause" data-iid="${iid}" type="button" style="display:none"><i class="fa-solid fa-pause"></i> Pause</button>
                <button class="koki-timer-btn koki-timer-reset" data-iid="${iid}" type="button"><i class="fa-solid fa-rotate-left"></i> Reset</button>
            </div>
        </div>
    </div>`;
}

// ── Palette parser + renderer ─────────────────────────────────────────────────

function parsePalette(raw) {
    const swatches = [];
    for (const line of raw.split('\n')) {
        const t = line.trim();
        if (!t || !t.startsWith('-')) continue;
        const content = t.slice(1).trim();
        const pipeIdx = content.indexOf('|');
        if (pipeIdx !== -1) {
            swatches.push({ hex: content.slice(0, pipeIdx).trim(), name: content.slice(pipeIdx + 1).trim() });
        } else {
            swatches.push({ hex: content, name: '' });
        }
    }
    return swatches;
}

function paletteHTML(iid, attrs, swatches) {
    const noAnim = _animated.has(iid) ? ' koki-no-anim' : '';
    _animated.add(iid);
    const title = attrs.title || 'Color Palette';

    const swatchesHTML = swatches.map(s =>
        `<div class="koki-swatch" data-iid="${iid}" data-hex="${esc(s.hex)}" title="Click to copy ${esc(s.hex)}" tabindex="0" role="button">
            <div class="koki-swatch-hex">${esc(s.hex)}</div>
            ${s.name ? `<div class="koki-swatch-name">${esc(s.name)}</div>` : ''}
            <div class="koki-swatch-color" style="background:${esc(s.hex)}"></div>
        </div>`
    ).join('');

    return `<div class="koki-interactive koki-palette${noAnim}" data-interactive="palette" data-iid="${iid}">
        <div class="koki-cl-head">
            <div class="koki-cl-meta">
                <div class="koki-cl-title">${esc(title)}</div>
            </div>
            <div class="koki-cl-stats">
                <div class="koki-cl-counter" style="font-size:17px">${swatches.length}</div>
                <div class="koki-cl-checked-lbl">colors</div>
            </div>
        </div>
        <div class="koki-palette-grid">${swatchesHTML}</div>
        <div class="koki-palette-toast" data-iid="${iid}"></div>
    </div>`;
}

// ── Tag extraction (run BEFORE marked.parse) ──────────────────────────────────

export function extractInteractives(src) {
    const store = new Map();
    let i = 0;

    // Extract complete <CheckboxList> / <List> tags
    let out = src.replace(/<(CheckboxList|List)((?:\s[^>]*)?)>([\s\S]*?)<\/\1>/gi,
        (_, tag, attrStr, inner) => {
            const attrs = parseAttrs(attrStr);
            const title    = attrs.title    || (tag === 'CheckboxList' ? 'Checklist' : 'List');
            const subtitle = attrs.subtitle || '';
            const sections = parseInner(inner);
            const iid = contentHash(title + subtitle + inner.trim());

            const html = tag.toLowerCase() === 'checkboxlist'
                ? checkboxListHTML(iid, title, subtitle, sections)
                : plainListHTML(iid, title, subtitle, sections);

            const pid = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Calculator> tags
    out = out.replace(/<Calculator((?:\s[^>]*)?)>([\s\S]*?)<\/Calculator>/gi,
        (_, attrStr, inner) => {
            const attrs    = parseAttrs(attrStr);
            const title    = attrs.title    || 'Calculator';
            const subtitle = attrs.subtitle || '';
            const icon     = attrs.icon     || '';
            const sections = parseCalculator(inner);
            const iid      = contentHash(title + subtitle + inner.trim());
            const html     = calculatorHTML(iid, title, subtitle, icon, sections);
            const pid      = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Rating> tags
    out = out.replace(/<Rating((?:\s[^>]*)?)(?:\/>|>[\s\S]*?<\/Rating>)/gi,
        (_, attrStr) => {
            const attrs = parseAttrs(attrStr);
            const iid   = contentHash((attrs.title || 'Rating') + (attrs.type || '') + (attrs.max || '5') + (attrs.subtitle || ''));
            const html  = ratingHTML(iid, attrs);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Quiz> tags
    out = out.replace(/<Quiz((?:\s[^>]*)?)>([\s\S]*?)<\/Quiz>/gi,
        (_, attrStr, inner) => {
            const attrs     = parseAttrs(attrStr);
            const questions = parseQuiz(inner);
            const iid       = contentHash((attrs.title || 'Quiz') + inner.trim());
            const html      = quizHTML(iid, attrs, questions);
            const pid       = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Flashcards> tags
    out = out.replace(/<Flashcards((?:\s[^>]*)?)>([\s\S]*?)<\/Flashcards>/gi,
        (_, attrStr, inner) => {
            const attrs = parseAttrs(attrStr);
            const cards = parseFlashcards(inner);
            const iid   = contentHash((attrs.title || 'Flashcards') + inner.trim());
            const html  = flashcardsHTML(iid, attrs, cards);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Stepper> tags
    out = out.replace(/<Stepper((?:\s[^>]*)?)>([\s\S]*?)<\/Stepper>/gi,
        (_, attrStr, inner) => {
            const attrs = parseAttrs(attrStr);
            const steps = parseStepper(inner);
            const iid   = contentHash((attrs.title || 'Stepper') + inner.trim());
            const html  = stepperHTML(iid, attrs, steps);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Kanban> tags
    out = out.replace(/<Kanban((?:\s[^>]*)?)>([\s\S]*?)<\/Kanban>/gi,
        (_, attrStr, inner) => {
            const attrs = parseAttrs(attrStr);
            const cols  = parseKanban(inner);
            const iid   = contentHash((attrs.title || 'Kanban') + inner.trim());
            const html  = kanbanHTML(iid, attrs, cols);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Form> tags
    out = out.replace(/<Form((?:\s[^>]*)?)>([\s\S]*?)<\/Form>/gi,
        (_, attrStr, inner) => {
            const attrs  = parseAttrs(attrStr);
            const fields = parseForm(inner);
            const iid    = contentHash((attrs.title || 'Form') + inner.trim());
            const html   = formHTML(iid, attrs, fields);
            const pid    = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Timeline> tags
    out = out.replace(/<Timeline((?:\s[^>]*)?)>([\s\S]*?)<\/Timeline>/gi,
        (_, attrStr, inner) => {
            const attrs = parseAttrs(attrStr);
            const items = parseTimeline(inner);
            const iid   = contentHash((attrs.title || 'Timeline') + inner.trim());
            const html  = timelineHTML(iid, attrs, items);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Meter> tags (self-closing or empty body)
    out = out.replace(/<Meter((?:\s[^>]*)?)(?:\/>|>([\s\S]*?)<\/Meter>)/gi,
        (_, attrStr) => {
            const attrs = parseAttrs(attrStr);
            const iid   = contentHash((attrs.title || 'Meter') + (attrs.value || '') + (attrs.max || '') + (attrs.unit || ''));
            const html  = meterHTML(iid, attrs);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Timer> tags (self-closing, empty, or any body content)
    out = out.replace(/<Timer((?:\s[^>]*)?)(?:\/>|>([\s\S]*?)<\/Timer>)/gi,
        (_, attrStr) => {
            const attrs = parseAttrs(attrStr);
            const iid   = contentHash((attrs.title || 'Timer') + (attrs.duration || '') + (attrs.label || ''));
            const html  = timerHTML(iid, attrs);
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Extract complete <Palette> tags
    out = out.replace(/<Palette((?:\s[^>]*)?)>([\s\S]*?)<\/Palette>/gi,
        (_, attrStr, inner) => {
            const attrs    = parseAttrs(attrStr);
            const swatches = parsePalette(inner);
            const iid      = contentHash((attrs.title || 'Palette') + inner.trim());
            const html     = paletteHTML(iid, attrs, swatches);
            const pid      = `\x00IATV_${i++}\x00`;
            store.set(pid, html);
            return pid;
        }
    );

    // Render dangling self-contained opening tags — AI often omits closing tag for
    // Timer/Meter/Rating. Complete-tag regexes above already consumed any properly
    // closed ones; only dangling `<Tag ...>` (no closing) can reach here.
    out = out.replace(/<Timer((?:\s[^>]*)?)>/gi, (_, attrStr) => {
        const attrs = parseAttrs(attrStr);
        const iid   = contentHash((attrs.title || 'Timer') + (attrs.duration || '') + (attrs.label || ''));
        const pid   = `\x00IATV_${i++}\x00`;
        store.set(pid, timerHTML(iid, attrs));
        return pid;
    });
    out = out.replace(/<Meter((?:\s[^>]*)?)>/gi, (_, attrStr) => {
        const attrs = parseAttrs(attrStr);
        const iid   = contentHash((attrs.title || 'Meter') + (attrs.value || '') + (attrs.max || '') + (attrs.unit || ''));
        const pid   = `\x00IATV_${i++}\x00`;
        store.set(pid, meterHTML(iid, attrs));
        return pid;
    });
    out = out.replace(/<Rating((?:\s[^>]*)?)>/gi, (_, attrStr) => {
        const attrs = parseAttrs(attrStr);
        const iid   = contentHash((attrs.title || 'Rating') + (attrs.type || '') + (attrs.max || '5') + (attrs.subtitle || ''));
        const pid   = `\x00IATV_${i++}\x00`;
        store.set(pid, ratingHTML(iid, attrs));
        return pid;
    });

    // Detect incomplete / still-streaming tags → show shimmer
    const ALL_TAGS = 'CheckboxList|List|Calculator|Rating|Quiz|Flashcards|Stepper|Kanban|Form|Timeline|Meter|Timer|Palette';
    const partialAny = out.match(new RegExp(`<(${ALL_TAGS})\\b([^>]*)>([\\s\\S]*)$`, 'i'));
    if (partialAny) {
        const tag   = partialAny[1].toLowerCase();
        const attrs = parseAttrs(partialAny[2] || '');
        const pid   = `\x00IATV_${i++}\x00`;
        const html  = (tag === 'calculator') ? calculatorShimmerHTML(attrs.title || '') : shimmerHTML(attrs.title || '');
        store.set(pid, html);
        out = out.replace(partialAny[0], pid);
    } else {
        // Also catch self-closing partials (<Meter ...) that haven't closed yet
        const partialSelfClose = out.match(new RegExp(`<(Meter|Timer|Rating)\\b([^>]*)$`, 'i'));
        if (partialSelfClose) {
            const attrs = parseAttrs(partialSelfClose[2] || '');
            const pid   = `\x00IATV_${i++}\x00`;
            store.set(pid, shimmerHTML(attrs.title || ''));
            out = out.replace(partialSelfClose[0], pid);
        }
    }

    return { src: out, store };
}

export function reinjectInteractives(html, store) {
    for (const [pid, rendered] of store) {
        html = html.split(pid).join(rendered);
    }
    return html;
}

// ── UI helpers (used by delegation handler) ───────────────────────────────────

function applyItemVisual(itemEl, isChecked, animate = true) {
    const mark = itemEl.querySelector('.koki-cl-mark');
    if (!mark) return;

    itemEl.classList.toggle('is-done', isChecked);
    itemEl.setAttribute('aria-checked', String(isChecked));

    if (isChecked) {
        mark.innerHTML = CHECK_SVG;
    } else {
        mark.innerHTML = '';
    }

    if (animate) {
        mark.classList.remove('koki-mark-spring');
        // Force reflow so re-adding the class triggers the animation
        void mark.offsetWidth;
        mark.classList.add('koki-mark-spring');
    }
}

function updateProgress(containerEl) {
    const boxes = [...containerEl.querySelectorAll('.koki-cl-cb')];
    const done  = boxes.filter(c => c.checked).length;
    const total = boxes.length;

    const doneEl  = containerEl.querySelector('.koki-cl-done');
    const totalEl = containerEl.querySelector('.koki-cl-total');
    const fillEl  = containerEl.querySelector('.koki-cl-fill');

    if (doneEl)  doneEl.textContent  = done;
    if (totalEl) totalEl.textContent = total;
    if (fillEl)  fillEl.style.width  = total ? `${(done / total) * 100}%` : '0%';
}

// ── Server persistence ────────────────────────────────────────────────────────

async function fetchServerState(iid) {
    try {
        const r = await fetch(`/api/interactives/${iid}`);
        if (!r.ok) return null;
        const d = await r.json();
        return Array.isArray(d.checked) ? new Set(d.checked) : null;
    } catch { return null; }
}

// checklist call: scheduleSave(iid)  — reads from _state
// other components: scheduleSave(iid, [array])  — explicit payload
function scheduleSave(iid, explicit) {
    clearTimeout(_saveQ.get(iid));
    _saveQ.set(iid, setTimeout(async () => {
        const checked = explicit !== undefined ? explicit : [...(_state.get(iid) || new Set())];
        try {
            await fetch(`/api/interactives/${iid}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ checked }),
            });
        } catch { /* best-effort */ }
    }, 800));
}

// ── Entrance animation helpers ────────────────────────────────────────────────

function _staggerItems(containerEl, selector) {
    const items = containerEl.querySelectorAll(selector);
    items.forEach((item, i) => {
        item.style.animationDelay = `${i * 30}ms`;
        item.classList.add('koki-item-enter');
    });
}

function _animateProgressBar(containerEl) {
    const fill = containerEl.querySelector('.koki-cl-fill');
    if (!fill) return;
    const target = fill.style.width || '0%';
    fill.style.transition = 'none';
    fill.style.width = '0%';
    // Let the browser paint 0%, then animate to the real value
    requestAnimationFrame(() => requestAnimationFrame(() => {
        fill.style.transition = 'width 0.7s cubic-bezier(0.25, 1, 0.5, 1)';
        fill.style.width = target;
    }));
}

// ── OOP class hierarchy ───────────────────────────────────────────────────────

class Interactive {
    constructor(el) {
        this.el  = el;
        this.iid = el.dataset.iid;
    }
    mount() {}
    sendToAI(text) {
        document.dispatchEvent(new CustomEvent('kokomi:interactive-send', { detail: { text } }));
    }
}

class InteractiveList extends Interactive {
    mount() {
        if (!_itemsAnimated.has(this.iid)) {
            _itemsAnimated.add(this.iid);
            _staggerItems(this.el, '.koki-list-item');
        }
    }
}

class InteractiveCheckboxList extends Interactive {
    mount() {
        // Stagger items + animate progress bar — only on first appearance
        if (!_itemsAnimated.has(this.iid)) {
            _itemsAnimated.add(this.iid);
            _staggerItems(this.el, '.koki-cl-item');
            _animateProgressBar(this.el);
        }

        // Restore in-memory state immediately (no flash)
        if (_state.has(this.iid)) {
            this._applyState(_state.get(this.iid));
        }
        // Then sync from server (overwrites local if found)
        if (!_loaded.has(this.iid)) {
            _loaded.add(this.iid);
            fetchServerState(this.iid).then(serverSet => {
                if (!serverSet) return;
                _state.set(this.iid, serverSet);
                // Re-find element in case it was re-rendered while fetch was in-flight
                const el = document.querySelector(`[data-interactive="checklist"][data-iid="${this.iid}"]`);
                if (el) this._applyStateTo(el, serverSet);
            });
        }
    }

    _applyState(stateSet) {
        this._applyStateTo(this.el, stateSet);
    }

    _applyStateTo(el, stateSet) {
        el.querySelectorAll('.koki-cl-item').forEach(item => {
            const idx       = parseInt(item.dataset.idx, 10);
            const isChecked = stateSet.has(idx);
            const cb        = item.querySelector('.koki-cl-cb');
            if (cb) cb.checked = isChecked;
            applyItemVisual(item, isChecked, false);
        });
        updateProgress(el);
    }
}

class InteractiveCalculator extends Interactive {
    mount() {
        _recalculator(this.iid);
    }
}

class InteractiveRating extends Interactive {
    mount() {
        if (_loaded.has(this.iid)) return;
        _loaded.add(this.iid);
        fetchServerState(this.iid).then(saved => {
            if (!saved || !saved.size) return;
            const val = [...saved][0];
            if (!val) return;
            const el = document.querySelector(`[data-interactive="rating"][data-iid="${this.iid}"]`);
            if (!el) return;
            el.dataset.val = val;
            el.querySelectorAll('.koki-star').forEach((s, i) => {
                const icon = s.querySelector('i');
                if (icon) icon.className = i < val ? 'fa-solid fa-star' : 'fa-regular fa-star';
                s.classList.toggle('is-filled', i < val);
            });
            const sliderVal = el.querySelector('.koki-rating-val');
            if (sliderVal) sliderVal.textContent = val;
            const slider = el.querySelector('.koki-rating-slider');
            if (slider) slider.value = val;
        });
    }
}

class InteractiveQuiz extends Interactive {
    mount() {
        if (_loaded.has(this.iid)) return;
        _loaded.add(this.iid);
        fetchServerState(this.iid).then(saved => {
            if (!saved || !saved.size) return;
            const el = document.querySelector(`[data-interactive="quiz"][data-iid="${this.iid}"]`);
            if (!el) return;
            let score = 0;
            saved.forEach(encoded => {
                const qi = Math.floor(encoded / 1000);
                const oi = encoded % 1000;
                const qDiv = el.querySelector(`.koki-quiz-q[data-qi="${qi}"]`);
                if (!qDiv || qDiv.dataset.answered === '1') return;
                const opt = qDiv.querySelector(`.koki-quiz-opt[data-qi="${qi}"][data-oi="${oi}"]`);
                if (!opt) return;
                qDiv.dataset.answered = '1';
                const correct = opt.dataset.correct === '1';
                opt.classList.add(correct ? 'is-correct' : 'is-wrong');
                if (!correct) qDiv.querySelectorAll('.koki-quiz-opt[data-correct="1"]').forEach(o => o.classList.add('is-correct'));
                qDiv.querySelectorAll('.koki-quiz-opt').forEach(o => { o.disabled = true; });
                if (correct) score++;
            });
            if (score > 0) {
                el.dataset.score = score;
                const scoreEl = el.querySelector('.koki-quiz-score');
                if (scoreEl) scoreEl.textContent = score;
            }
        });
    }
}

class InteractiveFlashcards extends Interactive {
    mount() {
        if (_loaded.has(this.iid)) return;
        _loaded.add(this.iid);
        fetchServerState(this.iid).then(saved => {
            if (!saved || !saved.size) return;
            const el = document.querySelector(`[data-interactive="flashcards"][data-iid="${this.iid}"]`);
            if (!el) return;
            saved.forEach(idx => el.querySelector(`.koki-fc-card[data-idx="${idx}"]`)?.classList.add('is-known'));
            const known = saved.size;
            el.dataset.known = known;
            const knownEl = el.querySelector('.koki-fc-known');
            if (knownEl) knownEl.textContent = known;
        });
    }
}

class InteractiveStepper extends Interactive {
    mount() {
        if (_loaded.has(this.iid)) return;
        _loaded.add(this.iid);
        fetchServerState(this.iid).then(saved => {
            if (!saved || !saved.size) return;
            const cur = [...saved][0];
            if (!cur) return;
            const el = document.querySelector(`[data-interactive="stepper"][data-iid="${this.iid}"]`);
            if (!el) return;
            const steps = el.querySelectorAll('.koki-step');
            steps.forEach((step, i) => {
                if (i < cur) {
                    step.classList.remove('is-current');
                    step.classList.add('is-done');
                    step.querySelector('.koki-step-btn')?.classList.add('koki-step-btn--hidden');
                } else if (i === cur) {
                    step.classList.add('is-current');
                    step.querySelector('.koki-step-btn')?.classList.remove('koki-step-btn--hidden');
                }
            });
            el.dataset.cur = cur;
            const doneEl = el.querySelector('.koki-step-done');
            if (doneEl) doneEl.textContent = cur;
        });
    }
}
class InteractiveKanban extends Interactive { mount() {} }
class InteractiveForm extends Interactive { mount() {} }
class InteractiveTimeline extends Interactive { mount() {} }
class InteractiveMeter extends Interactive {
    mount() {
        if (!_itemsAnimated.has(this.iid)) {
            _itemsAnimated.add(this.iid);
            _animateProgressBar(this.el);
        }
    }
}
class InteractiveTimer extends Interactive {
    mount() {
        if (this.el.dataset.running === '1' && !_timerIntervals.has(this.iid)) {
            _timerIntervals.set(this.iid, setInterval(() => _timerTick(this.iid), 1000));
        }
    }
}
class InteractivePalette extends Interactive { mount() {} }

// ── Hydration manager ─────────────────────────────────────────────────────────

const REGISTRY = {
    checklist:   InteractiveCheckboxList,
    list:        InteractiveList,
    calculator:  InteractiveCalculator,
    rating:      InteractiveRating,
    quiz:        InteractiveQuiz,
    flashcards:  InteractiveFlashcards,
    stepper:     InteractiveStepper,
    kanban:      InteractiveKanban,
    form:        InteractiveForm,
    timeline:    InteractiveTimeline,
    meter:       InteractiveMeter,
    timer:       InteractiveTimer,
    palette:     InteractivePalette,
};

function hydrate(root = document) {
    root.querySelectorAll('[data-interactive]:not([data-hydrated])').forEach(el => {
        el.dataset.hydrated = '1';
        const Cls = REGISTRY[el.dataset.interactive];
        if (Cls) new Cls(el).mount();
    });
}

// ── Setup (call once at app boot) ─────────────────────────────────────────────

export function setupInteractives() {
    // Hydrate existing + new nodes (survives Alpine x-html re-renders)
    new MutationObserver(() => hydrate()).observe(document.body, { childList: true, subtree: true });
    hydrate();

    // ── Calculator: delegated input (number / slider / select) ──
    document.addEventListener('input', e => {
        const inp = e.target.closest('.koki-calc-input[data-iid]');
        if (inp) { _recalculator(inp.dataset.iid); return; }

        // Rating slider
        const rsl = e.target.closest('.koki-rating-slider[data-iid]');
        if (rsl) {
            const iid = rsl.dataset.iid;
            const val = parseInt(rsl.value, 10);
            const container = document.querySelector(`[data-interactive="rating"][data-iid="${iid}"]`);
            if (container) {
                container.dataset.val = val;
                const display = container.querySelector('.koki-rating-display');
                if (display) display.textContent = val;
                const valEl = container.querySelector('.koki-rating-val');
                if (valEl) valEl.textContent = val;
            }
        }
    });

    // ── Delegated click handler — immune to DOM re-renders ──
    document.addEventListener('click', e => {
        // Calculator toggle
        const tog = e.target.closest('.koki-calc-toggle[data-iid]');
        if (tog) {
            const newState = tog.dataset.state === '1' ? '0' : '1';
            tog.dataset.state = newState;
            tog.classList.toggle('is-on', newState === '1');
            tog.setAttribute('aria-checked', newState === '1');
            _recalculator(tog.dataset.iid);
            return;
        }

        // Calculator reset
        const resetBtn = e.target.closest('.koki-calc-reset[data-iid]');
        if (resetBtn) {
            const iid      = resetBtn.dataset.iid;
            const defaults = _calcDefaults.get(iid);
            if (!defaults) return;
            const cont = document.querySelector(`[data-interactive="calculator"][data-iid="${iid}"]`);
            if (!cont) return;
            cont.querySelectorAll(`.koki-calc-input[data-iid="${iid}"]`).forEach(inp => {
                inp.value = defaults[inp.dataset.var] ?? 0;
            });
            cont.querySelectorAll(`.koki-calc-toggle[data-iid="${iid}"]`).forEach(tog => {
                const def = defaults[tog.dataset.var] ?? 0;
                tog.dataset.state = def ? '1' : '0';
                tog.classList.toggle('is-on', def !== 0);
                tog.setAttribute('aria-checked', String(def !== 0));
            });
            _recalculator(iid);
            return;
        }

        // ── Rating stars ──
        const star = e.target.closest('.koki-star[data-iid]');
        if (star) {
            const iid = star.dataset.iid;
            const val = parseInt(star.dataset.val, 10);
            const container = document.querySelector(`[data-interactive="rating"][data-iid="${iid}"]`);
            if (container) {
                container.dataset.val = val;
                const display = container.querySelector('.koki-rating-display');
                if (display) display.textContent = val;
                container.querySelectorAll('.koki-star').forEach((s, i) => {
                    const icon = s.querySelector('i');
                    if (icon) { icon.className = i < val ? 'fa-solid fa-star' : 'fa-regular fa-star'; }
                });
                scheduleSave(iid, [val]);
            }
            return;
        }

        // ── Quiz option ──
        const qopt = e.target.closest('.koki-quiz-opt[data-iid]');
        if (qopt) {
            const qDiv = qopt.closest('.koki-quiz-q');
            if (!qDiv || qDiv.dataset.answered === '1') return;
            qDiv.dataset.answered = '1';
            const correct = qopt.dataset.correct === '1';
            qopt.classList.add(correct ? 'is-correct' : 'is-wrong');
            if (!correct) {
                qDiv.querySelectorAll('.koki-quiz-opt[data-correct="1"]').forEach(o => o.classList.add('is-correct'));
            }
            qDiv.querySelectorAll('.koki-quiz-opt').forEach(o => o.disabled = true);
            const container = qopt.closest('[data-interactive="quiz"]');
            if (correct && container) {
                const score = (parseInt(container.dataset.score, 10) || 0) + 1;
                container.dataset.score = score;
                const scoreEl = container.querySelector('.koki-quiz-score');
                if (scoreEl) scoreEl.textContent = score;
            }
            // Persist all answered questions
            if (container) {
                const iid = container.dataset.iid;
                const answers = [...container.querySelectorAll('.koki-quiz-q[data-answered="1"]')].map(qd => {
                    const qi = parseInt(qd.dataset.qi, 10);
                    const sel = qd.querySelector('.koki-quiz-opt.is-correct, .koki-quiz-opt.is-wrong');
                    return sel ? qi * 1000 + parseInt(sel.dataset.oi, 10) : null;
                }).filter(v => v !== null);
                scheduleSave(iid, answers);
            }
            return;
        }

        // ── Flashcard nav ──
        const fcBtn = e.target.closest('.koki-fc-btn[data-action][data-iid]');
        if (fcBtn) {
            const iid = fcBtn.dataset.iid;
            const action = fcBtn.dataset.action;
            const container = document.querySelector(`[data-interactive="flashcards"][data-iid="${iid}"]`);
            if (!container) return;
            const total = parseInt(container.dataset.total, 10);
            let cur = parseInt(container.dataset.cur, 10);
            const cards = container.querySelectorAll('.koki-fc-card');

            if (action === 'know') {
                const known = (parseInt(container.dataset.known, 10) || 0) + 1;
                container.dataset.known = known;
                const knownEl = container.querySelector('.koki-fc-known');
                if (knownEl) knownEl.textContent = known;
                cards[cur]?.classList.add('is-known');
                // Persist known card indices
                const knownIndices = [...cards].reduce((a, c, i) => { if (c.classList.contains('is-known')) a.push(i); return a; }, []);
                scheduleSave(iid, knownIndices);
            }

            // Reset flip before navigating
            if (cards[cur]) { cards[cur].dataset.flipped = '0'; cards[cur].classList.remove('is-flipped'); }

            if (action === 'prev') cur = Math.max(0, cur - 1);
            else cur = Math.min(total - 1, cur + 1); // next or know → advance

            container.dataset.cur = cur;
            cards.forEach((c, i) => c.classList.toggle('is-active', i === cur));
            const idxEl = container.querySelector('.koki-fc-idx');
            if (idxEl) idxEl.textContent = cur + 1;
            const fill = container.querySelector('.koki-fc-prog-fill');
            if (fill) fill.style.width = `${((cur + 1) / total) * 100}%`;
            return;
        }

        // Flashcard flip (click on card itself)
        const fcCard = e.target.closest('.koki-fc-card[data-idx]');
        if (fcCard && !e.target.closest('.koki-fc-btn')) {
            const flipped = fcCard.dataset.flipped === '1';
            fcCard.dataset.flipped = flipped ? '0' : '1';
            fcCard.classList.toggle('is-flipped', !flipped);
            return;
        }

        // ── Stepper done ──
        const stepBtn = e.target.closest('.koki-step-btn[data-iid]');
        if (stepBtn) {
            const iid = stepBtn.dataset.iid;
            const idx = parseInt(stepBtn.dataset.idx, 10);
            const container = document.querySelector(`[data-interactive="stepper"][data-iid="${iid}"]`);
            if (!container) return;
            const total = parseInt(container.dataset.total, 10);
            const steps = container.querySelectorAll('.koki-step');

            steps[idx]?.classList.remove('is-current');
            steps[idx]?.classList.add('is-done');
            steps[idx]?.querySelector('.koki-step-btn')?.classList.add('koki-step-btn--hidden');

            const nextIdx = idx + 1;
            const doneEl = container.querySelector('.koki-step-done');
            if (doneEl) doneEl.textContent = nextIdx;

            if (nextIdx < total) {
                container.dataset.cur = nextIdx;
                steps[nextIdx]?.classList.add('is-current');
                steps[nextIdx]?.querySelector('.koki-step-btn')?.classList.remove('koki-step-btn--hidden');
            } else {
                container.dataset.cur = total;
            }
            scheduleSave(iid, [nextIdx]);
            return;
        }

        // ── Kanban move ──
        const kbMv = e.target.closest('.koki-kb-mv[data-iid]');
        if (kbMv) {
            const iid = kbMv.dataset.iid;
            const kid = kbMv.dataset.kid;
            const dir = parseInt(kbMv.dataset.dir, 10);
            const container = document.querySelector(`[data-interactive="kanban"][data-iid="${iid}"]`);
            if (!container) return;
            const card = container.querySelector(`.koki-kb-card[data-kid="${kid}"]`);
            if (!card) return;
            const curCol = parseInt(card.dataset.col, 10);
            const newCol = curCol + dir;
            const ncols = parseInt(container.dataset.ncols, 10);
            if (newCol < 0 || newCol >= ncols) return;

            const targetItems = container.querySelector(`.koki-kb-items[data-col="${newCol}"]`);
            if (!targetItems) return;

            card.dataset.col = newCol;
            targetItems.appendChild(card);

            // Rebuild move buttons on this card
            const actions = card.querySelector('.koki-kb-actions');
            if (actions) {
                actions.innerHTML =
                    (newCol > 0 ? `<button class="koki-kb-mv" data-iid="${iid}" data-kid="${kid}" data-dir="-1" title="← Move left" type="button"><i class="fa-solid fa-arrow-left"></i></button>` : '') +
                    (newCol < ncols - 1 ? `<button class="koki-kb-mv" data-iid="${iid}" data-kid="${kid}" data-dir="1" title="→ Move right" type="button"><i class="fa-solid fa-arrow-right"></i></button>` : '');
            }

            // Update column counts
            container.querySelectorAll('.koki-kb-col').forEach((col) => {
                const cnt = col.querySelector('.koki-kb-cnt');
                if (cnt) cnt.textContent = col.querySelectorAll('.koki-kb-card').length;
            });
            return;
        }

        // ── Form submit ──
        const formSubmit = e.target.closest('.koki-form-submit[data-iid]');
        if (formSubmit) {
            const iid = formSubmit.dataset.iid;
            const container = document.querySelector(`[data-interactive="form"][data-iid="${iid}"]`);
            if (!container || container.dataset.submitted === '1') return;

            const data = {};
            container.querySelectorAll('[data-fid]').forEach(el => {
                data[el.dataset.fid] = el.value;
            });
            _formSubmissions.set(iid, data);
            container.dataset.submitted = '1';
            container.querySelector('.koki-form-body')?.style.setProperty('display', 'none');
            container.querySelector('.koki-form-footer')?.style.setProperty('display', 'none');
            container.querySelector('.koki-form-done')?.style.removeProperty('display');
            return;
        }

        // ── Timer controls ──
        const timerStart = e.target.closest('.koki-timer-start[data-iid]');
        if (timerStart) {
            const iid = timerStart.dataset.iid;
            const el = document.querySelector(`[data-interactive="timer"][data-iid="${iid}"]`);
            if (!el || el.dataset.running === '1') return;
            el.dataset.running = '1';
            timerStart.style.display = 'none';
            el.querySelector(`.koki-timer-pause[data-iid="${iid}"]`)?.style.removeProperty('display');
            _timerIntervals.set(iid, setInterval(() => _timerTick(iid), 1000));
            return;
        }
        const timerPause = e.target.closest('.koki-timer-pause[data-iid]');
        if (timerPause) {
            const iid = timerPause.dataset.iid;
            const el = document.querySelector(`[data-interactive="timer"][data-iid="${iid}"]`);
            if (!el) return;
            el.dataset.running = '0';
            clearInterval(_timerIntervals.get(iid)); _timerIntervals.delete(iid);
            timerPause.style.display = 'none';
            el.querySelector(`.koki-timer-start[data-iid="${iid}"]`)?.style.removeProperty('display');
            return;
        }
        const timerReset = e.target.closest('.koki-timer-reset[data-iid]');
        if (timerReset) {
            const iid = timerReset.dataset.iid;
            const el = document.querySelector(`[data-interactive="timer"][data-iid="${iid}"]`);
            if (!el) return;
            clearInterval(_timerIntervals.get(iid)); _timerIntervals.delete(iid);
            el.dataset.running = '0';
            const total = parseInt(el.dataset.total, 10);
            el.dataset.rem = total;
            el.classList.remove('koki-timer--done');
            const timeEl = el.querySelector(`.koki-timer-time[data-iid="${iid}"]`);
            if (timeEl) timeEl.textContent = _fmtTime(total);
            const arc = el.querySelector(`.koki-timer-arc[data-iid="${iid}"]`);
            if (arc) arc.style.strokeDashoffset = '0';
            el.querySelector(`.koki-timer-start[data-iid="${iid}"]`)?.style.removeProperty('display');
            el.querySelector(`.koki-timer-pause[data-iid="${iid}"]`)?.style.setProperty('display', 'none');
            return;
        }

        // ── Palette copy ──
        const swatch = e.target.closest('.koki-swatch[data-hex]');
        if (swatch) {
            const hex = swatch.dataset.hex;
            const iid = swatch.dataset.iid;
            navigator.clipboard?.writeText(hex).catch(() => {});
            const toast = document.querySelector(`.koki-palette-toast[data-iid="${iid}"]`);
            if (toast) {
                toast.textContent = `${hex} copied!`;
                toast.classList.add('is-visible');
                setTimeout(() => toast.classList.remove('is-visible'), 1800);
            }
            return;
        }

        const item = e.target.closest('.koki-cl-item[data-idx]');
        if (!item) return;
        const container = item.closest('[data-interactive="checklist"]');
        if (!container) return;

        // Prevent native label-checkbox double-toggle
        e.preventDefault();

        const cb        = item.querySelector('.koki-cl-cb');
        if (!cb) return;
        const iid       = container.dataset.iid;
        const idx       = parseInt(item.dataset.idx, 10);
        const newState  = !cb.checked;

        // Update DOM
        cb.checked = newState;
        applyItemVisual(item, newState, true);
        updateProgress(container);

        // Update state map
        const saved = _state.get(iid) || new Set();
        newState ? saved.add(idx) : saved.delete(idx);
        _state.set(iid, saved);

        // Persist to server
        scheduleSave(iid);

        // Keyboard accessibility
        item.setAttribute('aria-checked', String(newState));

        // No auto-send — state is injected as context on the user's next message
    });

    // Keyboard support (Space/Enter on focusable items)
    document.addEventListener('keydown', e => {
        if (e.key !== ' ' && e.key !== 'Enter') return;
        const item = e.target.closest('.koki-cl-item[data-idx]');
        if (item) { e.preventDefault(); item.click(); return; }
        const tog = e.target.closest('.koki-calc-toggle[data-iid]');
        if (tog) { e.preventDefault(); tog.click(); return; }
        const swatch = e.target.closest('.koki-swatch[data-hex]');
        if (swatch) { e.preventDefault(); swatch.click(); return; }
        const fcCard = e.target.closest('.koki-fc-card[data-idx]');
        if (fcCard) { e.preventDefault(); fcCard.click(); }
    });

    // Bridge to Alpine sendMessage (kept for future use by other components)
    document.addEventListener('kokomi:interactive-send', e => {
        const text = e.detail?.text;
        if (!text) return;
        const appEl = document.getElementById('app');
        if (!appEl || !window.Alpine) return;
        const data = window.Alpine.$data(appEl);
        if (!data || data.input === undefined) return;
        data.input = text;
        data.sendMessage();
    });

    // Expose context summary builder so chat.js can inject state on every send
    window.KokomiInteractives = { getContextSummary };
}

/**
 * Scan all interactive components currently visible in the conversation and
 * return a compact plain-text state block, or '' if there's nothing relevant.
 * Injected as a hidden suffix on every outgoing user message so the model is
 * always aware of checked/unchecked state without the user having to say so.
 */
function getContextSummary() {
    const lines = [];

    // Checklists
    document.querySelectorAll('[data-interactive="checklist"]').forEach(el => {
        const title   = el.querySelector('.koki-cl-title')?.textContent?.trim() || 'Checklist';
        const boxes   = [...el.querySelectorAll('.koki-cl-cb')];
        if (!boxes.length) return;

        const done    = boxes.filter(c => c.checked).length;
        const total   = boxes.length;
        const allDone = done === total;
        const noneDone = done === 0;

        const itemLines = boxes.map(cb => {
            const item  = cb.closest('.koki-cl-item');
            const label = item?.querySelector('.koki-cl-text')?.textContent?.trim() || '';
            return `  ${cb.checked ? '[x]' : '[ ]'} ${label}`;
        });

        const status = allDone
            ? 'all items checked'
            : noneDone
                ? 'no items checked'
                : `${done}/${total} checked`;

        lines.push(`"${title}" (${status}):\n${itemLines.join('\n')}`);
    });

    // Ratings
    document.querySelectorAll('[data-interactive="rating"]').forEach(el => {
        const val = parseInt(el.dataset.val, 10) || 0;
        if (val === 0) return;
        const max   = parseInt(el.dataset.max, 10) || 5;
        const title = el.querySelector('.koki-cl-title')?.textContent?.trim() || 'Rating';
        lines.push(`Rating "${title}": ${val}/${max} stars`);
    });

    // Quizzes
    document.querySelectorAll('[data-interactive="quiz"]').forEach(el => {
        const score = parseInt(el.dataset.score, 10) || 0;
        if (score === 0) return;
        const total = el.querySelectorAll('.koki-quiz-q').length;
        const title = el.querySelector('.koki-cl-title')?.textContent?.trim() || 'Quiz';
        lines.push(`Quiz "${title}": ${score}/${total} correct`);
    });

    // Flashcards
    document.querySelectorAll('[data-interactive="flashcards"]').forEach(el => {
        const known = parseInt(el.dataset.known, 10) || 0;
        if (known === 0) return;
        const total = parseInt(el.dataset.total, 10) || 0;
        const title = el.querySelector('.koki-cl-title')?.textContent?.trim() || 'Flashcards';
        lines.push(`Flashcards "${title}": ${known}/${total} known`);
    });

    // Steppers
    document.querySelectorAll('[data-interactive="stepper"]').forEach(el => {
        const cur   = parseInt(el.dataset.cur, 10) || 0;
        if (cur === 0) return;
        const total = parseInt(el.dataset.total, 10) || 0;
        const title = el.querySelector('.koki-cl-title')?.textContent?.trim() || 'Steps';
        lines.push(`Steps "${title}": step ${cur}/${total}`);
    });

    // Kanban
    document.querySelectorAll('[data-interactive="kanban"]').forEach(el => {
        const title = el.querySelector('.koki-cl-title')?.textContent?.trim() || 'Kanban';
        const colParts = [];
        el.querySelectorAll('.koki-kb-col').forEach(col => {
            const colTitle = col.querySelector('.koki-kb-col-title')?.textContent?.trim() || '';
            const cards    = [...col.querySelectorAll('.koki-kb-card')].map(c => c.querySelector('.koki-kb-card-text')?.textContent?.trim() || '');
            colParts.push(`[${colTitle}: ${cards.join(', ')}]`);
        });
        lines.push(`Kanban "${title}": ${colParts.join(' ')}`);
    });

    // Form submissions
    _formSubmissions.forEach((data, iid) => {
        const el    = document.querySelector(`[data-interactive="form"][data-iid="${iid}"]`);
        const title = el?.querySelector('.koki-cl-title')?.textContent?.trim() || 'Form';
        const fields = Object.entries(data).map(([k, v]) => `${k}=${v}`).join(', ');
        lines.push(`Form "${title}" submitted: ${fields}`);
    });

    if (!lines.length) return '';
    return `\n\n[Interactive component state:\n${lines.join('\n\n')}]`;
}
