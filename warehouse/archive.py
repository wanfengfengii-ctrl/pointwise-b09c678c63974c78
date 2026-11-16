"""Whole-batch validation and immutable writer-schema bindings."""
import json
from vendor import sdk1
from .schemas import Schemas
from .db import canonical, claim, receipt
from .errors import Conflict, NotFound, DataError


class Archive:
    def __init__(self, db):
        self.db = db
        self.schemas = Schemas(db)

    def publish_batch(self, batch_id, schema_id, values, request_id):
        if not isinstance(batch_id, str) or not batch_id or type(values) is not list:
            raise DataError("batch id and value list required")
        document = self.schemas.get(schema_id)
        payloads = [sdk1.dumps(document, value) for value in values]
        fingerprint = canonical([batch_id, schema_id, payloads])
        response = {"batch_id": batch_id, "schema_id": schema_id, "count": len(payloads)}
        with self.db.transaction("publish"):
            old = claim(self.db, "publish", request_id, fingerprint)
            if old is not None:
                return old
            if self.db.conn.execute("SELECT 1 FROM batches WHERE batch_id=?",
                                    (batch_id,)).fetchone():
                raise Conflict("batch id already exists")
            self.db.conn.execute("INSERT INTO batches VALUES(?,?,?)",
                                 (batch_id, schema_id, canonical(payloads)))
            receipt(self.db, "publish", request_id, fingerprint, response)
        return response

    def read_batch(self, batch_id):
        row = self.db.conn.execute("SELECT schema_id,payloads FROM batches WHERE batch_id=?",
                                   (batch_id,)).fetchone()
        if row is None:
            raise NotFound("unknown batch")
        return {"batch_id": batch_id, "schema_id": row[0], "payloads": json.loads(row[1])}

    def values(self, batch_id):
        batch = self.read_batch(batch_id)
        document = self.schemas.get(batch["schema_id"])
        return [sdk1.loads(document, p) for p in batch["payloads"]]
