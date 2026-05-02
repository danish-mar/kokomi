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
        }
    };
}
