# Schema-bound warehouse archive

A Python 3.11 standard-library warehouse for immutable schema versions, archived
batches and fixed-reader processing jobs. SQLite stores the complete history,
request receipts, job bindings and terminal results. The normal business path
uses the offline SDK1 port.

Run from this directory:

```sh
python3.11 -B -m unittest discover -s tests -t . -v
python3.11 -B -m warehouse demo
python3.11 -B -m warehouse seed --database sample.sqlite
```

The demo creates its database inside a temporary directory, processes historical
data, closes and reopens the database, then processes a mixed-version batch list.
The seed command creates the finite history in the specified database. No
external services, packages or network access are needed.

## Public API

```python
from warehouse import Warehouse

with Warehouse("archive.sqlite") as w:
    writer = w.register_schema("long", "register-long")
    reader = w.register_schema(["double", "long"], "register-reader")
    receipt = w.publish_batch("day-1", writer, [0, 7], "publish-day-1")
    w.create_job("daily", ["day-1"], reader, "create-daily")
    result = w.run_job("daily")
    assert result == w.get_result("daily")
```

This result contains two rows in input order. Each value has reader union
identity, for example
`{"branch": "double", "index": 0, "value": 7.0}`.
`w.schemas.get(id)`, `w.archive.read_batch(id)`,
`w.archive.values(id)` and `w.jobs.binding(id)` expose the registered document,
stored SDK1 payloads, implicit decoded values and fixed job binding respectively.
See CONTRACT.md for exact semantics.

A new registration request creates a new immutable schema ID, including when its
record name equals an earlier version. Batches keep their writer IDs; jobs keep
their reader IDs and ordered input lists. No "latest schema" lookup occurs during
job execution. Duplicate batch references intentionally duplicate output rows.

## Business components

- `warehouse/schemas.py`: schema validation, named environments and registration.
- `warehouse/archive.py`: whole-batch validation, immutable publication and receipts.
- `warehouse/jobs.py`: fixed-reader binding and ordered processing.
- `warehouse/results.py`: atomic terminal results and stable queries.
- `warehouse/db.py`: SQLite schema, transactions and maintenance-window boundaries.
- `warehouse/legacy.py`: the normal SDK1 writer-to-reader resolution entry.
- `vendor/sdk1.py`, `vendor/sdk2.py`: offline same-schema codec ports.

The SDK2 port is available independently for explicit same-schema reads and
writes. Warehouse publication and jobs use SDK1. The ports share primitive
validation and schema parsing, and neither port resolves a writer schema to a
different reader schema.

## Finite history

`warehouse.samples.seed(w)` supplies:
two versions of the same recursive writer name, two fixed reader versions with
different union order, an incompatible reader, historical and recent batches,
completed and failed results, a queued job with repeated input, and all receipts.
`WRITER`, `READER`, `VALUES` and `PORT_EVENTS` are public finite fixtures.

## Regressions

The complete unittest suite includes registration rejection, request replay and
conflict, null/zero distinctions, batch rejection, promotion, reader defaults,
directional aliases, recursive arrays, both integral-number union identities,
same-shape named branches, pinned historical tasks, missing inputs, empty and
repeated batches, atomic business failure, retryable infrastructure failure,
maintenance rollback and disk-backed reopening.

Tests use in-memory SQLite or their own temporary directories. The demo and
tests do not inspect any other project or user files.
