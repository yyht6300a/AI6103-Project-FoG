"""Exercise Figure 8 structured output with synthetic evidence and no API calls.

Cases distinguish literal graph membership from later evidence interpretation
and verify that invalid output cannot alter question-local state.
"""

from dataclasses import FrozenInstanceError, asdict
import json
import unittest

from src.agent.memory_prompt import build_memory_prompt
from src.agent.memory_response import parse_memory_response
from src.agent.memory_state import QuestionMemory
from src.agent.potential_subgraph import build_potential_subgraph


def response_json(selected=None, answers=None, questions=None) -> str:
    """Encode a complete synthetic response with independently variable fields.

    Args:
        selected: Triple list, defaulting to an empty selection.
        answers: Entity list, defaulting to no tentative answer.
        questions: String list, defaulting to no clarification questions.

    Returns:
        Standard JSON with the three Figure 8 field names.
    """
    return json.dumps({
        "selected_triplets": [] if selected is None else selected,
        "predict_answer": [] if answers is None else answers,
        "subsequent_question": [] if questions is None else questions,
    }, ensure_ascii=False)


class MemoryResponseTests(unittest.TestCase):
    """Verify strict structural validation without transport or state transitions.

    Evidence uses readable synthetic entity names; all error text must omit it.
    """

    def test_preserves_literals_ranking_direction_and_duplicates(self) -> None:
        """Retain exact Unicode strings and model order in immutable output.

        Deduplicating the validation graph must not reorder or deduplicate the
        selected output or the ranked answers.
        """
        first = (' 国家 "A"\n', 'r\\s', '{entity} 国家')
        second = ('{entity} 国家', 'reverse', ' 国家 "A"\n')
        selected = [second, first, second]
        answers = [first[2], first[0], first[2]]
        questions = [' What is "{entity}"?\n', '后续问题？']
        result = parse_memory_response(
            " \n" + response_json(selected, answers, questions) + "\t",
            [first], (triple for triple in [second]),
        )
        self.assertEqual(result.selected_triplets, tuple(selected))
        self.assertEqual(result.predict_answer, tuple(answers))
        self.assertEqual(result.subsequent_question, tuple(questions))
        with self.assertRaises(FrozenInstanceError):
            result.predict_answer = ()
        with self.assertRaises(TypeError):
            result.selected_triplets[0][0] = "Changed"

    def test_empty_fields_and_question_count_boundaries(self) -> None:
        """Require one to three questions when no answer can be determined.

        The all-empty Figure 8 schema example is not a valid completed response.
        Empty evidence must still yield a query for the next retrieval round.
        """
        for count in range(1, 4):
            questions = ["Clarify?"] * count
            result = parse_memory_response(response_json(questions=questions), [], [])
            self.assertEqual(result.selected_triplets, ())
            self.assertEqual(result.predict_answer, ())
            self.assertEqual(result.subsequent_question, tuple(questions))
        for count in (0, 4):
            with self.subTest(count=count), self.assertRaises(ValueError):
                parse_memory_response(response_json(questions=["Clarify?"] * count), [], [])

    def test_unanswered_selected_evidence_requires_clarification(self) -> None:
        """Require follow-up even when the model keeps relevant bridge evidence.

        Retaining triples alone does not declare that the original query has an
        answer; no extra sufficiency check or synthesized query is introduced.
        """
        triple = ("Country", "anthem", "Song")
        with self.assertRaisesRegex(ValueError, "requires subsequent questions"):
            parse_memory_response(response_json([triple]), [triple], [])
        questions = [' Who leads "Country"?\n', '后续问题？', '后续问题？']
        result = parse_memory_response(response_json([triple], questions=questions), [triple], [])
        self.assertEqual(result.subsequent_question, tuple(questions))

    def test_tentative_answers_allow_optional_clarification(self) -> None:
        """Allow answered and partially answered outputs without mutual exclusion.

        Semantic sufficiency stays with the model. Answer presence must neither
        force questions nor discard questions seeking additional evidence.
        """
        triple = ("Country", "leader", "Person")
        for count in range(4):
            questions = ["Who else leads Country?"] * count
            result = parse_memory_response(
                response_json([triple], ["Person"], questions), [], [triple],
            )
            self.assertEqual(result.subsequent_question, tuple(questions))
        with self.assertRaises(ValueError):
            parse_memory_response(response_json([triple], ["Person"], ["Q?"] * 4), [], [triple])

    def test_rejects_non_json_wrappers_and_ambiguous_objects(self) -> None:
        """Reject text repairs, extra values, duplicate keys and nonstandard JSON.

        Duplicate keys are rejected even when values agree, including escaped
        spellings of the same key that decode to identical names.
        """
        valid = response_json()
        invalid = [None, b"{}", 1, "", " ", "{", "[]", "null", "true", '"text"',
                   "```json\n" + valid + "\n```", "Reply: " + valid,
                   valid + " {}", valid + " trailing", valid[:-1] + ",}",
                   valid[:-1] + ', "selected_triplets": []}',
                   valid[:-1] + ', "predict_\\u0061nswer": []}']
        for constant in ("NaN", "Infinity", "-Infinity"):
            invalid.append(valid.replace('"predict_answer": []',
                                         '"predict_answer": [' + constant + ']'))
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(ValueError):
                parse_memory_response(content, [], [])

    def test_requires_exactly_three_fields_with_array_types(self) -> None:
        """Reject missing/unknown fields and scalar, object or null field values.

        No aliases or default values may silently hide an incomplete response.
        """
        valid = json.loads(response_json())
        for field in valid:
            missing = dict(valid)
            del missing[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                parse_memory_response(json.dumps(missing), [], [])
            for value in (None, True, 1, "A", {}):
                malformed = dict(valid, **{field: value})
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_memory_response(json.dumps(malformed), [], [])
        for extra in ("explanation", "selected_triples", "Predict_answer"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                parse_memory_response(json.dumps(dict(valid, **{extra: []})), [], [])

    def test_rejects_malformed_array_items(self) -> None:
        """Enforce triple arity and nonblank strings in every output array.

        JSON objects, nested arrays, booleans and numeric values cannot stand in
        for entity, relation, answer or question strings.
        """
        for triple in (None, True, 1, "ABC", {}, [], ["A", "r"],
                       ["A", "r", "B", "C"], ["A", 1, "B"],
                       ["A", "r", " \n"], ["A", "r", ["B"]]):
            with self.subTest(triple=triple), self.assertRaises(ValueError):
                parse_memory_response(response_json(selected=[triple]), [("A", "r", "B")], [])
        for field in ("predict_answer", "subsequent_question"):
            for item in (None, False, 0, {}, [], "", " \n"):
                malformed = json.loads(response_json())
                malformed[field] = [item]
                with self.subTest(field=field, item=item), self.assertRaises(ValueError):
                    parse_memory_response(json.dumps(malformed), [], [])

    def test_selected_triplets_require_exact_submitted_membership(self) -> None:
        """Reject fabricated, reordered, rewritten or combined triple components.

        Knowing each component separately does not make a recombined edge valid.
        """
        knowledge = [("A", "r", "B"), ("C", "s", "D")]
        for triple in (("B", "r", "A"), ("A", "B", "r"), ("A", "s", "D"),
                       ("a", "r", "B"), ("A ", "r", "B"), ("A", "r", "Unknown")):
            with self.subTest(triple=triple), self.assertRaises(ValueError):
                parse_memory_response(response_json(selected=[triple]), knowledge, [])
        with self.assertRaises(ValueError):
            parse_memory_response(response_json(selected=[knowledge[0]]), [], [])

    def test_answers_require_head_or_tail_identity(self) -> None:
        """Validate literal entity range without guessing answer semantics.

        Relation-only names, partial names, case changes, normalization and
        external entities fail even when the submitted graph is fully selected.
        """
        knowledge = [("Country A", "led_by", "Person B"), (" é", "r", "C")]
        for answer in ("led_by", "Person", "person b", "Person B ", "é", " e\u0301", "External"):
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                parse_memory_response(response_json(knowledge, [answer]), knowledge, [])
        result = parse_memory_response(
            response_json(knowledge, ["Person B", "Country A"]), [], knowledge,
        )
        self.assertEqual(result.predict_answer, ("Person B", "Country A"))
        with self.assertRaises(ValueError):
            parse_memory_response(response_json(answers=["Person B"]), [], [])

    def test_answers_exclude_unselected_evidence(self) -> None:
        """Reject answers drawn only from omitted historical or potential rows.

        An empty selection cannot support any answer, even if the answer is an
        exact entity in the submitted knowledge. Empty answers remain valid.
        """
        old = ("Country", "anthem", "Song")
        new = ("Country", "leader", "Person")
        for selected, answer in (([old], "Person"), ([new], "Song"), ([], "Country")):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                parse_memory_response(response_json(selected, [answer]), [old], [new])
        for selected in ([], [old], [new]):
            result = parse_memory_response(
                response_json(selected, questions=["What evidence is missing?"]), [old], [new],
            )
            self.assertEqual(result.predict_answer, ())

    def test_ranked_answers_can_span_selected_multi_hop_evidence(self) -> None:
        """Preserve model ranking across a selected path and multiple candidates.

        No graph-order or alphabetical sorting may replace the model's ranking;
        literal endpoint membership also allows entities shared with omitted rows.
        """
        bridge = ("Song", "anthem_of", "Country")
        first = ("Country", "leader", "Z Person")
        second = ("Country", "leader", "A Person")
        omitted = ("Z Person", "birthplace", "Town")
        result = parse_memory_response(
            response_json([bridge, second, first], ["Z Person", "A Person", "Z Person"]),
            (row for row in [bridge]), (row for row in [first, second, omitted]),
        )
        self.assertEqual(result.predict_answer, ("Z Person", "A Person", "Z Person"))

    def test_prompt_union_threshold_and_state_preservation(self) -> None:
        """Use exactly the graph submitted in the prompt and preserve old state.

        Threshold-hidden and permanently rejected evidence is out of range when
        absent from the submitted union, even if known elsewhere to the caller.
        """
        old = ("Country", "anthem", "Song")
        new = ("Country", "leader", "Person")
        hidden = ("Country", "hidden", "Hidden")
        rejected = ("Country", "other", "Rejected")
        state = QuestionMemory("Who?", [old], [rejected])
        before = asdict(state)
        potential = build_potential_subgraph([new, hidden], [0.8, 0.7])
        potential_before = list(potential)
        prompt = build_memory_prompt(state.original_question, state.memory_triplets, potential)
        submitted = json.loads(prompt.rsplit("\nKnowledge: ", 1)[1])
        result = parse_memory_response(response_json(submitted, ["Person"]), state.memory_triplets, potential)
        self.assertEqual(set(result.selected_triplets), {old, new})
        for triple in (hidden, rejected):
            for content in (response_json([triple]), response_json(answers=[triple[2]])):
                with self.subTest(triple=triple), self.assertRaises(ValueError):
                    parse_memory_response(content, state.memory_triplets, potential)
        self.assertEqual(asdict(state), before)
        self.assertEqual(potential, potential_before)

    def test_invalid_graphs_and_errors_do_not_disclose_content(self) -> None:
        """Validate both graphs fully and keep failure messages content-free.

        Even empty selections must not bypass malformed submitted-graph inputs.
        Decoder errors must suppress snippets of private model response text.
        """
        for graph in (None, 1, "Secret", {"Secret": "value"}, [None],
                      [["Secret", "r"]], [["Secret", "r", False]]):
            for memory, potential in ((graph, []), ([], graph)):
                with self.subTest(graph=graph), self.assertRaises(ValueError) as error:
                    parse_memory_response(response_json(), memory, potential)
                self.assertNotIn("Secret", str(error.exception))
        for content in ('{"Secret": invalid}', response_json([["Secret", "r", "X"]]),
                        response_json(answers=["Secret"]), response_json(questions=[" "])):
            with self.subTest(content=content), self.assertRaises(ValueError) as error:
                parse_memory_response(content, [], [])
            self.assertNotIn("Secret", str(error.exception))
