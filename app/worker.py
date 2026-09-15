from __future__ import annotations

import os
import threading
import time
from uuid import uuid4

_started = False
_lock = threading.Lock()


def _worker_loop(index: int) -> None:
    from app.services.analyze_service import claim_next_run, get_run, release_run_lease, _run_in_background
    worker_id = f"worker-{index}-{uuid4().hex[:8]}"
    idle_seconds = max(0.25, float(os.getenv("RUN_QUEUE_POLL_SECONDS", "1")))
    while True:
        run_id = claim_next_run(worker_id, int(os.getenv("RUN_LEASE_SECONDS", "180")))
        if not run_id:
            time.sleep(idle_seconds)
            continue
        _run_in_background(run_id)
        run = get_run(run_id)
        release_run_lease(run_id, retry=bool(run and run.get("status") == "failed"))


def start_workers() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
        count = min(8, max(1, int(os.getenv("RUN_WORKER_COUNT", "2"))))
        for index in range(count):
            threading.Thread(target=_worker_loop, args=(index,), daemon=True, name=f"run-worker-{index}").start()
