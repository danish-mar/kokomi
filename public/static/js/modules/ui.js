/**
 * UI Interactions and Helpers
 */

import { isCanvasArtifact } from './canvas.js';

// Tools shown only as a muted dot beside the model name, never as a chip in
// the transcript: their result is internal state, not something to read.
const AMBIENT_TOOLS = ['memory_search'];

// Model-tier bar geometry, in px. Kept in sync with the .kokomi-tier-pill /
// .kokomi-tier-knob rules in the composer's stylesheet. The knob is inset
// from the bar on every side so it rides fully inside the track.
const TIER_BAR_W = 116;
const TIER_BAR_H = 28;
const TIER_KNOB_W = 22;
const TIER_INSET = (TIER_BAR_H - TIER_KNOB_W) / 2;                  // 3
const TIER_TRAVEL = TIER_BAR_W - TIER_KNOB_W - TIER_INSET * 2;      // 88


export function getUiActions() {
    return {
        // ── Tool-call indicators ──────────────────────────────────────────
        // Tools split into two presentations. "Ambient" ones are background
        // activity with nothing worth reading, so they show only as a muted
        // dot next to the model name. The rest keep an expandable chip in the
        // transcript because their result is content (web-search hits, tool
        // output). search_images is excluded everywhere — it renders as a
        // gallery instead.
        ambientTools(msg) {
            return (msg.tool_calls || []).filter(t => AMBIENT_TOOLS.includes(t.name));
        },
        detailTools(msg) {
            return (msg.tool_calls || []).filter(
                t => t.name !== 'search_images' && !AMBIENT_TOOLS.includes(t.name)
            );
        },
        toolIcon(tc) {
            if (tc.name === 'memory_search') return 'fa-brain';
            if (tc.name === 'web_search') {
                return tc.result === 'Executing...' ? 'fa-circle-notch fa-spin' : 'fa-globe';
            }
            return tc.icon || 'fa-wrench';
        },
        toolLabel(tc) {
            const running = tc.result === 'Executing...';
            if (tc.name === 'memory_search') return running ? 'Accessing memory…' : 'Accessed memory';
            if (tc.name === 'web_search') return running ? 'Searching the web…' : 'Searched the web';
            return tc.description || tc.name;
        },

        // Composer brain-icon slider. tier is 'fast' | 'normal' | 'smart'.
        setModelTier(tier) {
            this.modelTier = tier;
            try { localStorage.setItem('modelTier', tier); } catch (err) {}
        },

        // Icon shown on the brain button for a given tier: microchip (raw
        // speed) -> brain (default) -> atom (deepest reasoning).
        tierIcon(tier) {
            return tier === 'fast' ? 'fa-microchip' : (tier === 'smart' ? 'fa-atom' : 'fa-brain');
        },

        // Whether the tier bar is unfurled (hovered, or pinned open by a click).
        get modelTierOpen() {
            return this.modelTierHover || this.modelTierPinned;
        },

        // Slider step (0/1/2) and fill percentage (0/50/100) for a tier.
        tierSliderIndex(tier) {
            return tier === 'fast' ? 0 : (tier === 'smart' ? 2 : 1);
        },
        tierPercent(tier) {
            return this.tierSliderIndex(tier) * 50;
        },

        // Geometry of the tier bar (must match the .kokomi-tier-* CSS sizes).
        // Collapsed, the bar is exactly the knob, so every offset has to be 0
        // or the knob would be translated outside its own pill.
        tierKnobPx(tier, open) {
            if (!open) return 0;
            return (this.tierPercent(tier) / 100) * TIER_TRAVEL;
        },
        // Centre of the knob at a given stop — the single anchor the fill, the
        // preset dot and the tooltip all line up on. The knob starts inset
        // from the bar's left edge, so that offset carries into every centre.
        tierCentrePx(tier) {
            return TIER_INSET + TIER_KNOB_W / 2 + this.tierKnobPx(tier, true);
        },
        // The fill sweeps from the left edge to just past the knob (its far
        // edge plus the knob's inset), so the knob always rides the tip of the
        // fill and the bar is filled EDGE TO EDGE at the top stop.
        tierFillPx(tier, open) {
            if (!open) return 0;
            return this.tierKnobPx(tier, open) + TIER_BAR_H;
        },
        // Each preset dot sits at a knob centre (CSS pulls it back by half its
        // own size, so this is a true centre, not a left edge).
        tierDotLeft(tier) {
            return this.tierCentrePx(tier);
        },
        // The tooltip is anchored from the right, since the bar grows leftward
        // off a fixed icon: distance from the bar's right edge to the knob.
        tierTooltipRight(tier) {
            return TIER_BAR_W - this.tierCentrePx(tier);
        },

        // The model name Settings has configured for a given tier, shown as a
        // tooltip on the slider. Mirrors the provider -> field-name mapping
        // the backend uses in app/llm.py's active_model_name().
        tierModelLabel(tier) {
            if (!this.prefs) return '';
            const prefix = tier === 'normal' ? '' : `${tier}_`;
            const provider = this.prefs[`${prefix}llm_provider`] || 'groq';
            const field = provider === 'google' ? 'model_name'
                : provider === 'custom' ? 'custom_model'
                : provider === 'nvidia' ? 'nvidia_model'
                : 'model_name';
            return this.prefs[`${prefix}${field}`] || '';
        },

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
            // Monaco carries its own theme registry — repaint an open code canvas.
            this.syncCanvasTheme?.();
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
            this.showScrollToBottom = false;
        },
        // Smooth, user-triggered jump for the floating "scroll to bottom" button
        // (scrollToBottom above stays instant since it also fires mid-stream on
        // every chunk — smooth-scrolling there would fight itself).
        jumpToBottom() {
            const box = this.$refs.chatBox;
            if (box) box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
            this.showScrollToBottom = false;
        },
        // Toggle the floating button once the user has scrolled more than a
        // screen's-worth away from the bottom, so it doesn't flicker in/out on
        // tiny scroll jitter near the bottom.
        onChatScroll() {
            const box = this.$refs.chatBox;
            if (!box) return;
            const distanceFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight;
            this.showScrollToBottom = distanceFromBottom > 240;
        },
        // Keep a specific message bubble anchored near the top of the viewport —
        // used after editing/switching a branch so the message you were just
        // looking at doesn't jump out of view when the swapped-in variant has a
        // different height than the one it replaced.
        scrollToMessage(index) {
            const el = document.getElementById('msg-row-' + index);
            if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
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
            
            this.artifactForward.open = false;
            this.artifactForward.error = '';
            this.artifactForward.sendingId = null;

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
            if (!art) return;
            // A canvas belongs in the side-by-side editor, not the centred modal.
            if (isCanvasArtifact(art)) {
                this.openCanvas(art);
                return;
            }
            this.openArtifactModal(art);
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

        // -- Forward an artifact to a paired Triton machine (~/Documents) --
        async toggleArtifactForward() {
            this.artifactForward.open = !this.artifactForward.open;
            if (this.artifactForward.open) {
                await this.loadForwardDevices();
            }
        },
        async loadForwardDevices() {
            this.artifactForward.loading = true;
            this.artifactForward.error = '';
            try {
                const r = await fetch('/api/triton/devices');
                const data = await r.json();
                // Only online machines can actually receive a file.
                this.artifactForward.devices = (data.devices || []).filter(d => d.online);
            } catch (e) {
                this.artifactForward.error = 'Could not load devices';
                this.artifactForward.devices = [];
            } finally {
                this.artifactForward.loading = false;
            }
        },
        async forwardArtifactTo(device) {
            if (this.artifactForward.sendingId) return;
            this.artifactForward.sendingId = device.id;
            this.artifactForward.error = '';
            try {
                const r = await fetch('/api/triton/devices/' + encodeURIComponent(device.id) + '/forward', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        filename: this.artifactModal.title || 'artifact.txt',
                        content: this.artifactModal.content || ''
                    })
                });
                const data = await r.json().catch(() => ({}));
                if (r.ok) {
                    this.artifactForward.open = false;
                    if (this.showToast) {
                        this.showToast('Saved to ' + (data.path || '~/Documents') + ' on ' + (device.name || device.id));
                    }
                } else {
                    this.artifactForward.error = data.detail || 'Failed to forward';
                }
            } catch (e) {
                this.artifactForward.error = 'Network error';
            } finally {
                this.artifactForward.sendingId = null;
            }
        },

        // -- Pending clarifying question(s) (floating card above the composer) --
        // A question artifact is only "live" while it's on the very last message and
        // no reply has followed it yet; reloading history re-derives this so an
        // unanswered question survives a refresh, and answering makes it vanish the
        // moment the user's reply message is appended. Supports a single question OR
        // a batch ({"questions": [...]}) shown as tabs, answered one at a time.
        evaluatePendingQuestion() {
            const last = this.messages[this.messages.length - 1];
            if (!last || last.role !== 'assistant' || !last.artifacts) {
                this.pendingQuestion = null;
                return;
            }
            const art = last.artifacts.find(a => (a.type || '').toLowerCase() === 'question' && !a.streaming);
            if (!art) { this.pendingQuestion = null; return; }
            let spec;
            try { spec = JSON.parse(art.content || ''); } catch (e) { spec = null; }
            if (!spec) { this.pendingQuestion = null; return; }
            const rawItems = Array.isArray(spec.questions) ? spec.questions
                : (Array.isArray(spec.options) ? [spec] : null);
            if (!rawItems) { this.pendingQuestion = null; return; }
            const items = rawItems
                .filter(q => q && Array.isArray(q.options))
                .map((q, i) => {
                    // The model doesn't always remember to set the multiSelect JSON flag
                    // even when it phrases the question as one ("select all that apply") —
                    // since the user only ever sees that text, honor it as ground truth:
                    // detect the phrasing as a fallback so the UI never contradicts what
                    // the question literally says.
                    const impliesMulti = /(select|check|choose|pick|mark)\s+(all|any|multiple|several)\b|that apply\b|select two or more|more than one (?:option|answer|choice)/i.test(q.question || '');
                    const multiSelect = !!q.multiSelect || impliesMulti;
                    return {
                        title: q.title || `Q${i + 1}`,
                        question: q.question || art.title || 'Quick question',
                        options: q.options.map(String),
                        allowOther: q.allowOther !== false,
                        allowSkip: q.allowSkip !== false,
                        answer: null,
                        // Quiz/Kahoot mode: one objectively correct option, revealed on pick.
                        // Mutually exclusive with multiSelect.
                        quiz: !multiSelect && !!q.quiz && Number.isInteger(q.correctIndex) && q.correctIndex >= 0 && q.correctIndex < q.options.length,
                        correctIndex: Number.isInteger(q.correctIndex) ? q.correctIndex : null,
                        explanation: q.explanation || '',
                        revealed: false,
                        selectedIndex: null,
                        // Multi-select ("pick several"): checkboxes + a Continue button
                        // instead of answering on the first tap.
                        multiSelect,
                        selectedIndices: [],
                    };
                });
            if (!items.length) { this.pendingQuestion = null; return; }
            this.pendingQuestion = { id: art.id, msgId: last.id, items, activeIndex: 0 };
            this.pendingQuestionOther = '';
        },
        // Swaps the active tab with a brief fade-out/fade-in instead of an instant
        // snap: the body hides (leave transition), the data changes while it's
        // invisible, then it reappears (enter transition). See the x-show on the
        // question body in the template, gated on !pendingQuestionAnimating.
        _switchQuestionTab(i) {
            if (!this.pendingQuestion || !this.pendingQuestion.items[i]) return;
            this.pendingQuestionAnimating = true;
            setTimeout(() => {
                if (!this.pendingQuestion) return;
                this.pendingQuestion.activeIndex = i;
                this.pendingQuestionOther = '';
                this.pendingQuestionAnimating = false;
            }, 120);
        },
        selectQuestionTab(i) {
            if (!this.pendingQuestion || i === this.pendingQuestion.activeIndex) return;
            this._switchQuestionTab(i);
        },
        // Records the active tab's answer, then either advances to the next
        // unanswered tab or — once everything's answered/skipped — compiles all
        // answers into one message and sends it.
        _recordQuestionAnswer(value) {
            if (!value || !this.pendingQuestion || this.loading) return;
            const pq = this.pendingQuestion;
            pq.items[pq.activeIndex].answer = value;
            const nextIdx = pq.items.findIndex(it => it.answer === null);
            if (nextIdx !== -1) {
                this._switchQuestionTab(nextIdx);
                return;
            }
            const message = pq.items.length === 1
                ? pq.items[0].answer
                : pq.items.map(it => `${it.title}: ${it.answer}`).join('\n');
            this.pendingQuestion = null;
            this.input = message;
            this.sendMessage();
        },
        // index is only needed for quiz mode (to compare against correctIndex and to
        // drive the correct/incorrect highlight); plain clarifying questions ignore it.
        answerPendingQuestion(value, index) {
            const pq = this.pendingQuestion;
            if (!pq || this.loading) return;
            const item = pq.items[pq.activeIndex];
            if (item.quiz && !item.revealed) {
                item.revealed = true;
                item.selectedIndex = index;
                const isCorrect = index === item.correctIndex;
                // Hold the reveal on screen (Kahoot-style flash of correct/incorrect)
                // before advancing to the next tab or sending.
                setTimeout(() => {
                    this._recordQuestionAnswer(this._formatQuizAnswer(item, value, isCorrect));
                }, 1100);
                return;
            }
            this._recordQuestionAnswer(value);
        },
        // Multi-select: tapping an option just toggles its checkbox — it doesn't
        // answer immediately. The user confirms with confirmMultiSelect() once
        // they've picked everything they want.
        toggleQuestionOption(index) {
            const pq = this.pendingQuestion;
            if (!pq || this.loading) return;
            const item = pq.items[pq.activeIndex];
            const pos = item.selectedIndices.indexOf(index);
            if (pos === -1) item.selectedIndices.push(index);
            else item.selectedIndices.splice(pos, 1);
        },
        confirmMultiSelect() {
            const pq = this.pendingQuestion;
            if (!pq || this.loading) return;
            const item = pq.items[pq.activeIndex];
            if (!item.selectedIndices.length) return;
            const chosen = item.selectedIndices.slice().sort((a, b) => a - b).map(i => item.options[i]);
            this._recordQuestionAnswer(chosen.join(', '));
        },
        _formatQuizAnswer(item, value, isCorrect) {
            if (isCorrect) return `${value} — Correct!`;
            const correctText = item.options[item.correctIndex];
            const why = item.explanation ? ` (${item.explanation})` : '';
            return `${value} — Incorrect, the correct answer was "${correctText}."${why}`;
        },
        answerPendingQuestionOther() {
            const value = (this.pendingQuestionOther || '').trim();
            if (value) this._recordQuestionAnswer(value);
        },
        skipPendingQuestion() {
            this._recordQuestionAnswer("Skipped — go ahead with your best guess for this one.");
        },
        dismissPendingQuestion() {
            this.pendingQuestion = null;
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
