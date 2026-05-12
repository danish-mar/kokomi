# Changelog

All notable changes to this project will be documented in this file.

## [v0.4.9] - 2026-05-13

### Added
- **Basic Authentication**: Implemented JWT-based authentication for the entire application.
- **Premium Login UI**: Added a high-aesthetic, Apple-inspired login page with glassmorphism and smooth animations.
- **Global Route Protection**: Introduced a central middleware to enforce authentication across all pages and API endpoints.
- **Auth Management**: Added `/auth/login` and `/auth/logout` endpoints with secure cookie-based token storage.

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
*Releases prior to v0.4.0 are omitted.*
