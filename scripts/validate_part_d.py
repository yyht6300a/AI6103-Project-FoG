"""Run bounded live acceptance cases through Member D's public interfaces.

Use --list-cases for a free preview, or select a suite for paid
requests. Synthetic retrieval inputs replace C/E only; HTTP and model responses
are real. Reports under tmp contain public fixtures and validated outputs, never
credentials, raw failed responses or exception messages.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

# Support both python scripts/validate_part_d.py and python -m scripts.validate_part_d.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.agent.validation_error import failure_diagnostics
from src.agent.llm_client import LLMClient
from src.agent.llm_config import load_llm_config
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from src.agent.question_decomposition import decompose_question


QUESTION = "Which city was the author of The Glass Orchard born in?"
AUTHOR = ("The Glass Orchard", "written_by", "Mira Vale")
BIRTH = ("Mira Vale", "born_in", "Linden City")
DISTRACTOR = ("The Copper Harbor", "written_by", "Nolan Reed")
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
        ("insufficient", "Author alone cannot establish birthplace; ask for missing evidence."),
        ("empty", "No selected evidence or answer; return 1-3 clarification questions."),
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


def case_plan(suite: str, prompt_variant: str = "evidence_decision") -> list[dict]:
    """List the fixed calls and expectations without loading private settings.

    Args:
        suite: CLI-validated acceptance or diagnostic selection. Conditional
            full graphs/chains are separate from the original thirteen-case all.
        prompt_variant: Default decision pipeline or explicit comparison variant.

    Returns:
        Ordered cases with per-case logical request limits. Every memory round
        permits decision, selection and ranking if it returns multiple answers;
        decomposition permits one request. Each has YAML transport retries.
    """
    groups = ("single", "multi", "removal", "ranking") if suite == "all" else (suite,)
    memory_limit = 3 if prompt_variant == "evidence_decision" else 2
    return [{"case": f"{group}.{name}", "expected": expected,
             "logical_call_limit": 1 if (group == "single" and name == "decomposition")
                 or (group == "reference_diagnostic" and name != "named_author")
                 else 2 if group == "clarification_diagnostic" else memory_limit}
            for group in groups for name, expected in CASES[group]]


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
        completion = self.client.complete(messages, **options)
        self.calls.append({"messages": messages, "options": options, "completion": completion})
        return completion


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
                      **failure_diagnostics(error),
                      state_after=snapshot(state), old_state_unchanged=snapshot(state) == before)
        raise

    selected = result.state.memory_triplets
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
    if not record["old_state_unchanged"] or not record["state_rules_match"]:
        record["status"] = "state_rule_failure"
        raise RuntimeError("Acceptance state invariant failed")
    return result


def run_cases(client: LLMClient, report: dict, suite: str) -> None:
    """Execute fixed single-round and/or adaptive-state multi-round fixtures.

    Args:
        client: Real LLMClient, shared across independent question states.
        report: Mutable report storing validated output for later semantic review.
        suite: Standard acceptance group, conditional full graphs/chains, an
            explicit diagnostic group, or all. Conditional and diagnostics are
            excluded from all, which retains thirteen cases.

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
                record["controlled_checks"] = checks
                if not all(checks.values()):
                    record["status"] = "controlled_check_failure"
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

    if suite in ("single", "all"):
        record = begin_case(report, "single.decomposition")
        record["question"] = QUESTION
        record["logical_calls_started"] = 1
        questions = decompose_question(QUESTION, client)
        record.update(status="validated", sub_questions=questions)
        for name, triples, scores in (
            ("sufficient", [AUTHOR, BIRTH], [0.95, 0.95]),
            ("insufficient", [AUTHOR], [0.95]),
            ("empty", [], []),
        ):
            memory_round(client, report, f"single.{name}", QuestionMemory(QUESTION),
                         triples, scores, questions)

    if suite in ("multi", "all"):
        first = memory_round(
            client, report, "multi.partial", QuestionMemory(QUESTION),
            [AUTHOR, BIRTH, DISTRACTOR], [0.95, 0.7, 0.95], (QUESTION,),
        )
        second = memory_round(
            client, report, "multi.recovery", first.state,
            [AUTHOR, BIRTH, DISTRACTOR], [0.95, 0.95, 0.95],
            first.response.subsequent_question,
        )
        third = memory_round(
            client, report, "multi.exclusion_probe", second.state,
            [AUTHOR, BIRTH, DISTRACTOR], [0.95, 0.95, 0.95],
            second.response.subsequent_question,
        )
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
    parser.add_argument("--suite", choices=(*CASES, "all"), default="all")
    parser.add_argument(
        "--prompt-variant", default="evidence_decision",
        choices=("evidence_decision", "retrieval_questions", "paper", "answer_ranking"),
    )
    parser.add_argument("--list-cases", action="store_true",
                        help="Print expectations and call counts without loading .env or using API.")
    args = parser.parse_args(argv)
    report = {
        "suite": args.suite, "prompt_variant": args.prompt_variant,
        "plan": case_plan(args.suite, args.prompt_variant), "results": [],
        "semantic_review": "pending", "status": "not_started",
        "usage_csv": "logs/llm_usage.csv",
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
