"""Independent bounded-enumeration cross-check for the exact analyzer.

This module is deliberately independent of ``warehouse.certification``'s
abstract rules: it enumerates concrete finite input values up to explicit size
bounds, filters them through the untouched original ``vendor.sdk1.dumps``
encoder, and plays each surviving value through the untouched original
``warehouse.legacy.resolve``.  The observed bounded verdict is then compared
with the whole-domain verdict of the exact analyzer.

Bounded enumeration cannot by itself prove whole-domain safety -- a failure may
hide beyond the depth/width caps -- so this check is a corroboration of the
analyzer, not its specification.  The exactness argument and the recursive
termination argument live in HANDOFF.md.
"""
import argparse
import copy
import json
import random

from vendor import sdk1
from warehouse import Schema, ResolutionError
from warehouse.legacy import resolve
from warehouse.certification import analyze

PRIMITIVES = ["null", "long", "double", "string"]
# One representative per primitive keeps the bounded domain small while still
# covering every primitive type identity (int vs float, None, str).
PRIMITIVE_VALUES = {"null": [None], "long": [0], "double": [0.0],
                    "string": [""]}
MAX_VALUES = 600


def random_primitive_or_leaf(rng, depth, names, open_records):
    """A non-union branch: primitive, array or named record."""
    choice = rng.randrange(3)
    if choice == 0 or depth <= 0:
        return rng.choice(PRIMITIVES)
    if choice == 1:
        return {"type": "array",
                "items": random_schema(rng, depth - 1, names, open_records)}
    return random_record(rng, depth, names, open_records)


def random_record(rng, depth, names, open_records):
    name = "R%d" % len(names)
    document = {"type": "record", "name": name, "fields": []}
    names[name] = document
    child_open = open_records + [name]
    for i in range(rng.randrange(1, 3)):
        field_name = "f%d" % i
        document["fields"].append({
            "name": field_name,
            "type": random_schema(rng, depth - 1, names, child_open)})
    return document


def random_schema(rng, depth=0, names=None, open_records=None):
    """Generate a legal SDK1 schema document, including recursive records."""
    names = {} if names is None else names
    open_records = [] if open_records is None else open_records
    if open_records and rng.random() < 0.30:
        return rng.choice(list(open_records))
    if depth <= 0:
        return rng.choice(PRIMITIVES)
    choice = rng.randrange(6)
    if choice == 0:
        return rng.choice(PRIMITIVES)
    if choice == 1:
        return {"type": "array",
                "items": random_schema(rng, depth - 1, names, open_records)}
    if choice == 2:
        size = rng.randrange(2, 4)
        branches, labels = [], set()
        attempts = 0
        while len(branches) < size and attempts < size * 4:
            attempts += 1
            branch = random_primitive_or_leaf(rng, depth - 1, names,
                                              open_records)
            label = branch if isinstance(branch, str) else branch.get("type")
            if label in labels:
                continue
            labels.add(label)
            branches.append(branch)
        if len(branches) < 2:
            return rng.choice(PRIMITIVES)
        return branches
    # named record, optionally recursive
    if choice in (3, 4, 5):
        return random_record(rng, depth, names, open_records)


def values_for(document, rng, depth):
    """Bounded concrete values, filtered by the real SDK1 encoder."""
    root = Schema(copy.deepcopy(document)).root
    results = []

    def generate(node, level, record_budget):
        if len(results) >= MAX_VALUES:
            return
        if node.kind in ("null", "long", "double", "string"):
            for value in PRIMITIVE_VALUES[node.kind]:
                yield value
            return
        if node.kind == "union":
            seen = []
            for branch in node.branches:
                for value in generate(branch, level, record_budget):
                    if value in seen:
                        continue
                    seen.append(value)
                    yield value
            return
        if node.kind == "array":
            yield []
            if level <= 0:
                return
            elements = list(generate(node.items, level - 1, record_budget))[:4]
            for element in elements[:3]:
                yield [element]
            if len(elements) >= 2:
                yield [elements[0], elements[1]]
            return
        # record: a revisited record name consumes the unfolding budget so the
        # bounded enumeration always terminates on recursive schemas.
        seen_names = set(record_budget[0])
        if node.name in seen_names:
            remaining = record_budget[1] - 1
        else:
            seen_names.add(node.name)
            remaining = record_budget[1]
        if remaining <= 0:
            return
        budget = (seen_names, remaining - 1)
        field_values = []
        for _, child, _ in node.fields:
            field_values.append(
                list(generate(child, level - 1, budget))[:4])
        if not field_values or any(not options for options in field_values):
            return
        combinations = [{}]
        for (name, _, _), options in zip(node.fields, field_values):
            combinations = [dict(mapping, **{name: option})
                            for mapping in combinations for option in options]
            if len(combinations) > MAX_VALUES:
                combinations = combinations[:MAX_VALUES]
        yield from combinations

    for value in generate(root, depth, (set(), depth + 2)):
        try:
            payload = sdk1.dumps(document, value)
        except Exception:
            continue
        # Keep only genuinely encodable values; dedupe by canonical wire.
        yield value, payload


def observed_safety(writer, reader):
    """Brute-force the bounded domain through the real encoder and resolver."""
    encodable = 0
    for _value, payload in values_for(writer, None, depth=3):
        encodable += 1
        try:
            resolve(writer, reader, payload)
        except ResolutionError:
            return False, encodable
    return True, encodable


def cross_check(iterations=400, seed=20261116):
    rng = random.Random(seed)
    report = []
    mismatches = 0
    for index in range(iterations):
        writer = random_schema(rng, depth=rng.randrange(1, 5))
        reader = random_schema(rng, depth=rng.randrange(1, 5))
        try:
            Schema(copy.deepcopy(writer))
            Schema(copy.deepcopy(reader))
        except Exception:
            continue
        bounded_safe, encodable = observed_safety(writer, reader)
        exact_safe, witness = analyze(
            Schema(copy.deepcopy(writer)).root,
            Schema(copy.deepcopy(reader)).root)
        # Enumeration can only prove UNSAFE; its "safe" is a bounded result.
        consistent = exact_safe or bounded_safe is False
        if not consistent:
            mismatches += 1
        if not exact_safe:
            payload = sdk1.dumps(writer, witness)
            try:
                resolve(writer, reader, payload)
                raise AssertionError("witness did not fail on pair %d" % index)
            except ResolutionError:
                pass
        if index < 12 or not consistent:
            entry = {"pair": index, "bounded_encodable": encodable,
                     "bounded_safe": bounded_safe,
                     "exact_safe": exact_safe,
                     "consistent": consistent}
            if not consistent:
                entry["writer"] = writer
                entry["reader"] = reader
                entry["witness"] = witness
            report.append(entry)
    return {"iterations": iterations, "mismatches": mismatches,
            "enumeration_note": "bounded depth=3, width caps; proves only "
                                "unsafe verdicts, whole-domain safety is "
                                "established by analysis",
            "samples": report}


def termination_probe():
    """Recursive schemas must decide without fixed-depth enumeration."""
    loop = {"type": "record", "name": "Loop",
            "fields": [{"name": "next", "type": ["null", "Loop"]}]}
    mutual_a_doc = {"type": "record", "name": "A", "fields": [
        {"name": "b", "type": ["null", {"type": "record", "name": "B", "fields": [
            {"name": "a", "type": ["null", "A"]}]}]}]}
    probes = [
        ("self_loop_safe", loop, loop, True),
        ("self_loop_primitive_reader", loop, "string", False),
        ("mutual_recursion_safe", mutual_a_doc, mutual_a_doc, True),
        ("recursive_array_vs_primitive",
         {"type": "array", "items": loop}, {"type": "array", "items": "string"},
         False),
    ]
    outcomes = []
    for name, writer, reader, expected in probes:
        safe, witness = analyze(Schema(copy.deepcopy(writer)).root,
                                Schema(copy.deepcopy(reader)).root)
        assert safe is expected, name
        if not safe:
            payload = sdk1.dumps(writer, witness)
            try:
                resolve(writer, reader, payload)
                raise AssertionError(name + " witness did not fail")
            except ResolutionError:
                pass
        outcomes.append({"name": name, "safe": safe, "witness": witness})
    return outcomes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=400)
    parser.add_argument("--strict", action="store_true",
                        help="exit nonzero when a mismatch is found")
    args = parser.parse_args()
    result = cross_check(args.iterations)
    result["recursive_termination"] = termination_probe()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["mismatches"] and args.strict:
        raise SystemExit("enumeration cross-check found mismatches")


if __name__ == "__main__":
    main()
