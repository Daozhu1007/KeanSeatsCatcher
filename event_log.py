"""Append-only, credential-free registration audit events."""
import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class EventLog:
    def __init__(self, directory=None):
        self.run_id = uuid.uuid4().hex
        directory = Path(directory or os.environ.get(
            "KSC_LOG_DIR", Path.home() / ".keanseatscatcher" / "logs"))
        self.path = directory / f"registration-{self.run_id}.jsonl"
        self.error = None
        self._lock = threading.Lock()

    def write(self, event, **fields):
        # Callers supply only operation names, IDs, status/reason codes and times.
        # Never pass bodies, headers, cookies, URLs or exception strings here.
        record = dict(timestamp=timestamp(), run_id=self.run_id, event=event, **fields)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    stream.flush()
            self.error = None
        except OSError as exc:
            # A logging failure must not obscure the outcome of a sent request.
            self.error = type(exc).__name__
