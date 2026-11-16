"""Disk-backed reopening and transaction boundaries; run in a clean writable copy."""
import tempfile
import unittest
from pathlib import Path
from warehouse import Warehouse, NotReady, NotFound
from warehouse.samples import seed


class StorageTest(unittest.TestCase):
    def test_reopen_history_receipts_bindings_and_results(self):
        with tempfile.TemporaryDirectory(prefix="warehouse-test-") as directory:
            path = str(Path(directory) / "warehouse.sqlite")
            with Warehouse(path) as w:
                ids = seed(w)
                before = w.get_result("completed")
                failed = w.get_result("failed")
            with Warehouse(path) as w:
                self.assertEqual(ids, seed(w))
                self.assertEqual(before, w.get_result("completed"))
                self.assertEqual(failed, w.get_result("failed"))
                self.assertEqual(ids["reader"], w.jobs.binding("pending")["reader_id"])
                self.assertEqual(4, len(w.run_job("pending")["rows"]))
            with Warehouse(path) as w:
                self.assertEqual("done", w.get_result("pending")["state"])

    def test_before_commit_failure_can_reopen_and_retry(self):
        with tempfile.TemporaryDirectory(prefix="warehouse-test-") as directory:
            path = str(Path(directory) / "warehouse.sqlite")
            with Warehouse(path) as w:
                sid = w.register_schema("long", "s")
                w.publish_batch("b", sid, [1], "p")
                w.create_job("j", ["b"], sid, "j")
                def hook(boundary):
                    if boundary == "run.before_commit":
                        raise OSError("commit refused")
                w.db.hook = hook
                with self.assertRaises(OSError):
                    w.run_job("j")
            with Warehouse(path) as w:
                with self.assertRaises(NotReady):
                    w.get_result("j")
                self.assertEqual("done", w.run_job("j")["state"])

    def test_schema_maintenance_transaction_rollback(self):
        with Warehouse() as w:
            def hook(boundary):
                if boundary == "write.before_commit":
                    raise OSError("registration refused")
            w.db.hook = hook
            with self.assertRaises(OSError):
                w.register_schema("long", "s")
            w.db.hook = None
            with self.assertRaises(NotFound):
                w.schemas.get(1)
            sid = w.register_schema("long", "s")
            with self.assertRaises(RuntimeError):
                with w.db.transaction("maintenance"):
                    w.db.conn.execute("UPDATE metadata SET value='broken'")
                    raise RuntimeError("maintenance aborted")
            self.assertEqual("1", w.db.conn.execute("SELECT value FROM metadata").fetchone()[0])
            self.assertEqual("long", w.schemas.get(sid))
