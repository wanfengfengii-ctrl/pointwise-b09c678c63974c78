"""Opt-in feature checks. Original tests remain in tests/ unchanged."""
import copy
import tempfile
import unittest
from pathlib import Path

from warehouse import Warehouse, NotFound, Conflict, ResolutionError
from warehouse.legacy import resolve
from vendor import sdk1
from certification_examples import cases, record


class CertificationTests(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()

    def tearDown(self):
        self.w.close()

    def pair(self, writer="long", reader="double", prefix="pair"):
        return (self.w.register_schema(writer, prefix + ".w"),
                self.w.register_schema(reader, prefix + ".r"))

    def absent_job(self, job):
        with self.assertRaises(NotFound):
            self.w.jobs.binding(job)

    def test_exact_examples_and_real_witnesses(self):
        for case in cases():
            with self.subTest(case=case["name"]), Warehouse() as w:
                wid = w.register_schema(case["writer"], "w")
                rid = w.register_schema(case["reader"], "r")
                self.assertIsNone(w.get_certificate(wid, rid))
                certificate = w.certify(wid, rid)
                self.assertEqual(
                    {"writer_id", "reader_id", "safe", "witness"}, set(certificate))
                self.assertEqual((wid, rid),
                                 (certificate["writer_id"], certificate["reader_id"]))
                self.assertIs(certificate["safe"], case["safe"])
                if certificate["safe"]:
                    self.assertIsNone(certificate["witness"])
                else:
                    payload = sdk1.dumps(case["writer"], certificate["witness"])
                    with self.assertRaises(ResolutionError):
                        resolve(case["writer"], case["reader"], payload)
                saved = copy.deepcopy(certificate)
                self.assertEqual(saved, w.get_certificate(wid, rid))
                self.assertEqual(saved, w.certify(wid, rid))
                certificate["writer_id"] = -1
                certificate["witness"] = {"changed": True}
                self.assertEqual(saved, w.get_certificate(wid, rid))

    def test_missing_certificate_retry_and_ordered_execution(self):
        wid, rid = self.pair()
        second = self.w.register_schema("double", "second")
        self.w.publish_batch("a", wid, [7], "a")
        self.w.publish_batch("b", second, [2.0], "b")
        self.w.certify(wid, rid)
        with self.assertRaises(ValueError):
            self.w.create_certified_job("j", ["a", "b", "a"], rid, "j")
        self.absent_job("j")
        self.w.certify(second, rid)
        self.assertEqual("j", self.w.create_certified_job(
            "j", ["a", "b", "a"], rid, "j"))
        result = self.w.run_job("j")
        self.assertEqual("done", result["state"])
        self.assertEqual([7.0, 2.0, 7.0], [r["value"] for r in result["rows"]])
        self.assertEqual(["a", "b", "a"], [r["batch_id"] for r in result["rows"]])
        self.assertEqual("j", self.w.create_certified_job(
            "j", ["a", "b", "a"], rid, "j"))
        with self.assertRaises(Conflict):
            self.w.create_certified_job("different", ["a"], rid, "j")
        with self.assertRaises(Conflict):
            self.w.create_certified_job("j", ["a"], rid, "another")

    def test_empty_batch_empty_inputs_and_ordinary_scope(self):
        wid, rid = self.pair("string", "double")
        self.w.publish_batch("empty", wid, [], "empty")
        self.w.create_job("ordinary", ["empty"], rid, "same-request")
        self.assertEqual("done", self.w.run_job("ordinary")["state"])
        with self.assertRaises(ValueError):
            self.w.create_certified_job(
                "guarded", ["empty"], rid, "same-request")
        self.absent_job("guarded")
        self.assertIs(self.w.certify(wid, rid)["safe"], False)
        with self.assertRaises(ValueError):
            self.w.create_certified_job(
                "guarded", ["empty"], rid, "same-request")
        self.absent_job("guarded")
        self.w.create_certified_job("no-inputs", [], rid, "no-inputs")
        self.assertEqual([], self.w.run_job("no-inputs")["rows"])

    def test_same_name_version_is_not_a_certificate(self):
        old = record("Row", [("x", "long")])
        new = record("Row", [("x", "string")])
        reader = record("Row", [("x", "double")])
        wid, rid = self.pair(old, reader)
        newer = self.w.register_schema(new, "new-version")
        self.w.certify(wid, rid)
        self.w.publish_batch("old", wid, [{"x": 1}], "old")
        self.w.publish_batch("new", newer, [], "new")
        self.w.create_certified_job("old-job", ["old"], rid, "old-job")
        with self.assertRaises(ValueError):
            self.w.create_certified_job("new-job", ["new"], rid, "new-job")
        self.assertIsNone(self.w.get_certificate(newer, rid))
        self.assertIs(self.w.certify(newer, rid)["safe"], False)
        with self.assertRaises(ValueError):
            self.w.create_certified_job("new-job", ["new"], rid, "new-job")
        self.assertEqual("done", self.w.run_job("old-job")["state"])

    def test_unknown_references_and_no_leftover_job(self):
        wid, rid = self.pair()
        for method in (self.w.certify, self.w.get_certificate):
            with self.assertRaises(NotFound):
                method(9999, rid)
            with self.assertRaises(NotFound):
                method(wid, 9999)
        self.w.publish_batch("a", wid, [], "a")
        with self.assertRaises(NotFound):
            self.w.create_certified_job("j", ["a", "missing"], rid, "j")
        self.absent_job("j")
        with self.assertRaises(NotFound):
            self.w.create_certified_job("j", [], 9999, "j")
        self.absent_job("j")
        self.w.certify(wid, rid)
        self.w.create_certified_job("j", ["a"], rid, "j")

    def test_certificate_and_enqueue_transaction_boundaries(self):
        wid, rid = self.pair()
        hits = []
        def refuse_certificate(boundary):
            hits.append(boundary)
            if boundary == "certify.before_commit":
                raise OSError("certificate publication refused")
        self.w.db.hook = refuse_certificate
        with self.assertRaises(OSError):
            self.w.certify(wid, rid)
        self.assertIn("certify.before_commit", hits)
        self.assertIsNone(self.w.get_certificate(wid, rid))
        self.w.db.hook = None
        self.w.certify(wid, rid)
        self.w.publish_batch("a", wid, [0], "a")
        def refuse_enqueue(boundary):
            if boundary == "enqueue.before_commit":
                raise OSError("job publication refused")
        self.w.db.hook = refuse_enqueue
        with self.assertRaises(OSError):
            self.w.create_certified_job("j", ["a"], rid, "j")
        self.absent_job("j")
        self.w.db.hook = None
        self.w.create_certified_job("j", ["a"], rid, "j")
        self.assertEqual("done", self.w.run_job("j")["state"])

    def test_reopen_preserves_certificate_binding_and_receipt(self):
        with tempfile.TemporaryDirectory(prefix="certification-test-") as directory:
            path = str(Path(directory) / "archive.sqlite")
            with Warehouse(path) as w:
                wid = w.register_schema("long", "w")
                rid = w.register_schema("double", "r")
                bad = w.register_schema("string", "bad")
                good_certificate = w.certify(wid, rid)
                bad_certificate = w.certify(bad, rid)
                w.publish_batch("data", wid, [4], "data")
                w.create_certified_job("j", ["data", "data"], rid, "j")
            with Warehouse(path) as w:
                self.assertEqual(good_certificate, w.get_certificate(wid, rid))
                self.assertEqual(bad_certificate, w.get_certificate(bad, rid))
                self.assertEqual(bad_certificate, w.certify(bad, rid))
                self.assertEqual(["data", "data"], w.jobs.binding("j")["batch_ids"])
                result = w.run_job("j")
                self.assertEqual("done", result["state"])
                self.assertEqual([4.0, 4.0], [r["value"] for r in result["rows"]])
            with Warehouse(path) as w:
                self.assertEqual(result, w.get_result("j"))
                self.assertEqual("j", w.create_certified_job(
                    "j", ["data", "data"], rid, "j"))


if __name__ == "__main__":
    unittest.main()
