"""Verify question-level memory ownership with controlled graph snapshots.

These offline tests exercise initialization, multi-question isolation and
snapshot safety without implementing LLM decisions or making API requests.
"""

from dataclasses import FrozenInstanceError, replace
import unittest

from src.agent.memory_state import QuestionMemory
from src.agent.potential_subgraph import build_potential_subgraph


class QuestionMemoryTests(unittest.TestCase):
    """Protect question-local evidence and permanent rejection state.

    Explicit graph fixtures stand in for later validated LLM outputs; they
    verify state ownership without claiming the update policy is implemented.
    """

    def test_empty_initialization_and_literal_question(self) -> None:
        """Start both graphs empty and preserve the original query exactly.

        Whitespace around a valid query must not change its stored text.
        """
        state = QuestionMemory("  Which country?  ")
        self.assertEqual(state.original_question, "  Which country?  ")
        self.assertEqual(state.memory_triplets, frozenset())
        self.assertEqual(state.rejected_triplets, frozenset())

    def test_question_and_run_isolation(self) -> None:
        """Keep populated state local even when another run uses the same query.

        Replacing one snapshot cannot alter another question or its defaults.
        """
        first = QuestionMemory("Question A")
        other = QuestionMemory("Question B")
        same_text = QuestionMemory("Question A")
        updated = replace(first, memory_triplets=[("A", "r", "B")],
                          rejected_triplets=[("C", "r", "D")])
        self.assertEqual(updated.memory_triplets, frozenset({("A", "r", "B")}))
        self.assertEqual(updated.rejected_triplets, frozenset({("C", "r", "D")}))
        for state in (first, other, same_text, QuestionMemory("Question A")):
            self.assertEqual(state.memory_triplets, frozenset())
            self.assertEqual(state.rejected_triplets, frozenset())

    def test_inputs_are_detached_and_state_is_immutable(self) -> None:
        """Prevent nested input mutation and direct assignment from leaking in.

        Both evidence and rejection collections must own immutable triples.
        """
        memory = [["A", "r", "B"]]
        rejected = [["C", "r", "D"]]
        state = QuestionMemory("Question", memory, rejected)
        memory[0][0] = "changed"
        rejected[0][0] = "changed"
        memory.clear()
        rejected.clear()
        self.assertEqual(state.memory_triplets, frozenset({("A", "r", "B")}))
        self.assertEqual(state.rejected_triplets, frozenset({("C", "r", "D")}))
        with self.assertRaises(FrozenInstanceError):
            state.original_question = "Other question"
        for graph in (state.memory_triplets, state.rejected_triplets):
            with self.assertRaises(AttributeError):
                graph.add(("X", "r", "Y"))
            triple = next(iter(graph))
            with self.assertRaises(TypeError):
                triple[0] = "changed"

    def test_graph_identity_and_direction(self) -> None:
        """Collapse exact duplicates while retaining text and edge direction.

        Graphs use set semantics; their iteration order is not a prompt order.
        """
        triples = [[" 国家 ", "r", "B"], ["B", "r", " 国家 "], [" 国家 ", "r", "B"]]
        state = QuestionMemory("Question", triples)
        self.assertEqual(state.memory_triplets,
                         frozenset({(" 国家 ", "r", "B"), ("B", "r", " 国家 ")}))

    def test_invalid_snapshots_leave_existing_state_intact(self) -> None:
        """Reject malformed or contradictory snapshots without altering history.

        Validation errors must omit private query and triple contents.
        """
        original = QuestionMemory("Question", [("A", "r", "B")])
        for invalid in (None, "secret", {}, ["secret"], [["secret", "r"]],
                        [["secret", "r", "B", "extra"]], [["secret", " ", "B"]],
                        [["secret", "r", 1]]):
            for field in ("memory_triplets", "rejected_triplets"):
                with self.subTest(field=field, invalid=invalid):
                    with self.assertRaises(ValueError) as error:
                        replace(original, **{field: invalid})
                    self.assertNotIn("secret", str(error.exception))
        with self.assertRaises(ValueError):
            replace(original, rejected_triplets=[("A", "r", "B")])
        self.assertEqual(original.memory_triplets, frozenset({("A", "r", "B")}))
        self.assertEqual(original.rejected_triplets, frozenset())
        for query in (None, 1, "", " \n\t"):
            with self.subTest(query=query), self.assertRaises(ValueError):
                QuestionMemory(query)

    def test_threshold_filtering_does_not_create_rejection(self) -> None:
        """Retain empty masks when evidence is temporarily below the cutoff.

        Later potential-subgraph construction can recover the same triple.
        """
        state = QuestionMemory("Question")
        triple = ("A", "r", "B")
        self.assertEqual(build_potential_subgraph([triple], [0.6]), [])
        self.assertEqual(state.rejected_triplets, frozenset())
        self.assertEqual(build_potential_subgraph([triple], [0.9]), [triple])
        self.assertEqual(state.memory_triplets, frozenset())
