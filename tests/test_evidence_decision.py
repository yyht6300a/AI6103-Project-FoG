"""Check candidate wiring without invoking a paid model.

These checks establish stage order and full-graph transport, not decision quality
or the publication consistency gate planned for the next implementation step.
"""

import json
import unittest
from unittest.mock import Mock

from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from test_memory_update import A, B, completion


class EvidenceDecisionTests(unittest.TestCase):
    """Exercise the explicit candidate while retaining normal ranking.

    Synthetic decisions deliberately avoid any fixture-specific branch logic.
    """

    def test_candidate_passes_full_graph_and_runs_ranking(self):
        """Send historical and new evidence to both stages before ranking.

        The old state remains immutable and only final selections gain memory.
        """
        state = QuestionMemory("Who?", [A])
        decision = {"condition_status": "not_conditional", "condition_triplets": [],
                    "answer_targets": [], "required_triplets": [list(A), list(B)],
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
