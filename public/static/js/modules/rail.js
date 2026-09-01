/**
 * Message rail — replaces the chat scrollbar with a minimap of the questions
 * you've asked.
 *
 * Each USER message becomes a tick (assistant replies are deliberately left
 * out — the rail is for finding "where did I ask about X", and ticking every
 * message would just make it noise). A tick's length reflects how long that
 * message was, its vertical position mirrors where that message actually sits
 * in the transcript, and hovering — or dragging a finger down it on touch —
 * reveals the message in a bubble.
 *
 * Dragging the rail scrolls the transcript, so this genuinely replaces the
 * scrollbar rather than sitting beside one.
 */

// Tick length bounds, in px. A one-word "yes" and a 900-word paste should be
// visibly different without the long one dominating the rail.
const RAIL_MIN_LEN = 9;
const RAIL_MAX_LEN = 30;
const RAIL_CHARS_PER_PX = 14;
const RAIL_PREVIEW_CHARS = 160;

export function getRailActions() {
    return {
        // Rebuild tick geometry from the live DOM. Runs on scroll as well as on
        // message changes because message heights shift while content streams
        // in — cached offsets would drift out of alignment mid-response.
        computeRailTicks() {
            const box = this.$refs.chatBox;
            if (!box || !this.messages.length) { this.railTicks = []; return; }
            const total = box.scrollHeight;
            if (!total) { this.railTicks = []; return; }

            const boxTop = box.getBoundingClientRect().top;
            const ticks = [];
            this.messages.forEach((msg, index) => {
                if (msg.role !== 'user') return;
                const el = document.getElementById('msg-row-' + index);
                if (!el) return;
                // Measured rather than offsetTop: the rows' offsetParent isn't
                // the scroll container, so offsetTop would be relative to the
                // wrong element.
                const offset = el.getBoundingClientRect().top - boxTop + box.scrollTop;
                const text = (msg.content || '').trim();
                if (!text) return;
                ticks.push({
                    index,
                    offset,
                    topPct: Math.min(100, Math.max(0, (offset / total) * 100)),
                    len: Math.round(Math.min(RAIL_MAX_LEN, RAIL_MIN_LEN + text.length / RAIL_CHARS_PER_PX)),
                    text: text.length > RAIL_PREVIEW_CHARS
                        ? text.slice(0, RAIL_PREVIEW_CHARS).trimEnd() + '…'
                        : text,
                });
            });
            this.railTicks = ticks;
            this.syncRailActive();
        },

        // Highlight whichever question owns the part of the transcript you're
        // currently looking at (measured a third of the way down the viewport,
        // which tracks reading position better than the very top edge).
        syncRailActive() {
            const box = this.$refs.chatBox;
            if (!box || !this.railTicks.length) { this.railActiveIdx = -1; return; }
            const pos = box.scrollTop + box.clientHeight * 0.3;
            let active = -1;
            for (const t of this.railTicks) {
                if (t.offset <= pos) active = t.index;
                else break;
            }
            this.railActiveIdx = active;
        },

        get railVisible() {
            // One question doesn't need a minimap.
            return this.railTicks.length >= 2;
        },

        get railBubbleText() {
            const t = this.railTicks.find(x => x.index === this.railHoverIdx);
            return t ? t.text : '';
        },

        // Nearest tick to a pointer position on the rail, plus that position as
        // a 0-1 fraction of the rail's height.
        _railHit(clientY) {
            const rail = this.$refs.railEl;
            if (!rail || !this.railTicks.length) return null;
            const r = rail.getBoundingClientRect();
            const pct = Math.min(1, Math.max(0, (clientY - r.top) / r.height));
            let best = null, bestDist = Infinity;
            for (const t of this.railTicks) {
                const d = Math.abs(t.topPct / 100 - pct);
                if (d < bestDist) { bestDist = d; best = t; }
            }
            return { tick: best, pct, height: r.height };
        },

        onRailMove(e) {
            const hit = this._railHit(e.clientY);
            if (!hit) return;
            this.railHoverIdx = hit.tick.index;
            this.railBubbleTop = (hit.tick.topPct / 100) * hit.height;

            if (this.railDragging) {
                this.railMoved = true;
                const box = this.$refs.chatBox;
                // Same mapping the ticks use (fraction of total content height),
                // so releasing on a tick lands that message at the top of the
                // viewport instead of drifting by a viewport's worth.
                box.scrollTop = Math.min(
                    hit.pct * box.scrollHeight,
                    box.scrollHeight - box.clientHeight,
                );
            }
        },

        onRailDown(e) {
            this.railDragging = true;
            this.railMoved = false;
            const rail = this.$refs.railEl;
            // Pointer capture keeps the drag alive when the finger/cursor
            // wanders off the rail's narrow hit area mid-swipe.
            try { rail.setPointerCapture(e.pointerId); } catch (err) {}
            this.onRailMove(e);
        },

        onRailUp(e) {
            // A tap (press with no drag) is a jump-to-message, animated —
            // dragging already scrolled live, so only the tap needs easing.
            if (this.railDragging && !this.railMoved) {
                const hit = this._railHit(e.clientY);
                if (hit) {
                    const box = this.$refs.chatBox;
                    box.scrollTo({ top: hit.tick.offset, behavior: 'smooth' });
                }
            }
            this.railDragging = false;
            this.railHoverIdx = -1;
        },

        onRailLeave() {
            if (!this.railDragging) this.railHoverIdx = -1;
        },
    };
}
