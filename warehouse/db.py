"""SQLite ownership and maintenance-window transaction boundaries."""
import json
import sqlite3
from contextlib import contextmanager
from .errors import Conflict

DDL = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO metadata VALUES('storage_version','1');
CREATE TABLE IF NOT EXISTS schemas(
 schema_id INTEGER PRIMARY KEY, document TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS batches(
 batch_id TEXT PRIMARY KEY, schema_id INTEGER NOT NULL REFERENCES schemas,
 payloads TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs(
 job_id TEXT PRIMARY KEY, reader_id INTEGER NOT NULL REFERENCES schemas,
 inputs TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('queued','done','failed')));
CREATE TABLE IF NOT EXISTS results(
 job_id TEXT PRIMARY KEY REFERENCES jobs,
 outcome TEXT NOT NULL CHECK(outcome IN ('done','failed')),
 error_code TEXT, error_message TEXT);
CREATE TABLE IF NOT EXISTS result_values(
 job_id TEXT NOT NULL REFERENCES results, ordinal INTEGER NOT NULL,
 batch_id TEXT NOT NULL REFERENCES batches, row_index INTEGER NOT NULL,
 value TEXT NOT NULL, PRIMARY KEY(job_id,ordinal));
CREATE TABLE IF NOT EXISTS receipts(
 operation TEXT NOT NULL, request_id TEXT NOT NULL,
 fingerprint TEXT NOT NULL, response TEXT NOT NULL,
 PRIMARY KEY(operation,request_id));
CREATE TABLE IF NOT EXISTS certificates(
 writer_id INTEGER NOT NULL REFERENCES schemas,
 reader_id INTEGER NOT NULL REFERENCES schemas,
 safe INTEGER NOT NULL CHECK(safe IN (0,1)),
 witness TEXT NOT NULL,
 PRIMARY KEY(writer_id,reader_id));
CREATE TRIGGER IF NOT EXISTS immutable_schema BEFORE UPDATE ON schemas
 BEGIN SELECT RAISE(ABORT,'immutable schema'); END;
CREATE TRIGGER IF NOT EXISTS immutable_batch BEFORE UPDATE ON batches
 BEGIN SELECT RAISE(ABORT,'immutable batch'); END;
CREATE TRIGGER IF NOT EXISTS immutable_job_binding BEFORE UPDATE OF reader_id,inputs ON jobs
 BEGIN SELECT RAISE(ABORT,'immutable job binding'); END;
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def claim(db, operation, request_id, fingerprint):
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be nonempty text")
    row = db.conn.execute(
        "SELECT fingerprint,response FROM receipts WHERE operation=? AND request_id=?",
        (operation, request_id)).fetchone()
    if row is None:
        return None
    if row[0] != fingerprint:
        raise Conflict("request content changed")
    return json.loads(row[1])


def receipt(db, operation, request_id, fingerprint, response):
    db.conn.execute("INSERT INTO receipts VALUES(?,?,?,?)",
                    (operation, request_id, fingerprint, canonical(response)))


class Database:
    def __init__(self, path, hook=None):
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.hook = hook
        self.conn.executescript(DDL)
        version = self.conn.execute(
            "SELECT value FROM metadata WHERE key='storage_version'").fetchone()[0]
        if version != "1":
            self.close()
            raise ValueError("unsupported storage version")

    def hit(self, boundary):
        if self.hook is not None:
            self.hook(boundary)

    @contextmanager
    def transaction(self, boundary="write"):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.hit(boundary + ".before_commit")
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()
