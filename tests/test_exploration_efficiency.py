"""Verify question-local expansion exclusions and permanent LLM rejection.

Controlled multi-round retrieval and completions exercise the omission policy
without real API calls or changes to private provider configuration.
"""

import unittest
from unittest.mock import Mock

from src.agent.llm_client import LLMClient
from src.agent.memory_state import QuestionMemory
from src.agent.memory_update import update_memory
from src.agent.potential_subgraph import build_potential_subgraph
from test_memory_update import A, B, C, REJECTED, completion


class ExplorationEfficiencyTests(unittest.TestCase):
    """Check the exclusion boundary before expansion and after valid updates.

    Historical memory must still reach the LLM while excluded from expansion;
    rejections accumulate only after the entire response passes validation.
    """

    def test_expansion_filter_preserves_exact_identity_and_candidate_order(self) -> None:
        """Remove absorbed and rejected rows without suppressing shared entities.

        Reversed edges and literal string variants remain distinct candidates;
        duplicate eligible rows collapse at their first occurrence.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        reverse = (A[2], A[1], A[0])
        variant = (A[0] + " ", A[1], A[2])
        rows = [list(row) for row in [A, C, REJECTED, B, C, reverse, variant]]
        original = [row.copy() for row in rows]
        self.assertEqual(state.filter_expansion_triplets(iter(rows)), [C, B, reverse, variant])
        self.assertEqual(rows, original)
        self.assertEqual(state.excluded_triplets, {A, REJECTED})
        self.assertIsInstance(state.excluded_triplets, frozenset)
        self.assertEqual(state.filter_expansion_triplets([]), [])

    def test_filter_rejects_invalid_rows_without_changing_state(self) -> None:
        """Validate every candidate before returning any eligible expansion.

        Invalid containers and trailing malformed rows must fail without mutating
        either state set or leaking the private row contents in errors.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        for invalid in (None, "secret", {}, [B, ["secret"]], [A, ["secret", "r", 1]]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError) as error:
                state.filter_expansion_triplets(invalid)
            self.assertNotIn("secret", str(error.exception))
        self.assertEqual(state.excluded_triplets, {A, REJECTED})

    def test_multiround_masks_keep_memory_in_prompt_and_rejections_out(self) -> None:
        """Suppress repeated expansion while still reconsidering old evidence.

        Omitted potential evidence is rejected first, then omitted old memory;
        neither may return even in later clarification-guided retrieval.
        """
        state = QuestionMemory("Who?", rejected_triplets=[REJECTED])
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion([A], questions=["Who leads Country A?"]),
                                       completion([C], [C[2]]),
                                       completion(questions=["What evidence is missing?"])]
        first, _ = update_memory(state, [A, B, B], client)
        self.assertEqual(first.rejected_triplets, {B, REJECTED})
        retrieve = Mock(return_value=[B, A, REJECTED, C])
        eligible = first.filter_expansion_triplets(retrieve("Who leads Country A?"))
        self.assertEqual(eligible, [C])
        second, _ = update_memory(first, eligible, client)
        prompt = client.complete.call_args.args[0][0]["content"]
        self.assertIn('"Song C"', prompt)
        self.assertNotIn('"led_by"', prompt)
        self.assertEqual(second.memory_triplets, {C})
        self.assertEqual(second.rejected_triplets, {A, B, REJECTED})
        third, _ = update_memory(second, [], client)
        self.assertEqual(third.memory_triplets, set())
        self.assertEqual(third.rejected_triplets, {A, B, C, REJECTED})
        self.assertEqual(third.filter_expansion_triplets([A, B, C, REJECTED]), [])
        with self.assertRaises(ValueError):
            update_memory(third, [A], client)
        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(state.rejected_triplets, {REJECTED})

    def test_threshold_hidden_evidence_is_not_rejected(self) -> None:
        """Reject only submitted evidence, allowing unseen low-score recovery.

        Expansion exclusions precede scoring; failing the potential threshold
        alone must not create a permanent exclusion in a later successful round.
        """
        state = QuestionMemory("Who?")
        client = Mock(spec=LLMClient)
        client.complete.side_effect = [completion(questions=["Where was Person B born?"]),
                                       completion([C], [C[2]])]
        potential = build_potential_subgraph(state.filter_expansion_triplets([A, C]), [0.9, 0.7])
        first, _ = update_memory(state, potential, client)
        self.assertEqual(first.rejected_triplets, {A})
        eligible = first.filter_expansion_triplets([A, C])
        self.assertEqual(eligible, [C])
        second, _ = update_memory(first, build_potential_subgraph(eligible, [0.9]), client)
        self.assertEqual(second.memory_triplets, {C})
        self.assertEqual(second.rejected_triplets, {A})

    def test_invalid_response_does_not_publish_new_rejections(self) -> None:
        """Avoid inferring rejections from a partially valid or refused response.

        Omission has meaning only after all output fields and the envelope pass;
        existing memory and cumulative rejections remain usable on failure.
        """
        state = QuestionMemory("Who?", [A], [REJECTED])
        client = Mock(spec=LLMClient)
        refused = completion([B], [B[2]])
        refused["choices"][0]["message"]["refusal"] = "Refused"
        for output in (completion([B], ["Invented"]), completion([B]), refused):
            client.complete.return_value = output
            with self.subTest(output=output), self.assertRaises(ValueError):
                update_memory(state, [B, C], client)
            self.assertEqual(state.memory_triplets, {A})
            self.assertEqual(state.rejected_triplets, {REJECTED})
            self.assertEqual(state.filter_expansion_triplets([A, B, C]), [B, C])

    def test_masks_are_isolated_for_same_and_different_questions(self) -> None:
        """Keep identical graph candidates available in independent question runs.

        No query-text cache or global exclusion set may transfer decisions from
        the updated run to a fresh run, even when question strings are equal.
        """
        client = Mock(spec=LLMClient)
        client.complete.return_value = completion([A], questions=["Who leads Country A?"])
        updated, _ = update_memory(QuestionMemory("Who?"), [A, B], client)
        self.assertEqual(updated.filter_expansion_triplets([A, B, C]), [C])
        for state in (QuestionMemory("Who?"), QuestionMemory("Where?")):
            self.assertEqual(state.excluded_triplets, set())
            self.assertEqual(state.filter_expansion_triplets([A, B, C]), [A, B, C])
