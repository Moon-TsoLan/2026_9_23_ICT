"""Scheduling regressions: only merge yields; normal work stays bounded; answers stay identical."""

import asyncio
import json
import threading

import pytest

from ict.scheduling import MergeScheduler
from ict.llm import FakeLLMClient
from ict.pipeline import announcement_chain, run_announcement, run_batch
from test_pipeline import HTML


def test_only_merge_releases_case_slot_and_live_cases_are_bounded():
    release_merge = threading.Event()
    second_front = threading.Event()
    second_front_release = threading.Event()
    third_front = threading.Event()
    lock = threading.Lock()
    merge_active, peak_merges, live, peak_live = 0, 0, 0, 0

    def chain(number):
        nonlocal merge_active, peak_merges, live, peak_live
        with lock:
            live += 1
            peak_live = max(live, peak_live)
        if number == 1:
            second_front.set()
            assert second_front_release.wait(3)
        if number == 2:
            third_front.set()
        def merge():
            nonlocal merge_active, peak_merges
            with lock:
                merge_active += 1
                peak_merges = max(peak_merges, merge_active)
            try:
                assert release_merge.wait(3)
                return number
            finally:
                with lock:
                    merge_active -= 1
        value = yield merge
        with lock:
            live -= 1
        return value

    async def scenario():
        with MergeScheduler(1, 1) as scheduler:
            tasks = [asyncio.create_task(scheduler.run(chain(n))) for n in range(5)]
            try:
                assert await asyncio.to_thread(second_front.wait, 2)
                assert not third_front.is_set()  # second front is still using the only normal slot
                assert not release_merge.is_set()  # it started while first merge was in flight
            finally:
                second_front_release.set()
                release_merge.set()
            assert await asyncio.gather(*tasks) == list(range(5))
    asyncio.run(scenario())
    assert peak_merges == 1 and peak_live == 2 and live == 0


def _model():
    body = {"project_name": "示例项目", "package_mode": "single", "packages": [
        {"source_package_no": "1", "candidates": [{"entity_type": "cob", "source_ref": "html:body",
         "source_class": "cob_detail", "fields": {"object_name": "显示屏", "brand": "创维",
         "quantity": "2块", "unit_price": "100"}}]}]}
    class Capture(FakeLLMClient):
        def __init__(self):
            super().__init__({"read_announcement_summary": json.dumps({"purchaser": "示例单位"}),
                              "read_announcement_body": json.dumps(body),
                              "merge_candidates": '{"deltas": [], "quantity_accounting": []}'})
            self.requests = []

        def complete(self, *, step, prompt_version, user, thinking=False):
            self.requests.append((step, prompt_version, user, thinking))
            return super().complete(step=step, prompt_version=prompt_version, user=user, thinking=thinking)
    return Capture()


def test_batch_and_sync_share_payload_outputs_and_call_counts(tmp_path, monkeypatch):
    html = tmp_path / "html"
    html.mkdir()
    for aid in ("a", "b"):
        (html / f"{aid}.html").write_text(HTML, encoding="utf-8")
    monkeypatch.setattr("ict.pipeline.load_index", lambda *a: None)
    sync_clients = {aid: _model() for aid in ("a", "b")}
    sync = {aid: run_announcement(aid, llm=sync_clients[aid], html_dir=html, runs_root=tmp_path / "sync")
            for aid in ("a", "b")}
    batch_client = _model()
    batched = run_batch(["a", "a", "b"], workers=1, llm=batch_client, html_dir=html,
                        runs_root=tmp_path / "batch")
    assert list(batched) == ["a", "b"]
    assert sorted(batch_client.requests) == sorted(request for client in sync_clients.values()
                                                   for request in client.requests)
    for aid in batched:
        assert batched[aid].result == sync[aid].result
        assert batched[aid].llm_calls == sync[aid].llm_calls
        assert batched[aid].counts == sync[aid].counts
        assert batched[aid].failure_summary == sync[aid].failure_summary
        assert batched[aid].llm_calls["merge_candidates"] == 1
        for name in ("08_normalized_candidates.json", "08b_merge_evidence.json", "09_merged_projects.json",
                     "projects.json"):
            read = lambda kind: json.loads((tmp_path / kind / aid / name).read_text(encoding="utf-8"))
            assert read("sync") == read("batch")


def test_merge_display_phases_and_no_premature_output(tmp_path, monkeypatch):
    html = tmp_path / "html"
    html.mkdir()
    (html / "a.html").write_text(HTML, encoding="utf-8")
    monkeypatch.setattr("ict.pipeline.load_index", lambda *a: None)
    chain = announcement_chain("a", llm=_model(), html_dir=html, runs_root=tmp_path / "runs")
    run_dir = tmp_path / "runs/a"
    state = lambda: json.loads((run_dir / "run_state.json").read_text(encoding="utf-8"))
    merge = next(chain)
    assert state()["merge_wait"] == "queued"
    assert state()["status"] == "running" and not (run_dir / "projects.json").exists()
    import ict.pipeline as pipeline
    original = pipeline.merge_projects
    def checked(*args, **kwargs):
        assert state()["merge_wait"] == "running"
        return original(*args, **kwargs)
    monkeypatch.setattr(pipeline, "merge_projects", checked)
    merged = merge()
    assert state()["merge_wait"] == "resuming"
    assert not (run_dir / "projects.json").exists()
    try:
        chain.send(merged)
    except StopIteration as done:
        assert done.value.result["packages"]
    else:
        raise AssertionError("chain yielded outside merge")
    assert state()["merge_wait"] is None
    assert state()["current_step"] == "persist_and_report"
    timing = state()["merge_timing"]
    assert all(value.endswith("+08:00") and len(value.split(".")[1].split("+")[0]) == 3 for value in timing.values())
    assert list(timing.values()) == sorted(timing.values())
    report = json.loads((run_dir / "10_run_report.json").read_text(encoding="utf-8"))
    assert report["merge_timing"] == timing


def test_merge_exception_marks_failed_and_batch_drains_other_cases(tmp_path, monkeypatch):
    html = tmp_path / "html"
    html.mkdir()
    for aid in ("a", "b"):
        (html / f"{aid}.html").write_text(HTML, encoding="utf-8")
    monkeypatch.setattr("ict.pipeline.load_index", lambda *a: None)
    import ict.pipeline as pipeline
    original = pipeline.merge_projects
    def checked(run_id, *args, **kwargs):
        if run_id == "run_a":
            raise RuntimeError("merge implementation failed")
        return original(run_id, *args, **kwargs)
    monkeypatch.setattr(pipeline, "merge_projects", checked)
    with pytest.raises(RuntimeError, match="merge implementation failed"):
        run_batch(["a", "b"], workers=1, llm=_model(), html_dir=html, runs_root=tmp_path / "runs")
    state = json.loads((tmp_path / "runs/a/run_state.json").read_text(encoding="utf-8"))
    assert state["status"] == "failed" and state["merge_wait"] is None
    assert state["steps"][-1]["status"] == "failed"
    assert not (tmp_path / "runs/a/projects.json").exists()
    assert (tmp_path / "runs/b/10_run_report.json").exists()


def test_resume_finishes_while_both_front_threads_and_control_are_blocked():
    merge_started = threading.Event()
    release_merge, release_front, release_control = (threading.Event() for _ in range(3))
    fronts = [threading.Event(), threading.Event()]
    control_started = threading.Event()
    resumed = []

    def chain(number):
        if number:
            fronts[number - 1].set()
            assert release_front.wait(5)
        def merge():
            if number == 0:
                merge_started.set()
                assert release_merge.wait(5)
            return number
        value = yield merge
        resumed.append((number, threading.current_thread().name))
        return value

    def control():
        control_started.set()
        assert release_control.wait(5)

    async def scenario():
        with MergeScheduler(2, 2, 1) as scheduler:
            a = asyncio.create_task(scheduler.run(chain(0)))
            tasks, metadata = [a], None
            try:
                assert await asyncio.to_thread(merge_started.wait, 2)
                tasks += [asyncio.create_task(scheduler.run(chain(i))) for i in (1, 2)]
                assert all(await asyncio.gather(*(asyncio.to_thread(e.wait, 2) for e in fronts)))
                assert scheduler.admission_available == 0
                metadata = asyncio.create_task(scheduler.control(control))
                assert await asyncio.to_thread(control_started.wait, 2)
                release_merge.set()
                assert await asyncio.wait_for(asyncio.shield(a), 2) == 0
                assert not release_front.is_set() and not release_control.is_set()
                assert resumed[0][0] == 0 and resumed[0][1].startswith("ict-resume")
            finally:
                release_merge.set(); release_front.set(); release_control.set()
                await asyncio.gather(*tasks)
                if metadata:
                    await metadata
            assert scheduler._live == 0
    asyncio.run(scenario())


def test_separate_front_merge_resume_and_live_limits():
    lock = threading.Lock()
    active = {key: 0 for key in ("front", "merge", "resume", "live")}
    peak = active.copy()
    release_front, release_merge, release_resume = (threading.Event() for _ in range(3))
    front_full, merge_full, resume_full = (threading.Event() for _ in range(3))

    def add(stage, delta):
        with lock:
            active[stage] += delta
            peak[stage] = max(peak[stage], active[stage])
            if stage == "front" and active[stage] == 2: front_full.set()
            if stage == "merge" and active[stage] == 3: merge_full.set()
            if stage == "resume" and active[stage] == 2: resume_full.set()

    def chain(number):
        add("live", 1)
        try:
            add("front", 1)
            assert release_front.wait(5)
            add("front", -1)
            def merge():
                add("merge", 1)
                assert release_merge.wait(5)
                add("merge", -1)
                return number
            value = yield merge
            add("resume", 1)
            assert release_resume.wait(5)
            add("resume", -1)
            return value
        finally:
            add("live", -1)

    async def scenario():
        with MergeScheduler(2, 3, 2) as scheduler:
            tasks = [asyncio.create_task(scheduler.run(chain(i))) for i in range(20)]
            try:
                assert await asyncio.to_thread(front_full.wait, 2)
                release_front.set()
                assert await asyncio.to_thread(merge_full.wait, 2)
                release_merge.set()
                assert await asyncio.to_thread(resume_full.wait, 2)
            finally:
                release_front.set(); release_merge.set(); release_resume.set()
                values = await asyncio.gather(*tasks)
            assert values == list(range(20)) and scheduler.admission_available == 2
    asyncio.run(scenario())
    assert peak["front"] == 2 and peak["merge"] == 3 and peak["resume"] == 2
    assert peak["live"] <= 5 and all(value == 0 for value in active.values())


@pytest.mark.parametrize("stage", ["front", "merge", "resume"])
def test_exception_releases_slots_and_other_chains_drain(stage):
    def chain(number):
        if number == 0 and stage == "front": raise ValueError(stage)
        def merge():
            if number == 0 and stage == "merge": raise ValueError(stage)
            return number
        result = yield merge
        if number == 0 and stage == "resume": raise ValueError(stage)
        return result

    async def scenario():
        with MergeScheduler(1, 1, 1) as scheduler:
            values = await asyncio.gather(*(scheduler.run(chain(i)) for i in range(5)), return_exceptions=True)
            assert isinstance(values[0], ValueError) and values[1:] == [1, 2, 3, 4]
            assert scheduler._live == 0 and scheduler.admission_available == 1
    asyncio.run(scenario())


def test_completed_merges_queue_fifo_without_waiting_for_a_full_batch(monkeypatch):
    releases = [threading.Event() for _ in range(3)]
    hold_first_tail, first_tail_started = threading.Event(), threading.Event()
    order = []

    def chain(number):
        def merge():
            assert releases[number].wait(5)
            return number
        merge.ordinal = number
        value = yield merge
        order.append(number)
        if number == 0:
            first_tail_started.set()
            assert hold_first_tail.wait(5)
        return value

    async def scenario():
        loop = asyncio.get_running_loop()
        returned = [asyncio.Event() for _ in range(3)]
        with MergeScheduler(2, 3, 1) as scheduler:
            original = loop.run_in_executor
            def track(executor, func, *args):
                future = original(executor, func, *args)
                if executor is scheduler._merges:
                    future.add_done_callback(lambda done: returned[func.ordinal].set())
                return future
            monkeypatch.setattr(loop, "run_in_executor", track)
            tasks = [asyncio.create_task(scheduler.run(chain(i))) for i in range(3)]
            try:
                releases[0].set()
                assert await asyncio.to_thread(first_tail_started.wait, 2)
                # Return 2 before 1 while the first tail still owns the sole resume slot.
                for number in (2, 1):
                    releases[number].set()
                    await asyncio.wait_for(returned[number].wait(), 2)
                    # Let the completed merge's continuation enqueue before the next return.
                    await scheduler.control(lambda: None)
                assert order == [0]
            finally:
                for release in releases: release.set()
                hold_first_tail.set()
                assert await asyncio.gather(*tasks) == [0, 1, 2]
    asyncio.run(scenario())
    assert order == [0, 2, 1]
