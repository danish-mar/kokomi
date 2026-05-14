# Changelog

All notable changes to this project will be documented in this file.
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
