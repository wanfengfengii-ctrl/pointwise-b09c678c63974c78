import json
import unittest
from vendor import sdk1, sdk2
from vendor.codec import Union
from warehouse import DataError
from warehouse.samples import PORT_EVENTS


class PortsTest(unittest.TestCase):
    def test_frozen_sdk1_events_and_exact_number_identity(self):
        for event in PORT_EVENTS:
            encoded = sdk1.dumps(event["schema"], event["input"])
            self.assertEqual(event["sdk1_wire"], json.loads(encoded))
            decoded = sdk1.loads(event["schema"], encoded)
            self.assertIs(type(decoded), type(event["input"]))
            self.assertEqual(event["input"], decoded)

    def test_sdk2_explicit_native_branch_accepts_integral_double(self):
        schema = ["long", "double"]
        payload = sdk2.dumps(schema, Union(1, 7))
        self.assertEqual({"sdk": 2, "value": {"branch": "double", "value": 7.0}},
                         json.loads(payload))
        result = sdk2.loads(schema, payload)
        self.assertEqual(Union(1, 7.0), result)
        self.assertIs(type(result.value), float)
        with self.assertRaises(DataError):
            sdk2.dumps(schema, 7)
        with self.assertRaises(DataError):
            sdk1.loads(schema, payload)
        with self.assertRaises(DataError):
            sdk2.loads(schema, sdk1.dumps(schema, 7))

    def test_same_shape_records_can_have_distinct_explicit_identities(self):
        schema = [{"type": "record", "name": name, "fields": [
            {"name": "n", "type": "long"}]} for name in ("A", "B")]
        native = sdk2.dumps(schema, Union(1, {"n": 7}))
        self.assertEqual("B", json.loads(native)["value"]["branch"])
        self.assertEqual(Union(1, {"n": 7}), sdk2.loads(schema, native))
        self.assertEqual(0, json.loads(sdk1.dumps(schema, {"n": 7}))["datum"][0])

    def test_sdk2_recursive_roundtrip(self):
        schema = {"type": "record", "name": "Node", "fields": [
            {"name": "v", "type": ["null", "long"]},
            {"name": "children", "type": {"type": "array", "items": "Node"}}]}
        value = {"v": Union(1, 0), "children": [{"v": Union(0, None), "children": []}]}
        self.assertEqual(value, sdk2.loads(schema, sdk2.dumps(schema, value)))

    def test_malformed_ports_and_strict_primitive_inputs(self):
        for doc, value in (("long", True), ("long", 2 ** 63), ("double", 1),
                           ("double", float("inf")), ("string", "\ud800")):
            with self.subTest(doc=doc, value=repr(value)), self.assertRaises(DataError):
                sdk1.dumps(doc, value)
        for payload in ('{}', '{"sdk":1,"datum":[9,0]}',
                        '{"sdk":1,"datum":[true,0]}', '{"sdk":1,"datum":[0,"x"]}'):
            with self.subTest(payload=payload), self.assertRaises(DataError):
                sdk1.loads(["long", "null"], payload)
        with self.assertRaises(DataError):
            sdk2.loads(["long"], '{"sdk":2,"value":{"branch":"missing","value":1}}')
