"""One terminal publication per job; values include reader union identity."""
import json
from .db import canonical
from .errors import NotFound, NotReady


class Results:
    def __init__(self, db):
        self.db = db

    def publish(self, job_id, values=None, error=None):
        # The caller owns the same transaction as the job state change.
        if not self.db.conn.in_transaction:
            raise RuntimeError("result publication requires a transaction")
        if error is None:
            self.db.conn.execute("INSERT INTO results VALUES(?,'done',NULL,NULL)", (job_id,))
            self.db.conn.executemany("INSERT INTO result_values VALUES(?,?,?,?,?)",
                [(job_id, i, batch_id, row_index, canonical(value))
                 for i, (batch_id, row_index, value) in enumerate(values)])
            state = "done"
        else:
            self.db.conn.execute("INSERT INTO results VALUES(?,'failed',?,?)",
                                 (job_id, error.code, str(error)))
            state = "failed"
        self.db.conn.execute("UPDATE jobs SET state=? WHERE job_id=?", (state, job_id))
        return self.get_result(job_id)

    def get_result(self, job_id):
        job = self.db.conn.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if job is None:
            raise NotFound("unknown job")
        if job[0] == "queued":
            raise NotReady("job has not completed")
        row = self.db.conn.execute(
            "SELECT outcome,error_code,error_message FROM results WHERE job_id=?",
            (job_id,)).fetchone()
        if row is None:
            raise RuntimeError("terminal job without result")
        if row[0] == "failed":
            return {"job_id": job_id, "state": "failed",
                    "error": {"code": row[1], "message": row[2]}, "rows": []}
        rows = [{"batch_id": batch, "row_index": index, "value": json.loads(value)}
                for batch, index, value in self.db.conn.execute(
                    "SELECT batch_id,row_index,value FROM result_values WHERE job_id=? ORDER BY ordinal",
                    (job_id,))]
        return {"job_id": job_id, "state": "done", "rows": rows}
