"""Local history of enrollment confirmations, independent of live registration state."""
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app_data import data_directory, legacy_data_directory


_CREATE_CATCHES = """
    CREATE TABLE IF NOT EXISTS catches (
        id TEXT PRIMARY KEY, confirmed REAL NOT NULL, record TEXT NOT NULL
    )
"""
_INSERT_CATCH = "INSERT INTO catches VALUES (?, ?, ?) ON CONFLICT(id) DO NOTHING"


def sent_credits(requests, section_id):
    for request in requests:
        body = request.get("request", {}).get("json", {})
        for item in body.get("sectionRegistrations", []):
            if item.get("SectionId") == section_id and item.get("Action") in ("Add", "Waitlist"):
                if "Credits" in item:
                    return {"status": "included", "value": item["Credits"]}
                return {"status": "omitted"}
    return {"status": "unrecorded"}


def _record_key(record):
    student = record.get("student_id")
    identity = ["student", student] if student else ["run", record["run_id"]]
    return json.dumps([*identity, record.get("term_id"), record["section_id"]], ensure_ascii=False)


class CatchHistory:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else data_directory() / "catches.sqlite3"
        legacy = legacy_data_directory() if path is None else None
        self._legacy_path = legacy / self.path.name if legacy else None

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path, timeout=1)

    def migrate_legacy(self):
        source = self._legacy_path
        if source is None or not source.is_file() or source.resolve() == self.path.resolve():
            self._legacy_path = None
            return
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS ksc_migrations (name TEXT PRIMARY KEY)")
            migration = connection.execute("INSERT OR IGNORE INTO ksc_migrations VALUES ('legacy-home-catches')")
            if migration.rowcount:
                connection.execute(_CREATE_CATCHES)
                # A read-only SQLite connection includes committed rows still in the WAL.
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=1)) as legacy:
                    has_catches = legacy.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='catches'")
                    if has_catches.fetchone():
                        rows = legacy.execute("SELECT id, confirmed, record FROM catches")
                        connection.executemany(_INSERT_CATCH, rows)
        self._legacy_path = None

    def revision(self):
        """Check for changes from other processes after the initial migration."""
        self.migrate_legacy()
        stamps = []
        for path in (self.path, self.path.with_name(self.path.name + "-wal")):
            try:
                info = path.stat()
            except FileNotFoundError:
                stamps.append(None)
            else:
                stamps.append((info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size))
        return tuple(stamps)

    def save(self, records):
        rows = []
        for record in records:
            confirmed = datetime.fromisoformat(record["confirmed_at"]).timestamp()
            rows.append((_record_key(record), confirmed, json.dumps(record, ensure_ascii=False)))
        if not rows:
            return False
        self.migrate_legacy()
        with closing(self._connect()) as connection, connection:
            connection.execute(_CREATE_CATCHES)
            inserted = connection.executemany(_INSERT_CATCH, rows)
            return inserted.rowcount > 0

    def records(self):
        self.migrate_legacy()
        if not self.path.exists():
            return []
        with closing(self._connect()) as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT record FROM catches ORDER BY confirmed DESC, id")]

    def delete(self, record):
        self.migrate_legacy()
        if not self.path.exists():
            return False
        with closing(self._connect()) as connection, connection:
            deleted = connection.execute("DELETE FROM catches WHERE id = ? AND record = ?",
                                         (_record_key(record), json.dumps(record, ensure_ascii=False)))
            return deleted.rowcount == 1

    def import_legacy(self, path, course_details=None):
        """Recover only attempted, verified Sections from a pre-body JSONL log."""
        path = Path(path)
        starts, attempts, reads = {}, {}, {}
        records = []
        with path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # An interrupted append may leave a partial line.
                if not isinstance(event, dict):
                    continue
                kind = event.get("event")
                if kind == "request_started":
                    if event.get("operation") in (
                            "register_add", "register_waitlist", "complete_registration", "registration_read"):
                        starts[event["request_id"]] = event
                elif kind == "request_finished":
                    start = starts.pop(event.get("request_id"), None)
                    if start is not None:
                        self._legacy_request(start, event, attempts, reads)
                elif kind == "submission":
                    for sid in event.get("section_ids", []):
                        if sid in event.get("duplicates", []):
                            attempts.pop(sid, None)
                        elif sid in attempts:
                            unresolved = event.get("unresolved_credits")
                            status = "unrecorded" if unresolved is None else (
                                "omitted" if sid in unresolved else "included_value_unrecorded")
                            attempts[sid]["sent_credits"] = {"status": status}
                elif kind == "verification":
                    for sid, result in event.get("sections", {}).items():
                        attempt = attempts.get(sid)
                        if (not attempt or "sent_credits" not in attempt
                                or result.get("status") != "REGISTERED" or not result.get("attempted")):
                            continue
                        confirmed = result.get("first_confirmed_at") or event.get("observed_at")
                        if not confirmed:
                            continue
                        details = (course_details or {}).get(sid, {})
                        records.append({
                            "section_id": sid, "course_name": details.get("course_name"),
                            "term_id": result.get("term_id"), "credits": details.get("credits"),
                            "credits_source": "provided" if details.get("credits") is not None else None,
                            "student_id": event.get("student_id"),
                            "confirmed_at": confirmed, "server_registered_at": result.get("server_registered_at"),
                            "run_id": event["run_id"], "source_log": path.name, "source": "legacy",
                            "sent_credits": attempt["sent_credits"],
                            "requests": [*attempt["requests"], *([reads[sid]] if sid in reads else [])],
                        })
                        attempts.pop(sid)
        self.save(records)
        return records

    @staticmethod
    def _legacy_request(start, end, attempts, reads):
        trace = {key: end[key] for key in ("request_id", "round_id", "operation", "section_ids",
                                           "http_status", "reason", "response_received_at", "elapsed_ms")
                 if key in end}
        trace.update(method=start.get("method"), started_at=start.get("timestamp"))
        operation = start["operation"]
        for sid in start.get("section_ids", []):
            if operation == "registration_read":
                reads[sid] = trace
            elif operation == "complete_registration":
                if sid in attempts:
                    attempts[sid]["requests"].append(trace)
            elif end.get("reason") not in ("session_expired", "ConnectTimeout"):
                attempts[sid] = {"requests": [trace]}
                reads.pop(sid, None)
