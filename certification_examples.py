"""Public finite fixtures and real SDK1 probes; no compatibility analyzer."""
import argparse
import copy
import json
import tempfile
from pathlib import Path

from vendor import sdk1
from warehouse import Warehouse, Schema, ResolutionError
from warehouse.legacy import resolve
from warehouse.samples import WRITER, READER, VALUES


def record(name, fields, aliases=None):
    value = {"type": "record", "name": name,
             "fields": [{"name": key, "type": child} for key, child in fields]}
    if aliases is not None:
        value["aliases"] = aliases
    return value


def array(items):
    return {"type": "array", "items": items}


def cases():
    """Each probe is (input_value, expected_resolution_success)."""
    a = record("A", [("x", "long")])
    b = record("B", [("x", "long")])
    partial = record("B", [("x", ["long", "string"])])
    bad = record("Bad", [("y", "long")], ["A"])
    aa = record("AA", [("xs", array("long"))])
    bb = record("BB", [("xs", array("string"))])
    cc = record("CC", [("xs", array(["long", "string"]))])
    fa = record("FA", [("x", "long"), ("y", ["long", "string"])])
    fb = record("FB", [("x", ["long", "string"]), ("y", "long")])
    fc = record("FC", [("x", ["long", "string"]), ("y", ["long", "string"])])
    loop = record("Loop", [("next", "Loop")])
    item_reader = record("Loop", [("required", "long")])
    return copy.deepcopy([
        {"name": "promotion", "writer": "long", "reader": "double",
         "safe": True, "probes": [(0, True), (-2**63, True), (2**63-1, True)]},
        {"name": "primitive_mismatch", "writer": "long", "reader": "string",
         "safe": False, "probes": [(0, False)]},
        {"name": "null_witness", "writer": "null", "reader": "long",
         "safe": False, "probes": [(None, False)]},
        {"name": "fully_shadowed", "writer": [a, b], "reader": a,
         "safe": True, "probes": [({"x": 0}, True)]},
        {"name": "partly_shadowed", "writer": [a, partial], "reader": a,
         "safe": False, "probes": [({"x": 0}, True), ({"x": ""}, False)]},
        {"name": "reader_first_failure", "writer": a, "reader": [bad, a],
         "safe": False, "probes": [({"x": 1}, False)]},
        {"name": "array_mixture", "writer": [aa, bb, cc], "reader": [aa, bb],
         "safe": False, "probes": [
             ({"xs": []}, True), ({"xs": [0]}, True),
             ({"xs": [""]}, True), ({"xs": [0, ""]}, False)]},
        {"name": "field_correlation", "writer": [fa, fb, fc], "reader": [fa, fb],
         "safe": False, "probes": [
             ({"x": 0, "y": 0}, True), ({"x": "", "y": 0}, True),
             ({"x": 0, "y": ""}, True), ({"x": "", "y": ""}, False)]},
        {"name": "no_finite_value", "writer": loop, "reader": "string",
         "safe": True, "probes": []},
        {"name": "recursive_defaults", "writer": WRITER, "reader": READER,
         "safe": True, "probes": [(value, True) for value in VALUES]},
        {"name": "empty_array_mismatch", "writer": array("long"),
         "reader": array("string"), "safe": False, "probes": [([], False)]},
        {"name": "empty_only_array", "writer": array(loop),
         "reader": array(item_reader), "safe": True, "probes": [([], True)]},
        {"name": "empty_only_array_mismatch", "writer": array(loop),
         "reader": array("string"), "safe": False, "probes": [([], False)]},
        {"name": "unproductive_sibling",
         "writer": record("Root", [("ok", "long"), ("dead", loop)]),
         "reader": "string", "safe": True, "probes": []},
    ])


def check_source():
    events = []
    for case in cases():
        Schema(case["writer"])
        Schema(case["reader"])
        outcomes = []
        for value, expected in case["probes"]:
            payload = sdk1.dumps(case["writer"], value)
            try:
                resolved = resolve(case["writer"], case["reader"], payload)
                success, detail = True, resolved
            except ResolutionError as error:
                success, detail = False, str(error)
            assert success is expected, case["name"]
            outcomes.append({"input": value, "wire": json.loads(payload)["datum"],
                             "success": success, "result": detail})
        events.append({"case": case["name"], "probes": outcomes})
    node = Schema(record("Loop", [("next", "Loop")])).root
    assert node.fields[0][1] is node
    print(json.dumps({"source_probes": events}, ensure_ascii=True, indent=2))


def demo():
    with tempfile.TemporaryDirectory(prefix="certification-demo-") as directory:
        path = str(Path(directory) / "archive.sqlite")
        with Warehouse(path) as w:
            writer = w.register_schema("long", "writer")
            reader = w.register_schema("double", "reader")
            bad = w.register_schema("string", "bad-writer")
            safe = w.certify(writer, reader)
            unsafe = w.certify(bad, reader)
            assert safe["safe"] is True and unsafe["safe"] is False
            sdk1.dumps("string", unsafe["witness"])
            try:
                resolve("string", "double", sdk1.dumps("string", unsafe["witness"]))
            except ResolutionError:
                pass
            else:
                raise AssertionError("unsafe witness did not fail")
            w.publish_batch("data", writer, [7, -2], "publish-data")
            w.publish_batch("empty-bad", bad, [], "publish-bad")
            try:
                w.create_certified_job("rejected", ["empty-bad"], reader, "reject")
            except ValueError:
                rejection = "unsafe writer version rejected even for empty batch"
            else:
                raise AssertionError("unsafe empty batch admitted")
            w.create_certified_job("checked", ["data", "data"], reader, "checked")
        with Warehouse(path) as w:
            assert w.get_certificate(writer, reader) == safe
            result = w.run_job("checked")
            assert result["state"] == "done"
            assert [r["value"] for r in result["rows"]] == [7.0, -2.0, 7.0, -2.0]
            assert w.create_certified_job(
                "checked", ["data", "data"], reader, "checked") == "checked"
            print(json.dumps({"safe": safe, "unsafe": unsafe, "rejection": rejection,
                              "binding": w.jobs.binding("checked"),
                              "reopened_result": result}, ensure_ascii=True, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check-source", action="store_true")
    mode.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if args.check_source:
        check_source()
    else:
        demo()


if __name__ == "__main__":
    main()
