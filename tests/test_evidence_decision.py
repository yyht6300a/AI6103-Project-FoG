"""Check candidate wiring without invoking a paid model.

These checks establish stage order, full-graph transport, consistency gates and
atomic failure. Mocked decisions do not establish real model decision quality.
"""

import json
import unittest
from unittest.mock import Mock

import requests

from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from src.agent.validation_error import MemoryValidationError, failure_diagnostics
from test_memory_update import A, B, C, REJECTED, completion


OTHER_BIRTH = ("Person E", "born_in", "Town F")


def decision_completion(status="not_conditional", required=(A, B), **overrides):
    """Build an independent synthetic decision envelope for offline cases.

    Args:
        status: Literal condition status, including invalid values for tests.
        required: Submitted triples the final selection must retain.
        **overrides: Replacement fields for malformed or conditional records.

    Returns:
        A normal completion envelope carrying the five decision fields.
    """
    decision = {"condition_status": status, "condition_triplets": [],
                "targets_by_case": {"true": [], "false": [], "not_conditional": []},
                "required_triplets": list(required),
                "missing_relations": []}
    decision.update(overrides)
    result = completion()
    result["choices"][0]["message"]["content"] = json.dumps(decision)
    return result


class EvidenceDecisionTests(unittest.TestCase):
    """Exercise the explicit candidate while retaining normal ranking.

    Synthetic decisions deliberately avoid any fixture-specific branch logic.
    """

    def test_branch_targets_are_derived_before_final_prompt(self):
        """Forward only applicable targets, allowing missing facts and many objects.

        Unknown targets use a stable union. Objects from the question need no
        supporting triple yet; graph components must match exactly.
        """
        targets = {"true": [A[0], "Unseen Person"], "false": [B[2], A[0]],
                   "not_conditional": []}
        cases = [("true", targets, targets["true"]),
                 ("false", targets, targets["false"]),
                 ("unknown", targets, [A[0], "Unseen Person", B[2]]),
                 ("not_conditional", {"true": [], "false": [],
                                      "not_conditional": ["Unseen Person", A[0]]},
                  ["Unseen Person", A[0]])]
        for status, table, expected in cases:
            with self.subTest(status=status):
                state = QuestionMemory("What about Unseen Person?", [A], [REJECTED])
                client = Mock()
                client.complete.side_effect = [
                    decision_completion(status, targets_by_case=table,
                                        missing_relations=["birthplace"]),
                    completion([A, B], questions=["Where was Unseen Person born?"]),
                ]
                manage_memory(state, [B], [0.95], client, prompt_variant="evidence_decision")
                prompt = client.complete.call_args_list[1].args[0][0]["content"]
                active = json.loads(prompt.split("Return only the existing three output fields.\n")[-1])
                self.assertEqual(active["answer_targets"], expected)
                self.assertNotIn("targets_by_case", active)
                self.assertEqual(state, QuestionMemory("What about Unseen Person?", [A], [REJECTED]))

    def test_candidate_passes_full_graph_and_runs_ranking(self):
        """Send historical and new evidence to both stages before ranking.

        The old state remains immutable and only final selections gain memory.
        """
        state = QuestionMemory("Who?", [A])
        decision = {"condition_status": "not_conditional", "condition_triplets": [],
                    "targets_by_case": {"true": [], "false": [], "not_conditional": []},
                    "required_triplets": [list(A), list(B)],
                    "missing_relations": []}
        first = completion()
        first["choices"][0]["message"]["content"] = json.dumps(decision)
        ranked = completion()
        ranked["choices"][0]["message"]["content"] = json.dumps(
            {"predict_answer": [A[0], B[2]]})
        client = Mock()
        client.complete.side_effect = [first, completion([A, B], [B[2], A[0]]), ranked]
        result = manage_memory(state, [B], [0.95], client,
                               prompt_variant="evidence_decision")
        self.assertEqual(client.complete.call_count, 3)
        for call in client.complete.call_args_list[:2]:
            prompt = call.args[0][0]["content"]
            self.assertIn(state.original_question, prompt)
            self.assertIn(json.dumps(sorted([A, B]), ensure_ascii=False), prompt)
        decision.pop("targets_by_case")
        decision["answer_targets"] = []
        self.assertIn(json.dumps(decision, ensure_ascii=False),
                      client.complete.call_args_list[1].args[0][0]["content"])
        self.assertEqual(result.response.predict_answer, (A[0], B[2]))
        self.assertEqual(result.state.memory_triplets, {A, B})
        self.assertEqual(state.memory_triplets, {A})

    def test_bad_decision_stops_before_selection(self):
        """Reject a malformed first-stage record without a second request.

        Failure neither changes the old evidence nor adds permanent rejections.
        """
        state = QuestionMemory("Who?", [A])
        client = Mock()
        client.complete.return_value = completion()
        with self.assertRaises(ValueError):
            manage_memory(state, [B], [0.95], client, prompt_variant="evidence_decision")
        self.assertEqual(client.complete.call_count, 1)
        self.assertEqual(state.memory_triplets, {A})
        self.assertEqual(state.rejected_triplets, frozenset())

    def test_invalid_decision_records_stop_before_selection(self):
        """Reject malformed fields and nonliteral evidence before final output.

        Every subcase starts with both historical evidence and a rejection;
        failures preserve both and expose only fixed stage diagnostics.
        """
        cases = [
            (decision_completion(status=[]), "decision_status"),
            (decision_completion(status=True), "decision_status"),
            (decision_completion(extra=[]), "decision_fields"),
            (decision_completion(required="bad"), "decision_triplets"),
            (decision_completion(required=[["a", "r"]]), "decision_triplets"),
            (decision_completion(required=[["a", "r", 1]]), "decision_triplets"),
            (decision_completion(required=[[" ", "r", "b"]]), "decision_triplets"),
            (decision_completion(required=[REJECTED]), "decision_membership"),
            (decision_completion(required=[("country A", A[1], A[2])]),
             "decision_membership"),
            (decision_completion(condition_triplets=[C]), "decision_membership"),
            (decision_completion(condition_triplets=["bad"]), "decision_triplets"),
            (decision_completion(condition_triplets={}, required=[]), "decision_triplets"),
            (decision_completion(answer_targets=[]), "decision_fields"),
            (decision_completion(targets_by_case=[]), "decision_targets"),
            (decision_completion(targets_by_case={"true": []}), "decision_targets"),
            (decision_completion(targets_by_case={"true": [], "false": [],
                                                 "not_conditional": [False]}), "decision_strings"),
            (decision_completion(targets_by_case={"true": [], "false": [],
                                                 "not_conditional": ["Absent"]}),
             "decision_target_membership"),
            (decision_completion(targets_by_case={"true": [A[0]], "false": [],
                                                 "not_conditional": []}), "decision_targets"),
            (decision_completion("true", targets_by_case={"true": [], "false": [],
                                                          "not_conditional": [A[0]]}),
             "decision_targets"),
            (decision_completion(missing_relations=[" "]), "decision_strings"),
            (decision_completion(missing_relations={}), "decision_strings"),
            (decision_completion(required=[A], condition_triplets=[B]),
             "decision_condition_retention"),
        ]
        for first, code in cases:
            with self.subTest(code=code, first=first):
                state = QuestionMemory("Who?", [A], [REJECTED])
                client = Mock()
                client.complete.return_value = first
                with self.assertRaises(MemoryValidationError) as caught:
                    manage_memory(state, [B], [0.95], client,
                                  prompt_variant="evidence_decision")
                self.assertEqual(failure_diagnostics(caught.exception),
                                 {"failure_stage": "evidence_decision", "error_code": code})
                self.assertEqual(client.complete.call_count, 1)
                self.assertEqual(state, QuestionMemory("Who?", [A], [REJECTED]))

    def test_invalid_decision_envelopes_stop_before_selection(self):
        """Reject refusal, truncation, invalid JSON and ambiguous objects.

        A terminal first-stage failure must not trigger a correction request.
        """
        refused = decision_completion()
        refused["choices"][0]["message"]["refusal"] = "Refused"
        unfinished = decision_completion()
        unfinished["choices"][0]["finish_reason"] = "length"
        cases = [{}, {"choices": []}, refused, unfinished]
        for content in (None, "{", "[]", '{"x":1,"x":2}', '{"x":NaN}'):
            envelope = decision_completion()
            envelope["choices"][0]["message"]["content"] = content
            cases.append(envelope)
        for first in cases:
            with self.subTest(first=first):
                state = QuestionMemory("Who?", [A], [REJECTED])
                client = Mock()
                client.complete.return_value = first
                with self.assertRaises(MemoryValidationError):
                    manage_memory(state, [B], [0.95], client,
                                  prompt_variant="evidence_decision")
                self.assertEqual(client.complete.call_count, 1)
                self.assertEqual(state, QuestionMemory("Who?", [A], [REJECTED]))

    def test_unknown_then_resolved_paths_and_permanent_exclusion(self):
        """Retain both candidate birthplaces, then resolve either mock branch.

        Later rounds inherit actual returned snapshots. The omitted historical
        birthplace is permanently masked when reoffered with a high score.
        """
        for status, birth, discarded in (("true", C, OTHER_BIRTH),
                                         ("false", OTHER_BIRTH, C)):
            with self.subTest(status=status):
                state = QuestionMemory(f"Which town for {C[0]} if true, otherwise {OTHER_BIRTH[0]}?")
                client = Mock()
                client.complete.side_effect = [
                    decision_completion("unknown", [C, OTHER_BIRTH],
                                        targets_by_case={"true": [C[0]], "false": [OTHER_BIRTH[0]],
                                                         "not_conditional": []},
                                        missing_relations=[B[1]]),
                    completion([C, OTHER_BIRTH], questions=["Who leads Country A?"]),
                    decision_completion(status, [B, birth], condition_triplets=[B],
                                        targets_by_case={"true": [C[0]], "false": [OTHER_BIRTH[0]],
                                                         "not_conditional": []}),
                    completion([B, birth], [birth[2]]),
                    decision_completion(status, [B, birth], condition_triplets=[B],
                                        targets_by_case={"true": [C[0]], "false": [OTHER_BIRTH[0]],
                                                         "not_conditional": []}),
                    completion([B, birth], [birth[2]]),
                ]
                first = manage_memory(state, [C, OTHER_BIRTH, A], [0.95, 0.95, 0.7],
                                      client, prompt_variant="evidence_decision")
                self.assertEqual(first.state.memory_triplets, {C, OTHER_BIRTH})
                self.assertFalse(first.response.predict_answer)
                self.assertFalse(first.state.rejected_triplets)
                second = manage_memory(first.state, [B], [0.95], client,
                                       prompt_variant="evidence_decision")
                self.assertEqual(second.state.memory_triplets, {B, birth})
                self.assertEqual(second.state.rejected_triplets, {discarded})
                third = manage_memory(second.state, [B, C, OTHER_BIRTH], [0.95] * 3,
                                      client, prompt_variant="evidence_decision")
                self.assertEqual(third.state, second.state)
                self.assertEqual(third.response.predict_answer, (birth[2],))
                self.assertFalse(third.response.subsequent_question)
                for call in client.complete.call_args_list[-2:]:
                    self.assertNotIn(json.dumps(discarded), call.args[0][0]["content"])
                self.assertEqual(client.complete.call_count, 6)
                self.assertFalse(state.memory_triplets)
                self.assertEqual(first.state.memory_triplets, {C, OTHER_BIRTH})

    def test_stage_contradictions_fail_before_ranking(self):
        """Block omitted requirements, unknown answers and gap-free questions.

        Two tentative answers exercise the gate ahead of ranking. No failure
        publishes omissions as rejections or repairs the final model selection.
        """
        cases = [
            (decision_completion(), completion([B], [B[2]]), "decision_required_omitted"),
            (decision_completion("unknown", missing_relations=["relation"]),
             completion([A, B], [A[2], B[2]]), "decision_unknown_answer"),
        ]
        for status in ("true", "false", "not_conditional"):
            cases.extend([
                (decision_completion(status), completion([A, B], questions=["What?"]),
                 "decision_answer_required"),
                (decision_completion(status), completion([A, B], [B[2]], ["What?"]),
                 "decision_unnecessary_questions"),
            ])
        for first, final, code in cases:
            with self.subTest(code=code, first=first):
                state = QuestionMemory("Who?", [A], [REJECTED])
                client = Mock()
                client.complete.side_effect = [first, final]
                with self.assertRaises(MemoryValidationError) as caught:
                    manage_memory(state, [B], [0.95], client,
                                  prompt_variant="evidence_decision")
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(caught.exception.stage, "decision_consistency")
                self.assertEqual(client.complete.call_count, 2)
                self.assertEqual(state, QuestionMemory("Who?", [A], [REJECTED]))

    def test_resolved_but_missing_facts_may_remain_unanswered(self):
        """Allow factual follow-up when the resolved decision declares a gap.

        The no-gap rule must not reject a legitimate partial-evidence round.
        """
        client = Mock()
        client.complete.side_effect = [
            decision_completion("false", [B], condition_triplets=[B],
                                missing_relations=["born_in"]),
            completion([B], questions=["Where was Person B born?"]),
        ]
        result = manage_memory(QuestionMemory("Where?"), [B], [0.95], client,
                               prompt_variant="evidence_decision")
        self.assertFalse(result.response.predict_answer)
        self.assertEqual(result.state.memory_triplets, {B})
        self.assertEqual(client.complete.call_count, 2)

    def test_final_output_still_uses_original_strict_parser(self):
        """Do not let decision targets bypass selected-entity answer validation.

        Invalid final shapes and unknown answers leave the historical snapshot
        intact even when the preliminary decision itself is valid.
        """
        for final in (completion([A, B], ["External target"]),
                      completion([A, B], questions=[["What?"]]),
                      completion([A, B])):
            with self.subTest(final=final):
                state = QuestionMemory("Who?", [A], [REJECTED])
                client = Mock()
                client.complete.side_effect = [
                    decision_completion(targets_by_case={"true": [], "false": [],
                                                         "not_conditional": [A[0]]}), final]
                with self.assertRaises(MemoryValidationError):
                    manage_memory(state, [B], [0.95], client,
                                  prompt_variant="evidence_decision")
                self.assertEqual(client.complete.call_count, 2)
                self.assertEqual(state, QuestionMemory("Who?", [A], [REJECTED]))

    def test_each_request_failure_preserves_old_snapshot(self):
        """Fail decision, selection and ranking requests without partial results.

        Both exhausted transport and usage-write errors remain terminal; ranking
        validation failure likewise cannot publish a provisional memory update.
        """
        first = decision_completion(required=[B])
        final = completion([B], [B[0], B[2]])
        cases = []
        for error in (requests.Timeout(), OSError()):
            cases.extend(([error], [first, error], [first, final, error]))
        cases.append([first, final, completion()])
        for replies in cases:
            with self.subTest(length=len(replies), terminal=type(replies[-1])):
                state = QuestionMemory("Who?", [A], [REJECTED])
                client = Mock()
                client.complete.side_effect = replies
                with self.assertRaises((ValueError, requests.RequestException, OSError)):
                    manage_memory(state, [B], [0.95], client,
                                  prompt_variant="evidence_decision")
                self.assertEqual(client.complete.call_count, len(replies))
                self.assertEqual(state, QuestionMemory("Who?", [A], [REJECTED]))
