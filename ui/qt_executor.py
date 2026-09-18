# -*- coding: utf-8 -*-
"""Threaded Anki work with queued callbacks on the Qt GUI thread."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger("aqa")


class _ResultBridge(QObject):
    completed = Signal(object, object, object)


class QtThreadedExecutor:
    """Adapter matching ``QuickAddController``'s ``executor(job, done)`` API.

    Only the worker function runs in the pool.  Its result is emitted through
    a QObject owned by the GUI thread; no widget is ever touched by a worker.
    """

    def __init__(self, max_workers: int = 1):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aqa")
        self._bridge = _ResultBridge()
        self._bridge.completed.connect(self._deliver)
        self._closed = False

    def __call__(self, job, done) -> None:
        if self._closed:
            return

        def work() -> None:
            try:
                value = job()
                self._bridge.completed.emit(done, value, None)
            except BaseException as exc:  # return failures to the GUI state machine
                self._bridge.completed.emit(done, None, exc)

        self._pool.submit(work)

    @Slot(object, object, object)
    def _deliver(self, done, value, error) -> None:
        try:
            done(value, error)
        except Exception:
            log.exception("结果回调处理失败")

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

