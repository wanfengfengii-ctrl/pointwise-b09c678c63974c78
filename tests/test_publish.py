from warehouse import Conflict, DataError, NotFound
from .support import Case


class PublishTest(Case):
    def test_null_zero_and_replay(self):
        doc = {"type": "record", "name": "Row", "fields": [
            {"name": "n", "type": ["null", "long"]}]}
        sid = self.schema(doc)
        values = [{"n": None}, {"n": 0}, {"n": -3}]
        receipt = self.w.publish_batch("b", sid, values, "request")
        self.assertEqual({"batch_id": "b", "schema_id": sid, "count": 3}, receipt)
        self.assertEqual(receipt, self.w.publish_batch("b", sid, values, "request"))
        values[1]["n"] = 9
        with self.assertRaises(Conflict):
            self.w.publish_batch("b", sid, values, "request")
        with self.assertRaises(Conflict):
            self.w.publish_batch("b", sid, [{"n": 1}], "other")
        self.assertEqual([{"n": None}, {"n": 0}, {"n": -3}], self.w.archive.values("b"))
        self.assertEqual(sid, self.w.archive.read_batch("b")["schema_id"])

    def test_bad_last_value_is_not_a_batch(self):
        sid = self.schema("long")
        for values in ([1, 2, "bad"], [1, True], [2 ** 63], [-2 ** 63 - 1]):
            with self.subTest(values=values), self.assertRaises(DataError):
                self.w.publish_batch("bad", sid, values, "bad")
        with self.assertRaises(NotFound):
            self.w.archive.read_batch("bad")
        self.assertEqual(0, self.w.db.conn.execute(
            "SELECT count(*) FROM receipts WHERE operation='publish'").fetchone()[0])

    def test_empty_and_duplicate_batch_references(self):
        sid = self.schema("long")
        self.w.publish_batch("empty", sid, [], "empty")
        self.w.publish_batch("data", sid, [1, 1], "data")
        self.w.create_job("j", ["empty", "data", "data"], sid, "j")
        result = self.w.run_job("j")
        self.assertEqual([1, 1, 1, 1], [r["value"] for r in result["rows"]])
        self.assertEqual([0, 1, 0, 1], [r["row_index"] for r in result["rows"]])

    def test_publish_failure_rolls_back_receipt_and_batch(self):
        sid = self.schema("long")
        def hook(boundary):
            if boundary == "publish.before_commit":
                raise OSError("disk boundary")
        self.w.db.hook = hook
        with self.assertRaises(OSError):
            self.w.publish_batch("b", sid, [1], "p")
        self.w.db.hook = None
        with self.assertRaises(NotFound):
            self.w.archive.read_batch("b")
        self.assertEqual(1, self.w.publish_batch("b", sid, [1], "p")["count"])
