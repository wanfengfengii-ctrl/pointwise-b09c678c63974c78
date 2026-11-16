"""Offline SDK2 port: explicit union values and named record wire fields.

This port only reads/writes one schema. It does not resolve different schemas.
"""
import json
from warehouse.schemas import Schema
from warehouse.errors import DataError
from .codec import Union, encode, decode, validate_wire


def _target(node, wire):
    if node.kind == "union":
        i, value = wire
        return {"branch": node.branches[i].label, "value": _target(node.branches[i], value)}
    if node.kind == "record":
        return {"record": node.name, "fields": {
            name: _target(child, value)
            for (name, child, _), value in zip(node.fields, wire)}}
    if node.kind == "array":
        return [_target(node.items, v) for v in wire]
    return wire


def _wire(node, obj):
    if node.kind == "union":
        if type(obj) is not dict or set(obj) != {"branch", "value"}:
            raise DataError("invalid explicit union")
        for i, branch in enumerate(node.branches):
            if obj["branch"] == branch.label:
                return [i, _wire(branch, obj["value"])]
        raise DataError("unknown union branch")
    if node.kind == "record":
        if type(obj) is not dict or set(obj) != {"record", "fields"} or obj["record"] != node.name:
            raise DataError("invalid named record")
        fields = obj["fields"]
        if type(fields) is not dict or set(fields) != {f[0] for f in node.fields}:
            raise DataError("invalid record fields")
        return [_wire(child, fields[name]) for name, child, _ in node.fields]
    if node.kind == "array":
        if type(obj) is not list:
            raise DataError("invalid array")
        return [_wire(node.items, v) for v in obj]
    return obj


def dumps(document, value):
    root = Schema(document).root
    wire = encode(root, value, explicit=True)
    return json.dumps({"sdk": 2, "value": _target(root, wire)},
                      ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def loads(document, payload):
    root = Schema(document).root
    try:
        obj = json.loads(payload)
        if type(obj) is not dict or set(obj) != {"sdk", "value"} or type(obj["sdk"]) is not int or obj["sdk"] != 2:
            raise DataError("not an SDK2 envelope")
        wire = _wire(root, obj["value"])
        validate_wire(root, wire)
        return decode(root, wire, explicit=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise DataError("invalid SDK2 payload") from exc
