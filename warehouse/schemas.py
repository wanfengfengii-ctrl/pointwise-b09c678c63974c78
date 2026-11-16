"""Immutable schema documents and per-document named environments."""
import re
from dataclasses import dataclass, field
from .errors import SchemaError, Conflict, NotFound

PRIMITIVES = {"null", "long", "double", "string"}
NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")
MISSING = object()


@dataclass(eq=False)
class Node:
    kind: str
    name: str = ""
    aliases: tuple = ()
    fields: list = field(default_factory=list)
    items: object = None
    branches: list = field(default_factory=list)

    @property
    def label(self):
        return self.name if self.kind == "record" else self.kind


def full(name, namespace):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise SchemaError("invalid name")
    return name if "." in name or not namespace else namespace + "." + name


class Schema:
    def __init__(self, document):
        self.names = {}
        self.definitions = {}
        self.defaults = []
        self._declare(document, "")
        self.root = self._build(document, "")
        # All record nodes are filled before validating recursive defaults.
        from vendor.codec import default_wire
        from .errors import DataError
        for node, value in self.defaults:
            try:
                default_wire(node, value)
            except (DataError, RecursionError) as exc:
                raise SchemaError("invalid field default") from exc

    def _declare(self, spec, namespace):
        if isinstance(spec, str):
            return
        if isinstance(spec, list):
            for branch in spec:
                self._declare(branch, namespace)
            return
        if not isinstance(spec, dict):
            raise SchemaError("schema must be a name, object or union")
        kind = spec.get("type")
        if not isinstance(kind, str):
            raise SchemaError("type must be a string")
        if kind == "record":
            ns = spec.get("namespace", namespace)
            if not isinstance(ns, str) or (ns and not NAME.fullmatch(ns)):
                raise SchemaError("invalid namespace")
            name = full(spec.get("name"), ns)
            if name in self.names:
                raise SchemaError("duplicate record name")
            aliases = spec.get("aliases", [])
            if not isinstance(aliases, list):
                raise SchemaError("aliases must be a list")
            aliases = tuple(full(a, name.rpartition(".")[0]) for a in aliases)
            if len(set(aliases)) != len(aliases):
                raise SchemaError("duplicate alias")
            node = Node("record", name, aliases)
            self.names[name] = node
            self.definitions[id(spec)] = node
            fields = spec.get("fields")
            if not isinstance(fields, list):
                raise SchemaError("record fields must be a list")
            seen = set()
            for f in fields:
                if not isinstance(f, dict) or not isinstance(f.get("name"), str):
                    raise SchemaError("invalid field")
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", f["name"]):
                    raise SchemaError("invalid field name")
                if f["name"] in seen or "type" not in f:
                    raise SchemaError("duplicate or untyped field")
                seen.add(f["name"])
                self._declare(f["type"], name.rpartition(".")[0])
        elif kind == "array":
            if "items" not in spec:
                raise SchemaError("missing array items")
            self._declare(spec["items"], namespace)
        elif kind not in PRIMITIVES:
            raise SchemaError("unknown schema object")

    def _build(self, spec, namespace):
        if isinstance(spec, str):
            if spec in PRIMITIVES:
                return Node(spec)
            name = full(spec, namespace)
            if name not in self.names:
                raise SchemaError("unknown reference: " + name)
            return self.names[name]
        if isinstance(spec, list):
            if not spec:
                raise SchemaError("empty union")
            branches = [self._build(b, namespace) for b in spec]
            labels = [b.label for b in branches]
            if any(b.kind == "union" for b in branches) or len(set(labels)) != len(labels):
                raise SchemaError("nested or duplicate union branch")
            return Node("union", branches=branches)
        kind = spec["type"]
        if kind == "record":
            node = self.definitions[id(spec)]
            ns = node.name.rpartition(".")[0]
            for f in spec["fields"]:
                child = self._build(f["type"], ns)
                default = f.get("default", MISSING)
                node.fields.append((f["name"], child, default))
                if default is not MISSING:
                    self.defaults.append((child, default))
            return node
        if kind == "array":
            return Node("array", items=self._build(spec["items"], namespace))
        return Node(kind)


class Schemas:
    def __init__(self, db):
        self.db = db

    def register_schema(self, document, request_id):
        from .db import canonical, claim, receipt
        raw = canonical(document)
        Schema(document)
        fingerprint = canonical(document)
        with self.db.transaction():
            old = claim(self.db, "schema", request_id, fingerprint)
            if old is not None:
                return old
            cur = self.db.conn.execute("INSERT INTO schemas(document) VALUES(?)", (raw,))
            schema_id = cur.lastrowid
            receipt(self.db, "schema", request_id, fingerprint, schema_id)
            return schema_id

    def get(self, schema_id):
        import json
        row = self.db.conn.execute("SELECT document FROM schemas WHERE schema_id=?",
                                   (schema_id,)).fetchone()
        if row is None:
            raise NotFound("unknown schema")
        return json.loads(row[0])

    def compiled(self, schema_id):
        return Schema(self.get(schema_id))
