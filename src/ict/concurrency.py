"""Process-wide concurrency gates.

Announcements are independent, so they may run in separate threads; everything they *share*
has to be either read-only or bounded here. These knobs decide how many calls are in flight,
never what a call means: no judgement, no threshold, no word list lives in this module.
"""

from __future__ import annotations

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from ict.config import concurrency_settings

_settings = concurrency_settings()

# Every chat call in every step passes through this one semaphore, so running two announcements at
# once cannot quietly double the load on the model endpoint.
LLM_GATE = threading.BoundedSemaphore(max(1, int(_settings["llm_max_concurrency"])))

# sha256, page rendering, docx/xlsx reading: the work the two cores actually spend time on.
LOCAL_GATE = threading.BoundedSemaphore(max(1, int(_settings["local_workers"])))

# One worker is a FIFO queue with exactly one document in flight, which is what the GPU box wants.
# The queue, not the caller, does the waiting; when one document finishes the next starts at once.
PARSE_QUEUE = ThreadPoolExecutor(max_workers=max(1, int(_settings["parse_max_inflight"])),
                                 thread_name_prefix="ict-parse")

# The pool every per-unit step submits into. One pool for the whole process, so two announcements
# in flight cannot create two thread budgets; the gate above still bounds the endpoint itself.
LLM_POOL = ThreadPoolExecutor(max_workers=max(1, int(_settings["llm_max_concurrency"])),
                              thread_name_prefix="ict-llm")


def parallel_map(func, items: list) -> list:
    """Run `func` over `items` and hand the results back **in the original order**.

    Order is the whole point: every step in this pipeline assigns sequence numbers, writes summary
    lists and builds failure lists in the order its units were listed, so results are collected by
    position and never by completion. With one item, or a one-worker pool, this is exactly the
    plain loop it replaces.
    """
    items = list(items)
    if len(items) <= 1:
        return [func(item) for item in items]
    futures = [LLM_POOL.submit(func, item) for item in items]
    return [future.result() for future in futures]


class CallCounter:
    """A call counter that survives concurrent increments.

    `counter[step] += 1` is a read, an add and a store, so two threads can lose one of the two
    calls. The numbers here end up in `10_run_report.json`, so a lost increment is a silent lie
    in the audit trail rather than a crash.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: Counter = Counter()

    def add(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self._counts[key] += amount

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._counts)

    def __getitem__(self, key: str) -> int:
        with self._lock:
            return self._counts[key]


def bump_count(counter, key: str) -> None:
    """Increment a step's call count. Accepts CallCounter and a plain collections.Counter."""
    add = getattr(counter, "add", None)
    if callable(add):
        add(key)
    else:
        counter[key] += 1


def counts_snapshot(counter) -> dict:
    """The finished per-step counts, whichever counter type was used."""
    snapshot = getattr(counter, "snapshot", None)
    return snapshot() if callable(snapshot) else dict(counter)
