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

renderer.table = (obj) => {
    // marked v4+ returns token with header and rows
    const header = obj.header.map(cell => `<th>${marked.parseInline(cell.text)}</th>`).join('');
    const rows = obj.rows.map(row => `<tr>${row.map(cell => `<td>${marked.parseInline(cell.text)}</td>`).join('')}</tr>`).join('');
    return `<div class="table-wrapper"><table><thead><tr>${header}</tr></thead><tbody>${rows}</tbody></table></div>`;
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
        sidebarOpen: window.innerWidth > 768,
        input: '',
        loading: false,
        loadingStatus: 'Thinking...',
        messages: [],
        conversations: [],
        folders: [],
        openFolders: JSON.parse(localStorage.getItem('openFolders') || '[]'),
        currentConvId: null,
        projectModal: { show: false, id: null, name: '', icon: 'fa-solid fa-folder' },
        iconChoices: [
            'fa-solid fa-folder', 'fa-solid fa-briefcase', 'fa-solid fa-code', 
            'fa-solid fa-book', 'fa-solid fa-graduation-cap', 'fa-solid fa-user-group',
            'fa-solid fa-star', 'fa-solid fa-heart', 'fa-solid fa-bolt', 
            'fa-solid fa-lightbulb', 'fa-solid fa-compass', 'fa-solid fa-flask',
            'fa-solid fa-music', 'fa-solid fa-palette', 'fa-solid fa-microchip'
        ],
        prefs: { dynamic_suggestions: true },
        abortController: null,
        spaces: [],
        activeSpaceId: null,

        // Characters
        characters: [],
        activeCharId: localStorage.getItem('lastCharacterId') || 'kokomi',
        charPickerOpen: false,
        welcomePickerOpen: false,
        groupParticipants: [],
        roomPickerOpen: false,
        currentStreamingCharId: null,
        toasts: [],

        get activeChar() {
            return this.characters.find(c => c.id === this.activeCharId) || { name: 'Kokomi', avatar: null, id: 'kokomi' };
        },

        get isGroupChat() {
            return this.groupParticipants.length > 1;
        },

        get roomLabel() {
            if (!this.isGroupChat) return this.activeChar.name;
            return this.groupParticipants
                .map(pid => this.characters.find(c => c.id === pid)?.name || pid)
                .join(', ');
        },

        getCharById(id) {
            return this.characters.find(c => c.id === id) || { name: id, avatar: null, id };
        },

        // ── Init ──
        async init() {
            await Promise.all([
                this.fetchConversations(), 
                this.fetchFolders(), 
                this.fetchCharacters(), 
                this.fetchPrefs(),
                this.fetchSpaces()
            ]);
            // Initialize room with active character
            if (this.groupParticipants.length === 0) {
                this.groupParticipants = [this.activeCharId];
            }
            this.updateSuggestions();
        },

        async fetchSpaces() {
            try {
                const r = await fetch('/api/spaces');
                if (r.ok) this.spaces = await r.json();
            } catch(e) { console.error(e); }
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
            localStorage.setItem('lastCharacterId', id);
            this.charPickerOpen = false;
            this.welcomePickerOpen = false;
            
            // Add to room if not present
            if (!this.groupParticipants.includes(id)) {
                this.groupParticipants = [id];
            }
            
            this.updateSuggestions();
        },

        toggleParticipant(id) {
            const idx = this.groupParticipants.indexOf(id);
            if (idx === -1) {
                this.groupParticipants.push(id);
            } else {
                if (this.groupParticipants.length > 1) {
                    this.groupParticipants.splice(idx, 1);
                    // If we removed the active char, switch to first remaining
                    if (this.activeCharId === id) {
                        this.activeCharId = this.groupParticipants[0];
                        localStorage.setItem('lastCharacterId', this.activeCharId);
                    }
                } else {
                    this.showToast("At least one character must stay in the room.", "warning");
                }
            }
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

        async fetchFolders() {
            try {
                const r = await fetch('/api/folders');
                if (r.ok) this.folders = await r.json();
            } catch (e) { console.warn('Could not load folders', e); }
        },

        toggleFolder(id) {
            if (this.openFolders.includes(id)) {
                this.openFolders = this.openFolders.filter(x => x !== id);
            } else {
                this.openFolders.push(id);
            }
            localStorage.setItem('openFolders', JSON.stringify(this.openFolders));
        },

        isFolderOpen(id) {
            return this.openFolders.includes(id);
        },

        openProjectModal(folder = null) {
            if (folder) {
                this.projectModal = { show: true, id: folder.id, name: folder.name, icon: folder.icon || 'fa-solid fa-folder' };
            } else {
                this.projectModal = { show: true, id: null, name: '', icon: 'fa-solid fa-folder' };
            }
        },

        async saveProject() {
            const { id, name, icon } = this.projectModal;
            if (!name) return;
            try {
                const method = id ? 'PUT' : 'POST';
                const url = id ? `/api/folders/${id}` : '/api/folders';
                await fetch(url, {
                    method,
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name, icon })
                });
                this.projectModal.show = false;
                await this.fetchFolders();
            } catch (e) { console.error('Save project failed', e); }
        },

        async deleteFolder(fid) {
            if (!confirm('Delete this project and unarchive chats?')) return;
            try {
                await fetch(`/api/folders/${fid}`, { method: 'DELETE' });
                await this.fetchFolders();
                await this.fetchConversations();
            } catch (e) { console.error('Delete project failed', e); }
        },

        get conversationsByFolder() {
            const grouped = { 'none': [] };
            this.folders.forEach(f => grouped[f.id] = []);
            
            this.conversations.forEach(c => {
                const fid = c.folder_id || 'none';
                if (!grouped[fid]) grouped[fid] = [];
                grouped[fid].push(c);
            });
            return grouped;
        },

        async createFolder(name) {
            if (!name) return;
            try {
                await fetch('/api/folders', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name })
                });
                await this.fetchFolders();
            } catch (e) { console.error('Folder creation failed', e); }
        },

        async assignToFolder(convId, folderId) {
            try {
                await fetch(`/api/conversations/${convId}/folder`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folder_id: folderId })
                });
                await this.fetchConversations();
            } catch (e) { console.error('Move failed', e); }
        },

        async loadConversation(id) {
            if (this.currentConvId === id) return;
            try {
                const r = await fetch(`/api/conversations/${id}`);
                if (!r.ok) throw new Error(r.status);
                const doc = await r.json();
                this.currentConvId = id;
                this.messages = doc.messages || [];
                // Restore group participants from saved conversation
                this.groupParticipants = doc.participants || [doc.character_id || 'kokomi'];
                if (doc.character_id) this.activeCharId = doc.character_id;
                else if (this.groupParticipants.length > 0) this.activeCharId = this.groupParticipants[0];
                
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
            this.currentStreamingCharId = null;
            // Keep current room participants
            if (this.groupParticipants.length === 0) {
                this.groupParticipants = [this.activeCharId];
            }
            this.$nextTick(() => { this.autoResize(); document.getElementById('user-input')?.focus(); });
        },

        // ── Send ───────────────────────────────────────────────
        async sendMessage() {
            const text = this.input.trim();
            if (!text || this.loading) return;

            this.messages.push({ role: 'user', content: text });
            this.input = '';
            this.loading = true;
            this.loadingStatus = 'Thinking...';
            this.abortController = new AbortController();
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
                        space_id: this.activeSpaceId,
                    }),
                    signal: this.abortController.signal,
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
                    content: err.name === 'AbortError' ? '_Generation stopped._' : `**Error:** ${err.message}`,
                    thinking: null,
                });
            } finally {
                this.loading = false;
                this.abortController = null;
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        async sendMessageStream(msg) {
            // Don't pre-create a bubble — let the stream events create them per character_id
            const firstMsgIdx = this.messages.length; // Track where group responses start

            try {
                const response = await fetch('/api/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: msg,
                        character_id: this.activeCharId,
                        conversation_id: this.currentConvId,
                        participants: this.groupParticipants,
                        space_id: this.activeSpaceId
                    }),
                    signal: this.abortController.signal
                });

                if (!response.ok) throw new Error("Failed to start stream");

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                // Track which character_id maps to which message index
                let charMsgMap = {};

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
                            const charId = data.character_id || this.activeCharId;
                            
                            // Resolve which message index this character writes to
                            let targetIdx = charMsgMap[charId];
                            if (targetIdx === undefined && (data.type === 'content' || data.type === 'reasoning')) {
                                // Create a new message bubble for this character
                                const char = this.characters.find(c => c.id === charId) || { name: charId, id: charId };
                                this.messages.push({
                                    role: 'assistant',
                                    character_id: charId,
                                    character_name: char.name,
                                    content: '',
                                    thinking: '',
                                    displayContent: '',
                                    model: this.prefs.model_name,
                                    timestamp: new Date().toISOString(),
                                    streaming: true
                                });
                                targetIdx = this.messages.length - 1;
                                charMsgMap[charId] = targetIdx;
                                this.currentStreamingCharId = charId;
                                this.loadingStatus = `${char.name} is thinking...`;
                            }

                            if (data.type === 'content' && data.delta && targetIdx !== undefined) {
                                this.messages[targetIdx].content += data.delta;
                                this.parseStreamingThinking(this.messages[targetIdx]);
                            } else if (data.type === 'reasoning' && data.delta && targetIdx !== undefined) {
                                this.messages[targetIdx].thinking += data.delta;
                            } else if (data.type === 'tool_start') {
                                const charName = this.getCharById(charId).name;
                                this.loadingStatus = `${charName}: Running ${data.name}...`;
                                if (targetIdx !== undefined) {
                                    if (!this.messages[targetIdx].tool_calls) this.messages[targetIdx].tool_calls = [];
                                    this.messages[targetIdx].tool_calls.push({ name: data.name, result: "Executing..." });
                                }
                            } else if (data.type === 'tool_end') {
                                if (targetIdx !== undefined && this.messages[targetIdx].tool_calls) {
                                    const tcs = this.messages[targetIdx].tool_calls;
                                    if (tcs.length > 0) {
                                        tcs[tcs.length - 1].result = data.result;
                                    }
                                }
                            } else if (data.type === 'warning') {
                                this.showToast(data.message, 'warning');
                            } else if (data.type === 'done') {
                                this.currentConvId = data.conversation_id;
                                if (data.title) this.fetchConversations();
                            } else if (data.type === 'error') {
                                if (targetIdx !== undefined) {
                                    this.messages[targetIdx].content += `\n\n**Error:** ${data.message}`;
                                } else {
                                    this.showToast(data.message, 'error');
                                }
                            }
                        } catch(e) { console.error('Stream parse error:', e); }
                    }
                    // Trigger Alpine reactivity
                    this.messages = this.messages.slice();
                    this.$nextTick(() => this.scrollToBottom());
                }
            } catch (e) {
                console.error("Stream reader error:", e);
                if (e.name === 'AbortError') {
                    this.showToast('Generation stopped.', 'info');
                } else {
                    this.showToast(`Connection error: ${e.message}`, 'error');
                }
            } finally {
                // Mark ALL streaming messages as done + clean up name prefixes
                this.messages.forEach(m => { 
                    if (m.streaming) {
                        m.streaming = false;
                        // Strip character name prefix from content
                        if (m.character_name && m.content) {
                            const prefixes = [
                                `[${m.character_name}]: `, `[${m.character_name}]:`,
                                `${m.character_name}: `, `${m.character_name}:`
                            ];
                            for (const p of prefixes) {
                                while (m.content.startsWith(p)) {
                                    m.content = m.content.slice(p.length).trim();
                                }
                            }
                            // Also clean displayContent
                            if (m.displayContent) {
                                for (const p of prefixes) {
                                    while (m.displayContent.startsWith(p)) {
                                        m.displayContent = m.displayContent.slice(p.length).trim();
                                    }
                                }
                            }
                        }
                    }
                });
                this.messages = this.messages.slice();
                this.loading = false;
                this.currentStreamingCharId = null;
                this.abortController = null;
                this.fetchConversations();
                this.$nextTick(() => this.scrollToBottom());
            }
        },

        parseStreamingThinking(msg) {
            const content = msg.content;
            const thinkTag = '<think>';
            const thinkEndTag = '</think>';
            
            const thinkStart = content.indexOf(thinkTag);
            const thinkEnd = content.indexOf(thinkEndTag);
            
            if (thinkStart !== -1) {
                const preThink = content.slice(0, thinkStart).trim();
                if (thinkEnd !== -1) {
                    msg.thinking = content.slice(thinkStart + thinkTag.length, thinkEnd).trim();
                    const postThink = content.slice(thinkEnd + thinkEndTag.length).trim();
                    msg.displayContent = (preThink ? preThink + '\n\n' : '') + postThink;
                } else {
                    let thinking = content.slice(thinkStart + thinkTag.length);
                    const lastTagStart = thinking.lastIndexOf('</');
                    if (lastTagStart !== -1) {
                        const partial = thinking.slice(lastTagStart);
                        if (thinkEndTag.startsWith(partial)) {
                            thinking = thinking.slice(0, lastTagStart);
                        }
                    }
                    msg.thinking = thinking.trim();
                    msg.displayContent = preThink;
                }
            } else {
                let showContent = content;
                const lastTagStart = content.lastIndexOf('<');
                if (lastTagStart !== -1) {
                    const partial = content.slice(lastTagStart);
                    if (thinkTag.startsWith(partial)) {
                        showContent = content.slice(0, lastTagStart);
                    }
                }
                msg.displayContent = showContent;
            }
        },

        async regenerate(index) {
            if (this.loading) return;
            let userMsgIndex = -1;
            for (let i = index - 1; i >= 0; i--) {
                if (this.messages[i].role === 'user') {
                    userMsgIndex = i;
                    break;
                }
            }
            if (userMsgIndex === -1) return;

            const userText = this.messages[userMsgIndex].content;
            
            if (this.currentConvId) {
                try {
                    await fetch(`/api/conversations/${this.currentConvId}/pop`, { method: 'POST' });
                } catch (e) { console.error("Could not pop messages on server", e); }
            }

            this.messages = this.messages.slice(0, userMsgIndex);
            this.input = userText;
            await this.sendMessage();
        },

        async continueGeneration(index) {
            if (this.loading) return;
            this.input = "[Continue the response]";
            await this.sendMessage();
        },
        async deleteMessage(index) {
            if (this.currentConvId) {
                try {
                    await fetch(`/api/conversations/${this.currentConvId}/messages/${index}`, { method: 'DELETE' });
                } catch (e) {
                    console.error("Message deletion failed", e);
                }
            }
            this.messages.splice(index, 1);
        },
        
        stopGeneration() {
            if (this.abortController) {
                this.abortController.abort();
            }
        },

        // ── Helpers ────────────────────────────────────────────
        setInput(t) {
            this.input = t;
            this.$nextTick(() => {
                if (this.$refs.textarea) { this.$refs.textarea.focus(); this.autoResize(); }
            });
        },
        renderMarkdown(c, isStreaming = false) {
            if (!c) return '';
            try {
                let html = marked.parse(c);
                if (isStreaming) {
                    const fish = '<span class="fish-typing"><i class="fa-solid fa-fish-fins"></i></span>';
                    if (html.includes('</p>')) {
                        const parts = html.split('</p>');
                        const last = parts.pop();
                        html = parts.join('</p>') + ' ' + fish + '</p>' + last;
                    } else {
                        html += ' ' + fish;
                    }
                }
                return html;
            } catch { return c; }
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
        showToast(message, type = 'info') {
            const id = Date.now();
            this.toasts.push({ id, message, type });
            setTimeout(() => {
                this.toasts = this.toasts.filter(t => t.id !== id);
            }, 5000);
        }
    };
}
