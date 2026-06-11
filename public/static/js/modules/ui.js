/**
 * UI Interactions and Helpers
 */

export function getUiActions() {
    return {
        // Drag-to-resize the sidebar (desktop). Width persists in localStorage.
        startSidebarResize(e) {
            e.preventDefault();
            const startX = e.clientX;
            const startW = this.sidebarWidth;
            const MIN = 240, MAX = 560;
            const onMove = (ev) => {
                let w = startW + (ev.clientX - startX);
                this.sidebarWidth = Math.max(MIN, Math.min(MAX, w));
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
                document.body.classList.remove('sidebar-resizing');
                try { localStorage.setItem('sidebarWidth', String(this.sidebarWidth)); } catch (err) {}
            };
            document.body.classList.add('sidebar-resizing');
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
        },

        formatConvTime(updatedAt) {
            if (!updatedAt) return 'Just now';
            let dateVal = typeof updatedAt === 'number' ? updatedAt * 1000 : updatedAt;
            if (typeof updatedAt === 'string') {
                if (!isNaN(updatedAt)) {
                    dateVal = parseFloat(updatedAt) * 1000;
                } else {
                    let cleanStr = updatedAt.trim();
                    cleanStr = cleanStr.replace(' ', 'T');
                    if (!/Z|[+-]\d{2}(?::?\d{2})?$/.test(cleanStr)) {
                        cleanStr += 'Z';
                    }
                    dateVal = cleanStr;
                }
            }
            const date = new Date(dateVal);
            if (isNaN(date.getTime())) return 'Recently';
            
            const now = new Date();
            const isToday = date.toDateString() === now.toDateString();
            
            if (isToday) {
                return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: true });
            }
            
            const diffTime = Math.abs(now - date);
            const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
            
            if (diffDays < 7) {
                return date.toLocaleDateString([], { weekday: 'long' });
            }
            
            return date.toLocaleDateString([], { month: 'short', day: 'numeric' });
        },

        shouldShowConvImage(conv) {
            if (!conv || !conv._id) return false;
            // If the chat produced images, always surface one as the card thumbnail.
            if (conv.thumbnail) return true;
            const char = this.getCharById(conv.character_id);
            if (!char || !char.avatar) return false;
            
            // Deterministic random check (e.g. 50% chance based on id hash)
            let sum = 0;
            for (let i = 0; i < conv._id.length; i++) {
                sum += conv._id.charCodeAt(i);
            }
            return sum % 2 === 0;
        },
        getConvPreviewText(conv) {
            if (!conv || !conv.preview) return '';
            
            // Deterministic character limit between 40 and 130 based on id hash
            let sum = 0;
            for (let i = 0; i < conv._id.length; i++) {
                sum += conv._id.charCodeAt(i);
            }
            const limit = 40 + (sum % 91);
            
            if (conv.preview.length > limit) {
                return conv.preview.substring(0, limit) + '...';
            }
            return conv.preview;
        },

        // -- Theme --
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

        // -- Navigation & Layout --
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

        // -- Toasts --
        showToast(message, type = 'info') {
            const id = Date.now();
            this.toasts.push({ id, message, type });
            setTimeout(() => {
                this.toasts = this.toasts.filter(t => t.id !== id);
            }, 5000);
        },

        // -- Resizing & Scrolling --
        autoResize() {
            const el = this.$refs.textarea;
            if (!el) return;
            el.style.height = 'auto';
            el.style.height = Math.min(el.scrollHeight, 200) + 'px';
        },
        scrollToBottom() {
            const box = this.$refs.chatBox;
            if (box) box.scrollTop = box.scrollHeight;
        },
        async copyText(t) { 
            try { 
                await navigator.clipboard.writeText(t); 
                this.showToast('Copied to clipboard', 'info');
            } catch {} 
        },

        // -- Getters --
        get currentTitle() {
            if (!this.currentConvId) return 'New Conversation';
            const c = this.conversations.find(x => x._id === this.currentConvId);
            return c ? c.title : 'Chat';
        },
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

        // -- Voice to Text --
        toggleVoiceInput() {
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                this.showToast('Speech Recognition not supported in this browser.', 'warning');
                return;
            }

            if (this.isRecording) {
                if (this._recognition) this._recognition.stop();
                this.isRecording = false;
                return;
            }

            const recognition = new SpeechRecognition();
            recognition.lang = 'en-US';
            recognition.interimResults = true;
            recognition.continuous = false;

            recognition.onstart = () => {
                this.isRecording = true;
                this.showToast('Listening...', 'info');
            };

            recognition.onresult = (event) => {
                let transcript = '';
                for (let i = event.resultIndex; i < event.results.length; i++) {
                    transcript += event.results[i][0].transcript;
                }
                this.input = transcript;
                this.$nextTick(() => this.autoResize());
            };

            recognition.onerror = (event) => {
                console.error('Speech recognition error', event.error);
                this.isRecording = false;
                if (event.error !== 'no-speech') {
                    this.showToast(`Voice Error: ${event.error}`, 'error');
                }
            };

            recognition.onend = () => {
                this.isRecording = false;
            };

            this._recognition = recognition;
            recognition.start();
        },

        // -- Artifacts --
        openArtifactModal(artifact) {
            // Reset state completely to prevent pollution from previous artifact
            this.artifactModal.id = artifact.id || 'artifact-' + Date.now();
            this.artifactModal.title = artifact.title || 'Untitled Artifact';
            this.artifactModal.type = artifact.type || 'file';
            this.artifactModal.content = artifact.content || '';
            this.artifactModal.renderedContent = '';
            this.artifactModal.icon = artifact.icon || 'fa-solid fa-file-code';
            this.artifactModal.output = '';
            this.artifactModal.executing = false;
            this.artifactModal.tab = 'code'; 
            
            this.renderArtifactInModal();
            this.artifactModal.show = true;
        },

        closeArtifactModal() {
            this.artifactModal.show = false;
            // Clear content after a short delay (for transition) to prevent ghosting
            setTimeout(() => {
                if (!this.artifactModal.show) {
                    this.artifactModal.id = null;
                    this.artifactModal.title = '';
                    this.artifactModal.content = '';
                    this.artifactModal.renderedContent = '';
                    this.artifactModal.output = '';
                }
            }, 300);
        },

        openArtifact(msg, id) {
            if (!msg || !msg.artifacts) return;
            const art = msg.artifacts.find(a => a.id === id);
            if (art) {
                this.openArtifactModal(art);
            }
        },

        initGlobalListeners() {
            // High-priority capture-phase listener to catch clicks before re-renders detach elements
            window.addEventListener('mousedown', (e) => {
                const artBox = e.target.closest('.artifact-box');
                if (artBox && artBox.dataset.artId && artBox.dataset.msgId) {
                    const artId = artBox.dataset.artId;
                    const msgId = artBox.dataset.msgId;
                    
                    // Find message in app state
                    const msg = this.messages.find(m => m.id === msgId);
                    if (msg) {
                        this.openArtifact(msg, artId);
                    }
                }
            }, true); // Use capture phase

            // Spotlight keyboard shortcut listener
            window.addEventListener('keydown', (e) => {
                const isK = e.key === 'k' || e.key === 'K';
                const isSpace = e.code === 'Space';
                const isModifier = e.metaKey || e.ctrlKey;
                
                if (isModifier && (isK || isSpace)) {
                    e.preventDefault();
                    if (this.spotlightOpen) {
                        this.closeSpotlight();
                    } else {
                        this.openSpotlight();
                    }
                }
            });
        },
        renderArtifactInModal() {
            if (!this.artifactModal.content) {
                this.artifactModal.renderedContent = '';
                return;
            }
            // Syntax highlighting
            if (window.hljs) {
                try {
                    const result = hljs.highlightAuto(this.artifactModal.content);
                    this.artifactModal.renderedContent = result.value;
                } catch (e) {
                    this.artifactModal.renderedContent = this.artifactModal.content;
                }
            } else {
                this.artifactModal.renderedContent = this.artifactModal.content;
            }
        },
        downloadArtifact() {
            const blob = new Blob([this.artifactModal.content], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = this.artifactModal.title || 'artifact.txt';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        },

        // -- Attachments --
        triggerFileUpload() {
            this.$refs.fileInput.click();
        },
        async handleFileUpload(e) {
            const files = e.target.files;
            if (!files || files.length === 0) return;
            
            for (let i = 0; i < files.length; i++) {
                const file = files[i];
                const formData = new FormData();
                formData.append('file', file);
                
                try {
                    const r = await fetch('/api/upload', {
                        method: 'POST',
                        body: formData
                    });
                    if (r.ok) {
                        const data = await r.json();
                        this.attachments.push(data);
                    } else {
                        console.error('Upload failed');
                    }
                } catch (err) {
                    console.error('Upload error:', err);
                }
            }
            // Clear input for next selection
            e.target.value = '';
        },
        removeAttachment(index) {
            this.attachments.splice(index, 1);
        },
        async handlePaste(e) {
            const items = (e.clipboardData || e.originalEvent.clipboardData).items;
            for (let index in items) {
                const item = items[index];
                if (item.kind === 'file') {
                    const blob = item.getAsFile();
                    const formData = new FormData();
                    // Give it a generic name if it's a pasted blob
                    const filename = `pasted_file_${Date.now()}.${blob.type.split('/')[1] || 'png'}`;
                    formData.append('file', blob, filename);
                    
                    try {
                        const r = await fetch('/api/upload', {
                            method: 'POST',
                            body: formData
                        });
                        if (r.ok) {
                            const data = await r.json();
                            this.attachments.push(data);
                        }
                    } catch (err) {
                        console.error('Paste upload error:', err);
                    }
                }
            }
        },

        async runPythonArtifact() {
            if (this.artifactModal.executing) return;
            this.artifactModal.executing = true;
            this.artifactModal.tab = 'console';
            this.artifactModal.output = 'Loading Python engine...\n';
            
            try {
                // Initialize Pyodide if not already done
                if (!window._pyodide) {
                    window._pyodide = await loadPyodide({ indexURL: "/static/vendor/" });
                }
                
                // Clear output for fresh run
                this.artifactModal.output = '';
                
                // Detect needed packages
                const code = this.artifactModal.content;
                const packages = [];
                if (code.includes('import matplotlib') || code.includes('from matplotlib')) packages.push('matplotlib');
                if (code.includes('import numpy') || code.includes('from numpy')) packages.push('numpy');
                if (code.includes('import pandas') || code.includes('from pandas')) packages.push('pandas');
                
                if (packages.length > 0) {
                    this.artifactModal.output += `<span class="text-accent opacity-70">Installing ${packages.join(', ')}...</span>\n`;
                    await window._pyodide.loadPackage(packages);
                }

                // Inject Plot Helper
                await window._pyodide.runPythonAsync(`
import sys
import io
import base64

def kokomi_show_plot():
    import matplotlib.pyplot as plt
    buf = io.BytesIO()
    plt.savefig(buf, format='png', bbox_inches='tight', dpi=150)
    buf.seek(0)
    img_str = base64.b64encode(buf.read()).decode('utf-8')
    print(f'@@@IMG:data:image/png;base64,{img_str}@@@')
    plt.close()

# Monkeypatch plt.show if matplotlib is loaded
try:
    import matplotlib.pyplot as plt
    plt.show = kokomi_show_plot
except:
    pass
`);
                
                // Set up stdout/stderr capturing
                window._pyodide.setStdout({
                    batched: (msg) => {
                        if (msg.startsWith('@@@IMG:') && msg.endsWith('@@@')) {
                            const src = msg.replace('@@@IMG:', '').replace('@@@', '');
                            this.artifactModal.output += `<div class="my-3"><img src="${src}" class="max-w-full rounded-lg border border-white/10 shadow-lg" /></div>`;
                        } else {
                            // Escape HTML to prevent XSS from script output
                            const div = document.createElement('div');
                            div.textContent = msg;
                            this.artifactModal.output += `<span>${div.innerHTML}</span>\n`;
                        }
                    }
                });
                
                window._pyodide.setStderr({
                    batched: (msg) => {
                        const div = document.createElement('div');
                        div.textContent = msg;
                        this.artifactModal.output += `<span class="text-red-400">Error: ${div.innerHTML}</span>\n`;
                    }
                });
                
                // Execute code
                await window._pyodide.runPythonAsync(code);
                
            } catch (err) {
                this.artifactModal.output += '\nRuntime Error:\n' + err.message;
            } finally {
                this.artifactModal.executing = false;
            }
        },

        _previewTimer: null,
        updateLivePreview(content) {
            if (this._previewTimer) clearTimeout(this._previewTimer);
            this._previewTimer = setTimeout(() => {
                const iframe = this.$refs.previewIframe;
                if (!iframe) return;
                
                // Use blob for a smoother update if possible, or just srcdoc
                const blob = new Blob([content], { type: 'text/html' });
                const url = URL.createObjectURL(blob);
                
                const oldUrl = iframe.src;
                iframe.src = url;
                
                // Cleanup old blob URL after a short delay
                if (oldUrl.startsWith('blob:')) {
                    setTimeout(() => URL.revokeObjectURL(oldUrl), 1000);
                }
            }, 100); // 100ms debounce for smoothness
        },

        openSpotlight() {
            this.spotlightOpen = true;
            this.spotlightQuery = '';
            this.spotlightResults = [];
            this.spotlightSelectedId = null;
            this.spotlightPreview = null;
            this.spotlightLoading = false;
            this.spotlightGlowing = true;
            
            setTimeout(() => {
                this.spotlightGlowing = false;
            }, 1000);
            
            setTimeout(() => {
                const el = this.$refs.spotlightInput;
                if (el) el.focus();
            }, 50);
        },
        closeSpotlight() {
            this.spotlightOpen = false;
            this.spotlightQuery = '';
            this.spotlightResults = [];
            this.spotlightSelectedId = null;
            this.spotlightPreview = null;
        },
        handleSpotlightInput() {
            if (this._spotlightDebounceTimer) clearTimeout(this._spotlightDebounceTimer);
            this._spotlightDebounceTimer = setTimeout(() => {
                this.performSpotlightSearch();
            }, 1000); // 1-second debounce!
        },
        performSpotlightSearch() {
            const q = this.spotlightQuery.toLowerCase().trim();
            if (!q) {
                this.spotlightResults = [];
                this.spotlightSelectedId = null;
                this.spotlightPreview = null;
                return;
            }
            
            this.spotlightResults = this.conversations.filter(c =>
                c.title.toLowerCase().includes(q) ||
                (c.preview && c.preview.toLowerCase().includes(q))
            );
            
            if (this.spotlightResults.length > 0) {
                this.selectSpotlightItem(this.spotlightResults[0]._id);
            } else {
                this.spotlightSelectedId = null;
                this.spotlightPreview = null;
            }
        },
        async selectSpotlightItem(convId) {
            this.spotlightSelectedId = convId;
            this.spotlightLoading = true;
            this.spotlightPreview = null;
            
            try {
                const resp = await fetch(`/api/conversations/${convId}`);
                if (resp.ok) {
                    const data = await resp.json();
                    if (this.spotlightSelectedId === convId) {
                        this.spotlightPreview = data;
                        this.$nextTick(() => {
                            const container = this.$refs.spotlightPreviewContainer;
                            if (container) {
                                container.scrollTop = container.scrollHeight;
                            }
                        });
                    }
                }
            } catch (e) {
                console.error(e);
            } finally {
                if (this.spotlightSelectedId === convId) {
                    this.spotlightLoading = false;
                }
            }
        },
        confirmSpotlightSelection() {
            if (this.spotlightSelectedId) {
                this.loadConversation(this.spotlightSelectedId);
                this.closeSpotlight();
            }
        },
        navigateSpotlight(direction) {
            const list = this.spotlightQuery ? this.spotlightResults : this.spotlightSuggestions;
            if (!list || list.length === 0) return;
            const currentIdx = list.findIndex(c => c._id === this.spotlightSelectedId);
            let nextIdx = currentIdx + direction;
            if (nextIdx < 0) nextIdx = list.length - 1;
            if (nextIdx >= list.length) nextIdx = 0;
            const nextConv = list[nextIdx];
            if (nextConv) {
                this.selectSpotlightItem(nextConv._id);
                this.$nextTick(() => {
                    const el = document.getElementById(`spotlight-item-${nextConv._id}`);
                    if (el) el.scrollIntoView({ block: 'nearest' });
                });
            }
        },
        cleanMsgContent(content) {
            if (!content) return '';
            // Remove <think>...</think> or <thought>...</thought> (closed or unclosed)
            let cleaned = content.replace(/<(think|thought)>[\s\S]*?(<\/\1>|$)/gi, '');
            // Strip markdown heading markers
            cleaned = cleaned.replace(/^\s*#+\s+/gm, '');
            // Strip code blocks
            cleaned = cleaned.replace(/```[\s\S]*?```/g, '');
            return cleaned.trim();
        }
    };
}
