"""Exact finite-value compatibility analysis for SDK1 writer/reader pairs.

The decision answered here is:

  safe(writer, reader) iff every finite JSON-compatible value that the original
  vendor.sdk1 encoder accepts for ``writer`` is parsed successfully by the
  original warehouse.legacy resolver for ``reader``.

No SDK or resolver rule is changed to make a verdict pass: the reader-side
branch matching still goes through :func:`warehouse.legacy.matches`, and reader
defaults keep the registration-time validation of
:func:`vendor.codec.default_wire` (the resolver emits them locally, never
through a writer schema).

The analysis works on the finite graph of compiled schema nodes, never on
samples or depth bounds:

* ``empty(node)`` is the least fixpoint of "the node admits no finite value".
  Arrays are never empty because the empty list is always encodable.
* ``constructible(node, avoided)`` (EC) decides whether the node has a finite
  encodable value rejected by every avoided writer node.  Avoidance mirrors
  SDK1's first exactly-validating union branch precisely, so writer union
  routing is handled without enumerating values.
* ``safe_pair(node, avoided, reader)`` is a coinductive (greatest fixpoint)
  relation: revisited node pairs are treated as safe, exactly the closure rule
  the original recursive resolver uses for recursive schema nodes.

Every unsafe verdict is supported by a finite witness built from the EC
derivations.  The caller replays the witness through the real SDK1 encoder and
the real resolver before persisting anything.
"""
from .legacy import matches
from .schemas import MISSING

# Primitive acceptance is independent of the concrete value, so one member of
# each primitive domain is enough once constructibility has been proved.
PRIMITIVE_SAMPLE = {
    "null": None,
    "long": 0,
    "double": 0.0,
    "string": "",
}


def compute_empty(root):
    """Least fixpoint: True exactly for nodes admitting no finite value."""
    nodes = []
    seen = set()

    def collect(node):
        if id(node) in seen:
            return
        seen.add(id(node))
        nodes.append(node)
        if node.kind == "union":
            for branch in node.branches:
                collect(branch)
        elif node.kind == "array":
            collect(node.items)
        elif node.kind == "record":
            for _, child, _ in node.fields:
                collect(child)

    collect(root)
    empty = {id(node): True for node in nodes}
    changed = True
    while changed:
        changed = False
        for node in nodes:
            if not empty[id(node)]:
                continue
            if node.kind == "union":
                value = all(empty[id(b)] for b in node.branches)
            elif node.kind == "array":
                value = False  # [] is always a finite encodable list
            elif node.kind == "record":
                value = any(empty[id(child)] for _, child, _ in node.fields)
            else:
                value = False  # every primitive has finite values
            if value != empty[id(node)]:
                empty[id(node)] = value
                changed = True
    return empty


class Compatibility:
    def __init__(self, writer_root, reader_root):
        self.writer = writer_root
        self.reader = reader_root
        self.empty = compute_empty(writer_root)
        self.by_id = {}
        self._index(writer_root)
        self._index(reader_root)
        # (node id, frozenset(avoided node ids)) -> bool
        self.ec_memo = {}
        # (node id, avoided ids, reader id) -> bool
        self.ok_memo = {}
        self.ok_visiting = set()

    def _index(self, node):
        if id(node) in self.by_id:
            return
        self.by_id[id(node)] = node
        if node.kind == "union":
            for branch in node.branches:
                self._index(branch)
        elif node.kind == "array":
            self._index(node.items)
        elif node.kind == "record":
            for _, child, _ in node.fields:
                self._index(child)

    @staticmethod
    def _accepts_primitive(node, kind):
        """A node accepts either every value of the primitive kind or none."""
        if node.kind in PRIMITIVE_SAMPLE:
            return node.kind == kind
        if node.kind == "union":
            return any(Compatibility._accepts_primitive(b, kind)
                       for b in node.branches)
        return False  # records and arrays never accept primitive leaves

    @staticmethod
    def _array_item_demands(neg):
        """Item constraints that must hold for ``neg`` to reject a list."""
        if neg.kind == "array":
            return [neg.items]
        if neg.kind == "union":
            demands = []
            for branch in neg.branches:
                demands.extend(Compatibility._array_item_demands(branch))
            return demands
        return []  # primitive and record nodes always reject a list

    def _record_reject_groups(self, neg):
        """Record nodes that must reject a value for ``neg`` to reject it.

        None means ``neg`` rejects every record value regardless of shape;
        an empty list means rejection of a same-shape record is impossible;
        otherwise every returned record node (one per live union branch) must
        reject the value via one of its fields.
        """
        if neg.kind == "record":
            return [neg]
        if neg.kind == "union":
            groups = []
            for branch in neg.branches:
                group = self._record_reject_groups(branch)
                if group is None:
                    continue
                if not group:
                    return []
                groups.extend(group)
            return groups
        return None  # primitive/array nodes never accept a record value

    def _collect_record_groups(self, node, avoided_ids):
        """Return (groups, shape_mismatch) for avoided nodes over a record.

        shape_mismatch is True when some avoided node rejects every record of
        this field set outright, None when one accepts every such value, or
        False when all avoided nodes share the field set and need field
        rejection.
        """
        own_fields = [name for name, _, _ in node.fields]
        groups = []
        mismatch = False
        for neg_id in avoided_ids:
            group = self._record_reject_groups(self.by_id[neg_id])
            if group is None:
                mismatch = True
                continue
            if not group:
                return [], None
            for record_node in group:
                if [n for n, _, _ in record_node.fields] != own_fields:
                    mismatch = True
                    continue
                groups.append(record_node)
        return groups, mismatch

    # ------------------------------------------------------------------
    # EC: constructible values rejected by every avoided writer node
    # ------------------------------------------------------------------
    def ec(self, node, avoided):
        """True iff ``node`` has a finite value all avoided nodes reject."""
        avoided_ids = frozenset(id(n) for n in avoided)
        key = (id(node), avoided_ids)
        if key in self.ec_memo:
            return self.ec_memo[key]
        if not avoided_ids:
            result = not self.empty[id(node)]
            self.ec_memo[key] = result
            return result
        if node.kind in PRIMITIVE_SAMPLE:
            result = not any(self._accepts_primitive(self.by_id[i], node.kind)
                             for i in avoided_ids)
            self.ec_memo[key] = result
            return result
        if node.kind == "union":
            # SDK1 routes a value to the first branch that validates it: a
            # branch value must also be rejected by every earlier branch.
            result = False
            for index, branch in enumerate(node.branches):
                constraints = list(node.branches[:index])
                constraints.extend(self.by_id[i] for i in avoided_ids)
                if self.ec(branch, constraints):
                    result = True
                    break
            self.ec_memo[key] = result
            return result
        if node.kind == "array":
            result = True
            for neg_id in avoided_ids:
                for demand in self._array_item_demands(self.by_id[neg_id]):
                    if not self.ec(node.items, [demand]):
                        result = False
                        break
                if not result:
                    break
            self.ec_memo[key] = result
            return result
        # record
        groups, shape_mismatch = self._collect_record_groups(node, avoided_ids)
        if shape_mismatch is None:
            result = False  # one avoided node accepts every such record value
        elif shape_mismatch:
            result = True
        else:
            result = self._assignment_options(node, groups) is not None
        self.ec_memo[key] = result
        return result

    def _assignment_options(self, node, groups):
        """Per-group constructible field alternatives for same-shape records."""
        options = []
        for record_node in groups:
            alternatives = []
            for i, (_, neg_child, _) in enumerate(record_node.fields):
                writer_child = node.fields[i][1]
                if self.ec(writer_child, [neg_child]):
                    alternatives.append((i, neg_child))
            if not alternatives:
                return None
            options.append(alternatives)
        return options

    def _assignments(self, node, groups):
        """All feasible field-index -> avoided-node-set assignments."""
        options = self._assignment_options(node, groups)
        if options is None:
            return None
        results = []
        current = {}

        def backtrack(depth):
            if depth == len(options):
                results.append({i: frozenset(ns) for i, ns in current.items()})
                return
            for field_i, neg_child in options[depth]:
                current.setdefault(field_i, set()).add(neg_child)
                backtrack(depth + 1)
                current[field_i].discard(neg_child)
                if not current[field_i]:
                    del current[field_i]

        backtrack(0)
        return results

    # ------------------------------------------------------------------
    # Finite value / witness construction
    # ------------------------------------------------------------------
    def finite_value(self, node, seen=None):
        """A guaranteed finite SDK1-encodable value for a non-empty node.

        Termination follows from the emptiness least fixpoint: a non-empty
        union has a non-empty branch, a non-empty record has only non-empty
        fields, arrays always admit ``[]`` and primitives are leaves.
        """
        if self.empty[id(node)]:
            raise AssertionError("finite_value called on an empty node")
        seen = set() if seen is None else seen
        if node.kind in PRIMITIVE_SAMPLE:
            return PRIMITIVE_SAMPLE[node.kind]
        if node.kind == "array":
            return []
        if node.kind == "union":
            branches = [b for b in node.branches if not self.empty[id(b)]]
            branches.sort(key=lambda b: 0 if b.kind in PRIMITIVE_SAMPLE
                          or b.kind == "array" else 1)
            for branch in branches:
                if id(branch) not in seen:
                    return self.finite_value(branch, seen)
            return self.finite_value(branches[0], seen)
        if id(node) in seen:
            # Non-empty record on a productive cycle: the emptiness fixpoint
            # guarantees a primitive/array/nullable escape among its fields.
            value = {}
            for name, child, _ in node.fields:
                if child.kind == "array":
                    value[name] = []
                elif child.kind in PRIMITIVE_SAMPLE:
                    value[name] = PRIMITIVE_SAMPLE[child.kind]
                else:
                    value[name] = self.finite_value(child, seen)
            return value
        seen = seen | {id(node)}
        return {name: self.finite_value(child, seen)
                for name, child, _ in node.fields}

    def witness(self, node, avoided):
        """Build a finite value of ``node`` rejected by every avoided node.

        Constrained positions follow the EC derivation (the first validating
        writer-union branch, the rejecting record fields, the rejecting array
        items); every other position is filled by :meth:`finite_value`.  A memo
        on record shapes closes productive recursion without self-references.
        """
        memo = set()

        def build(current, current_avoided):
            avoided_ids = frozenset(id(n) for n in current_avoided)
            if current.kind in PRIMITIVE_SAMPLE:
                return PRIMITIVE_SAMPLE[current.kind]
            if current.kind == "union":
                # First exactly-validating branch: earlier branches reject.
                for index, branch in enumerate(current.branches):
                    constraints = list(current.branches[:index]) + \
                        list(current_avoided)
                    if self.ec(branch, constraints):
                        return build(branch, constraints)
                raise AssertionError("witness requested without constructibility")
            if current.kind == "array":
                value = []
                for neg in current_avoided:
                    for demand in self._array_item_demands(neg):
                        value.append(build(current.items, [demand]))
                return value
            # record
            key = (id(current), avoided_ids)
            if key in memo:
                return self.finite_value(current)
            memo.add(key)
            groups, shape_mismatch = self._collect_record_groups(
                current, avoided_ids)
            assignment = {}
            if not shape_mismatch and groups:
                feasible = self._assignments(current, groups)
                assignment = feasible[0] if feasible else {}
            value = {}
            for i, (name, child, _) in enumerate(current.fields):
                if i in assignment:
                    value[name] = build(child, list(assignment[i]))
                else:
                    value[name] = self.finite_value(child)
            return value

        return build(node, list(avoided))

    # ------------------------------------------------------------------
    # Coinductive whole-domain safety
    # ------------------------------------------------------------------
    def safe(self):
        if self.empty[id(self.writer)]:
            return True, None
        if self._ok(self.writer, frozenset(), self.reader):
            return True, None
        return False, self.failure_witness(
            self.writer, frozenset(), self.reader)

    def _ok(self, writer, avoided_ids, reader):
        key = (id(writer), avoided_ids, id(reader))
        if key in self.ok_memo:
            return self.ok_memo[key]
        if key in self.ok_visiting:
            return True  # coinductive closure of a productive revisited pair
        self.ok_visiting.add(key)
        try:
            result = self._ok_rules(writer, avoided_ids, reader)
        finally:
            self.ok_visiting.discard(key)
        self.ok_memo[key] = result
        return result

    def _ok_rules(self, writer, avoided_ids, reader):
        avoided = [self.by_id[i] for i in avoided_ids]
        if writer.kind == "union":
            for index, branch in enumerate(writer.branches):
                constraints = list(writer.branches[:index]) + avoided
                constraint_ids = frozenset(id(n) for n in constraints)
                if self.ec(branch, constraints):
                    if not self._ok(branch, constraint_ids, reader):
                        return False
            return True
        if reader.kind == "union":
            chosen = next((branch for branch in reader.branches
                           if matches(writer, branch)), None)
            if chosen is None:
                return not self.ec(writer, avoided)
            return self._ok(writer, avoided_ids, chosen)
        if not matches(writer, reader):
            return not self.ec(writer, avoided)
        if writer.kind == "array":
            # An empty item schema admits no elements, so only [] is encodable;
            # otherwise every possible element must resolve against the reader
            # item schema (so incompatible item unions are still detected).
            if not self.empty[id(writer.items)]:
                if not self._ok(writer.items, frozenset(), reader.items):
                    return False
                for neg in avoided:
                    for demand in self._array_item_demands(neg):
                        if not self._ok(writer.items,
                                        frozenset([id(demand)]), reader.items):
                            return False
            return True
        if writer.kind == "record":
            return self._ok_record(writer, avoided_ids, reader)
        # Primitive leaves (including long -> double promotion) cannot fail
        # once the structural match above succeeded.
        return True

    def _ok_record(self, writer, avoided_ids, reader):
        groups, shape_mismatch = self._collect_record_groups(writer, avoided_ids)
        if shape_mismatch is None:
            return False  # an avoided node accepts every same-shape record
        if shape_mismatch or not groups:
            assignments = [{}]
        else:
            assignments = self._assignments(writer, groups)
            if not assignments:
                return False
        field_index = {name: i for i, (name, _, _) in enumerate(writer.fields)}
        source = {name: child for name, child, _ in writer.fields}
        for assignment in assignments:
            for name, reader_child, default in reader.fields:
                if name in source:
                    writer_child = source[name]
                    constraint_nodes = assignment.get(field_index[name],
                                                      frozenset())
                    constraint_ids = frozenset(id(n) for n in constraint_nodes)
                    if not self._ok(writer_child, constraint_ids, reader_child):
                        return False
                elif default is MISSING:
                    # Every constructible writer record lacks this field.
                    return False
                # A reader default is built locally by the resolver and was
                # validated at registration via default_wire, so it needs no
                # writer-side check.
        return True

    # ------------------------------------------------------------------
    # Unsafe witness materialized along the actual failing resolution path
    # ------------------------------------------------------------------
    def failure_witness(self, writer, avoided_ids, reader):
        avoided = [self.by_id[i] for i in avoided_ids]
        if writer.kind == "union":
            for index, branch in enumerate(writer.branches):
                constraints = list(writer.branches[:index]) + avoided
                constraint_ids = frozenset(id(n) for n in constraints)
                if self.ec(branch, constraints) and not self._ok(
                        branch, constraint_ids, reader):
                    return self.failure_witness(
                        branch, constraint_ids, reader)
            raise AssertionError("unsafe pair produced no failing union branch")
        if reader.kind == "union":
            chosen = next((branch for branch in reader.branches
                           if matches(writer, branch)), None)
            if chosen is None:
                return self.witness(writer, avoided)
            return self.failure_witness(writer, avoided_ids, chosen)
        if not matches(writer, reader):
            return self.witness(writer, avoided)
        if writer.kind == "array":
            # No finite element can exist: the empty list is the only value and
            # it already fails the structural item match checked above.
            if self.empty[id(writer.items)]:
                return []
            if not self._ok(writer.items, frozenset(), reader.items):
                return [self.failure_witness(
                    writer.items, frozenset(), reader.items)]
            for neg in avoided:
                for demand in self._array_item_demands(neg):
                    demand_ids = frozenset([id(demand)])
                    if not self._ok(writer.items, demand_ids, reader.items):
                        return [self.failure_witness(
                            writer.items, demand_ids, reader.items)]
            raise AssertionError("unsafe array produced no failing element")
        if writer.kind == "record":
            return self._record_failure_witness(writer, avoided_ids, reader)
        raise AssertionError("primitive pair cannot be an unsafe witness")

    def _record_failure_witness(self, writer, avoided_ids, reader):
        groups, shape_mismatch = self._collect_record_groups(writer, avoided_ids)
        if shape_mismatch:
            assignments = [{}]
        else:
            assignments = self._assignments(writer, groups) or [{}]
        source = {name: child for name, child, _ in writer.fields}
        reader_fields = {name: child for name, child, _ in reader.fields}
        for assignment in assignments:
            value = {}
            failed = False
            for i, (name, writer_child, _) in enumerate(writer.fields):
                constraint_nodes = assignment.get(i, frozenset())
                constraint_ids = frozenset(id(n) for n in constraint_nodes)
                if name in reader_fields:
                    reader_child = reader_fields[name]
                    if not self._ok(writer_child, constraint_ids, reader_child):
                        value[name] = self.failure_witness(
                            writer_child, constraint_ids, reader_child)
                        failed = True
                        continue
                value[name] = self.finite_value(writer_child)
            for name, _, default in reader.fields:
                if name not in source and default is MISSING:
                    failed = True  # missing required reader field
            if failed:
                return value
        raise AssertionError("unsafe record produced no failing field")


def analyze(writer_root, reader_root):
    """Return ``(safe, witness)`` for a pair of compiled schema roots."""
    return Compatibility(writer_root, reader_root).safe()
