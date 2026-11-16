"""Exact version-pair compatibility certificates and certified jobs."""
import json
from vendor import sdk1
from .db import canonical, claim, receipt
from .certification import analyze
from .schemas import Schema, Schemas
from .archive import Archive
from .legacy import resolve
from .errors import Conflict, NotFound, ResolutionError


class Certificates:
    def __init__(self, db):
        self.db = db

    def _documents(self, writer_id, reader_id):
        row_w = self.db.conn.execute(
            "SELECT document FROM schemas WHERE schema_id=?", (writer_id,)).fetchone()
        row_r = self.db.conn.execute(
            "SELECT document FROM schemas WHERE schema_id=?", (reader_id,)).fetchone()
        if row_w is None or row_r is None:
            raise NotFound("unknown schema")
        return json.loads(row_w[0]), json.loads(row_r[0])

    def get_certificate(self, writer_id, reader_id):
        # Existence is checked first so an unknown ID raises even though no
        # certificate row can reference it.
        self._documents(writer_id, reader_id)
        row = self.db.conn.execute(
            "SELECT safe,witness FROM certificates WHERE writer_id=? AND reader_id=?",
            (writer_id, reader_id)).fetchone()
        if row is None:
            return None
        return {"writer_id": writer_id, "reader_id": reader_id,
                "safe": bool(row[0]), "witness": json.loads(row[1])}

    def certify(self, writer_id, reader_id):
        writer_document, reader_document = self._documents(writer_id, reader_id)
        existing = self.get_certificate(writer_id, reader_id)
        if existing is not None:
            return existing
        writer_root = Schema(writer_document).root
        reader_root = Schema(reader_document).root
        safe, witness = analyze(writer_root, reader_root)
        if not safe:
            # Replay the witness through the untouched original codec and
            # resolver: it must really encode and really raise ResolutionError.
            payload = sdk1.dumps(writer_document, witness)
            try:
                resolve(writer_document, reader_document, payload)
            except ResolutionError:
                pass
            else:  # pragma: no cover - defensive, analysis must stay exact
                raise RuntimeError("compatibility analysis produced no failing witness")
        with self.db.transaction("certify"):
            row = self.db.conn.execute(
                "SELECT safe,witness FROM certificates WHERE writer_id=? AND reader_id=?",
                (writer_id, reader_id)).fetchone()
            if row is not None:
                safe = bool(row[0])
                witness = json.loads(row[1])
            else:
                self.db.conn.execute(
                    "INSERT INTO certificates VALUES(?,?,?,?)",
                    (writer_id, reader_id, 1 if safe else 0,
                     canonical(None if safe else witness)))
        return {"writer_id": writer_id, "reader_id": reader_id,
                "safe": safe, "witness": None if safe else witness}


class CertifiedJobs:
    """Fixed-reader job bindings that require safe exact-version certificates."""

    OPERATION = "certified_job"

    def __init__(self, db):
        self.db = db
        self.schemas = Schemas(db)
        self.archive = Archive(db)
        self.certificates = Certificates(db)

    def create_certified_job(self, job_id, batch_ids, reader_id, request_id):
        if (not isinstance(job_id, str) or not job_id
                or type(batch_ids) is not list
                or any(not isinstance(b, str) or not b for b in batch_ids)):
            raise ValueError("job id and ordered batch list required")
        fingerprint = canonical([job_id, batch_ids, reader_id])
        with self.db.transaction("enqueue"):
            old = claim(self.db, self.OPERATION, request_id, fingerprint)
            if old is not None:
                return old
            # All references must exist before any certificate decision.
            self.schemas.get(reader_id)
            writer_versions = []
            for batch_id in batch_ids:
                batch = self.archive.read_batch(batch_id)
                writer_versions.append(batch["schema_id"])
            # Every actual batch's fixed writer version needs a safe
            # certificate against exactly this reader version. Empty batches
            # still carry their writer identity and are checked as well; an
            # empty input list has no writer version to certify.
            for writer_id in writer_versions:
                certificate = self.certificates.get_certificate(writer_id, reader_id)
                if certificate is None or not certificate["safe"]:
                    raise ValueError(
                        "no safe certificate for writer version %d and reader %d"
                        % (writer_id, reader_id))
            if self.db.conn.execute(
                    "SELECT 1 FROM jobs WHERE job_id=?", (job_id,)).fetchone():
                raise Conflict("job id already exists")
            self.db.conn.execute("INSERT INTO jobs VALUES(?,?,?,'queued')",
                                 (job_id, reader_id, canonical(batch_ids)))
            receipt(self.db, self.OPERATION, request_id, fingerprint, job_id)
        return job_id
