"""Request a separate evidence decision for the opt-in MSM candidate.

The original question and full submitted graph remain visible to both stages.
This module never publishes memory, rejects evidence, or repairs model output.
"""

import json

from src.agent.memory_response import _unique_object, _reject_constant, _string_array
from src.agent.memory_state import _freeze_triplets


DECISION_INSTRUCTIONS = """Decide evidence requirements before memory selection.
Return only a JSON object with exactly these fields:
condition_status: one of "true", "false", "unknown", "not_conditional";
condition_triplets: exact input triples establishing the condition decision;
answer_targets: string array identifying the objects whose requested facts matter;
required_triplets: exact input triples that must survive memory selection;
missing_relations: string array describing factual relations still needed.
Use only the original question and supplied facts. Distinguish evidence that a
condition is false from absence of evidence that it is true. An explicit unique
value premise in the question may be combined with an actual value in the graph;
do not assume a closed world or restrict alternative values to a known example.
Targets may be copied verbatim from the question, but are not final answers.
If the condition is unknown, list all still possible targets and retain their
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
            raise ValueError("Evidence decision completion not accepted")
        decision = json.loads(message["content"], object_pairs_hook=_unique_object,
                              parse_constant=_reject_constant)
    except (KeyError, IndexError, TypeError, AttributeError, json.JSONDecodeError):
        raise ValueError("Invalid evidence decision completion") from None
    fields = {"condition_status", "condition_triplets", "answer_targets",
              "required_triplets", "missing_relations"}
    if not isinstance(decision, dict) or set(decision) != fields:
        raise ValueError("Invalid evidence decision fields")
    if decision["condition_status"] not in ("true", "false", "unknown", "not_conditional"):
        raise ValueError("Invalid condition status")
    for field in ("condition_triplets", "required_triplets"):
        if not isinstance(decision[field], list):
            raise ValueError("Decision triples must be arrays")
        if not _freeze_triplets(decision[field]) <= graph:
            raise ValueError("Decision evidence must match submitted triples")
    for field in ("answer_targets", "missing_relations"):
        _string_array(decision[field], "question_array")
    return decision
