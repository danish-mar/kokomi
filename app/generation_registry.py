"""In-memory registry of in-flight chat generations.

Previously a generation lived and died with the HTTP request: closing the tab
cancelled the task, and since the conversation is only written to the database
once the whole response is finished, everything was lost — including the user's
own message. This decouples the two. The generation runs to completion no
matter who is watching, and clients attach to it.

Every SSE event is also retained, so a client that reconnects gets a replay of
what it missed and then continues streaming live from that point, rather than
staring at a blank conversation until the whole thing lands.

Deliberately in-memory and single-process: the app runs one uvicorn worker
(see main.py), so a dict is sufficient and a restart mid-generation losing
the buffer is acceptable — the alternative is persisting a partial stream,
which buys little for a single-user app.
"""
import asyncio
import time

# Buffered bytes kept per generation for replay. A long response is tens of KB;
# this is a ceiling against a pathological run, after which live streaming
# continues but late joiners get a truncated replay.
MAX_BUFFER_BYTES = 8 * 1024 * 1024

# How long a finished generation stays queryable, so a client reconnecting just
# as it completes still receives the tail and the done event instead of finding
# nothing.
DONE_TTL_SECONDS = 300


class Generation:
    """One in-flight response. Exposes `put` so it can be dropped in where an
    asyncio.Queue was used, leaving the producer code untouched."""

    def __init__(self, conv_id: str, title: str | None = None):
        self.conv_id = conv_id
        self.title = title
        self.events: list[str] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.task: asyncio.Task | None = None
        self.done = False
        self.cancelled = False
        self.started_at = time.time()
        self.finished_at: float | None = None
        self._bytes = 0
        self._truncated = False

    async def put(self, item):
        """Record an event and fan it out. `None` is the end-of-stream sentinel."""
        if item is None:
            self.done = True
            self.finished_at = time.time()
        else:
            if self._bytes + len(item) <= MAX_BUFFER_BYTES:
                self.events.append(item)
                self._bytes += len(item)
            else:
                self._truncated = True
        for q in list(self.subscribers):
            q.put_nowait(item)

    def subscribe(self) -> asyncio.Queue:
        """Attach a client: replays everything so far, then receives live events.

        The replay and the registration happen with no await between them, so
        no event can slip through the gap and be missed.
        """
        q: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            q.put_nowait(event)
        if self.done:
            q.put_nowait(None)
        self.subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        """Detach a client. The generation itself keeps running — that is the
        whole point; a disconnect must not abort the work."""
        self.subscribers.discard(q)

    def info(self) -> dict:
        return {
            "conversation_id": self.conv_id,
            "title": self.title,
            "started_at": self.started_at,
            "done": self.done,
            "cancelled": self.cancelled,
            "watchers": len(self.subscribers),
            "truncated": self._truncated,
        }


_generations: dict[str, Generation] = {}


def _sweep() -> None:
    now = time.time()
    for cid, gen in list(_generations.items()):
        if gen.done and gen.finished_at and now - gen.finished_at > DONE_TTL_SECONDS:
            _generations.pop(cid, None)


def start(conv_id: str, title: str | None = None) -> Generation:
    """Register a new generation, replacing any finished one for that
    conversation (a new message supersedes the previous response's buffer)."""
    _sweep()
    gen = Generation(conv_id, title)
    _generations[conv_id] = gen
    return gen


def get(conv_id: str) -> Generation | None:
    return _generations.get(conv_id)


def active() -> list[dict]:
    """Generations still running — what the sidebar glows for."""
    _sweep()
    return [g.info() for g in _generations.values() if not g.done]


def cancel(conv_id: str) -> bool:
    """Actually stop a generation. Required once a generation outlives its
    request: without it, "stop" would only hide the response from the user
    while it kept running and consuming tokens."""
    gen = _generations.get(conv_id)
    if not gen or gen.done:
        return False
    gen.cancelled = True
    if gen.task and not gen.task.done():
        gen.task.cancel()
    return True


def clear() -> None:
    """Test helper — drop all state."""
    _generations.clear()
