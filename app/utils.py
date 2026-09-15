from datetime import datetime, timezone
import uuid


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_dt().isoformat()


def time_hhmmss(dt: datetime | None = None) -> str:
    dt = dt or now_dt()
    return dt.astimezone(timezone.utc).strftime("%H:%M:%S")


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]