# Normal warehouse contract

## Scope and data

Only Python 3.11 and its standard library are required. Values and schema
documents are finite JSON-compatible objects. The supported types are null,
signed 64-bit long, finite IEEE double, UTF-8 string, named record, array and
union. Boolean is not a long. SDK1 long input is exactly int and double input is
exactly float, so 7 and 7.0 select different branches. JSON numbers retain these
host types when payloads are read.

A schema document is a primitive/reference string, a nonempty list of union
branches, or a type object. A record has type, name, optional namespace and
aliases, and a fields list of name/type/optional default objects. An array has
type and items. Primitive type objects are also accepted. Names consist of
dot-separated identifiers. A qualified record name determines its namespace.
Short references are resolved in the enclosing record namespace, never through
another registration's environment. Forward references within one document and
record recursion are supported. A document cannot define a record name twice.
Aliases belong to reader-side name matching; they do not introduce reference
definitions. Field aliases are outside this contract.

Union branches cannot themselves be unions or repeat a primitive, array or
fully qualified record label. Records with different names may have identical
fields. A record value must contain exactly its declared fields; write-time
defaults do not fill missing input fields. Arrays are ordered lists. Host
cycles and unbounded values are outside the input domain.

Schema defaults must validate against their field schema, with every union in
a default selecting its first branch. Thus a null default for [long,null] is
invalid, including inside an array default. Defaults use SDK1's exact primitive
input types. A default record must explicitly supply all its fields.

## Public operations

The facade is Warehouse(path=":memory:", hook=None). It is a context manager;
close() closes its SQLite connection. Its components are also public through
schemas, archive, jobs and results.

register_schema(document, request_id) -> integer schema_id:
validates the complete definition before registering an immutable version.
schemas.get(schema_id) returns a fresh document. Different request IDs produce
different versions even for equal documents. Same request/content reuses the
original schema ID; changed content raises Conflict. Invalid definitions raise
SchemaError without a schema or receipt.

publish_batch(batch_id, schema_id, values, request_id) -> receipt:
values is an ordered list. Every datum is encoded and checked before the
batch and receipt become visible. Receipt is
{batch_id, schema_id, count}. An empty batch is valid. read_batch(batch_id)
returns {batch_id, schema_id, payloads}, where each payload is an SDK1 JSON text.
values(batch_id) returns fresh implicit decoded values.
A batch cannot be replaced, including under another request ID. Bad input
raises DataError with no batch or receipt. Unknown schema raises NotFound.
Same successful request/content returns its original receipt; changed content
or an occupied batch ID raises Conflict. Request fingerprints include canonical
SDK1 payloads, so integral doubles remain distinct from longs.

create_job(job_id, batch_ids, reader_id, request_id) -> job_id:
batch_ids is a finite ordered list, possibly empty or containing duplicates.
All referenced batches and the reader version must exist. The binding is
immutable after creation. Unknown references raise NotFound without a job or
receipt. An occupied job ID or changed request content raises Conflict.
Successful request replay returns the original ID, including after completion.
jobs.binding(job_id) returns {reader_id, batch_ids, state}.

run_job(job_id) -> terminal result:
a queued job processes batches in binding order, then each batch's rows in
stored order. It uses each batch's fixed writer schema and the job's fixed
reader schema. A successful run publishes all rows and the done state in one
transaction. A ResolutionError publishes only a failed state and the error,
never earlier successful rows. A terminal rerun returns the existing result.
Nonbusiness exceptions propagate, roll back any terminal publication and leave
the job queued for retry. Stored payload corruption raises DataError and is
not converted to ResolutionError.

get_result(job_id) -> fresh result:
done is {job_id, state:"done", rows:[{batch_id,row_index,value}, ...]}.
row_index is the zero-based position within its source batch; repeated input
batches may therefore repeat coordinates. failed is
{job_id, state:"failed", error:{code:"ResolutionError",message}, rows:[]}.
Unknown job raises NotFound; queued job raises NotReady.
The result includes reader union identity as
{branch: fully-qualified-record-name-or-type, index: reader-branch-index, value}.
Ordinary records preserve reader field structure; arrays preserve order; null
and empty arrays remain distinct. A reader without a union produces an ordinary
value even when the writer schema contains a union.

Requests are scoped by operation: schema, publish or job. request_id must be
nonempty text; malformed operation IDs or container arguments raise ValueError
or DataError as stated by the API. Inputs are validated before replay, and
receipt content is never rebound. APIs do not mutate caller data.

## Directional resolution rules

The entry warehouse.legacy.resolve(writer_document, reader_document, payload)
reads one SDK1 envelope and returns a resolved value or raises ResolutionError.
Both schema environments are built independently.

1. A writer union uses its persisted branch index, then resolves that actual
   branch. It never guesses branch identity from decoded Python values.
2. For a reader union, choose the first schema-matching branch in declared order.
   Resolve within that branch; an error in its fields does not try a later branch.
3. Primitive types match themselves. Only long to double promotion is permitted.
4. Records match if full names are equal or the writer's full name appears in
   the reader's aliases. Matching uses names, not field-shape similarity.
   Reader fields are visited in reader order. Same-named writer fields resolve
   recursively. An absent field uses the reader's default or raises
   ResolutionError("missing reader field: NAME"). Extra writer fields are ignored
   after the SDK1 payload has been validated.
5. Arrays match arrays whose item schemas match, and resolve elements in order.
   In schema matching, a union matches when any of its branches matches the other
   schema. Revisited schema-node pairs close recursion; record-name matching
   does not pre-resolve fields. Actual datum resolution can still fail later,
   including within a writer union element. Empty incompatible primitive-item
   arrays do not become compatible merely because there are no elements.
6. Defaults are fresh reader-side values. Their union identity is always branch
   zero at every level. Default values are not resolved through a writer schema.

Other mismatch messages are "incompatible writer and reader" and
"no matching reader union branch". The first error in the ordered traversal is
the job error. Full payload validation precedes field resolution for each row.

## Codec ports and persisted SDK1 format

vendor.sdk1.dumps(document,value) -> JSON text;
vendor.sdk1.loads(document,payload) -> implicit value.
The envelope has exactly {sdk:1, datum:wire}. For a primitive, wire is its JSON
value; record wire is a field-order list; array wire is an item list; union wire
is [writer_branch_index, branch_wire]. Full named environments belong to the
schema ID referenced by the batch. Recursive references do not duplicate schema
definitions on the wire. load_wire(compiled_root,payload) validates and returns
the positional wire, preserving indices. An invalid envelope or datum raises
DataError.

vendor.sdk2.dumps(document,value) and loads(document,payload) use the same schema
language, but every union input and output is vendor.codec.Union(index,value).
An explicitly selected double accepts int or float and normalizes to float.
This allows Union(1,7) to select double in [long,double].
The SDK2 envelope is {sdk:2,value:target}. Union target is
{branch:branch_label,value:target}; record target is
{record:full_name,fields:{field_name:target,...}}; arrays and primitives retain
their JSON shapes. Branch labels are checked against the bound schema.
Neither port accepts the other generation's envelope.
vendor.codec contains same-schema validation helpers used by both ports.
Neither SDK port performs cross-schema reading or durable business operations.

## SQLite ownership and failure boundaries

warehouse/db.py contains the complete executable old DDL. metadata records
storage_version=1. schemas holds immutable documents; batches holds writer
bindings and JSON lists of SDK1 payload texts; jobs holds reader bindings,
ordered batch lists and queued/done/failed state. results and result_values
hold terminal metadata and ordered values. receipts holds operation-scoped
request fingerprints and responses. Foreign keys protect referenced objects;
SQL triggers reject updates to schema documents, batches and job bindings.
There is no update/delete business API.

Single-worker maintenance operations can reuse
with w.db.transaction("maintenance"): ... .
The transaction rolls back on any exception. A hook receives boundary names
write.before_commit, publish.before_commit, enqueue.before_commit,
run.after_resolve, run.before_commit, or maintenance.before_commit when reached.
Raising from a hook exercises the real rollback boundary. Hooks perform no
business work themselves. They are intended for deterministic failure checks,
not asynchronous concurrency. The normal application does not require a hook.

Schema rejection, a bad final batch datum, a later incompatible batch and
publication-boundary exceptions all leave no partial business output.
Closing/reopening preserves fixed bindings, terminal results and receipts.
No online migration, distributed scheduler, map/enum/fixed, automatic schema
inference, query language or general schema-registry service is supplied.
