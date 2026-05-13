/**
 * API Actions and Data Fetching
 */

export function getApiActions() {
    return {
        async fetchSpaces() {
            try {
                const r = await fetch('/api/spaces');
                if (r.ok) this.spaces = await r.json();
            } catch(e) { console.error(e); }
        },
        async fetchCharacters() {
            try {
                const r = await fetch('/api/characters');
                if (r.ok) this.characters = await r.json();
            } catch (e) { console.warn('Characters unavailable', e); }
        },
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
            }
        },

        async updatePreferences() {
            try {
                await fetch('/api/prefs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(this.prefs)
                });
            } catch (e) {
                console.error('Failed to update preferences', e);
            }
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
        }
    };
}
