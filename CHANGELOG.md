# Changelog

All notable changes to this project will be documented in this file.

## [v6.2.0] - 2026-09-02

### Changed

- **The chat scrollbar is now a message rail** — a minimap of the questions you've asked instead of a generic scroll thumb. Each user message becomes a tick positioned where it actually sits in the transcript, with its length scaled to how long that message was, and the tick covering what you're currently reading stays highlighted. Hovering a tick reveals that message in a bubble; on touch, dragging down the rail scrolls the transcript while the bubble follows your finger, so you can skim back through a long conversation by feel rather than scrubbing blindly. A tap jumps to that message. The native scrollbar is hidden in favour of it.

## [v6.1.0] - 2026-09-02

### Added

- **Scroll-to-bottom button**: a small floating button fades in once you've scrolled more than a screen's-worth away from the latest message, and smooth-scrolls you back down.
- **Triton devices can carry a description** — a free-text note ("home server, runs Docker + Qdrant") editable per device in Settings → Triton. Devices previously only had a hostname and platform, so the AI had no idea what any given machine actually *was*; the description now appears both in the `triton_list_devices` tool output and in the device roster injected into the system prompt.

### Changed

- **PDF artifacts are no longer a black box.** The artifact card used to show only a title and page estimate — the actual document existed nowhere in the UI until you clicked View and waited for a round-trip through ReportLab. It now renders the markdown inline as a live formatted preview, using the same renderer as chat messages, so you can read the document as it's being written (auto-following the newest line while streaming) and skim it afterwards in a capped, fade-bottomed panel. View/Download still produce the real PDF.

## [v6.0.4] - 2026-08-29

### Added

- **Regression test for the v6.0.3 event-loop-blocking fixes** (`tests/test_event_loop_blocking.py`): patches the PDF-artifact render with a controlled synchronous sleep and asserts a concurrent, unrelated `/health` request still returns in milliseconds instead of waiting behind it. Verified against the commit immediately before the fix (`a0e44ba`) via a git worktree using this checkout's venv: the concurrent request took ~1.0s there (blocked) vs. ~0.01s on the fixed commit (served concurrently).

## [v6.0.3] - 2026-08-29

### Fixed

- **Several unwrapped blocking calls were freezing the entire app** (a single-process, single-event-loop server — one slow synchronous call anywhere stalls every other request, WebSocket, and background task, not just the one that made it):
  - Rendering a PDF artifact (`POST /api/artifacts/render-pdf`) did ReportLab layout plus a blocking `requests.get` per embedded image directly on the event loop — viewing/downloading any AI-generated PDF froze the whole app for the duration.
  - The App Store's catalog fetch and app/persona install (`requests.get`, `subprocess.run` for pip/uv, up to 120s) ran unwrapped in their route handlers.
  - The software updater (`/update/check`, `/update/run`) ran every `git`/`uv`/`pip` `subprocess.run` call (up to 60s) directly on the loop.
  - The long-term-memory background save (after every chat turn) called the synchronous `save_memory()` directly instead of off-thread, unlike `search_memories()` which got this exact fix in v5.11.0.

  All now run via `asyncio.to_thread`, matching the pattern already used elsewhere (RAG/Spaces queries, workflow tool dispatch).

### Changed

- **Faster request handling on repeat calls**, not just fewer freezes:
  - `GeminiDirectLLM` no longer constructs two fresh `genai.Client` transports on every single chat message — the client is now cached per API key and reused (the LLM wrapper itself still isn't cached/shared across requests, since `bind_tools()` mutates it in place and a shared instance would let one request's tools leak into another's concurrent generation).
  - Added a shared, connection-pooled `httpx.AsyncClient` (`app/httpc.py`) for hot/repeating outbound calls — wired into the same-origin image proxy (`/api/img`, hit once per gallery image) instead of opening a fresh client per request.

## [v6.0.2] - 2026-08-29

### Fixed

- **Composer tier slider showed the wrong model name for a tier set to the Custom provider.** Its tooltip still checked for the provider name `local`, from before that provider was renamed to `custom` — so a Fast/Smart tier pointed at a Custom endpoint fell through to the wrong pref field (`fast_model_name` instead of `fast_custom_model`) and showed a stale/default value instead of the actual configured model.

## [v6.0.1] - 2026-08-29

### Added

- **Composer model-tier control**: a brain-icon button beside the attach button now morphs, on hover or click, into a progress bar with three presets — Fast, Normal, Smart — each pointing at its own configurable provider/model in Settings (mirrors how the Title and Atlas models already get their own slots). The icon itself rides the bar as the knob and travels to the active preset's stop, with the accent fill sweeping up behind it (full width at Smart); a tooltip above the knob names the resolved model. Selecting a tier picks the model for that message via a new `model_tier` field on the chat request, bypassing per-character model pinning for that one send. Persists across reloads like the web-search toggle.
- **Sidebar open/closed state now persists across page reloads** instead of always reopening on desktop — it previously ignored how you'd last left it.

### Changed

- **Message header decluttered**: long-term-memory access no longer renders as a full-width bordered card in the transcript — it's now a small muted dot beside the model badge, with an accent ring that sweeps its edge while the lookup is running. The remaining tool-call chips (web search, MCP tools) were similarly muted (no more accent border/background by default) and turned into real `<button>`s with `aria-expanded`, replacing the `▸`/`▾` text-glyph toggle with an animated `fa-chevron-down`.

### Fixed

- **Model badge on assistant messages had no vertical padding** — `py-0.25` isn't a real Tailwind spacing step (the app doesn't extend the scale), so it silently generated nothing and the badge sat flush against its own border.

## [v6.0.0] - 2026-07-25 — "coral atelier"

### Added

- **Spreadsheet canvas — a third canvas mode alongside code and document.** `mode="spreadsheet"` mounts **x-spreadsheet** (vendored, MIT), an Excel-like grid with real formulas (`=SUM(A2:A10)`, `=B2*C2`). CSV is the canonical stored/streamed/patched representation, mirroring how the code canvas stores raw source and the document canvas stores markdown, rather than round-tripping x-spreadsheet's own per-cell JSON through the LLM. **Ctrl+I** opens the same inline AI-prompt box as the code canvas, addressing the current cell selection by A1 range (`B2` or `B2:D5`) instead of a line range; right-click uses x-spreadsheet's own native menu (row/col insert/delete, etc.). Every AI edit carries an anchor verified before it's applied, same discipline as the existing line/block editors. Export to **XLSX** (numbers round-trip as real numbers, so formulas keep working in Excel), CSV, or HTML.
- The spreadsheet grid **follows the app's actual theme**, including a custom accent color — not a fixed dark palette. x-spreadsheet paints its header strip, gridlines, and cell fills onto a `<canvas>` with colors hardcoded in the minified bundle; the vendored file is patched at download time to read those colors from `getComputedStyle` against the app's own CSS custom properties instead.
- **Custom LLM provider now requires a real API key and lists its models.** The old "local" provider (assumed an unauthenticated OpenAI-compatible server) is renamed **custom** throughout, with a display name, a required API key, and a live model list fetched from the configured endpoint. Existing installs migrate automatically on first launch after upgrading.
- **Multiple named custom providers.** A **(+)** button on the provider selector saves multiple custom endpoints as presets, so chat, title generation, and Atlas can each point at a different self-hosted model instead of sharing one global endpoint.
- **Searchable, company-grouped model picker.** Every model dropdown across chat/title/Atlas settings (10 in total) is now a searchable, filterable list grouped by inferred company/family, replacing a native `<select>` that became unusable once a provider listed hundreds of models.

### Fixed

- **A closed canvas panel made the AI forget what it was editing.** The frontend only tells the backend which canvas is open while its panel is visibly expanded; closing it back to an inline artifact card sent no id at all, so a plain follow-up like "add three more records" showed the model nothing and it generated a brand new artifact instead of continuing the old one. The backend now falls back to the most recent canvas artifact in the conversation when no id is sent.
- **x-spreadsheet's horizontal scroll did nothing in Firefox.** Its trackpad/wheel panning is wired to the legacy non-standard `mousewheel` event, which Firefox never fires (only `wheel`) — vertical scroll worked anyway since it rides on a scrollbar div's native overflow, but horizontal was silently dead. A Firefox-only supplementary handler now drives the scrollbar directly.
- A model regenerating a whole spreadsheet could open with a stray blank CSV line, shifting every real row down by one without anything looking obviously wrong until the row numbers were checked.

### Changed

- Vendor assets shrank to a 4px scrollbar app-wide; inside the spreadsheet canvas that scrollbar is the only way to grab and drag its scroll track, so it's restored to a usable size there.

## [v5.11.0] - 2026-07-22 — "coral atelier"

### Added

- **Canvas — an editable artifact that opens beside the chat.** A new `type="canvas"` artifact splits the window into chat (40%) and a working surface (60%), with a draggable divider whose position persists. Two modes:
  - **`mode="code"`** mounts **Monaco**, the editor core VS Code itself is built on — syntax highlighting, minimap, multi-cursor, bracket colourisation.
  - **`mode="document"`** mounts a Word-style page (fixed 8.5×11in sheet, 1in margins, sticky ribbon) that follows the app theme rather than imitating Word's white chrome.

  Both editors are **vendored locally** (Monaco 0.52.2 across 41 files, Quill 2.0.3), so the canvas works fully offline like the rest of the app. Content **types out live** as the model writes it, and opening a canvas mid-generation resumes the stream rather than showing a frozen partial. Your edits autosave (debounced) back onto the stored artifact, and the canvas's *current* contents — including your edits — are injected into the next turn's system prompt, so "change this bit" acts on what you're actually looking at.

- **Surgical AI edits inside the canvas — patches, not rewrites.** Asking the AI to change something no longer regenerates the whole file, and no longer posts a message into the chat. The model is shown the content *addressed by position* and returns a minimal patch:
  - **Code** is addressed **by line** (`start_line`/`end_line`), applied through Monaco as a single undoable step — Ctrl+Z reverts the whole AI edit, scroll position survives, and changed lines flash briefly so you can see what moved.
  - **Documents** are addressed **by block** (paragraph/heading/list). Quill stores its content as HTML with no newlines, so line numbers would collapse the entire document into "line 1" and silently degrade into a full rewrite; paragraphs are the meaningful unit for prose. A block keeps its type — a heading stays a heading — and model output is HTML-escaped, so it can't restructure or inject markup.

  Every edit carries an **`expect` anchor** — the original text it believes it's replacing — which is verified before anything is applied. Line numbers are easy for a model to be off-by-one on, and a confidently-wrong range corrupts a file silently; a mismatched anchor now refuses the edit instead of guessing. Overlapping ranges, duplicate block edits and out-of-range targets are refused the same way, and edits apply bottom-up so earlier positions stay valid.

- **Canvas editing affordances.** Right-click inside a **document** for an AI menu (Improve writing, Fix spelling & grammar, Make shorter, Expand, Simplify, Continue writing, Ask AI…) plus a plain editing row — cut, copy, paste, delete, bold, italic, underline, strikethrough — that never touches the model. **Ctrl+Space** opens a free-form instruction box anchored at the caret. In the **code** canvas the AI actions live in Monaco's own right-click menu and command palette (Explain, Refactor, Find and fix bugs, Add comments, Add a test), with **Ctrl+I** for a free-form prompt (deliberately not Ctrl+Space, which Monaco reserves for autocomplete).

- **Export a canvas** as **DOCX** (the default for documents), **PDF**, Markdown, HTML or plain text, via a split download button. Quill HTML and raw markdown are normalised into one block model, so headings, lists, quotes and code survive into Word and PDF. A code canvas offers its own source file first. Exports flush unsaved edits first, so you never download a stale copy.

- **Keyword completions for languages Monaco has no language service for.** Monaco ships real IntelliSense only for TypeScript/JavaScript, JSON, CSS and HTML; everything else gets syntax highlighting alone (genuine smarts for Python/Java/C++ come from language servers, which can't run in a browser tab). The code canvas now registers keyword and builtin completions for Python, Java, C++, C#, Go, Rust, Ruby, PHP, shell and SQL, alongside Monaco's word-based suggestions.

- **Choose the model that names your conversations** (Settings → General → *Conversation Title Model*). Titles are one short line, so this defaults to the small fast model used before — but it can now point at any provider (Groq / Gemini / NVIDIA / local) independently of your chat model, mirroring how Atlas already picks its own planner.

- **Question cards gained tabs, quizzes and multi-select.** A single `<Artifact type="question">` can now carry several questions at once (`{"questions": [...]}`), shown as tabs you answer one at a time. Quiz mode (`quiz: true, correctIndex, explanation`) reveals correct/incorrect styling with the explanation before moving on, and multi-select questions (`multiSelect: true`) offer checkboxes with a Continue button — recognised from the JSON flag *or* from question text like "select all that apply". Skip moved out of the option list into the card header.

- **The composer glows on page load** — a soft accent light traces the outer edge of the message box and fades out. Respects `prefers-reduced-motion`.

### Fixed

- **`--font-mono` was never defined anywhere.** It was referenced in five places (inline code, artifact bodies, diagram errors, the code canvas), and an unresolved `var()` invalidates the whole declaration — so every one of those had been silently falling back to the UI sans-serif. Now declared globally, which fixes those pre-existing spots as well as the editor.

- **New vendored assets were never downloaded on existing installs.** `download_vendor_assets_if_missing()` returned early whenever the `.download_complete` sentinel existed, so any asset added by a later release (Monaco, Quill, a new language) would never be fetched on a machine that had already run once. It now diffs the asset list against what's actually on disk and only skips when nothing is missing.

- **The word "open" was triggering a spurious tool call and doubling every such request.** `open_url`'s description told the model to use it "immediately when the user asks to … 'open' something", so "open a canvas" fired it at a placeholder URL — forcing a second full generation before any real answer began. Its description now requires a genuine external destination and explicitly excludes in-app actions, placeholder URLs, and the mere presence of the word.

- **Long-term memory retrieval was freezing the event loop.** `search_memories()` performs a blocking embedding HTTP call and a blocking Qdrant query, but was called directly inside an `async` coroutine — so `asyncio.gather` couldn't parallelise it, and nothing else in the app could run while it worked (measured: **zero** event-loop ticks during the search, and SSE tokens couldn't reach the browser). Now dispatched to a worker thread: **3.8× faster** across four characters, and the loop stays responsive.

- **Rate limits no longer look like a hang.** A provider tokens-per-minute limit surfaced only as a long unexplained wait while the client retried with backoff. The error is now named explicitly, with a note that large prompts consume the per-minute budget quickly.

- **NVIDIA completions were capped at 4096 tokens**, truncating long-form requests (a multi-page document, a long story) well before the model was done. Raised to 16384.

- **PDF artifacts ignored requested page counts.** The instruction never said what "a 10-page story" means in prose, so the model produced a compressed outline. It now states that a page count means physical printed pages of roughly 400–500 words each, and to say so explicitly rather than silently returning a short version.

- **Conversation titles blocked the event loop** — `generate_title()` used a synchronous `.invoke()` inside an `async` function. Now awaited properly.

- **Canvas artifacts could render as a raw JSON dump.** Artifacts whose inline placeholder wasn't found in the message body fell through to a hardcoded fallback template that printed raw content, bypassing the type dispatcher. Both paths now share one renderer, so charts, diagrams, PDFs, questions and canvases always get their proper card.

- **Canvas artifacts opened in the wrong place when the model mislabelled them.** The base artifact rule (`type="language"`) is repeated many times in the system prompt and reliably outweighed the single canvas rule, so canvases arrived tagged `type="cpp"` and opened in the old modal. The carve-out now lives *inside* the repeated block so it carries the same weight, and the client identifies a canvas by its `mode` attribute rather than trusting `type` alone.

- **The document editor's right-click menu never appeared**, and its editor could be silently corrupted. Browser extensions that hook `contenteditable` (Grammarly and similar) attach their own `contextmenu` handler to the editor and swallowed ours — the handlers now bind on the container in the capture phase, so they fire first, survive editor remounts, and work in the page margins. The editor also opts out of such extensions entirely (`data-gramm="false"`), since they splice their own nodes into the DOM and can corrupt Quill's internal model.

- **The document editor grew a second toolbar on every remount.** Quill inserts its toolbar as a *sibling* of the host element, so clearing the host alone left the old one behind. Teardown now sweeps the whole sheet.

- **Live streaming into a document could corrupt the text.** Painting the editor fired Quill's `text-change`, whose handler overwrote the accumulating markdown with the editor's rendered HTML — after which the next delta was appended to HTML. The raw stream is now tracked separately from the editor's contents, with programmatic writes flagged so change handlers ignore them. Deltas arriving while an editor is still loading are also buffered and replayed instead of being dropped.

### Changed

- Question-card tab switching is now smooth. The card was keyed on the active index, so Alpine destroyed and recreated the node on every switch and no CSS transition could run; a constant key lets it patch the same node instead.
- Debug mode now reports pre-LLM setup time, time-to-first-token and generation time separately, so a slow response can be attributed to setup, the model, or a wasted tool round.

## [v5.10.2] - 2026-07-10

### Changed

- **Merged the clarifying-question panel into the composer itself**: rather than floating as its own separate card above the message box, the question now lives inside the same rounded composer, in the slot the Space Selector bar normally occupies — expanding the composer's height fluidly (via the app's existing `x-collapse` animation) instead of stacking a second box on top. If a Space is active when a question appears, the Space Selector bar is hidden while the question is showing (no double bar) and reappears once it's answered or dismissed.

## [v5.10.1] - 2026-07-10

### Changed

- **Question cards now float above the composer instead of appearing as an inline artifact**, and work regardless of the Artifacts toggle. Previously the clarifying-question card rendered inline in the message transcript and only worked when Artifacts was on; it now docks as its own card directly above the message box (expanding the input area upward, closable with an X), and question-tag parsing runs unconditionally so it keeps working even with Artifacts disabled — the model just won't emit other artifact types (charts/PDF/code) in that case. Once answered, the historical question collapses to a quiet one-line note in the transcript instead of staying as a full interactive card.

## [v5.10.0] - 2026-07-10

### Added

- **Interactive question cards**: when the AI needs you to make a choice or clarify something before it can answer well, it can now pop an interactive question card instead of writing a paragraph of questions — a titled question with 2–5 numbered options, an optional free-text "Something else…" row, and an optional Skip button. Tapping a choice (or typing your own, or skipping) sends it as your next message and the AI continues with your answer in hand. Built on the existing artifact pipeline as a new `type="question"` artifact whose body is a small JSON spec (`{question, options, allowOther, allowSkip}`); answered cards lock to the chosen option and stay that way across re-renders and conversation reloads. Gated by the existing Artifacts toggle.

## [v5.9.1] - 2026-07-09

### Fixed

- **Forward button now appears on PDF (and other document) artifacts too**: the Forward action added in v5.9.0 only lived in the expanded artifact viewer, but PDF artifacts render as their own card and never open that viewer — so there was no Forward button on them. PDF cards now have a Forward button alongside View/Download; clicking it renders the actual PDF bytes and, via a small device-picker popover, saves them to the chosen online machine's `~/Documents`. The forward endpoint now accepts base64 content so binary files (not just text artifacts) forward correctly. The picker is a reusable component (`KokomiForward`) so future document cards can reuse it.

## [v5.9.0] - 2026-07-09

### Added

- **Forward an artifact straight to a paired computer**: the artifact viewer now has a **Forward** button next to Copy/Download. Clicking it drops down a list of your Triton machines that are currently online; picking one saves the artifact into that machine's `~/Documents` folder and toasts the path it landed at. New endpoint `POST /api/triton/devices/{id}/forward` ({filename, content}) — purpose-built so it only ever writes into `~/Documents` (filename is basename-sanitized, no traversal), and the write still has to clear the client's own `--allow-write` gate (a machine without writes enabled comes back with a clear "file writing is disabled" message rather than failing silently). Only online machines are listed, since an offline one can't receive the file.

## [v5.8.0] - 2026-07-09

### Added

- **Triton gains six new powers on paired machines** (each surfaced as a chat tool, each with its own gate). Always-on additions are read-only; the rest are opt-in per machine:
  - **File writes** (`triton_write_file`) — off unless the client runs with `--allow-write`; confined to the shared `--allow` folders (a path outside is refused), creates parent folders, appends or overwrites, capped at 25 MB.
  - **Open a URL in the browser** (`triton_open_url`) — off unless `--allow-gui`; only `http(s)` URLs.
  - **Screenshot the desktop** (`triton_screenshot`) — off unless `--allow-gui`; grabs the screen via whatever's installed (`grim` on Wayland; `scrot`/`maim`/`gnome-screenshot`/`spectacle`/ImageMagick on X11) and embeds the image in chat.
  - **Clipboard read/set** (`triton_clipboard_get` / `triton_clipboard_set`) — off unless `--allow-gui`; uses `wl-clipboard` or `xclip`/`xsel`.
  - **Process watching** (`triton_list_processes`) — always on, **read-only**: a CPU-sorted snapshot via `ps`. Triton cannot kill processes.
  - **Service watching** (`triton_list_services`) — always on, **read-only**: systemd unit states via `systemctl`. Triton cannot start or stop services.
- Machines advertise exactly which of these they've enabled (via the `run_command`/`write_file`/`open_url`/… capabilities), and the chat system-prompt note lists per-machine which opt-in powers are live so Kokomi knows what it can attempt. A disabled or blocked action always returns a clear permission error rather than a silent success. New client flags: `--allow-write`, `--allow-gui` (plus `KOKOMI_ALLOW_WRITE` / `KOKOMI_ALLOW_GUI` env equivalents).

## [v5.7.0] - 2026-07-09

### Added

- **Triton can now run commands on paired machines (opt-in, doubly gated)**: beyond reading files, the chat model can execute shell commands on a mooring via a new `triton_run_command(device, command, cwd)` tool. Safety is enforced entirely on the client (the boundary), not the server:
  - Execution is **off by default** — the Linux client must be started with `--allow-exec` (or `KOKOMI_ALLOW_EXEC=1`).
  - An optional **command whitelist** (`--allow-cmd git --allow-cmd ls`, repeatable, or `KOKOMI_ALLOW_CMD`) restricts which binaries may run; when set, shell operators (`| ; & > \` $()`) are rejected so a command can't chain past the allowlist.
  - Every command's **working directory is pinned inside an `--allow` folder**; a `cwd` outside the shared roots is refused.
  - Commands are capped at **300 s** and their output at **100 KB per stream**; results come back with exit code, stdout, and stderr.
  - Machines advertise the `run_command` capability so the chat prompt knows which moorings have exec enabled; a disabled or blocked command returns a clear permission error rather than a silent success.

## [v5.6.0] - 2026-07-09

### Added

- **Triton poll transport (works through nginx / Cloudflare)**: the WebSocket transport needs `wss://` + upgrade headers and breaks on proxy idle timeouts, so remote/hosted setups failed with "not a valid url scheme". Added an HTTP long-poll transport that is just plain HTTPS and passes through any reverse proxy or CDN. The Linux client now defaults to `--transport poll` (`pip install requests`); `--transport ws` remains for low-latency LAN use. Point the client at a hosted server with `--server https://your-host`. New endpoints: `enroll`, `pair-status`, `poll`, `result`. Under the hood both transports share one per-device command queue + result-future registry.

### Fixed

- **Client falsely reported "Reconnected" and could get stuck unpairable**: the client printed success the moment it had a saved token, without waiting for the server to accept it — so a revoked/stale token left it "connected" with no way to pair (no code shown). The handshake now waits for the server's verdict: a valid token → online; an invalid one → the server replies `unpaired` and the client discards the stale token and shows a fresh 8-digit code; no token → shows the code. Applies to both transports.
- **Triton settings UI restyled** to match the rest of the app: a hero card with an icon chip, device rows with rounded platform chips, online/offline status pills, and proper empty states — instead of the previous boxy panels.

## [v5.5.1] - 2026-07-07

### Added

- **Triton is now usable from chat**: v5.5.0 shipped the pairing/plumbing, but the conversational model had no way to use it (asked "do you have access to my computer?" it correctly said no). The chat model can now reach paired machines through three read-only tools — `triton_list_devices` (which machines are paired/online), `triton_list_dir` (browse a folder), and `triton_fetch_file` (pull a file back into the chat as a download link). Wired into both the blocking and streaming chat paths, gated on there being paired devices, with a system-prompt note listing the reachable moorings. The client's per-folder allowlist remains the security boundary.

## [v5.5.0] - 2026-07-07 — "triton's conch"

### Added

- **Triton — remote moorings (Phase 1)**: pair your own computers so Kokomi can reach in and act on them. A lightweight client daemon (Linux first, in `clients/triton-linux/`) connects out to the server over a WebSocket (NAT-friendly), auto-discovers the server on the LAN via a UDP beacon, and prints an **8-digit code** you enter in **Settings → Triton** to pair. Once paired, the server can dispatch allowlisted, **read-only** actions — `list_dir` and `read_file` (fetch a file back into the app) — with the client enforcing its own per-folder allowlist so the server only ever sees what that machine permits. *Atlas plans, Triton acts.*
  - New `triton_devices` table + storage layer; core runtime in `app/triton.py` (live-connection registry, pending-pairing sessions, command request/response routing, LAN discovery beacon); browser-facing REST + the client-facing agent WebSocket in `app/routers/triton.py`.
  - New **Triton** settings tab (circle-nodes icon): discovered clients with pair-by-code, paired moorings with online status / last-seen / revoke, and an inline folder browser to test a mooring.
  - Device tokens are stored only as a SHA-256 hash server-side; the client keeps the raw token at `~/.config/kokomi-triton/state.json` (chmod 600). Revoking from the UI closes the live connection and forgets the device.

## [v5.4.5] - 2026-07-07

### Fixed

- **Compiled documents duplicated every section**: the multi-agent PDF worker built its output by globbing every `.md` file in the run's workspace, which holds both each researcher's intermediate section file **and** the writer's already-merged report — so each topic appeared two or three times (once raw, once inside the merge, often with slightly different wording). It read like the agents were out of sync, but it was the same content duplicated. The PDF worker now assembles from its direct dependencies: if a writer/compiler dependency exists it renders only that merged output, otherwise it concatenates the researcher dependencies; the old workspace glob remains only as a last-resort fallback.

## [v5.4.4] - 2026-07-07

### Fixed

- **Stale JS after deploys (recurring "startWorkflow is not defined")**: our own JS/CSS was served with a 7-day `Cache-Control: max-age`, so browsers kept running week-old cached code against freshly server-rendered HTML — e.g. an `atlas.html` that calls `startWorkflow()` while the cached `atlas.js` predates that method. A plain refresh couldn't reliably bust it. `/static/js/` and `/static/css/` (including `app.js`'s ES-module imports) are now served `no-cache` so the browser revalidates every load — a cheap 304 when unchanged, fresh bytes the instant a new build ships. Vendored libraries and images keep the long cache.
- **Atlas planning screen ignored the theme**: the "Decomposing request" loader card hardcoded the dark palette, so it rendered as a dark card with low-contrast text on the light theme and ignored custom swatches/accent. It now uses the app's semantic theme variables and adapts to light/dark like the rest of the UI.

## [v5.4.3] - 2026-07-07

### Fixed

- **Workflow WebSocket reconnect storm**: the Atlas summary and detail sockets reconnected on a fixed 3-second timer, so a socket that couldn't establish (e.g. the reverse proxy not upgrading `wss`, or the event loop briefly busy) hammered reconnect attempts indefinitely, flooding the console. Reconnects now use exponential backoff (1s→2s→…→30s cap) that resets on a successful connection.
- **Flash of unstyled content on load**: because Tailwind runs as an in-browser JIT compiler, pages briefly rendered unstyled before its styles were injected. The body is now hidden until the DOM is parsed plus one frame (Tailwind has compiled by then), with a 1.5s fail-safe so the page can never stay hidden. The zero-build runtime-JIT setup is kept intact.

## [v5.4.2] - 2026-07-07

### Fixed

- **Whole app hangs while an Atlas workflow runs**: built-in worker tools (`web_search`, `scrape_page`, `pdf_export`, `docx`/`pptx`/`excel_export`, `shell_exec`) are synchronous and were invoked directly on the single asyncio event loop, freezing it for the tool's entire duration (up to a 15s scrape or a full document render). That stalled every other request and dropped the live workflow WebSockets — with CPU staying low the whole time (a blocked loop waiting on I/O), which is why server usage looked fine yet the UI lagged. Each blocking tool/exporter call now runs via `asyncio.to_thread`, keeping the loop responsive during a run.

### Changed

- **More cohesive multi-agent output**: parallel workers run in isolation and previously drifted — inconsistent date framing ("June" vs "this week"), mismatched section styles, and thin/dropped sections when the compiler merged them. Every worker prompt now carries a shared run context: the absolute current date (so "recent/latest" resolves consistently and facts get explicit dates) plus a common house style. The writer/PDF compiler is additionally instructed to normalize all sections, add a title + executive summary + table of contents, and keep every topic as an equal top-level section.

## [v5.4.1] - 2026-07-07

### Fixed

- **Atlas recurring schedules 404**: the schedule sidebar (`loadSchedules`/`saveSchedule`/`toggleScheduleActive`/`deleteSchedule` in `atlas.js`) called `GET/POST/PUT/DELETE /api/workflows/schedules*`, but no such routes existed — `GET /api/workflows/schedules` was silently swallowed by the earlier `GET /api/workflows/{run_id}` route (matching `run_id="schedules"`), producing a 404 on every Atlas page load. Added the four schedule endpoints, backed by the existing `app/scheduler.py` engine, registered ahead of the `/workflows/{run_id}` routes so the literal path segment can't be shadowed again.

## [v5.4.0] - 2026-07-06

### Added

- **Atlas dry-run DAG + checkpoint gates**: Workflow runs now start as an editable **draft** plan instead of auto-executing — review or tweak the DAG, then press Run. Any task can be flagged as a **checkpoint**; the scheduler pauses the whole run before that node executes and waits for approval from the web UI or a Telegram `/approve <run_id>` / `/reject <run_id>` command (with a best-effort DM when a run pauses). The planner prompt now teaches the model when to gate a step (irreversible/sensitive side effects) versus leaving read-only steps ungated.
- **NVIDIA NIM embeddings for RAG**: A second embedding provider alongside Gemini, selectable from Settings (provider toggle + curated/live NVIDIA model list), with a provider-aware embedding client handling each service's doc/query conventions.
- **Redesigned Spaces page**: Glass cards matching the rest of the app, stat pills (file/chunk counts, Ready badge), a reindex banner + button for spaces flagged as needing it, an inline **"Ask this Space"** semantic search box, and a **"Chat"** button that deep-links a space straight into the main chat window as context.
- **Inline PDF artifacts in chat**: The AI can emit a PDF artifact for content meant to be an actual document (report, resume, letter, invoice) instead of a chat reply. The card shows a title and estimated page count with View/Download — the PDF is only rendered on demand, nothing is generated just because the artifact appeared.
- **Action-chip polish**: staggered entrance animation on finished chip rows, a shimmer skeleton while a chip block is still streaming in (replacing a raw-JSON flash), a "+N more" overflow guard past six chips, new `copy`/`set` verbs, `confirm` gates, and `primary`/`ghost`/`danger` variants.
- **ChatGPT-style message editing & branching**: Editing a sent message (or regenerating a reply) archives the existing continuation as a branch variant instead of destroying it, then resends and attaches the new reply as a sibling. A `< i/N >` control navigates between variants instantly, with no LLM call.

### Fixed

- **RAG silent zero-retrieval failures**: the default embedding model (`models/gemini-embedding-2`) was an unstable identifier whose vectors drifted out of self-compatibility over time, quietly killing retrieval on existing spaces. Switched the default to the stable GA `gemini-embedding-001`, added a one-time migration for stored prefs, and added per-space embedding-identity tracking so a mismatch surfaces as a "needs reindex" state instead of crashing or returning nothing. Also lowered the overly strict cosine `score_threshold` (0.4 → 0.2) that was filtering out genuine matches, and moved file ingestion off the event loop.
- **NVIDIA model dropdowns showing embedding/rerank models**: the chat and Atlas NVIDIA model pickers listed every NVIDIA model, embedders included; now filtered to chat-appropriate models only.
- **PDF export duplicate table row**: a missing `continue` after table parsing let the stale first table line fall through and get re-appended as a stray paragraph beneath the rendered table.
- **Software updater "branch doesn't exist" failures**: the updater ran git commands with no explicit working directory and no handling for a detached-HEAD checkout (common in Docker images built from CI), which produced errors that read as a missing branch. It now fetches origin first, detects and recovers from detached HEAD by checking out the remote's actual default branch, and pulls that branch explicitly rather than relying on ambient upstream-tracking config. Also fixed local changes being `git stash`ed before a pull and never restored afterward.
- **Branch navigation losing its arrows / scroll position**: switching to the newest branch (or reloading the page) could make the `< i/N >` control disappear because branch metadata was never stamped onto the message itself; switching branches could also jump the viewport when the new variant had a different height. Both fixed.

## [v5.3.0] - 2026-06-28

### Added

- **Telegram Bot Bridge**: A full Telegram integration alongside the WhatsApp bridge. Point the bot at any character, talk to it 1:1, and reuse the same MCP tools, history, and thinking-mode forwarding as the web chat. Configure everything from Settings — bot token, target character, context-history depth, an optional user allowlist, and show/hide thinking.
  - **Polling mode (default)**: long-polls `getUpdates` in a background loop, so no public URL or webhook is required — it works on a laptop or behind NAT. Start/Stop from Settings with a live status badge.
  - **Webhook mode**: one-click `setWebhook` registration using the server's own origin for public deployments. The two modes are mutually exclusive, and the bridge clears a stale webhook before polling so `getUpdates` never 409s.
  - **Profile sync**: pushes the selected character's name and description to the bot (the avatar is set manually via BotFather, which Telegram's API can't automate — surfaced as a note).
- **Rich Message Widgets**: AI message bubbles now upgrade plain markdown into interactive widgets, rendered through the same renderer + `MutationObserver` hydration used by charts/diagrams (everything degrades to text if a widget can't mount).
  - **Images**: markdown images become figures with captured pixel dimensions and a click-to-expand lightbox (with "open original"). Remote URLs are fronted by the `/api/img` proxy.
  - **Video**: a markdown link to a direct video file (`.mp4`/`.webm`/`.ogg`) auto-renders a player; a ```kokomi-video``` block adds poster/title.
  - **Tables**: GFM tables become **sortable** (numeric-aware) and **filterable**, with images in cells collapsing to thumbnails that open in the lightbox.
  - **Action chips**: a ```kokomi-actions``` JSON block renders interactive pills with verbs `send` / `fill` / `url` / `copy` / `set` (update a preference inline), optional `icon` / `variant` (`primary`/`ghost`/`danger`) / `confirm` (a one-tap approval gate for stateful actions), and a "+N more" overflow guard past six.

### Changed

- **Media prompting**: the system prompt (streaming and non-streaming) now documents the widget conventions so the model emits raw markdown/widget blocks instead of fenced source. The `open_url` instruction was softened so it no longer fires merely to show or embed media inline.

### Fixed

- **Workflow engine runaway loop**: the multi-agent engine could loop indefinitely when `restart_subtree`/`full_restart` reset attempt counters with no global ceiling and the circuit breaker's error signature was too brittle to match. Added hard caps (loop iterations, global restarts, total runtime) and a normalized error signature so identical failures trip the breaker reliably.
- **Scheduler decay sweep back-off**: a failing daily memory-decay sweep retried every 30s instead of backing off; the timestamp now advances before the attempt so a failure waits a full day.
- **Telegram reliability**: the token now persists correctly (it was dropped because `telegram_*` fields weren't declared on the prefs model, and separately clobbered by a full-prefs save race — fixed with a dedicated set-token endpoint and format validation), and the bot replies again after a lingering webhook previously blocked polling with 409 Conflict.
- **Widget video proxying**: video sources load directly from origin instead of being routed through the image-only `/api/img` proxy (which returns 415 for non-image content); only the poster image is proxied.

## [v5.2.9] - 2026-06-12

### Added

- **Live AI Image Galleries**: The assistant can now show real photos inline. A new `search_images` tool (Tavily or SearxNG, per the active search provider) returns image URLs that render as a themed, justified-rows gallery above the reply. The AI is prompted to use it **proactively** for visual topics (places, products, food, people, landmarks…), not just when explicitly asked.
- **Fullscreen Lightbox**: Tap any gallery image to open a fullscreen viewer with prev/next, keyboard arrows, and Esc to close. Opens instantly via the cached thumbnail, then upgrades to full resolution in the background.
- **Sidebar Chat Thumbnails**: Conversations that produced images now surface one as the card thumbnail.
- **Same-Origin Image Proxy** (`/api/img`): Fetches remote images server-side and re-serves them from our own origin, sidestepping Cross-Origin-Resource-Policy blocks, mixed-content, and Referer-based hotlink protection (with content-type sniffing for mislabeled hosts).
- **Resizable Sidebar**: Drag the right edge to resize; the width persists client-side (localStorage) across sessions.

### Changed

- **Sidebar Redesign (Pinterest aesthetic)**: Larger, airier masonry cards with bold titles, generous radius and soft shadows. Chats with an image render as **hero cards** — a full-bleed image with the title overlaid on a gradient scrim. Fully theme-aware (light/dark).
- **Mobile Sidebar**: Now covers the full screen on phones, with a dedicated close button.
- **Image-search prompting** is more directive so smaller models reliably show pictures for visual topics.

### Fixed

- Lightbox no longer flashes the previously viewed image while a large original loads.
- Gallery images that hosts block cross-origin (e.g. restrictive CORP) now load via the proxy instead of leaving blank gaps; genuinely dead URLs are hidden cleanly.

## [v5.2.0] - 2026-06-11

### Added

- **Live AI Charts (Chart.js)**: The assistant can now render quantitative data inline as a special artifact (`<Artifact type="chart">`). Charts are auto-themed to the active color scheme, support `bar`/`line`/`pie`/`doughnut`/`radar`/`polarArea`, and stream in with a shimmer placeholder. Each chart has **Expand** (full-screen interactive view) and **Export PNG** actions.
- **Live AI Diagrams (Mermaid)**: The assistant can render diagrams inline (`<Artifact type="mermaid">`) — flowcharts, sequence, ER, class, gantt, mindmap — themed to the app palette, with the same Expand and Export PNG actions. Rendered under `securityLevel: 'strict'` to sanitize model-authored labels.
- **Offline Vendoring**: Chart.js 4.4.6 and Mermaid 11.4.1 are added to the CDN asset manifest and fetched on first boot, keeping the app fully offline-capable.

### Changed

- **Chat Router Modularized**: The ~2,000-line `app/routers/chat.py` was split into a focused `app/routers/chat/` package (conversation, uploads, workflows, files, templates, sockets, schedules) with no behavioral change — route order and precedence preserved.

### Fixed

- **Docker In-Container Updates**: The published image baked an expired GitHub Actions `http.<host>.extraheader` credential into `.git/config` (because `.git` ships in the image), causing every in-app update to fail with `could not read Username for 'https://github.com': terminal prompts disabled`. The updater now strips any persisted `extraheader` before pulling, and CI checkout no longer persists credentials, so future images ship a clean `.git`.
- **Chart PNG Export Transparency**: Exports now composite onto a guaranteed-opaque surface color (forcing alpha to 1), fixing see-through PNGs caused by the glassmorphism (translucent) theme surfaces.
- **Diagram Theming on Modern Color Themes**: Mermaid theme colors are now resolved to concrete `rgb()`/`rgba()` via canvas before use, fixing `Unsupported color format` crashes when the active theme defines variables with `color-mix()`/`oklch()`.
- **Diagram Robustness**: Markdown code fences accidentally emitted around Mermaid source are stripped before rendering, and render failures now show the actual error plus the diagram source instead of a generic message.

## [v5.1.2] - 2026-06-10

### Fixed

- **Sidebar Masonry Card Grid Ordering**: Restructured the cards into left and right columns using index filtering, ensuring that the newest conversations always display at the top of the columns rather than flowing top-to-bottom first.
- **Staggered Masonry Aesthetics**: Added an intentional top padding offset (`pt-4`) to the right column container to establish a consistent, premium staggered card alignment.
- **Timezone Discrepancies**: Patched the JavaScript time parsing logic to treat naive server ISO strings as UTC, displaying accurate local times in the browser.

## [v5.1.1] - 2026-06-10

### Fixed

- **Software Update Git Authentication Helper Crash**: Bypassed host-specific git credential helpers (specifically `gh auth git-credential` which is absent in Docker) and disabled interactive prompts (`GIT_TERMINAL_PROMPT=0`) to allow successful anonymous pulling of public repositories.

## [v5.1.0] - 2026-06-10

### Added

- **Pinterest-style Masonry Grid Card Layout for Sidebar**: Redesigned the main chat sidebar conversation list to follow a premium card-based masonry list layout.
- **Clean Message Preview Prioritizing AI Response**: Changed the sidebar conversation preview to display the AI's first response instead of the user's question, falling back to the first user message or last message if needed.
- **Streamlined UI**: Removed the character selector and picker from the sidebar bottom bar to clean up the navigation layout.

### Changed

- **Active Card Highlight**: Updated the active conversation card background highlight to use the subtle accent color (`var(--accent-subtle)`) for better theme blending.

## [v5.0.9] - 2026-06-10

### Fixed

- **Software Update Git Authentication Prompt Error**:
  - Overrode the `git pull` and `git stash` subprocess execution to use `safe.directory=*` and disable credential helpers/expired GitHub Actions headers (`-c credential.helper= -c http.extraHeader=`).
  - Forces Git to pull anonymously and directly from the public HTTPS GitHub URL, preventing credential prompts and crashes inside production Docker containers.

## [v5.0.8] - 2026-06-10

### Fixed

- **Local CDN Self-Hosting for Pyodide**:
  - Configured `loadPyodide` to initialize with `indexURL` set to `/static/vendor/` to allow completely offline browser-side Python execution.
  - Bundled matching `pyodide.asm.js`, `pyodide.asm.wasm`, `pyodide-lock.json`, and `python_stdlib.zip` into the local CDN caching dictionary to avoid loading dynamic modules from external CDNs.
  - Localized all compatible wheel files for `numpy`, `matplotlib`, `pandas`, and their dependencies inside the offline bundle mapping.

## [v5.0.7] - 2026-06-10

### Fixed

- **Software Update Generator ASGI Exception**:
  - Fixed `TypeError: 'coroutine' object is not iterable` in Starlette's `StreamingResponse` by turning `update_generator` into a proper asynchronous generator. Yields status responses directly from the top level of the generator function.

## [v5.0.6] - 2026-06-10

### Fixed

- **Memory Search Access Boost validation error**:
  - Resolved `PointStruct` validation error on production when accessing `_boost_accessed`. Avoids initializing `PointStruct` when updating payloads via `qdrant.set_payload`, eliminating non-fatal Pydantic validation failures.

## [v5.0.5] - 2026-06-10

### Fixed

- **Automatic SQLite Schema Migration**:
  - Automatically migrates existing production databases to add the `selected_tools` column on startup to avoid `OperationalError: no such column: characters.selected_tools` failures.
- **Production Container Self-Updating**:
  - Installed `git` package in the final stage of the `Dockerfile` to enable self-updating capabilities within production Docker deployments.

### Added

- **Premium Click-To-Update Flow**:
  - Implemented real-time self-updating (`POST /api/update/run`) using Server-Sent Events (SSE) to pull from GitHub, stash local changes, sync package dependencies, and restart.
  - Designed a premium fullscreen update screen with a smooth-sailing fish logo, glowing progress bar, status notifications, and auto-reload countdowns.
  - Added an Easter Egg trigger (clicking the settings gear icon in Software Update card 10 times) to run a simulated update flow for developer testing.

## [v5.0.2] - 2026-06-10

### Fixed

- **Tool Calling Chain and Stream Reliability**:
  - Normalized `AIMessage` history serialization to fix tool calling chain breaking on subsequent rounds for models like `openai/gpt-oss-120b` via Nvidia/Groq APIs.
  - Resolved `UnboundLocalError: cannot access local variable 'now_iso'` in the streaming chat router when a model outputted only tool calls in its first chunk.

### Added

- **Configurable Tool Execution Limits**:
  - Introduced a user-configurable `max_tool_rounds` preference (range 1-100) on the settings page to control consecutive tool execution limits.
- **Select Tools Per Character**:
  - Restored the select-tools-per-character feature allowing users to restrict characters to a subset of available MCP tools.

## [v5.0.1] - 2026-06-09

### Fixed

- **Hardened App Store Installation**:
  - Added traceback logging for installation handler exceptions.
  - Implemented 120-second execution timeouts on dependency installs.
  - Gracefully handles empty files and 404 responses for missing `requirements.txt` assets.

### Changed

- **CI Workflow and Build Optimization**:
  - Added `workflow_dispatch` to allow manual workflow triggering via CLI or UI.
  - Switched Docker image builds from GHA cache to GHCR registry caching (`type=registry`) for faster build layer uploads/downloads.
  - Enabled Buildx cache mounts for `uv` package sync and `apt` package manager in the `Dockerfile`.
  - Disabled SLSA provenance generation to eliminate attestation overhead.

## [v5.0.0] - 2026-06-09

### Added

- **Integrated App Store & Persona Store**:
  - Implemented backend and frontend interfaces to install, toggle, and uninstall apps and characters directly from the online GitHub store.
  - Added a premium, inline click-to-confirm uninstallation workflow (bin icon transitions to a confirmation button).
  - Supported automatic character profile/avatar download from repositories, falling back gracefully to UI icons.
- **MCP App Bridge**:
  - Added an stdio-based MCP bridge (`app/mcp_app_bridge.py`) that lists, configures, and runs local applications as tools dynamically.
  - Extracts and exposes structured parameter typing (`inputSchema`) from `manifest.json` files for tool-calling models.
- **Reasoning Handling Refinements**:
  - Fortified `parse_thinking` to extract multiple, nested, or unclosed `<think>`/`<thought>` tag contents from reasoning models.
- **Lifecycle Integration**:
  - Integrated pool refresh triggers upon deleting or creating MCP servers to dynamically synchronize available tools.

## [v4.5.0] - 2026-06-02

### Added

- **About page refinements**:
  - Added smooth swimming animation to the fish icon.
  - Removed "Model Edition" section from the General Properties.
- **Diagnostics Easter Egg Remake**:
  - Remade the secret diagnostics easter egg from an inline panel into a premium, backdrop-blurred dialog modal.
  - Added a monospace terminal logs box with auto-scrolling log sequence.
- **Software Update Checker**:
  - Implemented an iOS/macOS-style Software Update card in the About tab with gear icon themed styling.
  - Fetches update info asynchronously from git repository `pyproject.toml` with cache-busting timestamp parameters.
  - Displays a clean release notes popup modal rendering parsed Markdown changelog blocks.

## [v4.1.0] - 2026-05-30

### Added

- **Settings Page v4.1.0**:
  - **Remake**: remake setting page UI to follow new aesthetic similar to macOS setting page UI
  - **Responsiveness** : the setting page looks different based on the device its viewed on eg IOS for smaller screen, and MacOS setting page for larger screen
  - **Fixes**:
    - added profile picture cropper, reused from the the character picture cropper

- **Theme Engine v4.0.6**:
  - **Dynamic Color Presets**: Added dynamic color-mix swatch presets for easy theme switching.
  - **AI Theme Generator**: Added an AI-powered theme generator to create new color schemes.
  - **Sidebar Contrast Audit**: Added a contrast audit to ensure accessibility and readability of sidebar elements.
  - **Sidebar Dark Mode**: Fixed sidebar dark mode to use `mix()` for proper background contrast.

## [v4.0.1] - 2026-05-27

### Fixed

- **ReAct Loop NoneType Defensiveness**: Hardened `execute_worker_task` ReAct agent execution against nullable database columns, preventing `TypeError: 'NoneType' object cannot be interpreted as an integer` when template limits are missing or null.
- **Workflow Time Monospace Clarity**: Applied CSS `text-transform: none;` on the active workflow duration time wrapper, keeping seconds (`s`) and minutes (`m`) lowercase to prevent monospace typeface confusion with the digit `5` (previously rendered as capitalized `S`/`M`).
- **Tool Sandbox Hardening**: Fortified `shell_exec` time limits to handle non-integer or `None` values gracefully.

## [v4.0.0] - 2026-05-27

### Added

- **Asynchronous SQLite Database Backend**:
  - Overhauled the storage layer to replace blocking flat JSON files (`conversations.json`, `characters.json`, `mcp_servers.json`, `folders.json`, `spaces.json`, `agent_templates.json`, `multi_agent_workflows.json`, `insights.jsonl`) with a fast, high-performance SQLite database (`data/database/kokomi.db`).
  - Configured the SQLite engine to run in **WAL (Write-Ahead Logging)** mode with synchronized NORMAL pragmas to allow parallel reads and concurrent writes.
  - Implemented core indexed tables (`conversations`, `messages`, `workflows`, `agent_templates`, `insights`, `characters`, `mcp_servers`, `spaces`, `folders`) with explicit constraints and relational foreign keys.
  - Integrated a threadsafe context shim (`_run_async`) to allow legacy synchronous routers to query the database asynchronously without causing thread blocking or 504 Gateway Timeouts under Nginx/OpenResty.
- **Automated Lifecycle Migration & Ingestion**:
  - Built an automated discovery and migration loop (`app/migration.py`) into the FastAPI lifespan startup.
  - Automatically auto-detects historical JSON files in the `/data` root on first boot, migrates all records into SQLite, creates the restructured `data/json/` directory, and cleanly archives legacy files under `data/json/backups/` completely hands-off.
- **Non-Blocking Background Workflow Saving**:
  - Integrated collaborative multi-agent workflow runs into SQLite, scheduling updates as non-blocking background tasks (`loop.create_task`) directly on the event loop during live agent runs.
- **Telescopic Telemetry Insights**:
  - Overhauled high-frequency metric collection (`/insights`) from disk-blocking JSONL appends to atomic database row insertions.
- **Restructured CLI & Directories**:
  - Relocated developer bootstrapping and migration utilities into the `scripts/` directory and corrected relative imports.
  - Relocated user preferences and scheduler configurations to `data/json/` to keep the data directory perfectly organized.

## [v3.6.4] - 2026-05-27

### Added

- **Local CDN Asset Bundling & Server Self-Hosting**:
  - Created a robust self-hosting resource manager (`app/cdn.py`) to automatically cache all remote external CDN libraries (Tailwind CSS JIT, jQuery, Alpine.js, KaTeX, FontAwesome, driver.js, and Outfit fonts) locally inside the FastAPI static folders.
  - Automatically fetches all KaTeX `.woff2` font variant outlines and FontAwesome brand/regular/solid webfonts on first boot to prevent mathematical formula breakage or missing native system icons.
  - Integrated the downloader task cleanly into the FastAPI lifespan startup in `app/__init__.py`.
  - Rewrote and redirected asset mappings inside `templates/base.html` to point to `/static/vendor/` endpoints instead of third-party domains, providing connection-free loading times (0ms browser cache hits) and true offline application execution.
  - Safely added `/public/static/vendor/` local static caches to `.gitignore`.

## [v3.6.3] - 2026-05-26

### Added

- **KaTeX Math Rendering**:
  - Integrated KaTeX library for fast, high-quality LaTeX formula rendering across all chats.
  - Render block-level math displays cleanly without constraining box containers or vertical scrolls.
  - Implemented an elegant, floating **Copy as Image** action on math block hover that captures KaTeX DOM elements and writes high-resolution PNG images directly to the clipboard via HTML-to-Canvas serialization.
  - Custom styled KaTeX `\boxed{}` command formulas to match the Midnight/Lavender theme using border colors and transparent backgrounds.

### Fixed

- **Instant Chat Scroll-to-Bottom**:
  - Overhauled conversation scroll behavior when shifting chat threads.
  - Utilized a highly reliable double-nextTick + requestAnimationFrame timing pattern on the scroll-box element, ensuring full layout computation before targeting `scrollTop = scrollHeight`.

## [v3.6.0] - 2026-05-26

### Added

- **Onboarding Tour**:
  - Implemented a premium, multi-step interactive guided onboarding tour of the multi-agent workflow terminal using `driver.js`.
  - Injected a highly realistic, complete mock execution pipeline (`Research & PDF: Microcomputer 8086`) containing distinct specialized worker nodes (Researcher, Coder, Writer, Mailer), precise start/end timing diagnostics, error details, and an interactive Supervisor chat log history.
  - Implemented dynamic, state-aware tab swappers inside the tour script that automatically flip the active UI view (List, Graph, Files, Chat, and Schedule button highlights) as you click through steps.
  - Developed a seamless completion loop that automatically redirects the user from the main conversational chat dashboard tour directly into the Atlas workflow page.
- **Fluid Conversational Transitions**:
  - Overhauled conversational switches in the main dashboard, introducing a new state toggle (`messagesLoaded`) to trigger a smooth fade-in and slide-up animation whenever changing between different previous chat threads.
- **SES Sandbox & Layout Polish**:
  - Bound the `atlasApp` Alpine component globally to the `window` context to bypass strict browser SES (Secure EcmaScript) sandbox security restrictions.
  - Fixed syntax errors inside the Atlas template (invalid `mx-auto` inside inline styles) and removed the manual guide button to keep the header minimal.

## [v3.5.5] - 2026-05-23

### Added

- **Living Long-Term Memory Engine (v2)**:
  - Overhauled unstructured Qdrant memory storage into a self-cleaning, importance-weighted memory system.
  - Implemented **Dedup-on-Write** using cosine similarity (`DEDUP_THRESHOLD = 0.85`) to dynamically merge redundant context into single high-fidelity memory atoms.
  - Introduced **Importance-Weighted Decay** (1.0 to 5.0 scale) and periodic background decay sweeps to automatically weaken and prune stale/trivial atoms.
  - Integrated **AI-Synthesized Character Profiles** which consolidate highly relevant long-term memory points into structured relationships per-character, automatically injected into LLM system prompts.
  - Added an in-memory LRU cache to reduce redundant vector search overhead during real-time multi-agent chats.
- **Mac-Style Memory Explorer Overhaul**:
  - Overhauled `/memories` UI displaying premium star ratings (★) for importance, access count tracking (👁️), and custom source badges (MANUAL, TOOL, AUTO).
  - Added a direct **AI-Synthesized Relationship Profile Card** to the sidebar navigation pane.
  - Added a manual **Decay Sweep** button to trigger instant pruning of fading context points.
  - Added hover-only visibility actions to keep card grids exceptionally clean and aesthetically native.
- **Docker Socket Host Configuration**:
  - Fully documented host Docker socket mounting requirements in `README.md` to safely spin up isolated task sandboxes.

### Fixed

- **Bulk Memory Deletion**: Fixed an issue where clearing character memory threw `500 Internal Server Error` due to incorrect argument mapping in the Qdrant delete method. Corrected `filter` to `points_selector`.

## [v3.2.5] - 2026-05-23

### Added

- **Unified Insights Telemetry**: Fully integrated the asynchronous workflow execution engine with the shared, non-blocking telemetry database, allowing all workflow agent actions to record tokens, latency, and duration metrics.
- **Apple-Style Insights Source Filter**: Introduced an interactive segmented control filter (**All Sources** / **Chat Only** / **Workflow Only**) on the global `/insights` page. Enables real-time, isolated ApexCharts visualization and model-by-model metrics breakdown of interactive vs. background task performance.

## [v3.2.4] - 2026-05-23

### Added

- **Execution Engine Agnostic Paths**: Fully standardized directory resolution across file/artifact reading and writing tools (`file_read`, `file_write`, `artifact_read`). Paths containing `/workspace` are dynamically translated to the real host workflow storage directory when in Docker sandbox mode, or mapped appropriately when running directly on the host, preventing broken operations in both run environments.
- **Dynamic Local vs Sandbox Prompts**: System prompts for the code execution worker now dynamically adjust based on the user's active Settings configuration. When set to `local`, the prompt guides the agent to execute safely on the host's filesystem; when set to `docker`, it informs them they are inside the isolated container sandbox.
- **Robust Execution Timeout Guardrails**: Every command run within the Docker sandbox now gets automatically wrapped in a `timeout` utility wrapper, preventing runaway execution hangs (e.g. blocking SSH sessions) from freezing the central application thread.

## [v3.1.4] - 2026-05-21

### Added

- **AI Character Generator**: Introduced a dedicated `✨ Generate with AI` modal in Settings → Characters. Users describe a character concept in plain language; the AI auto-fills name, short description, system persona, and assigns relevant MCP tools. Powered by a new stateless `POST /api/ai/generate` backend endpoint that never pollutes chat history.
- **Searchable Character Pickers**: All three character picker surfaces (welcome screen popover, sidebar list, and room picker) now have instant fuzzy search with a character count chip, an empty state, and a `max-h` scroll container — scaling gracefully to 40+ characters.
- **Character Description Field**: Added a `description` field (short tagline) to every character. Shown in accent color on the character card, in character pickers, and editable in the New / Edit Character modal.
- **Widescreen Settings Dashboard**: Expanded the Settings layout max-width from `860px` → `1200px` and sidebar from `200px` → `240px` for a more comfortable, premium feel on large screens.
- **Company-Branded Provider Icons**: Replaced generic icons (`fa-cloud`, `fa-wand-sparkles`, `fa-microchip`) in both General and Atlas Intelligence Provider segment controls with proper brand icons (`fa-brands fa-google` for Gemini, `fa-solid fa-bolt` for Groq, `fa-solid fa-network-wired` for NVIDIA NIM).
- **Model Brand Icons in Atlas Footer**: The Atlas terminal now shows the active model's company icon (`fa-brands fa-openai`, `fa-brands fa-google`, `fa-solid fa-bolt`, etc.) alongside just the clean model name (strips `org/` prefix) in the bottom status bar.
- **Stateless AI Utility Endpoint**: `POST /api/ai/generate` — a lightweight, zero-history, single-shot LLM call endpoint usable for any internal AI-assisted feature without touching conversation storage.

### Fixed

- **AI Character Generator isolation**: Separated generation from `/api/chat` so AI-generated character JSON never appears in the user's conversation history.

## [v3.1.1] - 2026-05-21

### Added

- **Premium Apple-Style Collapsible Sidebar**: Implemented a highly responsive, animated collapsible sidebar (`transition: all 0.3s`) for the Atlas terminal. Added a slick collapse trigger (`fa-chevron-left`) on the sidebar brand header and a persistent toggle hamburger (`fa-bars`) in the macOS-inspired topbar.
- **Unified Supervisor Collaboration Interface**: Merged the raw task execution logs directly inside the Supervisor's message box container under a styled sub-container, streamlining conversation threads and removing cluttered, floating log bars.
- **Containing-Block Breaking Fullscreen Canvas**: Bind the `fullscreen-ancestor` CSS class dynamically when zooming the workflow graph to full screen, disabling relative containing-block transform animations and restoring pixel-perfect viewport geometry for drawing connector nodes.
- **Proactive Auto-Start Chat Routing**: Added high-performance keyword analysis matching standard workflow start triggers (`start`, `go`, `run`, `kick off`) inside the interactive supervisor agent chat backend, immediately bootstrapping workflow run execution without requiring manual JSON tasks schema generation.
- **Module-Scoped Variable Protection**: Eradicated shadow-scoped, nested local `asyncio` imports from `app/routers/chat.py` endpoints, eliminating potential bytecode compile-time scoping exceptions (`UnboundLocalError`).

## [v3.0.1] - 2026-05-19

### Added

- **Multi-Page Table Header Repeating (`repeatRows=1`)**: Enabled automatic repeating of table headers at the top of subsequent pages when a table is split across page boundaries in compiled PDF reports.
- **Page Boundary Orphan Protection (`keepWithNext`)**: Added custom `keepWithNext=True` styling on all titles and heading styles to prevent orphan headings at the bottom of pages.
- **Provider-Specific Loop-Prevention Guardrails**: Configured the `web_search` tool to return clear, provider-specific instructions to agent workers when search queries return empty or error out, forcing them to instantly fall back to pre-trained internal knowledge instead of entering infinite ReAct loops.
- **Strict Search Provider Isolation**: Refactored the `web_search` tool to strictly use the configured `search_provider` (SearxNG or Tavily) and removed all general mocks and automatic search engines fallbacks.

## [v2.0.1] - 2026-05-18

### Added

- **Multi-Agent Document & Slide Deck Exporter Suite**: Integrated support for Microsoft Word (`.docx`), Microsoft PowerPoint (`.pptx`), and Microsoft Excel (`.xlsx`) generation into the core compilation workers.
- **Apple HIG Inline Bold-Text Parser**: Developed a state-of-the-art markdown inline text parser for PowerPoint slides and Word paragraphs. Converts raw `**bold**` markup decorators dynamically into native bold runs (`run.font.bold = True`), creating pristine, presentation-grade slide decks.
- **Dynamic Allowed Tools Endpoint**: Added `/api/workflow/tools` FastAPI router endpoint to dynamically populate available agent tools, decoupling Alpine.js frontend from hardcoded tool lists and enabling live discovery of connected external MCP services.
- **Thread-Safe Context Redirection**: Redirected all document compilation tools (`pdf_export`, `docx_export`, `pptx_export`, `excel_export`) to dynamically save output assets directly inside the active workflow storage folder (`active_storage_dir.get()`) rather than general upload folders.
- **Self-Healing LLM Task Recovery**: Refactored the `pdf_worker` system prompt to act as a _Professional Document Layout Designer_, instructing it to utilize dependent task prompt context to recover draft markdown sections when files are not physically present on disk.

## [v1.0.1] - 2026-05-17

### Added

- **Neural Memory Explorer Dashboard**: A dedicated, full-screen page (`/memories`) featuring macOS-style sidebar layout and continuous curves squircle glass cards.
- **Real-Time Memory Search & Filtering**: Fast text filter input to query key vector facts instantly.
- **Manual Memory Atom Insertion**: Deep-midnight modal allowing users to feed custom context points directly into Qdrant collections.
- **Granular Incognito & Erase Controls**: Multi-functional admin actions supporting selective purging of single vector cards, complete collection wipes, and toggle memory switches.
- **Vector Latency Optimizations**: Integrated singleton caching for Google Generative AI embeddings and concurrent database queries with `asyncio.gather`, cutting cold-start memory search times from 21 seconds down to sub-second speeds.
- **Sidebar Navigation Pins**: Persistent "Memory Explorer" and "Settings" links anchored to the sidebar's bottom section for instant navigation across all pages.
- **Message-Based Memory Recall**: Agents can now proactively search and inject relevant memories into conversations without explicit user prompts.

### Changed

- **Alpine.js Grid Separation**: Separated Alpine `x-collapse` attributes from layout wrappers inside character preference menus, restoring responsive grid systems.

## [v0.9.1] - 2026-05-15

### Fixed

- **Startup Crash**: Implemented automatic directory initialization for `data/uploads` and `data/avatars` to prevent `RuntimeError` during application boot.

## [v0.9.0] - 2026-05-15

### Added

- **Multimodal Attachment Engine**: Support for uploading and managing multiple file attachments in the chat interface.
- **Vision Integration**: Native base64 image encoding for vision-capable models (Gemini, Llama 3.2), enabling AI analysis of JPG, PNG, and WEBP files.
- **PDF Extraction**: Integrated `pypdf` for automated text extraction from PDF documents, allowing the AI to reason about document contents.
- **Rich Media Previews**: Implemented visual image thumbnails in both the attachment preview bar and chat bubbles.
- **Clipboard Support**: Added `Ctrl+V` (paste) functionality for instantly attaching screenshots and files.
- **UI Refinement**: Relocated the attachment action button to the primary input bar with a premium, state-aware design.

### Fixed

- **Backend Routing**: Resolved a duplicate router definition in `chat.py` and consolidated all API endpoints under a single `/api` prefix.

## [v0.8.0] - 2026-05-15

### Added

- **Interactive Python Sandbox**: Integrated Pyodide (WebAssembly) to enable safe, browser-side execution of Python artifacts.
- **Data Science Toolkit**: Added automatic package loading for `matplotlib`, `numpy`, and `pandas`, allowing for rich data visualization.
- **Live Rendering Engine**: Implemented real-time, token-by-token rendering for HTML and SVG artifacts using a debounced blob-based preview system.
- **IDE-Style Workspace**: Introduced a premium tabbed interface in the artifact modal with "Code," "Console," and "Preview" views.
- **Matplotlib Visualization**: Custom monkeypatching for `plt.show()` to intercept figures and render them as high-resolution images in the terminal.

### Fixed

- **Interaction Stability**: Implemented a global capture-phase event listener and unique message IDs to ensure artifact cards are 100% responsive even during high-speed streaming.
- **Modal Ghosting**: Resolved an issue where closing and opening different artifacts would briefly show previous content by implementing a hard-reset state protocol.
- **Refined Navigation**: Standardized the default artifact view to the "Code" tab based on user developer workflow preferences.
- **Streaming Sync**: Ensured the "Preview" tab updates live even if the modal is opened mid-stream during generation.

## [v0.7.0] - 2026-05-15

### Changed

- **Artifact System Overhaul**: Transitioned to a robust `[[ARTIFACT:id]]` anchoring system for reliable inline rendering during both live streaming and historical reloads.
- **Premium Inline Artifacts**: Inline artifact cards now feature the full premium design, including frosted-glass headers and automatic 120px code previews.
- **Improved Interaction**: Refactored the artifact opening logic using Alpine.js state lookup, ensuring consistent behavior after page refreshes.

### Added

- **Cinematic State Transitions**: Implemented smooth Alpine.js cross-fade transitions between the Welcome Screen and the Message List, creating a fluid, high-end feel when loading chats.
- **Artifact Preview Rendering**: Integrated an `escapeHtml` utility in `chat.js` to safely render code previews within inline artifact cards.

### Fixed

- **Message Alignment**: Restored the centered Apple aesthetic for user and assistant messages that was briefly broken by container refactoring.
- **Artifact Modal Spacing**: Fixed a persistent bottom-margin/gap issue in the artifact modal by allowing the code preview to fill the viewport naturally.
- **Sidebar Chronology**: Standardized the conversation sorting to ensure the newest chats always appear at the top.
- **Persistence**: Resolved the "Transport request timed out" error by ensuring URL hash persistence (`#chat=id`) correctly restores the active session.

## [v0.6.1] - 2026-05-15

### Added

- **Cinematic Splash Screen**: Replaced the static MCP connection list with a premium, drifting background wall of tool server posters.
- **Glowing Branding**: Redesigned the center Kokomi logo with a high-intensity neon glow and pulse effect for a more cinematic initialization experience.
- **Dynamic Backgrounds**: The splash wall now intelligently populates with randomized icons for all configured MCP servers, scaling beautifully for large pools.

## [v0.6.0] - 2026-05-14

### Added

- **Artifacts System**: Introduced a robust XML-based artifact generation system for code, markdown, and config files.
- **Interactive Artifact UI**: Added premium, inline artifact boxes with "generating" shimmer effects and a full-screen code viewer modal.
- **Artifact Versioning**: Implemented automatic version tracking for artifacts across conversation sessions.
- **UI Control Toggles**: Added dedicated "Artifacts" and "Debug" toggle pills in the chat input action bar for granular feature control.
- **Performance Polishing**: Refined the real-time telemetry bar (TPS, TTFT, Context) to match the macOS Tahoe design aesthetic.

### Fixed

- **Streaming Stability**: Fixed internal buffer flushing in the chat stream to prevent data loss or truncated XML tags during response transitions.
- **Preference Persistence**: Standardized preference synchronization between the frontend and backend.

## [v0.5.0] - 2026-05-14

### Added

- **Usage Insights Telemetry**: Implemented a comprehensive performance tracking system for LLM generations.
- **Real-Time Status Bar**: Added a pulsing live stats bar in the Chat UI showing TPS (Tokens Per Second), TTFT (Time To First Token), and Context Usage during streaming.
- **Message Metadata Footers**: Assistant messages now display generation benchmarks with interactive, high-contrast tooltips.
- **Telemetry Dashboard**: Added a dedicated "Usage Insights" page with rolling performance charts and model-level aggregations.
- **Insights Privacy Toggle**: Introduced a master `insights` toggle in settings to enable/disable telemetry collection and surfacing.

### Fixed

- **Conversation Sorting**: Resolved a `TypeError` in the conversation list caused by mixed string/float types in the `updated_at` field.
- **Frontend Stability**: Fixed a critical JavaScript syntax error in the chat streaming loop that caused rendering failures.
- **UI Visibility**: Redesigned tooltips with 100% opacity and high-contrast colors to ensure legibility across all themes and custom wallpapers.

## [v0.4.9] - 2026-05-13

## [v0.4.8] - 2026-05-12

### Added

- **NVIDIA NIM Integration**: Fully implemented NVIDIA NIM as a first-class AI provider, including support for specialized reasoning models like Nemotron-4.
- **Dynamic Model Discovery**: Refactored the settings UI to dynamically fetch and display all available NVIDIA NIM models (30+ models) from the API.
- **Real-time Debugging UI**: Introduced a "Server Debug Log" panel in the chat interface that streams internal execution steps (prompts, tool calls, processing chunks) when Debug Mode is enabled.
- **WhatsApp-style Status**: Added a "Thinking..." status next to the bot's name in the message header for better feedback during generations.

### Fixed

- **NIM Stability**: Switched to the official `ChatNVIDIA` client to resolve streaming parsing failures and removed deprecated models that caused `404 Not Found` errors.
- **Thinking UI**: Simplified the thinking block from a heavy collapsible box into a sleek, inline quote-style block that flows naturally within the chat bubble.

## [v0.4.7] - 2026-05-03

### Fixed

- **RAG Dumping**: Reduced RAG chunk sizes, added a score threshold, and updated the system prompt to force the AI to synthesize concise answers instead of dumping raw document text.
- **Thinking Box Overflow**: Added `word-break: break-word` and `overflow-wrap: anywhere` to the thinking box CSS to prevent long unbroken strings from visually escaping the container.

## [v0.4.6] - 2026-05-03

### Fixed

- **Spaces Page Redirects**: Fixed trailing slash issues in `/api/spaces` that caused insecure `http` redirects and Mixed Content errors on production deployments.
- **API Consistency**: Standardized API routes to handle both slashed and non-slashed requests without redirection loops.

## [v0.4.5] - 2026-05-03

### Fixed

- **Call Page Initialization**: Added missing `x-init` to ensure contacts and recent logs are populated on load.
- **Mixed Content Error**: Replaced `url_for` with relative paths for the favicon to prevent insecure `http` requests on `https` deployments.
- **Qdrant Connection**: Corrected RAG initialization to respect `QDRANT_URL` from environment variables.

### Changed

- **Environment Stuffs**: Externalized `QDRANT_URL`, `WHATSAPP_API_URL`, and `TAVILY_API_KEY` to be loaded from `.env` via `config.py`.

## [v0.4.4] - 2026-05-03

### Added

- **MCP Session Pool**: Complete architectural rewrite — MCP server sessions are now persistent and globally cached. Sessions are initialized once and automatically refreshed every 5 hours, eliminating all per-request connection overhead.
- **Splash Screen**: Beautiful macOS-style splash overlay with live server status indicators. Displays each MCP server's connection state (connecting → connected/error) with staggered animations. Automatically dismissed after initialization and uses a 5-hour localStorage TTL to skip on subsequent visits.
- **Pool Management API**: New `/api/mcp-servers/pool/status` and `/api/mcp-servers/pool/init` endpoints for frontend pool control.
- **App Lifespan Management**: Added proper FastAPI lifespan events for clean MCP session teardown on shutdown.

### Fixed

- **"Attempted to exit cancel scope in a different task"**: Completely resolved by replacing the per-request `AsyncExitStack` + `asyncio.gather` pattern with a single-task global pool. The test endpoint now uses an isolated `test_single_server()` function.
- **Per-request MCP reconnection**: Chat handlers no longer open/close MCP connections on every message. Tools are retrieved instantly from the cached pool via `get_pool_tools()`.

### Changed

- Removed all `AsyncExitStack` usage from chat handlers — no more `ExceptionGroup` teardown crashes.
- MCP test endpoint now runs in a fully isolated context, preventing cancel scope leaks.

## [v0.4.2] - 2026-05-02

### Added

- Implemented lazy parallel initialization of Model Context Protocol (MCP) servers to drastically improve chat startup latency.
- Added comprehensive `ExceptionGroup` error handling and suppression for internal `anyio` tasks during `streamable_http_client` teardown, preventing streaming connection crashes.

### Fixed

- Fixed an issue where the application would crash with `ExceptionGroup` when tearing down `AsyncExitStack` in the chat router.
- Removed sequential blocking loop in `connect_mcp_servers`, fixing the massive 21-second latency bottleneck on initial tool call requests.

## [v0.4.0] - 2026-05-02

### Added

- Introduced fully functional **User Profiles** with custom display names.
- Added a gorgeous **Profile Picture Cropping** UI powered by `Cropper.js` using macOS-style modal overlays.
- Sidebar UI and message bubbles dynamically reflect customized user identity instead of defaults.
- Implemented **Voice-to-Text** input button inside the chat interface using the Web Speech API.

### Fixed

- Re-engineered the chat input layout to a flexible flex-column stack, resolving overflow and UI collision issues on mobile viewports.
- Squashed a bug preventing standard `Shift + Enter` newlines inside the chat textbox.
- Addressed stale LLM model displays by injecting the active server-resolved model directly into the server-sent events (SSE) stream chunks.

---

_Releases prior to v0.4.0 are omitted._
