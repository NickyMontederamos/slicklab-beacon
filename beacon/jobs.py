"""Background jobs for the web inbox (drafting calls the AI and can take a minute)."""

from __future__ import annotations

import threading
from collections.abc import Callable

from .store import Store


class Jobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[tuple[str, str], threading.Thread] = {}

    def running(self, client_id: str) -> list[str]:
        with self._lock:
            return [
                kind
                for (cid, kind), t in self._running.items()
                if cid == client_id and t.is_alive()
            ]

    def start(
        self, client_id: str, kind: str, store: Store, actor: str, fn: Callable[[], dict]
    ) -> bool:
        """Start `fn` in a thread. Returns False if the same job is already running."""
        key = (client_id, kind)
        with self._lock:
            existing = self._running.get(key)
            if existing and existing.is_alive():
                return False

            def work() -> None:
                store.log(actor, f"job.{kind}.start")
                try:
                    detail = fn()
                    store.log(actor, f"job.{kind}.done", detail)
                except Exception as e:  # noqa: BLE001 - surface any failure to the operator
                    store.log(actor, f"job.{kind}.error", {"error": f"{type(e).__name__}: {e}"})

            thread = threading.Thread(target=work, name=f"beacon-{client_id}-{kind}", daemon=True)
            self._running[key] = thread
            thread.start()
        return True

    def join_all(self, timeout: float = 30) -> None:
        with self._lock:
            threads = list(self._running.values())
        for t in threads:
            t.join(timeout)
