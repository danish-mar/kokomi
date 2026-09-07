import { fetchWithCache, invalidateCache } from './cache.js';

export function getApiActions() {
    const parseDate = (v) => {
        if (!v) return 0;
        if (typeof v === 'number') return v;
        if (typeof v === 'string' && !isNaN(v)) return parseFloat(v);
        return new Date(v).getTime() / 1000 || 0;
    };

    return {
        async fetchSpaces() {
            try {
                this.spaces = await fetchWithCache('/api/spaces');
            } catch(e) { console.error(e); }
        },
        async fetchCharacters() {
            try {
                this.characters = await fetchWithCache('/api/characters');
            } catch (e) { console.warn('Characters unavailable', e); }
        },
        async fetchConversations() {
            try {
                const r = await fetch('/api/conversations');
                if (r.ok) {
                    const data = await r.json();
                    const parseDate = (v) => {
                        if (!v) return 0;
                        if (typeof v === 'number') return v;
                        if (typeof v === 'string' && !isNaN(v)) return parseFloat(v);
                        return new Date(v).getTime() / 1000 || 0;
                    };
                    data.sort((a, b) => parseDate(b.updated_at) - parseDate(a.updated_at));
                    this.conversations = data;
                }
            } catch (e) { console.warn('Could not load conversations', e); }
        },
        async fetchPrefs() {
            try {
                this.prefs = await fetchWithCache('/api/prefs');
            } catch (e) { console.warn('Prefs unavailable', e); }
        },
        async fetchFolders() {
            try {
                this.folders = await fetchWithCache('/api/folders');
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
                invalidateCache('/api/folders');
                this.projectModal.show = false;
                await this.fetchFolders();
            } catch (e) { console.error('Save project failed', e); }
        },
        async deleteFolder(fid) {
            if (!confirm('Delete this project and unarchive chats?')) return;
            try {
                await fetch(`/api/folders/${fid}`, { method: 'DELETE' });
                invalidateCache('/api/folders');
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
                    window.location.hash = '';
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
                invalidateCache('/api/prefs');
            } catch (e) {
                console.error('Failed to update preferences', e);
            }
        },
        get conversationsByFolder() {
            const grouped = { 'none': [] };
            this.folders.forEach(f => grouped[f.id] = []);
            
            // Ensure global list is sorted newest first
            const parseDate = (v) => {
                if (!v) return 0;
                if (typeof v === 'number') return v;
                if (typeof v === 'string' && !isNaN(v)) return parseFloat(v);
                return new Date(v).getTime() / 1000 || 0;
            };
            const sorted = [...this.conversations].sort((a, b) => 
                parseDate(b.updated_at) - parseDate(a.updated_at)
            );

            sorted.forEach(c => {
                const fid = c.folder_id || 'none';
                if (!grouped[fid]) grouped[fid] = [];
                grouped[fid].push(c);
            });
            return grouped;
        }
    };
}
