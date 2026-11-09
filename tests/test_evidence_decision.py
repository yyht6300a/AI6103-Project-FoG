"""Check graph evaluation and atomic candidate execution with synthetic replies.

These cases establish protocol behavior, not real model semantic correctness.
"""

import copy
import json
import unittest
from unittest.mock import Mock

from src.agent.evidence_decision import derive_decision
from src.agent.memory_management import manage_memory
from src.agent.memory_state import QuestionMemory
from src.agent.validation_error import MemoryValidationError
from test_memory_update import completion


FACT = ("Book", "publisher", "Actual")
LEFT = ("Person A", "born_in", "Town A")
RIGHT = ("Person B", "born_in", "Town B")
GRAPH = sorted([FACT, LEFT, RIGHT])
QUESTION = "Book has exactly one publisher. If Expected, Person A; otherwise Person B."


def description(value="Expected", unique=True):
    """Create an independent extraction using this graph's stable IDs.

    Args:
        value: Comparison constraint, not an observed fact.
        unique: Include the literal single-value premise when true.

    Returns:
        Structured condition and both branch paths.
    """
    return {"condition": {"kind": "equals", "subject": "Book", "relation": "publisher",
                          "operator": "eq", "value": value,
                          "uniqueness_quote": "Book has exactly one publisher." if unique else ""},
            "branches": {
                "true": {"targets": [LEFT[0]], "evidence_ids": [GRAPH.index(LEFT)], "missing_relations": []},
                "false": {"targets": [RIGHT[0]], "evidence_ids": [GRAPH.index(RIGHT)], "missing_relations": []},
                "not_conditional": {"targets": [], "evidence_ids": [], "missing_relations": []}}}


def envelope(record):
    """Serialize one extraction in a normal completion envelope.

    Args:
        record: Untrusted extraction object used by the test.

    Returns:
        Completion with an untouched JSON body.
    """
    result = completion()
    result["choices"][0]["message"]["content"] = json.dumps(record)
    return result


class EvidenceDecisionTests(unittest.TestCase):
    """Verify full-graph evaluation, references and publication gates.

    Caller snapshots must remain unchanged after every failed stage.
    """

    def test_nonconditional_ranking_and_unknown_answer_gate(self):
        """Retain nonconditional multi-target sorting and unknown-condition gates.

        Ranking still executes after valid selection; an unknown condition may
        retain both paths but cannot publish an answer even from a graph entity.
        """
        record = description()
        record["condition"] = dict.fromkeys(record["condition"], "")
        record["condition"]["kind"] = "none"
        for branch in record["branches"].values():
            branch.update(targets=[], evidence_ids=[], missing_relations=[])
        record["branches"]["not_conditional"].update(
            targets=[LEFT[0], RIGHT[0]], evidence_ids=[GRAPH.index(LEFT), GRAPH.index(RIGHT)])
        client = Mock()
        client.complete.side_effect = [envelope(record),
            completion([LEFT, RIGHT], [LEFT[2], RIGHT[2]]),
            envelope({"predict_answer": [RIGHT[2], LEFT[2]]})]
        result = manage_memory(QuestionMemory(QUESTION), GRAPH, [0.95] * 3,
                               client, prompt_variant="evidence_decision")
        self.assertEqual(result.response.predict_answer, (RIGHT[2], LEFT[2]))
        self.assertEqual(client.complete.call_count, 3)
        client.complete.side_effect = [envelope(description(unique=False)),
                                      completion(GRAPH, [RIGHT[2]])]
        with self.assertRaises(MemoryValidationError) as caught:
            manage_memory(QuestionMemory(QUESTION), GRAPH, [0.95] * 3,
                          client, prompt_variant="evidence_decision")
        self.assertEqual(caught.exception.code, "decision_unknown_answer")

    def test_value_evaluation(self):
        """Distinguish equality, unique inequality and open-world uncertainty.

        Condition facts are discovered even though neither branch references them.
        """
        for value, unique, expected in (("Actual", True, "true"), ("Expected", True, "false"),
                                        ("Expected", False, "unknown")):
            result = derive_decision(description(value, unique), QUESTION, GRAPH)
            self.assertEqual(result["condition_status"], expected)
            self.assertEqual(result["condition_triplets"], [FACT])
            self.assertIn(FACT, result["required_triplets"])
            if expected == "unknown":
                self.assertEqual(set(result["required_triplets"]), set(GRAPH))
        record = description()
        for branch in record["branches"].values():
            branch["evidence_ids"] = []
        result = derive_decision(record, QUESTION, [])
        self.assertEqual(result["condition_status"], "unknown")
        self.assertEqual(result["required_triplets"], [])
        self.assertEqual(result["missing_relations"], ["publisher"])

    def test_invalid_extraction_is_atomic(self):
        """Reject invalid IDs, unsupported predicates and single-value conflicts.

        No failure may initiate final selection or publish rejection state.
        """
        cases = []
        for bad in (-1, 3, True, "0"):
            record = description()
            record["branches"]["false"]["evidence_ids"] = [bad]
            cases.append((record, GRAPH))
        for key, value in (("operator", "ne"), ("kind", "unsupported"),
                           ("uniqueness_quote", "invented premise")):
            record = description()
            record["condition"][key] = value
            cases.append((record, GRAPH))
        record = description()
        record["branches"]["not_conditional"]["targets"] = ["Book"]
        cases.extend([(record, GRAPH), (description(), []),
                      (description(), GRAPH + [("Book", "publisher", "Expected")])])
        for record, graph in cases:
            client = Mock()
            client.complete.return_value = envelope(record)
            state = QuestionMemory(QUESTION)
            with self.assertRaises(MemoryValidationError):
                manage_memory(state, graph, [0.95] * len(graph), client, prompt_variant="evidence_decision")
            self.assertEqual(client.complete.call_count, 1)
            self.assertEqual(state, QuestionMemory(QUESTION))

    def test_final_consumes_derived_facts(self):
        """Pass derived targets and remove only the satisfied condition gap.

        Final omissions become permanent rejection under the existing rule.
        """
        record = description()
        record["branches"]["false"]["missing_relations"] = ["publisher"]
        client = Mock()
        client.complete.side_effect = [envelope(record), completion([FACT, RIGHT], [RIGHT[2]])]
        state = QuestionMemory(QUESTION, [LEFT])
        result = manage_memory(state, [FACT, RIGHT], [0.95] * 2, client, prompt_variant="evidence_decision")
        self.assertEqual(result.state.rejected_triplets, {LEFT})
        prompt = client.complete.call_args_list[1].args[0][0]["content"]
        active = json.loads(prompt.split("Return only the existing three output fields.\n")[-1])
        self.assertEqual(active["answer_targets"], [RIGHT[0]])
        self.assertEqual(active["missing_relations"], [])
        self.assertEqual(state.memory_triplets, {LEFT})

    def test_strict_format_and_final_gates(self):
        """Keep strict envelopes, original answer validation and required evidence.

        Every malformed stage leaves the original empty snapshot intact.
        """
        first = envelope(description())
        cases = []
        for content in ('```json\n{}\n```', '{}', '{"x":1,"x":2}', '{"x":NaN}'):
            reply = copy.deepcopy(first)
            reply["choices"][0]["message"]["content"] = content
            cases.append([reply])
        for key, value in (("finish_reason", "length"), ("refusal", "refused")):
            reply = copy.deepcopy(first)
            target = reply["choices"][0] if key == "finish_reason" else reply["choices"][0]["message"]
            target[key] = value
            cases.append([reply])
        cases.extend([[first, completion([RIGHT], [RIGHT[2]])],
                      [first, completion([FACT, RIGHT], ["External"])],
                      [first, completion([FACT, RIGHT], questions=["What?"])],
                      [first, completion([FACT, RIGHT], [RIGHT[2]], ["What?"])],
                      [first, OSError()]])
        for replies in cases:
            state = QuestionMemory(QUESTION)
            client = Mock()
            client.complete.side_effect = replies
            with self.assertRaises((ValueError, OSError)):
                manage_memory(state, GRAPH, [0.95] * 3, client, prompt_variant="evidence_decision")
            self.assertEqual(state, QuestionMemory(QUESTION))
            self.assertEqual(client.complete.call_count, len(replies))
