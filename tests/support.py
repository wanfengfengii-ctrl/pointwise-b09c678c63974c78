import unittest
from warehouse import Warehouse


class Case(unittest.TestCase):
    def setUp(self):
        self.w = Warehouse()

    def tearDown(self):
        self.w.close()

    def schema(self, document, request="schema"):
        return self.w.register_schema(document, request)

    def run_values(self, writer, reader, values):
        wid = self.schema(writer, "writer")
        rid = self.schema(reader, "reader")
        self.w.publish_batch("batch", wid, values, "publish")
        self.w.create_job("job", ["batch"], rid, "create")
        return self.w.run_job("job")


def branch(name, index, value):
    return {"branch": name, "index": index, "value": value}
