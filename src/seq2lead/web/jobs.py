"""One inference at a time, with progress and cancellation.

Inference is serialised on purpose. Two concurrent rankings on one machine would
contend for the same encoder and the same accelerator, making both slower and the
reported timings meaningless. A second request therefore queues rather than
racing, and the queue position is visible.

Cancellation is cooperative: the worker checks between phases. There is no way to
interrupt a single `forward` call partway through, so a cancel during embedding
takes effect when that phase ends. Saying so is better than implying an
instantaneous stop.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Cancelled(Exception):
    """Raised inside a worker when the job was cancelled between phases."""


@dataclass
class Job:
    id: str
    state: JobState = JobState.QUEUED
    phase: str = "queued"
    progress: float = 0.0
    result: Any = None
    error: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def to_dict(self, queue_position: int | None = None) -> dict[str, Any]:
        started = self.started_at or self.created_at
        finished = self.finished_at or time.time()
        payload: dict[str, Any] = {
            "id": self.id,
            "state": self.state.value,
            "phase": self.phase,
            "progress": round(self.progress, 3),
            "error": self.error,
            "elapsed": round(finished - started, 2),
        }
        if queue_position is not None:
            payload["queue_position"] = queue_position
        return payload


class JobQueue:
    """A single worker thread. Jobs run in submission order, never concurrently."""

    def __init__(self, keep: int = 32) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._pending: list[tuple[Job, Callable[[Job], Any]]] = []
        self._wake = threading.Condition(self._lock)
        self._keep = keep
        self._worker = threading.Thread(target=self._run, name="seq2lead-inference", daemon=True)
        self._worker.start()

    def submit(self, work: Callable[[Job], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12])
        with self._wake:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._pending.append((job, work))
            self._prune_locked()
            self._wake.notify()
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def position(self, job_id: str) -> int | None:
        """How many jobs are ahead of this one, 0 meaning next."""
        with self._lock:
            ids = [j.id for j, _ in self._pending]
            return ids.index(job_id) if job_id in ids else None

    def _prune_locked(self) -> None:
        while len(self._order) > self._keep:
            stale = self._order.pop(0)
            job = self._jobs.get(stale)
            if job and job.state in {JobState.QUEUED, JobState.RUNNING}:
                self._order.append(stale)  # never drop live work
                break
            self._jobs.pop(stale, None)

    def _run(self) -> None:
        while True:
            with self._wake:
                while not self._pending:
                    self._wake.wait()
                job, work = self._pending.pop(0)
            if job.cancelled:
                job.state, job.phase = JobState.CANCELLED, "cancelled before it started"
                job.finished_at = time.time()
                continue
            job.state, job.started_at, job.phase = JobState.RUNNING, time.time(), "starting"
            try:
                job.result = work(job)
                job.state = JobState.CANCELLED if job.cancelled else JobState.DONE
                job.phase = "cancelled" if job.cancelled else "done"
                job.progress = job.progress if job.cancelled else 1.0
            except Cancelled:
                job.state, job.phase = JobState.CANCELLED, "cancelled"
            except Exception as exc:  # surfaced to the browser, not swallowed
                job.state, job.phase = JobState.FAILED, "failed"
                job.error = f"{type(exc).__name__}: {exc}"
            finally:
                job.finished_at = time.time()
