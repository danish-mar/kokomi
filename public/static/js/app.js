/**
 * Main Entry Point - Kokomi AI App
 */

import { setupMarkdown } from './modules/markdown.js';
import { getInitialState } from './modules/state.js';
import { getUiActions } from './modules/ui.js';
import { getApiActions } from './modules/api.js';
import { getChatActions } from './modules/chat.js';
import { getCharacterActions } from './modules/characters.js';
import { getCanvasActions } from './modules/canvas.js';
import { getRailActions } from './modules/rail.js';
import { getBackgroundActions } from './modules/background.js';

// Initialize Markdown
setupMarkdown();

function aiApp() {
    const app = {};
    const modules = [
        getInitialState(),
        getUiActions(),
        getApiActions(),
        getChatActions(),
        getCharacterActions(),
        getCanvasActions(),
        getRailActions(),
        getBackgroundActions()
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

        // Restore chat from URL hash
        const hash = window.location.hash;
        if (hash && hash.startsWith('#chat=')) {
            const id = hash.split('=')[1];
            if (id) await this.loadConversation(id);
        }

        // Deep-link from the Spaces page: open a fresh chat with a space attached.
        if (hash && hash.startsWith('#space=')) {
            const sid = hash.split('=')[1];
            if (sid && this.spaces.some(s => s.id === sid)) {
                this.activeSpaceId = sid;
                const sp = this.spaces.find(s => s.id === sid);
                if (typeof this.showToast === 'function' && sp) this.showToast(`Knowledge space “${sp.name}” active`, 'success');
            }
            history.replaceState(null, '', window.location.pathname);
        }

        this.updateSuggestions();
        this.initGlobalListeners();

        // Responses outlive the page now, so find out what's still running —
        // including work started in another tab or before this reload — and
        // rejoin the current conversation's stream if it's mid-response.
        this.startActivePolling();
        if (this.currentConvId) this.maybeResume(this.currentConvId);

        // Bridge AI-emitted action chips (kokomi-actions widget) into the chat component.
        window.addEventListener('kokomi-action', (e) => this.handleWidgetAction(e.detail));

        // Dynamic Tab Title
        this.$watch('currentTitle', (val) => {
            document.title = `${val} - KokomiAi`;
        });
        document.title = `${this.currentTitle} - KokomiAi`;

        // Sidebar open/closed state persists across refreshes.
        this.$watch('sidebarOpen', (val) => {
            try { localStorage.setItem('sidebarOpen', val); } catch (err) {}
        });

        // Keep the message rail in step with the transcript. Scroll-driven
        // recomputation lives in onChatScroll; these cover the cases where the
        // content changes without any scrolling (a new message, a finished
        // response settling to its final height, a window resize).
        this.$watch('messages.length', () => this.$nextTick(() => this.computeRailTicks()));
        this.$watch('loading', () => this.$nextTick(() => this.computeRailTicks()));
        window.addEventListener('resize', () => this.computeRailTicks());

        // Live Artifact Preview Watcher
        this.$watch('artifactModal.content', (val) => {
            if (this.artifactModal.tab === 'preview' && (this.artifactModal.type === 'html' || this.artifactModal.type === 'svg')) {
                this.updateLivePreview(val);
            }
        });
        
        // Initial tab for visual artifacts
        this.$watch('artifactModal.show', (val) => {
            if (val && (this.artifactModal.type === 'html' || this.artifactModal.type === 'svg')) {
                this.$nextTick(() => this.updateLivePreview(this.artifactModal.content));
            }
        });

        // Trigger Guided Tour Prompt if not completed
        setTimeout(() => {
            if (this.prefs && this.prefs.tour_completed === false) {
                localStorage.removeItem('kokomi_tour_completed');
            }
            if ((!this.prefs || this.prefs.tour_completed !== true) && localStorage.getItem('kokomi_tour_completed') !== 'true') {
                this.showTourPrompt = true;
            }
        }, 1200);
    };

    app.startTour = async function() {
        this.showTourPrompt = false;
        localStorage.setItem('kokomi_tour_completed', 'true');
        
        // Sync tour completed to backend
        if (this.prefs) {
            this.prefs.tour_completed = true;
            try {
                await this.updatePreferences();
            } catch (e) {
                console.error('Failed to sync tour completion state to backend:', e);
            }
        }
        
        if (!window.driver || !window.driver.js) {
            console.error('Driver.js is not loaded.');
            return;
        }

        const driverObj = window.driver.js.driver({
            showProgress: true,
            allowClose: false,
            popoverClass: 'driverjs-theme',
            steps: [
                {
                    element: '#tour-sidebar',
                    popover: {
                        title: 'Chats & Projects',
                        description: 'Organize your conversations into project workspaces, view folder categories, and track recent chat threads.',
                        side: 'right',
                        align: 'start'
                    }
                },
                {
                    element: '#tour-room-capsule',
                    popover: {
                        title: 'Multi-Agent Room Capsule',
                        description: 'Engage multiple AI agents simultaneously! Switch between 1-on-1 chats and full multi-agent room sessions.',
                        side: 'bottom',
                        align: 'center'
                    }
                },
                {
                    element: '#tour-chat-welcome',
                    popover: {
                        title: 'Character Profiles',
                        description: 'Instantly select your active conversational partner, modify their details, or build customized AI personas.',
                        side: 'bottom',
                        align: 'center'
                    }
                },
                {
                    element: '#tour-quick-access',
                    popover: {
                        title: 'Quick Access Actions',
                        description: 'Quickly access multi-agent Knowledge Spaces, WhatsApp automation dashboards, live voice calls, and the powerful Atlas shell interface.',
                        side: 'right',
                        align: 'end'
                    }
                },
                {
                    element: '#tour-settings',
                    popover: {
                        title: 'Advanced Settings & Integration',
                        description: 'Configure multi-provider LLM credentials (Groq, Google, Nvidia), dynamic suggestions, developer tools, and sandboxed runtimes.',
                        side: 'right',
                        align: 'end'
                    }
                },
                {
                    element: '#tour-input',
                    popover: {
                        title: 'Intelligent Console (Next: Atlas Terminal)',
                        description: 'Input prompts, search the web, and run code. Clicking next will transition you to the next application in the suite—Atlas Terminal—to explore multi-agent workflow pipelines!',
                        side: 'top',
                        align: 'center',
                        onNextClick: () => {
                            window.location.href = '/atlas?tour=true';
                        }
                    }
                }
            ]
        });

        driverObj.drive();
    };

    app.dismissTour = async function() {
        this.showTourPrompt = false;
        localStorage.setItem('kokomi_tour_completed', 'true');
        
        // Sync tour completed to backend
        if (this.prefs) {
            this.prefs.tour_completed = true;
            try {
                await this.updatePreferences();
            } catch (e) {
                console.error('Failed to sync tour completion state to backend:', e);
            }
        }
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
