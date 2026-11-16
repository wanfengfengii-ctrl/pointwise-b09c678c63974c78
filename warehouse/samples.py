"""Finite warehouse history and same-schema codec examples."""
import copy

WRITER = {
    "type": "record", "name": "lake.Event",
    "fields": [
        {"name": "id", "type": "long"},
        {"name": "value", "type": ["long", "double", "null"]},
        {"name": "children", "type": {"type": "array", "items": "lake.Event"}}
    ]
}
READER = {
    "type": "record", "name": "view.Event", "aliases": ["lake.Event"],
    "fields": [
        {"name": "id", "type": "long"},
        {"name": "value", "type": ["double", "long", "null"]},
        {"name": "children", "type": {"type": "array", "items": "view.Event"}},
        {"name": "quality", "type": ["null", "string"], "default": None}
    ]
}
VALUES = [
    {"id": 1, "value": 7, "children": [
        {"id": 2, "value": 7.0, "children": []},
        {"id": 3, "value": None, "children": []}]},
    {"id": 4, "value": 0, "children": []}
]
# Frozen expected wire events expose the different integral-number identities.
PORT_EVENTS = [
    {"schema": ["long", "double"], "input": 7,
     "sdk1_wire": {"sdk": 1, "datum": [0, 7]}},
    {"schema": ["long", "double"], "input": 7.0,
     "sdk1_wire": {"sdk": 1, "datum": [1, 7.0]}}
]


def seed(warehouse):
    writer = warehouse.register_schema(copy.deepcopy(WRITER), "seed.writer.1")
    reader = warehouse.register_schema(copy.deepcopy(READER), "seed.reader.1")
    warehouse.publish_batch("history", writer, copy.deepcopy(VALUES), "seed.publish.1")
    warehouse.create_job("completed", ["history"], reader, "seed.job.completed")
    warehouse.run_job("completed")
    warehouse.create_job("pending", ["history", "history"], reader, "seed.job.pending")
    newer = copy.deepcopy(READER)
    newer["fields"][1]["type"] = ["long", "double", "null"]
    reader2 = warehouse.register_schema(newer, "seed.reader.2")
    warehouse.create_job("new-reader", ["history"], reader2, "seed.job.new")
    warehouse.run_job("new-reader")
    incompatible = warehouse.register_schema("string", "seed.reader.bad")
    warehouse.create_job("failed", ["history"], incompatible, "seed.job.failed")
    warehouse.run_job("failed")
    writer2_doc = copy.deepcopy(WRITER)
    writer2_doc["fields"].append({"name": "source", "type": "string"})
    writer2 = warehouse.register_schema(writer2_doc, "seed.writer.2")
    warehouse.publish_batch("recent", writer2,
        [{"id": 5, "value": -2.0, "children": [], "source": "sensor"}], "seed.publish.2")
    return {"writer": writer, "reader": reader, "reader2": reader2, "writer2": writer2}
