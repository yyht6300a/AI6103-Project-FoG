"""Check the Figure 8 input boundary without credentials or API calls.

Cases focus on graph identity, deterministic serialization, literal text and
failure preservation before later LLM response handling is implemented.
"""

from dataclasses import asdict
import json
import unittest

from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_state import QuestionMemory
from src.agent.potential_subgraph import build_potential_subgraph


class MemoryPromptTests(unittest.TestCase):
    """Verify the paper's instructions and question-local evidence boundary.

    All inputs are synthetic; building a prompt has no transport dependency.
    """

    def test_paper_instructions_and_output_example(self) -> None:
        """Keep Figure 8's evidence, answer and clarification requirements.

        The schema example must remain literal JSON after interpolation.
        """
        prompt = build_memory_prompt("Who?", [], [], prompt_variant="paper")
        for instruction in (
            'Given a question and several knowledge triples ("subject","relation","object"):',
            "Select relevant triples (high recall).",
            "Using only selected triples, predict the answer; if undetermined, ask 1–3 clarification questions.",
            "Keep a triple unless you’re confident it’s irrelevant.",
            "Use only supported evidence; if multiple answers, rank by likelihood from triples.",
            "Select only input triples/entities.",
            "No external knowledge; no fabrication; no rewriting/creating/reordering/merging/splitting triples.",
            "Selected triples must match input exactly.",
            "Answers must be entity strings appearing in input triples exactly as-is.",
        ):
            self.assertIn(instruction, prompt)
        example = prompt.split("### Output Format\n", 1)[1].split("### Real Input", 1)[0]
        self.assertEqual(json.loads(example), {
            "selected_triplets": [], "predict_answer": [], "subsequent_question": [],
        })

    def test_graph_union_is_stable_and_preserves_triple_direction(self) -> None:
        """Include old and new evidence once regardless of container order.

        Sorting graph rows must preserve distinct reverse edges and relations.
        """
        old = [("B", "r", "A"), ("A", "r", "B")]
        new = [("B", "r", "A"), ("A", "s", "B"), ("A", "r", "B")]
        prompt = build_memory_prompt("Original?", old, new)
        self.assertEqual(prompt, build_memory_prompt("Original?", frozenset(old), reversed(new)))
        knowledge = json.loads(prompt.split("\nKnowledge: ", 1)[1])
        self.assertEqual(knowledge, [["A", "r", "B"], ["A", "s", "B"], ["B", "r", "A"]])
        self.assertEqual(old, [("B", "r", "A"), ("A", "r", "B")])
        self.assertEqual(len(new), 3)

    def test_literal_question_and_json_round_trip(self) -> None:
        """Preserve Unicode, whitespace and syntax-like text without recursion.

        JSON escaping must retain embedded quotes, newlines and backslashes
        when decoded, while question insertion remains verbatim.
        """
        question = ' 谁是 "{question}"？\n{triplets} '
        triple = (' 国家 "A"\n', "r\\s", "{question} 国家")
        prompt = build_memory_prompt(question, [triple], [])
        self.assertIn("### Real Input\nQuestion: " + question + "\nKnowledge: ", prompt)
        self.assertEqual(json.loads(prompt.rsplit("\nKnowledge: ", 1)[1]), [list(triple)])
        self.assertIn("国家", prompt.rsplit("\nKnowledge: ", 1)[1])

    def test_empty_graphs_and_initial_snapshot(self) -> None:
        """Allow no evidence or evidence from only one side of the boundary.

        Empty memory is the normal first-round state, and no new potential
        triples must not discard evidence retained from an earlier round.
        """
        state = QuestionMemory("Original?")
        triple = ("A", "r", "B")
        self.assertTrue(build_memory_prompt(state.original_question, state.memory_triplets, []).endswith(
            "Knowledge: []"))
        self.assertEqual(build_memory_prompt("Original?", [triple], []),
                         build_memory_prompt("Original?", [], (t for t in [triple])))

    def test_invalid_inputs_fail_without_exposing_content(self) -> None:
        """Validate both graphs fully and keep validation errors content-free.

        Invalid new evidence must not alter the caller's immutable snapshot.
        """
        state = QuestionMemory("Private question?", [("Private head", "r", "B")])
        before = asdict(state)
        for question in (None, 1, [], "", " \n"):
            with self.subTest(question=question), self.assertRaises(ValueError):
                build_memory_prompt(question, [], [])
        invalid_graphs = (None, 1, "secret", b"secret", {"secret": "value"},
                          [None], ["abc"], [["Private head", "r"]],
                          [["Private head", "r", "B", "extra"]],
                          [["Private head", "r", 1]], [["Private head", "r", " "]])
        for graph in invalid_graphs:
            for memory, potential in ((graph, []), (state.memory_triplets, graph)):
                with self.subTest(graph=graph), self.assertRaises(ValueError) as error:
                    build_memory_prompt(state.original_question, memory, potential)
                self.assertNotIn("Private", str(error.exception))
                self.assertNotIn("secret", str(error.exception))
        self.assertEqual(asdict(state), before)

    def test_existing_module_inputs_and_rejected_state_stay_separate(self) -> None:
        """Serialize the supplied evidence without introducing retrieval policy.

        Threshold-filtered and rejected triples are not implicitly pulled into
        the prompt, and constructing it never changes either memory graph.
        """
        old = ("Country", "anthem", "Song")
        new = ("Country", "leader", "Person")
        hidden = ("Country", "other", "Hidden")
        rejected = ("Other", "r", "Rejected")
        state = QuestionMemory("Who leads the country with Song?", [old], [rejected])
        before = asdict(state)
        potential = build_potential_subgraph([new, hidden], [0.8, 0.7])
        prompt = build_memory_prompt(state.original_question, state.memory_triplets, potential)
        knowledge = json.loads(prompt.split("\nKnowledge: ", 1)[1])
        self.assertEqual(knowledge, [list(old), list(new)])
        self.assertEqual(asdict(state), before)
