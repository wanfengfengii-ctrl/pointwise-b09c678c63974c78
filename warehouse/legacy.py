"""SDK1-only writer-to-reader resolution of positional wire datums."""
from vendor import sdk1
from vendor.codec import default_wire
from .schemas import Schema, MISSING
from .errors import ResolutionError


def matches(writer, reader, seen=None):
    seen = set() if seen is None else seen
    pair = (id(writer), id(reader))
    if pair in seen:
        return True
    seen = seen | {pair}
    if writer.kind == "union":
        return any(matches(w, reader, seen) for w in writer.branches)
    if reader.kind == "union":
        return any(matches(writer, r, seen) for r in reader.branches)
    if writer.kind == reader.kind:
        if writer.kind == "record":
            return writer.name == reader.name or writer.name in reader.aliases
        if writer.kind == "array":
            return matches(writer.items, reader.items, seen)
        return True
    return writer.kind == "long" and reader.kind == "double"


def _default(node, wire):
    if node.kind == "union":
        i, value = wire
        return {"branch": node.branches[i].label, "index": i,
                "value": _default(node.branches[i], value)}
    if node.kind == "record":
        return {name: _default(child, value)
                for (name, child, _), value in zip(node.fields, wire)}
    if node.kind == "array":
        return [_default(node.items, v) for v in wire]
    return wire


def _resolve(writer, reader, wire):
    if writer.kind == "union":
        index, value = wire
        return _resolve(writer.branches[index], reader, value)
    if reader.kind == "union":
        for index, branch in enumerate(reader.branches):
            if matches(writer, branch):
                return {"branch": branch.label, "index": index,
                        "value": _resolve(writer, branch, wire)}
        raise ResolutionError("no matching reader union branch")
    if not matches(writer, reader):
        raise ResolutionError("incompatible writer and reader")
    if writer.kind == "record":
        source = {name: (child, value)
                  for (name, child, _), value in zip(writer.fields, wire)}
        output = {}
        for name, child, default in reader.fields:
            if name in source:
                wchild, value = source[name]
                output[name] = _resolve(wchild, child, value)
            elif default is not MISSING:
                output[name] = _default(child, default_wire(child, default))
            else:
                raise ResolutionError("missing reader field: " + name)
        return output
    if writer.kind == "array":
        return [_resolve(writer.items, reader.items, v) for v in wire]
    if writer.kind == "long" and reader.kind == "double":
        return float(wire)
    return wire


def resolve(writer_document, reader_document, payload):
    writer = Schema(writer_document).root
    reader = Schema(reader_document).root
    wire = sdk1.load_wire(writer, payload)
    return _resolve(writer, reader, wire)
