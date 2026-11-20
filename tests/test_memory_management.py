"""Verify the public C/D/E scored-round contract without paid API requests.

Controlled completions test score alignment, exclusions, next-round handoff and
atomic publication through the composed production entry point.
"""

from dataclasses import FrozenInstanceError
import json
import math
import unittest
from unittest.mock import Mock

import requests

from src.agent.llm_client import LLMClient
from src.agent.memory_management import MSMResult, manage_memory
from src.agent.memory_prompt import ANSWER_RANKING_INSTRUCTIONS, build_memory_prompt
from src.agent.memory_state import QuestionMemory
from test_memory_update import A, B, C, REJECTED, completion


class MemoryManagementTests(unittest.TestCase):
    """Exercise the integration entry point with synthetic C/E inputs.

    Assertions inspect submitted evidence as well as all returned state and
    response fields so a successful call establishes a usable next round.
    """

    def test_ranking_variant_preserves_contract_and_model_order(self) -> None:
        """Rank answers by default for every first-stage prompt variant.

        Even the paper prompt must run the separate ranking stage. Its model
        order survives unchanged without deterministic response repair.
        """
        state = QuestionMemory("Who?", [A])
        default = build_memory_prompt("Who?", [A], [B])
        for variant in ("paper", "retrieval_questions", "answer_ranking"):
            with self.subTest(variant=variant):
                client = Mock(spec=LLMClient)
                ranked = completion()
                ranked["choices"][0]["message"]["content"] = json.dumps({"predict_answer": [A[0], B[2]]})
                client.complete.side_effect = [completion([B, A], [B[2], A[0]]), ranked]
                result = manage_memory(state, [B, C], [0.95, 0.7], client,
                                       prompt_variant=variant)
                prompt = build_memory_prompt("Who?", [A], [B], prompt_variant=variant)
                self.assertEqual(client.complete.call_count, 2)
                self.assertEqual(client.complete.call_args_list[0].args[0], [{"role": "user", "content": prompt}])
                self.assertEqual(result.response.predict_answer, (A[0], B[2]))
                self.assertEqual(result.state.memory_triplets, {A, B})
                self.assertEqual(state.memory_triplets, {A})
                if variant == "answer_ranking":
                    self.assertEqual(prompt.replace(ANSWER_RANKING_INSTRUCTIONS, "", 1), default)
        client = Mock(spec=LLMClient)
        with self.assertRaises(ValueError):
            manage_memory(state, [B], [0.95], client, prompt_variant="unknown")
        client.complete.assert_not_called()

    def test_scored_round_returns_all_outputs_without_alignment_loss(self) -> None:
        """Keep later qualifying duplicates aligned with their own scores.

        Stale rejected rows are ignored defensively; historical memory reaches
        the prompt even when its current score is low. Output ranking survives.
        """
        state = QuestionMemory("Who leads Country A?", [A], [REJECTED])
        triples = [B, REJECTED, A, C, B]
        scores = [0.1, 1.0, 0.0, 0.7, 0.9]
        client = Mock(spec=LLMClient)
        ranked = completion()
        ranked["choices"][0]["message"]["content"] = json.dumps({"predict_answer": [B[2], A[0], B[2]]})
        client.complete.side_effect = [completion([B, A, B], [B[2], A[0], B[2]], ["Next?"]), ranked]
        result = manage_memory(state, triples, scores, client, prompt_variant="retrieval_questions")
        self.assertIsInstance(result, MSMResult)
        self.assertEqual(result.state.memory_triplets, {A, B})
        self.assertEqual(result.state.rejected_triplets, {REJECTED})
        self.assertEqual(result.state.excluded_triplets, {A, B, REJECTED})
        self.assertEqual(result.response.selected_triplets, (B, A, B))
        self.assertEqual(result.response.predict_answer, (B[2], A[0], B[2]))
        self.assertEqual(result.response.subsequent_question, ("Next?",))
        self.assertEqual(client.complete.call_count, 2)
        self.assertEqual(client.complete.call_args_list[0].args[0], [{"role": "user", "content":
            build_memory_prompt(state.original_question, [A], [B])}])
        self.assertEqual(triples, [B, REJECTED, A, C, B])
        self.assertEqual(scores, [0.1, 1.0, 0.0, 0.7, 0.9])
        self.assertEqual(state.memory_triplets, {A})
        with self.assertRaises(FrozenInstanceError):
            result.state = state
        client.close.assert_not_called()

    def test_next_round_recovers_hidden_evidence_and_rejects_removed_memory(self) -> None:
        """Hand clarification to E and recover freshly re-scored evidence.

        Omitted old memory becomes permanently rejected, while the earlier
        threshold-hidden row gains no rejection before its recovery.
        """
        state = QuestionMemory("Where was the leader born?", [A])
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion([A, B], questions=["Where was Person B born?"]),
                                       completion([B, C], [C[2]])]
        first = manage_memory(state, [B, C], [0.9, 0.5], client, prompt_variant="retrieval_questions")
        self.assertNotIn(C, first.state.excluded_triplets)
        retrieve = Mock(return_value=[A, B, C])
        rescore = Mock(return_value=[0.95])
        candidates = first.state.filter_expansion_triplets(
            retrieve(first.response.subsequent_question))
        second = manage_memory(first.state, candidates, rescore(candidates), client, prompt_variant="retrieval_questions")
        retrieve.assert_called_once_with(("Where was Person B born?",))
        rescore.assert_called_once_with([C])
        self.assertEqual(second.state.memory_triplets, {B, C})
        self.assertEqual(second.state.rejected_triplets, {A})
        self.assertEqual(second.state.excluded_triplets, {A, B, C})
        self.assertEqual(second.response.predict_answer, (C[2],))
        self.assertEqual(second.response.subsequent_question, ())
        self.assertEqual(first.state.memory_triplets, {A, B})

    def test_empty_graphs_and_custom_threshold_still_request_clarification(self) -> None:
        """Support empty or fully filtered inputs with explicit cutoff values.

        No-op graph filtering must not bypass Figure 8 or fabricate an answer.
        """
        for triples, scores, threshold in (([], [], 0.7), ([C], [0.5], 0.5),
                                            ([REJECTED], [1.0], 0.7)):
            with self.subTest(triples=triples):
                state = QuestionMemory("Where?", rejected_triplets=[REJECTED])
                client = Mock(spec=LLMClient)
                client.complete.return_value = completion(questions=["Which place?"])
                result = manage_memory(state, triples, scores, client, threshold, prompt_variant="retrieval_questions")
                self.assertEqual(result.state.memory_triplets, set())
                self.assertEqual(result.state.rejected_triplets, {REJECTED})
                self.assertEqual(result.response.predict_answer, ())
                self.assertEqual(result.response.subsequent_question, ("Which place?",))
                client.complete.assert_called_once_with([{"role": "user", "content":
                    build_memory_prompt("Where?", [], [])}])
        client.complete.return_value = completion([C], [C[2]])
        result = manage_memory(state, [C], [0.6], client, 0.5, prompt_variant="retrieval_questions")
        self.assertEqual(result.state.memory_triplets, {C})

    def test_invalid_inputs_fail_before_request_including_excluded_rows(self) -> None:
        """Validate every scored row before any filtering can hide an error.

        Malformed lengths, values and state must not incur API usage or alter
        the original memory and permanent rejection snapshots.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        cases = ((state, [B], [], 0.7), (state, [REJECTED], [math.nan], 0.7),
                 (state, [A], [True], 0.7), (state, [["bad"]], [0.1], 0.7),
                 (state, [B], [[0.9]], 0.7), (state, [], [], math.inf),
                 (None, [], [], 0.7))
        for snapshot, triples, scores, threshold in cases:
            with self.subTest(triples=triples, scores=scores):
                client = Mock(spec=LLMClient)
                with self.assertRaises(ValueError):
                    manage_memory(snapshot, triples, scores, client, threshold, prompt_variant="retrieval_questions")
                client.complete.assert_not_called()
                self.assertEqual(state.memory_triplets, {A})
                self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_failed_round_returns_no_replacement_snapshot(self) -> None:
        """Propagate transport, accounting and output failures without retry.

        A partial valid selection cannot publish state when another output
        field is invalid. The original snapshot remains safe for the caller.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        for failure in (requests.Timeout(), OSError(), completion([B], [C[2]]),
                        {"choices": []}):
            with self.subTest(failure=type(failure).__name__):
                client = Mock(spec=LLMClient)
                if isinstance(failure, Exception):
                    client.complete.side_effect = failure
                    error = type(failure)
                else:
                    client.complete.return_value = failure
                    error = ValueError
                with self.assertRaises(error):
                    manage_memory(state, [B], [0.9], client, prompt_variant="retrieval_questions")
                client.complete.assert_called_once()
                self.assertEqual(state.memory_triplets, {A})
                self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_identical_questions_have_independent_rounds(self) -> None:
        """Keep rejection state local even for identical original query text.

        The facade has no hidden session registry and does not close the shared
        caller-owned client between independent runs.
        """
        first = QuestionMemory("Who?")
        second = QuestionMemory("Who?")
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion(questions=["Who else?"]),
                                       completion([B], [B[2]])]
        rejected = manage_memory(first, [B], [0.9], client, prompt_variant="retrieval_questions")
        accepted = manage_memory(second, [B], [0.9], client, prompt_variant="retrieval_questions")
        self.assertEqual(rejected.state.rejected_triplets, {B})
        self.assertEqual(accepted.state.memory_triplets, {B})
        self.assertEqual(accepted.state.rejected_triplets, set())
        self.assertEqual(first.excluded_triplets, set())
        self.assertEqual(second.excluded_triplets, set())
        client.close.assert_not_called()
