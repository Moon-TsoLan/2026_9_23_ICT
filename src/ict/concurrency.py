"""Process-wide concurrency gates.

Announcements are independent, so they may run in separate threads; everything they *share*
has to be either read-only or bounded here. These knobs decide how many calls are in flight,
never what a call means: no judgement, no threshold, no word list lives in this module.
"""

from __future__ import annotations

import threading
from collections import Counter
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor

from ict.config import concurrency_settings

_settings = concurrency_settings()

# Every chat call in every step passes through this one semaphore, so running two announcements at
# once cannot quietly double the load on the model endpoint.
LLM_GATE = threading.BoundedSemaphore(max(1, int(_settings["llm_max_concurrency"])))

# sha256, page rendering, docx/xlsx reading: the work the two cores actually spend time on.
LOCAL_GATE = threading.BoundedSemaphore(max(1, int(_settings["local_workers"])))

class ParseQueue:
    """One document on the GPU at a time, rotated fairly between the announcements wanting it.

    The GPU box parses one document at a time - that is the contract - so this queue is where a
    run's waiting happens. A plain FIFO lets whichever announcement submitted first hold the box
    for its whole batch: with ten files ahead of it, a one-file announcement waits for all ten.
    This dispatcher keeps each owner's own submission order and rotates between owners, so every
    announcement gets a turn.

    The total GPU work is unchanged, so the big announcement still finishes when it would have;
    the small one no longer waits for it. The queue, not the caller, does the waiting, and the
    next job is already picked when the current one ends, so the box is never idle while work is
    pending.

    `workers` is `ICT_PARSE_MAX_INFLIGHT`: 1 is the contract, and raising it is an experiment.
    """

    def __init__(self, workers: int = 1) -> None:
        self.workers = max(1, int(workers))
        self._lock = threading.Condition()
        self._pending: dict[str, deque] = {}
        self._rotation: list[str] = []
        self._closed = False
        self._threads = [threading.Thread(target=self._dispatch, daemon=True,
                                          name="ict-parse-%d" % index)
                         for index in range(self.workers)]
        for thread in self._threads:
            thread.start()

    # -- producer side -------------------------------------------------------

    def submit(self, owner: str, func, *args, **kwargs) -> Future:
        """Queue one call for `owner`. Owners take turns; within an owner it stays first-in-first-out."""
        future: Future = Future()
        with self._lock:
            if self._closed:
                raise RuntimeError("parse queue is closed")
            if owner not in self._pending:
                self._pending[owner] = deque()
                self._rotation.append(owner)
            self._pending[owner].append((future, func, args, kwargs))
            self._lock.notify()
        return future

    def shutdown(self, wait: bool = True) -> None:
        """Stop accepting work and let the queued jobs finish."""
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        if wait:
            for thread in self._threads:
                thread.join(timeout=5)

    # -- consumer side -------------------------------------------------------

    def _take(self) -> tuple:
        """Pop the next job from the front owner, and send that owner to the back if it has more."""
        owner = self._rotation.pop(0)
        pending = self._pending[owner]
        job = pending.popleft()
        if pending:
            self._rotation.append(owner)
        else:
            del self._pending[owner]
        return job

    def _dispatch(self) -> None:
        while True:
            with self._lock:
                while not self._rotation and not self._closed:
                    self._lock.wait()
                if not self._rotation:
                    return
                future, func, args, kwargs = self._take()
            if future.set_running_or_notify_cancel():
                try:
                    result = func(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 - the caller must see the real error
                    future.set_exception(exc)
                else:
                    future.set_result(result)


PARSE_QUEUE = ParseQueue(int(_settings["parse_max_inflight"]))

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
