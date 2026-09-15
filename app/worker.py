from __future__ import annotations

import queue
import threading
from typing import Optional

_WORK_QUEUE: "queue.Queue[str]" = queue.Queue()
_WORKER_THREAD: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()


def enqueue_run(run_id: str) -> None:
    _WORK_QUEUE.put(run_id)


def start_worker() -> None:
    global _WORKER_THREAD

    with _WORKER_LOCK:
        if _WORKER_THREAD and _WORKER_THREAD.is_alive():
            return

        _WORKER_THREAD = threading.Thread(
            target=_worker_loop,
            name="tradingagents-worker",
            daemon=True,
        )
        _WORKER_THREAD.start()


def _worker_loop() -> None:
    from app.services.analyze_service import process_run

    while True:
        run_id = _WORK_QUEUE.get()
        try:
            process_run(run_id)
        except Exception:
            # process_run is expected to record failures itself.
            pass
        finally:
            _WORK_QUEUE.task_done()