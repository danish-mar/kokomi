/**
 * Background generation — reconnecting, live conversation glow, and
 * away-from-tab notifications.
 *
 * A response now outlives the request that started it (see
 * app/generation_registry.py), so the UI has to answer three questions it
 * never had to before: which conversations are still generating, how to
 * re-attach to one, and how to tell you a reply landed while you were
 * elsewhere.
 */

// How often to ask the server which conversations are still generating. This
// is the only way to learn about work started in another tab or before a
// reload, so it can't be replaced with purely local bookkeeping.
const ACTIVE_POLL_MS = 4000;

export function getBackgroundActions() {
    return {
        // ── Which conversations are mid-response ─────────────────────────────
        async refreshActiveGenerations() {
            try {
                const r = await fetch('/api/chat/active');
                if (!r.ok) return;
                const d = await r.json();
                const ids = (d.active || []).map(g => g.conversation_id);

                // A conversation that was generating and is no longer listed has
                // just finished. If we streamed it ourselves the transcript is
                // already up to date — reloading it here would replace the
                // messages and replay the load animation after every single
                // message sent, which reads as the chat window refreshing.
                // Only a response that landed somewhere we weren't watching
                // needs pulling in.
                const self = this._selfCompleted || (this._selfCompleted = new Set());
                const finished = this.activeGenerations.filter(id => !ids.includes(id));
                this.activeGenerations = ids;

                const elsewhere = finished.filter(id => !self.has(id));
                finished.forEach(id => self.delete(id));

                if (finished.length) this.fetchConversations();
                if (elsewhere.length) {
                    if (elsewhere.includes(this.currentConvId) && !this.loading) {
                        this.loadConversation(this.currentConvId, { resume: false });
                    }
                    elsewhere.forEach(id => this.notifyResponseReady(id));
                }
            } catch (e) { /* offline or restarting; the next tick retries */ }
        },

        startActivePolling() {
            this.refreshActiveGenerations();
            if (this._activeTimer) clearInterval(this._activeTimer);
            this._activeTimer = setInterval(() => this.refreshActiveGenerations(), ACTIVE_POLL_MS);
            // Coming back to the tab should feel instant rather than waiting
            // out the poll interval.
            window.addEventListener('focus', () => this.refreshActiveGenerations());
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) this.refreshActiveGenerations();
            });
        },

        isGenerating(convId) {
            return this.activeGenerations.includes(convId);
        },

        // ── Re-attaching ─────────────────────────────────────────────────────
        /** Rejoin a response already in progress: replays what was missed,
         *  then keeps streaming live. */
        async resumeGeneration(convId) {
            if (!convId || this.loading) return;
            this.loading = true;
            this.loadingStatus = 'Reconnecting…';
            this.abortController = new AbortController();
            this.streamingConvId = convId;
            let finishedBeforeAttach = false;
            try {
                await this.sendMessageStream(null, [], convId);
            } catch (e) {
                // The response landed in the gap between reading the saved
                // conversation and attaching, so the generation was already
                // gone. The saved copy now has the reply — load it, rather
                // than leaving the user looking at their own message alone.
                finishedBeforeAttach = true;
            } finally {
                this.loading = false;
                this.streamingConvId = null;
                this.refreshActiveGenerations();
            }
            if (finishedBeforeAttach) {
                await this.loadConversation(convId, { resume: false });
            }
        },

        /** Called after opening a conversation — picks the stream back up if
         *  that conversation is still being written. */
        async maybeResume(convId) {
            if (!convId || this.loading) return;
            await this.refreshActiveGenerations();
            if (this.isGenerating(convId)) await this.resumeGeneration(convId);
        },

        // ── Notifications ────────────────────────────────────────────────────
        async enableNotifications() {
            if (!('Notification' in window)) {
                this.showToast('This browser has no notification support', 'error');
                return;
            }
            const res = await Notification.requestPermission();
            this.notificationsEnabled = res === 'granted';
            try { localStorage.setItem('notificationsEnabled', this.notificationsEnabled); } catch (e) {}
            this.showToast(
                this.notificationsEnabled ? 'Notifications on' : 'Notifications blocked by the browser',
                this.notificationsEnabled ? 'success' : 'error',
            );
        },

        /** A short two-note chime, synthesized rather than loaded: the app is
         *  meant to work fully offline, so it must not depend on an audio asset
         *  being fetched. */
        playChime() {
            try {
                const Ctx = window.AudioContext || window.webkitAudioContext;
                if (!Ctx) return;
                const ctx = this._audioCtx || (this._audioCtx = new Ctx());
                // Browsers suspend audio until a gesture; a suspended context
                // would fail silently, so try to wake it.
                if (ctx.state === 'suspended') ctx.resume();
                const now = ctx.currentTime;
                [[880, 0], [1174.7, 0.14]].forEach(([freq, offset]) => {
                    const osc = ctx.createOscillator();
                    const gain = ctx.createGain();
                    osc.type = 'sine';
                    osc.frequency.value = freq;
                    // Ramped, not switched: an abrupt gain change clicks.
                    gain.gain.setValueAtTime(0.0001, now + offset);
                    gain.gain.exponentialRampToValueAtTime(0.12, now + offset + 0.02);
                    gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.28);
                    osc.connect(gain).connect(ctx.destination);
                    osc.start(now + offset);
                    osc.stop(now + offset + 0.3);
                });
            } catch (e) { /* audio unavailable; the notification still shows */ }
        },

        /** Ping only when the reply landed somewhere you weren't looking —
         *  notifying for a response you just watched arrive is noise. */
        notifyResponseReady(convId, msgIdx = undefined) {
            if (!document.hidden) return;
            if (this._notifiedFor === convId) return;
            this._notifiedFor = convId;

            const conv = this.conversations.find(c => c._id === convId);
            const title = (conv && conv.title) || 'Response ready';
            let body = 'Your reply is ready.';
            if (msgIdx !== undefined && this.messages[msgIdx]) {
                const t = (this.messages[msgIdx].content || '').replace(/\s+/g, ' ').trim();
                if (t) body = t.slice(0, 140) + (t.length > 140 ? '…' : '');
            }

            if (this.notificationsEnabled && 'Notification' in window
                && Notification.permission === 'granted') {
                try {
                    const n = new Notification(title, { body, icon: '/images/logo.png', tag: convId });
                    n.onclick = () => {
                        window.focus();
                        this.loadConversation(convId);
                        n.close();
                    };
                } catch (e) { /* some browsers block construction outside a SW */ }
            }
            if (this.notificationSound !== false) this.playChime();
        },
    };
}
