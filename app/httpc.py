"""Shared, connection-pooled httpx.AsyncClient for outbound HTTP calls on
hot/repeating paths.

Opening `httpx.AsyncClient()` per call (the pattern most of the codebase
uses) throws away TLS/connection reuse every time — fine for a one-shot
user-triggered action, real overhead on a path that fires repeatedly, like
the image proxy (once per gallery image, possibly several per message).
This client is built lazily on first use and closed in the app lifespan's
shutdown; call sites should NOT use it as a context manager (no `async
with`) since that would close the shared pool after a single request.
"""
import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient()
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
