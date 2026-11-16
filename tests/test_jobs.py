from warehouse import NotFound, NotReady
from .support import Case, branch


class JobsTest(Case):
    def test_defaults_promotion_and_field_order(self):
        writer = {"type": "record", "name": "Old", "fields": [
            {"name": "id", "type": "long"}, {"name": "discard", "type": "string"}]}
        reader = {"type": "record", "name": "New", "aliases": ["Old"], "fields": [
            {"name": "flags", "type": {"type": "array", "items": ["null", "long"]},
             "default": [None]},
            {"name": "id", "type": "double"},
            {"name": "note", "type": ["string", "null"], "default": "new"}]}
        result = self.run_values(writer, reader,
                                 [{"id": 0, "discard": "x"}, {"id": 4, "discard": "y"}])
        self.assertEqual("done", result["state"])
        expected = [{"flags": [branch("null", 0, None)], "id": float(i),
                     "note": branch("string", 0, "new")} for i in (0, 4)]
        self.assertEqual(expected, [r["value"] for r in result["rows"]])
        self.assertIs(type(result["rows"][0]["value"]["id"]), float)
        result["rows"][0]["value"]["flags"].append("mutation")
        self.assertEqual(expected, [r["value"] for r in self.w.get_result("job")["rows"]])
        self.assertEqual(self.w.get_result("job"), self.w.run_job("job"))

    def test_resolution_error_has_no_partial_rows(self):
        long = self.schema("long", "long")
        double = self.schema("double", "double")
        self.w.publish_batch("ok", long, [1, 2], "ok")
        self.w.publish_batch("bad", double, [7.0], "bad")
        self.w.create_job("j", ["ok", "bad"], long, "j")
        with self.assertRaises(NotReady):
            self.w.get_result("j")
        result = self.w.run_job("j")
        self.assertEqual("failed", result["state"])
        self.assertEqual("ResolutionError", result["error"]["code"])
        self.assertEqual([], result["rows"])
        self.assertEqual(0, self.w.db.conn.execute("SELECT count(*) FROM result_values").fetchone()[0])
        self.assertEqual(result, self.w.run_job("j"))

    def test_unknown_inputs_do_not_create_jobs(self):
        sid = self.schema("long")
        with self.assertRaises(NotFound):
            self.w.create_job("j", ["absent"], sid, "request")
        with self.assertRaises(NotFound):
            self.w.jobs.binding("j")
        with self.assertRaises(NotFound):
            self.w.get_result("absent")
        self.w.publish_batch("present", sid, [3], "p")
        self.assertEqual("j", self.w.create_job("j", ["present"], sid, "request"))

    def test_infrastructure_failure_is_retryable(self):
        sid = self.schema("long")
        self.w.publish_batch("b", sid, [1], "p")
        self.w.create_job("j", ["b"], sid, "j")
        for failpoint in ("run.after_resolve", "run.before_commit"):
            def hook(boundary):
                if boundary == failpoint:
                    raise OSError("infrastructure")
            self.w.db.hook = hook
            with self.assertRaises(OSError):
                self.w.run_job("j")
            self.assertEqual("queued", self.w.jobs.binding("j")["state"])
            self.assertEqual(0, self.w.db.conn.execute("SELECT count(*) FROM results").fetchone()[0])
        self.w.db.hook = None
        self.assertEqual("done", self.w.run_job("j")["state"])

    def test_reader_union_first_match_does_not_retry_failed_record(self):
        writer = {"type": "record", "name": "Source", "fields": []}
        reader = [
            {"type": "record", "name": "First", "aliases": ["Source"],
             "fields": [{"name": "required", "type": "long"}]},
            {"type": "record", "name": "Second", "aliases": ["Source"], "fields": []}]
        result = self.run_values(writer, reader, [{}])
        self.assertEqual("failed", result["state"])
        self.assertEqual("missing reader field: required", result["error"]["message"])
