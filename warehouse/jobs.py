"""Fixed-reader jobs over an immutable ordered batch list."""
import json
from .db import canonical, claim, receipt
from .schemas import Schemas
from .archive import Archive
from .results import Results
from .legacy import resolve
from .errors import Conflict, NotFound, ResolutionError


class Jobs:
    def __init__(self, db):
        self.db = db
        self.schemas = Schemas(db)
        self.archive = Archive(db)
        self.results = Results(db)

    def create_job(self, job_id, batch_ids, reader_id, request_id):
        if (not isinstance(job_id, str) or not job_id or type(batch_ids) is not list
                or any(not isinstance(b, str) or not b for b in batch_ids)):
            raise ValueError("job id and ordered batch list required")
        fingerprint = canonical([job_id, batch_ids, reader_id])
        with self.db.transaction("enqueue"):
            old = claim(self.db, "job", request_id, fingerprint)
            if old is not None:
                return old
            self.schemas.get(reader_id)
            for batch_id in batch_ids:
                self.archive.read_batch(batch_id)
            if self.db.conn.execute("SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
                raise Conflict("job id already exists")
            self.db.conn.execute("INSERT INTO jobs VALUES(?,?,?,'queued')",
                                 (job_id, reader_id, canonical(batch_ids)))
            receipt(self.db, "job", request_id, fingerprint, job_id)
        return job_id

    def binding(self, job_id):
        row = self.db.conn.execute(
            "SELECT reader_id,inputs,state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFound("unknown job")
        return {"reader_id": row[0], "batch_ids": json.loads(row[1]), "state": row[2]}

    def run_job(self, job_id):
        with self.db.transaction("run"):
            binding = self.binding(job_id)
            if binding["state"] != "queued":
                return self.results.get_result(job_id)
            reader = self.schemas.get(binding["reader_id"])
            values = []
            try:
                for batch_id in binding["batch_ids"]:
                    batch = self.archive.read_batch(batch_id)
                    writer = self.schemas.get(batch["schema_id"])
                    for index, payload in enumerate(batch["payloads"]):
                        value = resolve(writer, reader, payload)
                        values.append((batch_id, index, value))
                self.db.hit("run.after_resolve")
            except ResolutionError as error:
                return self.results.publish(job_id, error=error)
            return self.results.publish(job_id, values=values)
