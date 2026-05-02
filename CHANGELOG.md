# Changelog

All notable changes to this project will be documented in this file.

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
