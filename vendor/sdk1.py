"""Offline SDK1 port. Its JSON wire stores positional union indices."""
import json
from warehouse.schemas import Schema
from warehouse.errors import DataError
from .codec import encode, decode, validate_wire


def dumps(document, value):
    root = Schema(document).root
    wire = encode(root, value, explicit=False)
    return json.dumps({"sdk": 1, "datum": wire}, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False)


def load_wire(root, payload):
    try:
        obj = json.loads(payload)
        if type(obj) is not dict or set(obj) != {"sdk", "datum"} or type(obj["sdk"]) is not int or obj["sdk"] != 1:
            raise DataError("not an SDK1 envelope")
        validate_wire(root, obj["datum"])
        return obj["datum"]
    except (ValueError, TypeError, KeyError) as exc:
        raise DataError("invalid SDK1 payload") from exc


def loads(document, payload):
    root = Schema(document).root
    return decode(root, load_wire(root, payload), explicit=False)
