import copy
from warehouse.samples import WRITER, READER, VALUES
from .support import Case, branch


class RecursiveTest(Case):
    def test_alias_recursion_arrays_unions_and_defaults(self):
        result = self.run_values(WRITER, READER, copy.deepcopy(VALUES))
        expected = [
            {"id": 1, "value": branch("double", 0, 7.0), "children": [
                {"id": 2, "value": branch("double", 0, 7.0), "children": [],
                 "quality": branch("null", 0, None)},
                {"id": 3, "value": branch("null", 2, None), "children": [],
                 "quality": branch("null", 0, None)}], "quality": branch("null", 0, None)},
            {"id": 4, "value": branch("double", 0, 0.0), "children": [],
             "quality": branch("null", 0, None)}]
        self.assertEqual(expected, [r["value"] for r in result["rows"]])

    def test_recursive_name_environments_are_separate(self):
        reader = copy.deepcopy(READER)
        reader["fields"][1]["type"] = ["long", "null"]
        result = self.run_values(WRITER, reader, [copy.deepcopy(VALUES[0])])
        self.assertEqual("failed", result["state"])
        self.assertEqual("ResolutionError", result["error"]["code"])

    def test_writer_alias_is_not_a_reader_alias(self):
        writer = {"type": "record", "name": "New", "aliases": ["Old"], "fields": []}
        reader = {"type": "record", "name": "Old", "fields": []}
        self.assertEqual("failed", self.run_values(writer, reader, [{}])["state"])
