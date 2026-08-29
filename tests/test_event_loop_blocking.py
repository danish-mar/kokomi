"""Regression test for the v6.0.3 event-loop-blocking fixes.

Kokomi runs as a single process on one asyncio event loop (main.py calls
uvicorn.run with no --workers). That means ANY unwrapped synchronous
blocking call inside an async route handler stalls every other concurrent
request, WebSocket, and background task for its full duration — not just
the one that made the call.

Rather than depending on real network access, Qdrant, or LLM API keys (none
guaranteed available in CI or a fresh checkout), this monkeypatches the
actual blocking primitive at the fixed call site with a controlled
synchronous sleep, then measures whether an unrelated concurrent request
is delayed by it. That isolates exactly the property under test: does the
call run ON the event loop, or off it via asyncio.to_thread?

The PDF-artifact-render endpoint is used as the representative case (it's
the one fixed here that fires from ordinary chat use, not an admin action).
The same technique applies to the other calls fixed alongside it
(app_store.py, prefs.py's updater, memory.py's save path).

To compare against the commit immediately before this fix:
    git worktree add /tmp/kokomi-before a0e44ba
    cp tests/test_event_loop_blocking.py /tmp/kokomi-before/tests/
    cd /tmp/kokomi-before && \
        /path/to/kokomi/.venv/bin/python3.12 -m unittest tests.test_event_loop_blocking -v
(reuses this checkout's already-synced venv — a0e44ba has the same
dependencies, just without the asyncio.to_thread wrapper this test checks
for — so no separate `uv sync` is needed for the comparison run.)
On a0e44ba this test FAILS: the concurrent /health request takes ~1s
instead of a few ms, because render_markdown_to_pdf() ran directly on the
event loop. On this commit (and after) it PASSES.
"""
import asyncio
import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("GROQ_API_KEY", "mock-key")
os.environ.setdefault("GOOGLE_API_KEY", "mock-key")

from httpx import ASGITransport, AsyncClient

from app import app
from app.routers.chat.artifacts import render_pdf_artifact

# Artificial delay standing in for a slow ReportLab render / image fetch.
SLOW_CALL_SECONDS = 1.0
# A concurrent unrelated request should return in milliseconds if the slow
# call is properly threaded; this leaves a wide margin before "blocked".
FAST_REQUEST_THRESHOLD = SLOW_CALL_SECONDS * 0.5


class TestEventLoopBlocking(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_render_does_not_block_other_requests(self):
        """POST /api/artifacts/render-pdf must not stall a concurrent,
        unrelated request. Guards against regressing the asyncio.to_thread
        wrap added in app/routers/chat/artifacts.py (v6.0.3)."""

        def slow_sync_render(content, buf):
            # Stands in for render_markdown_to_pdf's real blocking work
            # (ReportLab layout + a requests.get per embedded image).
            time.sleep(SLOW_CALL_SECONDS)
            buf.write(b"%PDF-1.4 fake pdf bytes")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # render_markdown_to_pdf is imported locally inside the handler
            # (`from app.pdf_render import render_markdown_to_pdf`), so it
            # must be patched at its source module, not as an attribute of
            # app.routers.chat.artifacts.
            with patch(
                "app.pdf_render.render_markdown_to_pdf",
                side_effect=slow_sync_render,
            ):
                # The clock starts here, BEFORE the slow task is even
                # created — not after some "head start" sleep. If the slow
                # call truly blocks the loop, nothing else (not even a
                # sleep() timer in this very coroutine) can run again until
                # it returns, so any measurement taken after an intervening
                # await would already be too late to see the stall. Starting
                # here means fast_arrival below measures real wall-clock
                # time from before either request began.
                start = time.monotonic()

                # render_pdf_artifact is invoked directly as a plain coroutine
                # (it takes a plain dict, no FastAPI dependency injection to
                # resolve) so this test isolates the threading behavior of
                # the handler itself, independent of the auth middleware.
                slow_task = asyncio.create_task(
                    render_pdf_artifact({"content": "# Hello"})
                )
                # Yield once so slow_task actually starts (asyncio.create_task
                # only schedules it) before firing the "concurrent" request.
                await asyncio.sleep(0)

                resp = await client.get("/health")
                fast_arrival = time.monotonic() - start

                await slow_task

        print(
            f"\n[event-loop-blocking] concurrent /health request arrived at "
            f"t={fast_arrival:.3f}s while a {SLOW_CALL_SECONDS:.1f}s blocking PDF "
            f"render was in flight (threshold: {FAST_REQUEST_THRESHOLD:.3f}s)"
        )
        self.assertEqual(resp.status_code, 200)
        self.assertLess(
            fast_arrival,
            FAST_REQUEST_THRESHOLD,
            f"/health didn't arrive until t={fast_arrival:.3f}s (>= "
            f"{FAST_REQUEST_THRESHOLD:.3f}s threshold) — it was held up behind "
            f"the blocking PDF render instead of being serviced concurrently. "
            f"This means render_pdf_artifact() is calling "
            f"render_markdown_to_pdf() directly instead of via "
            f"asyncio.to_thread (regression of the v6.0.3 fix)."
        )


if __name__ == "__main__":
    unittest.main()
