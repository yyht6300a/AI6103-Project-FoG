"""Extract conditional descriptions, then evaluate them against the full graph.

Only the explicit candidate uses this protocol. Intermediate evidence never
publishes memory; final selection remains subject to the original strict parser.
"""

import json

from src.agent.memory_response import _unique_object, _reject_constant, _string_array
from src.agent.memory_state import _freeze_triplets
from src.agent.validation_error import MemoryValidationError


CASES = ("true", "false", "not_conditional")
DECISION_INSTRUCTIONS = """Extract the original question's condition and branch requirements.
Return only the specified JSON. Do not evaluate the condition yourself.
condition.kind is equals for a single relation equality, none for a nonconditional
question, unsupported for any other condition. subject and value must occur
verbatim in the original question or graph; relation is the exact graph relation
name (if absent, describe the required relation). operator is eq. uniqueness_quote
is the exact original-question passage explicitly establishing a single value,
or empty if absent. For none, all other condition strings must be empty.
The comparison value is a query constraint, NEVER an observed fact.
For each branch supply targets (entity names, not questions or predicted answers)
and fact_queries. Each query has target, relation, direction (outgoing means
target is head; incoming means target is tail). Describe every target's required
one-hop fact even when absent from the graph. Use exact graph relation names
when present. Never supply predicted values or select evidence. Both conditional
branches must have nonempty targets and queries; not_conditional must be empty.
For none, only not_conditional is active. Every target needs a query, and every
query target must belong to its branch. Multi-hop, aggregation and complex
requirements are unsupported: use direction unsupported to fail explicitly.
Code will query the COMPLETE graph for condition facts and select the branch.
"""


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
    "required": ["targets", "fact_queries"],
    "properties": {"targets": STRINGS, "fact_queries": {
        "type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["target", "relation", "direction"],
        "properties": {"target": STRING, "relation": STRING, "direction": {
            "type": "string", "enum": ["outgoing", "incoming", "unsupported"]}}}}},
}
DECISION_RESPONSE_FORMAT = {"type": "json_schema", "json_schema": {
    "name": "evidence_description", "strict": True,
    "schema": {"type": "object", "additionalProperties": False,
               "required": ["condition", "branches"],
               "properties": {"condition": CONDITION_SCHEMA, "branches": {
                   "type": "object", "additionalProperties": False,
                   "required": list(CASES),
                   "properties": {case: BRANCH_SCHEMA for case in CASES}}}},
}}


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
    graph = sorted(_freeze_triplets(submitted))
    if not isinstance(description, dict) or set(description) != {"condition", "branches"}:
        raise MemoryValidationError("decision_fields")
    condition = description["condition"]
    if not isinstance(condition, dict) or set(condition) != set(CONDITION_SCHEMA["required"]):
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
        if not isinstance(branch, dict) or set(branch) != set(BRANCH_SCHEMA["required"]):
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
        for query in queries:
            if (not isinstance(query, dict) or set(query) != {"target", "relation", "direction"}
                    or any(not isinstance(v, str) or not v.strip() for v in query.values())
                    or query["direction"] not in ("incoming", "outgoing")
                    or query["target"] not in branch["targets"]):
                raise MemoryValidationError("decision_queries")
        if set(branch["targets"]) != {query["target"] for query in queries}:
            raise MemoryValidationError("decision_query_coverage")
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
    if decision["condition_status"] == "unknown":
        if response.predict_answer:
            raise MemoryValidationError("decision_unknown_answer")
    elif not decision["missing_relations"]:
        if not response.predict_answer:
            raise MemoryValidationError("decision_answer_required")
        if response.subsequent_question:
            raise MemoryValidationError("decision_unnecessary_questions")
