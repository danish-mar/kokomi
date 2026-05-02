/**
 * Chat Logic, Streaming and Message Handling
 */

export function getChatActions() {
    return {
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
                        is_anonymous: this.isAnonymous,
                        use_web_search: this.useWebSearch
                    }),
                    signal: this.abortController.signal,
                });
                this.useWebSearch = false;

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
                            
                            let targetIdx = charMsgMap[charId];
                            if (targetIdx === undefined && (data.type === 'content' || data.type === 'reasoning')) {
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
                            } else if (data.type === 'error') {
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
                this.messages = doc.messages || [];
                this.isAnonymous = false;
                this.groupParticipants = doc.participants || [doc.character_id || 'kokomi'];
                if (doc.character_id) this.activeCharId = doc.character_id;
                else if (this.groupParticipants.length > 0) this.activeCharId = this.groupParticipants[0];
                this.$nextTick(() => this.scrollToBottom());
            } catch (e) { console.error('Load failed:', e); }
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
        }
    };
}
