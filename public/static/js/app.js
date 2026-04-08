// ─── Markdown ────────────────────────────────────────────────────
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
    return `<div class="code-wrapper">${label}<button class="code-copy-btn" onclick="copyCode(this)">Copy</button><pre><code class="hljs">${hl}</code></pre></div>`;
};
marked.use({ renderer });

function copyCode(btn) {
    const text = btn.nextElementSibling.querySelector('code').textContent;
    navigator.clipboard.writeText(text).then(() => {
        btn.textContent = 'Copied!';
        setTimeout(() => (btn.textContent = 'Copy'), 1500);
    });
}

// ─── App ─────────────────────────────────────────────────────────
function aiApp() {
    return {
        sidebarOpen: true,
        input: '',
        loading: false,
        loadingStatus: 'Thinking...',
        messages: [],
        conversations: [],
        currentConvId: null,
        prefs: { dynamic_suggestions: true },

        // Characters
        characters: [],
        activeCharId: 'kokomi',
        charPickerOpen: false,

        get activeChar() {
            return this.characters.find(c => c.id === this.activeCharId) || { name: 'Kokomi', avatar: null, id: 'kokomi' };
        },

        // ── Init ──
        async init() {
            await Promise.all([this.fetchConversations(), this.fetchCharacters(), this.fetchPrefs()]);
            this.updateSuggestions();
        },

        // ── Theme ──
        get darkMode() {
            return document.documentElement.classList.contains('dark');
        },
        toggleTheme() {
            const d = document.documentElement;
            const isDark = d.classList.toggle('dark');
            localStorage.setItem('theme', isDark ? 'dark' : 'light');
            document.getElementById('hljs-theme').href =
                `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/${isDark ? 'github-dark' : 'github'}.min.css`;
        },

        // ── Quick prompts ──
        quickPrompts: [
            { icon: 'fa-solid fa-code',       label: 'Write Code',  text: 'Write a Python function to parse JSON and handle errors.' },
            { icon: 'fa-solid fa-brain',      label: 'Explain',     text: 'Explain how transformer attention mechanisms work.' },
            { icon: 'fa-solid fa-pen-nib',    label: 'Draft',       text: 'Write a professional follow-up email for a job application.' },
            { icon: 'fa-solid fa-chart-line', label: 'Analyze',     text: 'What are the best practices for data visualization?' },
        ],

        get currentTitle() {
            if (!this.currentConvId) return 'New Conversation';
            const c = this.conversations.find(x => x._id === this.currentConvId);
            return c ? c.title : 'Chat';
        },

        // ── Characters ─────────────────────────────────────────
        async fetchCharacters() {
            try {
                const r = await fetch('/api/characters');
                if (r.ok) this.characters = await r.json();
            } catch (e) { console.warn('Characters unavailable', e); }
        },

        selectCharacter(id) {
            this.activeCharId = id;
            this.charPickerOpen = false;
            this.updateSuggestions();
        },

        async updateSuggestions() {
            if (this.prefs.dynamic_suggestions === false) {
                this.quickPrompts = [
                    { icon: 'fa-solid fa-code', label: 'Write Code', text: 'Write a Python function to solve a complex coding problem.' },
                    { icon: 'fa-solid fa-brain', label: 'Explain', text: 'Explain a difficult concept in simple terms.' },
                    { icon: 'fa-solid fa-pen-nib', label: 'Draft', text: 'Write a professional email or letter for a specific scenario.' },
                    { icon: 'fa-solid fa-chart-line', label: 'Analyze', text: 'Analyze this situation and provide key insights.' }
                ];
                return;
            }

            const id = this.activeCharId;
            try {
                const r = await fetch(`/api/characters/${id}/suggestions`);
                if (r.ok) {
                    this.quickPrompts = await r.json();
                }
            } catch (e) {
                console.warn('Failed to fetch suggestions', e);
                // Fallback to defaults
                this.quickPrompts = [
                    { icon: 'fa-solid fa-code', label: 'Write Code', text: 'Write a Python function to solve a complex coding problem.' },
                    { icon: 'fa-solid fa-brain', label: 'Explain', text: 'Explain a difficult concept in simple terms.' },
                    { icon: 'fa-solid fa-pen-nib', label: 'Draft', text: 'Write a professional email or letter for a specific scenario.' },
                    { icon: 'fa-solid fa-chart-line', label: 'Analyze', text: 'Analyze this situation and provide key insights.' }
                ];
            }
        },

        // ── Conversations ──────────────────────────────────────
        async fetchConversations() {
            try {
                const r = await fetch('/api/conversations');
                if (r.ok) this.conversations = await r.json();
            } catch (e) { console.warn('Could not load conversations', e); }
        },

        async fetchPrefs() {
            try {
                const r = await fetch('/api/prefs');
                if (r.ok) this.prefs = await r.json();
            } catch (e) { console.warn('Prefs unavailable', e); }
        },

        async loadConversation(id) {
            if (this.currentConvId === id) return;
            try {
                const r = await fetch(`/api/conversations/${id}`);
                if (!r.ok) throw new Error(r.status);
                const doc = await r.json();
                this.currentConvId = id;
                this.messages = doc.messages || [];
                // Switch to the character used in this conversation
                if (doc.character_id) this.activeCharId = doc.character_id;
                this.$nextTick(() => this.scrollToBottom());
            } catch (e) { console.error('Load failed:', e); }
        },

        async deleteConversation(id) {
            try {
                await fetch(`/api/conversations/${id}`, { method: 'DELETE' });
                if (this.currentConvId === id) {
                    this.messages = [];
                    this.currentConvId = null;
                }
                this.conversations = this.conversations.filter(c => c._id !== id);
            } catch (e) { console.error('Delete failed:', e); }
        },

        newChat() {
            this.messages = [];
            this.currentConvId = null;
            this.input = '';
        },

        // ── Send ───────────────────────────────────────────────
        async sendMessage() {
            const text = this.input.trim();
            if (!text || this.loading) return;

            this.messages.push({ role: 'user', content: text });
            this.input = '';
            this.loading = true;
            this.loadingStatus = 'Thinking...';
            if (this.$refs.textarea) this.$refs.textarea.style.height = 'auto';
            this.$nextTick(() => this.scrollToBottom());

            if (this.prefs.streaming_mode) {
                await this.sendMessageStream(text);
                return;
            }

            const timer = setTimeout(() => { this.loadingStatus = 'Composing response...'; }, 3000);

            try {
                const r = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: text,
                        conversation_id: this.currentConvId,
                        character_id: this.activeCharId,
                    }),
                });
                clearTimeout(timer);

                if (!r.ok) {
                    const e = await r.json().catch(() => ({}));
                    throw new Error(e.detail || `Server error ${r.status}`);
                }

                const data = await r.json();

                this.messages.push({
                    role: 'assistant',
                    content: data.response,
                    thinking: data.thinking || null,
                    tool_calls: data.tool_calls || null,
                    model: data.model || null,
                });

                if (!this.currentConvId && data.conversation_id) {
                    this.currentConvId = data.conversation_id;
                }
                await this.fetchConversations();

            } catch (err) {
                clearTimeout(timer);
                this.messages.push({
                    role: 'assistant',
                    content: `**Error:** ${err.message}`,
                    thinking: null,
                });
            } finally {
                this.loading = false;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        async sendMessageStream(msg) {
            this.messages.push({ 
                role: 'assistant', 
                content: '', 
                thinking: '', 
                model: this.prefs.model_name, 
                timestamp: new Date().toISOString(),
                streaming: true 
            });
            const msgIdx = this.messages.length - 1;

            try {
                const response = await fetch('/api/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: msg,
                        character_id: this.activeCharId,
                        conversation_id: this.currentConvId
                    })
                });

                if (!response.ok) throw new Error("Failed to start stream");

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    const chunk = decoder.decode(value, { stream: true });
                    buffer += chunk;
                    
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        const trimmedLine = line.trim();
                        if (!trimmedLine || !trimmedLine.startsWith('data: ')) continue;
                        
                        const jsonStr = trimmedLine.slice(6);
                        if (jsonStr === '[DONE]') break;
                        
                        try {
                            const data = JSON.parse(jsonStr);
                            if (data.type === 'content' && data.delta) {
                                this.messages[msgIdx].content += data.delta;
                                this.parseStreamingThinking(this.messages[msgIdx]);
                            } else if (data.type === 'tool_start') {
                                this.loadingStatus = `Running ${data.name}...`;
                            } else if (data.type === 'done') {
                                this.currentConvId = data.conversation_id;
                                if (data.title) this.fetchConversations();
                            } else if (data.type === 'error') {
                                this.messages[msgIdx].content += `\n\n**Error:** ${data.message}`;
                            }
                        } catch(e) { }
                    }
                    // Trigger Alpine reactivity (slice preserves object refs)
                    this.messages = this.messages.slice();
                    this.$nextTick(() => this.scrollToBottom());
                }
            } catch (e) {
                console.error("Stream reader error:", e);
                this.messages[msgIdx].content += `\n\n**Connection Error:** ${e.message}`;
            } finally {
                this.messages[msgIdx].streaming = false;
                this.loading = false;
                this.fetchConversations();
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        parseStreamingThinking(msg) {
            const content = msg.content;
            const thinkStart = content.indexOf('<think>');
            const thinkEnd = content.indexOf('</think>');
            
            if (thinkStart !== -1) {
                const preThink = content.slice(0, thinkStart).trim();
                if (thinkEnd !== -1) {
                    msg.thinking = content.slice(thinkStart + 7, thinkEnd).trim();
                    const postThink = content.slice(thinkEnd + 8).trim();
                    msg.displayContent = (preThink ? preThink + '\n\n' : '') + postThink;
                } else {
                    msg.thinking = content.slice(thinkStart + 7).trim();
                    msg.displayContent = preThink;
                }
            } else {
                msg.displayContent = content;
            }
        },

        // ── Helpers ────────────────────────────────────────────
        setInput(t) {
            this.input = t;
            this.$nextTick(() => {
                if (this.$refs.textarea) { this.$refs.textarea.focus(); this.autoResize(); }
            });
        },
        renderMarkdown(c) {
            if (!c) return '';
            try { return marked.parse(c); } catch { return c; }
        },
        autoResize() {
            const el = this.$refs.textarea;
            if (!el) return;
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 200) + 'px';
        },
        scrollToBottom() {
            document.getElementById('bottom-anchor')?.scrollIntoView({ behavior: 'smooth' });
        },
        async copyText(t) { try { await navigator.clipboard.writeText(t); } catch {} },
    };
}
