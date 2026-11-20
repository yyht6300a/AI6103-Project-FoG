"""Run bounded live acceptance cases through Member D's public interfaces.

Use --list-cases for a free preview, or select a suite for paid
requests. Synthetic retrieval inputs replace C/E only; HTTP and model responses
are real. Reports under tmp contain public fixtures and validated outputs, never
credentials, raw failed responses or exception messages.
"""

import argparse
from collections import Counter
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# Support both python scripts/validate_part_d.py and python -m scripts.validate_part_d.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.validation_error import failure_diagnostics
from src.agent.evidence_decision import decode_description, derive_decision
from src.agent.evidence_decision import validate_decision_response
from src.agent.memory_response import parse_memory_response
from src.agent.memory_response import _unique_object, _reject_constant
from src.agent.llm_client import LLMClient
from src.agent.llm_config import load_llm_config
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from src.agent.question_decomposition import decompose_question


QUESTION = "Which city was the author of The Glass Orchard born in?"
AUTHOR = ("The Glass Orchard", "written_by", "Mira Vale")
BIRTH = ("Mira Vale", "born_in", "Linden City")
DISTRACTOR = ("The Copper Harbor", "written_by", "Nolan Reed")
SINGLE_PATH_CASES = (
    ("sufficient", [AUTHOR, BIRTH], [0.95, 0.95]),
    ("insufficient", [AUTHOR], [0.95]),
    ("empty", [], []),
)
PATH_CASE_NAMES = {f"single.{name}" for name, _, _ in SINGLE_PATH_CASES} | {
    "multi.partial", "multi.recovery", "multi.exclusion_probe",
}
# Missing relations may use these explicit semantic aliases, never keyword matches.
AUTHOR_RELATIONS = ("written_by", "author", "authored_by")
BIRTH_RELATIONS = ("born_in", "birthplace", "place_of_birth")
# The shared conditional question compares one verbatim publisher value.
CONDITION_RELATIONS = ("publisher", "published_by")
CONDITION_VALUE = "Silver Press"
BRANCH_QUESTION = (
    "The Glass Orchard has exactly one publisher. If it was published by Silver "
    "Press, which city was Mira Vale born in? Otherwise, which city was Nolan Reed "
    "born in? If the publisher is unknown, ask for it before answering."
)
PUBLISHER = ("The Glass Orchard", "published_by", "Bronze Press")
OTHER_BIRTH = ("Nolan Reed", "born_in", "Cedar City")
RANKING_QUESTION = (
    "Which cities are listed as confirmed or possible birthplaces of Mira Vale? "
    "Include both confirmed and possible birthplaces."
)
CONFIRMED_BIRTH = ("Mira Vale", "confirmed_birthplace", "Linden City")
POSSIBLE_BIRTH = ("Mira Vale", "reported_possible_birthplace", "Cedar City")
RESIDENCE = ("Mira Vale", "lived_in", "Harbor City")
CASES = {
    "conditional": [
        (f"{branch}_{stage}", expected)
        for branch in ("silver", "bronze")
        for stage, expected in (
            ("full", "Independent full graph: resolve branch, retain its path and answer."),
            ("unknown", "Fresh state: retain both births; ask only for the publisher."),
            ("resolved", "Inherit state, add publisher: answer and reject only the other birth."),
            ("probe", "Re-submit all at 0.95: rejected birth stays filtered; answer remains."),
        )
    ],
    "reference_diagnostic": [
        ("paper", "Review each baseline sub-question in isolation for entity references."),
        ("explicit_references", "Full entity descriptions without invented author names."),
        ("named_author", "With authorship evidence, ask for Mira Vale's birthplace."),
    ],
    "single": [
        ("decomposition", "Nonempty standalone retrieval questions; review usefulness."),
        ("sufficient", "Retain the two-hop path; answer Linden City from selected evidence."),
        ("insufficient", "Retain AUTHOR, withhold answers and ask only for Mira Vale's birthplace."),
        ("empty", "No selected evidence or answer; ask only for The Glass Orchard's author."),
    ],
    "multi": [
        ("partial", "Keep useful author evidence; birth score 0.7 is hidden, not rejected."),
        ("recovery", "Use prior questions and memory; birth score 0.95 permits recovery."),
        ("exclusion_probe", "Re-submit candidates; previously rejected rows stay excluded."),
        ("independent", "A fresh question run can use evidence excluded in the other run."),
    ],
    "removal": [
        ("unresolved", "Birthplace may support one branch; ask which publisher applies."),
        ("resolved", "Bronze Press activates the other branch; observe old evidence removal."),
        ("exclusion_probe", "Removed evidence stays masked when re-submitted at high score."),
    ],
    "empty_diagnostic": [
        ("probe", "Same conditional question with empty knowledge; diagnose strict validation."),
    ],
    "clarification_diagnostic": [
        (f"{variant}_{stage}", expected)
        for variant in ("paper", "retrieval_questions")
        for stage, expected in (
            ("empty", "Ask for the book's author, not content or information availability."),
            ("author", "Keep author evidence and ask for the named author's birth city."),
            ("complete", "Keep the full path, answer Linden City and ask no further questions."),
        )
    ],
    "ranking": [
        ("equal_support", "Both authors' cities have equal direct support; either order is valid."),
        ("unequal_support", "Keep both birthplace candidates; confirmed precedes possible; residence is irrelevant."),
    ],
}


def case_plan(suite: str, prompt_variant: str = "evidence_decision", cases=None) -> list[dict]:
    """List the fixed calls and expectations without loading private settings.

    Args:
        suite: CLI-validated acceptance or diagnostic selection. Multihop reuses
            exactly the six path cases, without decomposition or isolation.
            Conditional full graphs/chains are separate from the original
            thirteen-case all.
        prompt_variant: Default decision pipeline or explicit comparison variant.
        cases: Optional exact case identifiers; dependency chains must be complete.

    Returns:
        Ordered cases with per-case logical request limits. Every memory round
        permits decision, selection and ranking if it returns multiple answers;
        decomposition permits one request. Each has YAML transport retries.
    """
    groups = (("single", "multi", "removal", "ranking") if suite == "all"
              else ("single", "multi") if suite == "multihop" else (suite,))
    memory_limit = 3 if prompt_variant == "evidence_decision" else 2
    plan = [{"case": f"{group}.{name}", "expected": expected,
             "logical_call_limit": 1 if (group == "single" and name == "decomposition")
                 or (group == "reference_diagnostic" and name != "named_author")
                 else 2 if group == "clarification_diagnostic" else memory_limit}
            for group in groups for name, expected in CASES[group]
            if suite != "multihop" or f"{group}.{name}" in PATH_CASE_NAMES]
    if cases is None:
        return plan
    selected = set(cases)
    available = {item["case"] for item in plan}
    if not selected <= available:
        raise ValueError("Case must belong to selected suite")
    chains = [ [f"{group}.{stage}" for stage in stages] for group, stages in (
        ("multi", ("partial", "recovery", "exclusion_probe")),
        ("removal", ("unresolved", "resolved", "exclusion_probe")),
        *(("conditional", tuple(f"{branch}_{s}" for s in ("unknown", "resolved", "probe")))
          for branch in ("silver", "bronze")),
    )]
    for chain in chains:
        for index, name in enumerate(chain):
            if name in selected and not set(chain[:index]) <= selected:
                raise ValueError("Select preceding dependency rounds too")
    if suite not in ("multihop", "conditional"):
        raise ValueError("Case selection supports multihop and conditional suites only")
    return [item for item in plan if item["case"] in selected]


def snapshot(state: QuestionMemory) -> dict:
    """Serialize immutable state deterministically for human comparison.

    Args:
        state: One independent question run's current snapshot.

    Returns:
        Original question and sorted memory, rejection and exclusion graphs.
    """
    return {
        "question": state.original_question,
        "memory": sorted(state.memory_triplets),
        "rejected": sorted(state.rejected_triplets),
        "excluded": sorted(state.excluded_triplets),
    }


def begin_case(report: dict, name: str) -> dict:
    """Reserve one planned call before any public API can send a request.

    Args:
        report: Mutable run report with the fixed plan and accumulating results.
        name: Unique planned case identifier.

    Returns:
        Mutable case record, initially marked started for failure localization.

    Raises:
        RuntimeError: A caller attempts an unplanned, duplicate or excess call.
    """
    planned = {case["case"]: case for case in report["plan"]}
    if name not in planned or any(case["case"] == name for case in report["results"]):
        raise RuntimeError("Unplanned or repeated acceptance call")
    record = dict(planned[name], status="started", logical_calls_started=0)
    report["results"].append(record)
    return record


class CountingClient:
    """Count logical requests per case without changing transport or logging.

    The underlying client remains caller-owned. Retries within complete are
    counted by its CSV, while this wrapper counts selection and ranking stages.
    """

    def __init__(self, client: LLMClient, record: dict) -> None:
        """Bind a case-local counter and the caller-owned client.

        Args:
            client: Configured client receiving unchanged messages.
            record: Case telemetry updated before every logical request.
        """
        self.client = client
        self.record = record
        self.calls = []
        record["logical_calls_started"] = 0
        record["logical_calls_received"] = 0

    def complete(self, messages: list[dict], **options) -> dict:
        """Count one request before forwarding, including terminal failures.

        Args:
            messages: Unchanged selection or ranking messages.
            **options: Explicit structured-output settings forwarded unchanged.

        Returns:
            Raw completion from the underlying client without output repair.

        Raises:
            RuntimeError: The case would exceed its declared logical budget.
            Exception: Any underlying client error, propagated unchanged.
        """
        if self.record["logical_calls_started"] >= self.record["logical_call_limit"]:
            raise RuntimeError("Case logical request budget exhausted")
        self.record["logical_calls_started"] += 1
        self.record["stage_progress"] = [
            {"stage": stage, "started": i < self.record["logical_calls_started"],
             "received": i < len(self.calls), "transaction_validated": False}
            for i, stage in enumerate(("extraction", "selection", "ranking"))]
        completion = self.client.complete(messages, **options)
        self.calls.append({"messages": messages, "options": options, "completion": completion})
        self.record["logical_calls_received"] = len(self.calls)
        self.record["stage_progress"][len(self.calls) - 1]["received"] = True
        return completion


def safe_payload(call: dict) -> dict:
    """Decode only finished, non-refused bounded strict JSON completions.

    Args:
        call: Captured transport result, never serialized on failure.

    Returns:
        Decoded object for subsequent vocabulary checks.

    Raises:
        ValueError: Envelope or content cannot be safely inspected.
    """
    choice = call["completion"]["choices"][0]
    message = choice["message"]
    if choice.get("finish_reason") != "stop" or message.get("refusal"):
        raise ValueError("Unavailable envelope")
    content = message["content"]
    if not isinstance(content, str) or len(content) > 12000:
        raise ValueError("Unavailable content")
    return json.loads(content, object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)


def extraction_projection(call: dict, state: QuestionMemory, submitted: set) -> dict:
    """Keep bounded, validated fixture fields after a failed extraction.

    Args:
        call: First captured completion; raw text never enters the report.
        state: Original question used by the production description decoder.
        submitted: Controlled graph, also used to annotate exact endpoint hits.

    Returns:
        A safe description and matching graph IDs, or a fixed unavailable marker.
        Strings must belong to the fixture vocabulary or original question.
    """
    unavailable = {"status": "unavailable"}
    try:
        payload = safe_payload(call)
        decode_description(payload, state.original_question, allow_duplicate_entities=True)
        description = payload["description"]
        vocabulary = {part for row in submitted for part in row} | {
            "nonconditional_path", "nonconditional", "conditional_equals", "unsupported",
            "incident", "observed", "missing", "outgoing", "incoming", "eq", "written_by",
            "born_in", "author", "authored_by", "birthplace", "place_of_birth",
            "published_by", "publisher", "",
        }
        def bounded(value, depth=0):
            """Check nested values against public fixture strings and size bounds.

            Args:
                value: Decoded schema-validated JSON subtree.

            Returns:
                Whether every string is safe to retain in the private report.
            """
            if depth > 12:
                return False
            if isinstance(value, str):
                return len(value) <= 512 and (value in vocabulary or value in state.original_question)
            if isinstance(value, dict):
                keys = {"kind", "branch", "branches", "requirements", "entity",
                        "fact_queries", "relation", "binding", "evidence_ids",
                        "direction", "root_entity", "steps", "condition",
                        "subject", "operator", "value", "condition_quote",
                        "uniqueness_quote", "true", "false", "quote"}
                return len(value) <= 16 and set(value) <= keys and all(
                    bounded(v, depth + 1) for v in value.values())
            if isinstance(value, list):
                return len(value) <= 32 and all(bounded(v, depth + 1) for v in value)
            return type(value) is int and 0 <= value <= 1000000
        if not bounded(description):
            return unavailable
        hits = []
        branches = description.get("branches", {"branch": description.get("branch", {})})
        for branch, body in branches.items():
            for group in body.get("requirements", []):
                for query in group["fact_queries"]:
                    hits.append({"branch": branch, "entity": group["entity"],
                                 "relation": query["relation"], "graph_ids": [
                                     i for i, row in enumerate(sorted(submitted))
                                     if row[1] == query["relation"] and group["entity"] in (row[0], row[2])]})
        ids = []
        def collect(value):
            """Collect bounded evidence identifiers from the safe description.

            Args:
                value: Already bounded subtree; identifiers remain unchanged.
            """
            if isinstance(value, dict):
                ids.extend(value.get("evidence_ids", []))
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)
        collect(description)
        return {"status": "validated_projection", "description": description, "hits": hits,
                "production_validated": False, "out_of_range_ids": [i for i in ids if i >= len(submitted)]}
    except (KeyError, IndexError, TypeError, ValueError, RecursionError):
        return unavailable


def conditional_failure_projection(calls: list, state: QuestionMemory, submitted: set) -> dict:
    """Retain bounded validated fields from already captured conditional replies.

    Args:
        calls: Completed stage records; no further requests are made.
        state: Unchanged prior snapshot supplying the original question.
        submitted: Exact controlled graph supplied to the transaction.

    Returns:
        Safe extraction, derived decision and final-output projections. Missing
        or unsafe stages carry fixed markers and never raw failed content.
    """
    unavailable = {"status": "unavailable"}
    result = {"extraction": unavailable, "decision": unavailable, "final": unavailable,
              "ranking": unavailable}
    if calls:
        result["extraction"] = extraction_projection(calls[0], state, submitted)
    if result["extraction"].get("status") == "validated_projection":
        try:
            decision = derive_decision({"description": result["extraction"]["description"]},
                                       state.original_question, submitted)
            result["decision"] = {key: decision[key] for key in (
                "condition_status", "query_matches", "answer_candidates",
                "required_triplets", "missing_relations")}
            result["decision"]["description_kind"] = decision.get("description_kind")
        except Exception:
            pass  # Diagnostic failures must never replace the public exception.
    result["extraction_decodable"] = result["extraction"].get("status") == "validated_projection"
    result["evaluation_succeeded"] = "condition_status" in result["decision"]
    if len(calls) > 1:
        try:
            payload = safe_payload(calls[1])
            content = json.dumps(payload)
            response = parse_memory_response(content, state.memory_triplets, submitted)
            from src.agent.evidence_decision import missing_relation_queries
            allowed = missing_relation_queries(result["decision"]["missing_relations"]) if "condition_status" in result["decision"] else []
            if (len(response.predict_answer) <= 32 and len(response.selected_triplets) <= 32
                    and all(query in allowed for query in response.subsequent_question)):
                result["final"] = {"status": "validated_projection",
                                   "selected_triplets": response.selected_triplets,
                                   "predict_answer": response.predict_answer,
                                   "subsequent_question": response.subsequent_question}
                try:
                    validate_decision_response(result["decision"], response)
                    result["final"]["decision_consistent"] = True
                except Exception:
                    result["final"]["decision_consistent"] = False
        except Exception:
            pass
    if len(calls) > 2 and result["final"].get("status") == "validated_projection":
        try:
            payload = safe_payload(calls[2])
            answers = payload["predict_answer"]
            candidates = result["final"]["predict_answer"]
            if (set(payload) == {"predict_answer"} and isinstance(answers, list)
                    and len(answers) <= 32 and all(isinstance(a, str) and a in candidates for a in answers)):
                result["ranking"] = {"status": "safe_projection", "predict_answer": answers,
                    "multiset_matches": Counter(answers) == Counter(candidates)}
        except Exception:
            pass
    return result


def memory_round(
    client: LLMClient, report: dict, name: str, state: QuestionMemory,
    triples: list, scores: list, queries: tuple | list,
    *, prompt_variant: str | None = None,
):
    """Run public MSM with mandatory ranking of multiple candidate answers.

    Args:
        client: Real configured client; its retry policy remains unchanged.
        report: Mutable report; failure records expose exception classes and fixed validation diagnostics.
        name: Planned case identifier for this default manage_memory call.
        state: Prior question-local immutable snapshot.
        triples: Controlled retrieval rows, including exclusion probes.
        scores: Positionally aligned controlled scores; cutoff stays 0.7.
        queries: Actual initial or preceding model questions driving this fixture.
        prompt_variant: Explicit comparison override, otherwise the report's
            selected variant (default evidence_decision).

    Returns:
        Validated MSM result with a new state and ordered response.

    Raises:
        Exception: Public API failure, recorded and propagated without retries
            beyond LLMClient's configured transport policy.
    """
    prompt_variant = prompt_variant or report.get("prompt_variant", "evidence_decision")
    record = begin_case(report, name)
    before = snapshot(state)
    eligible = state.filter_expansion_triplets(triples)
    submitted = state.memory_triplets | {
        tuple(row) for row, score in zip(triples, scores)
        if score > 0.7 and tuple(row) in eligible
    }
    record.update(queries=queries, triples=triples, scores=scores, threshold=0.7,
                  prompt_variant=prompt_variant,
                  ranking_stage_enabled=True,
                  state_before=before, submitted=sorted(submitted))
    counted = CountingClient(client, record)
    try:
        result = manage_memory(state, triples, scores, counted, potential_threshold=0.7,
                               prompt_variant=prompt_variant)
    except Exception as error:
        record.update(status="error", error_type=type(error).__name__,
                      production_validated=False, controlled_validated=False,
                      **failure_diagnostics(error),
                      state_after=snapshot(state), old_state_unchanged=snapshot(state) == before)
        if prompt_variant == "evidence_decision":
            try:
                record["failure_projection"] = conditional_failure_projection(counted.calls, state, submitted)
                record["failed_extraction"] = record["failure_projection"]["extraction"]
            except Exception:
                record["failure_projection"] = {"status": "unavailable"}
        record["local_validation"] = {
            "extraction": record.get("failure_projection", {}).get("evaluation_succeeded", False),
            "selection": record.get("failure_projection", {}).get("final", {}).get("decision_consistent", False),
            "ranking": record.get("failure_projection", {}).get("ranking", {}).get("multiset_matches", False),
        }
        locations = []
        trace = error.__traceback__
        while trace is not None:
            if trace.tb_frame.f_code.co_name in {"derive_decision", "derive_path_decision", "resolve_query", "validate_decision_response"}:
                locations.append({"function": trace.tb_frame.f_code.co_name, "line": trace.tb_lineno})
            trace = trace.tb_next
        record["failure_locations"] = locations
        raise

    selected = result.state.memory_triplets
    record["local_validation"] = {"extraction": True, "selection": True,
                                  "ranking": len(counted.calls) == 3}
    for stage in record.get("stage_progress", []):
        stage["transaction_validated"] = stage["received"]
    record.update(
        stages=counted.calls,
        status="validated", state_after=snapshot(result.state),
        selected_triplets=result.response.selected_triplets,
        predict_answer=result.response.predict_answer,
        subsequent_question=result.response.subsequent_question,
        old_state_unchanged=snapshot(state) == before,
        state_rules_match=(selected <= submitted
                           and result.state.rejected_triplets
                           == state.rejected_triplets | (submitted - selected)
                           and result.state.excluded_triplets
                           == selected | result.state.rejected_triplets),
        retained_memory=sorted(state.memory_triplets & selected),
        removed_memory=sorted(state.memory_triplets - selected),
        newly_rejected=sorted(result.state.rejected_triplets - state.rejected_triplets),
    )
    record["production_validated"] = True
    record["controlled_validated"] = False
    try:
        if not record["old_state_unchanged"] or not record["state_rules_match"]:
            record["status"] = "state_rule_failure"
            raise RuntimeError("Acceptance state invariant failed")
        if name in PATH_CASE_NAMES and prompt_variant == "evidence_decision":
            check_two_hop(record)
        if name.startswith("ranking.") and prompt_variant == "evidence_decision":
            check_ranking(record)
    except Exception as error:
        record.pop("stages", None)
        if record["status"] == "validated":
            record["status"] = "controlled_check_failure"
        record.update(error_type=type(error).__name__, **failure_diagnostics(error))
        try:
            record["failure_projection"] = conditional_failure_projection(counted.calls, state, submitted)
        except Exception:
            record["failure_projection"] = {"status": "unavailable"}
        raise
    record["controlled_validated"] = True
    return result


def check_ranking(record: dict) -> None:
    """Enforce the two public ranking fixtures before starting another case.

    Args:
        record: Successful transaction with captured original stages.

    Raises:
        RuntimeError: Extraction, completeness, evidence or support order differs.
    """
    equal = record["case"] == "ranking.equal_support"
    required = {BIRTH, OTHER_BIRTH} if equal else {CONFIRMED_BIRTH, POSSIBLE_BIRTH}
    stages = record["stages"]
    description = safe_payload(stages[0])["description"]
    expected = {(t[0], t[1]) for t in required}
    groups = description.get("branch", {}).get("requirements", [])
    observed = {(g["entity"], q["relation"]) for g in groups for q in g["fact_queries"]}
    graph = list(map(tuple, record["submitted"]))
    bindings = all(q["binding"]["kind"] == "observed" and
        all(graph[i][0] == g["entity"] and graph[i][1] == q["relation"]
            for i in q["binding"]["evidence_ids"])
        for g in groups for q in g["fact_queries"])
    candidates = Counter([BIRTH[2], OTHER_BIRTH[2]])
    selection = safe_payload(stages[1])
    from src.agent.memory_prompt import build_decision_memory_prompt
    from src.agent.answer_ranking import build_ranking_prompt
    question = record["state_before"]["question"]
    decision = derive_decision({"description": description}, question, set(graph))
    memory = set(map(tuple, record["state_before"]["memory"]))
    expected_selection = build_decision_memory_prompt(question, memory, set(graph) - memory, decision)
    expected_ranking = build_ranking_prompt(question, selection["selected_triplets"], selection["predict_answer"])
    answers = record["predict_answer"]
    checks = {
        "independent_extraction": description["kind"] == "nonconditional"
            and observed == expected and bindings
            and len(groups) == (2 if equal else 1)
            and sum(len(g["fact_queries"]) for g in groups) == 2,
        "exact_evidence": set(map(tuple, record["selected_triplets"])) == required,
        "complete_answers": Counter(record["predict_answer"]) == candidates,
        "no_questions": not record["subsequent_question"],
        "rejection": set(map(tuple, record["state_after"]["rejected"])) == (set() if equal else {RESIDENCE}),
        "ranking_executed": len(stages) == 3,
        "selection_complete": Counter(selection["predict_answer"]) == candidates,
        "derived_candidates": Counter(decision["answer_candidates"]) == candidates,
        "derived_required": set(map(tuple, decision["required_triplets"])) == required,
        "selection_input": stages[1]["messages"] == [{"role": "user", "content": expected_selection}],
        "ranking_input": len(stages) == 3 and stages[2]["messages"] == [{"role": "user", "content": expected_ranking}],
        "ranking_multiset": len(stages) == 3 and Counter(safe_payload(stages[2])["predict_answer"]) == Counter(selection["predict_answer"]) == Counter(answers),
        "support_order": equal or (BIRTH[2] in answers and OTHER_BIRTH[2] in answers
            and answers.index(BIRTH[2]) < answers.index(OTHER_BIRTH[2])),
        "old_state_unchanged": record["old_state_unchanged"],
    }
    record["controlled_checks"] = checks
    if not all(checks.values()):
        record["status"] = "controlled_check_failure"
        raise RuntimeError("Ranking acceptance expectation failed")


def check_two_hop(record: dict) -> None:
    """Gate the shared six path fixtures before a returned state is inherited.

    Args:
        record: Completed public transaction, including original stage requests
            and completions. Checks and natural-rejection coverage are appended.

    Raises:
        RuntimeError: Any fixture expectation fails. Optional distractor selection
            is never a failure; filtering is claimed only for prior rejection.
    """
    name = record["case"]
    complete = name in ("single.sufficient", "multi.recovery", "multi.exclusion_probe")
    empty = name == "single.empty"
    required = set() if empty else {AUTHOR, BIRTH} if complete else {AUTHOR}
    selected = set(map(tuple, record["selected_triplets"]))
    before_rejected = set(map(tuple, record["state_before"]["rejected"]))
    submitted = set(map(tuple, record["submitted"]))
    after_rejected = set(map(tuple, record["state_after"]["rejected"]))
    target, relations = (AUTHOR[0], AUTHOR_RELATIONS) if empty else (BIRTH[0], BIRTH_RELATIONS)
    allowed = {f'Find triples involving "{target}" with relation "{relation}".'
               for relation in relations}
    questions = record["subsequent_question"]
    checks = {
        "required_path": required <= selected,
        "exact_answer": tuple(record["predict_answer"]) == ((BIRTH[2],) if complete else ()),
        "reachable_query": not questions if complete else
            len(questions) == 1 and questions[0] in allowed,
        "empty_selection": not empty or not selected,
        "hidden_not_submitted_or_rejected": name != "multi.partial" or
            BIRTH not in submitted | after_rejected,
        "recovery": name != "multi.recovery" or
            AUTHOR in set(map(tuple, record["state_before"]["memory"])) and BIRTH in selected,
        "prior_rejections_filtered": not before_rejected.intersection(submitted),
    }
    if record["prompt_variant"] == "evidence_decision":
        # Inspect actual requests too: the runner's computed union alone cannot
        # prove that rejected evidence was absent from both model stages.
        stages = record["stages"]
        extraction = json.loads(stages[0]["completion"]["choices"][0]["message"]["content"])
        description = extraction["description"]
        root_relations = AUTHOR_RELATIONS if empty else (AUTHOR[1],)
        end_relations = (BIRTH[1],) if complete else BIRTH_RELATIONS
        steps = description.get("steps", [])
        checks["two_hop_extraction"] = (
            description.get("kind") == "nonconditional_path"
            and description.get("root_entity") == AUTHOR[0] and len(steps) == 2
            and steps[0].get("relation") in root_relations
            and steps[1].get("relation") in end_relations
            and steps[0].get("binding", {}).get("kind") == ("missing" if empty else "observed")
            and steps[1].get("binding", {}).get("kind") == ("observed" if complete else "missing")
        )
        if checks["two_hop_extraction"] and all(checks.values()):
            from src.agent.evidence_decision import derive_decision
            derived = derive_decision(extraction, QUESTION, submitted)
            final_text = stages[1]["messages"][0]["content"]
            active = json.loads(final_text.split(
                "Return only the existing three output fields.\n", 1)[1].split(
                "\nAllowed retrieval questions:", 1)[0])
            checks["derived_path"] = all(active.get(key) == json.loads(json.dumps(derived[key]))
                for key in ("query_matches", "missing_relations", "required_triplets", "answer_candidates"))
            checks["derived_directions"] = all(
                (q.get("direction") == "outgoing" and "scope" not in q)
                if q["triplets"] or "direction" in q else
                (q.get("scope") == "incident" and "direction" not in q)
                for q in derived["query_matches"])
        first_prompt = stages[0]["messages"][0]["content"]
        numbered = json.loads(first_prompt.split("\nNumbered knowledge: ", 1)[1])
        second_prompt = stages[1]["messages"][0]["content"]
        graph = json.loads(second_prompt.split("Knowledge: ", 1)[1].split(
            "\n### Validated Evidence Decision", 1)[0])
        checks["both_stage_graphs"] = (
            numbered == [[i, list(t)] for i, t in enumerate(sorted(submitted))]
            and graph == [list(t) for t in sorted(submitted)]
        )
    record["controlled_checks"] = checks
    record["distractor_rejection"] = {
        "observed_before": DISTRACTOR in before_rejected,
        "observed_after": DISTRACTOR in after_rejected,
        "filtering": "observed" if DISTRACTOR in before_rejected
            and all(checks.values()) else "not_observed",
    }
    if not all(checks.values()):
        record["status"] = "controlled_check_failure"
        raise RuntimeError("Two-hop acceptance expectation failed")


def check_conditional_extraction(record: dict) -> dict:
    """Independently gate both conditional branches before the next round runs.

    Args:
        record: Successful conditional transaction retaining the first-stage
            completion and the exact submitted graph.

    Returns:
        Named boolean expectations. Unreadable content yields one failed check
        rather than a second exception path.

    The gate derives its expectations from fixture constants and ``submitted``,
    so the inactive and active branches share one rule. It proves this fixture's
    extraction fidelity, not a general improvement of live extraction, and it
    never edits, merges or repairs the model reply.
    """
    try:
        description = safe_payload(record["stages"][0])["description"]
    except (KeyError, IndexError, TypeError, ValueError):
        return {"conditional_extraction": False}
    condition = description.get("condition")
    condition = condition if isinstance(condition, dict) else {}
    branches = description.get("branches")
    branches = branches if isinstance(branches, dict) else {}
    graph = [tuple(row) for row in record["submitted"]]

    def branch_ok(case: str, birth: tuple) -> bool:
        """Check one branch's single group, relation alias and binding role.

        Args:
            case: Branch key in the extracted description.
            birth: Expected entity, relation and birthplace city fixture triple.

        Returns:
            Whether the branch binds exactly the requested birthplace fact with
            the question-grounded direction.
        """
        body = branches.get(case)
        groups = body.get("requirements") if isinstance(body, dict) else None
        if not isinstance(groups, list) or len(groups) != 1:
            return False
        group = groups[0]
        queries = group.get("fact_queries")
        if (not isinstance(queries, list) or len(queries) != 1
                or group.get("entity") != birth[0]):
            return False
        query = queries[0]
        relation, binding = query.get("relation"), query.get("binding")
        if relation not in BIRTH_RELATIONS or not isinstance(binding, dict):
            return False
        fact = (birth[0], relation, birth[2])
        if fact not in graph:
            return binding == {"kind": "missing", "direction": "outgoing"}
        ids = binding.get("evidence_ids")
        return (binding.get("kind") == "observed"
                and set(binding) == {"kind", "evidence_ids"}
                and isinstance(ids, list) and bool(ids)
                and all(type(index) is int and 0 <= index < len(graph) for index in ids)
                and all(graph[index] == fact for index in ids))

    return {
        "conditional_kind": description.get("kind") == "conditional_equals",
        "conditional_condition": condition.get("subject") == PUBLISHER[0]
            and condition.get("value") == CONDITION_VALUE
            and condition.get("relation") in CONDITION_RELATIONS,
        "conditional_true_branch": branch_ok("true", BIRTH),
        "conditional_false_branch": branch_ok("false", OTHER_BIRTH),
    }


def run_cases(client: LLMClient, report: dict, suite: str) -> None:
    """Execute fixed single-round and/or adaptive-state multi-round fixtures.

    Args:
        client: Real LLMClient, shared across independent question states.
        report: Mutable report storing validated output for later semantic review.
        suite: Standard acceptance group, focused multihop group, conditional
            full graphs/chains, an explicit diagnostic group, or all. Conditional
            and diagnostics are excluded from all, which retains thirteen cases.

    Multi-round retrieval consumes the preceding response's questions but returns
    controlled rows rather than implementing C/E. Empty questions are recorded
    honestly; the last round is a forced exclusion probe, not a stopping policy.
    Model choices are never replaced to manufacture a desired state transition.
    """
    if suite == "conditional":
        for branch, value, birth, other in (
            ("silver", "Silver Press", BIRTH, OTHER_BIRTH),
            ("bronze", "Bronze Press", OTHER_BIRTH, BIRTH),
        ):
            publisher = (PUBLISHER[0], PUBLISHER[1], value)
            prior = None
            for stage in ("full", "unknown", "resolved", "probe"):
                if f"conditional.{branch}_{stage}" not in {item["case"] for item in report["plan"]}:
                    continue
                state = (QuestionMemory(BRANCH_QUESTION) if stage in ("full", "unknown")
                         else prior.state)
                triples = ([BIRTH, OTHER_BIRTH] if stage == "unknown" else [publisher]
                           if stage == "resolved" else [BIRTH, OTHER_BIRTH, publisher])
                result = memory_round(
                    client, report, f"conditional.{branch}_{stage}", state,
                    triples, [0.95] * len(triples),
                    (BRANCH_QUESTION,) if stage in ("full", "unknown")
                    else prior.response.subsequent_question,
                )
                record = report["results"][-1]
                expected_memory = {BIRTH, OTHER_BIRTH} if stage == "unknown" else {publisher, birth}
                questions = result.response.subsequent_question
                # Match the entire open-value query, not a publisher keyword.
                allowed_queries = {
                    f'Which entity does "The Glass Orchard" connect to through relation "{relation}"?'
                    for relation in ("published_by", "publisher")
                }
                checks = {
                    "memory": result.state.memory_triplets == expected_memory,
                    "answers": result.response.predict_answer == (() if stage == "unknown" else (birth[2],)),
                    "questions": len(questions) == 1 and questions[0] in allowed_queries
                        if stage == "unknown" else not questions,
                    "rejection": result.state.rejected_triplets == (
                        frozenset() if stage == "unknown" else frozenset({other})),
                    "filtered": stage != "probe" or other not in map(tuple, record["submitted"]),
                }
                # The default protocol must describe both branches faithfully,
                # including the branch the current condition does not activate.
                if record["prompt_variant"] == "evidence_decision":
                    checks.update(check_conditional_extraction(record))
                record["controlled_checks"] = checks
                if not all(checks.values()):
                    record["status"] = "controlled_check_failure"
                    record["controlled_validated"] = False
                    raise RuntimeError("Conditional acceptance expectation failed")
                prior = result

    if suite == "clarification_diagnostic":
        for variant in ("paper", "retrieval_questions"):
            for stage, triples in (("empty", []), ("author", [AUTHOR]),
                                   ("complete", [AUTHOR, BIRTH])):
                memory_round(
                    client, report, f"clarification_diagnostic.{variant}_{stage}",
                    QuestionMemory(QUESTION), triples, [0.95] * len(triples),
                    (QUESTION,), prompt_variant=variant,
                )

    if suite == "reference_diagnostic":
        for variant in ("paper", "explicit_references"):
            record = begin_case(report, f"reference_diagnostic.{variant}")
            record.update(question=QUESTION, prompt_variant=variant, logical_calls_started=1)
            questions = decompose_question(QUESTION, client, prompt_variant=variant)
            record.update(status="validated", sub_questions=questions)
        # These are recorded queries, not a substitute for real C/E retrieval.
        memory_round(client, report, "reference_diagnostic.named_author",
                     QuestionMemory(QUESTION), [AUTHOR], [0.95], questions)

    if suite == "empty_diagnostic":
        memory_round(client, report, "empty_diagnostic.probe",
                     QuestionMemory(BRANCH_QUESTION), [], [], (BRANCH_QUESTION,))

    if suite in ("single", "multihop", "all"):
        questions = (QUESTION,)
        if suite != "multihop":
            record = begin_case(report, "single.decomposition")
            record["question"] = QUESTION
            record["logical_calls_started"] = 1
            questions = decompose_question(QUESTION, client)
            record.update(status="validated", sub_questions=questions)
        for name, triples, scores in SINGLE_PATH_CASES:
            if f"single.{name}" not in {item["case"] for item in report["plan"]}:
                continue
            memory_round(client, report, f"single.{name}", QuestionMemory(QUESTION),
                         triples, scores, questions)

    if suite in ("multi", "multihop", "all"):
        if "multi.partial" not in {item["case"] for item in report["plan"]}:
            return
        first = memory_round(
            client, report, "multi.partial", QuestionMemory(QUESTION),
            [AUTHOR, BIRTH, DISTRACTOR], [0.95, 0.7, 0.95], (QUESTION,),
        )
        if "multi.recovery" not in {item["case"] for item in report["plan"]}:
            return
        second = memory_round(
            client, report, "multi.recovery", first.state,
            [AUTHOR, BIRTH, DISTRACTOR], [0.95, 0.95, 0.95],
            first.response.subsequent_question,
        )
        if "multi.exclusion_probe" not in {item["case"] for item in report["plan"]}:
            return
        third = memory_round(
            client, report, "multi.exclusion_probe", second.state,
            [AUTHOR, BIRTH, DISTRACTOR], [0.95, 0.95, 0.95],
            second.response.subsequent_question,
        )
        report["multihop_observations"] = {
            "natural_distractor_rejection": DISTRACTOR in third.state.rejected_triplets,
            "rejected_distractor_filtering": "observed" if any(
                r.get("distractor_rejection", {}).get("filtering") == "observed"
                for r in report["results"]
            ) else "not_observed",
        }
        if suite == "multihop":
            return
        independent = memory_round(
            client, report, "multi.independent",
            QuestionMemory("Who wrote The Copper Harbor?"),
            [DISTRACTOR], [0.95], ("Who wrote The Copper Harbor?",),
        )
        # Observations are coverage evidence, not forced model expectations.
        report["multi_observations"] = {
            "hidden_not_rejected": BIRTH not in first.state.excluded_triplets,
            "hidden_recovered": BIRTH in second.state.memory_triplets,
            "cross_round_retention": bool(first.state.memory_triplets
                                          & second.state.memory_triplets),
            "old_memory_removal": bool((first.state.memory_triplets
                                        - second.state.memory_triplets)
                                       | (second.state.memory_triplets
                                          - third.state.memory_triplets)),
            "submitted_omission": bool(third.state.rejected_triplets),
            "independent_selection": DISTRACTOR in independent.state.memory_triplets,
            "overlapping_rejection_isolation": (
                DISTRACTOR in third.state.rejected_triplets
                and DISTRACTOR in independent.state.memory_triplets),
        }

    if suite in ("removal", "all"):
        first = memory_round(
            client, report, "removal.unresolved", QuestionMemory(BRANCH_QUESTION),
            [BIRTH], [0.95], (BRANCH_QUESTION,),
        )
        second = memory_round(
            client, report, "removal.resolved", first.state,
            [PUBLISHER, OTHER_BIRTH], [0.95, 0.95], first.response.subsequent_question,
        )
        third = memory_round(
            client, report, "removal.exclusion_probe", second.state,
            [BIRTH, PUBLISHER, OTHER_BIRTH], [0.95, 0.95, 0.95],
            second.response.subsequent_question,
        )
        # Branch resolution changes relevance without editing prior model output.
        removed = first.state.memory_triplets - second.state.memory_triplets
        last = report["results"][-1]
        report["removal_observations"] = {
            "initial_branch_retained": BIRTH in first.state.memory_triplets,
            "old_memory_removal": bool(removed),
            "removed_permanently_rejected": bool(removed)
                and removed <= second.state.rejected_triplets & third.state.rejected_triplets,
            "removed_not_resubmitted": bool(removed)
                and not removed.intersection(map(tuple, last["submitted"])),
        }

    if suite in ("ranking", "all"):
        question = "Which cities were Mira Vale and Nolan Reed born in?"
        memory_round(
            client, report, "ranking.equal_support", QuestionMemory(question),
            [BIRTH, OTHER_BIRTH], [0.95, 0.95], (question,),
        )
        # Evidence labels establish qualitative support, not calibrated probabilities.
        # Equal retrieval scores deliberately cannot supply the answer ranking.
        memory_round(
            client, report, "ranking.unequal_support", QuestionMemory(RANKING_QUESTION),
            [POSSIBLE_BIRTH, RESIDENCE, CONFIRMED_BIRTH], [0.95, 0.95, 0.95],
            (RANKING_QUESTION,),
        )


def main(argv: list[str] | None = None) -> int:
    """Preview or execute paid cases, closing the client even after failure.

    Args:
        argv: Optional CLI arguments; defaults to the process argument list.

    Returns:
        Zero for preview or structurally valid execution, one on configuration,
        transport, validation or report failure. Semantic acceptance still needs
        human review and the final GPT-4o-mini gate, even when exit status is zero.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=(*CASES, "multihop", "all"), default="all")
    parser.add_argument(
        "--prompt-variant", default="evidence_decision",
        choices=("evidence_decision", "retrieval_questions", "paper", "answer_ranking"),
    )
    parser.add_argument("--list-cases", action="store_true",
                        help="Print expectations and call counts without loading .env or using API.")
    parser.add_argument("--case", action="append", help="Exact case ID; repeat to select dependencies.")
    args = parser.parse_args(argv)
    if args.suite == "multihop" and args.prompt_variant != "evidence_decision":
        parser.error("multihop requires the default evidence_decision protocol")
    try:
        plan = case_plan(args.suite, args.prompt_variant, args.case)
    except ValueError as error:
        parser.error(str(error))
    report = {
        "suite": args.suite, "prompt_variant": args.prompt_variant,
        "plan": plan, "results": [],
        "semantic_review": "pending", "status": "not_started",
        "usage_csv": "logs/llm_usage.csv",
        "source_sha256": {str(path.relative_to(Path(__file__).resolve().parents[1])):
                          hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in [Path(__file__).resolve(), *sorted(
                              (Path(__file__).resolve().parents[1] / "src" / "agent").glob("*.py"))]},
    }
    report["logical_call_limit"] = sum(case["logical_call_limit"] for case in report["plan"])
    if args.list_cases:
        print(json.dumps(report, indent=2))
        return 0

    client = None
    output = ROOT / "tmp" / (
        "tmp_part_d_validation_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        + ".json"
    )
    exit_code = 1
    try:
        config = load_llm_config()
        report.update(requested_model=config.model_name, base_url=config.base_url,
                      timeout=config.timeout, max_retries=config.max_retries,
                      http_attempt_limit=report["logical_call_limit"] * (config.max_retries + 1))
        # Reserve report storage before incurring API usage; final write may still fail.
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"paid_run": True, "model": config.model_name,
                          "logical_call_limit": report["logical_call_limit"],
                          "http_attempt_limit": report["http_attempt_limit"]}), flush=True)
        client = LLMClient(config)
        run_cases(client, report, args.suite)
        report["status"] = "structurally_valid_semantic_review_pending"
        exit_code = 0
    except Exception as error:
        report.update(status="error", error_type=type(error).__name__,
                      **failure_diagnostics(error))
    finally:
        if client is not None:
            client.close()
    report["logical_calls_started"] = sum(record["logical_calls_started"] for record in report["results"])
    try:
        output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "report": str(output),
                          "logical_calls_started": report["logical_calls_started"]}))
    except OSError as error:
        print(json.dumps({"status": "report_error", "error_type": type(error).__name__}))
        return 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
