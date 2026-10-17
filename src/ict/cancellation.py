"""Cancellation callbacks for an individual transport, never a shared connection pool."""
import threading


class Cancellation:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._callbacks = []

    def is_set(self):
        return self._event.is_set()

    def bind(self, callback):
        with self._lock:
            if not self.is_set():
                self._callbacks.append(callback)
                return
        callback()

    def set(self):
        with self._lock:
            self._event.set()
            callbacks, self._callbacks = self._callbacks, []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass  # Cancellation must still reach the remaining owned resources.

    def clear_callbacks(self):
        with self._lock:
            self._callbacks.clear()
