import copy
from warehouse.samples import WRITER, READER, VALUES, seed
from .support import Case, branch


class EvolutionTest(Case):
    def test_same_name_new_version_does_not_rebind_queued_job(self):
        wid = self.schema(WRITER, "writer")
        rid = self.schema(READER, "reader")
        self.w.publish_batch("b", wid, copy.deepcopy(VALUES), "p")
        self.w.create_job("old", ["b"], rid, "old")
        newer = copy.deepcopy(READER)
        newer["fields"][1]["type"] = ["long", "double", "null"]
        rid2 = self.schema(newer, "reader2")
        self.w.create_job("new", ["b"], rid2, "new")
        old = self.w.run_job("old")
        new = self.w.run_job("new")
        self.assertEqual(branch("double", 0, 7.0), old["rows"][0]["value"]["value"])
        self.assertEqual(branch("long", 0, 7), new["rows"][0]["value"]["value"])
        self.assertEqual(branch("double", 1, 7.0),
                         new["rows"][0]["value"]["children"][0]["value"])
        self.assertEqual(rid, self.w.jobs.binding("old")["reader_id"])
        self.assertEqual(wid, self.w.archive.read_batch("b")["schema_id"])

    def test_full_seed_history_and_request_replay(self):
        ids = seed(self.w)
        self.assertEqual(ids, seed(self.w))
        self.assertEqual("queued", self.w.jobs.binding("pending")["state"])
        self.assertEqual(4, len(self.w.run_job("pending")["rows"]))
        self.assertEqual("failed", self.w.get_result("failed")["state"])
        self.assertEqual("sensor", self.w.archive.values("recent")[0]["source"])
        self.assertEqual(ids["reader"], self.w.jobs.binding("completed")["reader_id"])
