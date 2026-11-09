"""Extract conditional descriptions, then evaluate them against the full graph.

The default memory pipeline uses this protocol. Intermediate evidence never
publishes memory; final selection remains subject to the original strict parser.
"""

import json

from src.agent.memory_response import _unique_object, _reject_constant, _string_array
from src.agent.memory_state import _freeze_triplets
from src.agent.validation_error import MemoryValidationError


CASES = ("true", "false", "not_conditional")
DECISION_INSTRUCTIONS = """Extract the original question's requirements.
First determine whether the ORIGINAL QUESTION explicitly specifies different
requirements for a true and a false equality condition. Only that form permits
conditional_equals. Never invent a condition from a relation in the graph.
Return only the specified JSON. Do not evaluate the condition yourself.
Wrap the extraction in description. Choose exactly one of four kinds from the
ORIGINAL QUESTION before filling fields: conditional_equals for a supported
explicit equality branch condition; nonconditional_path for a nonconditional two-hop
dependency; nonconditional for independent one-hop requirements only; unsupported
with a verbatim quote for requirements outside these supported forms.
Finding an entity and then asking its attribute is a dependency: ALWAYS use
nonconditional_path even when the graph already names the intermediate entity.
For example, "Where is the headquarters of the company that owns Aurora Tower?"
uses root_entity Aurora Tower and ordered outgoing steps owned_by, headquarters.
Do not represent that dependency as independent nonconditional entity groups.
Entity-locating relations (the author of a book, the company that owns a tower)
and relative clauses are dependencies, not true/false branch conditions.
Contrast the tower question above with: "Aurora Tower has exactly one owner.
If its owner is Helios Ltd, where was Ada Linn born? Otherwise, where was
Owen Pike born?" Only the latter has an equality condition and two branches.
Question type is independent of graph values and missing evidence.
A false or unknown condition is still conditional_equals. Quote the original
branch-determining condition in condition_quote; never use an entire
unconditional question to fill this field. An instruction to ask first
when evidence is missing is an answering prerequisite, not the branch condition.
conditional_equals contains condition and branches true/false only.
nonconditional contains only branch (requirements), without condition.
Only within conditional_equals, condition describes a single relation equality.
Its comparison value must be a verbatim constraint in the ORIGINAL QUESTION.
The graph supplies actual values and evidence, never a new branch condition.
subject must occur verbatim in the original question or graph;
relation is the exact graph relation
name (if absent, describe the required relation). operator is eq. uniqueness_quote
is the exact original-question passage explicitly establishing a single value,
or empty if absent. A definite article such as "the author" does not explicitly
establish a single value and cannot supply uniqueness_quote.
The comparison value is a query constraint, NEVER an observed fact.
Only for conditional_equals and nonconditional, the following grouped one-hop
binding instructions apply. For each branch supply requirements: groups with entity and fact_queries.
entity is the known entity whose attribute or relation the question asks about,
not the retrieved answer endpoint. Generate that entity only once per group.
Each query belongs to its group's entity and has only relation and binding.
For observed facts,
binding is {kind: observed, evidence_ids: [integer IDs from Numbered knowledge]}.
Reference facts with the exact relation and target endpoint; code derives direction.
For absent facts binding is {kind: missing, direction: outgoing or incoming}.
Outgoing means target is head; incoming means target is tail. A missing declaration
is invalid if either direction has the exact target/relation in the graph.
Describe every target's required
one-hop fact even when absent from the graph. Use exact graph relation names
when present. Never supply predicted values or select evidence. Both conditional
branches must have nonempty requirements, each with nonempty fact_queries.
Multiple entities and multiple relations per entity are allowed, including
incoming relations and entities with no observed facts.
For a nonconditional two-hop linear question use nonconditional_path instead:
{kind: nonconditional_path, root_entity: exact entity from the original question,
 steps: [{relation: exact relation, binding: observed or missing binding},
         {relation: exact relation, binding: observed or missing binding}]}.
Start with root_entity as the current node. At EACH step, direction is relative
to that step's currently reachable node: outgoing means it is the triple's head;
incoming means it is the triple's tail. Move to the other endpoint, then determine
the next step's direction relative to that new node. With evidence present, use
the exact relation and current endpoint, not passive wording, question word order
or the final answer's position. With evidence absent, describe the relation and
direction required by the original question; never invent intermediate entities.
For example, given Helios Ltd -> owns -> Aurora Tower and
Helios Ltd -> headquarters -> Northport, asking for the headquarters of the
company owning Aurora Tower uses root Aurora Tower and steps
owns/incoming then headquarters/outgoing. If the first fact instead is
Aurora Tower -> owned_by -> Helios Ltd, its first step is owned_by/outgoing.
Extract both ordered steps even if the graph is empty. Never fill intermediate
entities or predicted answers. For each observed step use binding
{kind: observed, evidence_ids: [IDs from Numbered knowledge]} and no direction.
Reference exact-relation edges connected to the current reachable frontier.
Code derives direction from endpoint roles and follows ALL matching edges.
Only when no frontier entity has that relation in either direction use
{kind: missing, direction: outgoing or incoming}. If an earlier step is missing,
keep subsequent steps as missing requirements without inventing an entity or
referencing disconnected facts. Code follows all actual connections
from the root. Do not flatten a dependent path into independent entity groups.
Other path lengths, conditional paths, aggregation and complex requirements
are unsupported: use the unsupported description variant.
Code will query the COMPLETE graph for condition facts and select the branch.
"""

# Generation and local decoding independently enforce the same tagged union.
# Paths have a separate evaluator; existing branch bindings retain their contract.


STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}
CONDITION_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["kind", "subject", "relation", "operator", "value", "uniqueness_quote"],
    "properties": {"kind": {"type": "string", "enum": ["equals", "none", "unsupported"]},
                   **{key: STRING for key in ("subject", "relation", "operator", "value", "uniqueness_quote")}},
}
BRANCH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["requirements"],
    "properties": {"requirements": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["entity", "fact_queries"], "properties": {
        "entity": {"type": "string", "description": "Known entity whose facts are requested; not the answer value."},
        "fact_queries": {
        "type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["relation", "binding"],
        "properties": {"relation": STRING, "binding": {"anyOf": [
            {"type": "object", "additionalProperties": False,
             "required": ["kind", "evidence_ids"], "properties": {
                 "kind": {"type": "string", "enum": ["observed"]},
                 "evidence_ids": {"type": "array", "items": {"type": "integer"}}}},
            {"type": "object", "additionalProperties": False,
             "required": ["kind", "direction"], "properties": {
                 "kind": {"type": "string", "enum": ["missing"]},
                 "direction": {"type": "string", "enum": ["incoming", "outgoing"]}}}
        ]}}}}}}}},
}
CONDITION_SCHEMA = {**CONDITION_SCHEMA,
    "required": [key for key in CONDITION_SCHEMA["required"] if key != "kind"] + ["condition_quote"],
    "properties": {key: value for key, value in CONDITION_SCHEMA["properties"].items() if key != "kind"}}
CONDITION_SCHEMA["properties"].update(
    operator={"type": "string", "enum": ["eq"]}, condition_quote=STRING)
DESCRIPTION_VARIANTS = [
    {"type": "object", "additionalProperties": False,
     "required": ["kind", "root_entity", "steps"], "properties": {
         "kind": {"type": "string", "enum": ["nonconditional_path"]},
         "root_entity": {"type": "string", "minLength": 1},
         "steps": {"type": "array", "minItems": 2, "maxItems": 2, "items": {
             "type": "object", "additionalProperties": False,
             "required": ["relation", "binding"], "properties": {
                 "relation": {"type": "string", "minLength": 1},
                 "binding": BRANCH_SCHEMA["properties"]["requirements"]["items"]["properties"]["fact_queries"]["items"]["properties"]["binding"]}}}}},
    {"type": "object", "additionalProperties": False,
     "required": ["kind", "condition", "branches"], "properties": {
         "kind": {"type": "string", "enum": ["conditional_equals"]},
         "condition": CONDITION_SCHEMA,
         "branches": {"type": "object", "additionalProperties": False,
                      "required": ["true", "false"],
                      "properties": {case: BRANCH_SCHEMA for case in ("true", "false")}}}},
    {"type": "object", "additionalProperties": False,
     "required": ["kind", "branch"], "properties": {
         "kind": {"type": "string", "enum": ["nonconditional"]}, "branch": BRANCH_SCHEMA}},
    {"type": "object", "additionalProperties": False,
     "required": ["kind", "quote"], "properties": {
         "kind": {"type": "string", "enum": ["unsupported"]}, "quote": STRING}},
]
DECISION_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "evidence_description", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["description"],
    "properties": {"description": {"anyOf": DESCRIPTION_VARIANTS}}}}}


def decode_description(record, question):
    """Decode the tagged wire structure without inferring or repairing fields.

    Args:
        record: Untrusted response object containing a single description.
        question: Original question used to validate literal condition quotes.

    Returns:
        Condition and three branch slots for the existing graph evaluator.

    Raises:
        MemoryValidationError: Mixed structures, invalid quotes or unsupported type.
    """
    if not isinstance(record, dict) or set(record) != {"description"}:
        raise MemoryValidationError("decision_fields")
    item = record["description"]
    if not isinstance(item, dict):
        raise MemoryValidationError("decision_fields")
    kind = item.get("kind")
    empty = {"targets": [], "fact_queries": []}
    if kind == "nonconditional_path":
        if (set(item) != {"kind", "root_entity", "steps"}
                or not isinstance(item["root_entity"], str)
                or not item["root_entity"].strip()
                or item["root_entity"] not in question
                or not isinstance(item["steps"], list) or len(item["steps"]) != 2):
            raise MemoryValidationError("decision_path")
        for step in item["steps"]:
            if (not isinstance(step, dict) or set(step) != {"relation", "binding"}
                    or not isinstance(step["relation"], str) or not step["relation"].strip()
                    or not isinstance(step["binding"], dict)):
                raise MemoryValidationError("decision_path")
            binding = step["binding"]
            if not ((set(binding) == {"kind", "evidence_ids"}
                     and binding["kind"] == "observed"
                     and isinstance(binding["evidence_ids"], list)
                     and binding["evidence_ids"]
                     and all(type(i) is int and i >= 0 for i in binding["evidence_ids"]))
                    or (set(binding) == {"kind", "direction"}
                        and binding["kind"] == "missing"
                        and binding["direction"] in ("outgoing", "incoming"))):
                raise MemoryValidationError("decision_path")
        return item
    if kind == "conditional_equals" and set(item) == {"kind", "condition", "branches"}:
        condition = item["condition"]
        if (not isinstance(condition, dict) or set(condition) != set(CONDITION_SCHEMA["required"])
                or any(not isinstance(v, str) for v in condition.values())):
            raise MemoryValidationError("decision_condition")
        quote = condition["condition_quote"]
        if not quote.strip() or quote not in question:
            raise MemoryValidationError("decision_condition")
        branches = item["branches"]
        if not isinstance(branches, dict) or set(branches) != {"true", "false"}:
            raise MemoryValidationError("decision_targets")
        return {"condition": {"kind": "equals", **{k: v for k, v in condition.items()
                                                  if k != "condition_quote"}},
                "branches": {**{case: decode_branch(branches[case]) for case in ("true", "false")}, "not_conditional": empty}}
    if kind == "nonconditional" and set(item) == {"kind", "branch"}:
        return {"condition": {"kind": "none", **dict.fromkeys(
                    ("subject", "relation", "operator", "value", "uniqueness_quote"), "")},
                "branches": {"true": empty, "false": empty, "not_conditional": decode_branch(item["branch"])}}
    if kind == "unsupported" and set(item) == {"kind", "quote"}:
        raise MemoryValidationError("decision_unsupported")
    raise MemoryValidationError("decision_fields")


def decode_branch(branch):
    """Expand grouped wire requirements into the internal query contract.

    Args:
        branch: Untrusted branch with entity groups and bound fact requests.

    Returns:
        Internal targets and queries inheriting their group's entity exactly.

    Raises:
        MemoryValidationError: Empty groups, old fields or malformed requests.
    """
    if not isinstance(branch, dict) or set(branch) != {"requirements"}:
        raise MemoryValidationError("decision_fields")
    groups = branch["requirements"]
    if not isinstance(groups, list) or not groups:
        raise MemoryValidationError("decision_queries")
    targets, queries = [], []
    for group in groups:
        if (not isinstance(group, dict) or set(group) != {"entity", "fact_queries"}
                or not isinstance(group["entity"], str) or not group["entity"].strip()
                or not isinstance(group["fact_queries"], list) or not group["fact_queries"]):
            raise MemoryValidationError("decision_queries")
        targets.append(group["entity"])
        for query in group["fact_queries"]:
            if not isinstance(query, dict) or set(query) != {"relation", "binding"}:
                raise MemoryValidationError("decision_queries")
            queries.append({"target": group["entity"], **query})
    return {"targets": list(dict.fromkeys(targets)), "fact_queries": queries}


def resolve_query(query, graph):
    """Validate a request-local binding and derive its directional query.

    Args:
        query: Untrusted target, relation and observed/missing binding.
        graph: Sorted complete submitted triples defining integer IDs.

    Returns:
        Target, relation and validated direction for full-graph matching.

    Raises:
        MemoryValidationError: Invalid binding, ambiguous role or false gap.
    """
    if (not isinstance(query, dict) or set(query) != {"target", "relation", "binding"}
            or any(not isinstance(query[k], str) or not query[k].strip()
                   for k in ("target", "relation"))):
        raise MemoryValidationError("decision_queries")
    binding = query["binding"]
    if not isinstance(binding, dict):
        raise MemoryValidationError("decision_queries")
    target, relation = query["target"], query["relation"]
    hits = {direction: [t for t in graph if t[index] == target and t[1] == relation]
            for direction, index in (("outgoing", 0), ("incoming", 2))}
    if binding.get("kind") == "observed" and set(binding) == {"kind", "evidence_ids"}:
        ids = binding["evidence_ids"]
        if (not isinstance(ids, list) or not ids or
                any(type(i) is not int or not 0 <= i < len(graph) for i in ids)):
            raise MemoryValidationError("decision_evidence_id")
        roles = set()
        for i in ids:
            triple = graph[i]
            endpoints = {d for d, index in (("outgoing", 0), ("incoming", 2))
                         if triple[index] == target and triple[1] == relation}
            if len(endpoints) != 1:
                raise MemoryValidationError("decision_queries")
            roles.update(endpoints)
        if len(roles) != 1:
            raise MemoryValidationError("decision_queries")
        direction = roles.pop()
    elif (binding.get("kind") == "missing" and set(binding) == {"kind", "direction"}
          and binding["direction"] in ("outgoing", "incoming")):
        direction = binding["direction"]
        # Reverse hits are a conservative conflict, not permission to flip direction.
        if any(hits.values()):
            raise MemoryValidationError("decision_conflict")
    else:
        raise MemoryValidationError("decision_queries")
    return {"target": target, "relation": relation, "direction": direction}


def derive_path_decision(description, graph):
    """Follow exactly two declared steps and protect every reachable prefix.

    Args:
        description: Locally validated path with a question-grounded root.
        graph: Complete eligible triples, sorted with exact string identities.

    Returns:
        Decision containing reachable terminal candidates and resolvable gaps.
        A gap on any candidate blocks answering even if another path completes.

    Raises:
        MemoryValidationError: A reachable self-loop or reverse-only conflict.
    """
    frontier = {description["root_entity"]}
    required, missing, matches = set(), [], []
    for step in description["steps"]:
        binding = step["binding"]
        if not frontier:
            if binding["kind"] == "observed":
                raise MemoryValidationError("decision_path_conflict")
            continue
        if binding["kind"] == "observed":
            roles = set()
            for evidence_id in binding["evidence_ids"]:
                if not 0 <= evidence_id < len(graph):
                    raise MemoryValidationError("decision_evidence_id")
                triple = graph[evidence_id]
                endpoints = {d for d, position in (("outgoing", 0), ("incoming", 2))
                             if triple[position] in frontier and triple[1] == step["relation"]}
                if len(endpoints) != 1:
                    raise MemoryValidationError("decision_path_conflict")
                roles.update(endpoints)
            if len(roles) != 1:
                raise MemoryValidationError("decision_path_conflict")
            direction = roles.pop()
        else:
            direction = binding["direction"]
            if any(t[1] == step["relation"] and (t[0] in frontier or t[2] in frontier)
                   for t in graph):
                raise MemoryValidationError("decision_path_conflict")
        index = 0 if direction == "outgoing" else 2
        following = set()
        for target in sorted(frontier):
            query = {"target": target, "relation": step["relation"], "direction": direction}
            facts = [t for t in graph if t[index] == target and t[1] == step["relation"]]
            if any(t[0] == t[2] for t in facts):
                raise MemoryValidationError("decision_path_conflict")
            if not facts:
                if any(t[2 - index] == target and t[1] == step["relation"] for t in graph):
                    raise MemoryValidationError("decision_path_conflict")
                missing.append(query)
            required.update(facts)
            following.update(t[2 - index] for t in facts)
            matches.append({"case": "not_conditional", **query, "triplets": facts})
        frontier = following
    return {"description_kind": "nonconditional_path", "path": description,
            "condition_status": "not_conditional", "condition_triplets": [],
            "answer_targets": sorted(frontier), "answer_candidates": sorted(frontier),
            "required_triplets": sorted(required), "missing_relations": missing,
            "query_matches": matches,
            "targets_by_case": {"true": [], "false": [], "not_conditional": sorted(frontier)}}


def derive_decision(description, question, submitted):
    """Validate extraction and derive requirements from actual graph values.

    Args:
        description: Untrusted structured extraction, without evaluated status.
        question: Unmodified original question, used for literal provenance.
        submitted: Full eligible graph, sorted internally for stable integer IDs.

    Returns:
        Internal decision consumed by the unchanged final selection contract.

    Raises:
        MemoryValidationError: Malformed extraction, unsupported condition,
            invalid fact query or conflicting single-value facts.
    """
    description = decode_description(description, question)
    graph = sorted(_freeze_triplets(submitted))
    if description.get("kind") == "nonconditional_path":
        return derive_path_decision(description, graph)
    if not isinstance(description, dict) or set(description) != {"condition", "branches"}:
        raise MemoryValidationError("decision_fields")
    condition = description["condition"]
    if not isinstance(condition, dict) or set(condition) != {"kind", "subject", "relation", "operator", "value", "uniqueness_quote"}:
        raise MemoryValidationError("decision_condition")
    if any(not isinstance(value, str) for value in condition.values()):
        raise MemoryValidationError("decision_condition")
    kind = condition["kind"]
    actual = []
    if kind == "none":
        if any(value for key, value in condition.items() if key != "kind"):
            raise MemoryValidationError("decision_condition")
        status = "not_conditional"
    elif kind == "equals" and condition["operator"] == "eq":
        if any(not condition[key].strip() for key in ("subject", "relation", "value")):
            raise MemoryValidationError("decision_condition")
        components = {value for triple in graph for value in triple}
        if any(condition[key] not in question and condition[key] not in components
               for key in ("subject", "value")):
            raise MemoryValidationError("decision_condition")
        quote = condition["uniqueness_quote"]
        if quote and (not quote.strip() or quote not in question):
            raise MemoryValidationError("decision_condition")
        actual = [triple for triple in graph if triple[:2] ==
                  (condition["subject"], condition["relation"])]
        values = {triple[2] for triple in actual}
        if quote and len(values) > 1:
            raise MemoryValidationError("decision_conflict")
        status = ("true" if condition["value"] in values else
                  "false" if values and quote else "unknown")
    else:
        raise MemoryValidationError("decision_unsupported")

    branches = description["branches"]
    if not isinstance(branches, dict) or set(branches) != set(CASES):
        raise MemoryValidationError("decision_targets")
    components = {value for triple in graph for value in triple}
    for case, branch in branches.items():
        if not isinstance(branch, dict) or set(branch) != {"targets", "fact_queries"}:
            raise MemoryValidationError("decision_fields")
        _string_array(branch["targets"], "decision_strings")
        if any(value not in question and value not in components for value in branch["targets"]):
            raise MemoryValidationError("decision_target_membership")
        inactive = case != "not_conditional" if kind == "none" else case == "not_conditional"
        if inactive and any(branch.values()):
            raise MemoryValidationError("decision_targets")
        queries = branch["fact_queries"]
        if not isinstance(queries, list):
            raise MemoryValidationError("decision_queries")
        if not inactive and (not branch["targets"] or not queries):
            raise MemoryValidationError("decision_queries")
        resolved = [resolve_query(query, graph) for query in queries]
        for query in resolved:
            if query["target"] not in branch["targets"]:
                raise MemoryValidationError("decision_queries")
        if set(branch["targets"]) != {query["target"] for query in resolved}:
            raise MemoryValidationError("decision_query_coverage")
        branches[case] = {**branch, "fact_queries": resolved}
    active = ("true", "false") if status == "unknown" else (status,)
    required = set(actual)
    targets, missing, matches = [], [], []
    for case in active:
        branch = branches[case]
        targets.extend(branch["targets"])
        for query in branch["fact_queries"]:
            index = 0 if query["direction"] == "outgoing" else 2
            facts = [triple for triple in graph if triple[index] == query["target"]
                     and triple[1] == query["relation"]]
            matches.append({"case": case, **query, "triplets": facts})
            required.update(facts)
            if not facts:
                missing.append(dict(query))
    # Keep condition uncertainty separate from object-specific answer gaps.
    # A known publisher must not erase another target's missing publisher fact.
    if kind == "equals":
        if status == "unknown":
            missing.append({"target": condition["subject"], "relation": condition["relation"],
                            "direction": "outgoing", "reason": "condition_unknown"})
    return {"condition_status": status, "condition_triplets": actual,
            "answer_targets": list(dict.fromkeys(targets)),
            "required_triplets": sorted(required),
            "missing_relations": missing, "query_matches": matches,
            "targets_by_case": {case: branches[case]["targets"] for case in CASES}}


def request_evidence_decision(question, submitted, client):
    """Request one extraction and derive a decision without publishing state.

    Args:
        question: Original question, preserved verbatim.
        submitted: Complete union of memory and eligible new evidence.
        client: Caller-owned client retaining transport retries and accounting.

    Returns:
        Validated decision containing only actual graph evidence.

    Raises:
        MemoryValidationError: Invalid envelope, extraction or graph evaluation.
    """
    graph = sorted(_freeze_triplets(submitted))
    prompt = DECISION_INSTRUCTIONS + "\nOriginal question: " + question
    prompt += "\nNumbered knowledge: " + json.dumps(list(enumerate(graph)), ensure_ascii=False)
    completion = client.complete([{"role": "user", "content": prompt}],
                                 response_format=DECISION_RESPONSE_FORMAT)
    try:
        choice = completion["choices"][0]
        message = choice["message"]
        if choice["finish_reason"] != "stop" or message.get("refusal"):
            raise ValueError
        description = json.loads(message["content"], object_pairs_hook=_unique_object,
                                 parse_constant=_reject_constant)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, RecursionError):
        raise MemoryValidationError("decision_completion") from None
    return derive_decision(description, question, graph)


def missing_relation_queries(missing):
    """Render stable open-value retrieval questions from derived gaps.

    Args:
        missing: Validated target, relation and direction records from evaluation.

    Returns:
        Ordered unique question strings, safely quoting literal graph names.
    """
    queries = []
    for gap in missing:
        target = json.dumps(gap["target"], ensure_ascii=False)
        relation = json.dumps(gap["relation"], ensure_ascii=False)
        query = (f"Which entity does {target} connect to through relation {relation}?"
                 if gap["direction"] == "outgoing" else
                 f"Which entity connects to {target} through relation {relation}?")
        if query not in queries:
            queries.append(query)
    return queries


def decision_response_format(decision, base):
    """Reuse the accepted strict schema while enforcing queries locally.

    Args:
        decision: Derived decision; gaps affect prompts and local validation only.
        base: Shared final response format, preserved for every request.

    Returns:
        Unchanged base schema without a dynamic query enumeration.
    """
    return base


def validate_decision_response(decision, response):
    """Reject stage contradictions before constructing any replacement snapshot.

    Args:
        decision: Derived requirements over the full submitted graph.
        response: Final output already checked by the strict memory parser.

    Raises:
        MemoryValidationError: Missing evidence, premature answer or unresolved
            final output despite a gap-free applicable decision.
    """
    if not _freeze_triplets(decision["required_triplets"]) <= frozenset(response.selected_triplets):
        raise MemoryValidationError("decision_required_omitted")
    if decision["condition_status"] == "unknown" and response.predict_answer:
        raise MemoryValidationError("decision_unknown_answer")
    if decision.get("description_kind") == "nonconditional_path":
        if decision["missing_relations"] and response.predict_answer:
            raise MemoryValidationError("decision_path_incomplete")
        if any(answer not in decision["answer_candidates"] for answer in response.predict_answer):
            raise MemoryValidationError("decision_path_answer")
    if decision["missing_relations"]:
        allowed = missing_relation_queries(decision["missing_relations"])
        questions = response.subsequent_question
        required = missing_relation_queries([gap for gap in decision["missing_relations"]
                                            if gap.get("reason") == "condition_unknown"])
        if (not questions or len(set(questions)) != len(questions)
                or any(q not in allowed for q in questions)
                or not set(required) <= set(questions)):
            raise MemoryValidationError("decision_questions")
    if decision["condition_status"] != "unknown" and not decision["missing_relations"]:
        if not response.predict_answer:
            raise MemoryValidationError("decision_answer_required")
        if response.subsequent_question:
            raise MemoryValidationError("decision_unnecessary_questions")
