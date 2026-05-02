/**
 * Main Entry Point - Kokomi AI App
 */

import { setupMarkdown } from './modules/markdown.js';
import { getInitialState } from './modules/state.js';
import { getUiActions } from './modules/ui.js';
import { getApiActions } from './modules/api.js';
import { getChatActions } from './modules/chat.js';
import { getCharacterActions } from './modules/characters.js';

// Initialize Markdown
setupMarkdown();

function aiApp() {
    const app = {};
    const modules = [
        getInitialState(),
        getUiActions(),
        getApiActions(),
        getChatActions(),
        getCharacterActions()
    ];

    modules.forEach(mod => {
        Object.defineProperties(app, Object.getOwnPropertyDescriptors(mod));
    });

    app.init = async function() {
        await Promise.all([
            this.fetchConversations(), 
            this.fetchFolders(), 
            this.fetchCharacters(), 
            this.fetchPrefs(),
            this.fetchSpaces()
        ]);

        if (this.groupParticipants.length === 0) {
            this.groupParticipants = [this.activeCharId];
        }
        this.updateSuggestions();

        // Dynamic Tab Title
        this.$watch('currentTitle', (val) => {
            document.title = `${val} - KokomiAi`;
        });
        document.title = `${this.currentTitle} - KokomiAi`;
    };

    app.setInput = function(t) {
        this.input = t;
        this.$nextTick(() => {
            const el = document.getElementById('user-input');
            if (el) {
                el.focus();
                el.style.height = 'auto';
                el.style.height = Math.min(el.scrollHeight, 200) + 'px';
            }
        });
    };

    return app;
}

// Attach to window so Alpine can find it
window.aiApp = aiApp;
