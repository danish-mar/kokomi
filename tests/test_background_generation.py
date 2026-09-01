"""Regression tests for generations that outlive their request.

Closing the tab used to abort the response: the SSE generator cancelled the
producer task on disconnect, and because the conversation is only written once
generation finishes, the whole exchange was lost — the user's own message
included.

The properties worth pinning down are behavioural rather than structural, so
these drive the registry and the HTTP endpoints instead of asserting on code
shape:

  * a disconnect must not stop the work
  * reconnecting must replay what was missed and then continue live
  * stop must actually stop it, now that aborting the fetch no longer does
"""
import asyncio
import os
import unittest

os.environ.setdefault("GROQ_API_KEY", "mock-key")
os.environ.setdefault("GOOGLE_API_KEY", "mock-key")

from httpx import ASGITransport, AsyncClient

from app import generation_registry as reg


class TestGenerationRegistry(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reg.clear()

    async def test_disconnect_does_not_stop_generation(self):
        gen = reg.start("c1")
        watcher = gen.subscribe()
        await gen.put("a")

        gen.unsubscribe(watcher)          # tab closed
        await gen.put("b")                # must still be produced and kept

        self.assertEqual(gen.events, ["a", "b"])
        self.assertIn("c1", [g["conversation_id"] for g in reg.active()])

    async def test_reconnect_replays_then_streams_live(self):
        gen = reg.start("c1")
        w = gen.subscribe()
        await gen.put("a")
        gen.unsubscribe(w)
        await gen.put("b")                # missed while away

        rejoin = gen.subscribe()
        replayed = [rejoin.get_nowait() for _ in range(rejoin.qsize())]
        self.assertEqual(replayed, ["a", "b"], "reconnect must replay what was missed")

        await gen.put("c")
        self.assertEqual(rejoin.get_nowait(), "c", "must keep streaming after the replay")

    async def test_late_joiner_after_completion_gets_everything(self):
        """A client reconnecting just as the response lands must still receive
        the transcript and the end sentinel, not an empty stream."""
        gen = reg.start("c1")
        await gen.put("a")
        await gen.put(None)

        late = gen.subscribe()
        self.assertEqual([late.get_nowait() for _ in range(late.qsize())], ["a", None])

    async def test_finished_generations_leave_the_active_list(self):
        gen = reg.start("c1")
        self.assertTrue(reg.active())
        await gen.put(None)
        self.assertEqual(reg.active(), [], "a finished response must stop glowing")

    async def test_cancel_actually_cancels_the_task(self):
        gen = reg.start("c1")
        started = asyncio.Event()

        async def work():
            started.set()
            await asyncio.sleep(30)

        gen.task = asyncio.create_task(work())
        await started.wait()

        self.assertTrue(reg.cancel("c1"))
        await asyncio.sleep(0.05)
        self.assertTrue(gen.task.cancelled(), "stop must really halt the work, not just hide it")
        self.assertFalse(reg.cancel("nonexistent"))

    async def test_buffer_is_bounded(self):
        """A pathological run must not grow the buffer without limit; live
        streaming continues, only the replay is truncated."""
        gen = reg.start("c1")
        chunk = "x" * 100_000
        for _ in range(int(reg.MAX_BUFFER_BYTES / len(chunk)) + 20):
            await gen.put(chunk)
        self.assertLessEqual(gen._bytes, reg.MAX_BUFFER_BYTES)
        self.assertTrue(gen.info()["truncated"])


class TestBackgroundEndpoints(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        reg.clear()
        from app import app as fastapi_app
        self._client = AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url="http://test"
        )

    async def asyncTearDown(self):
        await self._client.aclose()
        reg.clear()

    async def test_active_lists_only_running_generations(self):
        from unittest.mock import patch
        import app.storage as storage

        real = storage.load_prefs

        def open_prefs():
            p = dict(real())
            p["setup_completed"] = False   # bypass auth for the test
            return p

        with patch.object(storage, "load_prefs", open_prefs):
            r = await self._client.get("/api/chat/active")
            self.assertEqual(r.json(), {"active": []})

            gen = reg.start("c1", "Random PDF Generation")
            r = await self._client.get("/api/chat/active")
            listed = r.json()["active"]
            self.assertEqual(listed[0]["conversation_id"], "c1")
            self.assertEqual(listed[0]["title"], "Random PDF Generation")

            await gen.put(None)
            self.assertEqual((await self._client.get("/api/chat/active")).json()["active"], [])

            # Nothing to attach to once it's gone from the registry
            self.assertEqual((await self._client.get("/api/chat/attach/unknown")).status_code, 404)

    async def test_attach_endpoint_replays_and_follows(self):
        from unittest.mock import patch
        import app.storage as storage

        real = storage.load_prefs

        def open_prefs():
            p = dict(real())
            p["setup_completed"] = False
            return p

        with patch.object(storage, "load_prefs", open_prefs):
            gen = reg.start("c1")

            async def produce():
                await gen.put('data: {"type":"content","delta":"Hello "}\n\n')
                await asyncio.sleep(0.1)      # client attaches during this gap
                await gen.put('data: {"type":"content","delta":"world"}\n\n')
                await gen.put(None)

            gen.task = asyncio.create_task(produce())
            await asyncio.sleep(0.01)

            received = []
            async with self._client.stream("GET", "/api/chat/attach/c1") as resp:
                self.assertEqual(resp.status_code, 200)
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        received.append(line)

            body = "".join(received)
            self.assertIn("Hello ", body, "did not replay the part sent before attaching")
            self.assertIn("world", body, "did not keep streaming after the replay")


if __name__ == "__main__":
    unittest.main()
