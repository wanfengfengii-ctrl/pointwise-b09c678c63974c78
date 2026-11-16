"""Common same-schema validation; no writer-to-reader resolution."""
import math
from dataclasses import dataclass
from warehouse.errors import DataError


@dataclass(frozen=True)
class Union:
    branch: int
    value: object


def fail():
    raise DataError("value does not match schema")


def encode(node, value, explicit):
    kind = node.kind
    if kind == "union":
        if explicit:
            if not isinstance(value, Union) or type(value.branch) is not int:
                fail()
            i = value.branch
            if not 0 <= i < len(node.branches):
                fail()
            return [i, encode(node.branches[i], value.value, explicit)]
        # SDK1 has no public branch wrapper: first exactly validating branch wins.
        for i, branch in enumerate(node.branches):
            try:
                return [i, encode(branch, value, explicit)]
            except DataError:
                pass
        fail()
    if kind == "null":
        if value is not None:
            fail()
        return None
    if kind == "long":
        if type(value) is not int or not -(2 ** 63) <= value < 2 ** 63:
            fail()
        return value
    if kind == "double":
        # SDK1 deliberately distinguishes 7 from 7.0. SDK2's explicit branch
        # permits either numeric host value and normalizes it to a double.
        if type(value) not in ((int, float) if explicit else (float,)):
            fail()
        try:
            number = float(value)
        except (OverflowError, ValueError):
            fail()
        if not math.isfinite(number):
            fail()
        return number
    if kind == "string":
        if type(value) is not str:
            fail()
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            fail()
        return value
    if kind == "array":
        if type(value) is not list:
            fail()
        return [encode(node.items, v, explicit) for v in value]
    if kind == "record":
        if type(value) is not dict or set(value) != {f[0] for f in node.fields}:
            fail()
        return [encode(child, value[name], explicit) for name, child, _ in node.fields]
    fail()


def validate_wire(node, wire):
    kind = node.kind
    if kind == "union":
        if type(wire) is not list or len(wire) != 2 or type(wire[0]) is not int:
            fail()
        i, value = wire
        if not 0 <= i < len(node.branches):
            fail()
        validate_wire(node.branches[i], value)
    elif kind == "record":
        if type(wire) is not list or len(wire) != len(node.fields):
            fail()
        for (_, child, _), value in zip(node.fields, wire):
            validate_wire(child, value)
    elif kind == "array":
        if type(wire) is not list:
            fail()
        for value in wire:
            validate_wire(node.items, value)
    else:
        encode(node, wire, explicit=False)


def decode(node, wire, explicit):
    kind = node.kind
    if kind == "union":
        i, value = wire
        decoded = decode(node.branches[i], value, explicit)
        return Union(i, decoded) if explicit else decoded
    if kind == "record":
        return {name: decode(child, value, explicit)
                for (name, child, _), value in zip(node.fields, wire)}
    if kind == "array":
        return [decode(node.items, v, explicit) for v in wire]
    return wire


def default_wire(node, value):
    """Defaults select branch zero at every nested union."""
    if node.kind == "union":
        return [0, default_wire(node.branches[0], value)]
    if node.kind == "record":
        if type(value) is not dict or set(value) != {f[0] for f in node.fields}:
            fail()
        return [default_wire(child, value[name]) for name, child, _ in node.fields]
    if node.kind == "array":
        if type(value) is not list:
            fail()
        return [default_wire(node.items, v) for v in value]
    return encode(node, value, explicit=False)
