/**
 * Canvas — an editable artifact opened beside the chat.
 *
 * Two modes:
 *   code     → Monaco, the editor core VS Code itself is built on.
 *   document → Quill on a Word-style page (a white sheet with margins).
 *
 * The editor instances deliberately live in this module-scoped registry rather
 * than on Alpine state: Alpine deep-proxies whatever you put on the component,
 * and wrapping Monaco/Quill in a reactive Proxy breaks them (they rely on
 * object identity and mutate huge internal structures). Alpine only ever holds
 * plain data — text, flags, counts.
 */

const editors = {
    monaco: null,       // monaco.editor.IStandaloneCodeEditor
    quill: null,        // Quill instance
    monacoLoading: null // Promise, so concurrent opens share one AMD load
};

/**
 * Live-stream bookkeeping, kept out of Alpine state.
 *
 * `buffer` is the authoritative raw text the model has sent so far. It must be
 * tracked separately from canvas.content because painting the document editor
 * fires Quill's text-change, which would otherwise overwrite the accumulating
 * markdown with the editor's rendered HTML — after which the next delta gets
 * appended to HTML and the document turns to garbage.
 *
 * `suppress` guards exactly that: it's raised while we write into an editor
 * programmatically, so change handlers know the edit isn't the user's.
 */
const stream = { buffer: '', suppress: false };

/**
 * Last known caret/selection inside the document editor.
 *
 * Tracked via Quill's selection-change rather than read on demand: by the time
 * a right-click menu or prompt box is open the editor has usually lost focus,
 * at which point getSelection() returns null and the index needed to splice a
 * replacement is gone.
 */
const caret = { range: null, selection: null };

const VENDOR = '/static/vendor';

/**
 * Is this artifact a canvas?
 *
 * Deliberately not just `type === 'canvas'`. The base artifact rule ("use
 * type=\"language\"") is repeated many times in the system prompt and reliably
 * outweighs the single canvas rule, so models routinely emit
 * type="java" mode="code" instead of type="canvas". A `mode` attribute is only
 * ever produced by the canvas instruction, so treat it as the real signal.
 */
export function isCanvasArtifact(art) {
    if (!art) return false;
    if ((art.type || '').toLowerCase() === 'canvas') return true;
    const mode = (art.mode || '').toLowerCase();
    return mode === 'code' || mode === 'document';
}

/** Load Monaco's AMD bundle once, from the local vendor copy (works offline). */
function loadMonaco() {
    if (window.monaco && window.monaco.editor) return Promise.resolve(window.monaco);
    if (editors.monacoLoading) return editors.monacoLoading;

    editors.monacoLoading = new Promise((resolve, reject) => {
        // Monaco spawns its language services in web workers. Same-origin here,
        // so point it straight at the vendored worker rather than a blob shim.
        window.MonacoEnvironment = {
            getWorkerUrl(_moduleId, label) {
                const base = `${VENDOR}/monaco/vs`;
                const map = {
                    json: `${base}/language/json/jsonWorker.js`,
                    css: `${base}/language/css/cssWorker.js`,
                    scss: `${base}/language/css/cssWorker.js`,
                    less: `${base}/language/css/cssWorker.js`,
                    html: `${base}/language/html/htmlWorker.js`,
                    handlebars: `${base}/language/html/htmlWorker.js`,
                    razor: `${base}/language/html/htmlWorker.js`,
                    typescript: `${base}/language/typescript/tsWorker.js`,
                    javascript: `${base}/language/typescript/tsWorker.js`
                };
                const worker = map[label] || `${base}/base/worker/workerMain.js`;
                // The worker needs the AMD baseUrl to resolve its own imports.
                const shim = `self.MonacoEnvironment={baseUrl:'${location.origin}${base}/'};importScripts('${location.origin}${worker}');`;
                return `data:text/javascript;charset=utf-8,${encodeURIComponent(shim)}`;
            }
        };

        const script = document.createElement('script');
        script.src = `${VENDOR}/monaco/vs/loader.js`;
        script.onload = () => {
            try {
                window.require.config({ paths: { vs: `${VENDOR}/monaco/vs` } });
                window.require(['vs/editor/editor.main'], () => resolve(window.monaco));
            } catch (e) { reject(e); }
        };
        script.onerror = () => reject(new Error('Failed to load Monaco'));
        document.head.appendChild(script);
    });

    return editors.monacoLoading;
}

/** Load Quill (plain script + stylesheet) once. */
function loadQuill() {
    if (window.Quill) return Promise.resolve(window.Quill);
    return new Promise((resolve, reject) => {
        if (!document.getElementById('quill-css')) {
            const link = document.createElement('link');
            link.id = 'quill-css';
            link.rel = 'stylesheet';
            link.href = `${VENDOR}/quill/quill.snow.css`;
            document.head.appendChild(link);
        }
        const script = document.createElement('script');
        script.src = `${VENDOR}/quill/quill.js`;
        script.onload = () => resolve(window.Quill);
        script.onerror = () => reject(new Error('Failed to load Quill'));
        document.head.appendChild(script);
    });
}

/** Monaco has its own language ids; map a few common aliases onto them. */
function normalizeLanguage(lang) {
    const l = (lang || '').toLowerCase().trim();
    const alias = {
        js: 'javascript', jsx: 'javascript', mjs: 'javascript',
        ts: 'typescript', tsx: 'typescript',
        py: 'python', python3: 'python',
        sh: 'shell', bash: 'shell', zsh: 'shell',
        yml: 'yaml', md: 'markdown', 'c++': 'cpp', 'c#': 'csharp',
        golang: 'go', rb: 'ruby', ps1: 'powershell', text: 'plaintext'
    };
    return alias[l] || l || 'plaintext';
}

/**
 * Keyword + builtin completions per language.
 *
 * Monaco only ships real language services (semantic IntelliSense, type
 * checking) for typescript/javascript, json, css and html. Everything else
 * gets a Monarch tokenizer — syntax highlighting only, no completion. Real
 * smarts for Python/Java/C++ come from language servers (Pylance, jdtls,
 * clangd) which are OS processes and can't run in the browser.
 *
 * So we register our own provider: language keywords and common builtins,
 * which combined with Monaco's word-based suggestions covers most of what
 * you actually reach for while editing.
 */
const LANGUAGE_KEYWORDS = {
    python: {
        keyword: ['def', 'class', 'return', 'if', 'elif', 'else', 'for', 'while', 'break',
            'continue', 'import', 'from', 'as', 'try', 'except', 'finally', 'raise', 'with',
            'lambda', 'yield', 'global', 'nonlocal', 'pass', 'assert', 'del', 'async', 'await',
            'True', 'False', 'None', 'and', 'or', 'not', 'in', 'is', 'match', 'case'],
        builtin: ['print', 'len', 'range', 'enumerate', 'zip', 'map', 'filter', 'sorted',
            'sum', 'min', 'max', 'abs', 'round', 'int', 'str', 'float', 'bool', 'list', 'dict',
            'set', 'tuple', 'open', 'isinstance', 'type', 'super', 'hasattr', 'getattr',
            'setattr', 'format', 'reversed', 'any', 'all', 'input', 'repr', 'self']
    },
    java: {
        keyword: ['public', 'private', 'protected', 'class', 'interface', 'extends',
            'implements', 'static', 'final', 'void', 'new', 'return', 'if', 'else', 'for',
            'while', 'do', 'switch', 'case', 'break', 'continue', 'try', 'catch', 'finally',
            'throw', 'throws', 'import', 'package', 'this', 'super', 'null', 'true', 'false',
            'abstract', 'synchronized', 'instanceof', 'enum', 'record', 'var'],
        builtin: ['String', 'Integer', 'Double', 'Boolean', 'Long', 'Object', 'List',
            'ArrayList', 'Map', 'HashMap', 'Set', 'HashSet', 'System', 'Math', 'Exception',
            'Optional', 'Stream', 'Arrays', 'Collections', 'StringBuilder']
    },
    cpp: {
        keyword: ['int', 'char', 'float', 'double', 'bool', 'void', 'auto', 'const',
            'constexpr', 'static', 'struct', 'class', 'public', 'private', 'protected',
            'virtual', 'override', 'return', 'if', 'else', 'for', 'while', 'do', 'switch',
            'case', 'break', 'continue', 'try', 'catch', 'throw', 'namespace', 'using',
            'template', 'typename', 'new', 'delete', 'nullptr', 'true', 'false', 'sizeof',
            'enum', 'union', 'inline', 'friend', 'operator', 'this'],
        builtin: ['std', 'cout', 'cin', 'endl', 'string', 'vector', 'map', 'set',
            'unordered_map', 'pair', 'shared_ptr', 'unique_ptr', 'printf', 'malloc', 'free',
            'size_t', 'include']
    },
    csharp: {
        keyword: ['using', 'namespace', 'class', 'struct', 'interface', 'public', 'private',
            'protected', 'internal', 'static', 'readonly', 'const', 'void', 'var', 'new',
            'return', 'if', 'else', 'for', 'foreach', 'while', 'switch', 'case', 'break',
            'continue', 'try', 'catch', 'finally', 'throw', 'async', 'await', 'null', 'true',
            'false', 'this', 'base', 'override', 'virtual', 'abstract', 'record'],
        builtin: ['string', 'int', 'bool', 'double', 'decimal', 'object', 'List',
            'Dictionary', 'IEnumerable', 'Task', 'Console', 'Math', 'Exception', 'LINQ']
    },
    go: {
        keyword: ['func', 'package', 'import', 'var', 'const', 'type', 'struct', 'interface',
            'map', 'chan', 'go', 'defer', 'return', 'if', 'else', 'for', 'range', 'switch',
            'case', 'default', 'break', 'continue', 'select', 'nil', 'true', 'false'],
        builtin: ['make', 'new', 'len', 'cap', 'append', 'copy', 'delete', 'panic',
            'recover', 'print', 'println', 'string', 'int', 'error', 'fmt']
    },
    rust: {
        keyword: ['fn', 'let', 'mut', 'const', 'struct', 'enum', 'impl', 'trait', 'pub',
            'use', 'mod', 'match', 'if', 'else', 'loop', 'while', 'for', 'in', 'return',
            'break', 'continue', 'where', 'async', 'await', 'move', 'ref', 'dyn', 'self',
            'Self', 'true', 'false', 'unsafe'],
        builtin: ['Vec', 'String', 'Option', 'Result', 'Some', 'None', 'Ok', 'Err', 'Box',
            'HashMap', 'println', 'format', 'panic', 'clone', 'unwrap', 'expect', 'iter']
    },
    ruby: {
        keyword: ['def', 'end', 'class', 'module', 'if', 'elsif', 'else', 'unless', 'while',
            'until', 'for', 'in', 'do', 'begin', 'rescue', 'ensure', 'raise', 'return',
            'yield', 'require', 'attr_accessor', 'nil', 'true', 'false', 'self'],
        builtin: ['puts', 'print', 'p', 'gets', 'each', 'map', 'select', 'reject', 'length',
            'push', 'Array', 'Hash', 'String', 'Integer', 'Symbol']
    },
    php: {
        keyword: ['function', 'class', 'interface', 'extends', 'implements', 'public',
            'private', 'protected', 'static', 'return', 'if', 'else', 'elseif', 'foreach',
            'for', 'while', 'switch', 'case', 'break', 'continue', 'try', 'catch', 'finally',
            'throw', 'new', 'echo', 'require', 'include', 'namespace', 'use', 'null', 'true', 'false'],
        builtin: ['array', 'count', 'strlen', 'implode', 'explode', 'isset', 'empty',
            'var_dump', 'json_encode', 'json_decode', 'array_map', 'array_filter']
    },
    shell: {
        keyword: ['if', 'then', 'else', 'elif', 'fi', 'for', 'in', 'do', 'done', 'while',
            'case', 'esac', 'function', 'return', 'export', 'local', 'source', 'alias'],
        builtin: ['echo', 'cd', 'ls', 'grep', 'sed', 'awk', 'cat', 'mkdir', 'rm', 'cp',
            'mv', 'chmod', 'curl', 'find', 'xargs', 'read', 'printf', 'test']
    },
    sql: {
        keyword: ['SELECT', 'FROM', 'WHERE', 'INSERT', 'INTO', 'VALUES', 'UPDATE', 'SET',
            'DELETE', 'CREATE', 'TABLE', 'ALTER', 'DROP', 'INDEX', 'JOIN', 'LEFT', 'RIGHT',
            'INNER', 'OUTER', 'ON', 'GROUP', 'BY', 'ORDER', 'HAVING', 'LIMIT', 'OFFSET',
            'DISTINCT', 'AS', 'AND', 'OR', 'NOT', 'NULL', 'primary key', 'foreign key'],
        builtin: ['COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'COALESCE', 'CAST', 'NOW', 'UPPER', 'LOWER']
    }
};

// Monaco keeps providers globally, so registering twice would double every
// suggestion. Track which languages we've already wired up.
const registeredLanguages = new Set();

function registerCompletions(monaco, language) {
    const spec = LANGUAGE_KEYWORDS[language];
    if (!spec || registeredLanguages.has(language)) return;
    registeredLanguages.add(language);

    monaco.languages.registerCompletionItemProvider(language, {
        provideCompletionItems(model, position) {
            const word = model.getWordUntilPosition(position);
            const range = {
                startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
                startColumn: word.startColumn, endColumn: word.endColumn
            };
            const Kind = monaco.languages.CompletionItemKind;
            const build = (items, kind, detail) => items.map(label => ({
                label, kind, insertText: label, range, detail
            }));
            return {
                suggestions: [
                    ...build(spec.keyword, Kind.Keyword, `${language} keyword`),
                    ...build(spec.builtin, Kind.Function, `${language} builtin`)
                ]
            };
        }
    });
}

/** File extension to use when downloading a code canvas. */
function extensionForLanguage(lang) {
    const map = {
        javascript: 'js', typescript: 'ts', python: 'py', shell: 'sh',
        markdown: 'md', csharp: 'cs', cpp: 'cpp', ruby: 'rb', rust: 'rs',
        kotlin: 'kt', powershell: 'ps1', yaml: 'yml', plaintext: 'txt'
    };
    return map[lang] || lang || 'txt';
}

/**
 * Minimal markdown → HTML for seeding the document editor.
 * The app already ships `marked`, so use it when present and only fall back
 * to this for the handful of cases where it hasn't loaded yet.
 */
function markdownToHtml(md) {
    if (window.marked) {
        try {
            return window.marked.parse(md || '', { breaks: true });
        } catch (_) { /* fall through */ }
    }
    return (md || '')
        .split(/\n{2,}/)
        .map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`)
        .join('');
}

export function getCanvasActions() {
    return {
        // ── Opening / closing ──────────────────────────────────────────

        /** Open an artifact as a canvas. Accepts the artifact object. */
        async openCanvas(art) {
            if (!art) return;

            const mode = (art.mode || 'code').toLowerCase() === 'document' ? 'document' : 'code';

            // Switching to a different canvas: flush pending edits from the old one.
            if (this.canvas.open && this.canvas.id && this.canvas.id !== art.id && this.canvas.dirty) {
                await this.saveCanvas();
            }

            this.canvas.id = art.id;
            this.canvas.mode = mode;
            this.canvas.language = normalizeLanguage(art.language);
            this.canvas.title = art.title || 'Untitled';
            this.canvas.content = art.content || '';
            this.canvas.dirty = false;
            this.canvas.savedAt = null;
            this.canvas.open = true;

            // Opening a canvas the model is still writing (clicking the card
            // mid-generation): resume the live stream from what's arrived so far
            // rather than showing a frozen partial document.
            if (art.streaming) {
                this.canvas.streaming = true;
                stream.buffer = art.content || '';
            } else if (!this.canvas.streaming) {
                stream.buffer = '';
            }

            // Let the split-pane lay out before measuring for the editor.
            await this.$nextTick();
            await this.mountCanvasEditor();

            if (this.canvas.streaming) this.flushCanvasStream();
        },

        async closeCanvas() {
            if (this.canvas.dirty) await this.saveCanvas();
            this.disposeCanvasEditors();
            this.canvas.open = false;
            this.canvas.id = null;
            this.canvas.content = '';
            this.canvas.dirty = false;
        },

        disposeCanvasEditors() {
            if (editors.monaco) {
                try { editors.monaco.dispose(); } catch (_) {}
                editors.monaco = null;
            }
            // Quill has no dispose(). Critically, it injects its toolbar as a
            // SIBLING before the host element, so clearing the host alone leaves
            // the old toolbar behind and the next mount stacks a second one on
            // top — that's the duplicated header. Sweep the whole sheet.
            const host = document.getElementById('canvas-doc-host');
            const sheet = host && host.parentElement;
            if (sheet) sheet.querySelectorAll('.ql-toolbar').forEach(el => el.remove());
            if (host) {
                host.innerHTML = '';
                host.className = '';   // Quill leaves ql-container/ql-snow behind
            }
            editors.quill = null;
        },

        // ── Mounting the right editor ──────────────────────────────────

        async mountCanvasEditor() {
            this.disposeCanvasEditors();
            try {
                if (this.canvas.mode === 'document') await this.mountDocumentCanvas();
                else await this.mountCodeCanvas();
            } catch (e) {
                console.error('[canvas] editor failed to mount', e);
                this.showToast?.('Canvas editor failed to load', 'error');
            }
        },

        async mountCodeCanvas() {
            const monaco = await loadMonaco();
            const host = document.getElementById('canvas-code-host');
            if (!host) return;
            host.innerHTML = '';

            // Keyword/builtin completions for languages Monaco has no language
            // service for (python, java, cpp, go, rust, …).
            registerCompletions(monaco, this.canvas.language);

            editors.monaco = monaco.editor.create(host, {
                value: this.canvas.content || '',
                language: this.canvas.language,
                theme: this.darkMode ? 'vs-dark' : 'vs',
                automaticLayout: true,      // reflow when the split-pane resizes
                fontSize: 13.5,
                // A literal stack, not var(--font-mono): Monaco measures glyph widths
                // up front, and an unresolved var() invalidates the whole declaration,
                // silently falling back to the inherited sans-serif.
                fontFamily: "'JetBrains Mono', 'Fira Code', 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace",
                fontLigatures: true,
                minimap: { enabled: true },
                smoothScrolling: true,
                cursorBlinking: 'smooth',
                renderLineHighlight: 'all',
                scrollBeyondLastLine: false,
                padding: { top: 14, bottom: 14 },
                tabSize: 4,
                // Suggestions: our keyword provider above, plus Monaco's word-based
                // completion (identifiers already present in the file) so names you've
                // defined get offered even in languages with no language service.
                wordBasedSuggestions: 'allDocuments',
                quickSuggestions: { other: true, comments: false, strings: false },
                suggestOnTriggerCharacters: true,
                acceptSuggestionOnEnter: 'on',
                tabCompletion: 'on',
                suggestSelection: 'first',
                bracketPairColorization: { enabled: true }
            });

            editors.monaco.onDidChangeModelContent(() => {
                this.canvas.content = editors.monaco.getValue();
                this.markCanvasDirty();
            });

            // AI actions live in Monaco's OWN right-click menu rather than a
            // custom overlay: Monaco owns its context menu and swallows the
            // native contextmenu event, so the document canvas's approach can't
            // work here. addAction is the supported extension point, and these
            // also show up in the command palette (F1).
            const aiActions = [
                { id: 'kokomi.explain', label: 'Kokomi: Explain this code', order: 1,
                  run: () => this.openCanvasPrompt('Explain what this code does, as a comment above it.') },
                { id: 'kokomi.refactor', label: 'Kokomi: Refactor selection', order: 2,
                  run: () => this.patchCanvas('Refactor the selected code for clarity. Keep behaviour identical.') },
                { id: 'kokomi.fixbugs', label: 'Kokomi: Find and fix bugs', order: 3,
                  run: () => this.patchCanvas('Find and fix any bugs. Change only the lines that are wrong.') },
                { id: 'kokomi.comment', label: 'Kokomi: Add comments', order: 4,
                  run: () => this.patchCanvas('Add concise explanatory comments. Do not change any logic.') },
                { id: 'kokomi.tests', label: 'Kokomi: Add a test', order: 5,
                  run: () => this.patchCanvas('Add a small test for this code, inserted at a sensible place.') }
            ];
            for (const a of aiActions) {
                editors.monaco.addAction({
                    id: a.id, label: a.label,
                    contextMenuGroupId: 'kokomi', contextMenuOrder: a.order,
                    run: a.run
                });
            }
            // Ctrl+I for a free-form instruction. Deliberately NOT Ctrl+Space,
            // which Monaco reserves for triggering autocomplete.
            editors.monaco.addAction({
                id: 'kokomi.prompt',
                label: 'Kokomi: Edit with a prompt…',
                keybindings: [monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyI],
                contextMenuGroupId: 'kokomi', contextMenuOrder: 0,
                run: () => this.openCanvasPrompt('')
            });
        },

        async mountDocumentCanvas() {
            const Quill = await loadQuill();
            const host = document.getElementById('canvas-doc-host');
            if (!host) return;
            // Clear any toolbar left by a previous mount (see disposeCanvasEditors).
            const sheet = host.parentElement;
            if (sheet) sheet.querySelectorAll('.ql-toolbar').forEach(el => el.remove());
            host.innerHTML = '';
            host.className = '';

            editors.quill = new Quill(host, {
                theme: 'snow',
                placeholder: 'Start writing…',
                modules: {
                    toolbar: [
                        [{ header: [1, 2, 3, 4, false] }],
                        [{ font: [] }, { size: [] }],
                        ['bold', 'italic', 'underline', 'strike'],
                        [{ color: [] }, { background: [] }],
                        [{ align: [] }],
                        [{ list: 'ordered' }, { list: 'bullet' }, { indent: '-1' }, { indent: '+1' }],
                        ['blockquote', 'code-block', 'link'],
                        ['clean']
                    ]
                }
            });

            // Content arrives as markdown from the model, but round-trips as HTML
            // once the user has edited it — detect which we're holding.
            const raw = this.canvas.content || '';
            const looksHtml = /^\s*<(p|h[1-6]|ul|ol|blockquote|div|pre|table)[\s>]/i.test(raw);
            editors.quill.clipboard.dangerouslyPasteHTML(
                looksHtml ? raw : markdownToHtml(raw)
            );

            this.updateCanvasWordCount();
            editors.quill.on('text-change', () => {
                // Ignore our own programmatic paints, and anything during a
                // stream — otherwise the rendered HTML clobbers the raw markdown
                // the model is still appending to.
                if (stream.suppress || this.canvas.streaming) {
                    this.updateCanvasWordCount();
                    return;
                }
                this.canvas.content = editors.quill.root.innerHTML;
                this.updateCanvasWordCount();
                this.markCanvasDirty();
            });

            // Remember where the caret/selection was, so an edit triggered from
            // the menu (by which point focus has moved) still knows what to replace.
            editors.quill.on('selection-change', (range) => {
                if (!range) return;
                caret.range = range;
                if (range.length) caret.selection = range;
            });

            // Opt this editor out of Grammarly and friends. They inject an
            // overlay into contenteditable elements that swallows contextmenu
            // (so our right-click menu never fires) and — worse — splice their
            // own nodes into the DOM, which corrupts Quill's internal model.
            // The AI actions in this pane cover the same ground anyway.
            editors.quill.root.setAttribute('data-gramm', 'false');
            editors.quill.root.setAttribute('data-gramm_editor', 'false');
            editors.quill.root.setAttribute('data-enable-grammarly', 'false');

            // NOTE: the contextmenu / Ctrl+Space handlers are bound declaratively
            // on the scroll container in the template, in the CAPTURE phase — that
            // way they fire before any extension listener on the editor itself,
            // survive editor remounts, and also work in the page margins.
        },

        updateCanvasWordCount() {
            if (!editors.quill) return;
            const text = (editors.quill.getText() || '').trim();
            this.canvas.wordCount = text ? text.split(/\s+/).length : 0;
        },

        // ── Persistence ────────────────────────────────────────────────

        /** Mark edited and schedule a debounced autosave. */
        markCanvasDirty() {
            // While the model is streaming into the canvas, every keystroke-like
            // edit event is the model's own writing — not the user's. Marking it
            // dirty would autosave half-written content and race the stream.
            if (this.canvas.streaming) return;
            this.canvas.dirty = true;
            clearTimeout(this._canvasSaveTimer);
            this._canvasSaveTimer = setTimeout(() => this.saveCanvas(), 900);
        },

        // ── Live streaming (the model typing into the canvas) ──────────

        /**
         * Open the canvas empty as soon as the model starts writing it.
         *
         * Ordering matters: the flags and buffer are set BEFORE awaiting the
         * open, because mounting an editor for the first time has to fetch
         * Monaco/Quill over the network. Deltas that land during that wait must
         * still accumulate, and must not be wiped when the mount completes.
         */
        async beginCanvasStream(art) {
            stream.buffer = '';
            this.canvas.streaming = true;
            await this.openCanvas({ ...art, content: '' });
            this.canvas.streaming = true;   // openCanvas resets flags
            this.flushCanvasStream();       // paint whatever arrived while loading
        },

        /** Push the whole buffer into a freshly-mounted editor. */
        flushCanvasStream() {
            if (!stream.buffer) return;
            this.canvas.content = stream.buffer;
            if (this.canvas.mode === 'document') {
                this.paintStreamingDocument();
            } else if (editors.monaco && editors.monaco.getValue() !== stream.buffer) {
                stream.suppress = true;
                editors.monaco.setValue(stream.buffer);
                stream.suppress = false;
                const model = editors.monaco.getModel();
                if (model) editors.monaco.revealLine(model.getLineCount());
            }
        },

        /** Append a streamed delta so it visibly types out in the editor. */
        streamCanvasChunk(id, delta) {
            if (!this.canvas.open || this.canvas.id !== id || !delta) return;

            // Always accumulate, even if the editor hasn't mounted yet — the
            // flush on mount replays the buffer so nothing is lost.
            stream.buffer += delta;
            this.canvas.content = stream.buffer;

            if (this.canvas.mode === 'document') {
                // Re-rendering markdown on every token would thrash the DOM, so
                // repaint on a short throttle instead.
                if (!this._canvasDocTimer) {
                    this._canvasDocTimer = setTimeout(() => {
                        this._canvasDocTimer = null;
                        this.paintStreamingDocument();
                    }, 120);
                }
                return;
            }

            if (!editors.monaco) return;   // buffered; flush will replay it
            const model = editors.monaco.getModel();
            if (!model) return;
            // Append at the very end and follow it, so it reads as typing.
            const last = model.getLineCount();
            const col = model.getLineMaxColumn(last);
            stream.suppress = true;
            model.applyEdits([{
                range: { startLineNumber: last, startColumn: col, endLineNumber: last, endColumn: col },
                text: delta
            }]);
            stream.suppress = false;
            editors.monaco.revealLine(model.getLineCount());
        },

        paintStreamingDocument() {
            if (!editors.quill) return;
            // suppress: this paste is ours, not the user's — see `stream` above.
            stream.suppress = true;
            editors.quill.setContents([]);   // clear without leaving stale blots
            editors.quill.clipboard.dangerouslyPasteHTML(markdownToHtml(stream.buffer));
            stream.suppress = false;

            this.updateCanvasWordCount();
            // Keep the newest text in view as it grows.
            const scroller = document.querySelector('.kokomi-canvas-doc-scroll');
            if (scroller) scroller.scrollTop = scroller.scrollHeight;
        },

        /** Model finished writing — settle the final content and stop streaming. */
        endCanvasStream(art) {
            if (!this.canvas.open || this.canvas.id !== art.id) return;
            clearTimeout(this._canvasDocTimer);
            this._canvasDocTimer = null;

            // Settle on the server's authoritative copy, then paint once more —
            // still flagged as streaming so the change handlers stay suppressed.
            stream.buffer = art.content || stream.buffer;
            this.canvas.content = stream.buffer;

            if (this.canvas.mode === 'document') {
                this.paintStreamingDocument();
            } else if (editors.monaco && editors.monaco.getValue() !== stream.buffer) {
                stream.suppress = true;
                editors.monaco.setValue(stream.buffer);
                stream.suppress = false;
            }

            this.canvas.streaming = false;
            this.canvas.dirty = false;
        },

        async saveCanvas() {
            const { id, content } = this.canvas;
            if (!id || !this.currentConvId) return;

            clearTimeout(this._canvasSaveTimer);
            this.canvas.saving = true;
            try {
                const res = await fetch(`/api/canvas/${this.currentConvId}/${id}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ content, title: this.canvas.title })
                });
                if (!res.ok) throw new Error(`save failed (${res.status})`);

                this.canvas.dirty = false;
                this.canvas.savedAt = Date.now();

                // Keep the in-memory artifact in step so reopening shows the edits.
                for (const msg of this.messages || []) {
                    const art = (msg.artifacts || []).find(a => a.id === id);
                    if (art) { art.content = content; break; }
                }
            } catch (e) {
                console.error('[canvas] save failed', e);
                this.showToast?.('Could not save canvas', 'error');
            } finally {
                this.canvas.saving = false;
            }
        },

        // ── AI actions on the document (context menu + Ctrl+Space) ─────

        /** Quick actions offered on right-click. `needsSelection` ones are
         *  hidden when nothing is highlighted. */
        get canvasActions() {
            return [
                { id: 'improve', label: 'Improve writing', icon: 'fa-wand-magic-sparkles', needsSelection: true,
                  instruction: 'Rewrite this passage so it reads better — clearer, tighter, better flow — while preserving its meaning and level of detail.' },
                { id: 'fix', label: 'Fix spelling & grammar', icon: 'fa-spell-check', needsSelection: true,
                  instruction: 'Correct spelling, grammar and punctuation in this passage. Change nothing else.' },
                { id: 'shorter', label: 'Make shorter', icon: 'fa-compress', needsSelection: true,
                  instruction: 'Make this passage significantly more concise while keeping every key point.' },
                { id: 'longer', label: 'Expand', icon: 'fa-expand', needsSelection: true,
                  instruction: 'Expand this passage with more detail, examples and explanation.' },
                { id: 'simplify', label: 'Simplify', icon: 'fa-feather', needsSelection: true,
                  instruction: 'Rewrite this passage in plain, simple language a general reader can follow.' },
                { id: 'continue', label: 'Continue writing', icon: 'fa-pen-nib', needsSelection: false,
                  instruction: 'Continue writing the document from where it currently ends, matching its voice and structure.' },
                { id: 'ask', label: 'Ask AI…', icon: 'fa-comment-dots', needsSelection: false, custom: true }
            ];
        },

        /** Text currently highlighted inside the document editor. */
        canvasSelectionText() {
            if (!editors.quill) return '';

            const range = editors.quill.getSelection();
            if (range && range.length) {
                return editors.quill.getText(range.index, range.length).trim();
            }

            // getSelection() reports null when the editor isn't focused, which is
            // exactly the case on right-click. Fall back to the native selection,
            // but only if it actually lies inside this editor.
            const sel = window.getSelection();
            if (sel && !sel.isCollapsed && sel.rangeCount) {
                const node = sel.anchorNode;
                const el = node && (node.nodeType === 1 ? node : node.parentElement);
                if (el && editors.quill.root.contains(el)) {
                    return sel.toString().trim();
                }
            }
            return '';
        },

        openCanvasContextMenu(e) {
            e.preventDefault();
            this.canvasPrompt.open = false;
            this.canvasMenu.selection = this.canvasSelectionText();
            // Clamp so the menu never opens off-screen.
            this.canvasMenu.x = Math.min(e.clientX, window.innerWidth - 240);
            this.canvasMenu.y = Math.min(e.clientY, window.innerHeight - 330);
            this.canvasMenu.open = true;
        },

        closeCanvasMenu() { this.canvasMenu.open = false; },

        runCanvasAction(action) {
            const selection = this.canvasMenu.selection;
            this.canvasMenu.open = false;
            if (action.custom) {
                this.openCanvasPrompt(selection);
                return;
            }
            this.sendCanvasInstruction(action.instruction, selection);
        },

        /** Ctrl+Space (document) / Ctrl+I (code): inline instruction box. */
        openCanvasPrompt(selection = null) {
            this.canvasMenu.open = false;

            // Code canvas: centre the box over the editor and remember the
            // selected line range for the status line.
            if (this.canvas.mode === 'code') {
                const sel = editors.monaco && editors.monaco.getSelection();
                const lines = sel && !sel.isEmpty()
                    ? `lines ${sel.startLineNumber}–${sel.endLineNumber}` : '';
                this.canvasPrompt.selection = lines;
                this.canvasPrompt.text = typeof selection === 'string' ? selection : '';
                const host = document.getElementById('canvas-code-host');
                const r = host ? host.getBoundingClientRect() : { left: 0, top: 0, width: 600 };
                this.canvasPrompt.x = Math.min(Math.max(r.left + r.width / 2 - 190, 12), window.innerWidth - 400);
                this.canvasPrompt.y = Math.max(r.top + 56, 12);
                this.canvasPrompt.open = true;
                this.$nextTick(() => this.$refs.canvasPromptInput?.focus());
                return;
            }

            this.canvasPrompt.selection = selection !== null ? selection : this.canvasSelectionText();
            this.canvasPrompt.text = '';

            // Anchor to the caret when we can locate it, else the pane centre.
            let x = window.innerWidth / 2, y = window.innerHeight / 2;
            if (editors.quill) {
                const range = editors.quill.getSelection();
                if (range) {
                    const b = editors.quill.getBounds(range.index, range.length || 0);
                    const host = editors.quill.root.getBoundingClientRect();
                    x = host.left + b.left;
                    y = host.top + b.bottom + 8;
                }
            }
            this.canvasPrompt.x = Math.min(Math.max(x, 12), window.innerWidth - 400);
            this.canvasPrompt.y = Math.min(Math.max(y, 12), window.innerHeight - 130);
            this.canvasPrompt.open = true;
            this.$nextTick(() => this.$refs.canvasPromptInput?.focus());
        },

        closeCanvasPrompt() {
            this.canvasPrompt.open = false;
            this.canvasPrompt.text = '';
        },

        submitCanvasPrompt() {
            const text = (this.canvasPrompt.text || '').trim();
            if (!text) return;
            const selection = this.canvasPrompt.selection;
            this.closeCanvasPrompt();
            // Code → line-addressed patch.
            // Document with a selection → splice just that range (most precise).
            // Document without one → block-addressed patch, so an instruction
            // like "fix the typos" can reach anywhere without rewriting the file.
            if (this.canvas.mode === 'code') this.patchCanvas(text);
            else if (selection) this.sendCanvasInstruction(text, selection);
            else this.patchCanvas(text);
        },

        /**
         * Apply an instruction directly to the document — no chat turn.
         *
         * The old approach sent a message and let the model re-emit the whole
         * artifact, which both cluttered the conversation and rewrote the entire
         * document to change one sentence. This asks the server for the
         * replacement text only, then splices it into the exact range that was
         * selected, leaving everything else untouched.
         */
        async sendCanvasInstruction(instruction, selection) {
            if (!this.canvas.open || this.canvas.editing) return;

            // Resolve the target range now, before any focus changes.
            const range = selection ? (caret.selection || caret.range) : caret.range;

            this.canvas.editing = true;
            try {
                const res = await fetch(
                    `/api/canvas/${this.currentConvId}/${this.canvas.id}/edit`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ instruction, selection: selection || '' })
                    });
                if (!res.ok) {
                    const detail = await res.json().catch(() => ({}));
                    throw new Error(detail.detail || `edit failed (${res.status})`);
                }
                const { text } = await res.json();
                this.spliceCanvasText(text, selection ? range : null, range);
            } catch (e) {
                console.error('[canvas] edit failed', e);
                this.showToast?.(String(e.message || e), 'error');
            } finally {
                this.canvas.editing = false;
            }
        },

        /**
         * Put `text` into the document: replacing `replaceRange` when given,
         * otherwise inserting at `atRange` (or the end).
         */
        spliceCanvasText(text, replaceRange, atRange) {
            if (!editors.quill || !text) return;

            let index;
            if (replaceRange && replaceRange.length) {
                editors.quill.deleteText(replaceRange.index, replaceRange.length, 'user');
                index = replaceRange.index;
            } else {
                index = atRange ? atRange.index : editors.quill.getLength() - 1;
            }

            // Render through markdown so **bold**, lists and headings the model
            // emits become real formatting rather than literal characters.
            const html = markdownToHtml(text);
            const isBlock = /<(h[1-6]|ul|ol|blockquote|pre|table)[\s>]/i.test(html);
            if (isBlock) {
                editors.quill.clipboard.dangerouslyPasteHTML(index, html, 'user');
            } else {
                // A single paragraph: paste inline so it stays in the current
                // block instead of being promoted to its own paragraph.
                const inline = html.replace(/^\s*<p>/i, '').replace(/<\/p>\s*$/i, '').trim();
                editors.quill.clipboard.dangerouslyPasteHTML(index, inline, 'user');
            }

            this.canvas.content = editors.quill.root.innerHTML;
            this.updateCanvasWordCount();
            this.markCanvasDirty();

            // Leave the caret after the inserted text.
            const end = index + editors.quill.getText(index).length;
            caret.range = { index: Math.min(end, editors.quill.getLength() - 1), length: 0 };
            caret.selection = null;
        },

        // ── Line-addressed patching (code canvas) ──────────────────────

        /**
         * Ask the model for a minimal patch and apply it to the exact lines.
         *
         * The server returns the already-patched content plus the list of line
         * ranges it touched. We don't just setValue() that content: replacing
         * the whole model would blow away the undo stack and scroll position,
         * and would look identical whether one line changed or all of them.
         * Instead the changed ranges are re-applied as real Monaco edits, so
         * Ctrl+Z works normally and only those lines flash as modified.
         */
        async patchCanvas(instruction) {
            if (!this.canvas.open || this.canvas.editing) return;
            const isDoc = this.canvas.mode === 'document';
            if (!isDoc && !editors.monaco) return;
            if (isDoc && !editors.quill) return;

            const sel = !isDoc && editors.monaco.getSelection();
            const hasSel = sel && !sel.isEmpty();

            this.canvas.editing = true;
            try {
                const res = await fetch(
                    `/api/canvas/${this.currentConvId}/${this.canvas.id}/patch`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            instruction,
                            start_line: hasSel ? sel.startLineNumber : null,
                            end_line: hasSel ? sel.endLineNumber : null
                        })
                    });
                if (!res.ok) {
                    const d = await res.json().catch(() => ({}));
                    throw new Error(d.detail || `patch failed (${res.status})`);
                }
                const { content, applied, rejected, kind } = await res.json();

                let note;
                if (kind === 'block') {
                    this.applyCanvasBlockPatch(content);
                    note = `Rewrote ${applied.length} block${applied.length === 1 ? '' : 's'}`;
                } else {
                    this.applyCanvasPatch(content, applied);
                    const touched = applied.reduce(
                        (n, a) => n + Math.max(a.lines_added, a.lines_removed), 0);
                    note = `Patched ${applied.length} spot${applied.length === 1 ? '' : 's'} (${touched} lines)`;
                }
                if (rejected && rejected.length) note += ` — ${rejected.length} rejected`;
                this.showToast?.(note, rejected && rejected.length ? 'warning' : 'info');
            } catch (e) {
                console.error('[canvas] patch failed', e);
                this.showToast?.(String(e.message || e), 'error');
            } finally {
                this.canvas.editing = false;
            }
        },

        /**
         * Document patches come back as whole HTML rather than ranges: Quill has
         * no line model to splice into, and the server already rebuilt only the
         * edited blocks, leaving the rest byte-identical.
         */
        applyCanvasBlockPatch(newHtml) {
            if (!editors.quill) return;
            const sel = editors.quill.getSelection();

            stream.suppress = true;
            editors.quill.setContents([]);
            editors.quill.clipboard.dangerouslyPasteHTML(newHtml);
            stream.suppress = false;

            this.canvas.content = editors.quill.root.innerHTML;
            this.updateCanvasWordCount();
            this.markCanvasDirty();

            // Put the caret roughly back where it was.
            if (sel) {
                const len = editors.quill.getLength();
                editors.quill.setSelection(Math.min(sel.index, len - 1), 0, 'silent');
            }
        },

        applyCanvasPatch(newContent, applied) {
            if (!editors.monaco) return;
            const model = editors.monaco.getModel();
            if (!model) return;

            stream.suppress = true;
            // pushEditOperations keeps this on the undo stack as ONE undoable step.
            model.pushEditOperations(
                [],
                [{ range: model.getFullModelRange(), text: newContent }],
                () => null
            );
            stream.suppress = false;

            this.canvas.content = newContent;
            this.markCanvasDirty();

            // Reveal and briefly highlight what changed, so the edit is visible.
            if (applied && applied.length) {
                const first = applied[0];
                editors.monaco.revealLineInCenter(first.start_line);
                const decorations = applied.map(a => ({
                    range: {
                        startLineNumber: a.start_line, startColumn: 1,
                        endLineNumber: Math.max(a.start_line, a.start_line + a.lines_added - 1),
                        endColumn: 1
                    },
                    options: { isWholeLine: true, className: 'kokomi-patched-line' }
                }));
                const ids = editors.monaco.deltaDecorations([], decorations);
                setTimeout(() => {
                    try { editors.monaco.deltaDecorations(ids, []); } catch (_) {}
                }, 2200);
            }
        },

        // ── Direct text editing (the icon row on the context menu) ─────

        /** Clipboard/format actions that don't involve the model at all. */
        async canvasFormat(cmd) {
            if (!editors.quill) return;
            const range = caret.selection || caret.range;
            const sel = range && range.length
                ? editors.quill.getText(range.index, range.length) : '';
            this.canvasMenu.open = false;

            try {
                if (cmd === 'copy' || cmd === 'cut') {
                    if (!sel) return;
                    await navigator.clipboard.writeText(sel);
                    if (cmd === 'cut') editors.quill.deleteText(range.index, range.length, 'user');
                } else if (cmd === 'paste') {
                    // Only works over HTTPS/localhost and after a permission grant;
                    // fall back to telling the user rather than failing silently.
                    const text = await navigator.clipboard.readText();
                    if (!text) return;
                    if (range && range.length) editors.quill.deleteText(range.index, range.length, 'user');
                    editors.quill.insertText(range ? range.index : editors.quill.getLength() - 1, text, 'user');
                } else if (cmd === 'delete') {
                    if (range && range.length) editors.quill.deleteText(range.index, range.length, 'user');
                } else {
                    // bold / italic / underline / strike — toggle on the selection
                    if (!range) return;
                    const current = editors.quill.getFormat(range);
                    editors.quill.formatText(range.index, range.length, cmd, !current[cmd], 'user');
                }
            } catch (e) {
                console.error('[canvas] clipboard action failed', e);
                this.showToast?.(
                    cmd === 'paste' ? 'Clipboard read blocked by the browser — use Ctrl+V'
                                    : 'Clipboard action failed', 'error');
                return;
            }

            this.canvas.content = editors.quill.root.innerHTML;
            this.updateCanvasWordCount();
            this.markCanvasDirty();
        },

        // ── Toolbar actions ────────────────────────────────────────────

        copyCanvas() {
            const text = this.canvas.mode === 'document' && editors.quill
                ? editors.quill.getText()
                : this.canvas.content;
            this.copyText?.(text);
        },

        /** Formats offered in the download dropdown, in menu order. */
        get canvasExportFormats() {
            const common = [
                { id: 'docx', label: 'Word document', icon: 'fa-file-word', ext: '.docx' },
                { id: 'pdf', label: 'PDF', icon: 'fa-file-pdf', ext: '.pdf' },
                { id: 'md', label: 'Markdown', icon: 'fa-brands fa-markdown', ext: '.md' },
                { id: 'html', label: 'HTML', icon: 'fa-code', ext: '.html' },
                { id: 'txt', label: 'Plain text', icon: 'fa-file-lines', ext: '.txt' }
            ];
            if (this.canvas.mode === 'code') {
                // A code canvas should offer its own source file first.
                return [{
                    id: 'source', label: 'Source file', icon: 'fa-file-code',
                    ext: '.' + extensionForLanguage(this.canvas.language)
                }, ...common];
            }
            return common;
        },

        /** Default action of the download button: source for code, DOCX for docs. */
        downloadCanvas() {
            this.downloadCanvasAs(this.canvas.mode === 'code' ? 'source' : 'docx');
        },

        async downloadCanvasAs(fmt) {
            this.canvas.exportOpen = false;
            const name = (this.canvas.title || 'canvas').replace(/[^\w.-]+/g, '_');

            // The raw source needs no server round-trip.
            if (fmt === 'source') {
                const blob = new Blob([this.canvas.content], { type: 'text/plain' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${name}.${extensionForLanguage(this.canvas.language)}`;
                a.click();
                URL.revokeObjectURL(url);
                return;
            }

            // Everything else is rendered server-side, so flush edits first or
            // the export would be built from a stale stored copy.
            if (this.canvas.dirty) await this.saveCanvas();

            try {
                const res = await fetch(
                    `/api/canvas/${this.currentConvId}/${this.canvas.id}/export?format=${fmt}`);
                if (!res.ok) throw new Error(`export failed (${res.status})`);
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `${name}.${fmt}`;
                a.click();
                URL.revokeObjectURL(url);
            } catch (e) {
                console.error('[canvas] export failed', e);
                this.showToast?.(`Could not export as ${fmt.toUpperCase()}`, 'error');
            }
        },

        /** Re-theme Monaco when the app theme flips. */
        syncCanvasTheme() {
            if (editors.monaco && window.monaco) {
                window.monaco.editor.setTheme(this.darkMode ? 'vs-dark' : 'vs');
            }
        },

        // ── Split-pane resize ──────────────────────────────────────────

        startCanvasResize(e) {
            e.preventDefault();
            const row = document.getElementById('canvas-split-row');
            if (!row) return;
            const rect = row.getBoundingClientRect();

            const onMove = (ev) => {
                const pct = ((ev.clientX - rect.left) / rect.width) * 100;
                // Keep both panes usable.
                this.canvas.splitPct = Math.min(70, Math.max(25, pct));
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
                localStorage.setItem('canvasSplitPct', String(this.canvas.splitPct));
            };

            document.body.style.cursor = 'col-resize';
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        },

        /**
         * Called when the model re-emits a canvas artifact (same id) — refresh the
         * open editor in place rather than making the user reopen it.
         */
        refreshCanvasFromArtifact(art) {
            if (!this.canvas.open || !art || art.id !== this.canvas.id) return;
            this.canvas.content = art.content || '';

            if (this.canvas.mode === 'code' && editors.monaco) {
                // setValue would reset the cursor/undo stack; only write if changed.
                if (editors.monaco.getValue() !== this.canvas.content) {
                    editors.monaco.setValue(this.canvas.content);
                }
            } else if (this.canvas.mode === 'document' && editors.quill) {
                const raw = this.canvas.content;
                const looksHtml = /^\s*<(p|h[1-6]|ul|ol|blockquote|div|pre|table)[\s>]/i.test(raw);
                editors.quill.clipboard.dangerouslyPasteHTML(looksHtml ? raw : markdownToHtml(raw));
                this.updateCanvasWordCount();
            }
            this.canvas.dirty = false;
        }
    };
}
