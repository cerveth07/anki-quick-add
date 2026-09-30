# -*- coding: utf-8 -*-
"""Threaded Anki work with queued callbacks on the Qt GUI thread."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal, Slot

log = logging.getLogger("aqa")


class _ResultBridge(QObject):
    completed = Signal(object)


class QtThreadedExecutor:
    """Adapter matching ``QuickAddController``'s ``executor(job, done)`` API.

    Only the worker function runs in the pool.  Its result is emitted through
    a QObject owned by the GUI thread; no widget is ever touched by a worker.
    """

    def __init__(self, max_workers: int = 1):
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="aqa")
        self._bridge = _ResultBridge()
        self._bridge.completed.connect(self._deliver, Qt.ConnectionType.QueuedConnection)
        self._closed = False
        self._pending = {}
        self._on_finished = None

    def __call__(self, job, done) -> None:
        if self._closed:
            return

        future = self._pool.submit(job)
        self._pending[future] = done
        future.add_done_callback(self._bridge.completed.emit)

    @Slot(object)
    def _deliver(self, future) -> None:
        done = self._pending.pop(future)
        if self._closed:
            self._finish_shutdown()
            return
        try:
            value, error = future.result(), None
        except BaseException as exc:
            value, error = None, exc
        try:
            done(value, error)
        except Exception:
            log.exception("结果回调处理失败")

    def shutdown(self, on_finished=None) -> None:
        if self._closed:
            return
        self._closed = True
        self._on_finished = on_finished
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._finish_shutdown()

    def _finish_shutdown(self) -> None:
        if not self._pending and self._on_finished is not None:
            callback, self._on_finished = self._on_finished, None
            callback()
