/**
 * Chat Logic, Streaming and Message Handling
 */

export function getChatActions() {
    const actions = {
        async sendMessage() {
            const text = this.input.trim();
            if (!text || this.loading) return;

            this.messages.push({ 
                id: 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9),
                role: 'user', 
                content: text 
            });
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
                        use_web_search: this.useWebSearch
                    }),
                    signal: this.abortController.signal,
                });
                this.useWebSearch = false;

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

        async sendMessageStream(msg) {
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
                        use_web_search: this.useWebSearch
                    }),
                    signal: this.abortController.signal
                });
                this.useWebSearch = false;

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
                            if (targetIdx === undefined && (data.type === 'content' || data.type === 'reasoning')) {
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
                                        content: '',
                                        streaming: true
                                    });
                                }
                            } else if (data.type === 'artifact_chunk') {
                                if (targetIdx !== undefined && this.messages[targetIdx].artifacts) {
                                    const arts = this.messages[targetIdx].artifacts;
                                    const art = arts.find(a => a.id === data.id);
                                    if (art) {
                                        art.content += data.delta;
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
            window.location.hash = '';
            if (this.groupParticipants.length === 0) {
                this.groupParticipants = [this.activeCharId];
            }
            this.$nextTick(() => { this.autoResize(); document.getElementById('user-input')?.focus(); });
        },

        async loadConversation(id) {
            if (this.currentConvId === id) return;
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
                this.$nextTick(() => this.scrollToBottom());
            } catch (e) { console.error('Load failed:', e); }
        },

        renderMarkdown(msg, isStreaming = false) {
            if (!msg) return '';
            const rawContent = (msg.role === 'assistant' && msg.displayContent !== undefined) ? msg.displayContent : msg.content;
            if (!rawContent) return '';

            try {
                let html = marked.parse(rawContent);
                
                // Replace Artifact Placeholders with Cards
                if (msg.artifacts && msg.artifacts.length > 0) {
                    msg.artifacts.forEach(art => {
                        const placeholder = `[[ARTIFACT:${art.id}]]`;
                        if (html.includes(placeholder)) {
                            // Inject msg id into card for reliable click handling
                            const cardHtml = this.renderArtifactCard(art).replace('class="artifact-box', `data-msg-id="${msg.id}" class="artifact-box`);
                            html = html.replace(placeholder, cardHtml);
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

        renderArtifactCard(art) {
            const icon = art.icon || 'fa-solid fa-file-code';
            const title = art.title || 'Untitled Artifact';
            const type = art.type || 'file';
            const content = art.content || '';
            
            return `
                <div class="artifact-box mt-4 mb-2" data-art-id="${art.id}">
                    <div class="artifact-header">
                        <div class="artifact-info">
                            <div class="artifact-icon">
                                <i class="${icon}"></i>
                            </div>
                            <div>
                                <p class="artifact-title">${title}</p>
                                <p class="artifact-meta uppercase tracking-wider">${type}</p>
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
        }
    };
    return actions;
}

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
