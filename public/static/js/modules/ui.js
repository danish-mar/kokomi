/**
 * UI Interactions and Helpers
 */

export function getUiActions() {
    return {
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
                    window._pyodide = await loadPyodide();
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
        }
    };
}
