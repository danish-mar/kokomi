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
            document.getElementById('bottom-anchor')?.scrollIntoView({ behavior: 'smooth' });
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
            this.artifactModal.id = artifact.id || 'artifact';
            this.artifactModal.title = artifact.title || 'Untitled Artifact';
            this.artifactModal.type = artifact.type || 'file';
            this.artifactModal.content = artifact.content || '';
            this.artifactModal.icon = artifact.icon || 'fa-solid fa-file-code';
            this.renderArtifactInModal();
            this.artifactModal.show = true;
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
        }
    };
}
