"""Real-time fan-out to WebSocket clients (dashboard and Android app)."""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, field


@dataclass(eq=False)
class Client:
    user: dict
    q: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2000))
    devices: set = field(default_factory=set)  # devices this client may see (refreshed by core)

    def push(self, msg: dict):
        try:
            self.q.put_nowait(json.dumps(msg, default=str))
        except queue.Full:  # a stalled client must not slow everyone down
            pass


class Hub:
    def __init__(self):
        self._clients: set[Client] = set()
        self._lock = threading.Lock()

    def add(self, c: Client):
        with self._lock:
            self._clients.add(c)

    def remove(self, c: Client):
        with self._lock:
            self._clients.discard(c)

    def clients(self) -> list[Client]:
        with self._lock:
            return list(self._clients)

    def send(self, msg: dict, device_id: str | None = None, user_ids: set | None = None, roles: set | None = None):
        """Send to every client allowed to see `device_id` (admins see all), or to
        the given users / roles."""
        for c in self.clients():
            u = c.user
            if user_ids is not None and u["id"] not in user_ids:
                continue
            if roles is not None and u["role"] not in roles:
                continue
            if device_id is not None and u["role"] != "admin" and device_id not in c.devices:
                continue
            c.push(msg)

    def count(self) -> int:
        with self._lock:
            return len(self._clients)
