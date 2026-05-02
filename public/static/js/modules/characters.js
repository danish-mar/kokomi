/**
 * Character and Room Management Actions
 */

export function getCharacterActions() {
    return {
        selectCharacter(id) {
            this.activeCharId = id;
            localStorage.setItem('lastCharacterId', id);
            this.charPickerOpen = false;
            this.welcomePickerOpen = false;
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
                    if (this.activeCharId === id) {
                        this.activeCharId = this.groupParticipants[0];
                        localStorage.setItem('lastCharacterId', this.activeCharId);
                    }
                } else {
                    this.showToast("At least one character must stay in the room.", "warning");
                }
            }
        },
        startAnonymousChat() {
            this.messages = [];
            this.currentConvId = null;
            this.input = '';
            this.currentStreamingCharId = null;
            this.isAnonymous = true;
            if (this.groupParticipants.length === 0) {
                this.groupParticipants = [this.activeCharId];
            }
            this.$nextTick(() => { this.autoResize(); document.getElementById('user-input')?.focus(); });
        },
        exitAnonymousChat() {
            this.exitTempModal = false;
            this.newChat();
        },
        confirmExitTemp() {
            if (!this.messages.length) {
                this.exitAnonymousChat();
                return;
            }
            this.exitTempModal = true;
        },
        async saveAndExitTemp() {
            if (this.currentConvId) {
                try {
                    await fetch(`/api/conversations/${this.currentConvId}/save`, { method: 'POST' });
                    await this.fetchConversations();
                } catch (e) { console.warn('Save failed', e); }
            }
            this.exitTempModal = false;
            this.isAnonymous = false;
        }
    };
}
