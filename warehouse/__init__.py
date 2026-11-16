"""Public normal warehouse API."""
from .db import Database
from .schemas import Schemas, Schema
from .archive import Archive
from .jobs import Jobs
from .results import Results
from .certified import Certificates, CertifiedJobs
from .errors import (SchemaError, DataError, ResolutionError, Conflict,
                     NotFound, NotReady)


class Warehouse:
    def __init__(self, path=":memory:", hook=None):
        self.db = Database(path, hook=hook)
        self.schemas = Schemas(self.db)
        self.archive = Archive(self.db)
        self.jobs = Jobs(self.db)
        self.results = Results(self.db)
        self.certificates = Certificates(self.db)
        self.certified_jobs = CertifiedJobs(self.db)

    def register_schema(self, document, request_id):
        return self.schemas.register_schema(document, request_id)

    def publish_batch(self, batch_id, schema_id, values, request_id):
        return self.archive.publish_batch(batch_id, schema_id, values, request_id)

    def create_job(self, job_id, batch_ids, reader_id, request_id):
        return self.jobs.create_job(job_id, batch_ids, reader_id, request_id)

    def certify(self, writer_id, reader_id):
        return self.certificates.certify(writer_id, reader_id)

    def get_certificate(self, writer_id, reader_id):
        return self.certificates.get_certificate(writer_id, reader_id)

    def create_certified_job(self, job_id, batch_ids, reader_id, request_id):
        return self.certified_jobs.create_certified_job(
            job_id, batch_ids, reader_id, request_id)

    def run_job(self, job_id):
        return self.jobs.run_job(job_id)

    def get_result(self, job_id):
        return self.results.get_result(job_id)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
