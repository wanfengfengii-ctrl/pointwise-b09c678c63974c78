from warehouse import Schema, SchemaError, Conflict
from .support import Case


class SchemasTest(Case):
    def test_versions_and_request_identity(self):
        a = {"type": "record", "name": "Row", "fields": []}
        first = self.schema(a, "a")
        self.assertEqual(first, self.schema(a, "a"))
        second = self.schema(a, "b")
        self.assertNotEqual(first, second)
        a["fields"].append({"name": "x", "type": "long"})
        self.assertEqual([], self.w.schemas.get(first)["fields"])
        with self.assertRaises(Conflict):
            self.schema(a, "a")

    def test_invalid_definitions_leave_no_rows(self):
        invalid = [
            [], ["long", "long"], ["long", ["null", "string"]], "Missing",
            {"type": "record", "name": "X", "fields": [
                {"name": "x", "type": "long"}, {"name": "x", "type": "string"}]},
            {"type": "record", "name": "X", "fields": [
                {"name": "v", "type": ["long", "null"], "default": None}]},
            {"type": "record", "name": "X", "fields": [
                {"name": "v", "type": {"type": "array", "items": ["long", "null"]},
                 "default": [None]}]},
            {"type": "record", "name": "X", "namespace": 0, "fields": []},
            {"type": "array"},
            {"type": "unknown"},
            {"type": []}
        ]
        for i, document in enumerate(invalid):
            with self.subTest(document=document), self.assertRaises(SchemaError):
                self.schema(document, str(i))
        self.assertEqual(0, self.w.db.conn.execute("SELECT count(*) FROM schemas").fetchone()[0])
        self.assertEqual(0, self.w.db.conn.execute("SELECT count(*) FROM receipts").fetchone()[0])

    def test_qualified_references_and_forward_definition(self):
        schema = Schema({"type": "record", "name": "ns.Root", "fields": [
            {"name": "a", "type": "Child"},
            {"name": "b", "type": {"type": "record", "name": "Child", "fields": [
                {"name": "parents", "type": {"type": "array", "items": "ns.Root"}}]}}]})
        root = schema.root
        self.assertIs(root.fields[0][1], root.fields[1][1])
        self.assertIs(root, root.fields[0][1].fields[0][1].items)
