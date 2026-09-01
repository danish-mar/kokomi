/**
 * Message rail — replaces the chat scrollbar with a minimap of the conversation.
 *
 * Every message (yours and the character's) gets a tick, sized by how long that
 * message was. Ticks are spaced EVENLY and centred as a group rather than
 * positioned proportionally to scroll height: with five or six messages a
 * proportional layout scatters a few marks across a tall empty strip, whereas
 * even spacing gives a tight, legible cluster that still reads as a map of the
 * conversation. Once there are enough messages to fill the rail the gap
 * compresses and it behaves like a conventional scrollbar again.
 *
 * Hovering — or dragging a finger down it on touch — reveals the message in a
 * bubble; dragging scrubs the transcript, and the chevrons step one message at
 * a time.
 */

// Tick length bounds, in px. A one-word "ig!~" and a long paste should be
// visibly different without the long one dominating the rail.
const RAIL_MIN_LEN = 8;
const RAIL_MAX_LEN = 24;
const RAIL_CHARS_PER_PX = 16;
const RAIL_PREVIEW_CHARS = 120;

// Preferred vertical gap between ticks. Compressed automatically when there are
// more messages than comfortably fit.
const RAIL_GAP = 13;
// Space reserved at each end for the step chevrons.
const RAIL_END_PAD = 26;

// Fisheye: ticks swell toward the focus point (your pointer, or — when you're
// not touching the rail — wherever you're currently reading) and taper off with
// distance, so the rail bends around you instead of reacting one tick at a
// time. Gaussian falloff rather than linear: linear leaves a visible hard edge
// where the effect stops.
const RAIL_FISHEYE_PX = 20;
const RAIL_FISHEYE_SIGMA = 34;

export function getRailActions() {
    return {
        // Rebuild tick geometry from the live DOM. Runs on scroll as well as on
        // message changes because message heights shift while content streams
        // in — cached offsets would drift out of alignment mid-response.
        computeRailTicks() {
            const box = this.$refs.chatBox;
            if (!box || !this.messages.length) { this.railTicks = []; return; }

            const rail = this.$refs.railEl;
            // Falls back to the transcript's height on the first pass, before
            // the rail element itself has been laid out.
            const railH = (rail && rail.getBoundingClientRect().height)
                || Math.max(0, box.clientHeight - 16);
            this.railHeight = railH;

            const boxTop = box.getBoundingClientRect().top;
            const rows = [];
            this.messages.forEach((msg, index) => {
                const text = (msg.content || '').trim();
                if (!text) return;
                const el = document.getElementById('msg-row-' + index);
                if (!el) return;
                // Measured rather than offsetTop: the rows' offsetParent isn't
                // the scroll container, so offsetTop would be relative to the
                // wrong element.
                const offset = el.getBoundingClientRect().top - boxTop + box.scrollTop;
                rows.push({
                    index,
                    offset,
                    name: msg.role === 'user'
                        ? 'You'
                        : (msg.character_name || this.getCharById(msg.character_id)?.name || 'Assistant'),
                    len: Math.round(Math.min(RAIL_MAX_LEN, RAIL_MIN_LEN + text.length / RAIL_CHARS_PER_PX)),
                    text: text.length > RAIL_PREVIEW_CHARS
                        ? text.slice(0, RAIL_PREVIEW_CHARS).trimEnd() + '…'
                        : text,
                });
            });

            // Even spacing, centred as a group; the gap shrinks once the ticks
            // would otherwise overflow the rail.
            const usable = Math.max(0, railH - RAIL_END_PAD * 2);
            const gap = rows.length > 1
                ? Math.min(RAIL_GAP, usable / (rows.length - 1))
                : 0;
            const span = gap * Math.max(0, rows.length - 1);
            const startY = (railH - span) / 2;
            rows.forEach((r, i) => { r.y = startY + i * gap; });

            this.railTicks = rows;
            this.syncRailActive();
        },

        // Track whichever message owns the part of the transcript you're looking
        // at (measured a third of the way down the viewport, which follows
        // reading position better than the very top edge).
        syncRailActive() {
            const box = this.$refs.chatBox;
            if (!box || !this.railTicks.length) { this.railActiveIdx = -1; return; }
            const pos = box.scrollTop + box.clientHeight * 0.3;
            let active = this.railTicks[0].index;
            for (const t of this.railTicks) {
                if (t.offset <= pos) active = t.index;
                else break;
            }
            this.railActiveIdx = active;
        },

        get railVisible() {
            // A single message doesn't need a minimap.
            return this.railTicks.length >= 2;
        },

        _railTick(index) {
            return this.railTicks.find(x => x.index === index) || null;
        },

        get railBubbleText() {
            const t = this._railTick(this.railHoverIdx);
            return t ? t.text : '';
        },

        get railBubbleName() {
            const t = this._railTick(this.railHoverIdx);
            return t ? t.name : '';
        },

        // The point the fisheye bulges around: your pointer while you're on the
        // rail, otherwise wherever you're currently reading — so scrolling makes
        // the rail swell along with you rather than merely recolouring a tick.
        get railFocusY() {
            if (this.railPointerY >= 0) return this.railPointerY;
            const t = this._railTick(this.railActiveIdx);
            return t ? t.y : -1;
        },

        // A tick's rendered width: base length plus a gaussian swell by distance
        // from the focus point. Computed in the style binding rather than baked
        // into railTicks so movement restyles the existing elements instead of
        // rebuilding the whole x-for list.
        railTickWidth(t) {
            const focus = this.railFocusY;
            if (focus < 0) return t.len;
            const d = t.y - focus;
            const boost = Math.exp(-(d * d) / (2 * RAIL_FISHEYE_SIGMA * RAIL_FISHEYE_SIGMA));
            return Math.round(t.len + boost * RAIL_FISHEYE_PX);
        },

        // Nearest tick to a pointer position on the rail.
        _railHit(clientY) {
            const rail = this.$refs.railEl;
            if (!rail || !this.railTicks.length) return null;
            const r = rail.getBoundingClientRect();
            const y = clientY - r.top;
            let best = null, bestDist = Infinity;
            for (const t of this.railTicks) {
                const d = Math.abs(t.y - y);
                if (d < bestDist) { bestDist = d; best = t; }
            }
            return { tick: best, y, height: r.height };
        },

        onRailMove(e) {
            const hit = this._railHit(e.clientY);
            if (!hit) return;
            this.railHeight = hit.height;
            this.railPointerY = hit.y;
            this.railHoverIdx = hit.tick.index;

            if (this.railDragging) {
                this.railMoved = true;
                // Ticks are evenly spaced by message, so dragging scrubs message
                // to message rather than mapping onto raw scroll height.
                this.$refs.chatBox.scrollTop = hit.tick.offset;
            }
        },

        onRailDown(e) {
            this.railDragging = true;
            this.railMoved = false;
            // Pointer capture keeps the drag alive when the finger/cursor
            // wanders off the rail's narrow hit area mid-swipe.
            try { this.$refs.railEl.setPointerCapture(e.pointerId); } catch (err) {}
            this.onRailMove(e);
        },

        onRailUp(e) {
            // A tap (press with no drag) is an animated jump — dragging already
            // scrolled live, so only the tap needs easing.
            if (this.railDragging && !this.railMoved) {
                const hit = this._railHit(e.clientY);
                if (hit) this.$refs.chatBox.scrollTo({ top: hit.tick.offset, behavior: 'smooth' });
            }
            this.railDragging = false;
            this.railHoverIdx = -1;
            this.railPointerY = -1;
        },

        onRailLeave() {
            if (!this.railDragging) {
                this.railHoverIdx = -1;
                this.railPointerY = -1;
            }
        },

        // Chevrons: step to the previous/next message from wherever you are.
        railStep(dir) {
            if (!this.railTicks.length) return;
            const at = this.railTicks.findIndex(t => t.index === this.railActiveIdx);
            const next = Math.min(this.railTicks.length - 1, Math.max(0, (at < 0 ? 0 : at) + dir));
            const t = this.railTicks[next];
            if (t) this.$refs.chatBox.scrollTo({ top: t.offset, behavior: 'smooth' });
        },
    };
}
