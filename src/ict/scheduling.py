"""Yield only the merge call; keep the rest of each announcement's chain synchronous."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from functools import partial


def _advance(chain, value=None, error=None):
    # StopIteration must be consumed here: asyncio futures cannot carry it.
    try:
        work = chain.throw(error) if error is not None else chain.send(value)
        return False, work
    except StopIteration as done:
        return True, done.value


def run_sync(chain):
    """The single-announcement entry uses exactly the same chain without overlapping cases."""
    value, error = None, None
    while True:
        done, work = _advance(chain, value, error)
        if done:
            return work
        try:
            value, error = work(), None
        except Exception as exc:
            value, error = None, exc


class MergeScheduler:
    """Bound live cases with separate front, merge, continuation and metadata pools.

    A suspended generator retains its own existing locals. It is resumed by one thread at a
    time; no candidate/state copying or alternate pipeline is involved. Merge coordinators
    have their own pool, separate from the existing pool executing per-package LLM calls.
    """

    def __init__(self, workers: int, merge_workers: int, resume_workers: int = 1):
        workers, merge_workers = max(1, workers), max(1, merge_workers)
        self.workers = workers
        self.capacity = workers + merge_workers
        self._cases = asyncio.Semaphore(self.capacity)
        self._front_slots = asyncio.Semaphore(workers)
        self._front_occupied = self._live = 0
        self._front = ThreadPoolExecutor(workers, thread_name_prefix="ict-front")
        self._merges = ThreadPoolExecutor(merge_workers, thread_name_prefix="ict-merge")
        self._resume = ThreadPoolExecutor(max(1, resume_workers), thread_name_prefix="ict-resume")
        self._control = ThreadPoolExecutor(1, thread_name_prefix="ict-control")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._front.shutdown(wait=True)
        self._merges.shutdown(wait=True)
        self._resume.shutdown(wait=True)
        self._control.shutdown(wait=True)

    @property
    def admission_available(self):
        """Only claim cases that can start their front segment, within the live-case bound."""
        return min(self.workers - self._front_occupied, self.capacity - self._live)

    async def control(self, func, *args, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(
            self._control, partial(func, *args, **kwargs))

    async def run(self, chain):
        loop = asyncio.get_running_loop()
        # Wait before executor submission. Pending batch coroutines hold no loaded case data
        # and cannot build a FIFO backlog in front of already-completed merges.
        async with self._front_slots:
            self._front_occupied += 1
            try:
                await self._cases.acquire()
                self._live += 1
                try:
                    done, work = await loop.run_in_executor(self._front, _advance, chain)
                except BaseException:
                    self._live -= 1
                    self._cases.release()
                    raise
            finally:
                self._front_occupied -= 1
        try:
            while not done:
                try:
                    value, error = await loop.run_in_executor(self._merges, work), None
                except Exception as exc:
                    value, error = None, exc
                # Enqueue on return, never on batch size or timer. Exceptions enter the same
                # chain's catch/finally path on this dedicated continuation pool as well.
                done, work = await loop.run_in_executor(self._resume, _advance, chain, value, error)
            return work
        finally:
            self._live -= 1
            self._cases.release()
