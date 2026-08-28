/**
 * Chat Logic, Streaming and Message Handling
 */

import { parseWithMath } from './markdown.js';
import { isCanvasArtifact } from './canvas.js';

export function getChatActions() {
    const actions = {
        async sendMessage() {
            const text = this.input.trim();
            if (!text || this.loading) return;

            // Flush unsaved canvas edits first — the backend reads the stored
            // artifact to show the model what the user is looking at, so a
            // pending debounce would send it a stale version.
            if (this.canvas.open && this.canvas.dirty) {
                await this.saveCanvas();
            }

            this.pendingQuestion = null;
            const currentAttachments = [...this.attachments];
            this.messages.push({
                id: 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                role: 'user', 
                content: text,
                attachments: currentAttachments
            });
            this.input = '';
            this.attachments = [];
            this.loading = true;
            this.loadingStatus = 'Thinking...';
            this.abortController = new AbortController();
            if (this.$refs.textarea) this.$refs.textarea.style.height = 'auto';
            this.$nextTick(() => this.scrollToBottom());

            if (this.prefs.streaming_mode) {
                await this.sendMessageStream(text, currentAttachments);
                return;
            }

            const timer = setTimeout(() => { this.loadingStatus = 'Composing response...'; }, 3000);

            if (this.prefs.debug_mode) {
                console.log(`[DEBUG] Sending non-stream chat req. Conv: ${this.currentConvId}, Space: ${this.activeSpaceId}`);
                console.time('ChatRequest_NonStream');
            }

            try {
                const r = await fetch('/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: text,
                        conversation_id: this.currentConvId,
                        character_id: this.activeCharId,
                        space_id: this.activeSpaceId,
                        is_anonymous: this.isAnonymous,
                        use_web_search: this.useWebSearch,
                        attachments: currentAttachments,
                        canvas_id: this.canvas.open ? this.canvas.id : null,
                        model_tier: this.modelTier
                    }),
                    signal: this.abortController.signal,
                });

                clearTimeout(timer);

                if (this.prefs.debug_mode) console.timeEnd('ChatRequest_NonStream');

                if (!r.ok) {
                    const e = await r.json().catch(() => ({}));
                    throw new Error(e.detail || `Server error ${r.status}`);
                }

                const data = await r.json();

                this.messages.push({
                    id: 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
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

        async sendMessageStream(msg, attachments = []) {
            if (this.prefs.debug_mode) {
                console.log(`[DEBUG] Sending stream chat req. Conv: ${this.currentConvId}, Space: ${this.activeSpaceId}`);
                console.time('ChatRequest_Stream_TTFB');
                console.time('ChatRequest_Stream_Total');
            }
            try {
                const response = await fetch('/api/chat/stream', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: msg,
                        character_id: this.activeCharId,
                        conversation_id: this.currentConvId,
                        participants: this.groupParticipants,
                        space_id: this.activeSpaceId,
                        is_anonymous: this.isAnonymous,
                        use_web_search: this.useWebSearch,
                        attachments: attachments,
                        canvas_id: this.canvas.open ? this.canvas.id : null,
                        model_tier: this.modelTier
                    }),
                    signal: this.abortController.signal
                });

                if (!response.ok) throw new Error("Failed to start stream");

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let charMsgMap = {};

                let firstChunkReceived = false;

                while (true) {
                    const { done, value } = await reader.read();
                    if (!firstChunkReceived && this.prefs.debug_mode) {
                        console.timeEnd('ChatRequest_Stream_TTFB');
                        firstChunkReceived = true;
                    }
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
                            
                            let targetIdx = charMsgMap[charId];
                            if (targetIdx === undefined && (data.type === 'content' || data.type === 'reasoning' || data.type === 'tool_start')) {
                                const char = this.characters.find(c => c.id === charId) || { name: charId, id: charId };
                                this.messages.push({
                                    id: 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                                    role: 'assistant',
                                    character_id: charId,
                                    character_name: char.name,
                                    content: '',
                                    thinking: '',
                                    displayContent: '',
                                    model: data.model || this.prefs.model_name,
                                    timestamp: new Date().toISOString(),
                                    streaming: true,
                                    debug_logs: []
                                });
                                targetIdx = this.messages.length - 1;
                                charMsgMap[charId] = targetIdx;
                                this.currentStreamingCharId = charId;
                                this.loadingStatus = `${char.name} is thinking...`;
                            }

                            if (data.type === 'stats') {
                                this.liveStats = {
                                    tps: data.tps,
                                    ttft: data.ttft,
                                    context: data.context
                                };
                            }

                            if (data.type === 'debug' && targetIdx !== undefined) {
                                this.messages[targetIdx].debug_logs.push(data.message);
                            }

                            if (data.type === 'content' && data.delta && targetIdx !== undefined) {
                                if (data.model) this.messages[targetIdx].model = data.model;
                                this.messages[targetIdx].content += data.delta;
                                this.parseStreamingThinking(this.messages[targetIdx]);
                            } else if (data.type === 'reasoning' && data.delta && targetIdx !== undefined) {
                                if (data.model) this.messages[targetIdx].model = data.model;
                                this.messages[targetIdx].thinking += data.delta;
                            } else if (data.type === 'tool_start') {
                                if (data.name !== 'open_url' && data.name !== 'redirect_url') {
                                    const charName = this.getCharById(charId).name;
                                    this.loadingStatus = `${charName}: ${data.description || ('Running ' + data.name)}...`;
                                    if (targetIdx !== undefined) {
                                        if (!this.messages[targetIdx].tool_calls) this.messages[targetIdx].tool_calls = [];
                                        this.messages[targetIdx].tool_calls.push({ 
                                            name: data.name, 
                                            icon: data.icon || 'fa-wrench', 
                                            description: data.description,
                                            result: "Executing..." 
                                         });
                                    }
                                }
                            } else if (data.type === 'artifact_open') {
                                if (targetIdx !== undefined) {
                                    if (!this.messages[targetIdx].artifacts) this.messages[targetIdx].artifacts = [];
                                    const meta = data.metadata || {};
                                    this.messages[targetIdx].artifacts.push({
                                        id: data.id,
                                        title: meta.title || 'Untitled Artifact',
                                        type: meta.type || 'file',
                                        icon: meta.icon || 'fa-solid fa-file-code',
                                        // Canvas artifacts carry the editor mode + language
                                        mode: meta.mode || null,
                                        language: meta.language || null,
                                        content: '',
                                        streaming: true
                                    });
                                    // Open the canvas right away so the model's
                                    // writing types out live instead of appearing
                                    // all at once when it finishes.
                                    const justAdded = this.messages[targetIdx].artifacts[
                                        this.messages[targetIdx].artifacts.length - 1];
                                    if (isCanvasArtifact(justAdded)) {
                                        this.beginCanvasStream(justAdded);
                                    }
                                }
                            } else if (data.type === 'artifact_chunk') {
                                if (targetIdx !== undefined && this.messages[targetIdx].artifacts) {
                                    const arts = this.messages[targetIdx].artifacts;
                                    const art = arts.find(a => a.id === data.id);
                                    if (art) {
                                        art.content += data.delta;
                                        if (isCanvasArtifact(art)) {
                                            this.streamCanvasChunk(art.id, data.delta);
                                        }
                                        // Update modal in real-time if open
                                        if (this.artifactModal.show && this.artifactModal.id === data.id) {
                                            this.artifactModal.content = art.content;
                                            this.renderArtifactInModal();
                                            // Trigger live preview update if on preview tab
                                            if (this.artifactModal.tab === 'preview') {
                                                this.updateLivePreview(art.content);
                                            }
                                        }
                                    }
                                }
                            } else if (data.type === 'artifact_close') {
                                if (targetIdx !== undefined && this.messages[targetIdx].artifacts) {
                                    const arts = this.messages[targetIdx].artifacts;
                                    const art = arts.find(a => a.id === data.id);
                                    if (art) {
                                        art.streaming = false;
                                        if (data.content) art.content = data.content;
                                        // Final update for modal
                                        if (this.artifactModal.show && this.artifactModal.id === data.id) {
                                            this.artifactModal.content = art.content;
                                            this.renderArtifactInModal();
                                        }
                                        // A finished "question" artifact becomes the live overlay
                                        // above the composer instead of an inline card.
                                        if ((art.type || '').toLowerCase() === 'question') {
                                            this.evaluatePendingQuestion();
                                        }
                                        // A canvas opens the side-by-side editor. If that same
                                        // canvas is already open, the model just revised it —
                                        // refresh in place rather than remounting the editor.
                                        if (isCanvasArtifact(art)) {
                                            if (this.canvas.open && this.canvas.id === art.id) {
                                                // Was streaming live, or the model
                                                // revised an already-open canvas.
                                                if (this.canvas.streaming) this.endCanvasStream(art);
                                                else this.refreshCanvasFromArtifact(art);
                                            } else {
                                                this.openCanvas(art);
                                            }
                                        }
                                    }
                                }
                            } else if (data.type === 'tool_end') {
                                if (data.name === 'open_url' || data.name === 'redirect_url') {
                                    if (data.args && data.args.url) {
                                        window.open(data.args.url, '_blank');
                                        this.showToast(`Opened ${data.args.url}`, 'info');
                                    }
                                } else if (targetIdx !== undefined && this.messages[targetIdx].tool_calls) {
                                    const tcs = this.messages[targetIdx].tool_calls;
                                    if (tcs.length > 0) {
                                        tcs[tcs.length - 1].result = data.result;
                                        if (data.description) {
                                            tcs[tcs.length - 1].description = data.description;
                                        }
                                    }
                                }
                            } else if (data.type === 'warning') {
                                this.showToast(data.message, 'warning');
                            } else if (data.type === 'done') {
                                this.currentConvId = data.conversation_id;
                                window.location.hash = `chat=${data.conversation_id}`;
                                if (data.metrics && targetIdx !== undefined) {
                                    this.messages[targetIdx].metrics = data.metrics;
                                }
                                this.liveStats = { tps: null, ttft: null, context: null };
                            }
                            else if (data.type === 'error') {
                                if (targetIdx !== undefined) {
                                    this.messages[targetIdx].content += `\n\n**Error:** ${data.message}`;
                                } else {
                                    this.showToast(data.message, 'error');
                                }
                            }
                        } catch(e) { console.error('Stream parse error:', e); }
                    }
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
                this.messages.forEach(m => { 
                    if (m.streaming) {
                        m.streaming = false;
                        if (m.character_name && m.content) {
                            const prefixes = [
                                `[${m.character_name}]: `, `[${m.character_name}]:`,
                                `${m.character_name}: `, `${m.character_name}:`
                            ];
                            for (const p of prefixes) {
                                while (m.content.startsWith(p)) m.content = m.content.slice(p.length).trim();
                                if (m.displayContent) {
                                    while (m.displayContent.startsWith(p)) m.displayContent = m.displayContent.slice(p.length).trim();
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
                if (this.prefs.debug_mode) console.timeEnd('ChatRequest_Stream_Total');
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
                    msg.thinking = thinking.trim();
                    msg.displayContent = preThink;
                }
            } else {
                msg.displayContent = content;
            }
        },

        // Shared by regenerate() and saveEditMessage(): archive messages[index:]
        // as a branch variant (server-side, non-destructive), truncate locally,
        // resend `newText` through the normal flow, then stamp the resulting new
        // variant with the branch's group_id so nav arrows appear immediately.
        async _branchAndResend(index, newText) {
            let groupId = null;
            if (this.currentConvId) {
                try {
                    const r = await fetch(`/api/conversations/${this.currentConvId}/messages/${index}/branch`, { method: 'POST' });
                    if (r.ok) groupId = (await r.json()).group_id;
                } catch (e) { console.error("Branch archive failed", e); }
            }
            this.messages = this.messages.slice(0, index);
            this.input = newText;
            await this.sendMessage();

            if (groupId && this.currentConvId && this.messages[index]) {
                try {
                    const r = await fetch(`/api/conversations/${this.currentConvId}/messages/${index}/attach-group`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ group_id: groupId }),
                    });
                    if (r.ok) {
                        const meta = await r.json();
                        this.messages[index].group_id = meta.group_id;
                        this.messages[index].branch_index = meta.branch_index;
                        this.messages[index].branch_count = meta.branch_count;
                        this.messages = this.messages.slice();
                    }
                } catch (e) { console.error("attach-group failed", e); }
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
            await this._branchAndResend(userMsgIndex, userText);
        },

        // ── Inline message editing (ChatGPT-style) ───────────────────────────
        startEditMessage(index) {
            this.editingIndex = index;
            this.editDraft = this.messages[index].content;
            this.$nextTick(() => {
                const ta = document.getElementById('edit-textarea-' + index);
                if (ta) { ta.focus(); ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; ta.setSelectionRange(ta.value.length, ta.value.length); }
            });
        },

        cancelEditMessage() {
            this.editingIndex = null;
            this.editDraft = '';
        },

        async saveEditMessage(index) {
            const draft = this.editDraft.trim();
            if (!draft || this.loading) return;
            const unchanged = draft === this.messages[index].content;
            this.editingIndex = null;
            this.editDraft = '';
            if (unchanged) return;
            await this._branchAndResend(index, draft);
        },

        // Navigate to the previous/next branch variant at this message's
        // position. Instant — no LLM call, just swaps stored content back in.
        async switchBranch(index, direction) {
            if (!this.currentConvId || this.branchBusyIndex !== null || this.loading) return;
            this.branchBusyIndex = index;
            try {
                const r = await fetch(`/api/conversations/${this.currentConvId}/messages/${index}/switch-branch`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ direction }),
                });
                if (r.ok) {
                    const data = await r.json();
                    this.messages = data.messages;
                    // The swapped-in variant can be taller/shorter than the one it
                    // replaced — keep the branched message anchored in view instead
                    // of letting the page jump wherever the new layout happens to land.
                    this.$nextTick(() => this.scrollToMessage(index));
                }
            } catch (e) { console.error("switch-branch failed", e); }
            finally { this.branchBusyIndex = null; }
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
                } catch (e) { console.error("Message deletion failed", e); }
            }
            this.messages.splice(index, 1);
        },
        
        stopGeneration() {
            if (this.abortController) this.abortController.abort();
        },

        newChat() {
            this.messages = [];
            this.currentConvId = null;
            this.input = '';
            this.currentStreamingCharId = null;
            this.isAnonymous = false;
            this.pendingQuestion = null;
            window.location.hash = '';
            if (this.groupParticipants.length === 0) {
                this.groupParticipants = [this.activeCharId];
            }
            this.$nextTick(() => { this.autoResize(); document.getElementById('user-input')?.focus(); });
        },

        async loadConversation(id) {
            if (this.currentConvId === id) return;
            this.messagesLoaded = false;
            try {
                const r = await fetch(`/api/conversations/${id}`);
                if (!r.ok) throw new Error(r.status);
                const doc = await r.json();
                this.currentConvId = id;
                window.location.hash = `chat=${id}`;
                this.messages = (doc.messages || []).map(m => ({
                    ...m,
                    id: m.id || ('msg-' + Math.random().toString(36).substr(2, 9))
                }));
                this.isAnonymous = false;
                this.groupParticipants = doc.participants || [doc.character_id || 'kokomi'];
                if (doc.character_id) this.activeCharId = doc.character_id;
                else if (this.groupParticipants.length > 0) this.activeCharId = this.groupParticipants[0];
                this.evaluatePendingQuestion();

                // Show messages (triggers CSS fade-in), then immediately jump to bottom.
                // Double-nextTick + rAF ensures Alpine has finished rendering the x-for
                // list AND the browser has completed layout before we read scrollHeight.
                this.messagesLoaded = true;
                this.$nextTick(() => this.$nextTick(() => {
                    requestAnimationFrame(() => {
                        const box = this.$refs.chatBox;
                        if (box) box.scrollTop = box.scrollHeight;
                    });
                }));
            } catch (e) { 
                console.error('Load failed:', e);
                this.messagesLoaded = true;
            }
        },

        renderMarkdown(msg, isStreaming = false) {
            if (!msg) return '';
            let rawContent = (msg.role === 'assistant' && msg.displayContent !== undefined) ? msg.displayContent : msg.content;
            if (!rawContent) return '';

            // When images are shown in the gallery, strip any markdown image tags the
            // model also pasted so they don't double-render as ugly full-width images.
            if (msg.role === 'assistant' && this.galleryImages(msg).length) {
                rawContent = rawContent
                    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')   // ![alt](url)
                    .replace(/\n{3,}/g, '\n\n')
                    .trim();
            }

            try {
                // parseWithMath handles math extraction → marked → KaTeX injection
                let html = parseWithMath(rawContent);
                
                // Replace Artifact Placeholders with Cards
                if (msg.artifacts && msg.artifacts.length > 0) {
                    msg.artifacts.forEach(art => {
                        const placeholder = `[[ARTIFACT:${art.id}]]`;
                        if (html.includes(placeholder)) {
                            html = html.replace(placeholder, this.renderArtifactCardForMsg(art, msg.id));
                        }
                    });
                }

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
            } catch { return rawContent; }
        },

        // Same dispatcher, but with the owning message id injected onto generic (code/file)
        // cards so the global mousedown listener can reliably open the artifact modal.
        // Used both for inline [[ARTIFACT:id]] placeholders AND the fallback list below
        // (artifacts whose placeholder never made it into msg.content — e.g. ones
        // generated right after a tool call) so every artifact type — PDF, chart,
        // mermaid, question, or plain file — always renders through its real card,
        // never as raw text.
        renderArtifactCardForMsg(art, msgId) {
            return this.renderArtifactCard(art).replace('class="artifact-box', `data-msg-id="${msgId}" class="artifact-box`);
        },

        renderArtifactCard(art) {
            // Charts, diagrams and PDFs are special artifact types rendered live on the frontend.
            const atype = (art.type || '').toLowerCase();
            if (atype === 'chart') {
                return this.renderChartCard(art);
            }
            if (atype === 'mermaid' || atype === 'diagram') {
                return this.renderDiagramCard(art);
            }
            if (atype === 'pdf') {
                return this.renderPdfCard(art);
            }
            if (atype === 'question') {
                return this.renderQuestionCard(art);
            }
            if (isCanvasArtifact(art)) {
                return this.renderCanvasCard(art);
            }

            const icon = art.icon || 'fa-solid fa-file-code';
            const title = art.title || 'Untitled Artifact';
            const type = art.type || 'file';
            const content = art.content || '';

            return `
                <div class="artifact-box mt-4 mb-2" data-art-id="${art.id}">
                    ${art.streaming ? '<div class="artifact-shimmer"></div>' : ''}
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon">
                                <i class="${icon}"></i>
                            </div>
                            <div>
                                <p class="artifact-title">${title}</p>
                                <p class="artifact-meta uppercase tracking-wider">
                                    ${type}${art.streaming ? ' <span class="mx-1">•</span> <span class="animate-pulse text-accent">Generating...</span>' : ''}
                                </p>
                            </div>
                        </div>
                        <div class="text-4">
                            <i class="fa-solid fa-chevron-right text-[10px]"></i>
                        </div>
                    </div>
                    ${content ? `
                    <div class="artifact-body font-mono whitespace-pre overflow-x-auto text-[10px] bg-black/5 dark:bg-black/20 p-3 rounded-lg border border-themed mt-2" 
                         style="max-height: 120px; pointer-events: none;">${this.escapeHtml(content.substring(0, 500))}${content.length > 500 ? '...' : ''}</div>
                    ` : ''}
                </div>
            `;
        },

        escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        },

        // Derive an image gallery for a message from its `search_images` tool results.
        // Persistence-free: works live (tool_calls filled during streaming) and on reload
        // (tool_calls come from the DB). Memoized per message so re-renders are cheap and
        // x-for keys stay stable (images don't reload).
        galleryImages(msg) {
            if (!msg || !msg.tool_calls || !msg.tool_calls.length) return [];
            let sig = '';
            for (const tc of msg.tool_calls) {
                if (tc && tc.name === 'search_images' && tc.result) sig += tc.result.length + ':';
            }
            if (!sig) return [];
            this._galleryCache = this._galleryCache || {};
            const key = msg.id || msg.timestamp || '';
            const cached = this._galleryCache[key];
            if (cached && cached.sig === sig) return cached.images;

            const seen = new Set();
            const images = [];
            for (const tc of msg.tool_calls) {
                if (!tc || tc.name !== 'search_images' || !tc.result) continue;
                let data;
                try { data = JSON.parse(tc.result); } catch (e) { continue; }
                for (const im of (data && data.images) || []) {
                    const url = typeof im === 'string' ? im : (im && (im.url || im.thumbnail));
                    if (!url || seen.has(url)) continue;
                    seen.add(url);
                    const thumb = (typeof im === 'object' && im.thumbnail) ? im.thumbnail : url;
                    images.push({ url, thumbnail: thumb, title: (typeof im === 'object' && im.title) || '' });
                }
            }
            this._galleryCache[key] = { sig, images };
            return images;
        },

        // Route remote images through the same-origin proxy (sidesteps CORP /
        // mixed-content / hotlink blocks). Local paths (avatars, etc.) pass through.
        imgProxy(url) {
            if (!url) return '';
            if (url.startsWith('/') || url.startsWith('data:')) return url;
            return '/api/img?url=' + encodeURIComponent(url);
        },

        // Handle a click on an AI-emitted action chip (kokomi-actions widget).
        // detail: { fill?, send?, set?, label? }  (copy/url/confirm resolved in widgets.js)
        handleWidgetAction(detail) {
            if (!detail) return;

            // Update one or more preferences inline, then persist.
            if (detail.set && typeof detail.set === 'object') {
                let changed = false;
                for (const [k, v] of Object.entries(detail.set)) {
                    if (k in this.prefs) { this.prefs[k] = v; changed = true; }
                }
                if (changed && typeof this.updatePreferences === 'function') this.updatePreferences();
                if (changed && typeof this.showToast === 'function') this.showToast('Settings updated', 'success');
                return;
            }
            if (detail.fill) {
                this.input = detail.fill;
                this.$nextTick(() => {
                    const ta = this.$refs.textarea;
                    if (ta) { ta.focus(); ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 'px'; }
                });
                return;
            }
            if (detail.send) {
                if (this.loading) return;
                this.input = detail.send;
                this.sendMessage();
            }
        },

        openLightbox(images, index) {
            this.lightbox = { show: true, images: images || [], index: index || 0, src: '' };
            this._loadLightboxImage();
        },
        closeLightbox() { this.lightbox.show = false; },
        lightboxNext() {
            const n = this.lightbox.images.length;
            if (n) { this.lightbox.index = (this.lightbox.index + 1) % n; this._loadLightboxImage(); }
        },
        lightboxPrev() {
            const n = this.lightbox.images.length;
            if (n) { this.lightbox.index = (this.lightbox.index - 1 + n) % n; this._loadLightboxImage(); }
        },
        // Show the (already-cached) thumbnail instantly, then swap to the full-res image
        // once it has preloaded — so the lightbox never shows the previous image while a
        // large original downloads.
        _loadLightboxImage() {
            const img = this.lightbox.images[this.lightbox.index];
            if (!img) { this.lightbox.src = ''; return; }
            const thumb = this.imgProxy(img.thumbnail || img.url);
            const fullUrl = this.imgProxy(img.url);
            this.lightbox.src = thumb;
            if (fullUrl && fullUrl !== thumb) {
                const idx = this.lightbox.index;
                const full = new Image();
                full.onload = () => {
                    if (this.lightbox.show && this.lightbox.index === idx) this.lightbox.src = fullUrl;
                };
                full.src = fullUrl;
            }
        },

        renderChartCard(art) {
            const title = art.title || 'Chart';
            const raw = (art.content || '').trim();

            // While streaming (or before the JSON is complete/valid) show a shimmer
            // placeholder. The real canvas only appears once the spec parses, so the
            // chart manager never tries to render a half-streamed object.
            let spec = null;
            if (!art.streaming && raw) {
                try { spec = JSON.parse(raw); } catch (e) { spec = null; }
            }

            if (!spec) {
                return `
                <div class="kokomi-chart kokomi-chart--loading mt-4 mb-2">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon"><i class="fa-solid fa-chart-column"></i></div>
                            <div>
                                <p class="artifact-title">${this.escapeHtml(title)}</p>
                                <p class="artifact-meta uppercase tracking-wider">chart</p>
                            </div>
                        </div>
                        <i class="fa-solid fa-spinner fa-spin text-[11px] text-4"></i>
                    </div>
                    <div class="kokomi-chart-shimmer"></div>
                </div>`;
            }

            // Store the validated spec in an attribute; the manager hydrates the canvas
            // after this HTML is injected into the DOM (survives re-render).
            return `
                <div class="kokomi-chart mt-4 mb-2" data-chart-id="${art.id}" data-spec="${window.KokomiCharts.escapeAttr(raw)}">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon"><i class="fa-solid fa-chart-column"></i></div>
                            <div>
                                <p class="artifact-title">${this.escapeHtml(title)}</p>
                                <p class="artifact-meta uppercase tracking-wider">${this.escapeHtml((spec.type || 'bar'))} chart</p>
                            </div>
                        </div>
                        <div class="kokomi-chart-actions">
                            <button class="kokomi-chart-btn" title="Expand"
                                    onclick="window.KokomiCharts.expand('${art.id}', this)">
                                <i class="fa-solid fa-up-right-and-down-left-from-center"></i>
                            </button>
                            <button class="kokomi-chart-btn" title="Export as PNG"
                                    onclick="window.KokomiCharts.exportPNG('${art.id}', this)">
                                <i class="fa-solid fa-download"></i>
                            </button>
                        </div>
                    </div>
                    <div class="kokomi-chart-canvas-wrap"><canvas></canvas></div>
                </div>`;
        },

        renderDiagramCard(art) {
            const title = art.title || 'Diagram';
            const code = window.KokomiDiagrams.clean(art.content || '');

            // Show a shimmer until the diagram fully streams in; only then hand the
            // (complete) Mermaid source to the manager to render.
            if (art.streaming || !code) {
                return `
                <div class="kokomi-diagram kokomi-diagram--loading mt-4 mb-2">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon"><i class="fa-solid fa-diagram-project"></i></div>
                            <div>
                                <p class="artifact-title">${this.escapeHtml(title)}</p>
                                <p class="artifact-meta uppercase tracking-wider">diagram</p>
                            </div>
                        </div>
                        <i class="fa-solid fa-spinner fa-spin text-[11px] text-4"></i>
                    </div>
                    <div class="kokomi-chart-shimmer"></div>
                </div>`;
            }

            return `
                <div class="kokomi-diagram mt-4 mb-2" data-diagram-id="${art.id}" data-diagram="${window.KokomiCharts.escapeAttr(code)}">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon"><i class="fa-solid fa-diagram-project"></i></div>
                            <div>
                                <p class="artifact-title">${this.escapeHtml(title)}</p>
                                <p class="artifact-meta uppercase tracking-wider">diagram</p>
                            </div>
                        </div>
                        <div class="kokomi-chart-actions">
                            <button class="kokomi-chart-btn" title="Expand"
                                    onclick="window.KokomiDiagrams.expand('${art.id}', this)">
                                <i class="fa-solid fa-up-right-and-down-left-from-center"></i>
                            </button>
                            <button class="kokomi-chart-btn" title="Export as PNG"
                                    onclick="window.KokomiDiagrams.exportPNG('${art.id}', this)">
                                <i class="fa-solid fa-download"></i>
                            </button>
                        </div>
                    </div>
                    <div class="kokomi-diagram-host"></div>
                </div>`;
        },

        // PDF artifacts render a document-style card; the actual PDF bytes are only
        // generated on demand (View/Download) via KokomiPdf, which POSTs the raw
        // markdown to /api/artifacts/render-pdf. Nothing is written to disk just
        // because a PDF artifact appeared in a message.
        renderPdfCard(art) {
            const title = art.title || 'Document';
            const content = art.content || '';

            if (art.streaming || !content.trim()) {
                return `
                <div class="kokomi-pdf kokomi-pdf--loading mt-4 mb-2">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon"><i class="fa-solid fa-file-pdf"></i></div>
                            <div>
                                <p class="artifact-title">${this.escapeHtml(title)}</p>
                                <p class="artifact-meta uppercase tracking-wider">PDF document</p>
                            </div>
                        </div>
                        <i class="fa-solid fa-spinner fa-spin text-[11px] text-4"></i>
                    </div>
                    <div class="kokomi-pdf-shimmer"></div>
                </div>`;
            }

            const words = content.trim().split(/\s+/).length;
            const estPages = Math.max(1, Math.round(words / 400));

            return `
                <div class="kokomi-pdf mt-4 mb-2" data-pdf-id="${art.id}" data-pdf-content="${window.KokomiCharts.escapeAttr(content)}" data-pdf-title="${this.escapeHtml(title)}">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon"><i class="fa-solid fa-file-pdf"></i></div>
                            <div>
                                <p class="artifact-title">${this.escapeHtml(title)}</p>
                                <p class="artifact-meta uppercase tracking-wider">PDF · ~${estPages} page${estPages === 1 ? '' : 's'}</p>
                            </div>
                        </div>
                        <div class="kokomi-chart-actions">
                            <button class="kokomi-chart-btn" title="View PDF"
                                    onclick="window.KokomiPdf.view('${art.id}', this)">
                                <i class="fa-solid fa-eye"></i>
                            </button>
                            <button class="kokomi-chart-btn" title="Download PDF"
                                    onclick="window.KokomiPdf.download('${art.id}', this)">
                                <i class="fa-solid fa-download"></i>
                            </button>
                            <button class="kokomi-chart-btn" title="Forward to a paired computer"
                                    onclick="window.KokomiPdf.forward('${art.id}', this)">
                                <i class="fa-solid fa-share-from-square"></i>
                            </button>
                        </div>
                    </div>
                </div>`;
        },

        // Question artifacts are answered via a floating overlay docked above the
        // composer (see pendingQuestion in ui.js), not an inline card — a question is
        // a UI prompt, not a document to revisit. While it's the live, unanswered
        // question this renders nothing inline (the overlay owns it); once answered
        // (or once it's an older message) it collapses to a quiet one-line summary.
        renderQuestionCard(art) {
            if (this.pendingQuestion && this.pendingQuestion.id === art.id) return '';
            if (art.streaming) return '';
            let spec;
            try { spec = JSON.parse(art.content || ''); } catch (e) { spec = null; }
            const q = Array.isArray(spec && spec.questions)
                ? spec.questions.map(x => x && x.title).filter(Boolean).join(' · ') || art.title || 'Quick questions'
                : (spec && spec.question) || art.title || 'Quick question';
            return `<div class="kokomi-quiz-history"><i class="fa-solid fa-circle-question"></i> ${this.escapeHtml(q)}</div>`;
        },

        /**
         * Canvas artifacts live in the side pane, not inline. The message just gets a
         * compact card to (re)open it — the content itself is never dumped in the chat.
         */
        renderCanvasCard(art) {
            const isDoc = (art.mode || 'code').toLowerCase() === 'document';
            const icon = isDoc ? 'fa-file-word' : 'fa-code';
            const kind = isDoc ? 'Document' : (art.language || 'Code');
            const label = art.streaming ? 'Writing…' : 'Click to open in canvas';
            const lines = (art.content || '').split('\n').length;
            const meta = art.streaming ? '' : ` · ${lines} line${lines === 1 ? '' : 's'}`;

            return `<div class="artifact-box kokomi-canvas-card" data-art-id="${this.escapeHtml(art.id)}">
                ${art.streaming ? '<div class="artifact-shimmer"></div>' : ''}
                <div class="artifact-header">
                    <div class="artifact-info">
                        <div class="artifact-icon"><i class="fa-solid ${icon}"></i></div>
                        <div>
                            <div class="artifact-title">${this.escapeHtml(art.title || 'Canvas')}</div>
                            <div class="artifact-meta">${this.escapeHtml(kind)}${meta} · ${label}</div>
                        </div>
                    </div>
                    <i class="fa-solid fa-up-right-and-down-left-from-center" style="font-size:11px; opacity:0.5;"></i>
                </div>
            </div>`;
        }
    };
    return actions;
}

/**
 * KokomiCharts — module-level manager that turns chart-type artifacts into themed
 * Chart.js canvases. Because message bubbles are re-rendered from HTML strings on
 * every streamed token, we can't bind charts reactively; instead a debounced
 * MutationObserver re-hydrates any un-rendered chart card after the DOM settles,
 * and re-themes existing charts when the app's light/dark/accent theme changes.
 */
const KokomiCharts = {
    instances: {},          // chartId -> { chart, canvas, specStr }
    _observersReady: false,

    escapeAttr(str) {
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
    },

    theme() {
        const css = getComputedStyle(document.documentElement);
        const get = (v, fallback) => (css.getPropertyValue(v).trim() || fallback);
        const isDark = document.documentElement.classList.contains('dark');
        const accent = get('--accent', '#505081');
        const text = get('--text-tertiary', get('--text-quaternary', isDark ? '#9ca3af' : '#6b7280'));
        const grid = isDark ? 'rgba(255,255,255,0.08)' : 'rgba(0,0,0,0.08)';
        const palette = [accent, '#4ADE80', '#FBBF24', '#F87171', '#A78BFA', '#2DD4BF', '#60A5FA', '#F472B6'];
        return { isDark, accent, text, grid, palette };
    },

    _hexToRgba(hex, a) {
        const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(String(hex).trim());
        if (!m) return hex;
        return `rgba(${parseInt(m[1], 16)}, ${parseInt(m[2], 16)}, ${parseInt(m[3], 16)}, ${a})`;
    },

    buildConfig(spec, t) {
        const allowed = ['bar', 'line', 'pie', 'doughnut', 'radar', 'polarArea'];
        const type = allowed.includes(spec.type) ? spec.type : 'bar';
        const isCircular = ['pie', 'doughnut', 'polarArea'].includes(type);

        const datasets = (spec.datasets || []).map((ds, i) => {
            const color = t.palette[i % t.palette.length];
            if (isCircular) {
                return {
                    label: ds.label || '',
                    data: ds.data || [],
                    backgroundColor: (ds.data || []).map((_, j) => t.palette[j % t.palette.length]),
                    borderColor: t.isDark ? 'rgba(0,0,0,0.25)' : '#fff',
                    borderWidth: 2
                };
            }
            if (type === 'line') {
                return {
                    label: ds.label || '',
                    data: ds.data || [],
                    borderColor: color,
                    backgroundColor: this._hexToRgba(color, 0.15),
                    pointBackgroundColor: color,
                    pointRadius: 3,
                    borderWidth: 2,
                    tension: 0.35,
                    fill: true
                };
            }
            return {
                label: ds.label || '',
                data: ds.data || [],
                backgroundColor: type === 'radar' ? this._hexToRgba(color, 0.2) : color,
                borderColor: color,
                borderWidth: type === 'radar' ? 2 : 0,
                borderRadius: type === 'bar' ? 6 : 0
            };
        });

        const scales = isCircular ? {} : (type === 'radar' ? {
            r: { angleLines: { color: t.grid }, grid: { color: t.grid }, pointLabels: { color: t.text }, ticks: { color: t.text, backdropColor: 'transparent' } }
        } : {
            x: { stacked: !!spec.stacked, ticks: { color: t.text }, grid: { color: t.grid } },
            y: { stacked: !!spec.stacked, ticks: { color: t.text }, grid: { color: t.grid }, beginAtZero: true }
        });

        return {
            type,
            data: { labels: spec.labels || [], datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: { duration: 600, easing: 'easeOutQuart' },
                plugins: {
                    legend: {
                        display: datasets.length > 1 || isCircular,
                        labels: { color: t.text, usePointStyle: true, boxWidth: 8, padding: 14 }
                    },
                    title: spec.title ? { display: true, text: spec.title, color: t.text } : { display: false },
                    tooltip: { intersect: false, mode: isCircular ? 'nearest' : 'index' }
                },
                scales
            }
        };
    },

    renderOne(card) {
        if (!window.Chart) return;                 // Chart.js not loaded — leave card as-is
        const id = card.getAttribute('data-chart-id');
        const specStr = card.getAttribute('data-spec') || '';
        const canvas = card.querySelector('canvas');
        if (!canvas) return;

        const prev = this.instances[id];
        // Skip if this exact canvas already shows this exact spec (avoids churn).
        if (prev && prev.canvas === canvas && prev.specStr === specStr) return;
        if (prev && prev.chart) { try { prev.chart.destroy(); } catch (e) {} }

        let spec;
        try { spec = JSON.parse(specStr); } catch (e) { return; }

        try {
            const chart = new window.Chart(canvas.getContext('2d'), this.buildConfig(spec, this.theme()));
            this.instances[id] = { chart, canvas, specStr };
        } catch (e) {
            console.error('[KokomiCharts] render failed:', e);
        }
    },

    hydrate() {
        document.querySelectorAll('.kokomi-chart[data-spec]').forEach((card) => this.renderOne(card));
    },

    rethemeAll() {
        // Re-render every live chart with fresh theme colors, keeping its spec.
        Object.values(this.instances).forEach(({ canvas, specStr }) => {
            if (canvas && canvas.isConnected) {
                const card = canvas.closest('.kokomi-chart');
                if (card) { this.instances[card.getAttribute('data-chart-id')] = null; this.renderOne(card); }
            }
        });
    },

    // Resolve a guaranteed-OPAQUE background color for export. The UI is glassy, so
    // surfaces are often translucent (rgba with alpha < 1) — exporting with that alpha
    // yields a see-through PNG. Walk up from the chart to the first surface that has
    // any fill, then force its alpha to 1 so the PNG is solid.
    _exportBg(el) {
        let node = el;
        while (node && node.nodeType === 1) {
            const c = getComputedStyle(node).backgroundColor || '';
            const m = c.match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([\d.]+))?\s*\)$/);
            if (m) {
                const alpha = m[4] === undefined ? 1 : parseFloat(m[4]);
                if (alpha > 0.01) return `rgb(${m[1]}, ${m[2]}, ${m[3]})`;  // drop alpha → opaque
            }
            node = node.parentElement;
        }
        return document.documentElement.classList.contains('dark') ? '#16161a' : '#ffffff';
    },

    // Composite the (transparent) chart canvas onto an opaque themed background and
    // trigger a PNG download. Shared by the inline card and the expanded modal.
    _downloadCanvas(canvas, title, bgEl) {
        if (!canvas) return;
        const tmp = document.createElement('canvas');
        tmp.width = canvas.width;
        tmp.height = canvas.height;
        const ctx = tmp.getContext('2d');
        ctx.fillStyle = this._exportBg(bgEl || canvas);
        ctx.fillRect(0, 0, tmp.width, tmp.height);
        ctx.drawImage(canvas, 0, 0);
        const name = String(title || 'chart')
            .replace(/[^a-z0-9\-_]+/gi, '_').toLowerCase().replace(/^_+|_+$/g, '') || 'chart';
        const a = document.createElement('a');
        a.download = name + '.png';
        a.href = tmp.toDataURL('image/png');
        a.click();
    },

    exportPNG(id, btn) {
        const card = btn ? btn.closest('.kokomi-chart') : document.querySelector(`.kokomi-chart[data-chart-id="${id}"]`);
        const inst = this.instances[id];
        let canvas = inst && inst.canvas;
        if ((!canvas || !canvas.isConnected) && card) canvas = card.querySelector('canvas');
        const title = (card && card.querySelector('.artifact-title') && card.querySelector('.artifact-title').textContent) || 'chart';
        this._downloadCanvas(canvas, title, card);
    },

    expand(id, btn) {
        const card = btn ? btn.closest('.kokomi-chart') : document.querySelector(`.kokomi-chart[data-chart-id="${id}"]`);
        if (!card || !window.Chart) return;
        let spec;
        try { spec = JSON.parse(card.getAttribute('data-spec') || ''); } catch (e) { return; }
        const titleEl = card.querySelector('.artifact-title');
        const title = (titleEl && titleEl.textContent || 'Chart').trim();

        const overlay = document.createElement('div');
        overlay.className = 'kokomi-chart-overlay';
        const titleHtml = title.replace(/&/g, '&amp;').replace(/</g, '&lt;');
        overlay.innerHTML = `
            <div class="kokomi-chart-modal">
                <div class="kokomi-chart-modal-head">
                    <span class="kokomi-chart-modal-title"><i class="fa-solid fa-chart-column"></i> ${titleHtml}</span>
                    <div class="kokomi-chart-actions">
                        <button class="kokomi-chart-btn" data-act="export" title="Export as PNG"><i class="fa-solid fa-download"></i></button>
                        <button class="kokomi-chart-btn" data-act="close" title="Close (Esc)"><i class="fa-solid fa-xmark"></i></button>
                    </div>
                </div>
                <div class="kokomi-chart-modal-body"><canvas></canvas></div>
            </div>`;
        document.body.appendChild(overlay);

        const canvas = overlay.querySelector('canvas');
        const chart = new window.Chart(canvas.getContext('2d'), this.buildConfig(spec, this.theme()));
        // The modal lays out after append; force a resize on the next frame so the
        // chart fills the full modal body instead of its initial (small) size.
        requestAnimationFrame(() => { try { chart.resize(); } catch (e) {} });

        const close = () => {
            try { chart.destroy(); } catch (e) {}
            overlay.remove();
            document.removeEventListener('keydown', onKey);
        };
        const onKey = (ev) => { if (ev.key === 'Escape') close(); };

        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        overlay.querySelector('[data-act="close"]').onclick = close;
        overlay.querySelector('[data-act="export"]').onclick = () => this._downloadCanvas(canvas, title, overlay.querySelector('.kokomi-chart-modal'));
        document.addEventListener('keydown', onKey);
    },

    setupObservers() {
        if (this._observersReady) return;
        this._observersReady = true;

        let t = null;
        const obs = new MutationObserver(() => {
            clearTimeout(t);
            t = setTimeout(() => this.hydrate(), 120);
        });
        obs.observe(document.body, { childList: true, subtree: true });

        // Re-theme charts when the app toggles dark mode / accent (class on <html>).
        let tt = null;
        const themeObs = new MutationObserver(() => {
            clearTimeout(tt);
            tt = setTimeout(() => this.rethemeAll(), 150);
        });
        themeObs.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });

        this.hydrate();
    }
};

window.KokomiCharts = KokomiCharts;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => KokomiCharts.setupObservers());
} else {
    KokomiCharts.setupObservers();
}

/**
 * KokomiDiagrams — renders chart-style "mermaid" artifacts into themed Mermaid SVGs.
 * Mirrors KokomiCharts: a debounced MutationObserver hydrates un-rendered diagram
 * cards, results are cached by id+source so bubble re-renders re-inject instantly,
 * and a theme observer re-renders everything when light/dark/accent changes.
 */
const KokomiDiagrams = {
    cache: {},              // diagramId -> { code, svg }
    _seq: 0,
    _observersReady: false,

    // Models frequently wrap Mermaid in a ```mermaid fence despite instructions, which
    // makes the parser fail with "No diagram type detected". Strip a leading/trailing fence.
    clean(s) {
        let c = String(s || '').trim();
        c = c.replace(/^```[a-zA-Z0-9]*[ \t]*\r?\n?/, '').replace(/\r?\n?```\s*$/, '').trim();
        return c;
    },

    _esc(s) {
        const d = document.createElement('div');
        d.textContent = String(s);
        return d.innerHTML;
    },

    // Resolve any CSS color (incl. color-mix()/oklch()/named) to a concrete rgb()/rgba()
    // string. Mermaid's color lib (khroma) can't parse modern color functions, but a
    // 1x1 canvas rasterizes them, and reading the pixel gives a safe, concrete value.
    _resolveColor(val, fb) {
        const v = (val || '').trim();
        if (!v) return fb;
        if (/^#([0-9a-f]{3,8})$/i.test(v) || /^rgb\(/i.test(v)) return v;   // already safe
        if (window.CSS && CSS.supports && !CSS.supports('color', v)) return fb;
        try {
            const c = document.createElement('canvas');
            c.width = c.height = 1;
            const ctx = c.getContext('2d');
            ctx.fillStyle = v;
            ctx.fillRect(0, 0, 1, 1);
            const d = ctx.getImageData(0, 0, 1, 1).data;
            return d[3] === 255
                ? `rgb(${d[0]}, ${d[1]}, ${d[2]})`
                : `rgba(${d[0]}, ${d[1]}, ${d[2]}, ${(d[3] / 255).toFixed(3)})`;
        } catch (e) { return fb; }
    },

    _config() {
        const css = getComputedStyle(document.documentElement);
        const col = (v, f) => this._resolveColor(css.getPropertyValue(v).trim(), f);
        const isDark = document.documentElement.classList.contains('dark');
        const accent = col('--accent', '#505081');
        const accentText = col('--accent-text', '#ffffff');
        const text = col('--text-primary', isDark ? '#e5e7eb' : '#1f2937');
        const line = col('--text-quaternary', isDark ? '#6b7280' : '#9ca3af');
        return {
            startOnLoad: false,
            securityLevel: 'strict',           // sanitize AI-authored labels
            theme: 'base',
            fontFamily: 'inherit',
            flowchart: { htmlLabels: false, curve: 'basis' },
            themeVariables: {
                primaryColor: accent,
                primaryTextColor: accentText,
                primaryBorderColor: accent,
                lineColor: line,
                textColor: text,
                secondaryColor: '#4ADE80',
                tertiaryColor: isDark ? '#2a2a30' : '#f3f4f6',
                background: 'transparent',
                fontSize: '14px'
            }
        };
    },

    async render(card) {
        if (!window.mermaid) return;
        const id = card.getAttribute('data-diagram-id');
        const code = card.getAttribute('data-diagram') || '';
        const host = card.querySelector('.kokomi-diagram-host');
        if (!host || !code) return;

        if (host.getAttribute('data-rendered-code') === code && host.querySelector('svg')) return;

        // Cached SVG for the same source → inject synchronously (cheap re-render).
        const cached = this.cache[id];
        if (cached && cached.code === code) {
            host.innerHTML = cached.svg;
            host.setAttribute('data-rendered-code', code);
            return;
        }

        try {
            window.mermaid.initialize(this._config());
            const renderId = 'mmd-' + String(id).replace(/[^a-z0-9]/gi, '') + '-' + (++this._seq);
            const { svg } = await window.mermaid.render(renderId, code);
            this.cache[id] = { code, svg };
            // The bubble may have re-rendered during the await; target the live host.
            const liveHost = document.querySelector(`.kokomi-diagram[data-diagram-id="${id}"] .kokomi-diagram-host`);
            if (liveHost) {
                liveHost.innerHTML = svg;
                liveHost.setAttribute('data-rendered-code', code);
            }
        } catch (e) {
            const liveHost = document.querySelector(`.kokomi-diagram[data-diagram-id="${id}"] .kokomi-diagram-host`);
            if (liveHost) {
                const msg = (e && e.message) ? e.message : String(e);
                liveHost.innerHTML = `
                    <div class="kokomi-diagram-err">
                        <div class="kokomi-diagram-err-head"><i class="fa-solid fa-triangle-exclamation"></i> Couldn't render this diagram</div>
                        <div class="kokomi-diagram-err-msg">${this._esc(msg)}</div>
                        <details><summary>View Mermaid source</summary><pre>${this._esc(code)}</pre></details>
                    </div>`;
            }
            console.error('[KokomiDiagrams] render failed:', e);
        }
    },

    hydrate() {
        if (!window.mermaid) return;
        document.querySelectorAll('.kokomi-diagram[data-diagram]').forEach((card) => this.render(card));
    },

    rethemeAll() {
        this.cache = {};
        document.querySelectorAll('.kokomi-diagram[data-diagram] .kokomi-diagram-host')
            .forEach((h) => h.removeAttribute('data-rendered-code'));
        this.hydrate();
    },

    _svgFor(id, card) {
        const code = card.getAttribute('data-diagram') || '';
        if (this.cache[id] && this.cache[id].code === code) return this.cache[id].svg;
        const host = card.querySelector('.kokomi-diagram-host');
        return host ? host.innerHTML : '';
    },

    // Rasterize a rendered Mermaid <svg> to an opaque PNG and download it.
    _svgToPng(svgEl, title, bgEl) {
        if (!svgEl) return;
        const vb = svgEl.viewBox && svgEl.viewBox.baseVal;
        const rect = svgEl.getBoundingClientRect();
        const w = Math.ceil((vb && vb.width) || rect.width || 900);
        const h = Math.ceil((vb && vb.height) || rect.height || 600);
        const clone = svgEl.cloneNode(true);
        clone.setAttribute('width', w);
        clone.setAttribute('height', h);
        const xml = new XMLSerializer().serializeToString(clone);
        const url = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(xml)));
        const scale = 2;
        const img = new Image();
        img.onload = () => {
            const c = document.createElement('canvas');
            c.width = w * scale;
            c.height = h * scale;
            const ctx = c.getContext('2d');
            ctx.fillStyle = window.KokomiCharts._exportBg(bgEl || svgEl.parentElement);
            ctx.fillRect(0, 0, c.width, c.height);
            ctx.drawImage(img, 0, 0, c.width, c.height);
            const name = String(title || 'diagram').replace(/[^a-z0-9\-_]+/gi, '_').toLowerCase().replace(/^_+|_+$/g, '') || 'diagram';
            const a = document.createElement('a');
            a.download = name + '.png';
            a.href = c.toDataURL('image/png');
            a.click();
        };
        img.onerror = (e) => console.error('[KokomiDiagrams] PNG export failed:', e);
        img.src = url;
    },

    exportPNG(id, btn) {
        const card = btn ? btn.closest('.kokomi-diagram') : document.querySelector(`.kokomi-diagram[data-diagram-id="${id}"]`);
        if (!card) return;
        const svgEl = card.querySelector('.kokomi-diagram-host svg');
        const titleEl = card.querySelector('.artifact-title');
        this._svgToPng(svgEl, (titleEl && titleEl.textContent) || 'diagram', card);
    },

    expand(id, btn) {
        const card = btn ? btn.closest('.kokomi-diagram') : document.querySelector(`.kokomi-diagram[data-diagram-id="${id}"]`);
        if (!card) return;
        const svg = this._svgFor(id, card);
        if (!svg) return;
        const titleEl = card.querySelector('.artifact-title');
        const title = (titleEl && titleEl.textContent || 'Diagram').trim();
        const titleHtml = title.replace(/&/g, '&amp;').replace(/</g, '&lt;');

        const overlay = document.createElement('div');
        overlay.className = 'kokomi-chart-overlay';
        overlay.innerHTML = `
            <div class="kokomi-chart-modal">
                <div class="kokomi-chart-modal-head">
                    <span class="kokomi-chart-modal-title"><i class="fa-solid fa-diagram-project"></i> ${titleHtml}</span>
                    <div class="kokomi-chart-actions">
                        <button class="kokomi-chart-btn" data-act="export" title="Export as PNG"><i class="fa-solid fa-download"></i></button>
                        <button class="kokomi-chart-btn" data-act="close" title="Close (Esc)"><i class="fa-solid fa-xmark"></i></button>
                    </div>
                </div>
                <div class="kokomi-chart-modal-body kokomi-diagram-modal-body">${svg}</div>
            </div>`;
        document.body.appendChild(overlay);

        const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey); };
        const onKey = (ev) => { if (ev.key === 'Escape') close(); };
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        overlay.querySelector('[data-act="close"]').onclick = close;
        overlay.querySelector('[data-act="export"]').onclick = () =>
            this._svgToPng(overlay.querySelector('svg'), title, overlay.querySelector('.kokomi-chart-modal'));
        document.addEventListener('keydown', onKey);
    },

    setupObservers() {
        if (this._observersReady) return;
        this._observersReady = true;

        let t = null;
        new MutationObserver(() => {
            clearTimeout(t);
            t = setTimeout(() => this.hydrate(), 140);
        }).observe(document.body, { childList: true, subtree: true });

        let tt = null;
        new MutationObserver(() => {
            clearTimeout(tt);
            tt = setTimeout(() => this.rethemeAll(), 200);
        }).observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'style', 'data-theme'] });

        this.hydrate();
    }
};

window.KokomiDiagrams = KokomiDiagrams;
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => KokomiDiagrams.setupObservers());
} else {
    KokomiDiagrams.setupObservers();
}

/**
 * KokomiPdf — turns a `pdf`-type artifact's markdown into an actual PDF, on
 * demand. The backend (POST /api/artifacts/render-pdf) generates the bytes
 * in-memory (nothing written to disk); the result is cached per-card here so
 * repeated View/Download clicks on the same card don't re-render.
 */
const KokomiPdf = {
    _cache: new Map(), // art id -> blob URL

    _card(id) { return document.querySelector(`.kokomi-pdf[data-pdf-id="${id}"]`); },

    async _blobUrl(id) {
        if (this._cache.has(id)) return this._cache.get(id);
        const card = this._card(id);
        if (!card) return null;
        const content = card.getAttribute('data-pdf-content') || '';
        const resp = await fetch('/api/artifacts/render-pdf', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content }),
        });
        if (!resp.ok) throw new Error(`Render failed (${resp.status})`);
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        this._cache.set(id, url);
        return url;
    },

    async _withSpinner(btn, fn) {
        const icon = btn && btn.querySelector('i');
        const prevClass = icon ? icon.className : null;
        if (icon) icon.className = 'fa-solid fa-circle-notch fa-spin';
        try { await fn(); }
        catch (e) { console.error('[KokomiPdf]', e); }
        finally { if (icon && prevClass) icon.className = prevClass; }
    },

    async view(id, btn) {
        await this._withSpinner(btn, async () => {
            const url = await this._blobUrl(id);
            if (url) window.open(url, '_blank');
        });
    },

    async download(id, btn) {
        await this._withSpinner(btn, async () => {
            const url = await this._blobUrl(id);
            if (!url) return;
            const card = this._card(id);
            const title = (card && card.getAttribute('data-pdf-title')) || 'document';
            const safeName = title.replace(/[^a-zA-Z0-9_\-]+/g, '_').slice(0, 60) || 'document';
            const a = document.createElement('a');
            a.href = url;
            a.download = `${safeName}.pdf`;
            document.body.appendChild(a);
            a.click();
            a.remove();
        });
    },

    // Base64 of the rendered PDF, for forwarding the actual bytes to a machine.
    async _blobBase64(id) {
        const url = await this._blobUrl(id);
        if (!url) throw new Error('no pdf');
        const blob = await (await fetch(url)).blob();
        return await new Promise((resolve, reject) => {
            const r = new FileReader();
            r.onloadend = () => resolve(String(r.result).split(',')[1] || '');
            r.onerror = reject;
            r.readAsDataURL(blob);
        });
    },

    forward(id, btn) {
        const card = this._card(id);
        const title = (card && card.getAttribute('data-pdf-title')) || 'document';
        const safeName = title.replace(/[^a-zA-Z0-9_\-]+/g, '_').slice(0, 60) || 'document';
        window.KokomiForward.open(btn, `${safeName}.pdf`, async () => ({
            content: await this._blobBase64(id), b64: true,
        }));
    },
};
window.KokomiPdf = KokomiPdf;

/**
 * KokomiForward — a small imperative popover that lists the user's online Triton
 * machines and forwards a generated artifact to one (saved to its ~/Documents).
 * Used by raw-HTML cards (PDF etc.) that aren't Alpine-reactive. `getPayload`
 * returns { content, b64 } lazily so the file is only rendered when a machine is
 * actually picked.
 */
const KokomiForward = {
    _menu: null,
    _onDoc: null,

    _platIcon(p) {
        p = (p || '').toLowerCase();
        if (p.includes('linux')) return 'fa-brands fa-linux';
        if (p.includes('mac')) return 'fa-brands fa-apple';
        if (p.includes('win')) return 'fa-solid fa-desktop';
        return 'fa-solid fa-server';
    },

    close() {
        if (this._menu) { this._menu.remove(); this._menu = null; }
        if (this._onDoc) { document.removeEventListener('mousedown', this._onDoc, true); this._onDoc = null; }
    },

    async open(btn, filename, getPayload) {
        if (this._menu) { this.close(); return; }
        const menu = document.createElement('div');
        menu.className = 'kokomi-forward-menu';
        menu.innerHTML =
            `<div class="kfwd-head">Send to a computer<span>Saves to ~/Documents</span></div>` +
            `<div class="kfwd-list"><div class="kfwd-note"><i class="fa-solid fa-circle-notch fa-spin"></i> Loading devices…</div></div>`;
        document.body.appendChild(menu);
        const r = btn.getBoundingClientRect();
        const width = 264;
        menu.style.top = `${r.bottom + window.scrollY + 6}px`;
        menu.style.left = `${Math.max(8, Math.min(r.right + window.scrollX - width, window.innerWidth - width - 8))}px`;
        this._menu = menu;
        this._onDoc = (e) => { if (this._menu && !this._menu.contains(e.target) && !btn.contains(e.target)) this.close(); };
        setTimeout(() => document.addEventListener('mousedown', this._onDoc, true), 0);

        let devices = [];
        try {
            const resp = await fetch('/api/triton/devices');
            const data = await resp.json();
            devices = (data.devices || []).filter(d => d.online);
        } catch (e) { /* handled below */ }
        if (!this._menu) return; // closed while loading
        const list = menu.querySelector('.kfwd-list');
        if (!devices.length) {
            list.innerHTML = `<div class="kfwd-note">No computers online. Pair one in Settings → Triton and run it with <code>--allow-write</code>.</div>`;
            return;
        }
        list.innerHTML = '';
        devices.forEach(dev => {
            const item = document.createElement('button');
            item.className = 'kfwd-item';
            item.innerHTML =
                `<i class="${this._platIcon(dev.platform)}"></i>` +
                `<span class="kfwd-name">${(dev.name || dev.id).replace(/</g, '&lt;')}</span>` +
                `<i class="fa-solid fa-arrow-right kfwd-arrow"></i>`;
            item.onclick = async (ev) => {
                ev.stopPropagation();
                const arrow = item.querySelector('.kfwd-arrow');
                arrow.className = 'fa-solid fa-circle-notch fa-spin kfwd-arrow';
                try {
                    const payload = await getPayload();
                    const resp = await fetch(`/api/triton/devices/${encodeURIComponent(dev.id)}/forward`, {
                        method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ filename, ...payload }),
                    });
                    const d = await resp.json().catch(() => ({}));
                    if (resp.ok) {
                        list.innerHTML = `<div class="kfwd-note kfwd-ok"><i class="fa-solid fa-check"></i> Saved to ${(d.path || '~/Documents')} on ${(dev.name || dev.id).replace(/</g, '&lt;')}</div>`;
                        setTimeout(() => this.close(), 1600);
                    } else {
                        arrow.className = 'fa-solid fa-arrow-right kfwd-arrow';
                        this._err(menu, d.detail || 'Failed to forward');
                    }
                } catch (e) {
                    arrow.className = 'fa-solid fa-arrow-right kfwd-arrow';
                    this._err(menu, 'Network error');
                }
            };
            list.appendChild(item);
        });
    },

    _err(menu, msg) {
        let e = menu.querySelector('.kfwd-err');
        if (!e) { e = document.createElement('div'); e.className = 'kfwd-err'; menu.appendChild(e); }
        e.textContent = msg;
    },
};
window.KokomiForward = KokomiForward;

// Global helper for inline artifact cards
window.openArtifactFromCard = (id, el) => {
    try {
        // Find the message data from the bubble element
        const container = el.closest('.message-container');
        if (!container) return;
        
        // Use Alpine's own utility to find the data
        const bubbleData = Alpine.$data(container);
        if (bubbleData && bubbleData.msg) {
            const art = bubbleData.msg.artifacts.find(a => a.id === id);
            if (art) {
                const rootEl = document.getElementById('app');
                if (rootEl) {
                    const app = Alpine.$data(rootEl);
                    if (app && app.openArtifactModal) {
                        app.openArtifactModal(art);
                    }
                }
            }
        }
    } catch (e) {
        console.error('Failed to open artifact from card:', e);
    }
};
