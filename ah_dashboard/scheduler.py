"""Background work of the (single) server process.

Everything that runs outside a request goes through one :class:`Scheduler`,
created by ``create_app``:

- :meth:`Scheduler.every`: a periodic job (e.g. keeping the Bonus loaded,
  pruning expired sign-ins). A job may return a number of seconds to wait
  before its next run (e.g. a longer pause after a failure); a job that
  raises is logged and retried after ``backoff`` seconds, never killed.
- :meth:`Scheduler.spawn`: a one-off task (e.g. a cache entry reloading in the
  background).

:meth:`Scheduler.start` starts the periodic jobs; :meth:`Scheduler.stop` ends
them (and makes ``spawn`` a no-op), so tests and shutdown are clean. All
threads are daemons: they never keep the process alive.

The app must run as ONE process (see CLAUDE.md: AH rotates the refresh token
on every renewal), so there is no cross-process coordination here.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from .logging_setup import log_error

log = logging.getLogger(__name__)

Job = Callable[[], "float | None"]


@dataclass
class _Periodic:
    name: str
    interval: float
    fn: Job
    backoff: float
    thread: threading.Thread | None = None


class Scheduler:
    def __init__(self) -> None:
        self._jobs: dict[str, _Periodic] = {}
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._started = False

    # ---- periodic jobs -----------------------------------------------------------------
    def every(self, name: str, interval: float, fn: Job, *, backoff: float | None = None) -> None:
        """Run ``fn`` now and then every ``interval`` seconds (or after the
        number of seconds it returns). Registered jobs start with :meth:`start`;
        a job added after that starts at once."""
        job = _Periodic(name, interval, fn, max(interval, backoff or interval))
        with self._lock:
            if name in self._jobs:
                raise ValueError(f"job {name!r} already registered")
            self._jobs[name] = job
            if self._started:
                self._launch(job)

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop.clear()
            for job in self._jobs.values():
                self._launch(job)

    def stop(self, timeout: float = 5.0) -> None:
        """Ask every job to end and wait up to ``timeout`` seconds for them."""
        self._stop.set()
        with self._lock:
            self._started = False
            threads = [j.thread for j in self._jobs.values() if j.thread]
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(timeout)

    def running(self, name: str) -> bool:
        job = self._jobs.get(name)
        return bool(job and job.thread and job.thread.is_alive())

    def jobs(self) -> dict[str, bool]:
        """Registered jobs and whether each is running (admin panel)."""
        return {name: self.running(name) for name in self._jobs}

    def _launch(self, job: _Periodic) -> None:
        job.thread = threading.Thread(target=self._loop, args=(job,), name=f"job-{job.name}", daemon=True)
        job.thread.start()

    def _loop(self, job: _Periodic) -> None:
        while not self._stop.is_set():
            try:
                delay = job.fn()
            except Exception:  # a job must never die: log and try again later
                log_error(log, "background job %s crashed", job.name, exc_info=True)
                delay = job.backoff
            self._stop.wait(job.interval if delay is None else max(0.0, delay))

    # ---- one-off tasks -----------------------------------------------------------------
    def spawn(self, fn: Callable[[], None], name: str = "task") -> None:
        """Run ``fn`` once in a daemon thread (not after :meth:`stop`)."""
        if self._stop.is_set():
            log.info("scheduler stopped: task %s not started", name)
            return
        threading.Thread(target=fn, name=name, daemon=True).start()
