"""Request a separate evidence decision for the opt-in MSM candidate.

The original question and full submitted graph remain visible to both stages.
This module never publishes memory, rejects evidence, or repairs model output.
"""

import json

from src.agent.memory_response import _unique_object, _reject_constant, _string_array
from src.agent.memory_state import _freeze_triplets
from src.agent.validation_error import MemoryValidationError


DECISION_INSTRUCTIONS = """Decide evidence requirements before memory selection.
Return only a JSON object with exactly these fields:
condition_status: one of "true", "false", "unknown", "not_conditional";
condition_triplets: exact input triples establishing the condition decision;
targets_by_case: object with exactly true, false, not_conditional keys, each a
string array of the objects whose requested facts matter in that branch;
required_triplets: exact input triples that must survive memory selection;
missing_relations: string array describing factual relations still needed.
Use only the original question and supplied facts. Distinguish evidence that a
condition is false from absence of evidence that it is true. An explicit unique
value premise in the question may be combined with an actual value in the graph;
do not assume a closed world or restrict alternative values to a known example.
Identify each branch from the original question regardless of condition_status.
The true array contains only the if-branch targets; false contains only the
otherwise-branch targets. Each branch may have multiple targets. For conditional
questions not_conditional must be empty. For nonconditional questions true and
false must be empty and not_conditional contains the requested targets.
Targets must occur verbatim in the question or be exact graph components;
they need not yet have factual evidence and are not final answers.
If the condition is unknown, retain all still possible targets'
available relevant evidence in required_triplets. Include condition evidence and
the applicable answer path in required_triplets when the condition is resolved.
Report missing relations rather than guessing facts. Do not rewrite the question,
trim the graph, invent triples, or answer using outside knowledge.
"""


def request_evidence_decision(question, submitted, client):
    """Obtain a structurally checked decision without changing the snapshot.

    Args:
        question: Original question, copied without rewriting.
        submitted: Complete union of historical and eligible new triples.
        client: Caller-owned client, retaining its normal accounting policy.

    Returns:
        JSON-compatible decision with literal graph members and string arrays.

    Raises:
        ValueError: Completion or decision is malformed; no repair is attempted.
    """
    graph = _freeze_triplets(submitted)
    prompt = DECISION_INSTRUCTIONS + "\nOriginal question: " + question
    prompt += "\nKnowledge: " + json.dumps(sorted(graph), ensure_ascii=False)
    completion = client.complete([{"role": "user", "content": prompt}])
    try:
        choice = completion["choices"][0]
        message = choice["message"]
        if choice["finish_reason"] != "stop" or message.get("refusal"):
            raise MemoryValidationError("decision_completion")
        decision = json.loads(message["content"], object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (KeyError, IndexError, TypeError, AttributeError, ValueError, RecursionError):
        raise MemoryValidationError("decision_completion") from None
    fields = {"condition_status", "condition_triplets", "targets_by_case",
              "required_triplets", "missing_relations"}
    if not isinstance(decision, dict) or set(decision) != fields:
        raise MemoryValidationError("decision_fields")
    if decision["condition_status"] not in ("true", "false", "unknown", "not_conditional"):
        raise MemoryValidationError("decision_status")
    for field in ("condition_triplets", "required_triplets"):
        if not isinstance(decision[field], list):
            raise MemoryValidationError("decision_triplets")
        try:
            evidence = _freeze_triplets(decision[field])
        except ValueError:
            raise MemoryValidationError("decision_triplets") from None
        if not evidence <= graph:
            raise MemoryValidationError("decision_membership")
    _string_array(decision["missing_relations"], "decision_strings")
    targets = decision["targets_by_case"]
    if not isinstance(targets, dict) or set(targets) != {"true", "false", "not_conditional"}:
        raise MemoryValidationError("decision_targets")
    components = {value for triple in graph for value in triple}
    for values in targets.values():
        _string_array(values, "decision_strings")
        if any(value not in question and value not in components for value in values):
            raise MemoryValidationError("decision_target_membership")
    status = decision["condition_status"]
    if (status == "not_conditional" and (targets["true"] or targets["false"])) or (
        status != "not_conditional" and targets["not_conditional"]
    ):
        raise MemoryValidationError("decision_targets")
    # Resolve branch membership in code, including stable unknown-case deduplication.
    applicable = targets["true"] + targets["false"] if status == "unknown" else targets[status]
    decision["answer_targets"] = list(dict.fromkeys(applicable))
    if not _freeze_triplets(decision["condition_triplets"]) <= _freeze_triplets(
        decision["required_triplets"]
    ):
        raise MemoryValidationError("decision_condition_retention")
    return decision


def validate_decision_response(decision, response):
    """Reject contradictions before any memory or rejection is constructed.

    Args:
        decision: Validated first-stage record over the full submitted graph.
        response: Final output already checked by parse_memory_response.

    Raises:
        MemoryValidationError: Required evidence is omitted, an unknown condition
            is answered, or a resolved gap-free decision remains unanswered or
            asks further questions. No semantic entailment is established here.
    """
    required = _freeze_triplets(decision["required_triplets"])
    if not required <= frozenset(response.selected_triplets):
        raise MemoryValidationError("decision_required_omitted")
    if decision["condition_status"] == "unknown":
        if response.predict_answer:
            raise MemoryValidationError("decision_unknown_answer")
    elif not decision["missing_relations"]:
        if not response.predict_answer:
            raise MemoryValidationError("decision_answer_required")
        if response.subsequent_question:
            raise MemoryValidationError("decision_unnecessary_questions")
